"""``nodebootorder`` - list the effective boot order (compute only).

Legacy: ``query_gb200_bootorder`` expands ``Systems/system/BootOptions`` and
prints the ``Id``/``DisplayName``/``UefiDevicePath`` triple for every entry
referenced by ``Boot.BootOrder``.
"""


def collect(device, action, options):
    return device.odm.bootorder(device, action)
