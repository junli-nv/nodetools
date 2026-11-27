"""Vendor-neutral Redfish firmware inventory and task reads."""

import re

from .. import model
from ..errors import CapUnsupported, TransportError
from . import common

TASKS = "/redfish/v1/TaskService/Tasks"
TERMINAL = {"Completed", "Exception", "Killed", "Cancelled", "Interrupted"}
_COMPONENT_PATH = re.compile(r"/redfish/v1/(?:[A-Za-z0-9_.~-]+/)*[A-Za-z0-9_.~-]+")
_TASK_ID = re.compile(r"[A-Za-z0-9_~-][A-Za-z0-9_.~-]*")


def link(value):
    if isinstance(value, dict):
        value = value.get("@odata.id")
    if (isinstance(value, str) and _COMPONENT_PATH.fullmatch(value)
            and not {".", ".."}.intersection(value.split("/"))):
        return value
    raise CapUnsupported("expected a local Redfish resource link")


def collection(device, path, required=False, expand=False, page_size=None):
    path = link(path)
    if page_size:
        members = common.paged_members(device, path, page_size, expand=expand)
        if members is not None:
            yield from _member_details(device, path, members, expand)
            return
    page = path
    seen = set()
    while page:
        if page in seen:
            raise TransportError("Redfish collection repeats a page")
        seen.add(page)
        data = None
        if expand:
            separator = "&" if "?" in page else "?"
            data = common.resource(device, page + separator + "$expand=.")
        if not data or not isinstance(data.get("Members"), list):
            data = common.resource(device, page, required=required)
        if not data:
            return
        members = data.get("Members")
        if not isinstance(members, list):
            raise TransportError("invalid Redfish collection at {}".format(path))
        yield from _member_details(device, path, members, expand)
        page = data.get("Members@odata.nextLink")
        if page and (not isinstance(page, str) or not page.startswith(path + "?")):
            raise TransportError("unexpected Redfish collection page")


def _member_details(device, path, members, expand):
    for member in members:
        member_path = link(member)
        if not member_path.startswith(path + "/"):
            raise TransportError("unexpected member outside {}".format(path))
        item = member if expand and isinstance(member, dict) and "Version" in member else None
        yield member_path, item if item is not None else common.resource(device, member_path, required=True)


def components(device, path, supermicro=False):
    """Read firmware components for both nodefw and the legacy inventory view."""
    supermicro = supermicro or getattr(getattr(device, "odm", None), "name", None) == "smc"
    supermicro = supermicro or model.odm_for(device) == "smc"
    return collection(device, path, required=True, expand=True, page_size=16 if supermicro else None)


def update_service(device):
    root = common.resource(device, "/redfish/v1/", required=True)
    return common.resource(device, link(root.get("UpdateService")), required=True)


def read_collection(device, name):
    """Honor explicit instances or verified read-only defaults."""
    from .. import oem
    adapter = getattr(device, "odm", None) or (oem.for_device(device)
            if isinstance(device, model.Device) else None)
    field = {"Systems": "system_id", "Managers": "manager_id",
             "Chassis": "chassis_id"}[name]
    defaults = {"Systems": "read_system_id", "Managers": "read_manager_id",
                "Chassis": "read_chassis_id"}
    ident = getattr(device, "vars", {}).get(field)
    if not ident and getattr(adapter, "read_management_fixed", False):
        ident = getattr(adapter, defaults[name], None)
    if ident:
        path = "/redfish/v1/" + name + "/" + ident
        yield path, common.resource(device, path, required=True)
        return
    yield from collection(device, "/redfish/v1/" + name)


def inventory(device):
    from .. import oem
    adapter = getattr(device, "odm", None) or (oem.for_device(device)
            if isinstance(device, model.Device) else None)
    if not getattr(adapter, "read_management_fixed", False):
        yield from _legacy_inventory(device)
        return
    for _, system in read_collection(device, "Systems"):
        if system.get("BiosVersion"):
            yield "{} BIOS  {}".format(system.get("Model") or system.get("Id") or "System",
                                       system["BiosVersion"])
    for _, manager in read_collection(device, "Managers"):
        if manager.get("FirmwareVersion"):
            yield "{} BMC   {}".format(manager.get("Model") or manager.get("Id") or "Manager",
                                       manager["FirmwareVersion"])
    direct = getattr(adapter, "read_firmware_path", None)
    if direct:
        path = direct
    else:
        root = common.resource(device, "/redfish/v1/", required=True)
        if not root.get("UpdateService"):
            return
        service = common.resource(device, link(root["UpdateService"]), required=True)
        if not service.get("FirmwareInventory"):
            return
        path = link(service["FirmwareInventory"])
    for member_path, item in collection(device, path, required=True, expand=True):
        label = item.get("Name")
        if not label or label == "Software Inventory":
            label = item.get("Id") or member_path.rsplit("/", 1)[-1]
        yield "{}  {}".format(label, item.get("Version") or "NA")


def tasks(device, task_id=None):
    if task_id is None:
        yield from collection(device, TASKS, required=True)
        return
    if not _TASK_ID.fullmatch(task_id):
        raise CapUnsupported("expected a Redfish task ID")
    path = TASKS + "/" + task_id
    status, data = device.redfish.get_raw(path)
    if status == 404:
        raise TransportError("task {} is not present on the BMC".format(task_id))
    if status != 200 or not isinstance(data, dict) or "error" in data:
        reason = getattr(device.redfish, "timeout_reason", None) or "Redfish task query failed"
        raise TransportError("{} {}".format(reason, path))
    yield path, data


def task_name(task_id, task):
    name = task.get("Name")
    if name and name.casefold() not in ("task", "task " + task_id.casefold()):
        return name
    return task.get("Description") or "Task"


def task_details(path, task):
    task_id = path.rsplit("/", 1)[-1]
    name = task_name(task_id, task)
    yield "Task {}  {}".format(task_id, name)
    for key, label in (
        ("Description", "Description"), ("TaskState", "State"), ("TaskStatus", "Status"),
        ("PercentComplete", "Progress"), ("StartTime", "Started"), ("EndTime", "Ended"),
    ):
        value = task.get(key)
        if value is not None and (key != "Description" or value != name):
            yield "  {}  {}{}".format(label, value, "%" if key == "PercentComplete" else "")
    public = (task.get("Oem") or {}).get("Public") or {}
    for key, label in (("Type", "Type"), ("Trigger", "Trigger")):
        value = public.get(key)
        if isinstance(value, str) and value:
            yield "  {}  {}".format(label, value)
    for message in task.get("Messages") or []:
        if not isinstance(message, dict):
            continue
        fields = [message.get(key) for key in ("Severity", "MessageId", "Message")]
        if any(fields):
            yield "  Message  " + "  ".join(str(value) for value in fields if value)
        if message.get("Resolution"):
            yield "  Resolution  " + str(message["Resolution"])


def _legacy_inventory(device):
    supermicro = False
    for _, system in collection(device, "/redfish/v1/Systems"):
        manufacturer = str(system.get("Manufacturer") or "").casefold()
        supermicro |= "supermicro" in manufacturer or "super micro" in manufacturer
        if not system.get("BiosVersion"):
            continue
        yield "{} BIOS  {}".format(system.get("Model") or system.get("Id") or "System",
                                    system.get("BiosVersion") or "NA")
    for _, manager in collection(device, "/redfish/v1/Managers"):
        if not manager.get("FirmwareVersion"):
            continue
        yield "{} BMC   {}".format(manager.get("Model") or manager.get("Id") or "Manager",
                                    manager.get("FirmwareVersion") or "NA")
    root = common.resource(device, "/redfish/v1/", required=True)
    if not root.get("UpdateService"):
        return
    service = common.resource(device, link(root["UpdateService"]), required=True)
    if service.get("FirmwareInventory"):
        for path, item in components(device, link(service["FirmwareInventory"]), supermicro=supermicro):
            label = item.get("Name")
            if not label or label == "Software Inventory":
                label = item.get("Id") or path.rsplit("/", 1)[-1]
            yield "{}  {}".format(label, item.get("Version") or "NA")
