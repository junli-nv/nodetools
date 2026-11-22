"""BYD tray."""

from ..base import Oem


class Byd(Oem):
    name = "byd"
    family = "hgx"
    fan_chassis = ("1", "Chassis_0")

    bootorder_system = "Self"
    bios_system = "Self"
    bootorder_quoted = False
    bootorder_sep = ","
    bootorder_expand = False

    _BIOS_EXCLUDES = ("SETUP006", "SETUP", "MAPIDS", "FBO")

    def macs(self, device, action):
        return self._macs_iface_pairs(device, "Self", "Self")

    def bios(self, device, action):
        rows = self._bios_rows(device, self._BIOS_EXCLUDES, sort=True)
        return [self._label(device, row) for row in rows]

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
