"""``nodevm`` - virtual media (mount/eject ISO) via the ODM Redfish dialect.

The action set and endpoints differ per ODM (reference ``oem/<vendor>/nodevm*``);
the compute-tray ODM dispatches to its own implementation, everything else falls
back to the NVIDIA/AMI ``AMIVirtualMedia`` behaviour.
"""


def collect(device, action, options):
    return device.odm.vm(device, action, options)
