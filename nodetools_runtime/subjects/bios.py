"""Read or stage BIOS attributes on compute nodes."""

import json
import re

from ..errors import CapUnsupported, NodetoolsError, TransportError
from . import common


def collect(device, action, options):
    updates = getattr(options, "bios_updates", None)
    if not updates:
        if action == "base" and not getattr(device.odm, "bios_base_preset", False):
            return _standard_base(device)
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


_BASE_FIELDS = {
    "SNC": {"snc", "processorssnc", "subnumacluster", "subnumaclustering"},
    "CPU virtualization": {
        "vmx", "intelvirtualizationtechnology", "processorsintelvirtualizationtechnology",
        "virtualizationtechnology", "procvirtualization", "svmmode", "svm",
    },
}


def base_controls(attributes):
    controls = {}
    for key, current in attributes.items():
        normalized = re.sub(r"[^a-z0-9]", "", key.lower())
        for control, aliases in _BASE_FIELDS.items():
            if normalized not in aliases:
                continue
            if type(current) is bool:
                disabled = False
            elif isinstance(current, str) and current.lower() in ("enabled", "disabled", "auto"):
                disabled = "Disabled"
            else:
                continue
            if control in controls:
                raise CapUnsupported("ambiguous BIOS fields for " + control)
            controls[control] = (key, disabled)
    missing = [control for control in _BASE_FIELDS if control not in controls]
    if missing:
        raise CapUnsupported("base cannot verify BIOS controls for " + ", ".join(missing))
    return dict(controls.values())


def stage_flat_base(device, path, attributes):
    common.resource(device, path, required=True)
    response = common.require_write_success(path, common.patch(device, path, {"Attributes": attributes}))
    readback = common.resource(device, path, required=True).get("Attributes") or {}
    mismatch = [key for key, value in attributes.items() if readback.get(key) != value]
    if mismatch:
        raise TransportError("BIOS preset readback did not match: " + ", ".join(mismatch))
    return response


def _standard_base(device):
    prefix = "/redfish/v1/Systems"
    selected = device.vars.get("system_id")
    if selected:
        systems = [prefix + "/" + selected]
    else:
        systems = common.member_ids(common.resource(device, prefix, required=True))
    paths = set()
    for system in systems:
        if not system.startswith(prefix + "/"):
            continue
        data = common.resource(device, system, required=True)
        path = (data.get("Bios") or {}).get("@odata.id")
        if isinstance(path, str) and path.startswith(system + "/"):
            paths.add(path)
    if len(paths) != 1:
        raise CapUnsupported("base requires exactly one advertised BIOS resource")
    parent = paths.pop()
    data = common.resource(device, parent, required=True)
    controls = base_controls(data.get("Attributes") or {})
    target = ((data.get("@Redfish.Settings") or {}).get("SettingsObject") or {}).get("@odata.id")
    if not isinstance(target, str) or not target.startswith(parent + "/"):
        raise CapUnsupported("BMC did not advertise a BIOS settings resource for base")
    stage_flat_base(device, target, controls)
    return ["{}: BIOS base staged and verified (SNC and CPU virtualization disabled). "
            "Reboot separately to apply.".format(device.name)]
