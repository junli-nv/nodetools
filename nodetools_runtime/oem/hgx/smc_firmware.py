"""Firmware adapter for the verified SYS-822GS-NB3RT package."""

import hashlib
import json
import re
import shutil
import tempfile
import zipfile

import requests

from ...errors import CapUnsupported, NodetoolsError, TransportError
from ...subjects import common, firmware

MODEL = "SYS-822GS-NB3RT"
PACKAGE_SHA256 = "2f61d8017c3c5b5321b2af764e0f0c37205741ea1c5921e915454845d8b50be3"
BMC_VERSION = "01.12.05.00"
BIOS = "/redfish/v1/Systems/1/Bios"
MANAGER = "/redfish/v1/Managers/1"
UPLOAD = "/redfish/v1/UpdateService/upload"

IMAGES = {
    "bmc": (
        "BMC_X14AST2600-ROT20-3701MS_20261003_01.12.05.00_STDsp",
        "721f334b56b49b406c203e9819f1389cd1ddde31bbd307c5e015a8ac5a36b486",
        MANAGER, "BMC", BMC_VERSION,
        {"PreserveCfg": True, "PreserveSdr": True, "PreserveSsl": True, "BackupBMC": True},
    ),
    "bios": (
        "BIOS_X14DBGL-1E11_20260827_2.0_STDsp",
        "c1efd3586053599c42444fd8687ff5c7e8e24e0c2fbaddedf4a5f6f19406dae2",
        BIOS, "BIOS", "2.0",
        {"PreserveME": True, "PreserveNVRAM": True, "PreserveSMBIOS": True, "BackupBIOS": False},
    ),
}


def sha256(handle):
    digest = hashlib.sha256()
    for block in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(block)
    handle.seek(0)
    return digest.hexdigest()


def preflight(device, component, system):
    if component not in IMAGES:
        raise CapUnsupported("this adapter supports bmc and bios")
    if not system.get("SerialNumber"):
        raise CapUnsupported("system serial number is missing")
    chassis = common.resource(device, "/redfish/v1/Chassis/1", required=True)
    manager = common.resource(device, MANAGER, required=True)
    if chassis.get("Model") != "X14DBG-LC" or manager.get("Model") != "AST2600":
        raise CapUnsupported("unexpected Supermicro chassis or BMC model")
    if component == "bios":
        if manager.get("FirmwareVersion") != BMC_VERSION or system.get("PowerState") != "Off":
            raise NodetoolsError("BIOS requires BMC {} and host power Off".format(BMC_VERSION))
        version = system.get("BiosVersion") or "NA"
        match = re.search(r"\bVer\s+(\S+)", version)
        return match.group(1) if match else version
    return manager.get("FirmwareVersion") or "NA"


def upload_endpoint(device):
    service = firmware.update_service(device)
    if (service.get("ServiceEnabled") is not True
            or service.get("MultipartHttpPushUri") != UPLOAD):
        raise CapUnsupported("expected Supermicro Redfish upload endpoint is unavailable")
    for path, task in firmware.tasks(device):
        if task.get("TaskState") not in firmware.TERMINAL:
            raise NodetoolsError("outstanding Redfish task {}".format(path))
    return UPLOAD


def version_parts(value):
    if not re.fullmatch(r"\d+(?:\.\d+)+", value):
        raise NodetoolsError("cannot compare installed version {!r}".format(value))
    return tuple(int(part) for part in value.split("."))


def image_from_package(package, component, stem, checksum, destination):
    with package.open("rb") as handle:
        if sha256(handle) != PACKAGE_SHA256:
            raise NodetoolsError("package SHA256 does not match the verified official bundle")
        with zipfile.ZipFile(handle) as bundle, tempfile.TemporaryFile() as inner:
            with bundle.open(stem + ".zip") as archive:
                shutil.copyfileobj(archive, inner)
            with zipfile.ZipFile(inner) as archive:
                member = stem + ".bin"
                if component == "bios":
                    member = stem + "/" + member
                with archive.open(member) as image:
                    shutil.copyfileobj(image, destination)
    destination.seek(0)
    if sha256(destination) != checksum:
        raise NodetoolsError("firmware image SHA256 mismatch")


def submit(device, endpoint, target, kind, preserve, filename, image):
    payload = {
        "Targets": [target],
        "@Redfish.OperationApplyTime": "Immediate",
        "Oem": {"Supermicro": {kind: preserve}},
    }
    try:
        rf = device.redfish
        response = rf._session.post(rf.url(endpoint), files={
            "UpdateParameters": (None, json.dumps(payload), "application/json"),
            "UpdateFile": (filename, image, "application/octet-stream"),
        }, timeout=(120, 240), allow_redirects=False)
    except requests.RequestException as exc:
        raise TransportError("upload outcome unknown; check nodefw status before retrying") from exc
    if response.status_code != 202:
        raise TransportError("firmware upload returned HTTP {}; check nodefw status before retrying".format(
            response.status_code))
    try:
        task = firmware.link(response.headers.get("Location"))
    except CapUnsupported:
        return None
    return task if task.rsplit("/", 1)[0] == firmware.TASKS else None


def run(device, path, system, component, package, apply=False, reflash=False):
    if path != '/redfish/v1/Systems/1':
        raise CapUnsupported('unexpected Supermicro Redfish system path')
    serial = system.get('SerialNumber')
    current = preflight(device, component, system)
    stem, checksum, target, kind, expected, preserve = IMAGES[component]
    installed, bundled = version_parts(current), version_parts(expected)
    if installed > bundled:
        raise NodetoolsError("installed {} is newer than package {}".format(current, expected))
    if apply and installed == bundled and not reflash:
        raise NodetoolsError("{} is already installed; use --reflash for a same-version write".format(current))
    with tempfile.TemporaryFile() as image:
        image_from_package(package, component, stem, checksum, image)
        if apply:
            system = common.resource(device, path, required=True)
            if (system.get("Model") != MODEL or system.get("SerialNumber") != serial
                    or preflight(device, component, system) != current):
                raise NodetoolsError("target identity or installed version changed during validation")
        endpoint = upload_endpoint(device)
        if not apply:
            return "READY {} {} -> {} (package verified)".format(component, current, expected)
        task = submit(device, endpoint, target, kind, preserve, stem + ".bin", image)
    return "ACCEPTED {} {} -> {} (task {}; verify with status and show)".format(
        component, current, expected, task.rsplit("/", 1)[-1] if task else "not returned")
