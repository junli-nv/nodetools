"""Device model: identity (ODM) lookup, classification and transport errors.

Vendor identity is never probed by a command. ``nodetools scan`` walks a BMC
once and records the ODM in its scan map; every other command reads that map
(:func:`odm_for`), so dispatch costs a cache lookup, not a live walk. The only
identity work here is :func:`classify`, which ``scan`` uses to turn a probe
into an ODM string.
"""

from .config import ROLE_GROUPS

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
    ("aivres", ("aivres",)),
    ("compal", ("compal",)),
    ("netweb", ("netweb",)),
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


def classify(probe):
    """Turn a scan probe (or scan profile) into an ODM name."""
    haystack = []
    _collect_strings(probe, haystack)
    for name, markers in SIGNATURES:
        for marker in markers:
            if any(marker in item for item in haystack):
                return name
    return "unknown"


def odm_for(device):
    """ODM recorded by ``nodetools scan`` for this BMC, else ``unknown``.

    A plain cache lookup: commands never walk the BMC to identify a vendor.
    An unscanned node falls back to the base (GBXX/NVIDIA) handler, which is
    the reference the legacy top-level scripts used.
    """
    profile = getattr(device, "profile", None)
    if isinstance(profile, dict):
        odm = profile.get("odm")
        if odm:
            return odm
    return "unknown"


def role_for_groups(groups):
    for group in groups:
        if group in ROLE_GROUPS:
            return ROLE_GROUPS[group]
    return None
