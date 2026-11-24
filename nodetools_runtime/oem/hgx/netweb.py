"""Netweb HGX systems (reference ``oem/netweb/*.nw``).

Endpoints are pinned to ``Systems/System_0`` / ``Managers/BMC_0`` /
``Chassis/BMC_0``.
"""

import time

from ...errors import NodetoolsError, TransportError
from ...subjects import bios, common, vm
from ..base import Oem, _body_lines, _jq_r, firmware_name_lines

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


class Netweb(Oem):
    name = "netweb"
    family = "hgx"
    fan_profile = "netweb-hgx"
    bios_base_preset = True
    fan_chassis = ("BMC_0", "System_0")

    bootorder_system = "System_0"
    bios_system = "System_0"
    bootorder_quoted = False
    bootorder_sep = ","
    bootorder_expand = False

    fan_actions = ("auto", "low", "opt", "max")


    # -- BIOS settings (nodebios.nw) ------------------------------------
    def bios(self, device, action):
        if action == "base":
            active = common.resource(device, _SYSTEM + "/Bios", required=True)
            controls = bios.base_controls(active.get("Attributes") or {})
            body = bios.stage_flat_base(device, _BIOS_SD, {**_BIOS_BASE["Attributes"], **controls})
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
    vm_extra_actions = ("enable", "stop")

    def vm(self, device, action, options=None):
        action = (action or "status").strip().lower() or "status"
        if action == "enable":
            _, body = vm.post(
                device,
                _MANAGER + "/Actions/Oem/AMIVirtualMedia.EnableRMedia",
                {"RMediaState": "Enable"},
            )
            lines = _body_lines(body, pretty=True)
            time.sleep(5)
            mgr = common.resource(device, _MANAGER, required=True)
            status = ((mgr.get("Oem") or {}).get("Ami") or {}).get("VirtualMedia", {}).get("RMediaStatus")
            if status is not None and str(status).strip().lower() not in ("enabled", "enable"):
                raise TransportError("remote media did not become enabled")
            lines.append(_jq_r(status))
            return self._label_lines(device, lines)
        if action in ("mount", "insert"):
            _, body = vm.post(
                device,
                self._vm_insert_path(_VM_MANAGER, _VM_IFACE),
                self._vm_cifs_payload(options),
            )
            return self._label_lines(device, _body_lines(body, pretty=True))
        if action in ("eject", "stop"):
            image = vm.image_source(options, "smb", "cifs")[0] if action == "stop" else None
            lines = []
            coll = common.resource(device, _MANAGER + "/VirtualMedia")
            matched = False
            for oid in common.member_ids(coll):
                iface = common.resource(device, oid)
                mounted = iface.get("Image")
                if mounted in (None, "", "null") or (image is not None and mounted != image):
                    continue
                matched = True
                _, body = vm.post(device, oid + "/Actions/VirtualMedia.EjectMedia", {})
                lines += _body_lines(body, pretty=True)
            if image is not None and not matched:
                raise NodetoolsError("no mounted Netweb image matches the supplied URL")
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
