# Developer guide

English | [中文](DEVELOPMENT.md)

This document explains nodetools' design, module responsibilities, how to extend
it, and how to verify changes, for future development.

## 1. Background and goals

Troubleshooting GB200/GB300 used to rely on a pile of shell scripts forked per
OEM (`nodefan.*`, `nodepsu.*`, `nodemon.*`, `pspower.*` …); the same task was a
different file for every ODM, which is expensive to maintain. nodetools folds
them into one **role-prefixed** command family, keeping the legacy naming:

- Command name = prefix + subject: **prefix names the role**, **subject names
  the object**;
  - `node*` → compute tray, `nvsw*` → NVSwitch, `ps*` → power shelf;
  - subjects: `fan` / `psu` / `temp` (sensors) and `power` / `eventlog` /
    `inventory` (info);
  - examples: `nodepsu`, `pspsu`, `nodepower`, `nvswinventory`.
    GBXX uses all three prefixes; BXX only needs `node*`.
- Read paths form a **fallback ladder**, so one implementation covers every ODM;
- Devices, credentials, roles and ODM all come from the inventory plus runtime
  probing, never hardcoded in scripts.

Three subject groups are implemented: **fan / psu / temp** (sensors, one set per
role), **power / eventlog / inventory** (info, one set per role) and
**boot / bootorder / netboot / macs / bios** (boot, compute only). The
structure leaves room for more subjects (BMC info, button/reset, IB/SPX
switches, …).

> The tool's own output is always English; only the docs are bilingual.

### Where the interface knowledge comes from

The Redfish endpoints, control commands and sensor names are not guesses; they
come from reading the existing scripts end to end:

- top-level `nodefan/nodepsu/nodesensors/nodemon`, `func.sh` (GB200 constants),
  `passwd.sh`;
- powershelf / NVSwitch: `psmon`, `pspsu`, `pspower`, `nvswmon`;
- per-OEM templates: `oem/{ami,byd,dell,dgx,gb200,gigabyte,lenovo,smc}/`;
- GB300 variants (Wistron/Gigabyte/SMC/Inventec sensor names and per-sensor
  routing): `nodefan.Wistron`, `nodemon.Wistron`, `nodemon.v2.Wistron`,
  `nodemon.ipmi.Wistron`, `pspower.Wistron`.

> Before changing an interface, go back and read the matching script to confirm
> the fields; do not guess from a field name.

## Command dispatch

A command name is **prefix (role) + subject**. Sensor commands come from
`arguments.SENSOR_COMMANDS` (`config.ROLE_PREFIXES` × `SUBJECTS`); info commands
come from `arguments.INFO_COMMANDS` (the subject→supported-roles table in
`config.INFO_SUBJECTS` × `ROLE_PREFIXES`), so a subject that only exists for
some roles is expressible. The two forms are equivalent:

```sh
./nodefan cp[01-18]           # argv[0] is the command; main() dispatches by basename
nodetools nodefan cp[01-18]   # or as a subcommand
```

`nodelist`/`nodeping` are role-agnostic and cover the whole inventory
(`list`/`ping` are aliases). **Only `nodelist` needs no argument**: a bare call
lists everything and exits 0. Every other command (including `nodeping`)
requires arguments; a bare call (`len(argv)==1`) makes `main()` print that
command's full help to stdout and exit **0** — this is help, not an error (no
stderr output, no nonzero code), and it does not fall back to the whole group.
Role commands resolve via `cli._role_hosts`: when a noderange is named,
resolution happens first and is then filtered by role (cross-role hosts are
skipped with a notice); use `--all`/`--group` to select the whole role group.

## 2. Architecture overview

```
nodetools (launcher)
  └─ nodetools_runtime.cli
       ├─ arguments.py   parse args
       ├─ inventory.py   parse/generate inventory
       ├─ selection.py   selectors → host list
       ├─ reach.py       BMC ICMP reachability (list/ping)
       ├─ scan.py        Redfish endpoint scan + cache (the only cache writer)
       ├─ console.py      nodeconsole: IPMI SOL (legacy port + pty session)
       ├─ runtime.py     parallel engine: build Device, run caps/control (read-only)
       │    ├─ model.py       role/ODM lookup (odm_for) + classify
       │    ├─ caps/*.py      sensor collectors (fallback ladders)
       │    ├─ subjects/*.py  info subjects (power/eventlog/inventory, line output)
       │    ├─ oem/{hgx,nvl}/*.py       ODM differences (extra paths + control + info overrides)
       │    └─ transport/*.py Redfish / IPMI / racadm
       └─ report.py      output formatting (sensor CSV/summary + info lines)
```

Data flow of one invocation:

```
argv
 → Inventory.from_file(inventory.ini)         # groups + vars + defaults
 → selection.select(...)                       # name/group/pattern → Host[]
 → Runner.run(hosts) / InfoRunner.run(hosts)  (ThreadPoolExecutor)
     per host: resolve role creds → build Redfish+Ipmi → Device (+ scan profile, None if uncached)
           ├─ sensor --action: read ODM from profile → oem.get(odm).fan_control
           ├─ sensor otherwise: caps.collector_for(cap)(device) → readings[]
           └─ info: compute reads ODM from profile → subjects.handler_for(subject)(device, action)
 → report.render(results)                      # sensor  lines/summary; info  verbatim lines
```

## 3. Module responsibilities

| File | Responsibility | Notes |
| --- | --- | --- |
| `config.py` | constants: subject names, default jobs/timeout, cache dirs, role↔group, prefix↔role, default chassis | tuning entry point |
| `errors.py` | exception hierarchy | `CapUnsupported` means "ODM does not support this action" |
| `hostlist.py` | hostname range expansion | see §5, unit-tested |
| `inventory.py` | inventory parsing + `hosts.list` generator | syntax in §5 |
| `selection.py` | selector parsing (group/name/pattern) | selects only what you name |
| `reach.py` | BMC ICMP reachability | used by `list`/`ping`; no credentials |
| `scan.py` | probe collection endpoints, record per-chassis capability map + device identity/ODM | **the only cache writer**; others read via `rung_chassis`/`odm_for` |
| `model.py` | `Device`, `classify`, `odm_for` (reads the scan profile's ODM) | `unknown` when uncached; commands never probe |
| `ladder.py` | "try in order, return the first non-empty" | core control flow of collectors |
| `transport/redfish.py` | Redfish `get/get_raw/post/patch` | `verify=False`, 3s connect timeout |
| `transport/ipmi.py` | ipmitool wrapper, `sdr` / `raw` | two password modes, see §5 |
| `transport/racadm.py` | Dell racadm | `available()` returns False when missing |
| `caps/common.py` | Reading construction, health/speed/num, collection/sensor enumeration, `rung_chassis` | shared helpers |
| `caps/{fan,psu,temp}.py` | fallback ladders for the three sensor caps | see §4 |
| `subjects/common.py` | info helpers: `resource`/`entries`/`log_lines`/`column_t`/`jq`/`post`/`patch` | shared line-output helpers |
| `subjects/{power,eventlog,inventory}.py` | handlers for the three info subjects; nvsw dispatches to the ODM `nvswitch_power`, ps uses fixed NVIDIA paths, compute dispatches to ODM | see §4b |
| `subjects/{boot,bootorder,netboot,macs,bios}.py` | handlers for the five boot subjects (compute only), dispatching to `device.odm.<subject>` | see §4c |
| `console.py` | `nodeconsole`: `_argv`/`open_one`/`close_all`/`attach` (IPMI SOL: legacy port + pty session) | see §4d |
| `oem/base.py` | `Oem` base (GBXX reference) + `power`/`nvswitch_power`/`eventlog`/`firmware_inventory`/`boot`/`bootorder`/`macs`/`bios`/`netboot`/`vm` + `_ipmi_raw` | vendors fall back here when an endpoint is absent |
| `oem/{hgx,nvl}/<odm>.py` | per-ODM control actions / extra chassis / info overrides | reading mostly uses the generic ladder |
| `runtime.py` | `build_device` (+ scan profile), parallel `Runner`/`InfoRunner`, `Scanner`, error isolation | per-host try/except |
| `report.py` | sensor lines/summary (field order per subject) + info lines (`format_lines`) | keeps `grep -v ,OK` working |

## 4. Collection ladders (fallback order)

Each capability "tries in turn and returns the first backend that has data",
implemented with `ladder.first`. So even if the ODM is wrong, any working
generic path still yields data.

- **fan**: `ThermalSubsystem/Fans` (per member `.SpeedPercent.SpeedRPM|Reading`)
  → `Thermal.Fans[].Reading` → `Sensors` (name contains `fan`) → IPMI
  `sdr type Fan`.
- **psu**: `PowerSubsystem/PowerSupplies` (values `PowerMetrics.InputWatts`
  etc.; powershelf state from `Oem.deltaenergysystems.Power` → `On/Off`)
  → (powershelf) Delta `Oem/deltaenergysystems/AllSensors`
  → `Sensors` (name contains `psu/hsc/pwr`) → IPMI `sdr type Power Supply`.
- **temp**: `Thermal.Temperatures` → `ThermalSubsystem/ThermalMetrics`
  → `TelemetryService/MetricReports` (name filtered by temp keywords)
  → `Sensors` (name/`ReadingType` contains temp) → IPMI `sdr type Temperature`.

Every ladder walks `chassis_list(device, DEFAULT_CHASSIS)`, i.e. "detected
chassis + `config.DEFAULT_CHASSIS`", so `Chassis_0` / `1` / `System.Embedded.1`
/ `DGX` / `PDB_0` / `PowerShelf_0` all match. When a scan cache exists it walks
`rung_chassis(device, key)` instead: only the chassis whose key is non-empty or
present in the cache, short-circuiting when the cache says the endpoint is
absent (see §5 "Scan cache").

**Unified Reading**: `{id, name, value, health}`. health is
`.Status.Health or .Status.State or "Unknown"`; powershelf is special-cased to
`On/Off`.

## 4b. Info subjects (power / eventlog / inventory)

Info subjects are **line-oriented**: a handler returns a list of **complete
output lines** (already prefixed with `<node>: `; eventlog tags BMC/HMC with
`[BMC]`/`[HMC]`), and `report.render` detects a `lines` key and prints them
verbatim instead of the CSV/summary model (see `report.format_lines`). The goal
is to match the legacy scripts verbatim so existing pipes like `grep -v ,OK` and
`awk -F:` keep working.

Dispatch rules (`subjects/{power,eventlog,inventory}.py`):

- `runtime.InfoRunner` selects an adapter by platform, OEM, device role, and generation.
- NVSwitches and power shelves dispatch independently. The base adapter keeps
  the legacy NVIDIA paths. Other devices use Redfish discovery, with
  `system_id`, `manager_id`, or `chassis_id` to scope a shared BMC.
- On Compal NVSwitches, discovery can read power status from `Systems/Self`.
  The legacy `nvswpower.cpl` mapping `reseat=FullPowerCycle` is an NVL interface
  reference. Qualify writes on the actual model before enabling them.
- The `Oem` base class is the **NVIDIA GBXX reference implementation** (legacy
  top-level scripts + `func.sh`):
  - `power`: IPMI `power <action>`, printing `N/A` on failure (matching the
    legacy `|| echo 'N/A'`); `reseat` uses the GBXX auxiliary power cycle
    (`nodereseat`/`func.sh`), while DGX overrides it to `Chassis/BMC`
    `AuxPowerCycleForce`, Aivres to IPMI `raw 0x3c 0x28`, and SMC/Lenovo
    implement their own;
  - `nvswitch_power`: the legacy NVIDIA `Systems/System_0` `ComputerSystem.Reset` supports `on/off/cycle`.
  - `eventlog`: reads
    `HGX_Baseboard_0/LogServices/EventLog/Entries` by default (`[HMC]`), `bmc`
    uses IPMI `sel list` (`[BMC]`), `clear` clears `Systems/system`, the BMC
    SEL/EventLog and the HGX EventLog;
  - `firmware_inventory`: `UpdateService/FirmwareInventory`, line
    `name:Id Version` (**no space** after the colon, matching legacy
    `query_firmware`).
- Each vendor overrides **only where its HGX x86 script differs** (e.g. Dell
  iDRAC Sel, Lenovo `PlatformLog`, SMC `Log1`, AMI/GIGABYTE `Managers/1/SEL`,
  DGX BIOS+HGX), and falls back to `super()` (the GBXX convention) when the
  endpoint is absent.

Before changing an info subject, **re-read the matching legacy script** to
confirm the endpoint, fields and separator (space / comma / tab / `column -t`
alignment); do not guess. `subjects/common.py`'s `column_t` approximates
`column -t`, and `jq`/`jq_out` approximate `jq` (mind the quoting difference
between `jq -r` and `jq`, e.g. the quoted `PowerState` in SMC/NVSwitch).

## 4c. Boot subjects (boot / bootorder / netboot / macs / bios / vm)

Boot subjects **only make sense for compute trays**: `config.INFO_SUBJECTS`
marks `boot/bootorder/netboot/macs/bios/vm` as `("compute",)`, so only `node*`
commands are generated and `nvsw*`/`ps*` produce none. Output rules match the
info subjects (line-oriented, already prefixed with `<node>: `).

- **boot** — `Oem.boot` falls back from IPMI `chassis bootdev` to Redfish `Once` if supported. The fallback uses a known OEM system or a sole Systems member. Inventory `system_id` selects Redfish directly. If both attempts fail, the report includes both causes.
- **bootorder** — `Oem.bootorder`: `BootOptions?$expand` for `Id`/`DisplayName`/
  `UefiDevicePath`, then match each entry of `Systems/<id>`'s `Boot/BootOrder`
  (first `grep -w` on the number with the `Boot` prefix stripped, then on the
  whole string). The base class keeps JSON quoting (matching `jq` without
  `-r`); ODMs set `bootorder_quoted=False` / `bootorder_sep=","` /
  `bootorder_expand` to get bare fields.
- **macs** — `Oem.macs`: pick the PXE entries out of the BootOptions
  `UefiDevicePath` and take MAC/bus using the legacy `sed`/`awk` field order
  (note: the legacy pipeline actually treats the **IPv4 field** as macA, so the
  "mac" in the output is only a real MAC when the `UefiDevicePath` lists MAC
  before IPv4); then add the `Managers/<bmc>/EthernetInterfaces` BMC MACs.
- **bios** — `Oem.bios`: `Systems/<id>/Bios` `Attributes` as `key=value` per
  key, grepping out vendor noise keys. `base`/`pending` are write paths only on
  Lenovo/SMC.
- **netboot** — `subjects.netboot` lists PXE entries and joins them to hardware
  discovered by `boot_nics` using valid MACs. `prefer` updates persistent
  priority after batch preflight. Supermicro uses `FixedBootOrder` and
  `UEFINetwork`. NVL uses the advertised settings resource or system
  `BootOrder`. Check for changed order before writing and verify it afterward.
  Legacy `pxe` uses the OEM action.
- **vm** — `Oem.vm(device, action, options)`: virtual media (**a write**). The
  base class is NVIDIA/AMI: it discovers the non-`HGX` `BMC_ID` and the first
  `CD*`/`USB*` interface, `enable` goes through `AMIVirtualMedia.EnableRMedia`,
  and `mount`/`eject` through `VirtualMedia.InsertMedia`/`EjectMedia`;
  Compal/Netweb/Aivres/SMC/Dell override their endpoints and action sets (Dell
  goes through `racadm remoteimage`). `options.image`/`vm_user`/`vm_password`
  come from `nodevm`'s positional `image` plus `--vm-user`/`--vm-password`
  (`arguments._add_info_opts` extends the `vm` subject).

> Known deviations from the legacy scripts:
> - SMC `nodenetboot` and `nodebios base` had **no** `<node>: ` prefix; the
>   framework prefixes every line;
> - Lenovo `nodebios base` pretty-prints with `json.dumps(indent=2)`,
>   approximating `jq`'s formatting;
> - `nodevm`'s AMI `EnableRMedia` carries an `If-None-Match: not_match` header in
>   the legacy script, which the current transport cannot send (custom headers
>   are unsupported); Aivres `media`/`insert`/`eject` raw responses use compact
>   JSON instead of the legacy script's verbatim curl body.

> `nodebootorder`/`nodemacs`/`nodebios`/`nodevm status` are read-only;
> `nodenetboot prefer`/`pxe`, `nodebios base` and `nodevm`'s
> `enable`/`mount`/`insert`/`eject`/`umount`/`stop` **modify** the BMC/BIOS.

## 4d. Console (nodeconsole)

`nodeconsole` is not part of `SENSOR_COMMANDS`/`INFO_COMMANDS`; it is a dedicated
subcommand in `arguments`, handled directly by `cli._cmd_console` (dispatched
before the info-command check). `console.py` depends only on `transport` and
`runtime` (never touching sensors/subjects) and has two paths:

- **legacy port** (default): `open` runs `sol deactivate` (cipher 17) then
  `sol activate usesolkeepalive` (cipher 17, `-N 5 -R 5`), **inheriting the
  terminal stdio** to take over; with several nodes only the first is used.
  `close` runs `sol deactivate` (no cipher, no `-N/-R`) in parallel via
  `ThreadPoolExecutor(jobs)`, merges stdout+stderr and prints `<node>: <line>`,
  skipping empty lines (matching the legacy `xargs -I {} printf`). `_argv` builds
  `ipmitool -I lanplus -H ... -U/-P` (`-E` + `IPMI_PASSWORD` with `--ipmi-env`).
- **`--console`/`--interactive`**: `attach` uses `pty.fork()`+`execvpe` to run an
  interactive SOL, records JSON Lines (`_Log`, default
  `logs/sol_<label>_<stamp>.jsonl`, 0600), and `_relay` does raw-terminal relay
  treating `Ctrl-]` (`\x1d`) as a local detach; after a drop it reconnects per
  `--reconnect-attempts`/`--reconnect-delay` (reconnect is on by default for
  `--console`). It requires exactly one node and a TTY on stdin/stdout, else rc 2.
  `_reap` sends the `~.` exit sequence and reaps the child.

> Deviation from the legacy script: the old `nodeconsole` exits 1 with no args;
> the framework convention prints usage and returns `rc 0` (as with other
> commands).

## 5. Key conventions

### Development principles (must follow)

- **`#` comments**: `inventory.ini` (and its template/generator) keeps `#`
  comments, consistent with the legacy `hosts.list`/INI habit. A line starting
  with `#` (or `;`), or anything after an inline `#`, is ignored; both parsing
  and generation must follow this, as must any future manifest/config file. Do
  not introduce `//`, `--` or another style.
- **Always parallel**: every "per-node" operation (fan/psu/temp/control/`ping`…)
  must be parallel across nodes, equivalent to the legacy `xargs -P` (default
  `jobs=18`); **never serial**. Within a single node, ladder fallback is ordered
  by nature, but nodes must not queue behind each other. Always go through
  `runtime.py`'s `ThreadPoolExecutor(max_workers=jobs)`.
- **hostlist round-trip**: support both SLURM-style **expand** (`expand`, fed to
  operations) and SLURM-style **compact** (`compact`, the default `list`
  output), with `expand(compact(x)) == x`.

### Shared framework and vendor adapters

- **The shared layer defines the compatibility contract.** Command dispatch,
  adapter base classes, shared transports, and data models form the framework.
  The contract covers existing arguments and defaults, adapter inputs and return
  values, output structure, error semantics, and fallback behavior. Follow these
  interfaces and conventions when adding a vendor or model.
- **Keep vendor-specific behavior in the adapter.** Use existing extension points
  for vendor endpoints, field mapping, firmware differences, and capability checks.
  Register adapters by platform, OEM, role, and generation, and follow the shared
  contract. For example, Lenovo GB300 enriches `nodebootorder` with names and paths
  from `BootOptions` in `oem/nvl/lenovo.py`, within that adapter's scope.
- **Justify shared interface changes and verify compatibility.** Explain why the
  existing extension points are insufficient. Identify affected callers, adapters,
  and external behavior. Prefer backward-compatible extensions. For an unavoidable
  breaking change, document the migration path. Test the target model, add or run
  regression tests for affected existing implementations, and validate representative
  hardware as described in section 7. Record cases where hardware is unavailable.
  Results from one vendor apply to the models tested for that vendor.

### inventory syntax and precedence

```
[compute]
vdc1-pro2-t10-gpu-cp-01 host=10.128.235.129
[compute:vars]
bmc_user=root
bmc_password=0penBmc
[all:vars]
timeout=5
[nodehealth:defaults]
jobs=18
timeout=10
node_timeout=30
```

- **Comments**: whole-line `# ...` / `; ...` and inline `# ...` are ignored
  (everything after `#` is a comment).
- Address keys: `host=` / `bmc_ip=` / `ansible_host=` / `ip=`, or the second
  bare token.
- Variable precedence: `[all:vars]` < group `:vars` < inline `key=value`.
- Role comes from the group (`compute/nvswitch/powershelf`), with a name-pattern
  fallback.
- Role credential fields: compute `bmc_*`, nvswitch `nvswitch_bmc_*`,
  powershelf `powershelf_bmc_*` (falling back to `bmc_*`).
- `[nodehealth:defaults]` provides jobs/timeout/node_timeout/summary defaults,
  active when not passed explicitly.
- `node_timeout` is the **whole-node wall-clock budget** (seconds, default 30):
  once spent, Redfish/IPMI makes no new request and in-flight requests are
  clamped to the remainder, so a slow BMC cannot stall the batch. It applies to
  capability commands only; `scan` leaves it unset (it is a long task by
  design), and there is **no CLI flag**.
- The `hosts.list` generator groups by `DeviceType`: `PhysicalNode→compute`,
  `PowerShelf→powershelf`, `Switch` whose name contains `-gpu-nvl-` → `nvswitch`,
  otherwise `switch`.

### hostlist expand and compact

Self-implemented (no external GPL tools), bidirectional SLURM/scontrol style:

- `expand` (fed to operations): `node[01-03]`, `node[1-3]`, `gpu[001-003,007]`,
  `x{8..10}`, nested brackets, top-level comma separation, de-duplicated and
  order-preserving. Ranges zero-pad to the low-end width. Top-level comma
  splitting ignores commas inside `[]`/`{}` (`n[01,03-05]` is a single
  expression).
- `compact` (the default `list` output): folds a set of hostnames into
  `vdc1-pro2-n[03-10]-gpu-cp-[01-18]`, equivalent to the legacy `nodelist`'s
  `pshbak -c`. Guarantees `expand(compact(x)) == x`.

`nodelist` output matches the legacy script: one line per role, the role name
right-aligned in 15 columns (using the hosts.list DeviceType: `PhysicalNode` /
`Switch` / `PowerShelf`):

```
   PhysicalNode: nfs01,vdc1-pro2-n[03-10]-gpu-cp-[01-18],...
         Switch: vdc1-pro2-n[03-10]-gpu-nvl-[01-09],...
     PowerShelf: vdc1-pro2-n[03-10]-gpu-pwr-[01-08],...
```

`nodeping` output matches the legacy `dshbak -c`: grouped by status, each group
printing a `----` fence, the compacted hostlist, a fence, then the status line
(`BMC pingable` / `BMC unreachable` / `No BMC IP found`).

Neither prints an extra stderr statistics line, so `nodelist 2>/dev/null` is
byte-identical to the legacy script.

The legacy `nodeping` flow was "compact and save, then expand and ping each
one"; this tool likewise accepts the compacted expression (with in-bracket
commas, multiple ranges) directly as a selector for `ping` or a check command.

### Host selection

Selectors support: group name / single hostname / hostlist pattern, comma-mixed.
The role is decided by the **command prefix** (`node*`/`nvsw*`/`ps*`); compute /
nvswitch / powershelf **each have independent psu/temp/fan sensors** that must
not be mixed, hence:

- no noderange → the whole role group;
- with a noderange → resolve first, then filter by role (cross-role hosts are
  skipped with an stderr notice);
- `nodelist`/`nodeping` are role-agnostic and cover the whole inventory.

```sh
reachable=$(./nodeping --long vdc1-pro2-t10-gpu-cp-[01-18] \
            | awk -F: '/pingable/{print $1}' | paste -sd, -)
./nodetemp "$reachable" --errors-only
```

### Platform, OEM, role and cache

`runtime.build_device` reads identity from the BMC service root and first Manager and System. `model.platform_for` distinguishes HGX (B200/B300) from NVL (GB200/GB300), while `model.odm_for` identifies the OEM independently. The inventory supplies the role through an explicit `role=` or a built-in role group. `oem.for_device` selects an adapter using platform and OEM, so different NVL OEMs can use different adapters. Inventory entries can override detection with `platform=` and `oem=`.

When an OEM spans multiple platforms, register a specific adapter by `(platform, oem, role, generation)` in `oem/__init__.py`. Dell NVL compute nodes use `oem/nvl/dell.py` for Redfish virtual media status. Enable writes after validating the firmware workflow. Use `None` for an adapter shared across generations. Review the [NVIDIA RMS hardware list](https://docs.nvidia.com/rms/documentation/reference/hardware-compatibility-list/) for device identities and interfaces. Qualify each write against the specific device and firmware before registering it.

Public BMC interface references

- [NVIDIA NVSwitch Redfish URI list](https://docs.nvidia.com/networking/display/nvidiaswitchbmcusermanualv8800021040/supported%2Bredfish%2Bscheme%2Buris)
- [Lenovo GB300 NVL72 management interfaces](https://pubs.lenovo.com/gb300-nvl72/management_options)
- [HPE GB300 NVL72 BMC Redfish](https://support.hpe.com/hpesc/public/docDisplay?docId=sd00007150en_us&docLocale=en_US&page=GUID-1D3E5F93-529F-40DD-9E81-3D8DE8FDF8FF.html)
- [Dell XE9780 iDRAC](https://www.dell.com/support/product-details/en-us/product/poweredge-XE9780/resources/manuals)
- [Lenovo HGX B300 XClarity](https://pubs.lenovo.com/sr680a-v4/configure_the_firmware)
- [HPE HGX B300 Redfish](https://support.hpe.com/hpesc/public/docDisplay?docId=sd00007670en_us&docLocale=en_US&page=GUID-2D1518D5-6372-401B-AE97-5F40677DFD0D.html)
- [Supermicro Redfish API](https://www.supermicro.com/en/solutions/management-software/redfish)

### Blackwell compute model coverage

Cross-reference [HGX Certified Systems](https://docs.nvidia.com/certification-technology/certified-systems/nvidia-certified-systems/),
[NVL Qualified Systems](https://marketplace.nvidia.com/en-us/enterprise/qualified-system-catalog/),
and the [RMS compatibility list](https://docs.nvidia.com/rms/documentation/reference/hardware-compatibility-list/).

| Model | Implementation |
| --- | --- |
| NVIDIA DGX B200/B300 | HGX routing. Use `Systems/DGX/SD` if active boot order is missing. |
| HPE XD685 / XD690 HGX | Prefer iLO `Systems/1` on XD685. Discover OpenBMC resources on XD690. |
| NVIDIA GB200/GB300 NVL | Share `oem/nvl/nvidia.py` and retain `gb200` compatibility routing. |
| HPE GB200/GB300 NVL72 | `oem/nvl/hpe.py` prefers `Systems/System_0` and honors inventory overrides. |
| Cisco UCS C880A M8 HGX B300 | Prefer `Systems/DGX` for reads. |
| Dell XE9712 NVL | Discover AMI and iDRAC resources. Use reported identity to resolve generation. |
| Supermicro NVL | `oem/nvl/smc.py` uses standard read-only discovery. |
| Wiwynn, Aivres, KAYTUS NVL | `oem/nvl/wiwynn.py`, `aivres.py`, and `kaytus.py` use standard read-only discovery. |
| Wistron NVL | Existing Wistron adapter. |

Aivres `KRS8000` / `KRS8000V3` are GB200 and `KRS8500V3` is GB300. Aivres and
KAYTUS keep separate OEM identities. Use `platform=`, `oem=`, `generation=`,
and `system_id=` where needed. Hardware validation is pending for the new
paths. Verify writes separately per section 7.

### Wistron GB300 NVL hardware interfaces

The Wistron `GB300 Bianca PDB` (BMC `01.05.26.0305`) exposes its host as
`Systems/system`, managed by `Managers/BMC_0`. The separate
`Systems/HGX_Baseboard_0` exposes the GPU resources and is managed by
`Managers/HGX_BMC_0`. Firmware can repeat a module serial across several GPUs.
In that case, `nodeinventory --gpu` reports the per-GPU serial as unavailable
and displays the GPU UUID.

Hardware checks passed for `nodebmc info/time`, `nodefw show`, `nodemon`, and
`psfan` on the paired Delta Power Shelf. `nodemon` reads total tray power and
both PDB HSC power readings. `nodebios --set` was verified to stage a value and
restore the original. This firmware lacks the fields required by the `base`
and `bf3` presets. The PXE check used `nodenetboot prefer --mac` to promote one
port, read back the full `BootOrder`, and restore the original order. The
existing boot override retained its original value.

### HGX B300 hardware interfaces

Supermicro `SYS-822GS-NB3RT` (BMC `01.12.02.03`) uses `Chassis/1` for host PSUs.
The GPU baseboard separately exposes HSC/HSCC resources. `nodepsu` merges
modern and legacy host PSU records by serial. Aivres/KAYTUS
`KR9288-X3-A0-F0-01` (BMC `2.03.ef`) uses `Systems/1` and
`Managers/1/VMMService`. `Systems/NV` provides GPU serials.
BIOS staging, PXE settings, and fan modes were exercised on both models.
Supermicro writes to `Bios/SD`. The KR9288 requires the parent `Bios` ETag
when writing `Bios/Settings`. Supermicro `stop URL` detaches the slot matching
the exact URL. Firmware may retain the configured URL after detaching.
Mount waits for the Redfish task and checks `Inserted`. On hardware, KR9288 mounted and ejected an NFS ISO and restored its original mount. Supermicro inserted and ejected an ISO served by Apache on standard HTTP.
On the KR9288, `nodenetboot prefer` stages `NetworkBbsPriority` in `Bios/Settings` using the parent `Bios` ETag. `status` reads back the pending order. This changes the order within the network group and preserves the relative order of the disk and network groups.

### Command implementation by model and role

| Platform | Required command coverage |
| --- | --- |
| HGX | Implement the applicable compute-node `node*` commands and actions for each model. |
| NVL | For each rack design, cover compute-tray `node*`, NVSwitch `nvsw*`, and power-shelf `ps*`. |

Use the OEM capability checklist below and the existing commands for each role
to track implemented, pending, and unsupported commands or actions. Track
hardware verification separately. For NVL, choose the implementation based on
each rack device's own OEM, model, and management interface. Adapt generations
or models separately when their interfaces differ, including within one OEM.
Reuse standard Redfish, IPMI, or NVOS paths where the interface matches.
Follow section 7 for read-only hardware validation after implementation.

### Adding a power-shelf PMC

Use the identity reported by the PMC, or set `oem=liteon`, `oem=delta`,
`oem=megmeet`, or `oem=flex` in inventory. Lite-On and Delta each have a
dedicated adapter. Megmeet and Flex use standard discovery through
`oem/nvl/megmeet.py` and `oem/nvl/flex.py`. Unidentified vendors use the generic
adapter. Fields and capabilities depend on the device response. Older shelves that report only NVIDIA/DGX or no known OEM retain
the existing read-only Delta PSU sensor fallback.

For a new PMC, verify standard Redfish resources first. Register a
platform/generation or OEM/role adapter in `oem/__init__.py` when needed,
and register specialized PSU readers in the OEM fallback table. Record the
PMC model, firmware, and resources, and preserve the `ps*` command contract.
Qualify writes separately. Delta's inventory fallback follows public
documentation. Delta, Megmeet, and Flex are pending site hardware validation.

[NVIDIA PowerShelf vendors and models](https://docs.nvidia.com/multi-node-nvlink-systems/nvfupd-guide/examples/powershelf.html)

### OEM capability checklist

Use the original nodetools scripts as the baseline. Track compatibility per operation and model. Share standard Redfish reads, and override confirmed OEM differences in the adapter.

| Capability | Read path | Control or remaining work |
| --- | --- | --- |
| BIOS | `nodebios` current values and `pending` settings | Keep existing OEM actions. Qualify individual writes, apply timing, and reboot requirements |
| Boot | `nodebootorder` persistent order, `nodenetboot` boot and PXE data | `nodeboot` one-time override. Qualify persistent ordering and port selection by OEM |
| Assets and firmware | `*inventory` versions, `--details` identity, compute `--gpu`, `nodefru`, `nodefw show` | Qualify full FRU and part numbers by model |
| MAC and network | `nodemacs` host MACs, BMC/PXE for some legacy adapters | Extend BMC/PXE discovery, network settings, and cable diagnostics by interface |
| Temperature, fan, PSU | `*temp`, `*fan`, `*psu`, including health | Fan control is OEM-specific. PSU and temperature are read-only |
| Virtual media | `nodevm` status | Existing OEM mount/eject. Qualify media protocols, enable, and insert per model |
| Logs and tasks | `*eventlog`, `nodetask`, `*inventory --tasks` | Clear on supported adapters. Task cancellation and diagnostic bundles remain open |
| Power and console | `*power` state, `nodeconsole` SOL | Qualify main power, AUX power, and BMC restart separately, including rack devices |
| Additional legacy tools | Health monitoring, NVLink/UPHY, chassis and platform sensors | Compare thresholds, telemetry schedules, and alarms per tool |
| Management plane | `nodebmc` identity, firmware, and clock | Qualify `reset` and `time --set` per model. Extend account and network settings per model |


`scan` also probes chassis endpoints and writes a capability map. Its cache key uses vendor, manufacturer, model, available firmware versions and explicit inventory overrides. Normal commands only read the map. With no map or `--no-cache`, they still read device identity and discover sensors live.

### Scan cache (scan)

A single BMC fans out over dozens of chassis, and which chassis has which
collections is **vendor-specific and not always declared in the detail**
(Wistron's `Sensors` collection never appears in its chassis detail). So `scan`
probes the collection endpoints directly and records, per chassis, the member
counts of `Sensors`/`Fans`/`PowerSupplies`/`Temperatures` and whether the legacy
`Thermal`/`Power` singleton objects exist — plus the identity/ODM — writing to
`~/.cache/nodetools/scan/<identity>.json` (`SCAN_CACHE_VERSION`; bump it on
structural change to avoid misreading old files). This is the tool's **only
cache**, and it is **per machine type**, not per address.

fan/psu/temp walk only the chassis that can answer their ladder via
`common.rung_chassis(device, key)` (falling back to live discovery when there is
no cache, unchanged behavior): `Fans`/`PowerSupplies`/`Sensors`/`Temperatures`
by count > 0, `Thermal`/`Power` by existence. Measured on compute nodes: `fan`
0.9 s vs 11 s live, `psu` 8 s vs 22 s, `temp` 7 s vs 17 s. Re-scan any one node of
a type to refresh its template; `scan --clear` deletes it.

### Endpoint selection for all management commands

Apply this rule to every command that talks to a device, including status
queries and control actions. It covers compute trays, NVSwitch trays, and
power shelves through Redfish, IPMI, or OS interfaces.

1. **Use the configured endpoint directly.** Select an adapter from the
   vendor, platform, generation, and role in inventory. Keep fixed interfaces
   in the adapter for the model and validated firmware. Inventory may specify
   the resource instance with `system_id`, `manager_id`, or `chassis_id`.
   Once the target is known, read or act directly through the known interface
   and skip identity or collection probes unrelated to the operation.
2. **Reuse templates for variable layouts.** Use `scan` to discover and save
   machine-type templates. Discover only the resources needed when neither
   inventory nor a valid template identifies the target. Use the capability's
   fallback or rediscover when a fixed endpoint is no longer valid. Refresh
   with `scan`, or verify live using `--no-cache` where supported.
3. **Validate writes.** Reuse known paths and perform the target, resource,
   parameter, and task or state checks required by the action. Stop if the
   target cannot be confirmed. If the write outcome is uncertain, read the
   task or state before deciding whether to retry.
4. **Verify each command.** Preserve the command interface and output format.
   Test request counts with complete configuration, fallback when configuration
   or endpoints are missing, and readback after writes. Handle endpoint
   differences in the vendor adapter.

For each new model, review every applicable command and action by role.
Implement direct access for verified fixed endpoints and document the matching
`platform`, `oem`, and `generation` values in the inventory template. When
resources vary by device, discover only the collections the command needs.
Record request counts and test fallback and output completeness before merging.

Lenovo GB300 `nodefan` is an implemented read-only example. With `platform=nvl`,
`oem=lenovo`, and `generation=gb300` in inventory, the adapter uses
`inventory_fixed_caps` to skip startup discovery for `fan` and sends one GET
to `/redfish/v1/Chassis/<chassis_id>/Thermal`. The default is `Chassis_0`.
One response supplies all eight PWM and sixteen RPM readings.

### Direct-read certification completion criteria

`AGENTS.md` makes the endpoint-selection rule a repository requirement.
`docs/certified-reads.json` records evidence per model, firmware and capability.
Passing one capability does not certify the entire model, other commands or writes.
Legacy adapters are not automatically recertified by this review.

For a new or changed certified capability:
- Exercise `runtime.Runner`, device construction, adapter selection and collection.
  Assert the complete ordered request list and count with full configuration.
  Parser-only tests are insufficient. Normal reads must not rediscover the root,
  Managers or unrelated collections.
- Test missing resources, wrong IDs/types/units, non-finite or incomplete readings,
  valid zero values, health alarms and explicit resource selection.
- Verify real-device output, firmware and request counts, update evidence and
  unverified scope, and run the full regression suite. Generic discovery returning
  values is not certification. Do not relax request-count assertions to hide a regression.

`tests/test_direct_read_contract.py` checks runtime paths using independent transport
fixtures. The GitHub Actions job `direct-read-contract` runs the full suite.
The workflow does not configure branch protection. A required status check must
be enabled in repository settings for GitHub to block merges on failure.

Validated in this review:
- Supermicro GB NVL / GB300, BMC `70.02.02.01`: `nodepsu` directly reads
  `PDB_0_HSC_0_Pwr_0` and `PDB_0_HSC_1_Pwr_0`; two GETs, two tray DC HSC inputs.
  These are not rack AC PSU modules. Other capabilities and writes remain unverified.
- Lite-On PF-1333-9RB / CM-14MP-1RB, BMC `r1.3.9`: with explicit configuration,
  `pspsu` uses the `powershelf` view; eight GETs, six physical PSU rows.
  Merge modern identity and legacy `Power` watts by serial rather than reading value.

The optional shared extension `psu_read_sensors` expresses a fixed list of sensor
GETs. Existing `psu_sensor_chassis` bulk collection reads cannot express the
two-request SMC path. Adapters without the new field retain their existing
collection ladder and CLI. The SMC fixed reader is registered only for
`nvl/smc/compute/gb300`; GB200 and unknown generations keep discovery.

### IPMI password modes

- Default: `-U user -P pass` (matching the legacy scripts).
- Env mode: `--ipmi-env` or `NODETOOLS_IPMI_ENV=1` → `-U user -E`, with the
  password passed via `IPMI_PASSWORD`, never entering the process table.

Precedence: explicit flag > environment variable > inventory.

### Output

Field order is chosen per subject (`report._FIELDS`): fan is
`<node>: <id>,<name>,<speed>,<health>`, psu/temp is
`<node>: <id>,<health>,<reading>`, matching the legacy scripts; spaces/commas
inside fields become `_`, so `grep -v ,OK` works. `--summary` prints
`<node>: <CAP>,<role>,total=N,OK=N,nonOK=N`. Empty results print
`ERROR no readings ...` and exit with code 1.

## 6. How to extend

### Add a sensor subject (beyond fan/psu/temp)

1. Add the subject name to `config.CAPS` (`arguments.SUBJECTS` derives from it).
2. Create `caps/<subject>.py` implementing `collect(device) -> list[reading]`,
   chaining fallback steps with `ladder.first([...])`; reuse `caps/common.py`.
3. Register it in `caps/__init__.py`'s `COLLECTORS`.
4. For control actions, add a dispatch in `runtime.Runner._control`.
5. `arguments.SENSOR_COMMANDS` auto-generates `<prefix><subject>` per prefix.
6. Add `tests/test_caps.py` cases: build JSON responses with `FakeRedfish`.

If a subject only makes sense for one role (e.g. `inlet`, compute only), do **not**
add it to `config.CAPS`; instead register one explicit `<prefix><subject>` entry in
`arguments.SENSOR_COMMANDS` (still register `COLLECTORS`/`report._FIELDS` as usual)
so no empty commands are generated for the other roles. When a collector needs a
run-time parameter, read it from `device.options` (`runtime.build_device` attaches the
run's `Options`) and add the flag for that subject in `build_parser`.

If the value lives on a single chassis (e.g. `inlet`), do not walk every chassis
with `temp.collect`: a `ThermalMetrics` response is slow for the BMC and fanning
out over one BMC multiplies the latency. Instead probe chassis in
`config.DEFAULT_CHASSIS` priority order and return at the first hit (see
`caps/inlet._find`). Subjects that need no scan template can be listed in
`runtime._PROFILELESS_CAPS` to skip the identity read. Prefer a test in
`tests/test_caps.py` that uses a counting `FakeRedfish` to assert a single request.

### Add an info subject

1. Add `subject → supported roles` to `config.INFO_SUBJECTS`, and declare actions
   in `config.INFO_ACTIONS` / read words in `config.INFO_READ_WORDS` as needed.
2. Create `subjects/<subject>.py` implementing
   `collect(device, action, options) -> list[str]`, returning **complete output
   lines** (with the `<node>: ` prefix); reuse `subjects/common.py`. If the
   subject has role-specific fixed paths (like the ps NVIDIA endpoints), branch
   on `device.role` first and dispatch compute to `device.odm.<subject>` (power's
   nvsw goes to `device.odm.nvswitch_power`).
3. Register it in `subjects/__init__.py`'s `HANDLERS`.
4. `arguments.INFO_COMMANDS` auto-generates `<prefix><subject>` per supported
   role; add `pyproject.toml [project.scripts]` entries and repo-root symlinks.
5. Add the reference implementation to `oem/base.Oem` (the GBXX convention) and
   override per vendor from the legacy scripts; fall back to `super()` when an
   endpoint is absent.
6. Add `tests/test_subjects.py` cases; `FakeRedfish` gains a `post()` — see the
   existing cases for the boilerplate.

### Add a role prefix

Add `prefix → role` to `config.ROLE_PREFIXES` (e.g. `ib` → `ibswitch`) and the
matching group to the inventory; commands and `_role_hosts` filtering take
effect automatically.

### Add an ODM

1. Create `oem/{hgx,nvl}/<odm>.py`, subclass `oem/base.Oem`, set `name`/`fan_chassis`, and
   implement `fan_control` as needed.
2. Register it in `oem/__init__.py`'s `_MODULES`; add aliases to `_ALIASES`.
3. If the ODM's identity string is special, add a marker to `model.SIGNATURES`
   (mind the order).
4. If its **data reading** also differs from the generic path, prefer adding a
   targeted step in the `caps/*.py` ladder (the Wistron-specific
   `Sensors/Chassis_0_FAN_*` is hit via `_from_sensors`) rather than rewriting
   the whole collection in the ODM module.
5. For reads and writes, follow
   [Endpoint selection for all management commands](#endpoint-selection-for-all-management-commands).

### Add a transport

Add `transport/<name>.py` exposing a minimal interface (e.g. `get/post`) and
wire it into `Device` in `runtime._device`. Raise `errors.TransportError`
uniformly.

## 7. Testing and verification

### Unit tests

Reuse the project's virtual environment if one is available. Otherwise, create a temporary venv from the repository root, for example on the BCM head node:

```sh
test_venv=$(mktemp -d "${TMPDIR:-/tmp}/nodetools-test.XXXXXX")
python3 -m venv "$test_venv"
. "$test_venv/bin/activate"
```

With the project environment active, install the project and test dependencies from the repository root and run the tests:

```sh
python -m pip install -e . pytest
python -m pytest -q
```

Coverage: hostlist expansion, inventory parsing/precedence/generation,
selectors, output format and `grep -v ,OK` compatibility, ODM registration, the
three sensor subjects against `FakeRedfish`, the info subjects (power/eventlog/
inventory IPMI default, nvsw/ps fixed paths, Dell/Lenovo/SMC/AMI overrides, the
space-after-colon difference), scan cache (write / version-mismatch ignored /
`rung_chassis` hit and fallback), `odm_for` reading the profile (present/absent/
empty odm), `InfoRunner` reporting `BMC unreachable` on a silent transport
failure, `nodelist`/`nodeping` no-arg default-all and reachability checks, and
the per-node deadline (`subprocess` stubbed, no network). Cache tests override
`SCAN_CACHE_DIR` with `tmp_path`.

### Real-hardware smoke test (required after an interface change)

```sh
# generate an inventory from an existing hosts.list
./nodetools inventory init --from /path/to/hosts.list -o /tmp/nt.ini
./nodetools inventory show -i /tmp/nt.ini | head

# single host drill-down to confirm fields (prefix names the role)
./nodefan  vdc1-pro2-t10-gpu-cp-01 -i /tmp/nt.ini
./nodetemp vdc1-pro2-t10-gpu-cp-01 -i /tmp/nt.ini --summary
./pspsu    vdc1-pro2-t10-gpu-pwr-01 -i /tmp/nt.ini

# batch and errors-only
./nodetemp vdc1-pro2-t10-gpu-cp-[01-03] -i /tmp/nt.ini --errors-only
```

**Note**: `--action` (fan `auto/max`) really changes device
state; test on a single, known-reversible machine first, never the whole batch.

### Hardware validation for fan speed control

When adding or changing fan speed control, test the actual CLI on an authorized
representative node for each affected OEM and model. Record the model, BMC
firmware, and fans tested. Unit tests and `--dry-run` verify command selection.
Hardware validation includes the following steps.

1. Run `nodefan check` to record baseline RPM and health. Record the current
   control mode if the BMC reports it.
2. Run `nodefan <node> max` and record the result. Let the fans settle, then
   read the same fans again. Verify the RPM or the BMC's reported target PWM
   against the expected maximum setting.
3. Run `nodefan <node> auto` and read the fans again. Verify automatic mode
   when it is exposed, and check RPM and health. Finish in the mode specified
   by the restoration plan. Attempt and verify restoration even if the
   maximum-speed command only ran partially.
4. Record the CLI result, BMC response, speed readback, and restoration outcome
   separately. Mark the test as passed once the write succeeded, the expected
   behavior was observed, and restoration was verified. If recovery required
   a direct BMC call, record the recovery and mark the CLI operation as failed.
   Mark cases without a representative node as pending hardware validation.

### RemoteMedia hardware validation

After adding or changing a virtual media adapter, test a mount and eject on
representative hardware for each model, BMC firmware version, and share protocol.

1. Run `nodevm HOST status` and record the initial slot states. Use an empty slot
   and a small test ISO.
2. Run `nodevm HOST mount URL`. Pass `--vm-user` and `--vm-password` if the share
   requires authentication. Run `status` again and confirm the selected slot
   reports `inserted=True` and identifies the test ISO.
3. Run `nodevm HOST eject URL`. If the adapter only supports `nodevm HOST eject`,
   first confirm the test ISO is the only mounted image. Run `status` again and
   confirm the slot reports `inserted=False` and the other slots
   match their initial state.
4. Record the model, BMC firmware, share protocol, slot, and both readbacks.
   Mark cases without representative hardware as pending hardware validation.

#### CIFS test share

On Debian or Ubuntu, install `samba` and `smbclient`. Create a local test
account, set its Samba password with `smbpasswd -a ISO_USER`, and grant it access
to the directory and read access to the ISO. Add this share to the existing
Samba configuration and replace `ISO_USER` with the actual account.

```ini
[share]
    path = /var/www/html/isos/
    read only = yes
    valid users = ISO_USER
```

Place the test ISO in the share. Validate the configuration with `testparm -s`,
reload it, and check authentication and file visibility with
`smbclient //SMB_HOST/share -U ISO_USER -c 'ls'`. Confirm the BMC can reach TCP
port 445 on the Samba server. Wistron GB300 uses `smb://SMB_HOST/share/probe.iso`.
Verify supported protocols for each adapter on its hardware.

### Read-only hardware validation by model

When adding or changing a device interface, track hardware validation by actual
model. For HGX and NVL, select at least one representative of every available
compute model and run each applicable read-only `node*` command. Test separate
generations and models with different management interfaces even within one OEM.
For each NVL model, also run the applicable read-only checks on a representative
of each associated NVSwitch and power-shelf model.

| Device | Read-only coverage |
| --- | --- |
| HGX / NVL compute | `nodeping`, `nodefan`, `nodepsu`, `nodetemp`, `nodeinlet`, `nodemon`, `nodepower status`, `nodeeventlog status`, `nodeinventory`, `nodebootorder`, `nodenetboot list`, `nodemacs`, `nodebios status` / `pending`, `nodevm status`, `nodefru`, `nodebmc` / `time`, `nodetask`, `nodefw show` / `status` |
| NVSwitch | `nvswpsu`, `nvswtemp`, `nvswpower status`, `nvsweventlog status`, `nvswinventory` |
| Power shelf | `psfan`, `pspsu`, `pstemp`, `pspower status`, `pseventlog status`, `psinventory` |

Include any other read-only entry points affected by the change. With `nodetask`,
list tasks first and query details with an ID returned by the BMC when one is
available. Use `nodenetboot list` for PXE entries and `nodebootorder` for the full order.
Keep power control, log clearing, BIOS and boot changes, virtual media changes,
BMC resets, clock writes, and firmware updates outside this read-only pass.

Record the platform, OEM, actual model, BMC or NVOS version, representative
node, commands, and results. Check key fields against the device interface.
Distinguish passes, failures, unsupported operations, and unavailable hardware.
Mark cases without hardware as pending real-hardware validation.

#### HGX read-only hardware results

HGX390 (KAYTUS KR9288-X3-A0-F0-01, BMC 2.03.ef) and HGX654
(Supermicro SYS-822GS-NB3RT, BMC 01.07.04.00) were checked against the
table above. Both passed reachability, sensors, monitoring, power state,
active and pending BIOS, boot entries, NICs, virtual media, FRU, BMC
identity and clock, and task list and detail queries. HGX390 had no
pending BIOS settings. Task details used BIOSSetup on HGX390 and 1 on HGX654.

Event logs, full inventory, firmware inventory, and firmware task queries
passed on HGX390 and on the HGX654 retest. `nodeeventlog status` paged
through all 4,096 entries. `nodefw show` and the compatibility command
`nodeinventory` share firmware reads and expanded all 80 components in
pages. `nodefw show` also includes BIOS and BMC summaries. `nodefw status`
returned four tasks.

#### HGX direct-read coverage

The two verified B300 models were checked with automatic discovery and with
explicit inventory settings (`platform=hgx`, `generation=b300`, and
`oem=smc` or `oem=aivres`). Sensor IDs, names, and fields matched.
After confirming chassis coverage, set `temp_chassis_id=1` to read
`Chassis/1/Thermal`. The temperature collector falls back to discovery
when that endpoint returns no readings.

| Model or adapter | Adapter-defined direct reads | Hardware fan | Hardware PSU | Hardware temperature |
| --- | --- | --- | --- | --- |
| Supermicro SYS-822GS-NB3RT | fan, PSU | 6 (6 → 1) | 6 (13 → 8) | 35 (91 ‡ → 1) |
| Aivres/KAYTUS KR9288-X3-A0-F0-01 | fan, PSU | 40 (6 → 1) | 8 (6 → 1) | 71 (12 → 1) |
| AMI HGX | fan, PSU | Pending | Pending | Pending |
| BYD HGX | fan | Pending | Pending | Pending |
| Compal HGX | fan, PSU | Pending | Pending | Pending |
| Dell HGX | fan, PSU, temp | Pending | Pending | Pending |
| Gigabyte HGX | fan, PSU | Pending | Pending | Pending |
| Lenovo HGX | fan, PSU | Pending | Pending | Pending |
| Netweb HGX | fan, PSU | Pending | Pending | Pending |
| NVIDIA DGX B200/B300 | fan, PSU | Pending | Pending | Pending |

Cells show sensor counts and Redfish calls from discovery → direct read.
‡ On Supermicro's discovery path, 33 of the 91 calls returned HTTP responses
within the node deadline. One of its 35 temperature sensors lacked a numeric
reading in both modes. The remaining models and command families require
model-specific hardware validation.

## 8. Code style and commit conventions

- Include runnable examples in `--help` for every public command and each subcommand
  with its own arguments. Lead with common reads using defaults and host ranges where
  applicable. Show a preview or precheck for writes that support one. Verify examples
  after changing arguments and report common input errors with command-specific usage.
- Help acceptance is enforced by tests/test_help_examples.py across command trees,
  independent entries and auxiliary scripts. Examples must be visible and accepted
  by the real argument parser; they are parsed without executing device operations.
  New actions with dedicated options require examples in that command's help,
  covering normal reads, preview/precheck and application where relevant.
  Assert action coverage too; checking only the Examples heading is insufficient.
- Standard library + `requests` only; no extra dependencies (keep
  `pip install .` light).
- No incidental comments; keep naming consistent with existing files; put new
  logic in the right layer, not piled into `cli.py`.
- Before committing, run `python -m pytest -q` in the project environment. Attach
  hardware smoke-test results for interface changes.
- Commit messages in English, imperative, explaining "why".
- Author is always `junli-nv <junliz@nvidia.com>` (already in `pyproject.toml`'s
  `authors` and used as the git identity); do not sign with anyone else's
  name/email.
- Real `inventory.ini` / `hosts.list` stay out of the repo (already in
  `.gitignore`); never commit passwords.

## 9. Known limitations and TODO

- HGX B200/B300 use `node*` commands. Complete read-only validation by OEM and
  actual model. See the table above for Supermicro and Aivres sensor results.
- Persistent PXE order was verified on the KR9288 with BMC 2.03.ef. Two network
  BBS entries were exchanged and checked in both the pending settings and the
  active BIOS after power-on. Boot group priorities were preserved.
  The KR9288 adapter rejects AUX reseat pending a confirmed model-specific interface.
- Non-`-gpu-nvl-` switches in the `Switch` group are classified as `switch` for
  now, with no matching capability (no `nvsw*` command hits them).
- Control actions (`--action`) cover only the confirmed IPMI raw / Redfish PATCH
  / racadm commands; read the matching OEM script before adding more bytes.
- Role commands filter out cross-role selections with an stderr notice;
  `nodelist`/`nodeping` are role-agnostic and cover everything. `nodeping`
  matches the legacy script and uses a single flood probe
  (`ping -M do -f -c 1 -W <t> -w <t>`), requiring the same privileges the legacy
  script did (root), and thus the same momentary burst as the legacy script.
  Hosts without a BMC address are grouped as `No BMC IP found` (per-host under
  `--long`).
- Not yet done: `nodesensors` (MetricReports telemetry), result
  persistence/alerting, structured output for remaining commands,
  `nodecable`/`nodeflapping` (SSH), `nodemon.ipmi`.
- Info subjects' `clear` (log clearing) and `reseat` etc. depend on external
  tools (`onecli`, `racadm`) or return empty bodies in the legacy scripts, so
  their output details are not compared one by one; re-read the legacy script
  before changing them.
- `ibswhc`, `spxswhc`, `ibswmon`, and `spxswmon` have been migrated. Verify log access and temperature alarms on reachable IB/SPX switches.

## Adding a firmware update adapter

`nodefw show` and `nodefw status` use the standard Redfish queries in
`subjects/firmware.py`. When adding firmware update support for a vendor, implement the adapter in the
matching platform's OEM directory and register verified system models in
`firmware_cli.ADAPTERS`. The adapter validates hardware, package, version,
component, and power and task state, then reads the submission result.
`check` and `update` share preflight validation. Only `update` writes.

### Paired fan/PSU and bulk acceptance (mandatory)

Fan and PSU queries are cluster-scale operations. Review BOTH capabilities on
every supplied representative model and firmware: fixed endpoints, complete
physical readings, units, zero values, alarms and fallback. One successful
single-node capability is not model acceptance.

Run automated bulk acceptance using the actual selectors and requested
concurrency, including --jobs 200 when requested. Never silently reduce
concurrency or inflate timeouts. Account for every node and expected reading,
requests, failures, elapsed time and process resources. Verify slow-node
isolation, execution budgets after queueing and transport cleanup. Persist
per-node results incrementally. Missing supported readings or failed bulk
acceptance blocks completion. Hardware/BMC absences require real-device
evidence and an explicit unsupported conclusion, not a successful-read label.

### NVSwitch fan scope

No NVSwitch fan command is provided, and routine read audits do not probe fans.
The tested N5500_LD (BMC 88.0002.1962) has empty fan collections and NVOS rejects
the fan query. This is not a successful fan read or a claim about every model.
Keep compute-tray fans and the PSU-module fans actually reported by Lite-On
power shelves. Another NVSwitch model requires independent hardware evidence.

### Shared OS SSH for BMC user management

`nodebmc HOSTS user` reuses the existing NVOS SSH implementation through
transport/ssh.py, adding stdin payload support without changing existing
NVOS telemetry calls. Local-IPMI account operations are shared; SOL is
compute-only. Keep this flow independent of BMC root authentication.
Usage, tests and hardware validation limits: [BMC-USERS.md](BMC-USERS.md).
