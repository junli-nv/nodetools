# nodetools

English | [中文](README.md)

A single Python tool for inspecting NVIDIA GB200/GB300 nodes over Redfish and
IPMI. It replaces the per-OEM `node*`/`nvsw*`/`ps*` shell scripts with one
`node*`-style command family, where the prefix names the role (compute tray,
NVSwitch, power shelf) and the suffix names the subject.

Three subject groups are implemented:

- sensors: **fan / psu / temp** (one set per role);
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

# 2) optional: scan once to cache each BMC's Redfish map (speeds up fan/psu/temp)
./nodetools scan --all

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

```
# sensors
nodefan  <noderange> [status|check|auto|max]
nodepsu  <noderange> [status|on|off|cycle]
nodetemp <noderange>
nvswfan  <noderange> [status|check|auto|max]
nvswpsu  <noderange> [status|on|off|cycle]
nvswtemp <noderange>
psfan    <noderange> [status|check|auto|max]
pspsu    <noderange> [status|on|off|cycle]
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
nodeboot      <noderange> [pxe|disk|bios]
nodebootorder <noderange>
nodenetboot   <noderange> [status|mac|net]
nodemacs      <noderange>
nodebios      <noderange> [status|base|pending]

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
* `action` defaults to a read (`status`/`check`); `auto`/`max` (fan) and
  `on`/`off`/`cycle` (psu) issue a control action. `power` additionally accepts
  `reset`/`reseat`.
* Sensor output is comma-separated fields; info commands print verbatim
  `<node>: <line>` lines (see below).

### Info commands (power / eventlog / inventory)

Each of these commands corresponds to **one legacy script** and matches its
output verbatim:

* **power** — `nodepower` dispatches by ODM (NVIDIA GBXX default: IPMI
  `power <action>`; Dell has no override so it stays on IPMI, Lenovo/SMC use the
  `Systems/1` Redfish resource). `nvswpower`/`pspower` are NVIDIA-only
  infrastructure and always use the Redfish reset
  (`ComputerSystem.Reset`).
* **eventlog** — `nodeeventlog` reads `[HMC]` by default
  (`HGX_Baseboard_0` EventLog), `bmc` reads `[BMC]` (IPMI SEL), `clear` clears
  the BMC/HMC logs; each OEM overrides its own Sel/PlatformLog/BIOS collection.
* **inventory** — reads `UpdateService/FirmwareInventory` (compute line
  `name:Id Version`, nvsw/ps line `name: Id Version`; AMI/BYD/GIGABYTE/DGX/
  Lenovo override as their scripts do).

### Boot commands (boot / bootorder / netboot / macs / bios)

These only make sense for **compute trays** (`nvsw*`/`ps*` have no such
subject). The default NVIDIA GBXX implementation matches the legacy scripts
verbatim and each ODM overrides its own endpoints (falling back to the base
class when an endpoint is absent):

* **boot** — `nodeboot [pxe|disk|bios]`: IPMI `chassis bootdev <action>`
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

> Note: `nodenetboot` and `nodebios base/pending` **modify** the BMC/BIOS.

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
| `--user` / `--password` | override the BMC credentials |
| `--ipmi-env` | pass the IPMI password via `IPMI_PASSWORD` (`ipmitool -E`) |
| `--no-cache` | ignore the cached ODM and scan maps |
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

The `[nodehealth:defaults]` section sets run defaults: `jobs`, `timeout`
(per request) and `node_timeout` (whole-node wall-clock budget, default 30s).
`node_timeout` has no CLI flag; once it is spent a slow BMC is abandoned
instead of hanging the run. `scan` ignores it (it is a long task by design).

Generate a starter file from a `DeviceType Hostname BMC_IP` hosts.list:

```sh
./nodetools inventory init --from hosts.list -o inventory.ini
```

`inventory.ini` contains real addresses/credentials and is git-ignored.

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
legacy `Thermal`/`Power` objects exist. The map is written per BMC under
`~/.cache/nodetools/scan/<ip>.json`.

`fan`/`psu`/`temp` read that map and only walk the chassis that can answer
(measurements: compute `fan` 0.9 s vs 11 s live, `psu` 8 s vs 22 s, `temp`
7 s vs 17 s). Without a map they fall back to live discovery, so nothing is
required up front. Re-run `scan` to refresh a machine that changed, or
`scan --clear` to drop the entry.

## How collection works

Each capability tries a ladder of backends and returns the first that yields
data, so a single build covers every ODM:

* **fan** — `ThermalSubsystem/Fans` → `Thermal.Fans` → `Sensors` (name filter)
  → IPMI `sdr type Fan`.
* **psu** — `PowerSubsystem/PowerSupplies` → (powershelf) Delta
  `AllSensors` → `Sensors` → IPMI `sdr type Power Supply`.
* **temp** — `Thermal.Temperatures` → `ThermalSubsystem/ThermalMetrics` →
  `TelemetryService/MetricReports` → `Sensors` → IPMI `sdr type Temperature`.

The ODM (Dell/Lenovo/Supermicro/AMI/BYD/GIGABYTE/DGX/NVIDIA/Wistron/Inventec) is
detected from the BMC's Redfish identity and cached under
`~/.cache/nodetools/oem/<ip>`. ODM modules additionally provide vendor-specific
control actions and power/eventlog/inventory overrides, falling back to the
base class (GBXX convention) when an endpoint is absent.

## Development

See [`docs/DEVELOPMENT.en.md`](docs/DEVELOPMENT.en.md) (or the Chinese
[`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)) for the architecture, module
responsibilities, the collection-ladder design, and step-by-step guides for
adding a capability, an ODM, or a transport.

```sh
python3 -m pytest
```
