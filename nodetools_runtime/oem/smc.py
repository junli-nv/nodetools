"""Supermicro (FanMode control)."""

from .base import Oem


class Smc(Oem):
    name = "smc"
    fan_chassis = ("1",)

    def fan_control(self, device, action):
        if action not in ("auto", "max"):
            raise NotImplementedError(action)
        mode = "FullSpeed" if action == "max" else "Optimal"
        status, _ = device.redfish.patch(
            "/redfish/v1/Managers/1/Oem/Supermicro/FanMode",
            {"FanMode": mode},
        )
        if status in (200, 202, 204):
            return "ok"
        raise RuntimeError("FanMode PATCH failed")
