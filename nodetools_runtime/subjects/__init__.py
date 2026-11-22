"""Info subjects: verbatim ports of the legacy ``node*``/``nvsw*``/``ps*`` scripts.

Each handler takes ``(device, action, options)`` and returns a list of complete
output lines (already prefixed with the node name), so the report layer just
prints them.
"""

from . import bios, boot, bootorder, eventlog, inventory, macs, netboot, power, vm

HANDLERS = {
    "power": power.collect,
    "eventlog": eventlog.collect,
    "inventory": inventory.collect,
    "boot": boot.collect,
    "bootorder": bootorder.collect,
    "netboot": netboot.collect,
    "macs": macs.collect,
    "bios": bios.collect,
    "vm": vm.collect,
}


def handler_for(name):
    return HANDLERS.get(name)
