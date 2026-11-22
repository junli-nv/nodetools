"""power: ``nodepower`` / ``nvswpower`` / ``pspower``.

Compute trays dispatch to the ODM handler (NVIDIA GBXX reference by default,
HGX x86 vendor override where the original script differs). NVSwitch and power
shelf are NVIDIA infrastructure and always use their fixed Redfish paths.
"""

from . import common

NVSW_SYSTEM = "System_0"
PS_SYSTEM = "system"
_RESET = {"on": "On", "off": "ForceOff", "cycle": "PowerCycle"}
_READ = ("", "status", "check", "read")


def _normalize(action):
    action = (action or "").strip().lower()
    return "status" if action in _READ else action


def collect(device, action, options):
    action = _normalize(action)
    if device.role == "nvswitch":
        return _nvswitch(device, action)
    if device.role == "powershelf":
        return _powershelf(device, action)
    return device.odm.power(device, action)


def _reset_lines(device, path, action):
    status, body = common.post(
        device, path, {"ResetType": _RESET[action]}
    )
    text = common.jq_out(body)
    return ["{}: {}".format(device.name, text)] if text else []


def _nvswitch(device, action):
    base = "/redfish/v1/Systems/" + NVSW_SYSTEM
    if action in _RESET:
        return _reset_lines(device, base + "/Actions/ComputerSystem.Reset", action)
    data = common.resource(device, base, required=True)
    return ["{}: {}".format(device.name, common.jq(data.get("PowerState")))]


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
