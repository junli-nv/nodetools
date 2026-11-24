"""ODM module registry."""

from .. import model
from ..inventory import RESOURCE_ID_KEYS
from .hgx.aivres import Aivres
from .hgx.ami import Ami
from .base import Oem
from .hgx.byd import Byd
from .hgx.cisco import CiscoHgx
from .hgx.compal import Compal
from .hgx.dell import Dell
from .nvl.dell import DellNvl
from .nvl.delta import DeltaPowerShelf, legacy_psu_readings
from .hgx.dgx import Dgx, DgxBlackwell
from .hgx.hpe import HpeHgx
from .nvl.nvidia import NvidiaNvl
from .nvl.hpe import HpeNvl
from .nvl.lenovo import LenovoNvl
from .nvl.liteon import LiteOnPowerShelf
from .hgx.gigabyte import Gigabyte
from .redfish import ReadOnlyCompute, RedfishDiscovery
from .nvl.inventec import Inventec
from .hgx.kaytus import KaytusKr9288
from .hgx.lenovo import Lenovo
from .hgx.netweb import Netweb
from .hgx.smc import Smc
from .nvl.smc import SmcNvl
from .nvl.aivres import AivresNvl
from .nvl.kaytus import KaytusNvl
from .nvl.wiwynn import WiwynnNvl
from .nvl.megmeet import MegmeetPowerShelf
from .nvl.flex import FlexPowerShelf
from .nvl.wistron import Wistron

_MODULES = {
    "gb200": NvidiaNvl(),
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

_NVL_COMPUTE = ReadOnlyCompute()
_HPE_NVL = HpeNvl()
_DGX_BLACKWELL = DgxBlackwell()

_PLATFORM_MODULES = {
    ("nvl", "dell", "compute", None): DellNvl(),
    ("nvl", "lenovo", "compute", "gb300"): LenovoNvl(),
    ("nvl", "hpe", "compute", "gb200"): _HPE_NVL,
    ("nvl", "hpe", "compute", "gb300"): _HPE_NVL,
    ("hgx", "hpe", "compute", None): HpeHgx(),
    ("hgx", "cisco", "compute", None): CiscoHgx(),
    ("hgx", "dgx", "compute", "b200"): _DGX_BLACKWELL,
    ("hgx", "dgx", "compute", "b300"): _DGX_BLACKWELL,
    ("nvl", "smc", "compute", None): SmcNvl(),
    ("nvl", "wiwynn", "compute", None): WiwynnNvl(),
    ("nvl", "aivres", "compute", None): AivresNvl(),
    ("nvl", "kaytus", "compute", None): KaytusNvl(),
}

_ROLE_MODULES = {
    ("liteon", "powershelf"): LiteOnPowerShelf(),
    ("delta", "powershelf"): DeltaPowerShelf(),
    ("flex", "powershelf"): FlexPowerShelf(),
    ("megmeet", "powershelf"): MegmeetPowerShelf(),
}

# Older shelves may report only the platform vendor or no vendor at all.
_POWER_SHELF_PSU_FALLBACKS = dict.fromkeys(
    ("delta", "unknown", "nvidia", "dgx"), legacy_psu_readings
)

_GENERIC = Oem()
_KAYTUS_KR9288 = KaytusKr9288()
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
    module = _ROLE_MODULES.get((odm, device.role))
    if module is not None:
        return module
    if device.role in ("nvswitch", "powershelf"):
        scoped = any(device.vars.get(field) for field in RESOURCE_ID_KEYS)
        legacy = platform == "nvl" and odm in ("nvidia", "dgx")
        if not legacy or scoped:
            return _DISCOVERED_INFRA
    if platform == "hgx" and odm in ("kaytus", "aivres") and device.role == "compute":
        product = str(model.hardware_identity(device).get("model") or "").upper()
        if product == "KR9288-X3-A0-F0-01" and generation == "b300":
            return _KAYTUS_KR9288
        if capability == "fan" and odm == "kaytus" and product.startswith("KR9288-X3-"):
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


def powershelf_psu_fallback(device):
    reader = _POWER_SHELF_PSU_FALLBACKS.get(model.odm_for(device))
    return reader(device) if reader else []


def names():
    return sorted(_MODULES)
