"""ODM module registry."""

from ..errors import CapUnsupported
from .aivres import Aivres
from .ami import Ami
from .base import Oem
from .byd import Byd
from .compal import Compal
from .dell import Dell
from .dgx import Dgx
from .gb200 import Gb200
from .gigabyte import Gigabyte
from .inventec import Inventec
from .lenovo import Lenovo
from .netweb import Netweb
from .smc import Smc
from .wistron import Wistron

_MODULES = {
    "gb200": Gb200(),
    "dell": Dell(),
    "lenovo": Lenovo(),
    "smc": Smc(),
    "ami": Ami(),
    "byd": Byd(),
    "giga": Gigabyte(),
    "dgx": Dgx(),
    "wistron": Wistron(),
    "inventec": Inventec(),
    "aivres": Aivres(),
    "compal": Compal(),
    "netweb": Netweb(),
}

_GENERIC = Oem()

# ODM tokens as returned by model.classify, including aliases.
_ALIASES = {
    "gigabyte": "giga",
}


def get(odm):
    key = _ALIASES.get(odm, odm)
    return _MODULES.get(key, _GENERIC)


def names():
    return sorted(_MODULES)


def power_shelf_control(device, action):
    """Generic PowerShelf power control shared by every ODM."""
    if action == "cycle":
        raise CapUnsupported("PowerShelf does not advertise PowerCycle")
    body = {"ResetType": "On" if action == "on" else "ForceOff"}
    status, _ = device.redfish.post(
        "/redfish/v1/Chassis/PowerShelf_0/Actions/Chassis.Reset", body
    )
    if status in (200, 202, 204):
        return "ok"
    raise RuntimeError("Chassis.Reset returned {}".format(status))
