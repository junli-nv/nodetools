"""Command-line argument parsing.

The command surface mirrors the legacy ``node*`` scripts: the **prefix** names
the node role and the **suffix** names the subject, e.g. ``nodepsu``,
``nodelist``, ``nvswtemp``, ``pspsu``::

    node  -> compute tray     fan / psu / temp
    nvsw  -> NVSwitch         psu / temp
    ps    -> power shelf      fan / psu / temp

GBXX needs all three prefixes; BXX only ``node*``.  ``nodelist``/``nodeping``
are role-agnostic and cover the whole inventory.  ``nodetools <command>``
still works, and the legacy short names (``list``/``ping``) are aliases.
"""

import argparse

from .config import (
    CAPS,
    DEFAULT_INVENTORY,
    DEFAULT_JOBS,
    DEFAULT_TIMEOUT,
    INFO_SUBJECTS,
    ROLE_PREFIXES,
)

SUBJECTS = CAPS

# command name -> (role, capability)
SENSOR_COMMANDS = {
    "{}{}".format(prefix, subject): (role, subject)
    for prefix, role in ROLE_PREFIXES.items()
    for subject in SUBJECTS
    if (role, subject) != ("nvswitch", "fan")
}

# ``nodeinlet`` checks the compute-tray air intake only: NVSwitch trays and power
# shelves have no chassis inlet sensor, so this subject is not fanned out over
# the other role prefixes.
SENSOR_COMMANDS["nodeinlet"] = ("compute", "inlet")

# command name -> (role, subject) for the info subjects (power/eventlog/...).
INFO_COMMANDS = {
    "{}{}".format(prefix, subject): (role, subject)
    for subject, roles in INFO_SUBJECTS.items()
    for prefix, role in ROLE_PREFIXES.items()
    if role in roles
}

COMMAND_ALIASES = {"list": "nodelist", "ping": "nodeping"}

# programs (argv[0]) that dispatch straight to a command
NODE_PROGRAMS = (
    tuple(SENSOR_COMMANDS)
    + tuple(INFO_COMMANDS)
    + ("nodelist", "nodeping", "nodeconsole", "nodemon", "nodealive")
)


_COMMAND_HELP = {
    "fan": """Read fan speeds or set an OEM fan mode.
  status   Show speeds (default).
  check    Show each fan's speed and health.
  auto     Restore BMC fan control (Supermicro: Optimal or Standard).
  max      Select the highest supported fan mode.
  low      Netweb: fixed 50% PWM on all fan channels.
  opt      Netweb: fixed 80% PWM on all fan channels.""",
    "psu": "Read each PSU's health and available power readings. Use --details for identification and input/output watts.",
    "temp": "Read temperatures by sensor ID and name, with health and the reading in Celsius.",
    "inlet": "Show one intake temperature per node. Use check for threshold status.",
    "power": """Read or control power.
  status   Show power state (default).
  on/off   Power on or off. Off can be forced.
  cycle    Power off and back on.
  reset    Restart without a full power cycle, where supported.
  reseat   Request an OEM-specific virtual reseat, where supported.""",
    "eventlog": """Read or clear hardware event logs.
  status   Read the default logs.
{bmc}{hmc}  clear    Erase the hardware event log entries, where supported.""",
    "inventory": "List installed firmware versions. Select --details for hardware identity or --tasks for Redfish task status.",
    "boot": """Set the next boot device. Reboot separately.
  The generic path tries IPMI first, then Redfish Once when supported.
  pxe      Boot from the network (default if ACTION is omitted).
  disk     Boot from local storage.
  bios     Enter firmware setup.
  cdrom    Boot from an optical or virtual-media device, where supported.""",
    "bootorder": "Read the current boot device order.",
    "netboot": """Find PXE ports and set persistent network boot priority.
  list/status  List matching PXE ports (default, read-only).
  prefer       Move matching PXE entries ahead of other entries.
               Defaults to IPv4. Use --family all for both families.
               Use --dry-run to see the complete proposed boot order.
  pxe          Legacy OEM action without a port selector.

Examples
  nodenetboot 'HGX[600-602]' list
  nodenetboot HGX600 status
  nodenetboot 'HGX[600-602]' prefer --type cx7 --dry-run
  nodenetboot 'HGX[600-602]' prefer --type bf3 --slot 3 --port 1 --dry-run
  nodenetboot HGX601 prefer --uefi-path 'Acpi(0x168E09,0x133)/Pci(0x2,0x0)' --port 2 --dry-run

Run list first to see available types and positions. An unknown value is shown
as -. Use a UEFI path from the list when the slot or PCI address is unavailable.
Prefer moves all matching entries ahead in their current order. Remove
--dry-run to write the persistent priority, then reboot separately.""",
    "macs": "List Ethernet interface MAC addresses exposed by the BMC.",
    "bios": """Read or change BIOS settings.
  status   Read active settings (default).
  pending  Read staged settings, where supported.
  base     Write the nodetools OEM preset with SNC and CPU virtualization
           disabled. Supermicro SYS-822GS-NB3RT verifies pending values
           then power-cycles. Aivres may also restart immediately.
  bf3      Stage a BlueField-3 BIOS preset (EGM and PCIe option-ROM settings),
           where supported.""",
    "vm": """Inspect or control BMC virtual media.
  status            Show the selected or mounted image (default).
  mount/insert URL  Attach an ISO. Supported URL schemes depend on the OEM.
                    Use an NFS or Samba server IP or hostname reachable from
                    the BMC. The BMC connects to the server to read the ISO.
  eject/umount      Detach the image.
  stop URL          OEM-specific stop. On Lenovo GB300, eject first, then
                    use stop to disable Remote Media.
  enable/media/lic  OEM-specific enablement, media information or license view.

Examples
  nodevm HGX390 status

Lenovo GB300 CIFS example
  node=gb300-ctr-01
  image=smb://files.example.com/isos/example.iso
  ./nodevm "$node" mount "$image" --vm-user "$SMB_USER" --vm-password "$SMB_PASSWORD"
  ./nodevm "$node" status
  ./nodevm "$node" eject "$image"
  ./nodevm "$node" stop "$image"
  ./nodevm "$node" status

Set SMB_USER and SMB_PASSWORD to the share credentials first. Run
lsblk -o NAME,TYPE,LABEL,MODEL on the compute host before ejecting.
See --help-full for timeout options.""",
}


class _CommandHelp(argparse.RawDescriptionHelpFormatter):
    _shared = {
        "help", "inventory", "group", "all", "jobs", "timeout", "node_timeout",
        "action_opt", "user", "password", "ipmi_env", "no_cache",
        "errors_only", "cipher", "reconnect", "reconnect_attempts", "reconnect_delay",
    }

    def add_usage(self, usage, actions, groups, prefix=None):
        actions = [action for action in actions if action.dest not in self._shared]
        super().add_usage(usage, actions, groups, prefix)

    def add_argument(self, action):
        if action.dest in self._shared or action.dest in ("noderange", "selector", "action"):
            return
        super().add_argument(action)


class _FullHelpAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        formatter = parser.formatter_class
        try:
            parser.formatter_class = argparse.RawDescriptionHelpFormatter
            parser.print_help()
        finally:
            parser.formatter_class = formatter
        parser.exit()


class _CommandParser(argparse.ArgumentParser):
    def __init__(self, **kwargs):
        kwargs.setdefault("formatter_class", _CommandHelp)
        super().__init__(**kwargs)
        _add_common_flags(self)


def _add_common_flags(parser):
    parser.add_argument("--help-full", action=_FullHelpAction, nargs=0,
                        help="show all options, including common options")
    parser.add_argument("-x", "--debug", action="store_true", default=argparse.SUPPRESS,
                        help="show diagnostic information on stderr")


def normalize_command(name):
    return COMMAND_ALIASES.get(name, name)


def _add_inventory_opt(parser):
    parser.add_argument("-i", "--inventory", default=None, help="inventory file")


def _add_selection(parser):
    parser.add_argument("selector", nargs="*", metavar="HOSTS", help="hostlist expression / group")
    _add_inventory_opt(parser)
    parser.add_argument("--group", action="append", default=[], help="select a group")
    parser.add_argument("--all", action="store_true", help="select every host")


def _add_read_opts(parser):
    _add_inventory_opt(parser)
    parser.add_argument("--group", action="append", default=[], help="select a group")
    parser.add_argument("--all", action="store_true", help="select every host")
    parser.add_argument("--jobs", type=int, default=None, help="parallel workers")
    parser.add_argument("--timeout", type=float, default=None, help="per-request timeout")
    parser.add_argument(
        "--node-timeout", type=float, default=None,
        help="total request budget per node, including identification (default 30 seconds)",
    )


def _add_sensor_opts(parser):
    parser.add_argument(
        "noderange",
        metavar="HOSTS",
        nargs="?",
        help="nodelist expression (defaults to every host of the role)",
    )
    parser.add_argument(
        "action",
        metavar="ACTION",
        nargs="?",
        default=None,
    )
    _add_read_opts(parser)
    parser.add_argument("--summary", action="store_true", default=None, help="per-node summary")
    parser.add_argument("--errors-only", action="store_true", help="only non-OK lines")
    parser.add_argument(
        "-a",
        "--action",
        dest="action_opt",
        default=None,
        help="control action (alias for the positional action)",
    )
    parser.add_argument("--user", default=None, help="override BMC username")
    parser.add_argument("--password", default=None, help="override BMC password")
    parser.add_argument(
        "--ipmi-env",
        action="store_true",
        help="pass the IPMI password via IPMI_PASSWORD (ipmitool -E)",
    )
    parser.add_argument("--no-cache", action="store_true", help="skip caches")


def _add_info_opts(parser, subject, role):
    parser.add_argument(
        "noderange",
        metavar="HOSTS",
        nargs="?",
        help="nodelist expression (defaults to every host of the role)",
    )
    parser.add_argument("action", nargs="?", metavar="ACTION")
    if subject == "netboot":
        for flag, help_text in (
            ("type", "adapter family such as bf3, cx6, cx7, cx8"),
            ("vendor", "adapter vendor"),
            ("slot", "reported physical slot"),
            ("port", "reported port number"),
            ("pci", "reported PCI domain:bus:device.function"),
            ("link", "reported link technology"),
            ("mac", "adapter MAC address"),
            ("uefi-path", "UEFI device path or component prefix"),
        ):
            parser.add_argument("--" + flag, help=help_text)
        parser.add_argument("--family", choices=("ipv4", "ipv6", "all"), help="PXE address family")
        parser.add_argument("--dry-run", action="store_true", help="preview persistent changes")
    if subject == "vm":
        parser.add_argument(
            "image",
            nargs="?",
            default=None,
            help="ISO location, e.g. smb://host/share/x.iso, nfs://host/dir/x.iso",
        )
        parser.add_argument("--vm-user", default=None, help="virtual-media username")
        parser.add_argument(
            "--vm-password", default=None, help="virtual-media password"
        )
    if subject == "bios":
        parser.add_argument(
            "--set", dest="bios_updates", action="append", metavar="KEY=VALUE",
            help="stage a BIOS attribute on supported OEMs without rebooting (repeatable)",
        )
    if subject == "inventory":
        view = parser.add_mutually_exclusive_group()
        view.add_argument(
            "--details", action="store_true",
            help="show Redfish hardware model, manufacturer, and serial number",
        )
        if role == "compute":
            view.add_argument("--gpu", action="store_true", help="show GPU serial numbers")
        view.add_argument("--tasks", action="store_true", help="show Redfish task states")
    _add_inventory_opt(parser)
    parser.add_argument("--group", action="append", default=[], help="select a group")
    parser.add_argument("--all", action="store_true", help="select every host")
    parser.add_argument("--errors-only", action="store_true", help="only non-OK lines")
    parser.add_argument("--jobs", type=int, default=None, help="parallel workers")
    parser.add_argument("--timeout", type=float, default=None, help="per-request timeout")
    parser.add_argument(
        "--node-timeout", type=float, default=None,
        help="total request budget per node, including identification (default 30 seconds)",
    )
    parser.add_argument(
        "-a",
        "--action",
        dest="action_opt",
        default=None,
        help="control action (alias for the positional action)",
    )
    parser.add_argument("--user", default=None, help="override BMC username")
    parser.add_argument("--password", default=None, help="override BMC password")
    parser.add_argument(
        "--ipmi-env",
        action="store_true",
        help="pass the IPMI password via IPMI_PASSWORD (ipmitool -E)",
    )
    parser.add_argument("--no-cache", action="store_true", help="skip caches")


def _add_output_options(parser, formats=("text", "json"), default="text"):
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--format", choices=formats, default=default, help=f"output format (default {default})")
    output.add_argument("--json", dest="format", action="store_const", const="json", help="one JSON record per node")


def add_examples(parser, *commands):
    parser.epilog = ((parser.epilog + "\n\n") if parser.epilog else "") + "Examples\n" + "\n".join(
        "  " + command for command in commands
    )


def build_parser():
    parser = argparse.ArgumentParser(
        prog="nodetools",
        description="Inspect and manage HGX and NVL nodes over Redfish and IPMI.",
        epilog=("Run COMMAND --help for actions. Also available: nodefw, nodetask, nodebmc, nodefru. "
                "Common inventory, authentication, and timeout options are in README.md."),
    )
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    add_examples(parser, "nodetools nodelist", "nodetools nodepsu 'HGX[390-394]'")
    _add_common_flags(parser)
    parser.add_argument("--version", action="store_true", help="print version")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", parser_class=_CommandParser)

    redfish = sub.add_parser("redfish", help="export and replay Redfish snapshots")
    redfish_sub = redfish.add_subparsers(dest="snapshot_command", required=True)
    dump = redfish_sub.add_parser(
        "dump", help="capture one BMC with parallel endpoint reads",
        description="Capture a BMC snapshot. Progress is shown on interactive terminals. "
                    "Use -x to show progress when redirecting output.",
    )
    dump.add_argument("host", help="one host from inventory")
    dump.add_argument("-i", "--inventory", help="inventory file")
    dump.add_argument("-o", "--output", required=True, help="new snapshot file")
    dump.add_argument("--workers", type=int, default=4, help="concurrent requests to this BMC (default 4)")
    dump.add_argument("--seconds", type=float, default=60, help="capture budget in seconds (default 60)")
    dump.add_argument("--max-requests", type=int, default=600, help="request limit (default 600)")
    dump.add_argument("--timeout", type=float, help="override the Redfish response timeout")
    dump.add_argument("--path", action="append", default=[], help="prioritize an additional Redfish resource")
    replay = redfish_sub.add_parser("replay", help="run a read-only command from a snapshot")
    replay.add_argument("file", help="snapshot JSON file")
    replay.add_argument("snapshot_tool", metavar="COMMAND", help="a Redfish read command such as nodefan")
    replay.add_argument("action", nargs="?", choices=("status", "check", "pending"))
    replay.add_argument("--details", action="store_true", help="show detailed PSU fields")

    inv = sub.add_parser("inventory", help="inventory management")
    inv_sub = inv.add_subparsers(dest="inventory_command")
    init = inv_sub.add_parser("init", help="generate inventory from hosts.list")
    init.add_argument("--from", dest="source", default="hosts.list")
    init.add_argument("-o", "--output", default=DEFAULT_INVENTORY)
    merge = inv_sub.add_parser(
        "import", help="preview or add missing hosts from hosts.list",
        description="Add only missing hosts to an existing inventory. Previews by default.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples
  nodetools inventory import hosts.list
  nodetools inventory import /path/to/hosts.list --apply
  nodetools inventory import hosts.list -i /path/to/inventory.ini --apply""",
    )
    source = merge.add_mutually_exclusive_group()
    source.add_argument("source", nargs="?", metavar="HOSTS_LIST", help="legacy host list (default: hosts.list)")
    source.add_argument("--from", dest="source_option", metavar="HOSTS_LIST", help="alternative way to specify the host list")
    merge.set_defaults(_usage_parser=merge)
    merge.add_argument("-i", "--inventory", help="existing inventory")
    merge.add_argument("--apply", action="store_true", help="write additions after validation")
    show = inv_sub.add_parser("show", help="show parsed inventory")
    show.add_argument("-i", "--inventory", default=None)

    add_examples(redfish, "nodetools redfish dump HGX390 -o hgx390.json")
    add_examples(dump, "nodetools redfish dump HGX390 -o hgx390.json")
    add_examples(replay, "nodetools redfish replay hgx390.json nodepsu --details")
    add_examples(inv, "nodetools inventory show", "nodetools inventory import hosts.list")
    add_examples(init, "nodetools inventory init --from hosts.list -o inventory.ini")
    add_examples(show, "nodetools inventory show", "nodetools inventory show -i lab.ini")

    lst = sub.add_parser("nodelist", aliases=["list"], help="list hosts",
                         description="List selected inventory hosts as compact ranges. With no HOSTS, list all hosts.")
    _add_selection(lst)
    lst.add_argument(
        "--long", action="store_true", help="one line per host (role and BMC address)"
    )

    pg = sub.add_parser("nodeping", aliases=["ping"], help="ping BMC addresses",
                        description="Check BMC reachability over ICMP. Output reachable and unreachable host ranges.")
    _add_selection(pg)
    pg.add_argument("--jobs", type=int, default=None, help="parallel workers")
    pg.add_argument(
        "--ping-timeout", type=float, default=1.0, help="seconds to wait per reply"
    )
    pg.add_argument("--long", action="store_true", help="one line per host")
    _add_output_options(pg)

    alive = sub.add_parser(
        "nodealive", help="diagnose whether compute nodes are reachable",
        description="Check BMC ping, power, host ping, and SSH service. Add --slurm for scheduler state.",
    )
    alive.add_argument("noderange", nargs="?", metavar="HOSTS", help="hostlist expression")
    _add_read_opts(alive)
    alive.add_argument("--errors-only", action="store_true", help="show only degraded nodes")
    alive.add_argument("--slurm", action="store_true", help="include Slurm node state")
    alive.add_argument("--probe-timeout", type=float, default=2, help="seconds per ICMP or SSH probe (default 2)")
    alive.add_argument("--ssh-port", type=int, default=22, help="SSH port (default 22)")
    _add_output_options(alive)
    add_examples(alive, "nodealive HGX390", "nodealive 'HGX[390-394]' --slurm --json")

    for name, (role, subject) in SENSOR_COMMANDS.items():
        description = _COMMAND_HELP[subject]
        if role == "nvswitch":
            description = {
                "psu": "Read NVOS PSU telemetry, or DC input power from the tray HSC.",
                "temp": "Read NVOS platform temperatures in Celsius over SSH.",
            }[subject]
        elif subject == "fan" and role != "compute":
            description = """Read fan speeds and health for the selected devices.
  status   Read fan sensors (default).
  check    Show per-fan health.
  auto/max Request automatic or maximum speed, where supported."""
        sensor = sub.add_parser(name, help="{} {}".format(role, subject), description=description)
        _add_sensor_opts(sensor)
        _add_output_options(sensor)
        hosts = {"compute": "'HGX[390-394]'", "nvswitch": "nvsw01", "powershelf": "shelf01"}[role]
        examples = [f"{name} {hosts}"]
        if subject == "fan":
            examples.append(f"{name} {hosts} check")
            if role == "compute":
                examples.append("nodefan HGX390 max --dry-run")
        elif subject == "psu":
            examples.append(f"{name} {hosts} --details")
        elif subject == "temp":
            examples.append(f"{name} {hosts} --summary")
        if name == "nodepsu":
            examples.append("nodepsu HGX390 --json")
        add_examples(sensor, *examples)
        if subject == "fan":
            sensor.add_argument("--fan-profiles", metavar="FILE", help="use exact-model IPMI fan profiles from an INI file")
            sensor.add_argument("--dry-run", action="store_true", help="show the fan control plan without writing")
        if subject == "psu":
            sensor.add_argument(
                "--details", action="store_true",
                help="show PSU name, manufacturer, model, firmware, health and input/output watts",
            )
        if subject == "inlet":
            sensor.add_argument(
                "--target",
                type=float,
                default=None,
                help="expected inlet temperature in Celsius (default 25)",
            )
            sensor.add_argument(
                "--tolerance",
                type=float,
                default=None,
                help="allowed +/- band around the target (default 5)",
            )

    for name, (role, subject) in INFO_COMMANDS.items():
        description = _COMMAND_HELP[subject]
        if subject == "power" and role == "nvswitch":
            description = """Read NVSwitch power state or control power on supported adapters.
  status   Show power state (default).
  on/off   Turn tray power on or off, where supported.
  cycle    Power the tray off and back on, where supported."""
        if subject == "eventlog":
            hmc = "  hmc      Read HMC logs, where supported.\n" if role == "compute" else ""
            bmc = "  bmc      Read BMC logs, where supported.\n" if role != "powershelf" else ""
            description = description.format(bmc=bmc, hmc=hmc)
        info = sub.add_parser(name, help="{} {}".format(role, subject), description=description)
        _add_info_opts(info, subject, role)
        if name == "nodepower":
            _add_output_options(info)
        hosts = {"compute": "'HGX[390-394]'", "nvswitch": "nvsw01", "powershelf": "shelf01"}[role]
        if subject == "inventory":
            examples = [f"{name} {hosts}", f"{name} {hosts} --details"]
        elif subject == "bios":
            examples = [f"{name} {hosts} status", "nodebios HGX390 pending"]
        elif subject == "boot":
            examples = ["nodeboot HGX390 disk", "nodeboot HGX390 pxe"]
        elif subject in ("power", "eventlog"):
            examples = [f"{name} {hosts} status"]
        else:
            examples = [f"{name} {hosts}"]
        if subject not in ("netboot", "vm"):
            add_examples(info, *examples)

    mon = sub.add_parser("nodemon", help="sample node temperature and power",
                         description="Read inlet temperature, whole-node power, and per-PSU input watts.\n"
                                     "Each completed node is printed immediately. The default is one CSV sample.")
    mon.add_argument("noderange", nargs="?", metavar="HOSTS", help="hostlist expression (defaults to every compute host)")
    _add_read_opts(mon)
    mon.add_argument("--count", type=int, default=1, help="number of samples (default 1)")
    mon.add_argument("--interval", type=float, default=5, help="seconds between rounds (default 5)")
    _add_output_options(mon, formats=("csv", "json", "text"), default="csv")
    mon.add_argument("--psu-voltage", type=float, help="fallback PSU input voltage for watt estimates")
    mon.add_argument("--psu-power-factor", type=float, help="fallback power factor for single-phase PSU input")
    mon.add_argument("--user", default=None, help="override BMC username")
    mon.add_argument("--password", default=None, help="override BMC password")
    mon.add_argument("--ipmi-env", action="store_true", help="use IPMI_PASSWORD for IPMI")
    mon.add_argument("--no-cache", action="store_true", help="skip cached discovery profiles")

    cs = sub.add_parser(
        "nodeconsole",
        help="access the compute-node serial console",
        description="Manage IPMI Serial over LAN sessions.\n"
                    "  open      Attach to one node (default).\n"
                    "  close     Deactivate SOL sessions on the selected nodes.",
    )
    cs.add_argument(
        "noderange",
        metavar="HOSTS",
        nargs="?",
        help="nodelist expression (defaults to every compute host)",
    )
    cs.add_argument(
        "action",
        metavar="ACTION",
        nargs="?",
        default="open",
        choices=["open", "close"],
        help="open (attach one node, default) | close (deactivate many)",
    )
    _add_inventory_opt(cs)
    cs.add_argument("--group", action="append", default=[], help="select a group")
    cs.add_argument("--all", action="store_true", help="select every host")
    cs.add_argument("--jobs", type=int, default=None, help="parallel workers")
    cs.add_argument("--timeout", type=float, default=None, help="per-request timeout")
    cs.add_argument("--user", default=None, help="override BMC username")
    cs.add_argument("--password", default=None, help="override BMC password")
    cs.add_argument(
        "--ipmi-env",
        action="store_true",
        help="pass the IPMI password via IPMI_PASSWORD (ipmitool -E)",
    )
    cs.add_argument("--no-cache", action="store_true", help="skip caches")
    cs.add_argument(
        "--console",
        "--interactive",
        dest="console",
        action="store_true",
        help="attached SOL session with logging/reconnect (one node)",
    )
    cs.add_argument("--cipher", type=int, default=17, help="ipmitool -C cipher suite")
    cs.add_argument("--log-file", default=None, help="console log path (JSON Lines)")
    cs.add_argument("--log-dir", default="logs", help="auto-log directory")
    cs.add_argument("--no-log", action="store_true", help="do not write a console log")
    cs.add_argument(
        "--reconnect",
        dest="reconnect",
        action="store_true",
        default=None,
        help="reconnect after the SOL session drops (default for --console)",
    )
    cs.add_argument(
        "--no-reconnect",
        dest="reconnect",
        action="store_false",
        help="do not reconnect after a drop",
    )
    cs.add_argument(
        "--reconnect-attempts", type=int, default=3, help="reconnect tries (default 3)"
    )
    cs.add_argument(
        "--reconnect-delay",
        type=float,
        default=5.0,
        help="seconds between reconnect tries (default 5)",
    )

    bak = sub.add_parser(
        "bak", help="group and compare host-prefixed output",
        description="Group HOST:OUTPUT lines, show differences, and summarize recognized fields.",
    )
    bak.add_argument("file", nargs="?", default="-", metavar="FILE",
                     help="input file (default: read stdin)")
    mode = bak.add_mutually_exclusive_group()
    mode.add_argument("-c", "--collect", dest="bak_mode", action="store_const", const="collect",
                      help="merge hosts with identical output")
    mode.add_argument("-C", "--collect-similar", dest="bak_mode", action="store_const", const="similar",
                      help="group identical output and show differences from the largest group")
    bak.add_argument("--key-value", action="store_true",
                     help="force field comparison for HOST:KEY=VALUE or HOST:KEY VALUE")

    sc = sub.add_parser(
        "scan", help="probe and cache a Redfish resource map",
        description="Discover Redfish resource paths and cache them per machine type. Re-run to refresh."
    )
    _add_selection(sc)
    sc.add_argument("--jobs", type=int, default=None, help="parallel workers")
    sc.add_argument("--timeout", type=float, default=None, help="per-request timeout")
    sc.add_argument("--user", default=None, help="override BMC username")
    sc.add_argument("--password", default=None, help="override BMC password")
    sc.add_argument(
        "--clear", action="store_true", help="delete cached maps instead of writing"
    )
    add_examples(lst, "nodelist", "nodelist 'HGX[390-394]' --long")
    add_examples(pg, "nodeping 'HGX[390-394]'", "nodeping HGX390 --long", "nodeping HGX390 --json")
    add_examples(mon, "nodemon 'HGX[390-394]'", "nodemon HGX390 --count 3 --interval 5")
    add_examples(cs, "nodeconsole HGX390 open", "nodeconsole HGX390 open --console")
    add_examples(bak, "nodepower 'HGX[390-394]' status | nodetools bak -c")
    add_examples(sc, "nodetools scan HGX390 --timeout 5")
    return parser


def parse_args(argv=None):
    parser = build_parser()
    args, extras = parser.parse_known_args(argv)
    usage_parser = vars(args).pop("_usage_parser", None)
    if extras:
        if usage_parser is not None:
            usage_parser.error("unexpected arguments: {}. Try: nodetools inventory import HOSTS_LIST "
                               "(preview), or add --apply to write.".format(" ".join(extras)))
        parser.error("unrecognized arguments: " + " ".join(extras))
    return args


__all__ = [
    "build_parser",
    "parse_args",
    "normalize_command",
    "SENSOR_COMMANDS",
    "INFO_COMMANDS",
    "NODE_PROGRAMS",
    "DEFAULT_INVENTORY",
    "DEFAULT_JOBS",
    "DEFAULT_TIMEOUT",
]
