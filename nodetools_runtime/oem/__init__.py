"""ODM module registry."""

from .. import model
from .aivres import Aivres
from .ami import Ami
from .base import Oem
from .byd import Byd
from .compal import Compal
from .dell import Dell
from .dell_nvl import DellNvl
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

_PLATFORM_MODULES = {
    ("nvl", "dell", "compute"): DellNvl(),
}

_GENERIC = Oem()

# ODM tokens as returned by model.classify, including aliases.
_ALIASES = {
    "gigabyte": "giga",
}


def get(odm):
    key = _ALIASES.get(odm, odm)
    return _MODULES.get(key, _GENERIC)


def for_device(device, capability=None):
    platform = model.platform_for(device)
    odm = model.odm_for(device)
    module = _PLATFORM_MODULES.get((platform, odm, device.role))
    if module is not None:
        return module
    if platform == "nvl" and odm == "dell":
        return _GENERIC
    if capability == "fan" and platform == "hgx" and odm == "kaytus":
        product = str(model.hardware_identity(device).get("model") or "").upper()
        if product.startswith("KR9288-X3-"):
            return get("aivres")
    if odm in ("nvidia", "gb200"):
        return get("gb200") if platform == "nvl" else _GENERIC
    return get(odm)


def names():
    return sorted(_MODULES)
