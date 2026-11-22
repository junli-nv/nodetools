# nodetools

A single Python tool for inspecting NVIDIA GB200/GB300 nodes over Redfish and
IPMI. It replaces the collection of per-OEM `node*`/`ps*` shell scripts with one
capability-oriented CLI.

This phase implements three capabilities: **fan**, **psu** and **temp**.

## Install / run

No installation is required — run the launcher in place:

```sh
./nodetools fan vdc1-pro2-t10-gpu-cp-[01-18]
```

Optionally install the console script:

```sh
pip install .
nodetools temp --group compute --summary
```

## Commands

```
nodetools <cap> <selector> [options]
nodetools list [selector] [--long]
nodetools ping <selector> [--ping-timeout S]
nodetools inventory init [--from hosts.list] [-o inventory.ini]
nodetools inventory show [-i inventory.ini]
```

* `cap` is one of `fan`, `psu`, `temp`, `all` (fan+psu+temp) or `oem`.
* `selector` is a hostlist expression (`node[01-18]`), an inventory group name,
  or one or more hostnames. Combine with `--group G` and/or `--all`.
* `list` prints `host: role` (all hosts if no selector); `--long` adds the BMC
  address. A `# N hosts: ...` count line goes to stderr.
* `ping` ICMP-checks each selected host's BMC and prints
  `host: BMC pingable|unreachable` (or `No BMC IP found`).

### List, then check only the reachable nodes

```sh
./nodetools list                       # what nodes exist
reachable=$(./nodetools ping vdc1-pro2-t10-gpu-cp-[01-18] \
            | awk -F: '/pingable/{print $1}' | paste -sd, -)
./nodetools all "$reachable" --errors-only
```

### Options

| Option | Meaning |
| --- | --- |
| `--inventory FILE` | inventory file (default `inventory.ini` or `$NODETOOLS_INVENTORY`) |
| `--group G` | select an inventory group (repeatable) |
| `--all` | select every host |
| `--no-expand` | do not auto-add same-row `-pwr-`/`-nvl-` hosts |
| `--summary` | one summary line per node |
| `--errors-only` | print only non-OK lines |
| `--jobs N` | parallel workers (default 18) |
| `--timeout S` | per-request timeout (default 10) |
| `--action A` | control action: fan `auto`/`max`, psu `on`/`off`/`cycle` |
| `--user` / `--password` | override the BMC credentials |
| `--ipmi-env` | pass the IPMI password via `IPMI_PASSWORD` (`ipmitool -E`) |
| `--no-cache` | ignore the cached ODM classification |
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

Generate a starter file from a `DeviceType Hostname BMC_IP` hosts.list:

```sh
./nodetools inventory init --from hosts.list -o inventory.ini
```

`inventory.ini` contains real addresses/credentials and is git-ignored.

## Output

```
<node>: <CAP>,<role>,<id>,<name>,<value>,<health>
```

Example:

```
vdc1-pro2-t10-gpu-cp-01: FAN,compute,Fan_1,Front_Fan_1,8500,OK
```

This stays compatible with `grep -v ,OK`. `--summary` prints
`<node>: <CAP>,<role>,total=N,OK=N,nonOK=N`.

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
control actions.

## Development

See [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) for the architecture, module
responsibilities, the collection-ladder design, and step-by-step guides for
adding a capability, an ODM, or a transport.

```sh
python3 -m pytest
```
