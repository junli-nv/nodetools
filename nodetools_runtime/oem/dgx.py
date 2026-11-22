"""NVIDIA DGX (HGX) system."""

from ..subjects import common
from .base import FIRMWARE, Oem

_BIOS = "/redfish/v1/Systems/DGX/LogServices/BIOS"
_HGX = "/redfish/v1/Systems/HGX_Baseboard_0/LogServices/EventLog"


class Dgx(Oem):
    name = "dgx"
    fan_chassis = ("DGX", "1")

    bootorder_system = "DGX"
    bios_system = "DGX"
    bootorder_quoted = False
    bootorder_sep = ","
    bootorder_expand = False

    _BIOS_EXCLUDES = ("BootOrder", "MAPIDS", "RFServiceIp", "UefiPriorities")

    def macs(self, device, action):
        return self._macs_iface_pairs(device, "DGX", "BMC", "PermanentMACAddress")

    def bios(self, device, action):
        rows = self._bios_rows(device, self._BIOS_EXCLUDES, sort=True)
        return [self._label(device, row) for row in rows]

    def fan_control(self, device, action):
        if action == "auto":
            self._ipmi_raw(device, 0x3C, 0x73, 0)
        elif action == "max":
            self._ipmi_raw(device, 0x3C, 0x73, 0x01)
            self._ipmi_raw(device, 0x3C, 0x74, 100)
        else:
            raise NotImplementedError(action)
        return "ok"

    def eventlog(self, device, action):
        if action == "clear":
            lines = []
            for label, base in (("BMC", _BIOS), ("HMC", _HGX)):
                text = self._clear_one(device, base + "/Actions/LogService.ClearLog")
                if text:
                    lines.append("{}[{}]: {}".format(device.name, label, text))
            return lines
        lines = []
        if action in ("status", "bmc"):
            lines.extend(
                "{}[BMC]: {}".format(device.name, line)
                for line in common.log_lines(device, _BIOS + "/Entries", sep=" ")
            )
        if action in ("status", "hmc"):
            lines.extend(
                "{}[HMC]: {}".format(device.name, line)
                for line in common.log_lines(device, _HGX + "/Entries", sep=" ")
            )
        return lines

    def firmware_inventory(self, device):
        data = common.resource(device, FIRMWARE, required=True)
        ami = (data.get("Oem") or {}).get("Ami") or {}
        rows = [
            "{},{}".format(item.get("Name", ""), item.get("Version", ""))
            for item in ami.get("FirmwareInventory") or []
        ]
        return ["{}: {}".format(device.name, row) for row in rows]
