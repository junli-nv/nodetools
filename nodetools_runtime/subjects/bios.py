"""Read or stage BIOS attributes on compute nodes."""

import json

from ..errors import CapUnsupported, NodetoolsError
from . import common


def collect(device, action, options):
    updates = getattr(options, "bios_updates", None)
    if not updates:
        return device.odm.bios(device, action)
    mode = getattr(device.odm, "bios_write_target", None)
    if mode is None:
        raise CapUnsupported("BIOS attribute staging is unavailable for this OEM")
    path = "/redfish/v1/Systems/{}/Bios".format(device.odm.bios_system)
    data = common.resource(device, path, required=True)
    current = data.get("Attributes") or {}
    changes = {}
    for update in updates:
        name, sep, raw = update.partition("=")
        keys = name.split(".")
        if not sep or not name or "password" in name.lower():
            raise NodetoolsError("provide a BIOS attribute as KEY=VALUE")
        original = current
        for key in keys:
            original = original.get(key) if isinstance(original, dict) else None
        if type(original) not in (str, bool, int, float):
            raise NodetoolsError("unknown or unsupported BIOS attribute " + name)
        if type(original) is str:
            value = raw
        else:
            try:
                value = json.loads(raw)
            except ValueError:
                raise NodetoolsError("invalid value for BIOS attribute " + name) from None
        if type(value) is not type(original):
            raise NodetoolsError("incorrect value type for BIOS attribute " + name)
        node = changes
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        if keys[-1] in node:
            raise NodetoolsError("BIOS attribute was specified more than once: " + name)
        node[keys[-1]] = value
    etag_source = path if getattr(device.odm, "bios_parent_etag", False) else None
    if mode == "settings":
        settings = (data.get("@Redfish.Settings") or {}).get("SettingsObject") or {}
        parent = path
        path = settings.get("@odata.id")
        if not isinstance(path, str) or not path.startswith(parent + "/"):
            raise CapUnsupported("BMC did not advertise a BIOS settings resource")
        common.resource(device, path, required=True)
    common.require_write_success(path, common.patch(device, path, {"Attributes": changes}, etag_source))
    return ["{}: BIOS settings staged. Reboot separately to apply.".format(device.name)]
