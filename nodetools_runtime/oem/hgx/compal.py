"""Compal HGX systems (reference ``oem/compal/*.cpl``).

Endpoints are pinned to ``Systems/Self`` / ``Managers/BMC`` / ``Chassis/Self``.
"""

import re
import time

from ...diagnostics import debug_connection
from ...errors import NodetoolsError, TransportError
from ...subjects import bios, common, vm
from ..base import Oem, _body_lines, _jq_r, firmware_name_lines

_SYSTEM = "/redfish/v1/Systems/Self"
_MANAGER = "/redfish/v1/Managers/BMC"
_BIOS_SD = _SYSTEM + "/Bios/SD"
_SEL = _MANAGER + "/LogServices/SEL"
_EVENTLOG = _MANAGER + "/LogServices/EventLog"
_DECODED = _MANAGER + "/LogServices/DecodedLog"
_VM_MANAGER = "BMC"
_VM_IFACE = "CD1"

_BIOS_BASE = {
    "Attributes": {
        "S0Slt3BF3OROM": "Enabled",
        "S1Slt8BF3OROM": "Enabled",
        "RedfishEnable": "Enabled",
        "NumaEnable": "Enabled",
        "VirtualNumaEnable": "Disabled",
    }
}
_BIOS_EXCLUDES = ("BootOrder", "MAPIDS", "UefiPriorities")
_EVENT_TYPE = re.compile(r"Event.*Event_Type")


class Compal(Oem):
    name = "compal"
    family = "hgx"
    fan_profile = "compal-hgx"
    bios_base_preset = True
    fan_chassis = ("Self", "1")

    bootorder_system = "Self"
    bios_system = "Self"
    bootorder_quoted = False
    bootorder_sep = ","
    bootorder_expand = False

    # -- BIOS settings (nodebios.cpl) -----------------------------------
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

    # -- event log (nodeeventlog.cpl) -----------------------------------
    def eventlog(self, device, action):
        if action == "clear":
            lines = []
            for log in (_SEL, _EVENTLOG):
                text = self._clear_one(device, log + "/Actions/LogService.ClearLog")
                if text:
                    lines.append(self._label(device, text))
            if device.ipmi:
                try:
                    out = device.ipmi.run("sel", "clear").strip()
                except Exception:  # noqa: BLE001 - best-effort clear
                    out = ""
                lines.extend(self._label(device, line) for line in out.splitlines())
            return lines
        if action == "hmc":
            return self._entry_lines(device, _DECODED)
        return self._entry_lines(device, _SEL)

    def _entry_lines(self, device, log):
        rows = common.log_lines(device, log + "/Entries", sep=" ")
        return [
            self._label(device, _EVENT_TYPE.sub("Event_Type", row)) for row in rows
        ]

    # -- firmware inventory (nodeinventory.cpl) -------------------------
    def firmware_inventory(self, device):
        return [self._label(device, row) for row in firmware_name_lines(device)]

    # -- NIC / MAC inventory (nodemacs.cpl) -----------------------------
    def macs(self, device, action):
        return self._macs_iface_pairs(device, "Self", "BMC")

    # -- virtual media (nodevm.cpl) -------------------------------------
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
            return [self._label(device, line) for line in lines]
        if action in ("mount", "insert"):
            _, body = vm.post(
                device,
                self._vm_insert_path(_VM_MANAGER, _VM_IFACE),
                self._vm_cifs_payload(options),
            )
            return self._label_lines(device, _body_lines(body, pretty=True))
        if action == "eject":
            lines = []
            coll = common.resource(device, _MANAGER + "/VirtualMedia")
            for oid in common.member_ids(coll):
                if "CD1" not in oid:
                    continue
                iface = common.resource(device, oid)
                if iface.get("Image") in (None, "null"):
                    continue
                _, body = vm.post(device, oid + "/Actions/VirtualMedia.EjectMedia", {})
                lines += _body_lines(body, pretty=True)
            return self._label_lines(device, lines)
        if action == "stop":
            return self._vm_stop(device, options)
        mgr = common.resource(device, _MANAGER, required=True)
        status = ((mgr.get("Oem") or {}).get("Ami") or {}).get("VirtualMedia", {}).get(
            "RMediaStatus"
        )
        iface = common.resource(device, self._vm_iface_path(_VM_MANAGER, _VM_IFACE))
        row = "RMediaStatus:{},ID:{},NAME:{},Image:{},Inserted:{},ConnectedVia:{},RedirectionStatus:{}".format(
            _jq_r(status),
            _jq_r(iface.get("Id")),
            _jq_r(iface.get("ImageName")),
            _jq_r(iface.get("Image")),
            _jq_r(iface.get("Inserted")),
            _jq_r(iface.get("ConnectedVia")),
            _jq_r(iface.get("RedirectionStatus")),
        )
        return [self._label(device, row)]

    def _vm_stop(self, device, options):
        import requests
        from urllib.parse import unquote

        _, source = vm.image_source(options, "smb", "cifs")
        name = unquote(source.path.rsplit("/", 1)[-1])
        if not name or "/" in name or "\\" in name:
            raise NodetoolsError("Compal stop requires an image file URL")

        url = "https://{}/api".format(device.address)
        session = requests.Session()
        session.verify = False
        user = device.cred("bmc_user") or ""
        password = device.cred("bmc_password") or ""
        debug_connection(getattr(options, "debug", False), "compal:" + device.name,
                         "HTTPS login", device.address, user, password)
        try:
            login = session.post(
                url + "/session",
                data={"username": user, "password": password},
                timeout=20,
            )
            login.raise_for_status()
            token = (login.json() or {}).get("CSRFToken")
            if not token:
                raise TransportError("stop-media: no CSRF token")
            headers = {
                "X-CSRFTOKEN": token,
                "X-Requested-With": "XMLHttpRequest",
                "Origin": url.rsplit("/api", 1)[0],
            }
            resp = session.post(
                url + "/settings/media/remote/stop-media",
                json={
                    "image_name": name,
                    "image_type": 1,
                    "image_index": 0,
                    "image_share_type": "cifs",
                },
                headers=headers,
                timeout=20,
            )
            resp.raise_for_status()
            text = resp.text.strip()
        except (requests.RequestException, ValueError) as exc:
            raise TransportError("stop-media request failed") from exc
        finally:
            session.close()
        return self._label_lines(device, text.splitlines())
