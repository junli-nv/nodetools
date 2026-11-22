"""Entry point wiring: parse args, load inventory, run capabilities."""

import concurrent.futures
import os
import sys

from . import reach, selection
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


def _select(args, inv, default_all=False):
    selectors = getattr(args, "selector", [])
    groups = getattr(args, "group", [])
    all_ = getattr(args, "all", False)
    if default_all and not (selectors or groups):
        all_ = True
    return selection.select(
        inv, selectors=selectors, groups=groups, all_=all_, expand=False
    )


def _cmd_list(args):
    inv = Inventory.from_file(_inventory_path(args.inventory))
    hosts = _select(args, inv, default_all=True)
    if not hosts:
        print("nodetools: no hosts selected", file=sys.stderr)
        return 1
    for host in hosts:
        if args.long:
            print("{}: {} {}".format(host.name, host.role, host.address or "-"))
        else:
            print("{}: {}".format(host.name, host.role))
    counts = {}
    for host in hosts:
        counts[host.role] = counts.get(host.role, 0) + 1
    summary = " ".join("{}={}".format(k, counts[k]) for k in sorted(counts))
    print("# {} hosts: {}".format(len(hosts), summary), file=sys.stderr)
    return 0


def _cmd_ping(args):
    if not (args.selector or args.group or args.all):
        print("usage: nodetools ping <selector>", file=sys.stderr)
        return 2
    inv = Inventory.from_file(_inventory_path(args.inventory))
    hosts = _select(args, inv)
    if not hosts:
        print("nodetools: no hosts selected", file=sys.stderr)
        return 1
    jobs = max(1, args.jobs or int(inv.default("jobs", DEFAULT_JOBS)))

    def probe(host):
        if not host.address:
            return "{}: No BMC IP found".format(host.name)
        state = "pingable" if reach.ping(host.address, timeout=args.ping_timeout) else "unreachable"
        return "{}: BMC {}".format(host.name, state)

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for line in pool.map(probe, hosts):
            print(line)
    return 0


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
        if args.command == "list":
            return _cmd_list(args)
        if args.command == "ping":
            return _cmd_ping(args)
        if args.command in CAP_COMMANDS:
            return _cmd_capability(args)
        print("nodetools: unknown command {!r}".format(args.command), file=sys.stderr)
        return 2
    except NodetoolsError as exc:
        print("nodetools: {}".format(exc), file=sys.stderr)
        return 1
