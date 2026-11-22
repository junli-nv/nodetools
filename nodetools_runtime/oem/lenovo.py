"""Lenovo XCC (FanSpeedBoost control)."""

from ..errors import CapUnsupported
from ..subjects import common
from .base import FIRMWARE, Oem, _members_static

_SYSTEM = "/redfish/v1/Systems/1"
_PLATFORM_LOG = _SYSTEM + "/LogServices/PlatformLog"
_RESET = {"on": "On", "off": "ForceOff"}


class Lenovo(Oem):
    name = "lenovo"
    fan_chassis = ("1",)

    def fan_control(self, device, action):
        if action not in ("auto", "max"):
            raise NotImplementedError(action)
        value = "High" if action == "max" else "Normal"
        for chassis in self.fan_chassis:
            status, _ = device.redfish.patch(
                "/redfish/v1/Chassis/{}".format(chassis),
                {"Oem": {"Lenovo": {"FanSpeedBoost": value}}},
            )
            if status in (200, 202, 204):
                return "ok"
        raise RuntimeError("FanSpeedBoost PATCH failed")

    def power(self, device, action):
        if action == "reseat":
            raise CapUnsupported("lenovo reseat requires onecli (not available)")
        if action == "cycle":
            data = common.resource(device, _SYSTEM, required=True)
            reset = "ForceRestart" if data.get("PowerState") == "On" else "On"
            return self._reset(device, reset)
        if action in _RESET:
            return self._reset(device, _RESET[action])
        data = common.resource(device, _SYSTEM, required=True)
        return ["{}: PowerState={}".format(device.name, data.get("PowerState"))]

    def _reset(self, device, reset_type):
        status, _ = common.post(
            device, _SYSTEM + "/Actions/ComputerSystem.Reset", {"ResetType": reset_type}
        )
        if 200 <= status < 300:
            return [
                "{}: Reset action '{}' executed successfully.".format(
                    device.name, reset_type
                )
            ]
        return [
            "{}: Reset action '{}' failed with HTTP code {}".format(
                device.name, reset_type, status
            )
        ]

    def eventlog(self, device, action):
        if action == "clear":
            text = self._clear_one(
                device, _PLATFORM_LOG + "/Actions/LogService.ClearLog"
            )
            return ["{}: {}".format(device.name, text)] if text else []
        lines = common.log_lines(device, _PLATFORM_LOG + "/Entries", sep=" ")
        return ["{}: {}".format(device.name, line) for line in lines]

    def firmware_inventory(self, device):
        rows = []
        for data in _members_static(device, FIRMWARE):
            line = "{} {}".format(data.get("Id", ""), data.get("Version", ""))
            if "Pending" not in line:
                rows.append(line)
        rows.sort()
        return ["{}: {}".format(device.name, row) for row in rows]
