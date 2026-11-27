"""Command lines for BMC management, Redfish tasks and FRU serials."""

import argparse
from datetime import datetime

from .help_language import LocalizedArgumentParser
from . import firmware_cli
from .arguments import add_examples
from .subjects import management

DESCRIPTIONS = {
    "nodetask": "Read Redfish task states and progress.",
    "nodebmc": "Read BMC information, manage users through OS IPMI, set the clock, or restart BMC.",
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
        "nodebmc": (
            "nodebmc HGX654",
            "nodebmc HGX654 time",
            "nodebmc HGX654 time --set 2026-11-24T20:00:00+08:00",
            "nodebmc HGX654 reset",
            "nodebmc 'gb300-ct[01-04],nvsw[01-04]' user --name nvis --sol",
            "nodebmc gb300-ct01 user --name nvis --sol --apply",
            "nodebmc nvsw01 user --name nvis --id 8 --channel 1 --apply",
            "nodebmc 'gb300-ct[01-04],nvsw[01-04]' user --name nvis --sol --password-env NEW_BMC_PASSWORD --jobs 4 --apply",
            "nodebmc nvsw01 user --name nvis --password-env NEW_BMC_PASSWORD --verify-only",
        ),
        "nodefru": ("nodefru 'HGX[650-654]'",),
    }[prog])
    cli.add_argument("nodes", metavar="NODE(S)")
    cli.add_argument("-i", "--inventory", help="inventory file")
    cli.add_argument("--jobs", type=int, help="parallel node queries")
    if prog == "nodetask":
        cli.add_argument("task_id", metavar="TASK_ID", nargs="?", help="show task details")
        cli.add_argument("--task", help="show task details by ID")
    if prog == "nodebmc":
        cli.add_argument("action", nargs="?", choices=("info", "time", "reset", "user"), default="info",
                         help="info shows firmware and clock; time reads the clock; reset restarts BMC; user manages accounts through OS")
        cli.add_argument("--set", type=datetime_with_zone, help="with time, set an ISO 8601 timestamp with offset")
    if prog == "nodebmc":
        from . import bmc_user_cli
        bmc_user_cli.add_arguments(cli)
        cli.epilog += ("\n\nUser action previews by default. --apply prompts for the new BMC password "
                       "on a terminal; for batches export NEW_BMC_PASSWORD and use --password-env. "
                       "--sol applies to compute only; NVSwitch skips SOL. User IDs are selected "
                       "automatically unless --id is provided.")
    if not argv:
        cli.print_help()
        return 0
    args = cli.parse_args(argv)
    if prog == "nodetask" and args.task_id is not None and args.task is not None:
        cli.error("specify TASK_ID or --task, not both")
    if prog == "nodebmc" and args.set is not None and args.action != "time":
        cli.error("--set requires the time action")

    if prog == "nodebmc" and args.action == "user":
        return bmc_user_cli.execute(args, cli)

    def run(device, args):
        if prog == "nodetask":
            return firmware_cli.run(device, argparse.Namespace(action="status", task=args.task_id or args.task))
        if prog == "nodefru":
            return list(management.fru(device))
        return management.bmc(device, args.action, args.set)

    write = prog == "nodebmc" and (args.action == "reset" or args.set is not None)
    return firmware_cli.run_selected(args, prog, run, single=write)
