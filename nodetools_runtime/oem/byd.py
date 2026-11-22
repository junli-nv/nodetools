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

    # nodeeventlog.byd reads the IPMI SEL list.
    def eventlog(self, device, action):
        if action == "clear":
            text = self._sel_clear(device)
            return ["{}: {}".format(device.name, text)] if text else []
        return self._sel_lines(device)
