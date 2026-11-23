"""Event log operations dispatched by device role and OEM."""

from ..errors import CapUnsupported
from . import common

NVSW_LOG = "/redfish/v1/Systems/System_0/LogServices/EventLog"
PS_LOG = "/redfish/v1/Managers/PMC_0/LogServices/EventLog"
_READ = ("", "status", "check", "read")


def collect(device, action, options):
    action = (action or "").strip().lower()
    if action in _READ:
        action = "status"
    if device.role == "powershelf" and action == "bmc":
        raise CapUnsupported("pseventlog uses status to read the event log")
    if device.role in ("nvswitch", "powershelf"):
        if action == "hmc":
            raise CapUnsupported("the hmc action is only available for compute trays")
        return device.odm.infrastructure_eventlog(device, action)
    return device.odm.eventlog(device, action)


def _infra(device, action):
    base = NVSW_LOG if device.role == "nvswitch" else PS_LOG
    if action == "clear":
        status, body = common.post(device, base + "/Actions/LogService.ClearLog", {})
        text = common.body_text(body)
        return ["{}: {}".format(device.name, text)] if text else []
    lines = common.log_lines(device, base + "/Entries", sep=" ")
    return ["{}: {}".format(device.name, line) for line in lines]
