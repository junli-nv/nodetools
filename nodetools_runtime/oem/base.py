"""Base class shared by ODM modules.

The default implementation is the NVIDIA GBXX reference (the legacy top-level
scripts + ``func.sh``): IPMI power, the ``Systems/system`` + ``HGX_Baseboard_0``
event logs and ``UpdateService/FirmwareInventory``. Vendor subclasses override
only where their HGX x86 script differs, falling back to ``super()`` when the
vendor-specific endpoint is absent (which is how GBXX trays of that vendor keep
working).
"""

import json
import re

from ..errors import CapUnsupported
from ..subjects import common

# func.sh identifiers (GBXX / NVIDIA reference).
SYSTEM_ID = "system"
CHASSIS_ID = "Chassis_0"
BMC_ID = "BMC_0"
HGX_BASEBOARD_ID = "HGX_Baseboard_0"
HGX_BMC_ID = "HGX_BMC_0"

FIRMWARE = "/redfish/v1/UpdateService/FirmwareInventory"


def _parse_sel(text):
    """Reproduce ``func.sh``'s ``sel list | awk`` transform."""
    out = []
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) < 3:
            continue
        date = parts[1].replace(" ", "")
        time = parts[2].replace(" ", "")
        seg = date.split("/")
        if len(seg) == 3:
            date = "{}-{}-{}".format(seg[2], seg[0], seg[1])
        tail = " ".join(part.strip() for part in parts[3:6]).strip()
        out.append("{}T{} {}".format(date, time, tail).strip())
    out.sort()
    return out


def _jq_r(value):
    """Render a JSON value the way ``jq -r`` prints a scalar."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def _grep_word(rows, token):
    """Reproduce ``grep -w <token>`` over a list of lines."""
    if token == "":
        return list(rows)
    pattern = re.compile(
        r"(?<![A-Za-z0-9_]){}(?![A-Za-z0-9_])".format(re.escape(token))
    )
    return [row for row in rows if pattern.search(row)]


def _format_mac(mac_a):
    """``query_gb200_macs`` MAC rendering: chars 4..16 colon-separated, cut to 17."""
    body = mac_a[4:16]
    return ":".join(body[i : i + 2] for i in range(0, len(body), 2))[:17]


def _body_lines(body, pretty=False):
    """Split a Redfish response body into display lines."""
    if isinstance(body, (dict, list)):
        text = json.dumps(body, indent=2) if pretty else json.dumps(body)
    elif isinstance(body, str):
        text = body.strip()
    else:
        text = ""
    return text.splitlines() if text else []


class Oem:
    """Default ODM behaviour: NVIDIA GBXX reference."""

    name = "generic"
    fan_chassis = ("Chassis_0", "1", "System.Embedded.1", "DGX")

    system_id = SYSTEM_ID
    bmc_id = BMC_ID
    hgx_baseboard_id = HGX_BASEBOARD_ID

    # -- boot-family hooks (overridden per ODM) -------------------------
    # ``None`` means "use system_id".
    bootorder_system = None
    bios_system = None
    bootorder_fields = ("Id", "DisplayName", "UefiDevicePath")
    bootorder_quoted = True  # GBXX pipes through ``jq`` (quoted); ODMs use ``jq -r``
    bootorder_sep = "\t"
    bootorder_expand = True  # GBXX expands members; ODMs fetch each member

    # -- controls --------------------------------------------------------
    def fan_control(self, device, action):
        raise CapUnsupported(
            "fan action {!r} not supported for ODM {!r}".format(action, self.name)
        )

    def psu_control(self, device, action):
        raise CapUnsupported(
            "psu action {!r} not supported for ODM {!r}".format(action, self.name)
        )

    # -- power (nodepower) ----------------------------------------------
    def power(self, device, action):
        if not device.ipmi:
            raise CapUnsupported("ipmitool unavailable for {}".format(device.name))
        try:
            out = device.ipmi.power(action).strip()
        except Exception:  # noqa: BLE001 - legacy scripts echo N/A on failure
            out = "N/A"
        return ["{}: {}".format(device.name, out or "N/A")]

    # -- event log (nodeeventlog) ---------------------------------------
    def eventlog(self, device, action):
        if action == "clear":
            return self._clear_eventlog(device)
        if action == "bmc":
            return self._sel_lines(device)
        return self._hgx_log_lines(device)

    def _clear_eventlog(self, device):
        lines = []
        sys_log = "/redfish/v1/Systems/{}/LogServices/EventLog".format(self.system_id)
        bmc = "/redfish/v1/Managers/{}".format(self.bmc_id)
        for path in (
            sys_log + "/Actions/LogService.ClearLog",
            bmc + "/LogServices/SEL/Actions/LogService.ClearLog",
            bmc + "/LogServices/EventLog/Actions/LogService.ClearLog",
        ):
            text = self._clear_one(device, path)
            if text:
                lines.append("{}[BMC]: {}".format(device.name, text))
        text = self._sel_clear(device)
        if text:
            lines.append("{}[BMC]: {}".format(device.name, text))
        hgx = "/redfish/v1/Systems/{}/LogServices/EventLog/Actions/LogService.ClearLog".format(
            self.hgx_baseboard_id
        )
        text = self._clear_one(device, hgx)
        if text:
            lines.append("{}[HMC]: {}".format(device.name, text))
        return lines

    def _clear_one(self, device, path):
        try:
            status, body = common.post(device, path, {})
        except Exception:  # noqa: BLE001 - best-effort clear
            return ""
        return common.jq_out(body)

    def _sel_clear(self, device):
        if not device.ipmi:
            return ""
        try:
            return device.ipmi.run("sel", "clear").strip()
        except Exception:  # noqa: BLE001
            return ""

    def _sel_lines(self, device):
        if not device.ipmi:
            raise CapUnsupported("ipmitool unavailable for {}".format(device.name))
        try:
            raw = device.ipmi.run("sel", "list")
        except Exception:  # noqa: BLE001
            raw = ""
        return [
            "{}[BMC]: {}".format(device.name, line) for line in _parse_sel(raw)
        ]

    def _hgx_log_lines(self, device):
        path = "/redfish/v1/Systems/{}/LogServices/EventLog/Entries".format(
            self.hgx_baseboard_id
        )
        rows = []
        for entry in common.entries(device, path):
            rows.append(
                "\t".join(
                    str(entry.get(key, ""))
                    for key in ("Created", "Severity", "Message")
                )
            )
        rows.sort()
        return ["{}[HMC]: {}".format(device.name, row) for row in rows]

    # -- firmware inventory (nodeinventory) -----------------------------
    def firmware_inventory(self, device):
        rows = _firmware_id_version(device, FIRMWARE)
        # query_firmware prints ``${node}:${line}`` with no space after the colon.
        return ["{}:{}".format(device.name, row) for row in common.column_t(rows)]

    # -- boot device (nodeboot) -----------------------------------------
    def boot(self, device, action):
        action = (action or "pxe").strip().lower() or "pxe"
        if not device.ipmi:
            raise CapUnsupported("ipmitool unavailable for {}".format(device.name))
        try:
            out = device.ipmi.run("chassis", "bootdev", action)
        except Exception:  # noqa: BLE001 - legacy nodeboot prints nothing on failure
            return []
        return [
            "{}: {}".format(device.name, line.rstrip("\r"))
            for line in out.splitlines()
            if line.strip()
        ]

    # -- boot order (nodebootorder) -------------------------------------
    def bootorder(self, device, action):
        rows = self._boot_option_rows(device)
        lines = []
        for item in self._boot_order(device):
            token = item[4:] if item.startswith("Boot") else item
            matches = _grep_word(rows, token) or _grep_word(rows, item)
            lines.extend(matches)
        return [self._label(device, line, spaced=True) for line in lines]

    # -- MAC addresses (nodemacs) ---------------------------------------
    def macs(self, device, action):
        lines = []
        for row in self._boot_option_rows(device):
            if "PXEv4" not in row and not re.search("PXE.*IPv4", row):
                continue
            sed = row.replace("/MAC", '"MAC').replace("/IPv4", '"IPv4')
            parts = sed.split('"')
            boot = parts[1] if len(parts) > 1 else ""
            bus_b = parts[5] if len(parts) > 5 else ""
            mac_a = parts[6] if len(parts) > 6 else ""
            lines.append("OS {} {} {}".format(boot, _format_mac(mac_a), bus_b))
        for ident, mac in self._bmc_macs(device):
            lines.append("BMC {}\t{}".format(ident, mac))
        return ["{}:{}".format(device.name, line) for line in lines]

    def _bmc_macs(self, device):
        out = []
        for data in self._iface_members(
            device,
            "/redfish/v1/Managers/{}/EthernetInterfaces".format(self.bmc_id),
        ):
            out.append(
                (
                    json.dumps(data.get("Id", "")),
                    json.dumps(data.get("MACAddress", "")),
                )
            )
        return out

    # -- BIOS settings (nodebios) ---------------------------------------
    def bios(self, device, action):
        rows = self._bios_rows(device, ("MAPIDS", "SETUP006"))
        return ["{}:{}".format(device.name, row) for row in rows]

    # -- network boot override (nodenetboot) ----------------------------
    def netboot(self, device, action):
        name = device.name
        system = "/redfish/v1/Systems/{}".format(self._sys(self.bootorder_system))
        lines = ["Enable continuous PXE boot override option"]
        _, body = common.patch(
            device,
            system,
            {
                "Boot": {
                    "BootSourceOverrideEnabled": "Continuous",
                    "BootSourceOverrideMode": "UEFI",
                    "BootSourceOverrideTarget": "Pxe",
                }
            },
        )
        lines.extend(_body_lines(body))
        data = common.resource(device, system, required=True)
        boot = data.get("Boot") or {}
        lines.append(
            "{},{},{}".format(
                _jq_r(boot.get("BootSourceOverrideEnabled")),
                _jq_r(boot.get("BootSourceOverrideMode")),
                _jq_r(boot.get("BootSourceOverrideTarget")),
            )
        )
        order = self._boot_order(device, quoted=True)
        lines.append("OLD_BOOT_ORDER={}".format(",".join(order)))
        targets = self._network_targets(device)
        new = targets + [item for item in order if item not in targets]
        common.patch(device, system, {"Boot": {"BootOrder": new}})
        lines.append("New BootOrder:")
        lines.append(",".join(self._boot_order(device)))
        lines.append("INFO: reboot node to take effect")
        return ["{}:{}".format(name, line) for line in lines]

    def _network_targets(self, device):
        targets = []
        for member in self._boot_members(device, True):
            if "PciRoot(0x16)" in (member.get("UefiDevicePath") or ""):
                ident = member.get("Id")
                if ident:
                    targets.append(json.dumps(ident))
        return targets

    # -- boot-family helpers --------------------------------------------
    def _sys(self, override):
        return override or self.system_id

    def _label(self, device, line, spaced=True):
        if spaced:
            return "{}: {}".format(device.name, line)
        return "{}:{}".format(device.name, line)

    def _boot_option_rows(self, device):
        rows = []
        for member in self._boot_members(device, self.bootorder_expand):
            cells = []
            for field in self.bootorder_fields:
                value = member.get(field, "")
                cells.append(json.dumps(value) if self.bootorder_quoted else _jq_r(value))
            rows.append(self.bootorder_sep.join(cells))
        return rows

    def _boot_members(self, device, expand=True):
        suffix = "/BootOptions?$expand=." if expand else "/BootOptions"
        path = "/redfish/v1/Systems/{}{}".format(self._sys(self.bootorder_system), suffix)
        coll = common.resource(device, path, required=True)
        out = []
        for member in coll.get("Members") or []:
            if not isinstance(member, dict):
                continue
            if len(member) > 1:
                out.append(member)
                continue
            oid = member.get("@odata.id")
            if not oid:
                continue
            data = device.redfish.get(oid)
            if isinstance(data, dict) and "error" not in data:
                out.append(data)
        return out

    def _boot_order(self, device, quoted=False):
        path = "/redfish/v1/Systems/{}?$select=Boot/BootOrder".format(
            self._sys(self.bootorder_system)
        )
        data = common.resource(device, path, required=True)
        order = (data.get("Boot") or {}).get("BootOrder") or []
        return [json.dumps(item) if quoted else _jq_r(item) for item in order]

    def _iface_members(self, device, path):
        coll = common.resource(device, path)
        out = []
        for oid in common.member_ids(coll):
            data = device.redfish.get(oid)
            if isinstance(data, dict) and "error" not in data:
                out.append(data)
        return out

    def _iface_rows(self, device, path, fields, sep="\t"):
        rows = []
        for data in self._iface_members(device, path):
            rows.append(sep.join(_jq_r(data.get(field, "")) for field in fields))
        return rows

    def _macs_iface_pairs(self, device, system, manager, manager_field="MACAddress"):
        lines = self._iface_rows(
            device,
            "/redfish/v1/Systems/{}/EthernetInterfaces".format(system),
            ("Id", "MACAddress", "UefiDevicePath"),
        )
        lines += self._iface_rows(
            device,
            "/redfish/v1/Managers/{}/EthernetInterfaces".format(manager),
            ("Id", manager_field),
        )
        return [self._label(device, line) for line in lines]

    def _bios_rows(self, device, excludes=(), sort=False):
        path = "/redfish/v1/Systems/{}/Bios".format(self._sys(self.bios_system))
        return self._bios_rows_at(device, path, excludes=excludes, sort=sort)

    def _bios_rows_at(self, device, path, excludes=(), sort=False):
        data = common.resource(device, path, required=True)
        rows = [
            "{}={}".format(key, _jq_r(value))
            for key, value in (data.get("Attributes") or {}).items()
        ]
        if excludes:
            rows = [row for row in rows if not any(x in row for x in excludes)]
        if sort:
            rows = sorted(set(rows))
        return rows

    # -- helpers ---------------------------------------------------------
    def _ipmi_raw(self, device, *payload):
        if not device.ipmi:
            raise CapUnsupported("ipmitool unavailable for {}".format(device.name))
        return device.ipmi.raw(*payload)

    def _members(self, device, path):
        coll = common.resource(device, path, required=True)
        out = []
        for oid in common.member_ids(coll):
            data = device.redfish.get(oid)
            if isinstance(data, dict) and "error" not in data:
                out.append(data)
        return out


def firmware_name_lines(device, path=FIRMWARE):
    """AMI-style inventory: ``Name Version`` or ``FirmwareType,Version,State``."""
    rows = []
    for data in _members_static(device, path):
        if data.get("Name") is not None:
            rows.append(
                "{} {}".format(data.get("Name", ""), data.get("Version", ""))
            )
        elif data.get("FirmwareType") is not None:
            rows.append(
                "{},{},{}".format(
                    data.get("FirmwareType", ""),
                    data.get("Version", ""),
                    data.get("State", ""),
                )
            )
    rows.sort()
    return rows


def _firmware_id_version(device, path):
    rows = []
    for data in _members_static(device, path):
        rows.append([data.get("Id", ""), data.get("Version", "")])
    return rows


def _members_static(device, path):
    coll = common.resource(device, path, required=True)
    out = []
    for oid in common.member_ids(coll):
        data = device.redfish.get(oid)
        if isinstance(data, dict) and "error" not in data:
            out.append(data)
    return out
