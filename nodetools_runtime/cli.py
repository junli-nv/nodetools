"""Entry point wiring: parse args, load inventory, run capabilities."""

import concurrent.futures
import json
import math
import os
import signal
import sys
import time
from datetime import datetime, timezone

from . import bak, hostlist, model, oem, reach, selection
from .arguments import (
    INFO_COMMANDS,
    NODE_PROGRAMS,
    SENSOR_COMMANDS,
    normalize_command,
    parse_args,
)
from .caps.monitor import estimate_settings
from .config import (
    DEFAULT_INVENTORY,
    DEFAULT_JOBS,
    DEFAULT_NODE_TIMEOUT,
    DEFAULT_SUMMARY,
    DEFAULT_TIMEOUT,
    ROLE_LABELS,
    ROLE_ORDER,
    VERSION,
)
from . import console
from .diagnostics import debug
from .errors import NodetoolsError
from .inventory import Inventory, generate_from_hostslist
from .monitor_report import MonitorCSV
from .report import format_inlet_temperature, render
from .subjects.vm import image_source
from .runtime import InfoRunner, Runner, Scanner

_READ_WORDS = ("", "status", "check", "read")

# These commands have useful behavior with no arguments.
_BARE_COMMANDS = ("nodelist", "bak")


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
        self.inlet_target = getattr(args, "target", None)
        self.inlet_tolerance = getattr(args, "tolerance", None)
        self.details = getattr(args, "details", False)
        self.gpu = getattr(args, "gpu", False)
        self.tasks = getattr(args, "tasks", False)
        self.bios_updates = getattr(args, "bios_updates", None)
        self.image = getattr(args, "image", None)
        self.vm_user = getattr(args, "vm_user", None)
        self.vm_password = getattr(args, "vm_password", None)
        self.user = getattr(args, "user", None)
        self.password = getattr(args, "password", None)
        self.ipmi_env = getattr(args, "ipmi_env", False)
        self.no_cache = getattr(args, "no_cache", False)
        self.clear = getattr(args, "clear", False)
        self.debug = getattr(args, "debug", False)
        self.console = getattr(args, "console", False)
        self.cipher = getattr(args, "cipher", None)
        self.reconnect = getattr(args, "reconnect", None)
        self.reconnect_attempts = getattr(args, "reconnect_attempts", None)
        self.reconnect_delay = getattr(args, "reconnect_delay", None)
        self.log_file = getattr(args, "log_file", None)
        self.log_dir = getattr(args, "log_dir", "logs")
        self.no_log = getattr(args, "no_log", False)


def _apply_defaults(options, inv, args):
    options.jobs = args.jobs if args.jobs is not None else int(
        inv.default("jobs", DEFAULT_JOBS)
    )
    options.timeout = args.timeout if args.timeout is not None else float(
        inv.default("timeout", DEFAULT_TIMEOUT)
    )
    node_timeout = getattr(args, "node_timeout", None)
    options.node_timeout = node_timeout if node_timeout is not None else float(
        inv.default("node_timeout", DEFAULT_NODE_TIMEOUT)
    )
    for label, value in (("timeout", options.timeout), ("node_timeout", options.node_timeout)):
        if not math.isfinite(value) or value <= 0:
            raise NodetoolsError("{} must be a finite positive number".format(label))
    summary = getattr(args, "summary", None)
    options.summary = (
        summary if summary is not None else inv.default_bool("summary", DEFAULT_SUMMARY)
    )


def _cmd_inventory(args):
    if args.inventory_command == "init":
        debug(getattr(args, "debug", False), "inventory", "generate from {!r} into {!r}".format(args.source, args.output))
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
            print("  {} role={} platform={} oem={} generation={} host={}".format(
                name, host.role, host.get("platform") or "auto", host.get("oem") or "auto",
                host.get("generation") or "auto", host.address or "-",
            ))
        return 0
    print("usage: nodetools inventory {init|show}", file=sys.stderr)
    return 2


def _select(args, inv, default_all=False):
    selectors = getattr(args, "selector", [])
    groups = getattr(args, "group", [])
    all_ = getattr(args, "all", False)
    if default_all and not (selectors or groups):
        all_ = True
    hosts = selection.select(inv, selectors=selectors, groups=groups, all_=all_)
    debug(getattr(args, "debug", False), "selection", "selected {} host(s)".format(len(hosts)))
    return hosts


def _role_hosts(args, inv, role):
    """Resolve hosts for a role-prefixed command, filtered to that role.

    With no selectors, include every inventory host with this role, including
    hosts in custom groups. Explicit selections are narrowed to the role.
    """
    nrange = getattr(args, "noderange", None)
    selectors = [nrange] if nrange else []
    groups = list(getattr(args, "group", []))
    all_ = getattr(args, "all", False)
    if not (selectors or groups or all_):
        kept = [host for host in inv.hosts.values() if host.role == role]
        debug(getattr(args, "debug", False), "selection", "{}: {} host(s)".format(role, len(kept)))
        return kept
    hosts = selection.select(inv, selectors=selectors, groups=groups, all_=all_)
    kept = [host for host in hosts if host.role == role]
    if len(kept) != len(hosts):
        print(
            "nodetools: skipped {} host(s) not of role {}".format(
                len(hosts) - len(kept), role
            ),
            file=sys.stderr,
        )
    debug(getattr(args, "debug", False), "selection", "{}: {} host(s)".format(role, len(kept)))
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
        reachable = reach.ping(host.address, timeout=args.ping_timeout)
        debug(getattr(args, "debug", False), "icmp:" + host.name,
              "{} timeout={}s reachable={}".format(host.address, args.ping_timeout, reachable))
        return host, "BMC pingable" if reachable else "BMC unreachable"

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


def _cmd_bak(args):
    def display(stream):
        if args.key_value and args.bak_mode is None:
            bak.compare_fields(stream)
        elif args.bak_mode is None or args.key_value:
            lines = list(stream)
            bak.collect_text(lines, args.bak_mode or "similar")
            bak.compare_fields(lines, table=True, autodetect=not args.key_value)
        else:
            bak.collect_text(stream, args.bak_mode)

    try:
        if args.file == "-":
            display(sys.stdin)
        else:
            with open(args.file, encoding="utf-8") as stream:
                display(stream)
    except (OSError, UnicodeError) as exc:
        raise NodetoolsError(f"bak: {exc}") from exc
    return 0


def _cmd_scan(args):
    if not (args.selector or args.group or args.all):
        print("usage: nodetools scan <host> [<host> ...] [--clear]", file=sys.stderr)
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
        if "cache_path" in result:
            path = result["cache_path"]
            message = "cache saved to " + path if path else "cache not written (device identity unavailable)"
            print("{}: {}".format(result["name"], message), flush=True)
        if result.get("error"):
            exit_code = 1
    return exit_code


def _sensor_action_word(args):
    action = getattr(args, "action", None) or getattr(args, "action_opt", None) or ""
    return action.strip().lower()


def _interrupt_sensor_query(signum, frame):
    # A read-only CLI query can exit without waiting for executor teardown.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        os.write(2, b"nodetools: interrupted\n")
    finally:
        os._exit(130)


def _cmd_sensor(args):
    command = normalize_command(args.command)
    role, capability = SENSOR_COMMANDS[command]
    action = _sensor_action_word(args)
    if action not in _READ_WORDS:
        if capability != "fan" or role == "nvswitch":
            raise NodetoolsError("{} is read-only; use status or check".format(command))
        if action not in ("auto", "max", "low", "opt"):
            raise NodetoolsError("invalid fan action {!r}".format(action))
    inv = Inventory.from_file(_inventory_path(args.inventory))
    options = Options(args)
    options.action = None if action in _READ_WORDS else action
    fan_speeds = (
        command == "nodefan" and action in ("", "status", "read") and not args.errors_only
    )
    _apply_defaults(options, inv, args)

    simple_inlet = (
        capability == "inlet" and action in ("", "status", "read")
        and not (options.summary or args.errors_only)
    )

    hosts = _role_hosts(args, inv, role)
    if not hosts:
        print(
            "nodetools: no {} hosts selected".format(role), file=sys.stderr
        )
        return 1

    exit_code = 0
    runner = Runner(capability, options)
    previous_handler = None
    if options.action is None:
        previous_handler = signal.signal(signal.SIGINT, _interrupt_sensor_query)
    try:
        for result in runner.stream(hosts):
            if simple_inlet:
                print(format_inlet_temperature(result), flush=True)
                if result.get("error"):
                    print("{}: ERROR {}".format(result["name"], result["error"]), file=sys.stderr)
            else:
                for line in render(
                    [result], summary=options.summary, errors_only=args.errors_only,
                    details=getattr(args, "details", False), fan_speeds=fan_speeds,
                ):
                    print(line, flush=True)
            if result.get("error"):
                exit_code = 1
    finally:
        if previous_handler is not None:
            signal.signal(signal.SIGINT, previous_handler)
    return exit_code


def _cmd_monitor(args):
    if args.count < 1 or not math.isfinite(args.interval) or args.interval <= 0:
        raise NodetoolsError("--count and --interval must be positive and finite")
    inv = Inventory.from_file(_inventory_path(args.inventory))
    options = Options(args)
    _apply_defaults(options, inv, args)
    hosts = _role_hosts(args, inv, "compute")
    if not hosts:
        raise NodetoolsError("no compute hosts selected")

    options.psu_voltage = args.psu_voltage
    options.psu_power_factor = args.psu_power_factor
    for host in hosts:
        estimate_settings(host.vars, options)

    runner = Runner("monitor", options)
    table = None
    if args.format != "json":
        slot_layouts = {
            tuple(getattr(oem.for_device(model.Device(host)), "monitor_hsc_sensors", ()))
            for host in hosts
        }
        slots = next(iter(slot_layouts)) if len(slot_layouts) == 1 else ()
        table = MonitorCSV(sys.stdout, multiple_nodes=len(hosts) > 1, psu_slots=slots)
    exit_code = 0
    previous_handler = signal.signal(signal.SIGINT, _interrupt_sensor_query)
    try:
        for index in range(args.count):
            timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for result in runner.stream(hosts):
                result.update(timestamp=timestamp, sample=index + 1)
                if table is None:
                    print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
                else:
                    table.write(result)
                if result["error"]:
                    exit_code = 1
                    if table is not None:
                        print("{}: {}".format(result["name"], result["error"]), file=sys.stderr)
            if index + 1 < args.count:
                time.sleep(args.interval)
    finally:
        signal.signal(signal.SIGINT, previous_handler)
    return exit_code


def _cmd_info(args):
    command = normalize_command(args.command)
    role, subject = INFO_COMMANDS[command]
    options = Options(args)
    action = (
        getattr(args, "action", None) or getattr(args, "action_opt", None) or ""
    ).strip().lower()
    if subject == "eventlog" and role != "compute" and action == "hmc":
        raise NodetoolsError("the hmc action is only available for compute trays (nodeeventlog)")
    if command == "pseventlog" and action == "bmc":
        raise NodetoolsError("pseventlog uses status to read the event log")
    options.action = None if action in _READ_WORDS else action
    explicit_targets = args.noderange or args.group or args.all
    if options.bios_updates and (action or not explicit_targets):
        raise NodetoolsError("nodebios --set requires explicit targets and cannot be combined with an action")
    if subject == "netboot" and action == "pxe" and not explicit_targets:
        raise NodetoolsError("nodenetboot pxe requires explicit targets")
    if subject == "vm" and action in ("mount", "insert", "stop"):
        image_source(options)
    inv = Inventory.from_file(_inventory_path(args.inventory))
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


def _cmd_console(args):
    inv = Inventory.from_file(_inventory_path(args.inventory))
    options = Options(args)
    _apply_defaults(options, inv, args)
    hosts = _role_hosts(args, inv, "compute")
    if not hosts:
        print("nodetools: no compute hosts selected", file=sys.stderr)
        return 1
    if options.console:
        if len(hosts) != 1:
            print(
                "nodetools: --console requires exactly one node", file=sys.stderr
            )
            return 2
        if options.reconnect is None:
            options.reconnect = True
        return console.attach(hosts[0], options)
    if args.action == "close":
        return console.close_all(hosts, options)
    if len(hosts) > 1:
        print(
            "nodetools: open supports one node; using {}".format(hosts[0].name),
            file=sys.stderr,
        )
    return console.open_one(hosts[0], options)


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
    if len(argv) == 1 and command not in _BARE_COMMANDS:
        # Bare call: show this command's help and exit 0 (help, not an error).
        try:
            parse_args([args.command, "--help"])
        except SystemExit:
            pass
        return 0
    if getattr(args, "debug", False):
        debug(True, "nodetools", "command={}".format(command))
        if command != "bak" and (command != "inventory" or args.inventory_command == "show"):
            debug(True, "inventory", "load {!r}".format(_inventory_path(getattr(args, "inventory", None))))
    try:
        if command == "inventory":
            return _cmd_inventory(args)
        if command == "nodelist":
            return _cmd_list(args)
        if command == "nodeping":
            return _cmd_ping(args)
        if command == "bak":
            return _cmd_bak(args)
        if command == "scan":
            return _cmd_scan(args)
        if command == "nodeconsole":
            return _cmd_console(args)
        if command == "nodemon":
            return _cmd_monitor(args)
        if command in INFO_COMMANDS:
            return _cmd_info(args)
        if command in SENSOR_COMMANDS:
            return _cmd_sensor(args)
        print("nodetools: unknown command {!r}".format(args.command), file=sys.stderr)
        return 2
    except NodetoolsError as exc:
        print("nodetools: {}".format(exc), file=sys.stderr)
        return 1
