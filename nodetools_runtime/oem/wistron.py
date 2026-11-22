"""Wistron GB300 tray (Sensor-based readings, GB200-style fan control)."""

from .base import Oem


class Wistron(Oem):
    name = "wistron"
    fan_chassis = ("Chassis_0",)

    def fan_control(self, device, action):
        if action == "auto":
            self._ipmi_raw(device, 0x3C, 0x73, 0)
        elif action == "max":
            self._ipmi_raw(device, 0x3C, 0x73, 0x01)
            self._ipmi_raw(device, 0x3C, 0x74, 100)
        else:
            raise NotImplementedError(action)
        return "ok"
