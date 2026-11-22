"""Gigabyte GB300 tray."""

import re

from ..subjects import common
from .base import Oem, firmware_name_lines

_SEL = "/redfish/v1/Managers/1/LogServices/SEL"
_EVENT_TYPE = re.compile(r"Event.*Event_Type")


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
