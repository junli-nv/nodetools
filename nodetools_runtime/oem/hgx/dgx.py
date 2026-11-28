"""NVIDIA DGX (HGX) system."""

import json

from ...subjects import common
from ..base import FIRMWARE, Oem, _body_lines, _jq_r

_BIOS = "/redfish/v1/Systems/DGX/LogServices/BIOS"
_HGX = "/redfish/v1/Systems/HGX_Baseboard_0/LogServices/EventLog"
_AC_CYCLE = "/redfish/v1/Chassis/BMC/Actions/Oem/NvidiaChassis.AuxPowerReset"


class Dgx(Oem):
    name = "dgx"
    family = "hgx"
    fan_profile = "dgx-hgx"
    fan_chassis = ("DGX", "1")
    fan_read_ipmi = True
    inventory_fixed_caps = ("fan", "psu")
    psu_read_endpoint = ("DGX", "modern")
    monitor_preferred_chassis = "DGX"

    bootorder_system = "DGX"
    bios_system = "DGX"
    bootorder_quoted = False
    bootorder_sep = ","
    bootorder_expand = False

    _BIOS_EXCLUDES = ("BootOrder", "MAPIDS", "RFServiceIp", "UefiPriorities")

    def macs(self, device, action):
        return self._system_macs(device, "DGX")

    def bios(self, device, action):
        rows = self._bios_rows(device, self._BIOS_EXCLUDES, sort=True)
        return [self._label(device, row) for row in rows]


    def _reseat(self, device):
        """func-dgx.sh ``power_ac_cycle``: ``Chassis/BMC`` AuxPowerReset with
        ``AuxPowerCycleForce`` (the DGX variant of the GBXX aux power cycle)."""
        device.redfish.set_deadline(None)
        _, body = common.post(device, _AC_CYCLE, {"ResetType": "AuxPowerCycleForce"})
        return [self._label(device, line) for line in _body_lines(body, pretty=True)]

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


class DgxBlackwell(Dgx):
    def _boot_order(self, device, quoted=False):
        system = "/redfish/v1/Systems/DGX"
        active = common.resource(device, system + "?$select=Boot/BootOrder")
        order = (active.get("Boot") or {}).get("BootOrder")
        if order is None:
            settings = common.resource(device, system + "/SD", required=True)
            order = (settings.get("Boot") or {}).get("BootOrder")
        return [json.dumps(item) if quoted else _jq_r(item) for item in order or []]
