"""Lite-On NVL power-shelf PSU, firmware inventory and event log support."""

from ...subjects import common, inventory
from .reading_layouts import LITEON_PF1333
from ..fixed_reads import FixedManagementReads
from ..redfish import RedfishDiscovery


class LiteOnPowerShelf(FixedManagementReads, RedfishDiscovery):
    name = "liteon"
    read_manager_id = "bmc"
    read_system_id = "system"
    read_chassis_id = "powershelf"
    read_log_paths = {
        "status": (("Managers", "manager_id", "bmc"), "/LogServices/EventLog/Entries"),
    }
    # PF-1333-7RB/9RB have two chassis aliases for the same shelf.
    # Modern resources supply identity; legacy Power supplies input watts.
    psu_read_endpoint = ("powershelf", "both")
    psu_legacy_slots = ("powershelf", "powerdevice", 6)
    inventory_fixed_caps = ("psu", "fan", "temp")
    direct_read_sets = LITEON_PF1333
    _infrastructure_eventlog_actions = ("status", "clear")

    def infrastructure_inventory(self, device):
        rows = inventory._firmware_rows(device, self._firmware_inventory_path(device))
        missing = [row for row in rows if str(row[0]).startswith("FW_PSU_") and _missing_version(row[1])]
        if missing:
            modules = _psu_inventory(device)
            if len(modules) >= len(missing):
                rows = [row for row in rows if row not in missing]
            else:
                for row in missing:
                    row[1] = "N/A"
            rows.extend(modules)
        if not any(str(row[0]).startswith(("FW_PSU_", "PSU_")) for row in rows):
            rows.extend(_psu_inventory(device))
        return inventory._format_firmware(device, rows)


def _missing_version(version):
    return version is None or str(version).strip().lower() in ("", "null", "n/a")


def _psu_inventory(device):
    chassis = device.vars.get("chassis_id") or "powershelf"
    path = "/redfish/v1/Chassis/{}/PowerSubsystem/PowerSupplies".format(chassis)
    modules = {}
    for oid in common.member_ids(common.resource(device, path)):
        data = common.resource(device, oid)
        ident, version = data.get("Id"), data.get("FirmwareVersion")
        if ident is not None:
            label = "PSU_{}".format(ident)
            modules[label] = [label, "N/A" if _missing_version(version) else version, oid, "FirmwareVersion"]
    return [modules[label] for label in sorted(modules)]
