"""``nodeboot`` - set the next boot device over IPMI (compute only).

Legacy: the top-level ``nodeboot`` script sources ``passwd.sh`` and runs
``ipmitool chassis bootdev pxe|disk|bios`` on the compute BMC.
"""


def collect(device, action, options):
    return device.odm.boot(device, action)
