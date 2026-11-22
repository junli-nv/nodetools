"""ODM module registry."""

from .. import model
from ..inventory import RESOURCE_ID_KEYS
from .hgx.aivres import Aivres
from .hgx.ami import Ami
from .base import Oem
from .hgx.byd import Byd
from .hgx.compal import Compal
from .hgx.dell import Dell
from .nvl.dell import DellNvl
from .hgx.dgx import Dgx
from .nvl.gb200 import Gb200
from .hgx.gigabyte import Gigabyte
from .redfish import ReadOnlyCompute, RedfishDiscovery
from .nvl.inventec import Inventec
from .hgx.lenovo import Lenovo
from .hgx.netweb import Netweb
from .hgx.smc import Smc
from .nvl.wistron import Wistron

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
    ("nvl", "dell", "compute", None): DellNvl(),
    ("nvl", "lenovo", "compute", "gb300"): ReadOnlyCompute("lenovo", "System_0"),
    ("nvl", "hpe", "compute", "gb300"): ReadOnlyCompute("hpe", "System_0"),
    ("hgx", "hpe", "compute", None): ReadOnlyCompute("hpe", family="hgx"),
}

_GENERIC = Oem()
_NVL_COMPUTE = ReadOnlyCompute()
_DISCOVERED_INFRA = RedfishDiscovery()

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
    key = (platform, odm, device.role)
    generation = model.generation_for(device)
    module = _PLATFORM_MODULES.get((*key, generation)) or _PLATFORM_MODULES.get((*key, None))
    if module is not None:
        return module
    if device.role in ("nvswitch", "powershelf"):
        scoped = any(device.vars.get(field) for field in RESOURCE_ID_KEYS)
        legacy = platform == "nvl" and odm in ("nvidia", "dgx")
        if not legacy or scoped:
            return _DISCOVERED_INFRA
    if capability == "fan" and platform == "hgx" and odm == "kaytus":
        product = str(model.hardware_identity(device).get("model") or "").upper()
        if product.startswith("KR9288-X3-"):
            return get("aivres")
    if platform == "nvl" and odm in ("nvidia", "dgx", "gb200"):
        return get("gb200")
    adapter = get(odm)
    if platform == "nvl" and device.role == "compute" and (
        adapter is _GENERIC or adapter.family != "nvl"
    ):
        return _NVL_COMPUTE
    if platform == "hgx" and adapter.family != "hgx":
        return _GENERIC
    return adapter


def names():
    return sorted(_MODULES)
