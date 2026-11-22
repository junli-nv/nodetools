"""Gigabyte GB300 tray."""

from .base import Oem


class Gigabyte(Oem):
    name = "giga"
    fan_chassis = ("1", "Chassis_0")

    def fan_control(self, device, action):
        if action == "auto":
            self._ipmi_raw(device, 0x2E, 0x10, 0x00)
        elif action == "max":
            self._ipmi_raw(device, 0x2E, 0x10, 0x01)
        else:
            raise NotImplementedError(action)
        return "ok"
