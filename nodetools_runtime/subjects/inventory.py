"""Firmware inventory dispatched by device role and OEM."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from types import SimpleNamespace

from ..errors import CapUnsupported, TransportError
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
        workers = getattr(device.odm, "hardware_inventory_workers", None)
        if workers and not scoped:
            resources = _model_hardware(device, base, collection == "Systems", workers)
        else:
            paths = [base + "/" + ident] if ident else common.member_ids(common.resource(device, base))
            resources = [(path, common.resource(device, path, required=bool(ident))) for path in paths]
        for path, data in resources:
            fields = ("Manufacturer", "Model", "SerialNumber")
            if any(data.get(field) for field in fields):
                lines.append("{}: {}/{} manufacturer={} model={} serial={}".format(
                    device.name, collection, data.get("Id") or (path or "-").rstrip("/").rsplit("/", 1)[-1],
                    *(data.get(field) or "-" for field in fields),
                ))
    if not lines:
        raise CapUnsupported("Redfish did not report hardware identity")
    return lines


def _model_hardware(device, base, expand, workers):
    expanded = common.resource(device, base + "?$expand=.") if expand else {}
    collection = expanded or common.resource(device, base, required=True)
    members = collection.get("Members")
    total = collection.get("Members@odata.count")
    if (not isinstance(members, list) or collection.get("Members@odata.nextLink")
            or type(total) is not int or total != len(members)):
        raise TransportError("incomplete hardware inventory collection at " + base)
    paths = [boot_nics.local_link(item) for item in members]
    if (any(not path or path.rsplit("/", 1)[0] != base for path in paths)
            or len(set(paths)) != len(paths)):
        raise TransportError("invalid hardware inventory member at " + base)
    resources = {path: item for path, item in zip(paths, members) if expanded and item.get("Id")}
    pending = [path for path in paths if path not in resources]
    fork = getattr(device.redfish, "fork", None)

    def read_chunk(chunk):
        with closing(fork()) as connection:
            source = SimpleNamespace(redfish=connection)
            return [(path, common.resource(source, path, required=True)) for path in chunk]

    if fork and len(pending) > 1:
        count = min(workers, len(pending))
        with ThreadPoolExecutor(max_workers=count) as pool:
            for records in pool.map(read_chunk, (pending[i::count] for i in range(count))):
                resources.update(records)
    else:
        resources.update((path, common.resource(device, path, required=True)) for path in pending)
    return [(path, resources[path]) for path in paths]


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
