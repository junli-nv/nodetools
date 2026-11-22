"""Dell iDRAC (racadm + OME-PMP telemetry)."""

import re

from ..subjects import common
from ..transport.racadm import Racadm
from .base import Oem

_SEL = "/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel"
_SQUEEZE = re.compile(r"\s+")


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

    # nodepower.dell is empty: keep the base IPMI behaviour.

    def eventlog(self, device, action):
        if action == "clear":
            text = self._clear_one(device, _SEL + "/Actions/LogService.ClearLog")
            return ["{}: {}".format(device.name, text)] if text else []
        lines = []
        for entry in common.entries(device, _SEL + "/Entries"):
            row = "{} {} {}".format(
                entry.get("Created", ""),
                entry.get("Severity", ""),
                entry.get("Message", ""),
            )
            lines.append(_SQUEEZE.sub(" ", row).strip())
        return ["{}: {}".format(device.name, line) for line in lines]
