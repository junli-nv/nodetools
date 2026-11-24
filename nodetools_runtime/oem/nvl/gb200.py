"""NVIDIA GB200/GB300 reference tray."""

from ...subjects import common, power
from ..base import Oem


class Gb200(Oem):
    name = "gb200"
    family = "nvl"
    fan_profile = "gb200-nvl"
    fan_chassis = ("Chassis_0", "BMC_0")

    def power(self, device, action):
        if action == "status":
            selected = device.vars.get("system_id")
            path = "/redfish/v1/Systems/" + (selected or "System_0")
            state = common.resource(device, path, required=bool(selected)).get("PowerState")
            if state or selected:
                return power.host_status(device.name, state)
        return super().power(device, action)
