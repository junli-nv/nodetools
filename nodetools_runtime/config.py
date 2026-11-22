"""Static configuration: defaults and locations."""

import os

VERSION = "0.1.0"

# Capabilities implemented in this phase.
CAPS = ("fan", "psu", "temp")

DEFAULT_JOBS = 18
DEFAULT_TIMEOUT = 10
DEFAULT_SUMMARY = False
# Whole-node wall-clock budget for a capability run: once it is spent, the
# transport stops issuing new requests instead of hanging on a slow BMC.
# No CLI flag on purpose (keep the interface small); set node_timeout in the
# [nodehealth:defaults] inventory section to override.
DEFAULT_NODE_TIMEOUT = 30.0

# Default inventory file (relative to CWD) and its template.
DEFAULT_INVENTORY = "inventory.ini"
EXAMPLE_INVENTORY = "inventory.example.ini"

# Reserved inventory section holding CLI defaults.
DEFAULTS_SECTION = "nodehealth:defaults"

CACHE_DIR = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
    "nodetools",
)
ODM_CACHE_DIR = os.path.join(CACHE_DIR, "oem")

# Per-BMC Redfish interface map produced by `nodetools scan` and reused by the
# capability commands. Bump SCAN_CACHE_VERSION whenever the schema changes so
# stale files are ignored instead of misread.
SCAN_CACHE_DIR = os.path.join(CACHE_DIR, "scan")
SCAN_CACHE_VERSION = 2

# Roles and the inventory group that maps to each one.
ROLE_GROUPS = {
    "compute": "compute",
    "nvswitch": "nvswitch",
    "powershelf": "powershelf",
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
