"""inventory: ``nodeinventory`` / ``nvswinventory`` / ``psinventory``.

Read-only firmware inventory. NVSwitch and power shelf read
``UpdateService/FirmwareInventory`` (``Id`` + ``Version``); compute trays
dispatch to the ODM handler.
"""

from . import common

FIRMWARE = "/redfish/v1/UpdateService/FirmwareInventory"


def collect(device, action, options):
    if device.role in ("nvswitch", "powershelf"):
        return _firmware(device)
    return device.odm.firmware_inventory(device)


def _firmware(device):
    coll = common.resource(device, FIRMWARE, required=True)
    rows = []
    for oid in common.member_ids(coll):
        data = device.redfish.get(oid)
        if isinstance(data, dict) and "error" not in data:
            rows.append([data.get("Id", ""), data.get("Version", "")])
    return [
        "{}: {}".format(device.name, row) for row in common.column_t(rows)
    ]
