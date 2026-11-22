"""``nodemacs`` - list PXE MACs (OS) and BMC MACs (compute only).

Legacy: ``query_gb200_macs`` derives OS NIC MACs from the PXE boot option
``UefiDevicePath`` and BMC MACs from ``Managers/BMC_0/EthernetInterfaces``.
"""


def collect(device, action, options):
    return device.odm.macs(device, action)
