"""Run operations through the selected nodes' operating systems."""

import argparse
import sys

from . import bmc_user_cli
from .arguments import add_examples
from .help_language import LocalizedArgumentParser

RESOURCES = {"bmc": bmc_user_cli}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = LocalizedArgumentParser(
        prog="nodeos", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-x", "--debug", action="store_true", help="show OS execution steps")
    parser.add_argument("nodes", metavar="HOSTS", help="node name or quoted host range")
    groups = parser.add_subparsers(dest="resource", metavar="RESOURCE")
    children = {}
    for name, handler in RESOURCES.items():
        child = groups.add_parser(name, help=handler.__doc__)
        handler.configure_parser(child)
        children[name] = child
    add_examples(parser,
        "nodeos 'nvsw[01-09]' bmc adduser nvis 'ExamplePass123!'",
        "nodeos nvsw01 bmc deluser nvis",
        "nodeos nvsw01 bmc moduser nvis 'NewExample123!'",
        "nodeos -x nvsw01 bmc listuser",
    )
    if not argv:
        parser.print_help()
        return 0
    args = parser.parse_args(argv)
    if args.resource is None:
        parser.print_help()
        return 0
    handler = RESOURCES[args.resource]
    child = children[args.resource]
    handler.simple_action(args, child)
    return handler.execute(args, child)


if __name__ == "__main__":
    raise SystemExit(main())
