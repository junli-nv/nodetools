"""Base class shared by ODM modules."""

from ..errors import CapUnsupported


class Oem:
    """Default ODM behaviour: no vendor-specific controls."""

    name = "generic"
    fan_chassis = ("Chassis_0", "1", "System.Embedded.1", "DGX")

    # -- controls --------------------------------------------------------
    def fan_control(self, device, action):
        raise CapUnsupported(
            "fan action {!r} not supported for ODM {!r}".format(action, self.name)
        )

    def psu_control(self, device, action):
        raise CapUnsupported(
            "psu action {!r} not supported for ODM {!r}".format(action, self.name)
        )

    # -- helpers ---------------------------------------------------------
    def _ipmi_raw(self, device, *payload):
        if not device.ipmi:
            raise CapUnsupported("ipmitool unavailable for {}".format(device.name))
        return device.ipmi.raw(*payload)
