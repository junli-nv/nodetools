#!/bin/bash
# Author: Junli Zhang <junliz@nvidia.com>
# Optional BCM adapter. Manual hosts.list files work without this script.
exec python3 - "$@" <<'PY'
import argparse
import sys
import ipaddress
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

parser = argparse.ArgumentParser(prog='gen_hosts.sh', description="Export BCM devices to a generic hosts.list; requires cmsh only when run.")
parser.formatter_class = argparse.RawDescriptionHelpFormatter
parser.epilog = 'Examples\n  ./gen_hosts.sh\n  ./gen_hosts.sh -o /tmp/hosts.list --timeout 30'
parser.add_argument("-o", "--output", default="hosts.list")
parser.add_argument("--timeout", type=float, default=30, help="seconds per cmsh query")
parser.add_argument("-x", "--debug", action="store_true", help="show diagnostics on stderr")
parser.epilog += '\n  ./gen_hosts.sh -x -o /tmp/hosts-debug.list'
args = parser.parse_args()
def trace(message):
    if args.debug:
        print("[gen_hosts] " + message, file=sys.stderr, flush=True)

if not math.isfinite(args.timeout) or args.timeout <= 0:
    parser.error("--timeout must be positive")
if not shutil.which("cmsh"):
    parser.error("cmsh not found; load its module, or write hosts.list manually")

def parse(text, label, interface):
    rows, name = [], None
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        if len(fields) == 1 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", fields[0]):
            if name is not None:
                raise ValueError("missing management interface for " + name)
            name = fields[0]
            continue
        if fields[0] != interface:
            raise ValueError("unexpected cmsh interface output for " + (name or label))
        if interface == "physical" and fields[1:2] != ["eth0"]:
            continue
        if name is None or len(fields) < 3:
            raise ValueError("interface without a hostname or IP address")
        address = ipaddress.ip_address(fields[2])
        if address.is_unspecified:
            raise ValueError("unconfigured management address for " + name)
        rows.append((label, name, str(address)))
        name = None
    if name is not None:
        raise ValueError("missing management interface for " + name)
    return rows

queries = (
    ("PhysicalNode", "PhysicalNode", "bmc"),
    ("NVSwitch", "Switch", "bmc"),
    ("PowerShelf", "PowerShelf", "physical"),
)
temporary = None
try:
    records, names = [], set()
    for label, kind, interface in queries:
        query = "device; foreach -t {} ( get hostname; interfaces; list {})".format(kind, interface)
        trace("query type=" + kind + " interface=" + interface)
        result = subprocess.run(["cmsh", "-c", query], capture_output=True, text=True, timeout=args.timeout)
        if result.returncode:
            raise ValueError("cmsh {} query failed (exit {}); output unchanged".format(kind, result.returncode))
        trace("cmsh exit=" + str(result.returncode))
        for row in sorted(parse(result.stdout, label, interface)):
            if row[1].casefold() in names:
                raise ValueError("duplicate BCM hostname: " + row[1])
            names.add(row[1].casefold())
            records.append(row)
    if not records:
        raise ValueError("BCM returned no devices; output unchanged")
    trace("validated devices=" + str(len(records)) + " output=" + args.output)
    target = Path(args.output)
    with tempfile.NamedTemporaryFile(mode="w", dir=target.parent, prefix="."+target.name+".", delete=False) as handle:
        temporary = handle.name
        for row in records:
            handle.write("{} {} {}\n".format(*row))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    temporary = None
    print("wrote {} devices to {}".format(len(records), target))
except (OSError, ValueError, subprocess.SubprocessError) as exc:
    parser.exit(1, "gen_hosts: {}\n".format(exc))
finally:
    if temporary:
        Path(temporary).unlink(missing_ok=True)
PY
