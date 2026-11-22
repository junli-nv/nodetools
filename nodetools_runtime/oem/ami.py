"""AMI MegaRAC generic."""

from .base import Oem


class Ami(Oem):
    name = "ami"
    fan_chassis = ("1", "Chassis_0")

    def fan_control(self, device, action):
        if action == "auto":
            self._ipmi_raw(device, 0x3A, 0x30, 0x00)
        elif action == "max":
            self._ipmi_raw(device, 0x3A, 0x30, 0x01)
        else:
            raise NotImplementedError(action)
        return "ok"
