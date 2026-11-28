"""AIVRES HGX systems (reference ``oem/aivres/*.avr``).

Endpoints are pinned to ``Systems/1`` / ``Managers/1`` / ``Chassis/1``; fan
control and NIC inventory use the ``Oem.Public`` namespace.
"""

import json
import time

from ...errors import CapUnsupported
from ...errors import NodetoolsError
from ...subjects import common, power, vm
from ..base import Oem, _body_lines, _jq_r, _members_static

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
    bios_base_preset = True
    monitor_sensor_collection = "/ThresholdSensors"
    fan_chassis = ("1", "Chassis_0")
    fan_read_chassis = ("1",)
    inventory_fixed_caps = ("fan", "psu")
    psu_read_endpoint = ("1", "legacy")
    monitor_preferred_chassis = "1"

    bootorder_system = "1"
    bios_system = "1"
    bios_write_target = "settings"
    bios_parent_etag = True
    bootorder_quoted = False
    bootorder_sep = ","
    bootorder_expand = False


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
        if action == "status":
            return power.host_status(device.name, data.get("PowerState"))
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
        common.resource(device, _THERMAL, required=True)
        status, _ = common.patch(device, _THERMAL, body)
        if status not in (200, 202, 204):
            raise RuntimeError("Thermal PATCH returned {}".format(status))

    # -- BIOS settings (nodebios.avr) -----------------------------------
    def bios(self, device, action):
        if action == "base":
            parent = _SYSTEM + "/Bios"
            path = parent + "/Settings"
            common.resource(device, parent, required=True)
            body = common.require_write_success(path, common.patch(device, path, _BIOS_BASE, parent))
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
            if not isinstance(value, dict) or "password" in outer.lower():
                continue
            for inner, ivalue in value.items():
                if "password" in inner.lower():
                    continue
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
    def _compute_mac_rows(self, device, validated=False):
        from ...subjects import boot_nics

        lines = []
        chassis = device.vars.get("chassis_id")
        base = "/redfish/v1/Chassis/" + chassis if chassis else _CHASSIS
        adapters = common.resource(device, base + "/NetworkAdapters", required=validated)
        for path in common.member_ids(adapters):
            adapter = common.resource(device, path, required=validated)
            if not adapter:
                continue
            pub = (adapter.get("Oem") or {}).get("Public") or {}
            card = ",".join(
                _jq_r(pub.get(field, ""))
                for field in ("CardModel", "DeviceLocator", "NetworkTechnology")
            )
            ports = pub.get("NetworkPorts")
            links = common.member_ids(ports)
            if isinstance(ports, list):
                links = [entry.get("@odata.id") for entry in ports if isinstance(entry, dict) and entry.get("@odata.id")]
            if not links:
                resource = adapter.get("NetworkPorts") or {}
                collection = resource.get("@odata.id") or path + "/NetworkPorts"
                links = common.member_ids(common.resource(device, collection))
            port_count = 0
            for oid in links:
                port = common.resource(device, oid, required=validated)
                if not port.get("AssociatedNetworkAddresses"):
                    continue
                port_count += 1
                ppub = (port.get("Oem") or {}).get("Public") or {}
                values = port.get("AssociatedNetworkAddresses") or []
                if validated:
                    values = [boot_nics.format_mac(value) for value in values if boot_nics.mac(value)]
                    if not values:
                        port_count -= 1
                        continue
                addresses = ",".join(_jq_r(value) for value in values)
                lines.append(
                    ",".join(
                        (
                            _jq_r(ppub.get("BDF", "")).replace("-", ":"),
                            _jq_r(port.get("VendorId") or pub.get("VenderID") or ""),
                            addresses,
                            _jq_r(port.get("LinkStatus", "")),
                            _jq_r(port.get("CurrentLinkSpeedMbps") or ppub.get("LinkSpeed") or ""),
                            card,
                        )
                    )
                )
            if not port_count:
                resource = adapter.get("NetworkDeviceFunctions") or {}
                collection = resource.get("@odata.id") or path + "/NetworkDeviceFunctions"
                functions = common.resource(device, collection)
                for oid in common.member_ids(functions):
                    entry = common.resource(device, oid, required=validated)
                    eth = entry.get("Ethernet") or {}
                    mac = eth.get("PermanentMACAddress") or eth.get("MACAddress")
                    if validated and not boot_nics.mac(mac):
                        mac = eth.get("MACAddress")
                    if mac and (not validated or boot_nics.mac(mac)):
                        address = boot_nics.format_mac(mac) if validated else mac
                        lines.append("{},{},{},,,{}".format(entry.get("Id", ""), pub.get("VenderID", ""), address, card))
        return lines

    def macs(self, device, action):
        return [self._label(device, line) for line in self._compute_mac_rows(device)]

    # -- virtual media (nodevm.avr) -------------------------------------
    vm_protocols = ("nfs",)
    vm_mount_steps = ("mount", "insert")
    vm_extra_actions = ("media", "umount")

    def vm(self, device, action, options=None):
        action = (action or "status").strip().lower() or "status"
        if action == "mount":
            _, source = vm.image_source(options, "nfs")
            nfs_path = source.path
            if nfs_path.lower().endswith(".iso"):
                nfs_path = nfs_path.rpartition("/")[0] or "/"
            _, body = vm.post(
                device,
                _VMM + "/RemoteMount",
                {"Host": source.netloc, "Path": nfs_path, "Protocol": "NFS"},
            )
            lines = _body_lines(body, pretty=True)
            lines += self._vm_field(device, "RemoteMount")
            return self._label_lines(device, lines)
        if action == "umount":
            _, body = vm.post(device, _VMM + "/RemoteUmount", {"Protocol": "NFS"})
            lines = _body_lines(body)
            lines += self._vm_field(device, "RemoteMount")
            return self._label_lines(device, lines)
        if action == "media":
            body = device.redfish.get(_VMM + "/BrowserRemoteFiles?$filter=Path eq NFS")
            return self._label_lines(device, _body_lines(body))
        if action in ("insert", "eject"):
            _, source = vm.image_source(options, "nfs")
            iso = source.path.rsplit("/", 1)[-1]
            if not iso.lower().endswith(".iso"):
                raise NodetoolsError("Aivres insert/eject requires an NFS ISO file URL")
            suffix = "RemoteMediaInsert" if action == "insert" else "RemoteMediaEject"
            payload = {"ImagePath": "NFS/{}".format(iso)}
            if action == "insert":
                payload["FileType"] = "File"
            _, body = vm.post(device, _VMM + "/" + suffix, payload)
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
