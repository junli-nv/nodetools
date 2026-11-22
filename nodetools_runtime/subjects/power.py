"""Power state and control for compute trays, NVSwitches, and power shelves."""

from . import common

PS_SYSTEM = "system"
_RESET = {"on": "On", "off": "ForceOff", "cycle": "PowerCycle"}
_READ = ("", "status", "check", "read")


def _normalize(action):
    action = (action or "").strip().lower()
    return "status" if action in _READ else action


def host_status(name, state):
    """Render Redfish and ipmitool chassis states using the same spelling."""
    state = str(state or "").strip().lower()
    prefix = "chassis power is "
    if state.startswith(prefix):
        state = state[len(prefix):].strip()
    states = {
        "on": "On",
        "off": "Off",
        "poweringon": "PoweringOn",
        "poweringoff": "PoweringOff",
        "paused": "Paused",
        "unknown": "Unknown",
    }
    return ["{}: {}".format(name, states.get(state, "N/A"))]


def collect(device, action, options):
    action = _normalize(action)
    if device.role == "nvswitch":
        return device.odm.nvswitch_power(device, action)
    if device.role == "powershelf":
        return device.odm.powershelf_power(device, action)
    return device.odm.power(device, action)


def _reset_lines(device, path, action):
    status, body = common.post(
        device, path, {"ResetType": _RESET[action]}
    )
    text = common.jq_out(body)
    return ["{}: {}".format(device.name, text)] if text else []


def _powershelf(device, action):
    base = "/redfish/v1/Systems/" + PS_SYSTEM
    if action in _RESET:
        return _reset_lines(device, base + "/Actions/ComputerSystem.Reset", action)
    chassis = common.resource(device, "/redfish/v1/Chassis", required=True)
    lines = []
    for oid in common.member_ids(chassis):
        data = device.redfish.get(oid)
        if isinstance(data, dict) and "error" not in data:
            lines.append(
                "{}: {},{}".format(
                    device.name, data.get("SerialNumber", ""), data.get("PowerState", "")
                )
            )
    return lines
