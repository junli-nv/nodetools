"""Lenovo HG635N_V2 Redfish adapter."""

from ..redfish import ReadOnlyCompute


class LenovoNvl(ReadOnlyCompute):
    monitor_chassis = "Chassis_0"
    monitor_power_control = "Chassis Power Control"
    monitor_hsc_sensors = {"79": ("79_0", "HSC0_Power"), "84": ("84_0", "HSC1_Power")}

    def __init__(self):
        super().__init__(name="lenovo", system_id="System_0")
