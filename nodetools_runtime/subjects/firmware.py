"""Vendor-neutral Redfish firmware inventory and task reads."""

import re

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


def collection(device, path, required=False):
    path = link(path)
    page = path
    seen = set()
    while page:
        if page in seen:
            raise TransportError("Redfish collection repeats a page")
        seen.add(page)
        data = common.resource(device, page, required=required)
        if not data:
            return
        members = data.get("Members")
        if not isinstance(members, list):
            raise TransportError("invalid Redfish collection at {}".format(path))
        for member in members:
            member_path = link(member)
            if not member_path.startswith(path + "/"):
                raise TransportError("unexpected member outside {}".format(path))
            yield member_path, common.resource(device, member_path, required=True)
        page = data.get("Members@odata.nextLink")
        if page and (not isinstance(page, str) or not page.startswith(path + "?")):
            raise TransportError("unexpected Redfish collection page")


def update_service(device):
    root = common.resource(device, "/redfish/v1/", required=True)
    return common.resource(device, link(root.get("UpdateService")), required=True)


def inventory(device):
    for _, system in collection(device, "/redfish/v1/Systems"):
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
        for path, item in collection(device, link(service["FirmwareInventory"]), required=True):
            yield "{}  {}".format(item.get("Name") or item.get("Id") or path.rsplit("/", 1)[-1],
                                  item.get("Version") or "NA")


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
