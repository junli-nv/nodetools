"""Entry point wiring: parse args, load inventory, run capabilities."""

import concurrent.futures
import os
import sys

from . import hostlist, reach, selection
from .arguments import CAP_COMMANDS, parse_args
from .config import (
    CAPS,
    DEFAULT_INVENTORY,
    DEFAULT_JOBS,
    DEFAULT_NODE_TIMEOUT,
    DEFAULT_SUMMARY,
    DEFAULT_TIMEOUT,
    VERSION,
)
from .errors import NodetoolsError
from .inventory import Inventory, generate_from_hostslist
from .report import render
from .runtime import Runner, Scanner


def _inventory_path(explicit):
    return (
        explicit
        or os.environ.get("NODETOOLS_INVENTORY")
        or DEFAULT_INVENTORY
    )


class Options:
    def __init__(self, args):
        self.jobs = getattr(args, "jobs", None)
        self.timeout = getattr(args, "timeout", None)
        self.node_timeout = None
        self.summary = False
        self.action = getattr(args, "action", None)
        self.user = getattr(args, "user", None)
        self.password = getattr(args, "password", None)
        self.ipmi_env = getattr(args, "ipmi_env", False)
        self.no_cache = getattr(args, "no_cache", False)
        self.clear = getattr(args, "clear", False)
        self.debug = getattr(args, "debug", False)


def _apply_defaults(options, inv, args):
    options.jobs = args.jobs if args.jobs is not None else int(
        inv.default("jobs", DEFAULT_JOBS)
    )
    options.timeout = args.timeout if args.timeout is not None else float(
        inv.default("timeout", DEFAULT_TIMEOUT)
    )
    options.node_timeout = float(
        inv.default("node_timeout", DEFAULT_NODE_TIMEOUT)
    )
    options.summary = (
        args.summary
        if args.summary is not None
        else inv.default_bool("summary", DEFAULT_SUMMARY)
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
        inv, selectors=selectors, groups=groups, all_=all_
    )


def _cmd_list(args):
    inv = Inventory.from_file(_inventory_path(args.inventory))
    hosts = _select(args, inv, default_all=True)
    if not hosts:
        print("nodetools: no hosts selected", file=sys.stderr)
        return 1
    counts = {}
    for host in hosts:
        counts[host.role] = counts.get(host.role, 0) + 1
    if args.long:
        for host in hosts:
            print("{}: {} {}".format(host.name, host.role, host.address or "-"))
    else:
        by_role = {}
        for host in hosts:
            by_role.setdefault(host.role, []).append(host.name)
        for role in sorted(by_role):
            print("{}: {}".format(role, hostlist.compact(by_role[role])))
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
            return host, "no BMC IP"
        state = "pingable" if reach.ping(host.address, timeout=args.ping_timeout) else "unreachable"
        return host, state

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(probe, hosts))

    if args.long:
        for host, state in results:
            print("{}: BMC {}".format(host.name, state))
        return 0
    groups = {}
    for host, state in results:
        groups.setdefault(state, []).append(host.name)
    for state in sorted(groups):
        print("{}: {}".format(state, hostlist.compact(groups[state])))
    summary = " ".join(
        "{}={}".format(state.replace(" ", "_"), len(groups[state]))
        for state in sorted(groups)
    )
    print("# {} hosts: {}".format(len(results), summary), file=sys.stderr)
    return 0


def _cmd_scan(args):
    if not (args.selector or args.group or args.all):
        print("usage: nodetools scan <selector> [--clear]", file=sys.stderr)
        return 2
    inv = Inventory.from_file(_inventory_path(args.inventory))
    options = Options(args)
    options.jobs = (
        args.jobs if args.jobs is not None else int(inv.default("jobs", DEFAULT_JOBS))
    )
    options.timeout = (
        args.timeout
        if args.timeout is not None
        else float(inv.default("timeout", DEFAULT_TIMEOUT))
    )
    hosts = _select(args, inv)
    if not hosts:
        print("nodetools: no hosts selected", file=sys.stderr)
        return 1
    exit_code = 0
    for result in Scanner(options).stream(hosts):
        for line in render([result], summary=False, errors_only=False):
            print(line, flush=True)
        if result.get("error"):
            exit_code = 1
    return exit_code


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
    )
    if not hosts:
        print("nodetools: no hosts selected", file=sys.stderr)
        return 1

    capabilities = CAPS if args.command == "all" else (args.command,)
    exit_code = 0
    for capability in capabilities:
        runner = Runner(capability, options)
        for result in runner.stream(hosts):
            for line in render([result], summary=options.summary,
                               errors_only=args.errors_only):
                print(line, flush=True)
            if result.get("error"):
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
        if args.command == "scan":
            return _cmd_scan(args)
        if args.command in CAP_COMMANDS:
            return _cmd_capability(args)
        print("nodetools: unknown command {!r}".format(args.command), file=sys.stderr)
        return 2
    except NodetoolsError as exc:
        print("nodetools: {}".format(exc), file=sys.stderr)
        return 1
