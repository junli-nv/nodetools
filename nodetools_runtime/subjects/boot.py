"""Set the next boot device on compute nodes."""

import re

from ..errors import CapUnsupported
from . import common

TARGETS = {"pxe": "Pxe", "disk": "Hdd", "bios": "BiosSetup", "cdrom": "Cd"}
SYSTEMS = "/redfish/v1/Systems"
SYSTEM_PATH = re.compile(re.escape(SYSTEMS) + r"/[A-Za-z0-9_.~-]+")


def collect(device, action, options):
    return device.odm.boot(device, action)


def redfish_once(device, action, system_id=None):
    target = TARGETS[action]
    if system_id:
        path = SYSTEMS + "/" + system_id
    else:
        systems = common.resource(device, SYSTEMS, required=True)
        paths = common.member_ids(systems)
        if len(paths) != 1:
            raise CapUnsupported("expected one Redfish system; configure system_id")
        path = paths[0]
    if not SYSTEM_PATH.fullmatch(path) or path.rsplit("/", 1)[-1] in (".", ".."):
        raise CapUnsupported("invalid Redfish boot system path")
    state = common.resource(device, path, required=True).get("Boot") or {}
    if target not in (state.get("BootSourceOverrideTarget@Redfish.AllowableValues") or ()):
        raise CapUnsupported("BMC does not advertise one-time boot target " + target)
    modes = state.get("BootSourceOverrideEnabled@Redfish.AllowableValues")
    if modes is not None and "Once" not in modes:
        raise CapUnsupported("BMC does not advertise the Once boot override")
    common.require_write_success(path, common.patch(device, path, {
        "Boot": {"BootSourceOverrideEnabled": "Once", "BootSourceOverrideTarget": target},
    }, etag_source=path))
    return target
