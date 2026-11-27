"""Repeatable management read audit: Redfish GET and allowlisted NVOS reads."""

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
from unittest.mock import patch

from . import cli, oem, runtime
from .arguments import INFO_COMMANDS, SENSOR_COMMANDS, parse_args
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
    device = runtime.build_device(host, options, load_profile=False)
    try:
        device.odm = oem.for_device(device)
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
        device.close()


def run_case(host, case, user=None, password=None, capture=None):
    allowed = {c["id"]: c for c in cases_for(host.role)}
    if case != allowed.get(case.get("id")):
        raise ValueError("audit accepts only its built-in read-only cases")
    argv = [case["command"], host.name, *case["flags"]]
    if case["kind"] == "management":
        argv = ["nodepsu", host.name]
    args = parse_args(argv)
    options = cli.Options(args)
    action = (getattr(args, "action", None) or getattr(args, "action_opt", None) or "").lower()
    options.action = None if action in cli._READ_WORDS else action
    if case["cap"] == "netboot":
        from .subjects import netboot
        options.netboot_filters = netboot.normalize_filters(
            {key:getattr(args, key, None) for key in netboot.FILTERS})
        options.netboot_family = args.family
    options.timeout = 3
    options.timeout_explicit = True
    options.node_timeout = 15
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
        result = original_ssh(target, opts, command, deadline)
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
    return dict(case=case["id"], state=state, requests=requests, request_count=len(requests),
                rows=len(rows) if rows else len(lines), error=error,
                readings=[{k:r.get(k) for k in ("id","value","unit","health","kind")}
                          for r in rows], discovery=unexpected)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("-i", "--inventory", default="inventory.ini")
    parser.add_argument("--only", action="append", help="one or more case IDs; default all")
    parser.add_argument("--list", action="store_true", help="list cases without contacting BMC")
    parser.add_argument("--user")
    parser.add_argument("--password-env", help="environment variable containing BMC password")
    args = parser.parse_args(argv)
    host = Inventory.from_file(args.inventory).hosts[args.host]
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
    failed = False
    for case in cases:
        result = run_case(host, case, args.user, password)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        failed |= result["state"] != "read_ok"
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
