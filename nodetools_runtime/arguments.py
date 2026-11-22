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
    tuple(SENSOR_COMMANDS) + tuple(INFO_COMMANDS) + ("nodelist", "nodeping")
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

    sc = sub.add_parser(
        "scan", help="probe and cache each BMC's Redfish map (re-run to refresh)"
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
