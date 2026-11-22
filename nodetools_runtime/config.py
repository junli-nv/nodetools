"""Static configuration: defaults and locations."""

import os

VERSION = "0.1.0"

# Capabilities implemented in this phase.
CAPS = ("fan", "psu", "temp")

# Info commands: subject suffix -> roles that expose it. The prefix names the
# role (see ROLE_PREFIXES), so ``node``+``power`` = ``nodepower``,
# ``nvsw``+``inventory`` = ``nvswinventory`` and so on. One command per legacy
# script: compute carries the ODM-specific subjects, the NVIDIA infrastructure
# roles carry only the ones the original scripts provide.
INFO_SUBJECTS = {
    "power": ("compute", "nvswitch", "powershelf"),
    "eventlog": ("compute", "nvswitch", "powershelf"),
    "inventory": ("compute", "nvswitch", "powershelf"),
    # Boot-family subjects are compute-only: the legacy ``node*`` scripts have
    # no NVSwitch / power-shelf counterpart.
    "boot": ("compute",),
    "bootorder": ("compute",),
    "netboot": ("compute",),
    "macs": ("compute",),
    "bios": ("compute",),
    "vm": ("compute",),
}

# Accepted actions per subject (first entry is the read/default form); used for
# help text and validation. An empty tuple means the subject is read-only.
INFO_ACTIONS = {
    "power": ("status", "on", "off", "cycle", "reset", "reseat"),
    "eventlog": ("status", "bmc", "hmc", "clear"),
    "inventory": (),
    "boot": ("pxe", "disk", "bios", "cdrom"),
    "bootorder": (),
    "netboot": ("status", "mac", "net"),
    "macs": (),
    "bios": ("status", "base", "pending", "bf3"),
    "vm": ("status", "enable", "mount", "umount", "insert", "eject", "stop", "media", "lic"),
}
# Action words that mean "just read" for the info commands (the legacy scripts
# default the action to ``status`` when it is empty).
INFO_READ_WORDS = ("", "status", "check", "read")

DEFAULT_JOBS = 18
DEFAULT_TIMEOUT = 10
DEFAULT_SUMMARY = False
# Whole-node wall-clock budget for a capability run: once it is spent, the
# transport stops issuing new requests instead of hanging on a slow BMC.
# No CLI flag on purpose (keep the interface small); set node_timeout in the
# [nodehealth:defaults] inventory section to override.
DEFAULT_NODE_TIMEOUT = 30.0

# Air-intake ("inlet") temperature check (``nodeinlet``, compute only). The tray
# front sensor should read roughly room ambient; readings outside
# ``INLET_TARGET +/- INLET_TOLERANCE`` are flagged and can be tuned per run with
# ``--target`` / ``--tolerance``.
INLET_TARGET = 25.0
INLET_TOLERANCE = 5.0

# Default inventory file (relative to CWD) and its template.
DEFAULT_INVENTORY = "inventory.ini"
EXAMPLE_INVENTORY = "inventory.example.ini"

# Reserved inventory section holding CLI defaults.
DEFAULTS_SECTION = "nodehealth:defaults"

CACHE_DIR = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
    "nodetools",
)

# The one cache: the per-machine-type Redfish interface map produced by
# `nodetools scan` and reused by every other command (which only read it, never
# write it). It is keyed by the machine identity (vendor/manufacturer/model), so
# one representative tray's template serves every tray of the same type; it also
# carries the ODM identity, so dispatch is a cache lookup, not a probe.
# Bump SCAN_CACHE_VERSION whenever the schema changes so stale files are
# ignored instead of misread.
SCAN_CACHE_DIR = os.path.join(CACHE_DIR, "scan")
SCAN_CACHE_VERSION = 4

# Roles and the inventory group that maps to each one.
ROLE_GROUPS = {
    "compute": "compute",
    "nvswitch": "nvswitch",
    "powershelf": "powershelf",
}

# Display order of the roles and the DeviceType labels the legacy scripts print
# in `nodelist` (from hosts.list column 1). Kept so `nodelist` output lines up
# with the original tools.
ROLE_ORDER = ("compute", "nvswitch", "powershelf")
ROLE_LABELS = {
    "compute": "PhysicalNode",
    "nvswitch": "Switch",
    "powershelf": "PowerShelf",
}

# Command-name prefix -> node role. The legacy scripts encode the role in the
# prefix (``node`` = compute tray, ``nvsw`` = NVSwitch, ``ps`` = power shelf).
ROLE_PREFIXES = {
    "node": "compute",
    "nvsw": "nvswitch",
    "ps": "powershelf",
}

# Role inferred from a hostname when it is not listed in inventory.
NAME_ROLE_PATTERNS = (
    ("-gpu-cp-", "compute"),
    ("-gpu-nvl-", "nvswitch"),
    ("-gpu-pwr-", "powershelf"),
)

# Chassis ids tried by the generic Redfish ladder, in priority order.
DEFAULT_CHASSIS = (
    "Chassis_0",
    "1",
    "System.Embedded.1",
    "DGX",
    "PDB_0",
)
