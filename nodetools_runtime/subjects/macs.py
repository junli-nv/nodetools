"""Read compute tray MAC addresses exposed by the BMC."""

from ..errors import CapUnsupported


def collect(device, action, options):
    lines = device.odm.macs(device, action)
    if not lines:
        raise CapUnsupported("BMC did not expose compute tray MAC addresses")
    return lines
