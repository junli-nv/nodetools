"""Repeatable management read audit: Redfish GET and allowlisted NVOS reads."""

import argparse
from contextlib import ExitStack
from copy import copy
import json
import math
from unittest.mock import patch

from . import cli, oem, runtime
from .arguments import INFO_COMMANDS, SENSOR_COMMANDS, add_examples, parse_args
from .diagnostics import debug as trace
from .errors import NodetoolsError
from .inventory import Inventory
from .subjects import firmware, management
from .transport.ipmi import Ipmi
from .transport.redfish import Redfish


def cases_for(role):
    cases = []
    for command, (command_role, cap) in SENSOR_COMMANDS.items():
        if command_role == role:
            cases.append(dict(id=command, command=command, cap=cap, kind="sensor", flags=[]))
            if cap in ("fan", "inlet", "psu"):
                variant = "details" if cap == "psu" else "check"
                flags = ["--details"] if cap == "psu" else ["check"]
                cases.append(dict(id=command+"/"+variant, command=command, cap=cap,
                                  kind="sensor", flags=flags))
    if role == "compute":
        cases.append(dict(id="nodemon", command="nodemon", cap="monitor", kind="sensor", flags=[]))
    variants = {
        "power": [("", [])],
        "eventlog": [("", [])] + ([("bmc", ["bmc"]), ("hmc", ["hmc"])] if role == "compute"
                                else [("bmc", ["bmc"])] if role == "nvswitch" else []),
        "inventory": [("", []), ("details", ["--details"]), ("tasks", ["--tasks"])]
                     + ([("gpu", ["--gpu"])] if role == "compute" else []),
        "bios": [("", []), ("pending", ["pending"])],
        "bootorder": [("", [])], "netboot": [("", ["list"])],
        "macs": [("", [])], "vm": [("", [])],
    }
    for command, (command_role, cap) in INFO_COMMANDS.items():
        if command_role != role or cap not in variants:
            continue
        for label, flags in variants[cap]:
            cases.append(dict(id=command + ("/" + label if label else ""),
                              command=command, cap=cap, kind="info", flags=flags))
    for name, cap in (("nodebmc/info", "bmc_info"), ("nodebmc/time", "bmc_time"),
                      ("nodefru", "fru"), ("nodefw/show", "firmware"),
                      ("nodefw/status", "tasks"), ("nodetask", "tasks")):
        cases.append(dict(id=name, command=name.split("/")[0], cap=cap, kind="management", flags=[]))
    return cases


def _invoke(host, case, options):
    if case["kind"] == "sensor":
        return runtime.Runner(case["cap"], options)._one(host)
    if case["kind"] == "info":
        return runtime.InfoRunner(case["cap"], options)._one(host)
    device = None
    try:
        device = runtime.build_device(host, options, load_profile=False)
        device.odm = oem.for_device(device, configured_read=True)
        cap = case["cap"]
        if cap.startswith("bmc_"):
            lines = management.bmc(device, cap.split("_")[1])
        elif cap == "fru":
            lines = list(management.fru(device))
        elif cap == "firmware":
            lines = list(firmware.inventory(device))
        else:
            tasks = list(firmware.tasks(device))
            lines = [str(task.get("TaskState") or "Unknown") for _, task in tasks]
            lines = lines or ["BMC reports no Redfish tasks"]
        return dict(lines=lines, error=None)
    except Exception as exc:
        return dict(lines=[], error=type(exc).__name__ + ": " + str(exc))
    finally:
        if device is not None:
            device.close()


def run_case(host, case, user=None, password=None, capture=None, *,
             inventory=None, timeout=None, node_timeout=None, debug=False,
             os_user=None, os_password=None):
    allowed = {c["id"]: c for c in cases_for(host.role)}
    if case != allowed.get(case.get("id")):
        raise ValueError("audit accepts only its built-in read-only cases")
    argv = [case["command"], host.name, *case["flags"]]
    if case["kind"] == "management":
        argv = ["nodepsu", host.name]
    args = parse_args(argv)
    args.timeout, args.node_timeout, args.debug = timeout, node_timeout, debug
    options = cli.Options(args)
    cli._apply_defaults(options, inventory or Inventory(), args)
    action = (getattr(args, "action", None) or getattr(args, "action_opt", None) or "").lower()
    options.action = None if action in cli._READ_WORDS else action
    if case["cap"] == "netboot":
        from .subjects import netboot
        options.netboot_filters = netboot.normalize_filters(
            {key:getattr(args, key, None) for key in netboot.FILTERS})
        options.netboot_family = args.family
    options.user, options.password = user, password
    requests = []
    original = Redfish.get_raw
    original_ssh = runtime.nvos._ssh

    def get_raw(transport, path, *args, **kwargs):
        status, body = original(transport, path, *args, **kwargs)
        requests.append(dict(path=path, status=status))
        if capture is not None:
            capture(path, status, body)
        return status, body

    def nvos_read(target, opts, command, deadline):
        allowed_queries = {
            runtime.nvos._HSC,
            "nv show platform environment psu -o json",
            "nv show platform environment temperature -o json",
        }
        if command not in allowed_queries:
            raise NodetoolsError("read audit rejects an unlisted NVOS query")
        ssh_options = copy(opts)
        ssh_options.user, ssh_options.password = os_user, os_password
        ssh_options.debug = False
        address = target.vars.get(target.role + "_os_host") or target.vars.get("os_host") or target.name
        username = os_user or target.vars.get(target.role + "_os_user") or target.vars.get("os_user")
        trace(debug, "read_audit:" + target.name,
              f"SSH address={address!r} user={username!r} command={command!r}")
        result = original_ssh(target, ssh_options, command, deadline)
        trace(debug, "read_audit:" + target.name, f"SSH exit={result.returncode}")
        marker = "nvos:lm5066i-vin-iin-pin" if command == runtime.nvos._HSC else "nvos:" + command
        requests.append(dict(path=marker, status=result.returncode, transport="nvos_ssh"))
        if capture is not None:
            capture(marker, result.returncode, dict(stdout=result.stdout, stderr=result.stderr))
        return result

    def forbidden(*args, **kwargs):
        raise NodetoolsError("read audit forbids writes and unlisted IPMI fallback")

    with ExitStack() as stack:
        stack.enter_context(patch.object(Redfish, "get_raw", get_raw))
        for name in ("post", "patch"):
            stack.enter_context(patch.object(Redfish, name, forbidden))
        stack.enter_context(patch.object(Ipmi, "run", forbidden))
        stack.enter_context(patch.object(runtime.nvos, "_ssh", nvos_read))
        trace(debug, "read_audit:" + host.name,
              f"case={case['id']} timeout={options.timeout} node_timeout={options.node_timeout}")
        result = _invoke(host, case, options)
    error = result.get("error")
    rows = result.get("readings", [])
    lines = result.get("lines", [])
    if not rows and not lines and not error:
        error = "empty output: completeness needs verification"
    startup = {"/redfish/v1", "/redfish/v1/", "/redfish/v1/Managers",
               "/redfish/v1/Systems", "/redfish/v1/Chassis"}
    unexpected = [r["path"] for r in requests if r["path"] in startup]
    state = "failed" if error else "discovery" if unexpected else "read_ok"
    trace(debug, "read_audit:" + host.name, f"case={case['id']} state={state} requests={len(requests)}")
    return dict(case=case["id"], state=state, requests=requests, request_count=len(requests),
                rows=len(rows) if rows else len(lines), error=error,
                readings=[{k:r.get(k) for k in ("id","value","unit","health","kind")}
                          for r in rows], discovery=unexpected)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m nodetools_runtime.read_audit", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_examples(parser,
        'python3 -m nodetools_runtime.read_audit gb300-ct01 --list',
        'python3 -m nodetools_runtime.read_audit gb300-ct01',
        'python3 -m nodetools_runtime.read_audit gb300-ct01 -x',
        'python3 -m nodetools_runtime.read_audit nvlsw-01 -x --only nvswpsu --os-user admin --os-password-env NVOS_PASSWORD',
        'python3 -m nodetools_runtime.read_audit shelf01 --only pspsu --only psfan',
    )
    parser.add_argument("-x", "--debug", action="store_true", help="show execution diagnostics on stderr")
    parser.add_argument("--timeout", type=float, help="request timeout; default from inventory")
    parser.add_argument("--node-timeout", type=float, help="per-node budget; default from inventory")
    parser.add_argument("--os-user", help="NVOS SSH user override; separate from BMC --user")
    parser.add_argument("--os-password-env", help="environment variable containing NVOS SSH password")
    parser.add_argument("host")
    parser.add_argument("-i", "--inventory", default="inventory.ini")
    parser.add_argument("--only", action="append", help="one or more case IDs; default all")
    parser.add_argument("--list", action="store_true", help="list cases without contacting BMC")
    parser.add_argument("--user")
    parser.add_argument("--password-env", help="environment variable containing BMC password")
    args = parser.parse_args(argv)
    for flag in ("timeout", "node_timeout"):
        value = getattr(args, flag)
        if value is not None and (not math.isfinite(value) or value <= 0):
            parser.error("--" + flag.replace("_", "-") + " must be finite and positive")
    inv = Inventory.from_file(args.inventory)
    host = inv.hosts[args.host]
    cases = cases_for(host.role)
    if args.only:
        unknown = set(args.only) - {c["id"] for c in cases}
        if unknown:
            parser.error("unknown read-only cases: " + ", ".join(sorted(unknown)))
        cases = [c for c in cases if c["id"] in args.only]
    if args.list:
        for case in cases:
            print(case["id"])
        return 0
    import os
    password = None
    if args.password_env:
        if args.password_env not in os.environ:
            parser.error("password environment variable is not set")
        password = os.environ[args.password_env]
    os_password = None
    if args.os_password_env:
        if args.os_password_env not in os.environ:
            parser.error("OS password environment variable is not set")
        os_password = os.environ[args.os_password_env]
    failed = False
    for case in cases:
        result = run_case(host, case, args.user, password, inventory=inv,
                          timeout=args.timeout, node_timeout=args.node_timeout, debug=args.debug,
                          os_user=args.os_user, os_password=os_password)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        failed |= result["state"] != "read_ok"
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
