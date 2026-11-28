"""BYD tray."""

from ..base import Oem


class Byd(Oem):
    name = "byd"
    family = "hgx"
    fan_profile = "byd-hgx"
    fan_chassis = ("1", "Chassis_0")
    fan_read_ipmi = True
    inventory_fixed_caps = ("fan",)

    bootorder_system = "Self"
    bios_system = "Self"
    bootorder_quoted = False
    bootorder_sep = ","
    bootorder_expand = False

    _BIOS_EXCLUDES = ("SETUP006", "SETUP", "MAPIDS", "FBO")

    def macs(self, device, action):
        return self._system_macs(device, "Self")

    def bios(self, device, action):
        rows = self._bios_rows(device, self._BIOS_EXCLUDES, sort=True)
        return [self._label(device, row) for row in rows]


    # nodeeventlog.byd reads the IPMI SEL list.
    def eventlog(self, device, action):
        if action == "clear":
            text = self._sel_clear(device)
            return ["{}: {}".format(device.name, text)] if text else []
        return self._sel_lines(device)
