"""NVIDIA DGX (HGX) system."""

from .base import Oem


class Dgx(Oem):
    name = "dgx"
    fan_chassis = ("DGX", "1")

    def fan_control(self, device, action):
        if action == "auto":
            self._ipmi_raw(device, 0x3C, 0x73, 0)
        elif action == "max":
            self._ipmi_raw(device, 0x3C, 0x73, 0x01)
            self._ipmi_raw(device, 0x3C, 0x74, 100)
        else:
            raise NotImplementedError(action)
        return "ok"
