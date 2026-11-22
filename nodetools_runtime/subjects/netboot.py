"""``nodenetboot`` - force continuous PXE boot / inspect NICs (compute only).

Legacy: ``network_boot_gb200`` patches the boot override and boot order; some
ODMs expose ``mac`` (list PXE-capable NICs) and ``net`` (configure PXE devices).
"""


def collect(device, action, options):
    return device.odm.netboot(device, action)
