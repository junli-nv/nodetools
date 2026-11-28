"""Supermicro NVL compute trays."""

from .reading_layouts import SMC_GB300
from ..fixed_reads import FixedManagementReads
from ..redfish import ReadOnlyCompute


class SmcNvl(ReadOnlyCompute):
    """Fallback for Supermicro NVL generations without verified fixed interfaces."""


class SmcGb300(FixedManagementReads, SmcNvl):
    """GB NVL / GB300, BMC 70.02.02.01: two tray DC inputs, not shelf AC PSUs."""

    inventory_fixed_caps = ("psu", "fan", "temp", "inlet", "monitor")
    # Numeric $top/$skip returns inconsistent counts on this layout.
    firmware_page_size = None
    read_system_id = "System_0"
    read_manager_id = "BMC_0"
    read_chassis_id = "Chassis_0"
    bootorder_system = "System_0"
    gpu_reports_module_serials = True
    gpu_inventory_systems = ("HGX_Baseboard_0",)
    probe_fixed_boot_order = False
    macs_from_boot_options = True
    read_nic_inventory = False
    network_inventory_chassis = (
        "IO_Board_0", "IO_Board_1", "Riser_Slot1_BlueField_3_Card",
    )
    inlet_sensor = ("Chassis_0", "Chassis_0_Front_IO_Temp_0")
    monitor_chassis = "Chassis_0"
    monitor_power_unit = "W"
    monitor_power_sensor = "Chassis_0_TotalHSC_Power_0"
    read_log_paths = {
        "status": (("Systems", "system_id", "System_0"), "/LogServices/EventLog/Entries"),
        "bmc": (("Managers", "manager_id", "BMC_0"), "/LogServices/EventLog/Entries"),
        "hmc": (("Managers", "hmc_manager_id", "HGX_BMC_0"), "/LogServices/EventLog/Entries"),
    }
    direct_read_sets = SMC_GB300
    psu_read_sensors = (
        ("PDB_0", "PDB_0_HSC_0_Pwr_0", "HSC0"),
        ("PDB_0", "PDB_0_HSC_1_Pwr_0", "HSC1"),
    )

    def __init__(self):
        super().__init__(name="smc", system_id="System_0")
