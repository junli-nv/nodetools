"""Lenovo XCC (FanSpeedBoost control)."""

from .base import Oem


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
