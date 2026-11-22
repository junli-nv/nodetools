"""AMI MegaRAC generic."""

import re

from ..subjects import common
from .base import Oem, firmware_name_lines

_SEL = "/redfish/v1/Managers/1/LogServices/SEL"
_EVENT_TYPE = re.compile(r"Event.*Event_Type")


class Ami(Oem):
    name = "ami"
    fan_chassis = ("1", "Chassis_0")

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
        return self._macs_iface_pairs(device, "1", "1")

    def bios(self, device, action):
        rows = self._bios_rows(device, self._BIOS_EXCLUDES, sort=True)
        return [self._label(device, row) for row in rows]

    def fan_control(self, device, action):
        if action == "auto":
            self._ipmi_raw(device, 0x3A, 0x30, 0x00)
        elif action == "max":
            self._ipmi_raw(device, 0x3A, 0x30, 0x01)
        else:
            raise NotImplementedError(action)
        return "ok"

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
