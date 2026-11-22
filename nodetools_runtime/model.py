"""Device model: identity probing, ODM classification and caching."""

import os

from .config import CACHE_DIR, ODM_CACHE_DIR, ROLE_GROUPS

# Marker substrings -> ODM. More specific vendors first so that generic
# "nvidia" strings on a GB200 tray do not shadow an ODM reading.
SIGNATURES = (
    ("dell", ("dell",)),
    ("lenovo", ("lenovo",)),
    ("smc", ("supermicro", "super micro")),
    ("ami", ("american megatrends", "ami_")),
    ("gigabyte", ("gigabyte", "giga-byte")),
    ("wistron", ("wistron", "wiwynn")),
    ("inventec", ("inventec",)),
    ("byd", ("byd",)),
    ("dgx", ("dgx",)),
    ("gb200", ("gb200", "gb300", "nvidia")),
)


class Device:
    """Everything a capability needs to talk to one BMC."""

    def __init__(self, host, redfish_factory=None, ipmi_factory=None):
        self.host = host
        self.name = host.name
        self.address = host.address
        self.role = host.role
        self.vars = host.vars
        self.redfish = redfish_factory(host) if redfish_factory else None
        self.ipmi = ipmi_factory(host) if ipmi_factory else None
        self.odm = None
        self.probe = {}

    def cred(self, *keys):
        for key in keys:
            value = self.vars.get(key)
            if value:
                return value
        return None

    def close(self):
        if self.redfish:
            self.redfish.close()


def _collect_strings(obj, acc):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, str):
                acc.append(value.lower())
            elif key == "Oem" and isinstance(value, dict):
                acc.extend(k.lower() for k in value.keys())
            else:
                _collect_strings(value, acc)
    elif isinstance(obj, list):
        for item in obj:
            _collect_strings(item, acc)


def probe_redfish(redfish):
    """Gather vendor/model/manufacturer/chassis ids from a BMC."""
    info = {"vendor": None, "model": None, "manufacturer": None,
            "chassis": [], "raw": []}

    root = redfish.get("/redfish/v1")
    if root:
        info["vendor"] = root.get("Vendor") or root.get("Oem", {}).get(
            "Vendor"
        )
        info["raw"].append(root)

    for path in ("/redfish/v1/Managers/1", "/redfish/v1/Systems/1"):
        data = redfish.get(path)
        if data:
            info["model"] = info["model"] or data.get("Model")
            info["manufacturer"] = (
                info["manufacturer"] or data.get("Manufacturer")
            )
            info["raw"].append(data)

    chassis = redfish.get("/redfish/v1/Chassis")
    if chassis and isinstance(chassis.get("Members"), list):
        for member in chassis["Members"]:
            oid = member.get("@odata.id")
            if not oid:
                continue
            ident = oid.rstrip("/").rsplit("/", 1)[-1]
            info["chassis"].append(ident)
            detail = redfish.get(oid)
            if detail:
                info["model"] = info["model"] or detail.get("Model")
                info["manufacturer"] = (
                    info["manufacturer"] or detail.get("Manufacturer")
                )
                info["raw"].append(detail)
    return info


def classify(probe):
    haystack = []
    _collect_strings(probe, haystack)
    for name, markers in SIGNATURES:
        for marker in markers:
            if any(marker in item for item in haystack):
                return name
    return "unknown"


def _cache_path(address):
    safe = address.replace("/", "_").replace(":", "_")
    return os.path.join(ODM_CACHE_DIR, safe)


def load_cached_odm(address):
    path = _cache_path(address)
    if address and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip() or None
    return None


def store_cached_odm(address, odm):
    if not address:
        return
    os.makedirs(ODM_CACHE_DIR, exist_ok=True)
    tmp = _cache_path(address) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(odm)
    os.replace(tmp, _cache_path(address))


def detect_odm(device, use_cache=True):
    if use_cache:
        cached = load_cached_odm(device.address)
        if cached:
            return cached
    probe = probe_redfish(device.redfish)
    device.probe = probe
    odm = classify(probe)
    store_cached_odm(device.address, odm)
    return odm


def role_for_groups(groups):
    for group in groups:
        if group in ROLE_GROUPS:
            return ROLE_GROUPS[group]
    return None
