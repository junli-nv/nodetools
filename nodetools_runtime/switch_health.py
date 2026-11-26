"""Use the nodetools inventory to run a Bash health check over SSH."""

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from .help_language import LocalizedArgumentParser
from .config import DEFAULT_INVENTORY
from .errors import NodetoolsError
from .hostlist import expand
from .inventory import Inventory


def query(target, ssh, script, timeout):
    try:
        proc = subprocess.run([*ssh, "--", target, "bash", "-s"], input=script,
                              text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout:g}s"
    except OSError as exc:
        return False, str(exc)
    output = proc.stdout.rstrip("\n")
    if proc.returncode:
        return False, " ".join(proc.stderr.split()) or f"SSH exited {proc.returncode}"
    return bool(output.strip()), output if output.strip() else "no switch output"


MONITOR_TEMPERATURES = {
    "ibswmon": ("Ambient-Fan-Side-Temp", "Ambient-Port-Side-Temp"),
    "spxswmon": ("Port-Ambient-Sensor", "Main-Board-Ambient-Sensor"),
}



def main(command, argv):
    monitor = MONITOR_TEMPERATURES.get(command)
    sample = "ib-leaf" if command.startswith("ib") else "spx-leaf"
    description = "Sample switch temperatures, fans and PSU status as CSV." if monitor else "Read switch health and alarm logs."
    repeat_example = f"\n  ./{command} {sample}01 --count 3 --interval 5" if monitor else ""
    parser = LocalizedArgumentParser(
        prog=command, description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""Examples
  ./{command} '{sample}[01-04]'
  ./{command} {sample}01 -i lab.ini --dry-run
  ./{command} {sample}01 --checks ./my-checks.sh{repeat_example}""",
    )
    parser.add_argument("hosts", help="switch name or host range")
    parser.add_argument("-i", "--inventory", default=os.getenv("NODETOOLS_INVENTORY") or DEFAULT_INVENTORY)
    parser.add_argument("--user", help="override the inventory SSH user")
    parser.add_argument("--jobs", default=9, type=int, help="parallel switches (default 9)")
    parser.add_argument("--timeout", default=30, type=float, help="seconds per switch (default 30)")
    parser.add_argument("--checks", type=Path, help="use a local Bash check file")
    parser.add_argument("--dry-run", action="store_true", help="show targets without connecting")
    if monitor:
        parser.add_argument("--count", type=int, default=1, help="number of samples (default 1)")
        parser.add_argument("--interval", type=float, default=5, help="seconds between samples (default 5)")
    args = parser.parse_args(argv)
    if monitor and (args.count < 1 or not math.isfinite(args.interval) or args.interval <= 0):
        parser.error("--count and --interval must be positive")
    if args.jobs < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--jobs and --timeout must be positive")
    try:
        inv = Inventory.from_file(args.inventory)
        names = expand(args.hosts)
        script = args.checks.read_text() if args.checks else sys.stdin.read()
    except (NodetoolsError, OSError, UnicodeError) as exc:
        parser.error(str(exc))
    if not names:
        parser.error("specify at least one switch")
    targets = {}
    missing = []
    for name in names:
        host = inv.get(name)
        address = (host.get("ssh_host") or host.address) if host else None
        if not address:
            missing.append(name)
            continue
        user = args.user or host.get("ssh_user")
        targets[name] = f"{user}@{address}" if user else address
    if args.dry_run:
        for name in missing:
            print(f"{name}: ERROR: no SSH address in inventory")
        for name, target in targets.items():
            print(f"{name}: {target}")
        return int(bool(missing))

    password = bool(os.getenv("SSHPASS"))
    if password and not shutil.which("sshpass"):
        parser.error("SSHPASS is set but sshpass was not found")
    ssh = ["sshpass", "-e"] if password else []
    ssh += ["ssh", "-T", "-o", f"BatchMode={'no' if password else 'yes'}"]
    writer = csv.writer(sys.stdout, lineterminator="\n") if monitor else None
    if writer:
        writer.writerow(("Timestamp", "Switch", *monitor, "FAN-Direction", "FAN", "PSU-Status"))
        sys.stdout.flush()
    count = args.count if monitor else 1
    failed = False
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for round_number in range(count):
            timestamp = datetime.now().isoformat(timespec="seconds")
            for name in missing:
                _report(name, False, "no SSH address in inventory", writer, timestamp)
                failed = True
            jobs = {pool.submit(query, target, ssh, script, args.timeout): name
                    for name, target in targets.items()}
            for job in as_completed(jobs):
                success, message = job.result()
                failed |= not _report(jobs[job], success, message, writer, timestamp)
            if round_number + 1 < count:
                time.sleep(args.interval)
    return int(failed)


def _report(name, success, message, writer, timestamp):
    if writer:
        fields = [value.strip() or "NA" for value in message.splitlines()] if success else []
        error = None
        if len(fields) != 5:
            error = "expected five sensor values" if success else message
            fields = ["NA"] * 5
        elif all(value == "NA" for value in fields):
            error = "no sensor readings"
        if error:
            print(f"{name}: ERROR: {error}", file=sys.stderr, flush=True)
        writer.writerow((timestamp, name, *fields))
        sys.stdout.flush()
        return error is None
    print(f"{name}: {'' if success else 'ERROR: '}{' '.join(message.split())}", flush=True)
    return success


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1], sys.argv[2:]))
    except KeyboardInterrupt:
        sys.exit(130)
