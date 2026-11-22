"""Command-line argument parsing."""

import argparse

from .config import CAPS, DEFAULT_INVENTORY, DEFAULT_JOBS, DEFAULT_TIMEOUT

CAP_COMMANDS = tuple(CAPS) + ("all", "oem")


def _add_common(parser):
    parser.add_argument("selector", nargs="*", help="hostlist expression / group")
    parser.add_argument("-i", "--inventory", default=None, help="inventory file")
    parser.add_argument("--group", action="append", default=[], help="select a group")
    parser.add_argument("--all", action="store_true", help="select every host")
    parser.add_argument(
        "--no-expand", action="store_true", default=None, help="no same-row expansion"
    )
    parser.add_argument(
        "--summary", action="store_true", default=None, help="per-node summary"
    )
    parser.add_argument("--errors-only", action="store_true", help="only non-OK lines")
    parser.add_argument("--jobs", type=int, default=None, help="parallel workers")
    parser.add_argument("--timeout", type=float, default=None, help="per-request timeout")
    parser.add_argument("--action", default=None, help="control action (auto/max/on/off)")
    parser.add_argument("--user", default=None, help="override BMC username")
    parser.add_argument("--password", default=None, help="override BMC password")
    parser.add_argument(
        "--ipmi-env",
        action="store_true",
        help="pass the IPMI password via IPMI_PASSWORD (ipmitool -E)",
    )
    parser.add_argument("--no-cache", action="store_true", help="skip ODM cache")
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

    for name in CAP_COMMANDS:
        _add_common(sub.add_parser(name, help="{} capability".format(name)))

    return parser


def parse_args(argv=None):
    return build_parser().parse_args(argv)


__all__ = [
    "build_parser",
    "parse_args",
    "CAP_COMMANDS",
    "DEFAULT_JOBS",
    "DEFAULT_TIMEOUT",
]
