"""BYD tray."""

from .base import Oem


class Byd(Oem):
    name = "byd"
    fan_chassis = ("1", "Chassis_0")

    def fan_control(self, device, action):
        if action == "auto":
            self._ipmi_raw(device, 0x3C, 0x06)
        elif action == "max":
            self._ipmi_raw(device, 0x3C, 0x03)
        else:
            raise NotImplementedError(action)
        return "ok"
