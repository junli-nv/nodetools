# nodetools

[English](README.en.md) | 中文

一个统一的 Python 工具，用 Redfish 和 IPMI 查看 NVIDIA GB200/GB300 节点。它把按 OEM
分叉的 `node*`/`nvsw*`/`ps*` shell 脚本收敛成一套 `node*` 风格命令族：**前缀定角色**
（compute tray / NVSwitch / power shelf），**后缀定对象**。

当前已实现两组对象：

- 传感器：**fan / psu / temp**（三种角色各一套）；
- 信息：**power / eventlog / inventory**（三种角色各一套）。

命令本身的输出固定为英文（字段、状态、日志行都不翻译）；只有文档提供中英双语。

## 安装 / 运行

无需安装，直接用仓库里的启动器：

```sh
./nodefan cp[01-18]
```

也可以安装 console scripts（每个命令一个）：

```sh
pip install .
nodetemp --group compute --summary
```

## 命令

命令名 = 前缀 + 对象，与旧脚本一致：`node` = compute tray，`nvsw` = NVSwitch，
`ps` = power shelf。GBXX 三个前缀都要；BXX 只用 `node*`。

```
# 传感器
nodefan  <noderange> [status|check|auto|max]
nodepsu  <noderange> [status|on|off|cycle]
nodetemp <noderange>
nvswfan  <noderange> [status|check|auto|max]
nvswpsu  <noderange> [status|on|off|cycle]
nvswtemp <noderange>
psfan    <noderange> [status|check|auto|max]
pspsu    <noderange> [status|on|off|cycle]
pstemp   <noderange>

# 信息
nodepower    <noderange> [status|on|off|cycle|reset|reseat]
nodeeventlog <noderange> [status|bmc|hmc|clear]
nodeinventory <noderange>
nvswpower    <noderange> [status|on|off|cycle|reset|reseat]
nvsweventlog <noderange> [status|clear]
nvswinventory <noderange>
pspower      <noderange> [status|on|off|cycle|reset|reseat]
pseventlog   <noderange> [status|clear]
psinventory  <noderange>

# 通用
nodelist [selector] [--long]
nodeping [selector] [--long] [--ping-timeout S]
nodetools scan <selector> [--clear]
nodetools inventory init [--from hosts.list] [-o inventory.ini]
nodetools inventory show [-i inventory.ini]
```

任何命令也可写成 `nodetools <command> ...`；旧短名 `list`/`ping` 是
`nodelist`/`nodeping` 的别名。

* `noderange` 是 hostlist 表达式（`cp[01-18]`）、inventory 组名或主机名。它会被
  收窄到前缀所对应的角色；不给 noderange 时用整个角色分组。可配合 `--group G` /
  `--all`。
* `action` 默认是读取（`status`/`check`）；`auto`/`max`（fan）与 `on`/`off`/`cycle`
  （psu）会下发控制动作。`power` 额外支持 `reset`/`reseat`。
* 传感器输出为逗号分隔字段；信息命令原样逐行输出 `<node>: <line>`（见下文）。

### 信息命令（power / eventlog / inventory）

这组命令**每一条对应一个旧脚本**，输出格式与旧脚本逐字对齐：

* **power** — `nodepower` 走 ODM 策略（默认 NVIDIA GBXX：IPMI `power <action>`；
  Dell 无覆盖即回退 IPMI，Lenovo/SMC 用 `Systems/1` 的 Redfish）。`nvswpower`/
  `pspower` 是 NVIDIA 专有基础设施，固定走 Redfish 复位（`ComputerSystem.Reset`）。
* **eventlog** — `nodeeventlog` 默认读 `[HMC]`（`HGX_Baseboard_0` EventLog），
  `bmc` 读 `[BMC]`（IPMI SEL），`clear` 清 BMC/HMC 日志；各 OEM 覆盖各自的 Sol/
  PlatformLog/BIOS 集合。
* **inventory** — 读 `UpdateService/FirmwareInventory`（compute 行 `name:Id Version`，
  nvsw/ps 行 `name: Id Version`；AMI/BYD/GIGABYTE/DGX/Lenovo 各自覆盖）。

### 先 list，再只检查可达节点

```sh
./nodelist                             # 有哪些节点（聚合）
reachable=$(./nodeping --long cp[01-18] \
            | awk -F: '/pingable/{print $1}' | paste -sd, -)
./nodetemp "$reachable" --errors-only
```

### 选项

| 选项 | 含义 |
| --- | --- |
| `--inventory FILE` | inventory 文件（默认 `inventory.ini` 或 `$NODETOOLS_INVENTORY`） |
| `--group G` | 选择一个 inventory 组（可重复） |
| `--all` | 选择所有主机 |
| `--summary` | 每节点输出一行汇总（仅传感器命令） |
| `--errors-only` | 只打印非 OK 行 |
| `--jobs N` | 并行 worker 数（默认 18） |
| `--timeout S` | 单请求超时（默认 10） |
| `--action A` | 控制动作（位置参数的别名） |
| `--user` / `--password` | 覆盖 BMC 凭据 |
| `--ipmi-env` | 经 `IPMI_PASSWORD` 传 IPMI 密码（`ipmitool -E`） |
| `--no-cache` | 忽略 ODM 与 scan 缓存 |
| `-x`, `--debug` | 详细日志 |

## Inventory

Ansible 风格 INI，见 `inventory.example.ini`。每台主机用 `host=` 带 BMC 地址。
凭据按角色划分，优先级 `[all:vars] < group:vars < 行内 key=value`：

| 角色 | 凭据变量 |
| --- | --- |
| compute | `bmc_user` / `bmc_password` |
| nvswitch | `nvswitch_bmc_user` / `nvswitch_bmc_password` |
| powershelf | `powershelf_bmc_user` / `powershelf_bmc_password` |

`[nodehealth:defaults]` 设置运行默认值：`jobs`、`timeout`（单请求）、
`node_timeout`（整节点墙钟预算，默认 30s）。`node_timeout` 没有 CLI flag；用完后
慢 BMC 会被放弃，而不是拖住整批。`scan` 不使用它（本身就是长任务）。

从 `DeviceType Hostname BMC_IP` 的 hosts.list 生成初始文件：

```sh
./nodetools inventory init --from hosts.list -o inventory.ini
```

`inventory.ini` 含真实地址/凭据，已被 git 忽略。

## 输出

传感器行是 `<node>: <field>,<field>,...`，与旧脚本一致，`grep -v ,OK` 仍可用：

```
<node>: <id>,<name>,<speed>,<health>     # fan
<node>: <id>,<health>,<reading>          # psu, temp
```

示例：

```
vdc1-pro2-t10-gpu-cp-01: Fan_1,Front_Fan_1,8500,OK
vdc1-pro2-t10-gpu-cp-01: PSU_1,OK,1318.8
```

`--summary` 输出 `<node>: <CAP>,<role>,total=N,OK=N,nonOK=N`。

信息命令不使用 CSV/汇总模型，直接原样打印 `<node>: <line>`（eventlog 的 BMC/HMC
分别带 `[BMC]`/`[HMC]` 前缀），见"信息命令"。

## 扫描与缓存

一个 GB200 BMC 会摊开几十个 chassis，哪个 chassis 有什么**因厂商而异且不一定声明**
（Wistron 暴露了 `Sensors` 集合，但其 chassis detail 从不列出），因此 `scan` 直接探测
集合端点，按 chassis 记录它暴露了多少 `Sensors`/`Fans`/`PowerSupplies`/
`Temperatures`，以及旧式 `Thermal`/`Power` 对象是否存在。映射按 BMC 存到
`~/.cache/nodetools/scan/<ip>.json`。

`fan`/`psu`/`temp` 读该映射，只遍历"能回答"的 chassis（实测 compute：`fan` 0.9s
vs 实时 11s，`psu` 8s vs 22s，`temp` 7s vs 17s）。没有映射时回退到实时发现，所以
事先什么都不做也能用。机器变动后重跑 `scan` 刷新，`scan --clear` 删除条目。

## 采集原理

每个能力按后端阶梯依次尝试，返回第一个有数据的后端，因此一套实现覆盖所有 ODM：

* **fan** — `ThermalSubsystem/Fans` → `Thermal.Fans` → `Sensors`（名字过滤）→ IPMI
  `sdr type Fan`。
* **psu** — `PowerSubsystem/PowerSupplies` →（powershelf）Delta `AllSensors` →
  `Sensors` → IPMI `sdr type Power Supply`。
* **temp** — `Thermal.Temperatures` → `ThermalSubsystem/ThermalMetrics` →
  `TelemetryService/MetricReports` → `Sensors` → IPMI `sdr type Temperature`。

ODM（Dell/Lenovo/Supermicro/AMI/BYD/GIGABYTE/DGX/NVIDIA/Wistron/Inventec）从 BMC 的
Redfish 身份探测并缓存到 `~/.cache/nodetools/oem/<ip>`。ODM 模块另外提供厂商特有的
控制动作以及 power/eventlog/inventory 的覆盖实现，端点缺失时回退到基类（GBXX 约定）。

## 开发

架构、模块职责、采集阶梯设计，以及新增对象/ODM/传输的分步指南，见
[`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)。

```sh
python3 -m pytest
```
