"""Firmware inventory dispatched by device role and OEM."""

from ..errors import CapUnsupported
from . import common

FIRMWARE = "/redfish/v1/UpdateService/FirmwareInventory"


def collect(device, action, options):
    if getattr(options, "tasks", False):
        return _tasks(device)
    if getattr(options, "gpu", False):
        return _gpu_serials(device)
    if getattr(options, "details", False):
        return _hardware(device)
    if device.role in ("nvswitch", "powershelf"):
        return device.odm.infrastructure_inventory(device)
    return device.odm.firmware_inventory(device)


def _hardware(device):
    selected = [("Systems", "system_id"), ("Chassis", "chassis_id")]
    scoped = any(device.vars.get(field) for _, field in selected)
    lines = []
    for collection, field in selected:
        base = "/redfish/v1/" + collection
        ident = device.vars.get(field)
        if scoped and not ident:
            continue
        paths = [base + "/" + ident] if ident else common.member_ids(
            common.resource(device, base)
        )
        for path in paths:
            data = common.resource(device, path, required=bool(ident))
            fields = ("Manufacturer", "Model", "SerialNumber")
            if any(data.get(field) for field in fields):
                lines.append("{}: {}/{} manufacturer={} model={} serial={}".format(
                    device.name, collection, data.get("Id") or path.rstrip("/").rsplit("/", 1)[-1],
                    *(data.get(field) or "-" for field in fields),
                ))
    if not lines:
        raise CapUnsupported("Redfish did not report hardware identity")
    return lines


def _firmware(device, path=FIRMWARE):
    coll = common.resource(device, path, required=True)
    rows = []
    for oid in common.member_ids(coll):
        data = device.redfish.get(oid)
        if isinstance(data, dict) and "error" not in data:
            rows.append([data.get("Id", ""), data.get("Version", "")])
    return [
        "{}: {}".format(device.name, row) for row in common.column_t(rows)
    ]


def _gpu_serials(device):
    base = "/redfish/v1/Systems"
    ident = device.vars.get("system_id")
    paths = [base + "/" + ident] if ident else common.member_ids(
        common.resource(device, base, required=True)
    )
    rows = []
    for path in paths:
        system = common.resource(device, path, required=bool(ident))
        link = system.get("Processors") or {}
        collection = link.get("@odata.id") or path.rstrip("/") + "/Processors"
        if not common.resource(device, collection):
            continue
        for gpu in common.entries(device, collection):
            gpu_id = gpu.get("Id") or ""
            if (gpu.get("ProcessorType") or "").lower() == "gpu" or gpu_id.upper().startswith("GPU"):
                rows.append("{}: {} serial={}".format(device.name, gpu_id, gpu.get("SerialNumber") or "-"))
    if not rows:
        raise CapUnsupported("Redfish did not advertise any GPU processors")
    return rows


def _tasks(device):
    return [
        "{}: {} {} {}".format(
            device.name, task.get("Id") or "-", task.get("TaskState") or "Unknown",
            task.get("TaskStatus") or "-",
        )
        for task in common.entries(device, "/redfish/v1/TaskService/Tasks")
    ]
