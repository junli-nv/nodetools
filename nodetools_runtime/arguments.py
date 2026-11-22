"""Command-line argument parsing.

The command surface mirrors the legacy ``node*`` scripts: the **prefix** names
the node role and the **suffix** names the subject, e.g. ``nodepsu``,
``nodelist``, ``nvswfan``, ``pspsu``::

    node  -> compute tray     fan / psu / temp
    nvsw  -> NVSwitch         fan / psu / temp
    ps    -> power shelf      fan / psu / temp

GBXX needs all three prefixes; BXX only ``node*``.  ``nodelist``/``nodeping``
are role-agnostic and cover the whole inventory.  ``nodetools <command>``
still works, and the legacy short names (``list``/``ping``) are aliases.
"""

import argparse

from .config import (
    CAPS,
    DEFAULT_INVENTORY,
    DEFAULT_JOBS,
    DEFAULT_TIMEOUT,
    INFO_ACTIONS,
    INFO_SUBJECTS,
    ROLE_PREFIXES,
)

SUBJECTS = CAPS

# command name -> (role, capability)
SENSOR_COMMANDS = {
    "{}{}".format(prefix, subject): (role, subject)
    for prefix, role in ROLE_PREFIXES.items()
    for subject in SUBJECTS
}

# command name -> (role, subject) for the info subjects (power/eventlog/...).
INFO_COMMANDS = {
    "{}{}".format(prefix, subject): (role, subject)
    for subject, roles in INFO_SUBJECTS.items()
    for prefix, role in ROLE_PREFIXES.items()
    if role in roles
}

COMMAND_ALIASES = {"list": "nodelist", "ping": "nodeping"}

# programs (argv[0]) that dispatch straight to a command
NODE_PROGRAMS = (
    tuple(SENSOR_COMMANDS)
    + tuple(INFO_COMMANDS)
    + ("nodelist", "nodeping", "nodeconsole")
)


def normalize_command(name):
    return COMMAND_ALIASES.get(name, name)


def _add_inventory_opt(parser):
    parser.add_argument("-i", "--inventory", default=None, help="inventory file")


def _add_selection(parser):
    parser.add_argument("selector", nargs="*", help="hostlist expression / group")
    _add_inventory_opt(parser)
    parser.add_argument("--group", action="append", default=[], help="select a group")
    parser.add_argument("--all", action="store_true", help="select every host")
    parser.add_argument("-x", "--debug", action="store_true", help="verbose logging")


def _add_sensor_opts(parser):
    parser.add_argument(
        "noderange",
        nargs="?",
        help="nodelist expression (defaults to every host of the role)",
    )
    parser.add_argument(
        "action",
        nargs="?",
        default=None,
        help="status/check (read, default) | auto/max (fan) | on/off/cycle (psu)",
    )
    _add_inventory_opt(parser)
    parser.add_argument("--group", action="append", default=[], help="select a group")
    parser.add_argument("--all", action="store_true", help="select every host")
    parser.add_argument(
        "--summary", action="store_true", default=None, help="per-node summary"
    )
    parser.add_argument("--errors-only", action="store_true", help="only non-OK lines")
    parser.add_argument("--jobs", type=int, default=None, help="parallel workers")
    parser.add_argument("--timeout", type=float, default=None, help="per-request timeout")
    parser.add_argument(
        "-a",
        "--action",
        dest="action_opt",
        default=None,
        help="control action (alias for the positional action)",
    )
    parser.add_argument("--user", default=None, help="override BMC username")
    parser.add_argument("--password", default=None, help="override BMC password")
    parser.add_argument(
        "--ipmi-env",
        action="store_true",
        help="pass the IPMI password via IPMI_PASSWORD (ipmitool -E)",
    )
    parser.add_argument("--no-cache", action="store_true", help="skip caches")
    parser.add_argument("-x", "--debug", action="store_true", help="verbose logging")


def _add_info_opts(parser, subject):
    parser.add_argument(
        "noderange",
        nargs="?",
        help="nodelist expression (defaults to every host of the role)",
    )
    actions = [a for a in INFO_ACTIONS.get(subject, ()) if a != "status"]
    help_text = "read (default) | " + " | ".join(actions) if actions else "read-only"
    parser.add_argument("action", nargs="?", default=None, help=help_text)
    if subject == "vm":
        parser.add_argument(
            "image",
            nargs="?",
            default=None,
            help="ISO location, e.g. smb://host/share/x.iso, nfs://host/dir/x.iso",
        )
        parser.add_argument("--vm-user", default=None, help="virtual-media username")
        parser.add_argument(
            "--vm-password", default=None, help="virtual-media password"
        )
    _add_inventory_opt(parser)
    parser.add_argument("--group", action="append", default=[], help="select a group")
    parser.add_argument("--all", action="store_true", help="select every host")
    parser.add_argument("--errors-only", action="store_true", help="only non-OK lines")
    parser.add_argument("--jobs", type=int, default=None, help="parallel workers")
    parser.add_argument("--timeout", type=float, default=None, help="per-request timeout")
    parser.add_argument(
        "-a",
        "--action",
        dest="action_opt",
        default=None,
        help="control action (alias for the positional action)",
    )
    parser.add_argument("--user", default=None, help="override BMC username")
    parser.add_argument("--password", default=None, help="override BMC password")
    parser.add_argument(
        "--ipmi-env",
        action="store_true",
        help="pass the IPMI password via IPMI_PASSWORD (ipmitool -E)",
    )
    parser.add_argument("--no-cache", action="store_true", help="skip caches")
    parser.add_argument("-x", "--debug", action="store_true", help="verbose logging")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="nodetools",
        description="Inspect GB200/GB300 nodes over Redfish and IPMI.",
    )
    parser.add_argument("--version", action="store_true", help="print version")
    sub = parser.add_subparsers(dest="command")

    inv = sub.add_parser("inventory", help="inventory management")
    inv_sub = inv.add_subparsers(dest="inventory_command")
    init = inv_sub.add_parser("init", help="generate inventory from hosts.list")
    init.add_argument("--from", dest="source", default="hosts.list")
    init.add_argument("-o", "--output", default=DEFAULT_INVENTORY)
    show = inv_sub.add_parser("show", help="show parsed inventory")
    show.add_argument("-i", "--inventory", default=None)

    lst = sub.add_parser("nodelist", aliases=["list"], help="list hosts (aggregated)")
    _add_selection(lst)
    lst.add_argument(
        "--long", action="store_true", help="one line per host (role and BMC address)"
    )

    pg = sub.add_parser("nodeping", aliases=["ping"], help="ping BMC addresses")
    _add_selection(pg)
    pg.add_argument("--jobs", type=int, default=None, help="parallel workers")
    pg.add_argument(
        "--ping-timeout", type=float, default=1.0, help="seconds to wait per reply"
    )
    pg.add_argument("--long", action="store_true", help="one line per host")

    for name, (role, subject) in SENSOR_COMMANDS.items():
        sensor = sub.add_parser(
            name, help="{} {} readings/control".format(role, subject)
        )
        _add_sensor_opts(sensor)

    for name, (role, subject) in INFO_COMMANDS.items():
        info = sub.add_parser(name, help="{} {}".format(role, subject))
        _add_info_opts(info, subject)

    cs = sub.add_parser(
        "nodeconsole",
        help="compute SOL console (open/close, or --console for an attached session)",
    )
    cs.add_argument(
        "noderange",
        nargs="?",
        help="nodelist expression (defaults to every compute host)",
    )
    cs.add_argument(
        "action",
        nargs="?",
        default="open",
        choices=["open", "close"],
        help="open (attach one node, default) | close (deactivate many)",
    )
    _add_inventory_opt(cs)
    cs.add_argument("--group", action="append", default=[], help="select a group")
    cs.add_argument("--all", action="store_true", help="select every host")
    cs.add_argument("--jobs", type=int, default=None, help="parallel workers")
    cs.add_argument("--timeout", type=float, default=None, help="per-request timeout")
    cs.add_argument("--user", default=None, help="override BMC username")
    cs.add_argument("--password", default=None, help="override BMC password")
    cs.add_argument(
        "--ipmi-env",
        action="store_true",
        help="pass the IPMI password via IPMI_PASSWORD (ipmitool -E)",
    )
    cs.add_argument("--no-cache", action="store_true", help="skip caches")
    cs.add_argument(
        "--console",
        "--interactive",
        dest="console",
        action="store_true",
        help="attached SOL session with logging/reconnect (one node)",
    )
    cs.add_argument("--cipher", type=int, default=17, help="ipmitool -C cipher suite")
    cs.add_argument("--log-file", default=None, help="console log path (JSON Lines)")
    cs.add_argument("--log-dir", default="logs", help="auto-log directory")
    cs.add_argument("--no-log", action="store_true", help="do not write a console log")
    cs.add_argument(
        "--reconnect",
        dest="reconnect",
        action="store_true",
        default=None,
        help="reconnect after the SOL session drops (default for --console)",
    )
    cs.add_argument(
        "--no-reconnect",
        dest="reconnect",
        action="store_false",
        help="do not reconnect after a drop",
    )
    cs.add_argument(
        "--reconnect-attempts", type=int, default=3, help="reconnect tries (default 3)"
    )
    cs.add_argument(
        "--reconnect-delay",
        type=float,
        default=5.0,
        help="seconds between reconnect tries (default 5)",
    )
    cs.add_argument("-x", "--debug", action="store_true", help="verbose logging")

    sc = sub.add_parser(
        "scan", help="probe and cache a machine type's Redfish map (re-run to refresh)"
    )
    _add_selection(sc)
    sc.add_argument("--jobs", type=int, default=None, help="parallel workers")
    sc.add_argument("--timeout", type=float, default=None, help="per-request timeout")
    sc.add_argument("--user", default=None, help="override BMC username")
    sc.add_argument("--password", default=None, help="override BMC password")
    sc.add_argument(
        "--clear", action="store_true", help="delete cached maps instead of writing"
    )
    return parser


def parse_args(argv=None):
    return build_parser().parse_args(argv)


__all__ = [
    "build_parser",
    "parse_args",
    "normalize_command",
    "SENSOR_COMMANDS",
    "INFO_COMMANDS",
    "NODE_PROGRAMS",
    "DEFAULT_INVENTORY",
    "DEFAULT_JOBS",
    "DEFAULT_TIMEOUT",
]
