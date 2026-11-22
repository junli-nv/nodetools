"""AIVRES HGX systems (reference ``oem/aivres/*.avr``).

Endpoints are pinned to ``Systems/1`` / ``Managers/1`` / ``Chassis/1``; fan
control and NIC inventory use the ``Oem.Public`` namespace.
"""

import json
import time

from ..errors import CapUnsupported
from ..subjects import common
from .base import Oem, _body_lines, _jq_r, _members_static

_SYSTEM = "/redfish/v1/Systems/1"
_MANAGER = "/redfish/v1/Managers/1"
_CHASSIS = "/redfish/v1/Chassis/1"
_THERMAL = _CHASSIS + "/Thermal"
_SEL = _MANAGER + "/LogServices/SEL"
_IDL = _MANAGER + "/LogServices/IDL"
_FIRMWARE = "/redfish/v1/UpdateService/FirmwareInventory"
_VMM = _MANAGER + "/VMMService"

_BIOS_BASE = {
    "Attributes": {
        "SocketMpLinkConfig": {"SNC": "Disabled"},
        "SocketProcessorCoreConfig": {
            "VMX": "Disabled",
            "ProcessorX2apic": "Enabled",
        },
        "SocketIioConfig": {"VTdSupport": "Enabled", "InterruptRemap": "Enabled"},
    }
}
_BIOS_EXCLUDES = ("BbsPriority", "VLANs")


def _bbs_number(row):
    """``sort -n -t',' -k1,1`` on the leading BootOptionNumber field."""
    first = row.split(",", 1)[0]
    try:
        return (0, int(first))
    except ValueError:
        return (1, first)


class Aivres(Oem):
    name = "aivres"
    family = "hgx"
    fan_chassis = ("1", "Chassis_0")

    bootorder_system = "1"
    bios_system = "1"
    bootorder_quoted = False
    bootorder_sep = ","
    bootorder_expand = False

    vm_server = "192.168.0.9"
    vm_nfs_path = "/var/www/html/isos"
    vm_iso = "ubuntu-24.04-live.iso"

    # -- power (nodepower.avr) ------------------------------------------
    def power(self, device, action):
        if action == "reseat":
            return self._reseat(device)
        if action in ("on", "off", "reset"):
            reset = {"on": "On", "off": "ForceOff", "reset": "ForceRestart"}[action]
            _, body = common.post(
                device,
                _SYSTEM + "/Actions/ComputerSystem.Reset",
                {"ResetType": reset},
            )
            return [self._label(device, line) for line in _body_lines(body, pretty=True)]
        data = common.resource(device, _SYSTEM, required=True)
        return [self._label(device, json.dumps(data.get("PowerState")))]

    def _reseat(self, device):
        """``raw 0x3c 0x28 0xff 0xFC`` -> sleep 10 -> ``0xFD`` (IPMI)."""
        if not device.ipmi:
            raise CapUnsupported("ipmitool unavailable for {}".format(device.name))
        device.redfish.set_deadline(None)
        device.ipmi.set_deadline(None)
        lines = []
        self._raw_lines(device, lines, 0x3C, 0x28, 0xFF, 0xFC)
        time.sleep(10)
        self._raw_lines(device, lines, 0x3C, 0x28, 0xFF, 0xFD)
        return [self._label(device, line) for line in lines]

    @staticmethod
    def _raw_lines(device, lines, *payload):
        try:
            out = device.ipmi.raw(*payload).strip()
        except Exception as exc:  # noqa: BLE001 - surface the raw error
            out = str(exc)
        if out:
            lines.extend(out.splitlines())

    # -- fan control (nodefan.avr) --------------------------------------
    def fan_control(self, device, action):
        if action == "max":
            self._patch_thermal(
                device,
                {
                    "Oem": {
                        "Public": {
                            "FanControlMode": "Manual",
                            "SmartCooling": {"SmartCooling": "HighPerformance"},
                        }
                    }
                },
            )
            data = common.resource(device, _THERMAL, required=True)
            indexes = sorted(
                {
                    ((fan.get("Oem") or {}).get("Public") or {}).get("FanIndex")
                    for fan in data.get("Fans") or []
                }
                - {None}
            )
            for index in indexes:
                self._patch_thermal(
                    device,
                    {
                        "Fans": [
                            {
                                "Oem": {
                                    "Public": {
                                        "SpeedRatio": 100,
                                        "FanIndex": index,
                                    }
                                }
                            }
                        ]
                    },
                )
            return "ok"
        if action == "auto":
            self._patch_thermal(
                device,
                {
                    "Oem": {
                        "Public": {
                            "FanControlMode": "Auto",
                            "SmartCooling": {"SmartCooling": "Auto"},
                        }
                    }
                },
            )
            return "ok"
        raise NotImplementedError(action)

    @staticmethod
    def _patch_thermal(device, body):
        status, _ = common.patch(device, _THERMAL, body)
        if status not in (200, 202, 204):
            raise RuntimeError("Thermal PATCH returned {}".format(status))

    # -- BIOS settings (nodebios.avr) -----------------------------------
    def bios(self, device, action):
        if action == "base":
            _, body = common.patch(device, _SYSTEM + "/Bios/Settings", _BIOS_BASE)
            lines = _body_lines(body, pretty=True)
            time.sleep(2)
            if device.ipmi:
                try:
                    out = device.ipmi.power("cycle")
                    lines.extend(line for line in out.splitlines() if line.strip())
                except Exception:  # noqa: BLE001 - best-effort cycle
                    pass
            return [self._label(device, line) for line in lines]
        if action == "pending":
            rows = self._bios_rows_at(device, _SYSTEM + "/Bios/Settings")
            return [self._label(device, row) for row in rows]
        return self._bios_status(device)

    def _bios_status(self, device):
        data = common.resource(device, _SYSTEM + "/Bios", required=True)
        rows = set()
        for outer, value in (data.get("Attributes") or {}).items():
            if not isinstance(value, dict):
                continue
            for inner, ivalue in value.items():
                row = '{}."{}": {}'.format(outer, inner, json.dumps(ivalue))
                if "BbsPriority" in row or "VLANs" in row:
                    continue
                rows.add(row)
        return [self._label(device, row) for row in sorted(rows)]

    # -- boot order (nodebootorder.avr) ---------------------------------
    def bootorder(self, device, action):
        data = common.resource(device, _SYSTEM + "/Bios", required=True)
        attrs = data.get("Attributes") or {}
        rows = []
        for key in ("NetworkBbsPriority", "OtherBbsPriority", "HddBbsPriority"):
            for item in attrs.get(key) or []:
                if not isinstance(item, dict):
                    continue
                rows.append(
                    "{},{},{}".format(
                        _jq_r(item.get("BootOptionNumber", "")),
                        _jq_r(item.get("DescriptionName", "")),
                        _jq_r(item.get("DevicePath", "")),
                    )
                )
        rows.sort(key=_bbs_number)
        return [self._label(device, row) for row in rows]

    # -- event log (nodeeventlog.avr) -----------------------------------
    def eventlog(self, device, action):
        if action == "clear":
            lines = []
            for log in (_SEL, _IDL):
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
        if action == "bmc":
            return self._entry_lines(device, _SEL)
        # status / hmc / default -> the IDL log, quoted with the suggestion.
        return self._entry_lines(device, _IDL, suggestion=True, quoted=True)

    def _entry_lines(self, device, log, suggestion=False, quoted=False):
        rows = []
        for entry in common.entries(device, log + "/Entries"):
            text = "{} {} {}".format(
                entry.get("Created", ""),
                entry.get("Severity", ""),
                entry.get("Message", ""),
            )
            if suggestion:
                value = ((entry.get("Oem") or {}).get("Public") or {}).get(
                    "HandlingSuggestion"
                )
                text += " {}".format(_jq_r(value))
            rows.append(json.dumps(text) if quoted else text)
        rows.sort()
        return [self._label(device, row) for row in rows]

    # -- firmware inventory (nodeinventory.avr) -------------------------
    def firmware_inventory(self, device):
        rows = []
        for member in _members_static(device, _FIRMWARE):
            if member.get("FirmwareType") is not None:
                rows.append(
                    "{},{},{}".format(
                        member.get("FirmwareType", ""),
                        member.get("Version", ""),
                        member.get("State", ""),
                    )
                )
            else:
                rows.append(
                    "{} {}".format(member.get("Id", ""), member.get("Version", ""))
                )
        rows.sort()
        return [self._label(device, row) for row in rows]

    # -- NIC / MAC inventory (nodemacs.avr) -----------------------------
    def macs(self, device, action):
        lines = []
        for adapter in self._members(device, _CHASSIS + "/NetworkAdapters"):
            pub = (adapter.get("Oem") or {}).get("Public") or {}
            card = ",".join(
                _jq_r(pub.get(field, ""))
                for field in ("CardModel", "DeviceLocator", "NetworkTechnology")
            )
            for oid in common.member_ids(pub.get("NetworkPorts")):
                port = device.redfish.get(oid)
                if not isinstance(port, dict) or "error" in port:
                    continue
                ppub = (port.get("Oem") or {}).get("Public") or {}
                addresses = ",".join(
                    _jq_r(mac) for mac in port.get("AssociatedNetworkAddresses") or []
                )
                lines.append(
                    ",".join(
                        (
                            _jq_r(ppub.get("BDF", "")).replace("-", ":"),
                            _jq_r(port.get("VendorId", "")),
                            addresses,
                            _jq_r(port.get("LinkStatus", "")),
                            _jq_r(port.get("CurrentLinkSpeedMbps", "")),
                            card,
                        )
                    )
                )
        for iface in self._members(device, _MANAGER + "/EthernetInterfaces"):
            lines.append(
                ",".join(
                    _jq_r(iface.get(field, ""))
                    for field in (
                        "Id",
                        "Name",
                        "PermanentMACAddress",
                        "LinkStatus",
                        "SpeedMbps",
                    )
                )
            )
        return [self._label(device, line) for line in lines]

    # -- virtual media (nodevm.avr) -------------------------------------
    def vm(self, device, action, options=None):
        action = (action or "status").strip().lower() or "status"
        server, nfs_path, iso = self._vm_nfs(options)
        if action == "mount":
            _, body = common.post(
                device,
                _VMM + "/RemoteMount",
                {"Host": server, "Path": nfs_path, "Protocol": "NFS"},
            )
            lines = _body_lines(body, pretty=True)
            lines += self._vm_field(device, "RemoteMount")
            return self._label_lines(device, lines)
        if action == "umount":
            _, body = common.post(device, _VMM + "/RemoteUmount", {"Protocol": "NFS"})
            lines = _body_lines(body)
            lines += self._vm_field(device, "RemoteMount")
            return self._label_lines(device, lines)
        if action == "media":
            body = device.redfish.get(_VMM + "/BrowserRemoteFiles?$filter=Path eq NFS")
            return self._label_lines(device, _body_lines(body))
        if action in ("insert", "eject"):
            suffix = "RemoteMediaInsert" if action == "insert" else "RemoteMediaEject"
            payload = {"ImagePath": "NFS/{}".format(iso)}
            if action == "insert":
                payload["FileType"] = "File"
            _, body = common.post(device, _VMM + "/" + suffix, payload)
            lines = _body_lines(body)
            lines += self._vm_field(device, "VirtualMediaList", each=True)
            return self._label_lines(device, lines)
        lines = self._vm_field(device, "RemoteMount")
        lines += self._vm_field(device, "VirtualMediaList", each=True)
        return self._label_lines(device, lines)

    def _vm_field(self, device, field, each=False):
        data = common.resource(device, _VMM, required=True)
        value = data.get(field)
        if each:
            out = []
            for item in value or []:
                out += _body_lines(item, pretty=True)
            return out
        return _body_lines(value, pretty=True)

    def _vm_nfs(self, options):
        server, nfs_path, iso = self.vm_server, self.vm_nfs_path, self.vm_iso
        image = getattr(options, "image", None) if options is not None else None
        if image:
            rest = image[len("nfs://"):] if image.startswith("nfs://") else image
            server, _, tail = rest.partition("/")
            if tail:
                nfs_path = "/" + tail
            if nfs_path.lower().endswith(".iso"):
                nfs_path, _, iso = nfs_path.rpartition("/")
        return server, nfs_path, iso
