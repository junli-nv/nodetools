"""Supermicro NVL compute trays."""

from ..redfish import ReadOnlyCompute


class SmcNvl(ReadOnlyCompute):
    """Fallback for Supermicro NVL generations without verified fixed interfaces."""


class SmcGb300(SmcNvl):
    """GB NVL / GB300, BMC 70.02.02.01: two tray DC inputs, not shelf AC PSUs."""

    inventory_fixed_caps = ("psu",)
    psu_read_sensors = (
        ("PDB_0", "PDB_0_HSC_0_Pwr_0", "HSC0"),
        ("PDB_0", "PDB_0_HSC_1_Pwr_0", "HSC1"),
    )

    def __init__(self):
        super().__init__(name="smc", system_id="System_0")
