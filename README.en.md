# nodetools

English | [中文](README.md)

A single Python tool for inspecting NVIDIA GB200/GB300 nodes over Redfish and
IPMI. It replaces the per-OEM `node*`/`nvsw*`/`ps*` shell scripts with one
`node*`-style command family, where the prefix names the role (compute tray,
NVSwitch, power shelf) and the suffix names the subject.

Three subject groups are implemented:

- sensors: **fan / psu / temp** (one set per role), plus **inlet** (air-intake check, compute only);
- info: **power / eventlog / inventory** (one set per role);
- boot: **boot / bootorder / netboot / macs / bios** (compute only, see below).

The tool's own output is always English (fields, status and log lines are never
translated); only the documentation is bilingual.

## Quick start

`scan` is **not required**: `nodelist`/`nodeping` and every `node*`/`nvsw*`/`ps*`
command run without it. `scan` only speeds up `fan`/`psu`/`temp` by caching each
BMC's Redfish map; without it those commands fall back to live discovery, and
`power`/`eventlog`/`inventory` never read the map.

Recommended order (step 0 once; step 2 optional):

```sh
# 0) generate the inventory (once). Each hosts.list line is `DeviceType Hostname BMC_IP`
./nodetools inventory init --from hosts.list -o inventory.ini

# 1) see what exists and which BMCs are reachable (no scan needed)
./nodelist                      # aggregated node list by role
./nodeping --long               # per-node BMC reachability

# 2) optional: scan once to cache the Redfish map per machine type (speeds up fan/psu/temp)
#    scanning one representative node covers every node of the same type
./nodetools scan cp01

# 3) start using it
./nodetemp  cp[01-18] --summary # one summary line per node
./nodepower cp01                # power state (read)
./nodeeventlog cp01             # HMC event log
./nodetools nodefan cp01 max    # also works as `nodetools <command>`
```

## Install / run

No installation is required — run the launcher in place:

```sh
./nodefan cp[01-18]
```

Optionally install the console scripts (one per command):

```sh
pip install .
nodetemp --group compute --summary
```

## Commands

The prefix names the node role and the suffix names the subject, exactly like
the legacy scripts: `node` = compute tray, `nvsw` = NVSwitch, `ps` = power shelf.
GBXX uses all three prefixes; BXX only needs `node*`.

Compute-node `nodefan` lists speeds on one line per node. Numbers are RPM and percentage readings carry `%`. Use `check` for per-fan health and `--errors-only` to show exceptions. OEM adapters handle `auto` and `max`. On Netweb, `low` and `opt` set fixed 50% and 80% PWM, respectively. Run each tool with `--help` for action descriptions. Common options are listed below.

`nodemon` Reads inlet temperature, whole-node power, and per-PSU input. Whole-node and PSU power are in watts, with units in the column names. The tool prefers reported PSU input watts, uses NA when conversion inputs are insufficient, and marks calculated readings as estimated in JSON. The default is one CSV sample per node, written as soon as it finishes. Missing values are NA and additional slots appear in PSU_OTHER_W. Use --count and --interval for repeated rounds (default interval: 5 seconds) and --format json for JSON Lines. Use nodefan for fan speeds.

```bash
./nodemon 'HGX[390,474]'
./nodemon 'HGX[390,474]' --interval 10 --count 6 --format json
```

For single-phase PSUs that report only current, supply the site voltage and power factor. Replace the example values with those of your feed.

```bash
./nodemon HGX390 --psu-voltage 230 --psu-power-factor 0.95
```

You can also set `psu_input_volts` and `psu_input_power_factor` as host or group variables in the inventory. CLI flags override the inventory. Measured voltage and power factor take precedence.


PSU output defaults to `ID,Health,Reading`. Use `nodepsu --details HGX390` for name, manufacturer, model, firmware, health, input watts and output watts. Missing fields appear as `NA`.
Ctrl-C exits read-only sensor queries immediately with status 130.

```
# sensors
nodefan  <noderange> [status|check|auto|max|low|opt]
nodepsu  <noderange> [status|check]
nodetemp <noderange>
nodemon  <noderange> [--count N] [--interval SEC] [--format csv|json]
nodeinlet <noderange> [check] [--target C] [--tolerance D]
nvswfan  <noderange> [status|check|auto|max]
nvswpsu  <noderange> [status|check]
nvswtemp <noderange>
psfan    <noderange> [status|check|auto|max]
pspsu    <noderange> [status|check]
pstemp   <noderange>

# info
nodepower    <noderange> [status|on|off|cycle|reset|reseat]
nodeeventlog <noderange> [status|bmc|hmc|clear]
nodeinventory <noderange>
nvswpower    <noderange> [status|on|off|cycle|reset|reseat]
nvsweventlog <noderange> [status|clear]
nvswinventory <noderange>
pspower      <noderange> [status|on|off|cycle|reset|reseat]
pseventlog   <noderange> [status|clear]
psinventory  <noderange>

# boot (compute only)
nodeboot      <noderange> [pxe|disk|bios|cdrom]
nodebootorder <noderange>
nodenetboot   <noderange> [status|mac|net]
nodemacs      <noderange>
nodebios      <noderange> [status|base|pending]
nodevm        <noderange> [status|enable|mount|umount|insert|eject|stop|media|lic] [image]

# console (compute only)
nodeconsole   <noderange> [open|close]        # SOL serial console

# general
nodelist [selector] [--long]
nodeping [selector] [--long] [--ping-timeout S]
nodetools scan <selector> [--clear]
nodetools inventory init [--from hosts.list] [-o inventory.ini]
nodetools inventory show [-i inventory.ini]
```

Any command also works as `nodetools <command> ...`, and the legacy short
names `list`/`ping` are aliases for `nodelist`/`nodeping`.

Running a command with **no arguments** (e.g. `./nodepower`) prints that
command's usage and exits non-zero instead of falling back to the whole role
group; select the whole role explicitly with `--all` (or `--group G`).

* `noderange` is a hostlist expression (`cp[01-18]`), an inventory group name,
  or a hostname. It is narrowed to the role named by the prefix. Use
  `--group G` / `--all` instead (both also narrow to the command's role).
* Sensor commands read status by default. Fan modes depend on the OEM. PSU queries are read-only. Device power actions use the `power` commands.
* Sensor output is comma-separated fields; info commands print verbatim
  `<node>: <line>` lines (see below).
* `nodetemp` displays `ID,Name,Health,Reading` for each sensor.
* `nodeinlet` defaults to one line per node, for example `HGX390: 22`. Use `check` to see the threshold verdict.
* `nodeinlet` (compute only) reports the chassis air intake (on GB200 the
  `Chassis_0_Front_IO_Temp_0` sensor; other ODMs use `Inlet_Temp`/`Ambient_Temp`
  and so on), excluding internal inlets such as processor modules and the PDB.
  Each reading is judged against the expected inlet temperature (`OK`/`HIGH`/
  `LOW`; default target 25 C, tolerance +/-5, i.e. 20-30 C is OK, tunable with
  `--target` / `--tolerance`). The intake sensor lives on a single chassis, so
  `nodeinlet` probes chassis in priority order and stops at the first hit (no
  full chassis walk, no scan template) -- roughly one Redfish request per node.
  NVSwitch trays and power shelves expose no chassis air-intake
  sensor over BMC, so no `nvswinlet`/`psinlet` is generated.

### Info commands (power / eventlog / inventory)

Each of these commands corresponds to **one legacy script** and matches its
output verbatim:

* **power** — `nodepower` dispatches by ODM (NVIDIA GBXX default: IPMI
  `power <action>`; Dell has no override so it stays on IPMI, Lenovo/SMC use the
  `Systems/1` Redfish resource). `nvswpower`/`pspower` are NVIDIA-only
  infrastructure, using the Redfish reset (`Systems/System_0`
  `ComputerSystem.Reset`) by default; Compal NVSwitch uses `Systems/Self` with
  `reseat` as `FullPowerCycle`.
  `nodepower status` displays consistent Redfish and IPMI states such as `On` and `Off`, with `N/A` when unavailable.
* **eventlog** — `nodeeventlog` reads `[HMC]` by default
  (`HGX_Baseboard_0` EventLog), `bmc` reads `[BMC]` (IPMI SEL), `clear` clears
  the BMC/HMC logs; each OEM overrides its own Sel/PlatformLog/BIOS collection.
* **inventory** — reads `UpdateService/FirmwareInventory` (compute line
  `name:Id Version`, nvsw/ps line `name: Id Version`; AMI/BYD/GIGABYTE/DGX/
  Lenovo override as their scripts do).

### Boot commands (boot / bootorder / netboot / macs / bios / vm)

These only make sense for **compute trays** (`nvsw*`/`ps*` have no such
subject). The default NVIDIA GBXX implementation matches the legacy scripts
verbatim and each ODM overrides its own endpoints (falling back to the base
class when an endpoint is absent):

* **boot** — `nodeboot [pxe|disk|bios|cdrom]`: IPMI `chassis bootdev <action>`
  (default `pxe`). No ODM variant.
* **bootorder** — `nodebootorder`: reads `BootOptions` and
  `Systems/<id>`'s `Boot/BootOrder`, printing `Id`/`DisplayName`/
  `UefiDevicePath` per entry.
* **netboot** — `nodenetboot [status|mac|net]`: configure a one-shot or
  continuous PXE boot override (GBXX default writes Continuous/UEFI/Pxe and
  rewrites BootOrder). Dell/Lenovo/SMC have their own implementations.
* **macs** — `nodemacs`: prints `OS <boot> <mac> <bus>` plus the BMC NIC MACs.
* **bios** — `nodebios [status|base|pending]`: `status` reads current BIOS
  settings (vendor noise keys filtered out); `base`/`pending` are the
  Lenovo/SMC write paths.
* **vm** — `nodevm [status|enable|mount|umount|insert|eject|stop|media|lic]
  [image]`: virtual media (mount/eject an ISO). NVIDIA/AMI default (discovers
  `BMC_ID`/VMI dynamically, uses `AMIVirtualMedia.EnableRMedia` +
  `VirtualMedia.InsertMedia`); Compal/Netweb/Aivres/SMC/Dell implement their
  own (Dell goes through `racadm remoteimage`). `image` accepts `smb://`
  (Compal/Netweb/GBXX), `http://` (SMC/Dell) or `nfs://` (Aivres);
  `--vm-user`/`--vm-password` override the SMB credentials.
  `mount URL` runs the OEM's complete workflow. `insert URL` is an alias.
  AMI, Compal, Netweb and Dell enable remote media before inserting it.
  Aivres mounts the NFS directory and inserts the supplied ISO. Its `eject`
  also requires the ISO URL. SMC inserts directly. Use `status` to inspect media.
  Compal supports an explicit `stop URL` through its stop-media endpoint if
  normal eject fails. Netweb `stop URL` uses standard eject and matches the
  full mounted URL reported by the BMC.

> Note: `nodenetboot` and `nodebios base/pending` **modify** the BMC/BIOS;
> `nodevm`'s `enable`/`mount`/`insert`/`eject`/`umount`/`stop` are writes too.

### Console (nodeconsole)

`nodeconsole <noderange> [open|close]` mirrors the legacy `nodeconsole`:

* `open` (default) runs `sol deactivate` then `sol activate usesolkeepalive`
  (cipher 17, `-N 5 -R 5`), taking over one node's SOL on the terminal; with
  several nodes only the first is used.
* `close` runs `sol deactivate` in parallel and prints `<node>: <line>` (nothing
  is printed for empty output).
* `--console` (alias `--interactive`) enables a pty session: JSON Lines log
  (`--log-file` / `--log-dir`, default `logs/`), reconnect on drop
  (`--reconnect-attempts`/`--reconnect-delay`), and `Ctrl-]` to detach locally.
  It requires exactly one node and a TTY on stdin/stdout, else it exits rc 2.

### List, then check only the reachable nodes

```sh
./nodelist                             # what nodes exist (aggregated)
reachable=$(./nodeping --long cp[01-18] \
            | awk -F: '/pingable/{print $1}' | paste -sd, -)
./nodetemp "$reachable" --errors-only
```

### Options

| Option | Meaning |
| --- | --- |
| `--inventory FILE` | inventory file (default `inventory.ini` or `$NODETOOLS_INVENTORY`) |
| `--group G` | select an inventory group (repeatable) |
| `--all` | select every host |
| `--summary` | one summary line per node (sensor commands only) |
| `--errors-only` | print only non-OK lines |
| `--jobs N` | parallel workers (default 18) |
| `--timeout S` | per-request timeout (default 10) |
| `--action A` | control action (alias for the positional action) |
| `--target C` / `--tolerance D` | `nodeinlet` expected inlet temperature (default 25) and allowed band (default 5, i.e. 20-30 C) |
| `--user` / `--password` | override the BMC credentials |
| `--ipmi-env` | pass the IPMI password via `IPMI_PASSWORD` (`ipmitool -E`) |
| `--console` / `--interactive` | `nodeconsole` pty session (single node) |
| `--cipher N` | `ipmitool -C` cipher (default 17) |
| `--log-file` / `--log-dir` / `--no-log` | session log path/dir/off |
| `--reconnect` / `--no-reconnect` | reconnect after a drop (`--console`: on) |
| `--reconnect-attempts` / `--reconnect-delay` | tries/interval (default 3 / 5s) |
| `--no-cache` | skip the scan-map cache |
| `-x`, `--debug` | verbose logging |

## Inventory

Ansible-style INI; see `inventory.example.ini`. Each host carries its BMC
address via `host=`. Credentials are role-scoped and resolved with precedence
`[all:vars] < group :vars < per-host inline`:

| Role | Credential variables |
| --- | --- |
| compute | `bmc_user` / `bmc_password` |
| nvswitch | `nvswitch_bmc_user` / `nvswitch_bmc_password` |
| powershelf | `powershelf_bmc_user` / `powershelf_bmc_password` |

One OEM can have multiple platforms. Use `platform=hgx` for B200/B300,
`platform=nvl` for GB200/GB300, and `oem=dell` to identify the manufacturer.
The example inventory defines `dell_hgx` and `dell_nvl` groups with `role=compute`.
Use `role=nvswitch` or `role=powershelf` for the corresponding devices. The three
built-in role groups still infer their roles. For hosts in multiple groups,
later memberships take precedence among group variables. Inline settings take precedence
over all group variables.

Omit `platform` or `oem` to detect them from the BMC. Run
`./nodetools inventory show -i inventory.ini` to see the configured values.
`auto` indicates automatic detection. Run `./nodevm --group dell_nvl`
to query Dell NVL virtual media. Dell NVL currently provides read-only Redfish
status. Mount and eject require a verified adapter.

Use `generation=gb200` or `gb300` (or `b200` / `b300` for HGX) to override
BMC model detection. It is optional. Set the OEM per device, for example
`oem=nvidia` for an NVSwitch or `oem=liteon` for a power shelf. On a shared BMC,
`system_id` selects the power system, `manager_id` selects the log manager, and
`chassis_id` scopes chassis and sensor queries. Firmware inventory is BMC-wide.
Unmapped infrastructure uses read-only Redfish discovery. Writes require a
verified adapter.

The `[nodehealth:defaults]` section sets run defaults: `jobs`, `timeout`
(per request) and `node_timeout` (whole-node request budget including identification, default 30 seconds).
Use `--node-timeout S` to override `node_timeout`; once it is spent a slow BMC is abandoned
instead of hanging the run. `scan` ignores it (it is a long task by design).

Generate a starter file from a `DeviceType Hostname BMC_IP` hosts.list:

```sh
./nodetools inventory init --from hosts.list -o inventory.ini
```

`inventory.ini` contains real addresses/credentials and is git-ignored.

Inventory commands list firmware by default. Select a view for hardware identifiers, GPU serial numbers, or BMC tasks.

```sh
./nodeinventory HGX390 --details
./nodeinventory HGX390 --gpu
./nodeinventory HGX390 --tasks
```

On Supermicro and Aivres/KAYTUS, `nodebios NODE --set KEY=VALUE` stages
a pending BIOS setting. Use `Group.Attribute=value` for nested KR9288 settings.
Check attribute names and types with `nodebios NODE`. Schedule the reboot separately.

On Supermicro, `nodenetboot NODE` reads the current order and `nodenetboot NODE pxe`
stages network first in the persistent order, retaining the other entries.
On KR9288, `pxe` stages a one-time override. Specify the target explicitly for writes.

## Output

Sensor lines are `<node>: <field>,<field>,...`, matching the legacy scripts so
`grep -v ,OK` still works:

```
<node>: <id>,<name>,<speed>,<health>     # fan
<node>: <id>,<health>,<reading>          # psu, temp
```

Example:

```
vdc1-pro2-t10-gpu-cp-01: Fan_1,Front_Fan_1,8500,OK
vdc1-pro2-t10-gpu-cp-01: PSU_1,OK,1318.8
```

`--summary` prints `<node>: <CAP>,<role>,total=N,OK=N,nonOK=N`.

Info commands do not use the CSV/summary model; they print `<node>: <line>`
verbatim (eventlog tags BMC/HMC with `[BMC]`/`[HMC]`), see "Info commands".

## Scanning and caching

A GB200 BMC fans out over dozens of chassis, and which chassis holds what is
vendor-specific and not always advertised (Wistron exposes a `Sensors`
collection that its chassis detail never lists), so `scan` probes the
collection endpoints directly and records, per chassis, how many
`Sensors`/`Fans`/`PowerSupplies`/`Temperatures` it exposes plus whether the
legacy `Thermal`/`Power` objects exist. The map is keyed **by machine type**
(vendor + manufacturer + model) under
`~/.cache/nodetools/scan/<vendor>__<model>.json`, so one scanned node of a type
serves every node of that type.

`fan`/`psu`/`temp` read that map and only walk the chassis that can answer
(measurements: compute `fan` 0.9 s vs 11 s live, `psu` 8 s vs 22 s, `temp`
7 s vs 17 s). Without a map they fall back to live discovery, so nothing is
required up front. Re-scan any one node of a type to refresh it after a change,
or `scan --clear` to drop that type's template.

## How collection works

Each capability tries a ladder of backends and returns the first that yields
data, so a single build covers every ODM:

* **fan** — `ThermalSubsystem/Fans` → `Thermal.Fans` → `Sensors` (name filter)
  → IPMI `sdr type Fan`.
* **psu** — `PowerSubsystem/PowerSupplies` → (powershelf) Delta
  `AllSensors` → `Sensors` → IPMI `sdr type Power Supply`.
* **temp** — `Thermal.Temperatures` → `ThermalSubsystem/ThermalMetrics` →
  `TelemetryService/MetricReports` → `Sensors` → IPMI `sdr type Temperature`.

Hardware identification tracks platform, OEM and role independently. HGX covers B200/B300 and NVL covers GB200/GB300. Each platform can have multiple OEMs. NVL compute trays, NVSwitches and power shelves keep their inventory roles. `scan` reports all three dimensions.

Inventory host entries or group variables can override detection with `platform=hgx` / `platform=nvl` and values such as `oem=aivres` or `oem=compal`. Detection otherwise uses BMC identity and can use the hostname for the platform. OEM dispatch also works without a scan template. `--no-cache` skips the template but still reads BMC identity.

## Development

See [`docs/DEVELOPMENT.en.md`](docs/DEVELOPMENT.en.md) (or the Chinese
[`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)) for the architecture, module
responsibilities, the collection-ladder design, and step-by-step guides for
adding a capability, an ODM, or a transport.

```sh
python3 -m pytest
```
