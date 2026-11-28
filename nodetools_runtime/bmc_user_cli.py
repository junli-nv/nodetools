"""Manage BMC users through OS SSH and local IPMI."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import getpass
import json
import math
import os
from pathlib import Path
import re
import shlex
import time
from types import SimpleNamespace

from . import model, oem, selection
from .errors import NodetoolsError
from .diagnostics import debug
from .inventory import Inventory
from .subjects import bmc_user_agent as agent
from .transport import ssh
from .transport.redfish import Redfish


def verify_path(host, override):
    adapter = oem.for_device(model.Device(host))
    ident = host.get("system_id") or getattr(adapter, "read_system_id", None)
    path = override or ("/redfish/v1/Systems/" + ident if ident else "")
    if not path:
        return None
    if not re.fullmatch(r"/redfish/v1/(Systems|Managers)/[A-Za-z0-9_.-]+", path):
        raise ValueError("configure system_id or supply a fixed --verify-path")
    return path


def verify(host, name, password, path, deadline, allow_discovery=True, tracing=False):
    transport = Redfish(host.address, name, password, timeout=10)
    transport.set_deadline(deadline)

    def read(target):
        status, body = transport.get_raw(target)
        debug(tracing, "redfish:" + getattr(host, "name", host.address), "GET " + target + " HTTP " + str(status))
        if transport.read_error is not None:
            raise ValueError("BMC verification: " + str(transport.read_error))
        if status in (401, 403):
            raise ValueError("BMC verification rejected account: HTTP " + str(status))
        return status, body

    def valid(target, status, body):
        return (status == 200 and isinstance(body, dict) and "error" not in body
                and body.get("Id") == target.rsplit("/", 1)[-1]
                and body.get("@odata.id", target) == target)

    try:
        if path:
            status, body = read(path)
            if valid(path, status, body):
                return "HTTP 200 " + path
            if status not in (404, 410) or not allow_discovery:
                raise ValueError("BMC verification failed: HTTP " + str(status))
        base = "/redfish/v1/Systems"
        status, body = read(base)
        if status != 200 or not isinstance(body, dict) or "error" in body:
            raise ValueError("BMC system discovery failed: HTTP " + str(status))
        members = body.get("Members")
        if not isinstance(members, list) or not members:
            raise ValueError("BMC reports no system resource for verification")
        for member in members[:8]:
            target = member.get("@odata.id") if isinstance(member, dict) else None
            if (not isinstance(target, str)
                    or not re.fullmatch(r"/redfish/v1/Systems/[A-Za-z0-9_.-]+", target)
                    or target.rsplit("/", 1)[-1] in (".", "..")):
                raise ValueError("BMC reports an invalid system resource link")
            status, item = read(target)
            if valid(target, status, item):
                return "HTTP 200 " + target
            if status not in (404, 410):
                raise ValueError("BMC verification failed: HTTP " + str(status))
        raise ValueError("BMC reported no readable system resource")
    finally:
        transport.close()


def one(host, args, password, os_password):
    deadline = time.monotonic() + args.node_timeout
    options = SimpleNamespace(
        user=args.os_user or host.get(host.role + "_os_user") or host.get("os_user") or getpass.getuser(),
        password=os_password, timeout=args.timeout, debug=False)
    payload = dict(role=host.role, name=args.name, id=args.id, channel=args.channel,
                   operation=getattr(args, "operation", "upsert"),
                   apply=args.apply, password=password, sol=args.sol and host.role == "compute",
                   debug=getattr(args, "debug", False), seconds=max(0.1, deadline-time.monotonic()))
    path = verify_path(host, args.verify_path)
    agent.validate(payload)
    source = Path(agent.__file__).read_text()
    prefix = "" if options.user == "root" else "sudo -n "
    command = prefix + "python3 -c " + shlex.quote(source)
    tracing = getattr(args, "debug", False)
    address = host.get(host.role + "_os_host") or host.get("os_host") or host.name
    debug(tracing, "os:" + host.name, "SSH target=" + address + " user=" + options.user)
    response = ssh.run(host, options, command, deadline, role=host.role,
                       input_text=json.dumps(payload), query_label="local-IPMI BMC user provisioning")
    debug(tracing, "os:" + host.name, "SSH exit=" + str(response.returncode))
    if tracing and response.returncode:
        detail = getattr(response, "stderr", "")
        values = [password, options.password] + [v for k, v in host.vars.items() if "pass" in k.lower()]
        for value in values:
            if value:
                detail = detail.replace(str(value), "<redacted>")
        debug(True, "os:" + host.name, detail.strip())
    try:
        result = json.loads(response.stdout)
    except ValueError:
        raise ValueError("OS SSH returned no provisioning result (exit {}); inspect before retry".format(response.returncode)) from None
    for entry in result.pop("trace", []):
        debug(tracing, "ipmi:" + host.name, entry)
    if "slot" in result:
        debug(tracing, "ipmi:" + host.name, "selected user slot=" + str(result["slot"]))
    if response.returncode or not result.get("ok"):
        raise ValueError("{}; completed={}".format(result.get("error", "OS SSH failed"),
                                                  result.get("completed", [])))
    result["host"] = host.name
    if "sol" in result:
        result["sol"] = result["sol"] if host.role == "compute" else "unsupported; skipped"
    try:
        result["verification"] = (verify(host, args.name, password, path, deadline,
                                          not bool(args.verify_path or host.get("system_id")), tracing)
                                  if (args.apply or args.verify_only) and payload["operation"] not in ("list", "delete") else "not run")
    except ValueError as exc:
        raise ValueError("{}; account steps completed={}".format(exc, result["completed"])) from None
    return result


def execute(args, parser):
    try:
        operation = getattr(args, "operation", "upsert")
        if not args.name and operation != "list":
            raise ValueError("user action requires --name")
        args.jobs = args.jobs if args.jobs is not None else 4
        if args.jobs < 1 or not all(math.isfinite(v) and v > 0 for v in (args.timeout, args.node_timeout)):
            raise ValueError("jobs and timeouts must be positive and finite")
        inv = Inventory.from_file(args.inventory or "inventory.ini")
        hosts = selection.select(inv, selectors=[args.nodes])
        if not hosts or any(h.name not in inv.hosts or h.role not in ("compute", "nvswitch") or not h.address for h in hosts):
            raise ValueError("select configured compute/NVSwitch hosts with BMC addresses")
        targets = [h.get(h.role+"_os_host") or h.get("os_host") or h.name for h in hosts]
        if len(set(targets)) != len(targets):
            raise ValueError("selection contains duplicate OS SSH targets")

        def secret(variable):
            if not variable:
                return None
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable):
                raise ValueError("password-env takes a variable NAME, without $")
            if variable not in os.environ:
                raise ValueError("password environment variable is unset; supply its NAME")
            return os.environ[variable]

        password = getattr(args, "position_password", None)
        if password is None:
            password = secret(args.password_env)
        os_password = secret(args.os_password_env)
        if (args.apply or args.verify_only) and operation not in ("list", "delete") and password is None:
            if not os.isatty(0):
                raise ValueError("supply --password-env for noninteractive use")
            password = getpass.getpass("New BMC account password: ")
        for host in hosts:
            agent.validate(dict(operation=operation, role=host.role, name=args.name, id=args.id, channel=args.channel,
                                apply=args.apply or args.verify_only, password=password,
                                sol=args.sol and host.role == "compute"))
            verify_path(host, args.verify_path)
    except (ValueError, NodetoolsError) as exc:
        parser.error(str(exc))
    failed = False
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        pending = {pool.submit(one, h, args, password, os_password): h for h in hosts}
        for future in as_completed(pending):
            host = pending[future]
            try:
                result = future.result()
            except Exception as exc:
                failed = True
                result = dict(host=host.name, ok=False, error=str(exc))
            if getattr(args, "simple_output", False):
                if not result.get("ok"):
                    line = host.name + ": ERROR " + result.get("error", "failed")
                elif "users" in result:
                    line = "\n".join(host.name + ": " + row["name"] + " " + row["privilege"]
                                     for row in result["users"]) or host.name + ": no matching users"
                else:
                    line = host.name + ": OK " + args.action + " " + args.name
            else:
                line = json.dumps(result, ensure_ascii=False)
            for value in (password, os_password):
                if value:
                    line = line.replace(value, "<redacted>")
            print(line, flush=True)
    return int(failed)


SHORT_ACTIONS = {
    "adduser": "upsert", "moduser": "modify", "deluser": "delete",
    "listuser": "list", "checkuser": "verify",
}


def simple_action(args, parser):
    operation = SHORT_ACTIONS[args.action]
    if args.apply or args.verify_only:
        parser.error("the short command needs no --apply or --verify-only flag")
    if args.name or args.password_env:
        parser.error("use USERNAME PASSWORD after " + args.action)
    if operation != "list" and not args.account:
        parser.error(args.action + " requires USERNAME")
    if operation in ("upsert", "modify", "verify") and args.account_password is None:
        parser.error(args.action + " requires USERNAME PASSWORD")
    if operation in ("list", "delete") and args.account_password is not None:
        parser.error(args.action + " takes a username only")
    args.name = args.account
    args.position_password = args.account_password
    args.operation = operation
    args.simple_output = True
    args.apply = operation in ("upsert", "modify", "delete")
    args.verify_only = operation == "verify"
    args.sol = operation == "upsert"


def simple_help(parser):
    import argparse
    parser.formatter_class = argparse.RawDescriptionHelpFormatter

    class FullHelp(argparse.Action):
        def __call__(self, target, namespace, values, option_string=None):
            old = target.epilog
            try:
                for action, text in target.advanced_help.items():
                    action.help = text
                target.epilog = target.full_epilog
                target.print_help()
            finally:
                target.epilog = old
                for action in target.advanced_help:
                    action.help = argparse.SUPPRESS
            target.exit()

    parser.add_argument("account", nargs="?", metavar="USERNAME", help="BMC username")
    parser.add_argument("account_password", nargs="?", metavar="PASSWORD", help="BMC password")
    parser.add_argument("--help-full", action=FullHelp, nargs=0, help="advanced options")
    basic = """Examples
  nodeos 'nvsw[01-09]' bmc adduser nvis 'ExamplePass123!'
  nodeos nvsw01 bmc deluser nvis
  nodeos nvsw01 bmc moduser nvis 'NewExample123!'
  nodeos 'nvsw[01-09]' bmc listuser
  nodeos nvsw01 bmc listuser nvis
  nodeos nvsw01 bmc checkuser nvis 'ExamplePass123!'
  nodeos nvsw01 bmc listuser

User slots, channels and verification paths are automatic.
Compute SOL is automatic; NVSwitch skips SOL."""
    advanced_examples = parser.epilog.removeprefix("Examples\n")
    parser.full_epilog = basic + "\n\n" + advanced_examples
    parser.epilog = basic
    visible = {"help", "help_zh", "help_full", "debug", "scope", "nodes", "action", "account", "account_password"}
    parser.advanced_help = {}
    for action in parser._actions:
        if action.dest not in visible and "--help-zh" not in action.option_strings:
            parser.advanced_help[action] = action.help
            action.help = argparse.SUPPRESS


def configure_parser(parser):
    import argparse
    from .arguments import add_examples
    parser.add_argument("action", choices=tuple(SHORT_ACTIONS), metavar="COMMAND",
                        help="adduser, deluser, moduser, listuser, checkuser")
    parser.add_argument("-x", "--debug", action="store_true", default=argparse.SUPPRESS,
                        help="show SSH and IPMI steps")
    parser.add_argument("-i", "--inventory")
    parser.add_argument("--jobs", type=int)
    parser.add_argument("--id", type=int)
    parser.add_argument("--channel", type=int)
    parser.add_argument("--os-user")
    parser.add_argument("--os-password-env")
    parser.add_argument("--verify-path")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--node-timeout", type=float, default=60)
    parser.set_defaults(name=None, password_env=None, apply=False, verify_only=False, sol=False)
    add_examples(parser,
        "nodeos 'compute[01-04],nvsw[01-04]' bmc adduser nvis 'ExamplePass123!' --jobs 4",
        "nodeos nvsw01 bmc listuser --os-user admin",
    )
    simple_help(parser)
