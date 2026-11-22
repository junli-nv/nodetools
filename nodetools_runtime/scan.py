"""Probe and cache each *machine type's* Redfish interface map.

Scanning a GB200 BMC means walking every chassis (dozens of them) to learn
which collections exist and how many members each holds. Doing that on every
``fan``/``psu``/``temp`` run would be the single biggest cost, so ``nodetools
scan`` records it once and the collectors then read the cache and only touch the
chassis that can actually answer.

The map is a property of the hardware, so the cache is keyed by the machine
identity (vendor + manufacturer + model) rather than the BMC address: scan one
representative tray to a template and every other tray of the same type reuses
it. Commands match their template by reading the machine identity and looking
the file up, so dispatch is a cache lookup, not a full walk. Re-run ``scan`` to
refresh a template, or ``scan --clear`` to drop it.
"""

import concurrent.futures
import json
import os
import re
import time

from . import model
from .inventory import RESOURCE_ID_KEYS
from .caps import common
from .config import SCAN_CACHE_DIR, SCAN_CACHE_VERSION

# Member counts recorded per chassis (0 when the collection is absent/empty).
# Counts come from the collection endpoints directly rather than from what the
# chassis detail advertises: some vendors (Wistron) expose a Sensors collection
# that the detail object never lists, so trusting the detail silently drops it.
COUNTS = ("Sensors", "Fans", "PowerSupplies", "Temperatures")

# Legacy singleton objects, recorded as booleans (almost always absent on
# OpenBMC, but still present on some vendors).
OBJECTS = ("Thermal", "Power")


def _slug(value):
    """Normalize an identity field for use in a cache key/filename."""
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def identity_key(vendor, manufacturer, model, bios=None, bmc_fw=None):
    """Cache key for a machine type.

    Keyed by vendor + manufacturer + system model, plus the BIOS and BMC
    firmware revisions when the BMC reports them. The firmware fields come from
    the same requests the identity read already makes, so they cost nothing and
    keep two units of the same model but different firmware from sharing a
    template. Empty fields are skipped; ``None`` when nothing identifies the
    machine, so the caller caches nothing (rather than falling back to a
    per-address file).
    """
    parts = [_slug(value) for value in (vendor, manufacturer, model, bios, bmc_fw)]
    parts = [part for part in parts if part]
    return "__".join(parts) if parts else None


def key_for(identity, overrides=None):
    """Cache key for a device: its machine identity, or ``None`` if unidentified.

    There is deliberately no per-BMC fallback: a machine whose identity cannot
    be read is simply not cached (it falls back to live discovery), rather than
    getting a private per-address file.
    """
    key = identity_key(
        identity.get("vendor"),
        identity.get("manufacturer"),
        identity.get("model"),
        identity.get("bios"),
        identity.get("bmc_fw"),
    )

    if key:
        for field in ("platform", "oem", "role", "generation", *RESOURCE_ID_KEYS):
            value = _slug((overrides or {}).get(field))
            if value:
                key += "__{}-{}".format(field, value)
    return key


def cache_path(key):
    safe = str(key).replace("/", "_").replace(":", "_")
    return os.path.join(SCAN_CACHE_DIR, safe + ".json")


def read_identity(redfish, selectors=None):
    """Read the machine identity cheaply (no chassis walk).

    This is the few requests a command needs to find the shared template; it is
    not the expensive part of a scan.
    """
    identity = {
        "vendor": None,
        "manufacturer": None,
        "model": None,
        "bios": None,
        "bmc_fw": None,
    }
    selectors = selectors or {}
    root = redfish.get("/redfish/v1")
    if isinstance(root, dict):
        identity["vendor"] = root.get("Vendor") or (root.get("Oem") or {}).get(
            "Vendor"
        )
    # The manager reports the BMC's own firmware/model; the system reports the
    # real server model and BIOS. Seed from the manager, then let the system
    # override manufacturer/model and supply the BIOS revision.
    manager = selectors.get("manager_id")
    members = ["/redfish/v1/Managers/" + manager] if manager else common.member_ids(
        redfish.get("/redfish/v1/Managers")
    )
    if members:
        data = redfish.get(members[0])
        if isinstance(data, dict):
            identity["bmc_fw"] = data.get("FirmwareVersion")
            identity["model"] = data.get("Model")
            identity["manufacturer"] = data.get("Manufacturer")
    chassis = selectors.get("chassis_id")
    system = selectors.get("system_id")
    if chassis and not system:
        members = ["/redfish/v1/Chassis/" + chassis]
    else:
        members = ["/redfish/v1/Systems/" + system] if system else common.member_ids(
            redfish.get("/redfish/v1/Systems")
        )
    if members:
        data = redfish.get(members[0])
        if isinstance(data, dict):
            identity["model"] = data.get("Model") or identity["model"]
            identity["manufacturer"] = data.get("Manufacturer") or identity["manufacturer"]
            identity["bios"] = data.get("BiosVersion") or data.get("Version")
    return identity


def _read(key):
    if not key:
        return None
    try:
        with open(cache_path(key), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != SCAN_CACHE_VERSION:
        return None
    return data


def load(device):
    """Cached template for this machine type, matched via its identity."""
    device.identity = read_identity(device.redfish, device.vars)
    return _read(key_for(device.identity, device.vars))


def store(profile):
    if not profile.get("key"):
        return None
    os.makedirs(SCAN_CACHE_DIR, exist_ok=True)
    path = cache_path(profile["key"])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(profile, handle, sort_keys=True)
    os.replace(tmp, path)
    return path


def clear(device):
    key = key_for(read_identity(device.redfish, device.vars), device.vars)
    if not key:
        return False
    try:
        os.remove(cache_path(key))
        return True
    except OSError:
        return False


def _count(redfish, path):
    """Member count from a collection without expanding it."""
    data = redfish.get(path)
    if not isinstance(data, dict):
        return 0
    count = data.get("Members@odata.count")
    if isinstance(count, int):
        return count
    members = data.get("Members")
    return len(members) if isinstance(members, list) else 0


def _shown(redfish, path):
    """True when an endpoint exists (a Redfish error is itself a JSON object)."""
    data = redfish.get(path)
    return isinstance(data, dict) and "error" not in data


def _chassis_caps(redfish, cid):
    base = "/redfish/v1/Chassis/" + cid
    rec = {
        "Sensors": _count(redfish, base + "/Sensors"),
        "Fans": _count(redfish, base + "/ThermalSubsystem/Fans"),
        "PowerSupplies": _count(redfish, base + "/PowerSubsystem/PowerSupplies"),
    }
    metrics = redfish.get(base + "/ThermalSubsystem/ThermalMetrics")
    readings = (
        metrics.get("TemperatureReadingsCelsius")
        if isinstance(metrics, dict)
        else None
    )
    rec["Temperatures"] = len(readings) if isinstance(readings, list) else 0
    for obj in OBJECTS:
        rec[obj] = _shown(redfish, base + "/" + obj)
    return rec


def probe(device, workers=common.SCAN_WORKERS):
    """Walk a BMC and build its interface map (does not touch the cache)."""
    redfish = device.redfish
    identity = read_identity(redfish, device.vars)
    device.identity = identity
    selected = device.vars.get("chassis_id")
    chassis = [selected] if selected else common.discover_chassis(redfish)
    caps = {}

    def one(cid):
        return cid, _chassis_caps(redfish, cid)

    if chassis:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(workers, len(chassis))
        ) as pool:
            for cid, rec in pool.map(one, chassis):
                caps[cid] = rec
    profile = {
        "version": SCAN_CACHE_VERSION,
        "address": device.address,
        "scanned": time.time(),
        "vendor": identity["vendor"],
        "model": identity["model"],
        "manufacturer": identity["manufacturer"],
        "chassis": chassis,
        "caps": caps,
    }
    profile["platform"] = model.platform_for(device)
    profile["generation"] = model.generation_for(device)
    profile["odm"] = model.odm_for(device)
    profile["key"] = key_for(identity, device.vars)
    return profile


def refresh(device, workers=common.SCAN_WORKERS):
    """Probe and write/update the cached template for this machine type."""
    profile = probe(device, workers=workers)
    store(profile)
    return profile
