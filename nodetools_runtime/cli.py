"""Entry point wiring: parse args, load inventory, run capabilities."""

import concurrent.futures
import os
import sys

from . import hostlist, reach, selection
from .arguments import (
    INFO_COMMANDS,
    NODE_PROGRAMS,
    SENSOR_COMMANDS,
    normalize_command,
    parse_args,
)
from .config import (
    DEFAULT_INVENTORY,
    DEFAULT_JOBS,
    DEFAULT_NODE_TIMEOUT,
    DEFAULT_SUMMARY,
    DEFAULT_TIMEOUT,
    ROLE_GROUPS,
    ROLE_LABELS,
    ROLE_ORDER,
    VERSION,
)
from .errors import NodetoolsError
from .inventory import Inventory, generate_from_hostslist
from .report import render
from .runtime import InfoRunner, Runner, Scanner

_READ_WORDS = ("", "status", "check", "read")


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
        self.action = None
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
            print("  {} role={} host={}".format(name, host.role, host.address))
        return 0
    print("usage: nodetools inventory {init|show}", file=sys.stderr)
    return 2


def _select(args, inv, default_all=False):
    selectors = getattr(args, "selector", [])
    groups = getattr(args, "group", [])
    all_ = getattr(args, "all", False)
    if default_all and not (selectors or groups):
        all_ = True
    return selection.select(inv, selectors=selectors, groups=groups, all_=all_)


def _role_hosts(args, inv, role):
    """Resolve hosts for a role-prefixed command, filtered to that role.

    With no noderange the whole role group is used; an explicit noderange is
    resolved normally and then narrowed to the command's role.
    """
    nrange = getattr(args, "noderange", None)
    selectors = [nrange] if nrange else []
    groups = list(getattr(args, "group", []))
    all_ = getattr(args, "all", False)
    if not (selectors or groups or all_):
        groups = [ROLE_GROUPS[role]]
    hosts = selection.select(inv, selectors=selectors, groups=groups, all_=all_)
    kept = [host for host in hosts if host.role == role]
    if len(kept) != len(hosts):
        print(
            "nodetools: skipped {} host(s) not of role {}".format(
                len(hosts) - len(kept), role
            ),
            file=sys.stderr,
        )
    return kept


def _cmd_list(args):
    inv = Inventory.from_file(_inventory_path(args.inventory))
    hosts = _select(args, inv, default_all=True)
    if not hosts:
        print("nodetools: no hosts selected", file=sys.stderr)
        return 1
    if args.long:
        for host in hosts:
            print("{}: {} {}".format(host.name, host.role, host.address or "-"))
        return 0
    by_role = {}
    for host in hosts:
        by_role.setdefault(host.role, []).append(host.name)
    for role in ROLE_ORDER:
        if role in by_role:
            print(
                "%15s: %s"
                % (ROLE_LABELS.get(role, role), hostlist.compact(by_role.pop(role)))
            )
    for role in sorted(by_role):
        print(
            "%15s: %s"
            % (ROLE_LABELS.get(role, role), hostlist.compact(by_role[role]))
        )
    return 0


def _cmd_ping(args):
    inv = Inventory.from_file(_inventory_path(args.inventory))
    hosts = _select(args, inv, default_all=True)
    if not hosts:
        print("nodetools: no hosts selected", file=sys.stderr)
        return 1
    jobs = max(1, args.jobs or int(inv.default("jobs", DEFAULT_JOBS)))

    def probe(host):
        if not host.address:
            return host, "No BMC IP found"
        if reach.ping(host.address, timeout=args.ping_timeout):
            return host, "BMC pingable"
        return host, "BMC unreachable"

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(probe, hosts))

    if args.long:
        for host, message in results:
            print("{}: {}".format(host.name, message))
        return 0
    groups = {}
    for host, message in results:
        groups.setdefault(message, []).append(host.name)
    for message in sorted(groups):
        print("----------------")
        print(hostlist.compact(groups[message]))
        print("----------------")
        print(message)
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


def _sensor_action(args):
    action = (getattr(args, "action", None) or getattr(args, "action_opt", None) or "")
    action = action.strip().lower()
    return None if action in _READ_WORDS else action


def _cmd_sensor(args):
    command = normalize_command(args.command)
    role, capability = SENSOR_COMMANDS[command]
    inv = Inventory.from_file(_inventory_path(args.inventory))
    options = Options(args)
    options.action = _sensor_action(args)
    _apply_defaults(options, inv, args)

    hosts = _role_hosts(args, inv, role)
    if not hosts:
        print(
            "nodetools: no {} hosts selected".format(role), file=sys.stderr
        )
        return 1

    exit_code = 0
    runner = Runner(capability, options)
    for result in runner.stream(hosts):
        for line in render(
            [result], summary=options.summary, errors_only=args.errors_only
        ):
            print(line, flush=True)
        if result.get("error"):
            exit_code = 1
    return exit_code


def _cmd_info(args):
    command = normalize_command(args.command)
    role, subject = INFO_COMMANDS[command]
    inv = Inventory.from_file(_inventory_path(args.inventory))
    options = Options(args)
    action = (
        getattr(args, "action", None) or getattr(args, "action_opt", None) or ""
    ).strip().lower()
    options.action = None if action in _READ_WORDS else action
    _apply_defaults(options, inv, args)

    hosts = _role_hosts(args, inv, role)
    if not hosts:
        print("nodetools: no {} hosts selected".format(role), file=sys.stderr)
        return 1

    exit_code = 0
    runner = InfoRunner(subject, options)
    for result in runner.stream(hosts):
        for line in render([result], summary=False, errors_only=args.errors_only):
            print(line, flush=True)
        if result.get("error"):
            exit_code = 1
    return exit_code


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
        program = os.path.basename(sys.argv[0])
        if program in NODE_PROGRAMS:
            argv = [program] + list(argv)
    args = parse_args(argv)
    if getattr(args, "version", False) and not args.command:
        print("nodetools {}".format(VERSION))
        return 0
    if not args.command:
        parse_args(["--help"])
        return 2
    command = normalize_command(args.command)
    try:
        if command == "inventory":
            return _cmd_inventory(args)
        if command == "nodelist":
            return _cmd_list(args)
        if command == "nodeping":
            return _cmd_ping(args)
        if command == "scan":
            return _cmd_scan(args)
        if command in INFO_COMMANDS:
            return _cmd_info(args)
        if command in SENSOR_COMMANDS:
            return _cmd_sensor(args)
        print("nodetools: unknown command {!r}".format(args.command), file=sys.stderr)
        return 2
    except NodetoolsError as exc:
        print("nodetools: {}".format(exc), file=sys.stderr)
        return 1
