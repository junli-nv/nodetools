"""NVIDIA N5500_LD, GB300 NVSwitch BMC 88.0002.1962 read layout."""

from .reading_layouts import NVIDIA_N5500
from ..fixed_reads import FixedManagementReads
from ..redfish import RedfishDiscovery


class NvidiaN5500(FixedManagementReads, RedfishDiscovery):
    name = "nvidia"
    nvos_psu_is_hsc = True
    nvos_fixed_caps = ("psu",)
    inventory_fixed_caps = ("temp",)
    direct_read_sets = NVIDIA_N5500
    bmc_psu_collection = "/redfish/v1/Chassis/{}/PowerSubsystem/PowerSupplies"
    family = "nvl"
    read_system_id = "System_0"
    read_manager_id = "BMC_0"
    read_chassis_id = "System_0"
    read_log_paths = {
        "status": (("Systems", "system_id", "System_0"), "/LogServices/EventLog/Entries"),
        "bmc": (("Managers", "manager_id", "BMC_0"), "/LogServices/EventLog/Entries"),
    }
