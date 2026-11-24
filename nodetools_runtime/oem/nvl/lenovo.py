"""Lenovo HG635N_V2 Redfish adapter."""

from ...errors import CapUnsupported
from ...subjects import boot_nics, netboot
from ..redfish import ReadOnlyCompute


class LenovoNvl(ReadOnlyCompute):
    monitor_chassis = "Chassis_0"
    monitor_power_control = "Chassis Power Control"
    monitor_hsc_sensors = {"79": ("79_0", "HSC0_Power"), "84": ("84_0", "HSC1_Power")}

    def __init__(self):
        super().__init__(name="lenovo", system_id="System_0")

    def bootorder(self, device, action):
        system = self._system(device)
        boot = system.get("Boot") or {}
        order = boot.get("BootOrder")
        if not isinstance(order, list):
            raise CapUnsupported("Redfish did not report a persistent boot order")
        if not order:
            return []

        collection = boot_nics.local_link(system.get("BootOptions") or boot.get("BootOptions"))
        if not collection:
            system_path = boot_nics.local_link(system)
            ident = system.get("Id") or device.vars.get("system_id") or self.read_system_id
            if not system_path and ident:
                system_path = boot_nics.local_link("/redfish/v1/Systems/" + str(ident))
            if system_path:
                collection = system_path + "/BootOptions"

        options = {}
        for _, option in boot_nics.members(device, collection):
            key = option.get("BootOptionReference") or option.get("Id")
            if key:
                options[netboot.reference(key)] = [
                    str(option[field]) for field in ("DisplayName", "UefiDevicePath")
                    if option.get(field)
                ]

        rows = ["  ".join([str(item), *options.get(netboot.reference(item), [])]) for item in order]
        return self._label_lines(device, rows)
