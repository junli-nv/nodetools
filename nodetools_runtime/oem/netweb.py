"""Netweb HGX systems (reference ``oem/netweb/*.nw``).

Endpoints are pinned to ``Systems/System_0`` / ``Managers/BMC_0`` /
``Chassis/BMC_0``.
"""

import time

from ..subjects import common
from .base import Oem, _body_lines, _jq_r, firmware_name_lines

_SYSTEM = "/redfish/v1/Systems/System_0"
_MANAGER = "/redfish/v1/Managers/BMC_0"
_CHASSIS = "/redfish/v1/Chassis/BMC_0"
_BIOS_SD = _SYSTEM + "/Bios/SD"
_LOGS = _CHASSIS + "/LogServices/Logs"
_VM_MANAGER = "BMC_0"
_VM_IFACE = "CD1"

_BIOS_BASE = {
    "Attributes": {
        "ARUF_ADV_2C7": "ARUF_ADV_2C7Disabled",
        "ASRR_ADV_006": "ASRR_ADV_006Disabled",
    }
}
_BIOS_EXCLUDES = ("MAPIDS", "SETUP")


def _duty(value):
    """``raw 0x3a 0xd0 0x0e``-style payload repeated over the 16 fan channels."""
    return [value] * 16


class Netweb(Oem):
    name = "netweb"
    family = "hgx"
    fan_chassis = ("BMC_0", "System_0")

    bootorder_system = "System_0"
    bios_system = "System_0"
    bootorder_quoted = False
    bootorder_sep = ","
    bootorder_expand = False

    # -- fan control (nodefan.nw) ---------------------------------------
    def fan_control(self, device, action):
        if action == "auto":
            self._ipmi_raw(device, 0x3A, 0xD0, 0x11, *_duty(0x00))
        elif action == "low":
            self._ipmi_raw(device, 0x3A, 0xD0, 0x11, *_duty(0x02))
            self._ipmi_raw(device, 0x3A, 0xD0, 0x0E, *_duty(0x32))
        elif action == "opt":
            self._ipmi_raw(device, 0x3A, 0xD0, 0x11, *_duty(0x02))
            self._ipmi_raw(device, 0x3A, 0xD0, 0x0E, *_duty(0x50))
        elif action == "max":
            self._ipmi_raw(device, 0x3A, 0xD0, 0x11, *_duty(0x02))
            self._ipmi_raw(device, 0x3A, 0xD0, 0x0E, *_duty(0x64))
        else:
            raise NotImplementedError(action)
        return "ok"

    # -- BIOS settings (nodebios.nw) ------------------------------------
    def bios(self, device, action):
        if action == "base":
            _, body = common.patch(device, _BIOS_SD, _BIOS_BASE)
            lines = [
                self._label(device, line) for line in _body_lines(body, pretty=True)
            ]
            lines.append(
                self._label(
                    device,
                    "INFO: cycle the node to make the bios settings take effect",
                )
            )
            return lines
        rows = self._bios_rows(device, _BIOS_EXCLUDES, sort=True)
        return [self._label(device, row) for row in rows]

    # -- event log (nodeeventlog.nw) ------------------------------------
    def eventlog(self, device, action):
        if action == "clear":
            text = self._clear_one(device, _LOGS + "/Actions/LogService.ClearLog")
            return [self._label(device, text)] if text else []
        rows = []
        for entry in common.entries(device, _LOGS + "/Entries"):
            rows.append(
                "{} {} {} Sensor_Number:{} {}".format(
                    entry.get("Created", ""),
                    entry.get("Severity", ""),
                    entry.get("SensorType", ""),
                    entry.get("SensorNumber", ""),
                    entry.get("EntryCode", ""),
                )
            )
        rows.sort()
        return [self._label(device, row) for row in rows]

    # -- firmware inventory (nodeinventory.nw) --------------------------
    def firmware_inventory(self, device):
        return [self._label(device, row) for row in firmware_name_lines(device)]

    # -- NIC / MAC inventory (nodemacs.nw) ------------------------------
    def macs(self, device, action):
        return self._macs_iface_pairs(device, "System_0", "BMC_0")

    # -- virtual media (nodevm.nw) --------------------------------------
    def vm(self, device, action, options=None):
        action = (action or "status").strip().lower() or "status"
        if action == "enable":
            _, body = common.post(
                device,
                _MANAGER + "/Actions/Oem/AMIVirtualMedia.EnableRMedia",
                {"RMediaState": "Enable"},
            )
            lines = _body_lines(body, pretty=True)
            time.sleep(5)
            mgr = common.resource(device, _MANAGER, required=True)
            lines.append(_jq_r(((mgr.get("Oem") or {}).get("Ami") or {}).get("VirtualMedia", {}).get("RMediaStatus")))
            return self._label_lines(device, lines)
        if action == "insert":
            _, body = common.post(
                device,
                self._vm_insert_path(_VM_MANAGER, _VM_IFACE),
                self._vm_cifs_payload(options),
            )
            return self._label_lines(device, _body_lines(body, pretty=True))
        if action == "eject":
            lines = []
            coll = common.resource(device, _MANAGER + "/VirtualMedia")
            for oid in common.member_ids(coll):
                iface = common.resource(device, oid)
                if iface.get("Image") in (None, "null"):
                    continue
                _, body = common.post(device, oid + "/Actions/VirtualMedia.EjectMedia", {})
                lines += _body_lines(body, pretty=True)
            return self._label_lines(device, lines)
        mgr = common.resource(device, _MANAGER, required=True)
        status = ((mgr.get("Oem") or {}).get("Ami") or {}).get("VirtualMedia", {}).get(
            "RMediaStatus"
        )
        lines = ["RMediaStatus: {}".format(_jq_r(status))]
        coll = common.resource(device, _MANAGER + "/VirtualMedia")
        for oid in common.member_ids(coll):
            iface = common.resource(device, oid)
            lines.append(
                "{}\t{}\t{}".format(
                    _jq_r(iface.get("Id")),
                    _jq_r(iface.get("ImageName")),
                    _jq_r(iface.get("Image")),
                )
            )
        return self._label_lines(device, lines)
