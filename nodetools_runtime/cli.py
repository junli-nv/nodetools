"""Entry point wiring: parse args, load inventory, run capabilities."""

import os
import sys

from . import selection
from .arguments import CAP_COMMANDS, parse_args
from .config import CAPS, DEFAULT_INVENTORY, DEFAULT_JOBS, DEFAULT_TIMEOUT, VERSION
from .errors import NodetoolsError
from .inventory import Inventory, generate_from_hostslist
from .report import render
from .runtime import Runner


def _inventory_path(explicit):
    return (
        explicit
        or os.environ.get("NODETOOLS_INVENTORY")
        or DEFAULT_INVENTORY
    )


class Options:
    def __init__(self, args):
        self.jobs = args.jobs
        self.timeout = args.timeout
        self.expand = True
        self.summary = False
        self.action = args.action
        self.user = args.user
        self.password = args.password
        self.ipmi_env = args.ipmi_env
        self.no_cache = args.no_cache
        self.debug = args.debug


def _apply_defaults(options, inv, args):
    options.jobs = args.jobs if args.jobs is not None else int(
        inv.default("jobs", DEFAULT_JOBS)
    )
    options.timeout = args.timeout if args.timeout is not None else float(
        inv.default("timeout", DEFAULT_TIMEOUT)
    )
    expand_default = inv.default_bool("expand", True)
    options.expand = (
        not args.no_expand if args.no_expand is not None else expand_default
    )
    options.summary = (
        args.summary if args.summary is not None else inv.default_bool("summary", False)
    )


def _cmd_inventory(args):
    if args.inventory_command == "init":
        text = generate_from_hostslist(args.source)
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text)
        print("wrote {} from {}".format(args.output, args.source))
        return 0
    if args.inventory_command == "show":
        path = _inventory_path(args.inventory)
        inv = Inventory.from_file(path)
        print("inventory: {} ({} hosts)".format(path, len(inv.order)))
        for group, names in inv.groups.items():
            print("  [{}] {} hosts".format(group, len(names)))
        for name in inv.order:
            host = inv.get(name)
            print(
                "  {} role={} host={}".format(name, host.role, host.address)
            )
        return 0
    print("usage: nodetools inventory {init|show}", file=sys.stderr)
    return 2


def _cmd_capability(args):
    path = _inventory_path(args.inventory)
    inv = Inventory.from_file(path)
    options = Options(args)
    _apply_defaults(options, inv, args)

    hosts = selection.select(
        inv,
        selectors=args.selector,
        groups=args.group,
        all_=args.all,
        expand=options.expand,
    )
    if not hosts:
        print("nodetools: no hosts selected", file=sys.stderr)
        return 1

    capabilities = CAPS if args.command == "all" else (args.command,)
    exit_code = 0
    for capability in capabilities:
        runner = Runner(capability, options)
        results = runner.run(hosts)
        for line in render(results, summary=options.summary,
                           errors_only=args.errors_only):
            print(line)
        if any(result.get("error") for result in results):
            exit_code = 1
    return exit_code


def main(argv=None):
    args = parse_args(argv)
    if getattr(args, "version", False) and not args.command:
        print("nodetools {}".format(VERSION))
        return 0
    if not args.command:
        parse_args(["--help"])
        return 2
    try:
        if args.command == "inventory":
            return _cmd_inventory(args)
        if args.command in CAP_COMMANDS:
            return _cmd_capability(args)
        print("nodetools: unknown command {!r}".format(args.command), file=sys.stderr)
        return 2
    except NodetoolsError as exc:
        print("nodetools: {}".format(exc), file=sys.stderr)
        return 1
