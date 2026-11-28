"""Bulk acceptance of fan/PSU reads through the production execution engine."""

import argparse
from collections import Counter
from copy import copy
import json
import math
import os
import resource
import threading
import time

from . import cli, hostlist, runtime
from .arguments import SENSOR_COMMANDS, add_examples, parse_args
from .diagnostics import debug
from .errors import NodetoolsError
from .inventory import Inventory


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate(result, expected):
    if result.get("error"):
        return "incomplete" if str(result["error"]).startswith("incomplete ") else "failed"
    rows = result.get("readings", [])
    ids = [str(row.get("id") or "") for row in rows]
    if len(rows) != expected or not all(ids) or len(set(ids)) != len(ids):
        return "incomplete"
    for row in rows:
        if result["cap"] == "psu":
            if not finite(row.get("input_watts")):
                return "incomplete"
        elif not finite(row.get("value")) or row.get("unit") not in ("RPM", "%"):
            return "incomplete"
    return "passed"


def run(hosts, capability, options, expected, emit):
    """Emit each completed node immediately; measure only this batch."""
    from unittest.mock import patch
    original = runtime.Redfish
    original_ssh = runtime.nvos._ssh
    local = threading.local()

    def measured_ssh(host, opts, command, deadline):
        started = time.monotonic()
        ssh_options = copy(opts)
        ssh_options.debug = False
        address = host.vars.get(host.role + "_os_host") or host.vars.get("os_host") or host.name
        debug(opts.debug, "batch_audit:" + host.name,
              f"SSH address={address!r} command={command!r}")
        result = original_ssh(host, ssh_options, command, deadline)
        debug(opts.debug, "batch_audit:" + host.name, f"SSH exit={result.returncode}")
        calls = getattr(local, "nvos_calls", None)
        if calls is not None:
            calls.append(dict(transport="nvos_ssh",
                         query="lm5066i vin/iin/pin" if command == runtime.nvos._HSC else command,
                         returncode=result.returncode, seconds=round(time.monotonic()-started, 4)))
        return result

    class MeasuredRedfish(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.audit_calls = []
            self.audit_closed = False
            local.transport = self

        def get_raw(self, path, *args, **kwargs):
            started = time.monotonic()
            status, body = super().get_raw(path, *args, **kwargs)
            self.audit_calls.append(dict(path=path, http=status,
                                         seconds=round(time.monotonic()-started, 4)))
            return status, body

        def close(self):
            try:
                super().close()
            finally:
                self.audit_closed = True

    class MeasuredRunner(runtime.Runner):
        def _one(self, host):
            local.transport = None
            local.nvos_calls = []
            started = time.monotonic()
            result = super()._one(host)
            transport = local.transport
            result["seconds"] = round(time.monotonic()-started, 4)
            result["requests"] = (transport.audit_calls if transport else []) + local.nvos_calls
            result["closed"] = transport.audit_closed if transport else True
            result["acceptance"] = validate(result, expected)
            if not result["closed"]:
                result["acceptance"] = "failed"
            return result

    started = time.monotonic()
    before = resource.getrusage(resource.RUSAGE_SELF)
    counts = Counter()
    requests = 0
    elapsed = []
    with patch.object(runtime, "Redfish", MeasuredRedfish), \
            patch.object(runtime.nvos, "_ssh", measured_ssh):
        for result in MeasuredRunner(capability, options).stream(hosts):
            counts[result["acceptance"]] += 1
            requests += len(result["requests"])
            elapsed.append(result["seconds"])
            emit(result)
    after = resource.getrusage(resource.RUSAGE_SELF)
    ordered = sorted(elapsed)
    return dict(selected=len(hosts), completed=len(elapsed), outcomes=dict(counts),
                jobs=options.jobs, expected_readings_per_node=expected, requests=requests,
                seconds=round(time.monotonic()-started, 3),
                node_seconds_max=max(elapsed, default=0),
                node_seconds_p95=ordered[min(len(ordered)-1, int(len(ordered)*.95))] if ordered else 0,
                cpu_seconds=round(after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime, 3),
                peak_rss_kib=after.ru_maxrss)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m nodetools_runtime.batch_audit", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_examples(parser,
        "python3 -m nodetools_runtime.batch_audit pspsu 'shelf[01-04]' --jobs 200 --expected-readings 6 --output /tmp/psu-audit.jsonl",
        "python3 -m nodetools_runtime.batch_audit pspsu shelf01 -x --expected-readings 6 --output /tmp/psu-debug.jsonl",
        'python3 -m nodetools_runtime.batch_audit nodefan --selector-file compute.selector --expected-readings 24 --output /tmp/fan-audit.jsonl',
    )
    parser.add_argument("command", choices=("nodefan", "nodepsu", "psfan", "pspsu", "nvswpsu"))
    parser.add_argument("selector", nargs="?")
    parser.add_argument("--selector-file")
    parser.add_argument("-i", "--inventory", default="inventory.ini")
    parser.add_argument("--jobs", type=int)
    parser.add_argument("-x", "--debug", action="store_true", help="show execution diagnostics on stderr")
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--node-timeout", type=float)
    parser.add_argument("--expected-readings", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if bool(args.selector) == bool(args.selector_file):
        parser.error("supply a selector or --selector-file")
    if (args.jobs is not None and args.jobs < 1) or args.expected_readings < 1:
        parser.error("jobs and expected-readings must be positive")
    selector = args.selector
    if args.selector_file:
        from pathlib import Path
        selector = Path(args.selector_file).read_text().strip()
    inv = Inventory.from_file(args.inventory)
    role, capability = SENSOR_COMMANDS[args.command]
    names = list(dict.fromkeys(hostlist.expand(selector)))
    missing = [name for name in names if name not in inv.hosts]
    wrong_role = [name for name in names if name in inv.hosts and inv.hosts[name].role != role]
    if not names or missing or wrong_role:
        parser.error("empty, missing or wrong-role selection: " + repr(missing + wrong_role))
    hosts = [inv.hosts[name] for name in names]
    targets = [(host.vars.get("nvswitch_os_host") or host.name)
               if args.command == "nvswpsu" else host.address for host in hosts]
    if len(set(targets)) != len(hosts):
        parser.error("selected inventory nodes share a BMC address")
    core_args = parse_args([args.command, names[0]])
    for key in ("jobs", "timeout", "node_timeout", "debug"):
        setattr(core_args, key, getattr(args, key))
    options = cli.Options(core_args)
    try:
        cli._apply_defaults(options, inv, core_args)
    except (NodetoolsError, ValueError) as exc:
        parser.error(str(exc))
    if options.jobs < 1:
        parser.error("jobs must be positive")
    args.jobs = options.jobs
    debug(args.debug, "batch_audit",
          f"selected={len(hosts)} jobs={options.jobs} timeout={options.timeout} node_timeout={options.node_timeout}")
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        def write(record):
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
        write(dict(type="selection", command=args.command, hosts=names, jobs=args.jobs,
                   timeout=options.timeout, node_timeout=options.node_timeout,
                   expected_readings=args.expected_readings))
        completed = 0
        def emit(result):
            nonlocal completed
            completed += 1
            debug(args.debug, "batch_audit:" + result["name"],
                  f"acceptance={result['acceptance']} requests={len(result['requests'])} seconds={result['seconds']}")
            write(dict(type="node", **result))
            if result["acceptance"] != "passed":
                print(json.dumps(dict(host=result["name"], acceptance=result["acceptance"],
                      error=result["error"], requests=len(result["requests"]),
                      seconds=result["seconds"])), flush=True)
            elif completed % 25 == 0 or completed == len(hosts):
                print("completed {}/{}".format(completed, len(hosts)), flush=True)
        summary = run(hosts, capability, options, args.expected_readings, emit)
        write(dict(type="summary", **summary))
        print(json.dumps(summary), flush=True)
    return int(summary["completed"] != len(hosts)
               or summary["outcomes"].get("passed", 0) != len(hosts))


if __name__ == "__main__":
    raise SystemExit(main())
