# nodetools

[English](README.en.md) | 中文

一个统一的 Python 工具，用 Redfish 和 IPMI 查看 NVIDIA GB200/GB300 节点。它把按 OEM
分叉的 `node*`/`nvsw*`/`ps*` shell 脚本收敛成一套 `node*` 风格命令族：**前缀定角色**
（compute tray / NVSwitch / power shelf），**后缀定对象**。

当前已实现三组对象：

- 传感器：**fan / psu / temp**（三种角色各一套），另加 **inlet**（入风口温度检查，仅 compute）；
- 信息：**power / eventlog / inventory**（三种角色各一套）；
- 启动：**boot / bootorder / netboot / macs / bios**（仅 compute，见下文）。

命令本身的输出固定为英文（字段、状态、日志行都不翻译）；只有文档提供中英双语。

## 快速开始

`scan` **不是必须的**：`nodelist`/`nodeping` 以及所有 `node*`/`nvsw*`/`ps*` 命令
都能直接运行。`scan` 只是**给 `fan`/`psu`/`temp` 提速**（按机型缓存 Redfish 映射，
取一台做模版即可），缺省时这些命令回退到实时发现；`power`/`eventlog`/`inventory`
根本不读该映射。

推荐顺序（第 0 步一次性，第 2 步可选）：

```sh
# 0) 生成 inventory（一次性）。hosts.list 每行是 `DeviceType Hostname BMC_IP`
./nodetools inventory init --from hosts.list -o inventory.ini

# 1) 先看有什么、哪些 BMC 可达（无需先 scan）
./nodelist                      # 按角色聚合的节点列表
./nodeping --long               # 逐节点 BMC 可达性

# 2) 可选：扫描一次并缓存每种机型的 Redfish 映射（fan/psu/temp 提速）
#    同机型取一台做模版，其余同机型节点自动复用
./nodetools scan cp01

# 3) 开始用
./nodetemp  cp[01-18] --summary # 每个节点一行汇总
./nodepower cp01                # 电源状态（读取）
./nodeeventlog cp01             # HMC 事件日志
./nodetools nodefan cp01 max    # 也可写成 `nodetools <command>`
```

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

计算节点 `nodefan` 默认每节点一行列出转速。数字单位为 RPM，百分比带 `%`。`check` 逐风扇显示健康状态，配合 `--errors-only` 可只看异常。`auto` 和 `max` 由 OEM 适配器处理，Netweb 的 `low` 和 `opt` 分别固定为 50% 和 80% PWM。用各工具的 `--help` 查看动作含义，通用参数见下文。

`nodemon` 只读采集进风温度、整机功耗和各 PSU 的输入读数。整机和 PSU 功耗统一以 W 为单位，只在列名标单位。优先使用 BMC 直接报告的输入功耗，换算所需读数不足时填 NA。JSON 用 estimated 标记换算值。默认采样一次，CSV 每节点一行，采完立即输出。缺测填 NA，额外槽位进入 PSU_OTHER_W。用 --count 和 --interval 指定轮数和轮间等待（默认 5 秒），用 --format json 输出 JSON Lines。风扇转速用 nodefan 查询。

```bash
./nodemon 'HGX[390,474]'
./nodemon 'HGX[390,474]' --interval 10 --count 6 --format json
```

只上报电流的单相 PSU 可以用现场电压和功率因数估算。示例中的 230 V 和 0.95 要换成现场参数。

```bash
./nodemon HGX390 --psu-voltage 230 --psu-power-factor 0.95
```

也可在 inventory 的主机或组变量中设置 PSU 输入电压 `psu_input_volts` 和功率因数 `psu_input_power_factor`。命令行参数覆盖 inventory。实测输入电压和功率因数优先。


PSU 默认输出 `ID,Health,Reading`。用 `nodepsu --details HGX390` 查看名称、厂商、型号、固件、健康状态、输入功率（W）和输出功率（W）。未提供的字段显示 `NA`。
只读传感器查询按 Ctrl-C 立即退出，返回退出码 130。

```
# 传感器
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

# 启动（仅 compute）
nodeboot      <noderange> [pxe|disk|bios|cdrom]
nodebootorder <noderange>
nodenetboot   <noderange> [status|mac|net]
nodemacs      <noderange>
nodebios      <noderange> [status|base|pending]
nodevm        <noderange> [status|enable|mount|umount|insert|eject|stop|media|lic] [image]

# 控制台（仅 compute）
nodeconsole   <noderange> [open|close]        # SOL 串口控制台

# 通用
nodelist [selector] [--long]
nodeping [selector] [--long] [--ping-timeout S]
nodetools scan <selector> [--clear]
nodetools inventory init [--from hosts.list] [-o inventory.ini]
nodetools inventory show [-i inventory.ini]
```

任何命令也可写成 `nodetools <command> ...`；旧短名 `list`/`ping` 是
`nodelist`/`nodeping` 的别名。

**不带任何参数运行某个命令（如 `./nodepower`）会打印该命令的用法并以非 0 退出**，
而不是回退查询整个角色分组。要查询整个角色请显式加 `--all`（或 `--group G`）。

* `noderange` 是 hostlist 表达式（`cp[01-18]`）、inventory 组名或主机名。它会被
  收窄到前缀所对应的角色。也可改用 `--group G` / `--all`（`--all` 也会收窄到该
  命令的角色）。
* 传感器默认读取状态。风扇的调速动作由 OEM 决定；PSU 查询为只读。设备电源动作通过 `power` 命令执行。
* 传感器输出为逗号分隔字段；信息命令原样逐行输出 `<node>: <line>`（见下文）。
* `nodetemp` 逐传感器显示 `ID,名称,状态,读数`。
* `nodeinlet` 默认每台一行，例如 `HGX390: 22`。用 `check` 查看阈值判定。
* `nodeinlet`（仅 compute）取机箱进风温度（GB200 为 `Chassis_0_Front_IO_Temp_0`，
  其他 ODM 为 `Inlet_Temp`/`Ambient_Temp` 等），排除处理器模块/PDB 等内部进风点；
  读数按目标值判定 `OK`/`HIGH`/`LOW`（默认目标 25℃、容差 ±5，即 20–30℃ 为 OK，
  可用 `--target` / `--tolerance` 调整）。进风传感器只在一个 chassis 上，`nodeinlet`
  直接按优先顺序探测 chassis 并在首个命中处停止（不再遍历全部 chassis，也不读 scan
  模板），单个节点约一次 Redfish 请求。NVSwitch tray 与 power shelf 的 BMC 不暴露机箱进风传感器，
  故不生成 `nvswinlet`/`psinlet`。

### 信息命令（power / eventlog / inventory）

这组命令**每一条对应一个旧脚本**，输出格式与旧脚本逐字对齐：

* **power** — `nodepower` 走 ODM 策略（默认 NVIDIA GBXX：IPMI `power <action>`；
  Dell 无覆盖即回退 IPMI，Lenovo/SMC 用 `Systems/1` 的 Redfish）。
  `nvswpower`/`pspower` 是 NVIDIA 专有基础设施，默认固定走 Redfish 复位
  （`Systems/System_0` 的 `ComputerSystem.Reset`）；Compal 的 NVSwitch 走
  `Systems/Self` 且 `reseat` 为 `FullPowerCycle`。
  `nodepower status` 将 Redfish 和 IPMI 的状态统一为 `On`、`Off` 等值，状态缺失时显示 `N/A`。
* **eventlog** — `nodeeventlog` 默认读 `[HMC]`（`HGX_Baseboard_0` EventLog），
  `bmc` 读 `[BMC]`（IPMI SEL），`clear` 清 BMC/HMC 日志；各 OEM 覆盖各自的 Sol/
  PlatformLog/BIOS 集合。
* **inventory** — 读 `UpdateService/FirmwareInventory`（compute 行 `name:Id Version`，
  nvsw/ps 行 `name: Id Version`；AMI/BYD/GIGABYTE/DGX/Lenovo 各自覆盖）。

### 启动命令（boot / bootorder / netboot / macs / bios / vm）

这组只对 **compute tray** 有意义（`nvsw*`/`ps*` 无此对象）。默认 NVIDIA GBXX 实现
与旧脚本逐字对齐，各 ODM 覆盖各自的端点（端点缺失时回退基类）：

* **boot** — `nodeboot [pxe|disk|bios|cdrom]`：IPMI `chassis bootdev <action>`（默认
  `pxe`）。无 ODM 变体。
* **bootorder** — `nodebootorder`：读 `BootOptions` 与 `Systems/<id>` 的
  `Boot/BootOrder`，逐项打印 `Id`/`DisplayName`/`UefiDevicePath`。
* **netboot** — `nodenetboot [status|mac|net]`：配置一次性/连续 PXE 启动覆盖
  （GBXX 默认写 Continuous/UEFI/Pxe 并改 BootOrder）。Dell/Lenovo/SMC 各自实现。
* **macs** — `nodemacs`：打印 `OS <boot> <mac> <bus>` 与 BMC 网口 MAC。
* **bios** — `nodebios [status|base|pending]`：`status` 读当前 BIOS 设置（过滤掉
  厂商噪声键）；`base`/`pending` 是 Lenovo/SMC 的写入路径。
* **vm** — `nodevm [status|enable|mount|umount|insert|eject|stop|media|lic] [image]`：
  虚拟媒体（挂载/弹出 ISO）。默认 NVIDIA/AMI（动态发现 `BMC_ID`/VMI，走
  `AMIVirtualMedia.EnableRMedia` + `VirtualMedia.InsertMedia`）；Compal/Netweb/
  Aivres/SMC/Dell 各自实现（Dell 走 `racadm remoteimage`）。`image` 支持 `smb://`
  （Compal/Netweb/GBXX）、`http://`（SMC/Dell）、`nfs://`（Aivres），`--vm-user`/
  `--vm-password` 覆盖 SMB 凭据。
  `mount URL` 是统一挂载入口，`insert URL` 与它等价。AMI、Compal、Netweb 和 Dell
  先启用远程媒体再插入。Aivres 传 NFS ISO URL，先挂载其目录再插入该 ISO；
  `eject` 也须指定该 URL。SMC 直接插入。`status` 查询当前状态。
  Compal 正常弹出失败后可显式执行 `stop URL`，调用专用停止接口。Netweb 的
  `stop URL` 使用标准弹出接口，按 BMC 报告的完整 URL 匹配目标。

> 注意：`nodenetboot` 与 `nodebios base/pending` 会**修改** BMC/BIOS，属于写操作；
> `nodevm` 的 `enable`/`mount`/`insert`/`eject`/`umount`/`stop` 也是写操作。

### 控制台（nodeconsole）

`nodeconsole <noderange> [open|close]` 逐字复刻旧 `nodeconsole`：

* `open`（默认）先 `sol deactivate` 再 `sol activate usesolkeepalive`
  （cipher 17，`-N 5 -R 5`），终端里直接接管一个节点的 SOL；多选时只用第一个。
* `close` 对所选节点并行 `sol deactivate`，逐行输出 `<node>: <line>`（无输出不打印）。
* `--console`（别名 `--interactive`）启用 pty 交互会话：JSON Lines 记录（`--log-file`
  / `--log-dir`，默认 `logs/`）、断线重连（`--reconnect-attempts`/`--reconnect-delay`）、
  `Ctrl-]` 本地断开。要求恰好一个节点且 stdin/stdout 为 TTY，否则以 rc 2 退出。

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
| `--target C` / `--tolerance D` | `nodeinlet` 的期望进风温度（默认 25）与允许偏差（默认 5，即 20–30℃） |
| `--user` / `--password` | 覆盖 BMC 凭据 |
| `--ipmi-env` | 经 `IPMI_PASSWORD` 传 IPMI 密码（`ipmitool -E`） |
| `--console` / `--interactive` | `nodeconsole` 的 pty 交互会话（单节点） |
| `--cipher N` | `ipmitool -C` cipher（默认 17） |
| `--log-file` / `--log-dir` / `--no-log` | 交互会话日志路径/目录/关闭 |
| `--reconnect` / `--no-reconnect` | 断线后是否重连（`--console` 默认重连） |
| `--reconnect-attempts` / `--reconnect-delay` | 重连次数/间隔（默认 3 / 5s） |
| `--no-cache` | 跳过扫描映射缓存 |
| `-x`, `--debug` | 详细日志 |

## Inventory

Ansible 风格 INI，见 `inventory.example.ini`。每台主机用 `host=` 带 BMC 地址。
凭据按角色划分，优先级 `[all:vars] < group:vars < 行内 key=value`：

| 角色 | 凭据变量 |
| --- | --- |
| compute | `bmc_user` / `bmc_password` |
| nvswitch | `nvswitch_bmc_user` / `nvswitch_bmc_password` |
| powershelf | `powershelf_bmc_user` / `powershelf_bmc_password` |

同一厂商可以有不同平台。`platform=hgx` 表示 B200/B300，`platform=nvl`
表示 GB200/GB300，`oem=dell` 指定厂商。示例 inventory 包含 `dell_hgx` 和
`dell_nvl` 两组，用 `role=compute` 声明角色。角色也可以设为 `nvswitch`
或 `powershelf`。原有的三个同名角色组仍可自动识别角色。机器在多个组时，
后出现的组成员关系优先，行内设置优先于所有组。

不指定 `platform` 或 `oem` 时，由 BMC 自动识别。用
`./nodetools inventory show -i inventory.ini` 查看配置值，`auto` 表示交给自动识别。
用 `./nodevm --group dell_nvl` 查询 Dell NVL 虚拟介质。Dell NVL 的
挂载和推出需要已验证的机型适配，目前提供 Redfish 只读查询。

`generation=gb200` / `gb300`（HGX 可填 `b200` / `b300`）用于覆盖 BMC
型号识别，型号不明确时可省略。NVSwitch 和电源架按设备本身设置 OEM，
例如 `oem=nvidia` 和 `oem=liteon`。共享 BMC 时，可用 `system_id` 定位
电源控制系统，`manager_id` 定位日志管理器，`chassis_id` 限定机箱和传感器查询。
固件库存仍按 BMC 列出。未适配的机型尝试 Redfish 只读发现，写操作需要已验证的适配器。

`[nodehealth:defaults]` 设置运行默认值：`jobs`、`timeout`（单请求）、
`node_timeout`（含身份识别的整节点请求预算，默认 30 秒）。可用 `--node-timeout S` 覆盖 `node_timeout`；用完后
慢 BMC 会被放弃，而不是拖住整批。`scan` 不使用它（本身就是长任务）。

从 `DeviceType Hostname BMC_IP` 的 hosts.list 生成初始文件：

```sh
./nodetools inventory init --from hosts.list -o inventory.ini
```

`inventory.ini` 含真实地址/凭据，已被 git 忽略。

固件列表是库存命令的默认输出。需要查硬件标识、GPU 序列号或最近的 BMC 任务时，可以指定查询类型。

```sh
./nodeinventory HGX390 --details
./nodeinventory HGX390 --gpu
./nodeinventory HGX390 --tasks
```

在 Supermicro 和 Aivres/KAYTUS 上，`nodebios NODE --set KEY=VALUE`
提交待生效设置。KR9288 的嵌套设置使用 `组.属性=值`。先用 `nodebios NODE`
确认属性名称和类型。提交后由操作者单独安排重启。

Supermicro 的 `nodenetboot NODE` 查询当前顺序，`nodenetboot NODE pxe`
把网络启动移到持久顺序前面并保留其他启动项。KR9288 的 `pxe` 是一次性启动覆盖。
两者均需明确指定目标后才能提交。

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
`Temperatures`，以及旧式 `Thermal`/`Power` 对象是否存在。映射**按机型**
（厂商+制造商+型号）存到 `~/.cache/nodetools/scan/<vendor>__<model>.json`，
所以同机型只需扫一台做模版，其余节点读同一份。

`fan`/`psu`/`temp` 读该映射，只遍历"能回答"的 chassis（实测 compute：`fan` 0.9s
vs 实时 11s，`psu` 8s vs 22s，`temp` 7s vs 17s）。没有映射时回退到实时发现，所以
事先什么都不做也能用。机器变动后重跑 `scan` 刷新（同机型扫任意一台即可），
`scan --clear` 删除该机型的模版。

## 采集原理

每个能力按后端阶梯依次尝试，返回第一个有数据的后端，因此一套实现覆盖所有 ODM：

* **fan** — `ThermalSubsystem/Fans` → `Thermal.Fans` → `Sensors`（名字过滤）→ IPMI
  `sdr type Fan`。
* **psu** — `PowerSubsystem/PowerSupplies` →（powershelf）Delta `AllSensors` →
  `Sensors` → IPMI `sdr type Power Supply`。
* **temp** — `Thermal.Temperatures` → `ThermalSubsystem/ThermalMetrics` →
  `TelemetryService/MetricReports` → `Sensors` → IPMI `sdr type Temperature`。

硬件识别分为三项：平台、OEM 和角色。HGX 对应 B200/B300，NVL 对应 GB200/GB300。两类平台都独立识别 OEM；NVL 的计算托盘、NVSwitch 和电源架仍按库存角色选择命令。`scan` 输出这三项。

库存可在主机行或组变量中设置 `platform=hgx` / `platform=nvl` 和 `oem=aivres` / `oem=compal` 等覆盖。省略时从 BMC 身份识别，平台还可根据主机名判断。OEM 适配器也在未扫描时选择。`--no-cache` 跳过扫描映射，仍读取 BMC 身份。

## 开发

架构、模块职责、采集阶梯设计，以及新增对象/ODM/传输的分步指南，见
[`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)。

```sh
python3 -m pytest
```
