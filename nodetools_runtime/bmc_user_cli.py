"""Provision BMC users through compute/NVSwitch OS local IPMI."""

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
from .inventory import Inventory
from .subjects import bmc_user_agent as agent
from .transport import ssh
from .transport.redfish import Redfish


def add_arguments(parser):
    parser.add_argument("--name", help="BMC account to create or update")
    parser.add_argument("--id", type=int, help="BMC user slot (default: reuse named user or an empty slot)")
    parser.add_argument("--channel", type=int, default=1, help="IPMI channel (default 1)")
    parser.add_argument("--password-env", help="environment variable with the new BMC password")
    parser.add_argument("--os-user", help="override OS SSH username")
    parser.add_argument("--os-password-env", help="environment variable with OS SSH password")
    parser.add_argument("--sol", action="store_true", help="enable SOL for compute; skip NVSwitch")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="apply user configuration and verify Redfish")
    mode.add_argument("--verify-only", action="store_true", help="check user slot and Redfish without writes")
    parser.add_argument("--verify-path", help="fixed Redfish Systems/Managers resource")
    parser.add_argument("--timeout", type=float, default=30, help="OS SSH operation timeout")
    parser.add_argument("--node-timeout", type=float, default=60, help="whole-node budget")


def verify_path(host, override):
    adapter = oem.for_device(model.Device(host))
    ident = host.get("system_id") or getattr(adapter, "read_system_id", None)
    path = override or ("/redfish/v1/Systems/" + ident if ident else "")
    if not re.fullmatch(r"/redfish/v1/(Systems|Managers)/[A-Za-z0-9_.-]+", path):
        raise ValueError("configure system_id or supply a fixed --verify-path")
    return path


def verify(host, name, password, path, deadline):
    transport = Redfish(host.address, name, password, timeout=10)
    transport.set_deadline(deadline)
    try:
        status, body = transport.get_raw(path)
        if transport.read_error is not None:
            raise ValueError("new account Redfish verification: " + str(transport.read_error))
        if (status != 200 or not isinstance(body, dict) or "error" in body
                or body.get("Id") != path.rsplit("/", 1)[-1]
                or body.get("@odata.id", path) != path):
            raise ValueError("new account Redfish verification failed: HTTP " + str(status))
        return "HTTP 200 " + path
    finally:
        transport.close()


def one(host, args, password, os_password):
    deadline = time.monotonic() + args.node_timeout
    options = SimpleNamespace(
        user=args.os_user or host.get(host.role + "_os_user") or host.get("os_user") or getpass.getuser(),
        password=os_password, timeout=args.timeout, debug=False)
    payload = dict(role=host.role, name=args.name, id=args.id, channel=args.channel,
                   apply=args.apply, password=password, sol=args.sol and host.role == "compute",
                   seconds=max(0.1, deadline-time.monotonic()))
    path = verify_path(host, args.verify_path)
    agent.validate(payload)
    source = Path(agent.__file__).read_text()
    prefix = "" if options.user == "root" else "sudo -n "
    command = prefix + "python3 -c " + shlex.quote(source)
    response = ssh.run(host, options, command, deadline, role=host.role,
                       input_text=json.dumps(payload), query_label="local-IPMI BMC user provisioning")
    try:
        result = json.loads(response.stdout)
    except ValueError:
        raise ValueError("OS SSH returned no provisioning result (exit {}); inspect before retry".format(response.returncode)) from None
    if response.returncode or not result.get("ok"):
        raise ValueError("{}; completed={}".format(result.get("error", "OS SSH failed"),
                                                  result.get("completed", [])))
    result["host"] = host.name
    result["sol"] = result["sol"] if host.role == "compute" else "unsupported; skipped"
    try:
        result["verification"] = (verify(host, args.name, password, path, deadline)
                                  if args.apply or args.verify_only else "not run (preview)")
    except ValueError as exc:
        raise ValueError("{}; account steps completed={}".format(exc, result["completed"])) from None
    return result


def execute(args, parser):
    try:
        if not args.name:
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
            if variable not in os.environ:
                raise ValueError("environment variable is not set: " + variable)
            return os.environ[variable]

        password, os_password = secret(args.password_env), secret(args.os_password_env)
        if (args.apply or args.verify_only) and password is None:
            if not os.isatty(0):
                raise ValueError("supply --password-env for noninteractive use")
            password = getpass.getpass("New BMC account password: ")
        for host in hosts:
            agent.validate(dict(role=host.role, name=args.name, id=args.id, channel=args.channel,
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
            line = json.dumps(result, ensure_ascii=False)
            for value in (password, os_password):
                if value:
                    line = line.replace(value, "<redacted>")
            print(line, flush=True)
    return int(failed)
