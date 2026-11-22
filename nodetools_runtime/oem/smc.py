"""Supermicro (FanMode control)."""

from ..subjects import common
from .base import Oem

_SYSTEM = "/redfish/v1/Systems/1"
_LOG = _SYSTEM + "/LogServices/Log1"
_RESET = {"on": "On", "off": "ForceOff", "reset": "ForceRestart", "reseat": "ACCycle"}


class Smc(Oem):
    name = "smc"
    fan_chassis = ("1",)

    def fan_control(self, device, action):
        if action not in ("auto", "max"):
            raise NotImplementedError(action)
        mode = "FullSpeed" if action == "max" else "Optimal"
        status, _ = device.redfish.patch(
            "/redfish/v1/Managers/1/Oem/Supermicro/FanMode",
            {"FanMode": mode},
        )
        if status in (200, 202, 204):
            return "ok"
        raise RuntimeError("FanMode PATCH failed")

    def power(self, device, action):
        if action in _RESET:
            common.post(
                device,
                _SYSTEM + "/Actions/ComputerSystem.Reset",
                {"ResetType": _RESET[action]},
            )
            return []
        data = common.resource(device, _SYSTEM, required=True)
        return ["{}: {}".format(device.name, common.jq(data.get("PowerState")))]

    def eventlog(self, device, action):
        if action == "clear":
            text = self._clear_one(device, _LOG + "/Actions/LogService.ClearLog")
            return ["{}: {}".format(device.name, text)] if text else []
        lines = common.log_lines(device, _LOG + "/Entries", sep=" ")
        return ["{}: {}".format(device.name, line) for line in lines]
