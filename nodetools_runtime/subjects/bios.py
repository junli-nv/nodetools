"""``nodebios`` - show (or stage) BIOS settings (compute only).

Legacy: ``query_bios_current_settings`` dumps ``Systems/system/Bios``
``Attributes`` as ``key=value``; some ODMs add ``base``/``pending`` actions that
stage settings on a pending BIOS resource and reboot.
"""


def collect(device, action, options):
    return device.odm.bios(device, action)
