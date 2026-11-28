"""Gigabyte GB300 tray."""

import re

from ...subjects import common
from ..base import Oem, firmware_name_lines

_SEL = "/redfish/v1/Managers/1/LogServices/SEL"
_EVENT_TYPE = re.compile(r"Event.*Event_Type")


class Gigabyte(Oem):
    name = "giga"
    family = "hgx"
    fan_profile = "gigabyte-hgx"
    fan_chassis = ("1", "Chassis_0")
    fan_subsystem_from_manager = True
    inventory_fixed_caps = ("fan", "psu")
    psu_read_endpoint = ("1", "both")
    monitor_preferred_chassis = "1"

    bootorder_system = "1"
    bios_system = "1"
    bootorder_quoted = False
    bootorder_sep = ","
    bootorder_expand = False

    _BIOS_EXCLUDES = (
        "BootOption",
        "SETUP",
        "MapIDlist",
        "LAN",
        "IPaddress",
        "IPMaskaddress",
        "AMIVirtual",
    )

    def macs(self, device, action):
        return self._system_macs(device, "1")

    def bios(self, device, action):
        rows = self._bios_rows(device, self._BIOS_EXCLUDES, sort=True)
        return [self._label(device, row) for row in rows]


    def eventlog(self, device, action):
        if action == "clear":
            lines = []
            text = self._clear_one(device, _SEL + "/Actions/LogService.ClearLog")
            if text:
                lines.append("{}: {}".format(device.name, text))
            text = self._sel_clear(device)
            if text:
                lines.append("{}: {}".format(device.name, text))
            return lines
        lines = common.log_lines(device, _SEL + "/Entries", sep=" ")
        return [
            "{}: {}".format(device.name, _EVENT_TYPE.sub("Event_Type", line))
            for line in lines
        ]

    def firmware_inventory(self, device):
        return [
            "{}: {}".format(device.name, row) for row in firmware_name_lines(device)
        ]
