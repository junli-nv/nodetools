"""Firmware inventory dispatched by device role and OEM."""

from collections import Counter

from ..errors import CapUnsupported
from . import boot_nics, common

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
    return _format_firmware(device, _firmware_rows(device, path))


def _firmware_rows(device, path):
    coll = common.resource(device, path, required=True)
    rows = []
    for oid in common.member_ids(coll):
        data = device.redfish.get(oid)
        if isinstance(data, dict) and "error" not in data:
            rows.append([data.get("Id", ""), data.get("Version", ""), oid, "Version"])
    return rows


def _format_firmware(device, rows):
    if getattr(device.redfish, "debug_label", None):
        for label, _, oid, field in rows:
            device.redfish.trace_source(label, oid, field)
    return [
        "{}: {}".format(device.name, row) for row in common.column_t([row[:2] for row in rows])
    ]


def _gpu_serials(device):
    base = "/redfish/v1/Systems"
    module_serials = getattr(device.odm, "gpu_reports_module_serials", False)
    ident = None if module_serials else device.vars.get("system_id")
    fixed = getattr(device.odm, "gpu_inventory_systems", ())
    paths = ([base + "/" + ident] if ident else [base + "/" + item for item in fixed]
             if fixed else common.member_ids(common.resource(device, base, required=True)))
    fixed = getattr(device.odm, "gpu_inventory_systems", ())
    if fixed and not ident:
        paths = [base + "/" + item for item in fixed]
    records = []
    for path in paths:
        system = common.resource(device, path, required=bool(ident))
        collection = boot_nics.local_link(system.get("Processors")) or path.rstrip("/") + "/Processors"
        for _, gpu in boot_nics.members(device, collection):
            gpu_id = gpu.get("Id") or ""
            if (gpu.get("ProcessorType") or "").lower() == "gpu" or gpu_id.upper().startswith("GPU"):
                records.append((gpu_id, gpu, system.get("SerialNumber")))
    if not records:
        raise CapUnsupported("Redfish did not advertise any GPU processors")

    serial_counts = Counter(str(gpu.get("SerialNumber") or "") for _, gpu, _ in records)
    rows = []
    for gpu_id, gpu, system_serial in records:
        serial = gpu.get("SerialNumber") or "-"
        detail = ""
        if module_serials:
            if serial == system_serial or serial_counts[str(serial)] > 1:
                serial = "-"
            uuid = gpu.get("UUID")
            if not uuid:
                pcie = boot_nics.local_link((gpu.get("Links") or {}).get("PCIeDevice"))
                if pcie:
                    uuid = common.resource(device, pcie).get("UUID")
            detail = " uuid={}".format(uuid or "-")
        rows.append("{}: {} serial={}{}".format(device.name, gpu_id, serial, detail))
    return rows


def _tasks(device):
    return [
        "{}: {} {} {}".format(
            device.name, task.get("Id") or "-", task.get("TaskState") or "Unknown",
            task.get("TaskStatus") or "-",
        )
        for task in common.entries(device, "/redfish/v1/TaskService/Tasks")
    ] or ["{}: BMC reports no Redfish tasks".format(device.name)]
