"""Device model and independent platform and OEM identification."""

import re

from .config import ROLE_GROUPS

# Marker substrings -> ODM. More specific vendors first so that generic
# "nvidia" strings on a GB200 tray do not shadow an ODM reading.
SIGNATURES = (
    ("dell", ("dell",)),
    ("cisco", ("cisco", "ucs c880a m8", "ucsc-c880a-m8")),
    ("lenovo", ("lenovo",)),
    ("smc", ("supermicro", "super micro")),
    ("gigabyte", ("gigabyte", "giga-byte")),
    ("wistron", ("wistron",)),
    ("wiwynn", ("wiwynn",)),
    ("hpe", ("hewlett packard enterprise", "hpe")),
    ("liteon", ("lite-on", "liteon", "lite on")),
    ("delta", ("delta electronics", "delta")),
    ("megmeet", ("megmeet", "mpsc6000-mcu")),
    ("flex", ("flextronics", "flex power shelf", "flex powershelf", "nvd-p-0000adt00")),
    ("inventec", ("inventec",)),
    ("byd", ("byd",)),
    ("aivres", ("aivres",)),
    ("kaytus", ("kaytus",)),
    ("compal", ("compal",)),
    ("netweb", ("netweb",)),
    ("dgx", ("dgx",)),
    ("ami", ("american megatrends", "ami_")),
    ("nvidia", ("nvidia",)),
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
        self.identity = {}
        # Cached scan map for this BMC (``nodetools scan``), attached by
        # ``runtime.build_device``; ``None`` when there is no cache.
        self.profile = None

    def cred(self, *keys):
        for key in keys:
            value = self.vars.get(key)
            if value:
                return value
        return None

    @property
    def transport_errors(self):
        """Number of connection-level failures seen by either transport."""
        total = 0
        for transport in (self.redfish, self.ipmi):
            total += getattr(transport, "transport_errors", 0)
        return total

    def close(self):
        if self.redfish:
            self.redfish.close()


_PLATFORM_PATTERNS = (
    ("nvl", re.compile(r"\b(?:gb[ -]?(?:200|300)|nvl(?:72|36)?)\b", re.I)),
    ("hgx", re.compile(r"\b(?:hgx(?:[-_]?\d+)?|b[ -]?(?:200|300))\b", re.I)),
)


def classify(probe):
    """Identify the system OEM, falling back to the Redfish vendor."""
    system = " ".join(
        str(probe.get(key) or "") for key in ("manufacturer", "model")
    ).lower()
    vendor = str(probe.get("vendor") or "").lower()
    for source in (system, vendor):
        for name, markers in SIGNATURES:
            if name not in ("ami", "nvidia") and any(marker in source for marker in markers):
                return name
    for name, markers in SIGNATURES:
        if any(marker in system or marker in vendor for marker in markers):
            return name
    return "unknown"


def platform_family(identity, name=""):
    for source in (identity.get("model"), name):
        for platform, pattern in _PLATFORM_PATTERNS:
            if pattern.search(str(source or "")):
                return platform
    return "unknown"


def hardware_identity(device):
    return {
        **(getattr(device, "profile", None) or {}),
        **{key: value for key, value in (getattr(device, "identity", None) or {}).items() if value},
    }


_GENERATION = re.compile(r"\b(gb|b)[ _-]?(200|300)\b", re.I)


_MODEL_GENERATIONS = {
    ("cisco", "UCS C880A M8"): "b300",
    ("cisco", "UCS C880A M8 RACK SERVER"): "b300",
    ("cisco", "UCSC-C880A-M8"): "b300",
    ("lenovo", "HG635N_V2"): "gb300",
    ("kaytus", "KR9288-X3-A0-F0-01"): "b300",
    ("aivres", "KR9288-X3-A0-F0-01"): "b300",
    ("aivres", "KRS8000"): "gb200",
    ("aivres", "KRS8000V3"): "gb200",
    ("aivres", "KRS8500V3"): "gb300",
    ("smc", "SYS-822GS-NB3RT"): "b300",
}


def generation_for(device):
    override = device.vars.get("generation", "").strip().lower()
    if override == "unknown":
        return override
    if override and not _GENERATION.fullmatch(override):
        raise ValueError("Unsupported inventory generation: " + override)
    platform = device.vars.get("platform", "").strip()
    platform_generation = platform if _GENERATION.fullmatch(platform) else ""
    source = override or platform_generation or hardware_identity(device).get("model") or ""
    generations = {prefix.lower() + number for prefix, number in _GENERATION.findall(str(source))}
    if len(generations) == 1:
        return generations.pop()
    product = str(hardware_identity(device).get("model") or "").strip().upper()
    return _MODEL_GENERATIONS.get((odm_for(device), product), "unknown")


# Platform-only aliases for product names that omit the accelerator generation.
_MODEL_PLATFORMS = {
    ("hpe", "XD685"): "hgx",
    ("hpe", "XD690"): "hgx",
    ("hpe", "PROLIANT COMPUTE XD685"): "hgx",
    ("hpe", "HPE PROLIANT COMPUTE XD685"): "hgx",
    ("hpe", "COMPUTE XD690"): "hgx",
    ("hpe", "HPE COMPUTE XD690"): "hgx",
    ("dell", "XE9712"): "nvl",
    ("dell", "POWEREDGE XE9712"): "nvl",
}


def platform_for(device):
    override = (getattr(device, "vars", {}) or {}).get("platform", "").strip().lower()
    if override:
        for platform, pattern in _PLATFORM_PATTERNS:
            if pattern.fullmatch(override):
                return platform
        if override != "unknown":
            raise ValueError("Unsupported inventory platform: " + override)
        return override
    generation = generation_for(device)
    if generation != "unknown":
        return "nvl" if generation.startswith("gb") else "hgx"
    identity = hardware_identity(device)
    product = str(identity.get("model") or "").strip().upper()
    family = _MODEL_PLATFORMS.get((odm_for(device), product)) or platform_family(
        identity, getattr(device, "name", "")
    )
    if family == "unknown" and getattr(device, "role", None) in ("nvswitch", "powershelf"):
        return "nvl"
    return family


def odm_for(device):
    override = (getattr(device, "vars", {}) or {}).get("oem", "").strip().lower()
    if override:
        override = {"giga": "gigabyte", "gb200": "nvidia"}.get(override, override)
        if override != "unknown" and all(name != override for name, _ in SIGNATURES):
            raise ValueError("Unsupported inventory OEM: " + override)
        return override
    identity = hardware_identity(device)
    if getattr(device, "role", None) == "powershelf" and any(
        str(identity.get(field) or "").strip().casefold() == "flex"
        for field in ("manufacturer", "vendor")
    ):
        return "flex"
    detected = classify(identity)
    if detected != "unknown":
        return detected
    cached = identity.get("odm")
    # The old gb200 bucket also included unidentified NVIDIA devices.
    return cached if cached and cached != "gb200" else "unknown"


def role_for_groups(groups):
    for group in groups:
        if group in ROLE_GROUPS:
            return ROLE_GROUPS[group]
    return None
