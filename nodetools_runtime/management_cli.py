"""Command lines for BMC management, Redfish tasks and FRU serials."""

import argparse
from datetime import datetime

from .help_language import LocalizedArgumentParser
from . import firmware_cli
from .arguments import add_examples
from .subjects import management

DESCRIPTIONS = {
    "nodetask": "Read Redfish task states and progress.",
    "nodebmc": "Read BMC information, set the BMC clock, or restart the BMC.",
    "nodefru": "Read the system serial number.",
}


def datetime_with_zone(value):
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        parsed = None
    if parsed is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("expected ISO 8601 time with offset, such as 2026-11-24T20:00:00+08:00")
    return parsed


def main(argv, prog):
    cli = LocalizedArgumentParser(
        prog=prog, description=DESCRIPTIONS[prog], formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_examples(cli, *{
        "nodetask": ("nodetask HGX654", "nodetask HGX654 1"),
        "nodebmc": ("nodebmc HGX654", "nodebmc HGX654 time", "nodebmc HGX654 reset", "nodebmc HGX654 time --set 2026-11-24T20:00:00+08:00"),
        "nodefru": ("nodefru 'HGX[650-654]'",),
    }[prog])
    cli.add_argument("nodes", metavar="NODE(S)")
    cli.add_argument("-i", "--inventory", help="inventory file")
    cli.add_argument("-x", "--debug", action="store_true", help="show transport diagnostics")
    cli.add_argument("--jobs", type=int, help="parallel node queries")
    if prog == "nodetask":
        cli.add_argument("task_id", metavar="TASK_ID", nargs="?", help="show task details")
        cli.add_argument("--task", help="show task details by ID")
    if prog == "nodebmc":
        cli.add_argument("action", nargs="?", choices=("info", "time", "reset"), default="info",
                         help="info shows firmware and clock; time reads the clock; reset restarts the BMC")
        cli.add_argument("--set", type=datetime_with_zone, help="with time, set an ISO 8601 timestamp with offset")
    if not argv:
        cli.print_help()
        return 0
    if prog == "nodebmc" and any(v in ("user", "adduser", "deluser", "moduser", "listuser", "checkuser") for v in argv):
        cli.error("BMC users through OS: nodeos HOSTS bmc adduser USERNAME PASSWORD")
    args = cli.parse_args(argv)
    if prog == "nodetask" and args.task_id is not None and args.task is not None:
        cli.error("specify TASK_ID or --task, not both")
    if prog == "nodebmc" and args.set is not None and args.action != "time":
        cli.error("--set requires the time action")

    def run(device, args):
        if prog == "nodetask":
            return firmware_cli.run(device, argparse.Namespace(action="status", task=args.task_id or args.task))
        if prog == "nodefru":
            return list(management.fru(device))
        return management.bmc(device, args.action, args.set)

    write = prog == "nodebmc" and (args.action == "reset" or args.set is not None)
    return firmware_cli.run_selected(args, prog, run, single=write)
