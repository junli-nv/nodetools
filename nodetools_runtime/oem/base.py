"""Base class shared by ODM modules.

The default implementation is the NVIDIA GBXX reference (the legacy top-level
scripts + ``func.sh``): IPMI power, the ``Systems/system`` + ``HGX_Baseboard_0``
event logs and ``UpdateService/FirmwareInventory``. Vendor subclasses override
only where their HGX x86 script differs, falling back to ``super()`` when the
vendor-specific endpoint is absent (which is how GBXX trays of that vendor keep
working).
"""

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


class Oem:
    """Default ODM behaviour: NVIDIA GBXX reference."""

    name = "generic"
    fan_chassis = ("Chassis_0", "1", "System.Embedded.1", "DGX")

    system_id = SYSTEM_ID
    bmc_id = BMC_ID
    hgx_baseboard_id = HGX_BASEBOARD_ID

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
