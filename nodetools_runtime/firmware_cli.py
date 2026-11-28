"""Command line for firmware inventory and OEM-specific updates."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import signal
import sys
import zipfile
from pathlib import Path

from .help_language import LocalizedArgumentParser
from . import selection
from .arguments import add_examples
from .errors import CapUnsupported, NodetoolsError
from .inventory import Inventory
from .oem.hgx import smc_firmware
from .runtime import build_device
from .subjects import firmware

ADAPTERS = {smc_firmware.MODEL: smc_firmware}


class _FirmwareParser(LocalizedArgumentParser):
    def parse_args(self, args=None, namespace=None):
        values = list(sys.argv[1:] if args is None else args)
        if not getattr(self, "_legacy_syntax", False):
            position = 0
            while position < len(values):
                token = values[position]
                if not token.startswith("-"):
                    if token in ("show", "status", "check", "update"):
                        return parser(self.prog, legacy=True).parse_args(values, namespace)
                    break
                option = self._option_string_actions.get(token.split("=", 1)[0])
                if option is None or token == "--":
                    break
                position += 1 if "=" in token or option.nargs == 0 else 2
        return super().parse_args(values, namespace)


def parser(prog, legacy=False):
    cli = _FirmwareParser(
        prog=prog, description="Query and manage BMC-managed firmware.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    cli._legacy_syntax = legacy
    if not legacy:
        cli.add_argument("nodes", metavar="NODE(S)")
    cli.add_argument("-i", "--inventory", help="inventory file")
    cli.add_argument("--jobs", type=int, help="parallel node queries")
    cli.add_argument("--timeout", type=float, help="limit for each Redfish read")
    cli.add_argument("--node-timeout", type=float, help="total reading budget per node")
    cli.add_argument("-x", "--debug", action="store_true", default=argparse.SUPPRESS,
                     help="show transport diagnostics")
    add_examples(cli, "nodefw HGX654", "nodefw -x HGX654 show", "nodefw HGX654 status")
    actions = cli.add_subparsers(dest="action")
    for action, help_text in (
        ("show", "list installed firmware"),
        ("status", "read Redfish task status"),
        ("check", "verify a package and update prerequisites without writing"),
        ("update", "submit a firmware update and report the accepted task"),
    ):
        command = actions.add_parser(
            action, help=help_text, description=help_text.capitalize() + ".",
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        command.add_argument("-x", "--debug", action="store_true", default=argparse.SUPPRESS,
                             help="show transport diagnostics")
        examples = {
            "show": ("nodefw HGX654 show", "nodefw 'HGX[650-654]' show --jobs 2"),
            "status": ("nodefw HGX654 status", "nodefw HGX654 status --task 1"),
            "check": ("nodefw HGX654 check bmc bmc-package.zip",),
            "update": ("nodefw HGX654 check bmc bmc-package.zip", "nodefw HGX654 update bmc bmc-package.zip"),
        }
        add_examples(command, *examples[action])
        if action in ("show", "status"):
            command.add_argument("--timeout", type=float, default=argparse.SUPPRESS, help="limit for each Redfish read")
            command.add_argument("--node-timeout", type=float, default=argparse.SUPPRESS, help="total reading budget per node")
        if legacy:
            command.add_argument("nodes", metavar="NODE(S)")
        command.add_argument("-i", "--inventory", default=argparse.SUPPRESS, help="inventory file")
        jobs_help = "concurrent uploads; required for multiple nodes" if action == "update" else "parallel node queries"
        command.add_argument("--jobs", type=int, default=argparse.SUPPRESS, help=jobs_help)
        if action == "status":
            command.add_argument("--task", help="task ID returned by update")
        if action in ("check", "update"):
            command.add_argument("component", metavar="COMPONENT", help="firmware component, e.g. bmc or bios")
            command.add_argument("package", type=Path, help="verified vendor package")
        if action == "update":
            command.add_argument("--reflash", action="store_true", help="allow writing the installed version again")
    cli.set_defaults(action="show", task=None)
    return cli


def run(device, args):
    if args.action == "show":
        return list(firmware.inventory(device))
    if args.action == "status":
        if args.task is not None:
            return [line for path, task in firmware.tasks(device, args.task)
                    for line in firmware.task_details(path, task)]
        return [
            "{}  {}  {}  {}  {}".format(
                path.rsplit("/", 1)[-1], firmware.task_name(path.rsplit("/", 1)[-1], task),
                task.get("TaskState") or "NA", task.get("TaskStatus") or "NA",
                "{}%".format(task["PercentComplete"]) if task.get("PercentComplete") is not None else "NA",
            )
            for path, task in firmware.tasks(device, args.task)
        ] or ["BMC reports no Redfish tasks"]
    candidates = [
        (path, system, ADAPTERS[system["Model"]])
        for path, system in firmware.collection(device, "/redfish/v1/Systems", required=True)
        if system.get("Model") in ADAPTERS
    ]
    if not candidates:
        raise CapUnsupported("no update adapter for the reported system models")
    if len(candidates) != 1:
        raise CapUnsupported("firmware writes require exactly one matching Redfish system")
    path, system, adapter = candidates[0]
    return [adapter.run(device, path, system, args.component, args.package,
                        apply=args.action == "update", reflash=getattr(args, "reflash", False))]


def main(argv, prog):
    cli = parser(prog)
    if argv is not None and not argv:
        cli.print_help()
        return 0
    args = cli.parse_args(argv)
    if not args.action:
        cli.print_help()
        return 0
    write = args.action == "update"
    return run_selected(args, prog, run, single=write and args.jobs is None, write=write)


def run_selected(args, prog, runner, single, write=False):
    from .cli import Options, _apply_defaults, _interrupt_sensor_query, _inventory_path

    args.jobs = getattr(args, "jobs", None)
    args.timeout = getattr(args, "timeout", None)
    try:
        inv = Inventory.from_file(_inventory_path(args.inventory))
        options = Options(args)
        _apply_defaults(options, inv, args)
        hosts = selection.select(inv, selectors=[args.nodes])
        if single and (
            len(hosts) != 1 or hosts[0].name.casefold() != args.nodes.casefold()
        ):
            message = ("specify one inventory node name or pass --jobs for a batch update"
                       if prog == "nodefw" else "specify exactly one inventory node name")
            raise NodetoolsError(message)
        if not hosts:
            raise NodetoolsError("no inventory nodes selected")
        if write and len(hosts) > 1:
            addresses = [host.address.casefold() for host in hosts if host.address]
            if len(set(addresses)) != len(addresses):
                raise NodetoolsError("selected nodes share a BMC address")
        if options.jobs < 1:
            raise NodetoolsError("--jobs must be at least 1")

        def query(host):
            device = None
            try:
                device = build_device(host, options, load_profile=False)
                lines = list(runner(device, args))
                if not lines:
                    raise CapUnsupported("no information returned")
                return host.name, lines, None
            except (NodetoolsError, OSError, zipfile.BadZipFile) as exc:
                return host.name, [], exc
            finally:
                if device is not None:
                    device.close()

        def display(result):
            name, lines, error = result
            if error is not None:
                print("{}: {}".format(name, error), file=sys.stderr, flush=True)
                return 1
            for line in lines:
                print("{}: {}".format(name, line), flush=True)
            return 0

        if single or len(hosts) == 1:
            return display(query(hosts[0]))
        previous_handler = None if write else signal.signal(signal.SIGINT, _interrupt_sensor_query)
        try:
            failed = 0
            with ThreadPoolExecutor(max_workers=min(options.jobs, len(hosts))) as pool:
                pending = {pool.submit(query, host) for host in hosts}
                try:
                    for future in as_completed(pending):
                        failed |= display(future.result())
                        pending.remove(future)
                except KeyboardInterrupt:
                    remaining = [future for future in pending if not future.cancel()]
                    for future in as_completed(remaining):
                        display(future.result())
                    raise
            return failed
        finally:
            if previous_handler is not None:
                signal.signal(signal.SIGINT, previous_handler)
    except (NodetoolsError, OSError, ValueError) as exc:
        print("{}: {}".format(prog, exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
