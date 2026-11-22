"""Dell iDRAC (racadm + OME-PMP telemetry)."""

from ..transport.racadm import Racadm
from .base import Oem


class Dell(Oem):
    name = "dell"
    fan_chassis = ("System.Embedded.1",)

    def fan_control(self, device, action):
        racadm = Racadm(device.address, device.cred("bmc_user"), device.cred("bmc_password"))
        if action == "max":
            racadm.run("set", "system.thermalsettings.ThermalProfile", "Maximum Performance")
        elif action == "auto":
            racadm.run("set", "system.thermalsettings.ThermalProfile", "Default Thermal Profile")
        else:
            raise NotImplementedError(action)
        return "ok"
