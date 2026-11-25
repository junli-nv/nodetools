# 开发者文档

本文说明 nodetools 的设计思路、模块职责、扩展方式和验证方法，供后续继续开发。

## 1. 背景与目标

原来排查 GB200/GB300 靠一堆按 OEM 分叉的 shell 脚本（`nodefan.*`、`nodepsu.*`、
`nodemon.*`、`pspower.*` …），同一件事在每家 ODM 下都是另一份文件，维护成本高。
nodetools 把它们收敛成一个 **按角色分前缀** 的命令族，命名沿用旧脚本的约定：

- 命令名 = 前缀 + 对象：**前缀定角色**，**对象定主体**；
  - `node*` → compute tray，`nvsw*` → NVSwitch，`ps*` → power shelf；
  - 对象：`fan` / `psu` / `temp`（传感器）与 `power` / `eventlog` / `inventory`（信息）；
  - 例：`nodepsu`、`pspsu`、`nodepower`、`nvswinventory`。GBXX 三个前缀都要；BXX 只用 `node*`。
- 读取路径做成 **回退阶梯**，一套实现覆盖所有 ODM；
- 设备、凭据、角色、ODM 都从 inventory + 运行时探测得到，不再写死在脚本里。

已实现三组对象：**fan / psu / temp**（传感器，三种角色各一套）、
**power / eventlog / inventory**（信息，三种角色各一套）与
**boot / bootorder / netboot / macs / bios**（启动，仅 compute），结构预留了继续加
对象（BMC 信息、按键/复位、IB/SPX 交换机等）。

> 命令本身输出固定英文；文档才分中英双语。

### 接口知识的来源

各 OEM 的 Redfish 端点、控制命令、sensor 命名不是猜的，来自对现有脚本的通读：

- 顶层 `nodefan/nodepsu/nodesensors/nodemon`、`func.sh`（GB200 常量）、`passwd.sh`；
- powershelf / NVSwitch：`psmon`、`pspsu`、`pspower`、`nvswmon`；
- 各 OEM 模板：`oem/{ami,byd,dell,dgx,gb200,gigabyte,lenovo,smc}/`；
- GB300 变体（Wistron/Gigabyte/SMC/Inventec 的 sensor 名与逐 sensor 路由）：
  `nodefan.Wistron`、`nodemon.Wistron`、`nodemon.v2.Wistron`、`nodemon.ipmi.Wistron`、
  `pspower.Wistron`。

> 改接口前，先回去读对应脚本确认字段，不要凭字段名猜测。

## 命令派发

命令名由 **前缀（角色）+ 对象** 组成。传感器命令由 `arguments.SENSOR_COMMANDS`
（`config.ROLE_PREFIXES` × `SUBJECTS`）生成；信息命令由 `arguments.INFO_COMMANDS`
（`config.INFO_SUBJECTS` 给出的 对象→支持角色表 × `ROLE_PREFIXES`）生成，因此
某个对象只在部分角色出现时也能自然表达。两种调用等价：

```sh
./nodefan cp[01-18]           # argv[0] 即命令名，main() 按 basename 直接派发
nodetools nodefan cp[01-18]   # 或作为子命令
```

`nodelist`/`nodeping` 不分角色、覆盖整个 inventory（`list`/`ping` 是别名）。
其中**只有 `nodelist` 无需参数**：不带参数即默认列出全部，且以 0 退出。其余命令
（含 `nodeping`）都需要参数，**不带任何参数**运行（`len(argv)==1`）时 `main()`
打印该命令的完整帮助并以 **0** 退出——这是"给帮助"，不是报错（帮助文本打到
stdout，stderr 为空），也不回退查询整组。角色命令用 `cli._role_hosts` 解析：点名了
noderange 则在解析后再按角色过滤（跨角色的主机被跳过并提示）；改用
`--all`/`--group` 时取该角色分组。

## 2. 架构总览

```
nodetools (入口)
  └─ nodetools_runtime.cli
       ├─ arguments.py   解析参数
       ├─ inventory.py   解析/生成 inventory
       ├─ selection.py   选择器 → 主机列表
       ├─ reach.py       BMC ICMP 可达性检查（list/ping 命令用）
       ├─ scan.py        Redfish 端点扫描 + 缓存（唯一的缓存写入者）
       ├─ console.py      nodeconsole：IPMI SOL（legacy 端口 + pty 交互会话）
       ├─ runtime.py     并行引擎：建 Device、跑能力/控制（只读缓存）
       │    ├─ model.py       角色/ODM 读取（odm_for）+ 分类（classify）
       │    ├─ caps/*.py      传感器收集器（回退阶梯）
       │    ├─ subjects/*.py  信息对象（power/eventlog/inventory，逐行输出）
       │    ├─ oem/{hgx,nvl}/*.py       ODM 差异（附加路径 + 控制动作 + 信息覆盖）
       │    └─ transport/*.py Redfish / IPMI / racadm
       └─ report.py      输出格式化（传感器 CSV/汇总 + 信息逐行）
```

一次调用的数据流：

```
argv
 → Inventory.from_file(inventory.ini)         # 分组 + 变量 + 默认值
 → selection.select(...)                       # 名字/组/模式 → Host[]
 → Runner.run(hosts) / InfoRunner.run(hosts)  (ThreadPoolExecutor)
     每台: 解析角色凭据 → 建 Redfish+Ipmi → Device（附 scan profile，若无缓存则为 None）
           ├─ 传感器 --action: 读 profile 的 ODM → oem.get(odm).fan_control
           ├─ 传感器否则: caps.collector_for(cap)(device) → readings[]
           └─ 信息命令: compute 读 profile 的 ODM → subjects.handler_for(subject)(device, action)
 → report.render(results)                      # 传感器 行/汇总；信息 原样逐行
```

## 3. 模块职责

| 文件 | 职责 | 备注 |
| --- | --- | --- |
| `config.py` | 常量：对象名、默认 jobs/timeout、缓存目录、角色↔分组、命令前缀↔角色、默认 chassis | 调参入口 |
| `errors.py` | 异常层级 | `CapUnsupported` 表示"该 ODM 不支持此动作" |
| `hostlist.py` | 主机名范围展开 | 见第 5 节，带单测 |
| `inventory.py` | inventory 解析 + `hosts.list` 生成器 | 语法见第 5 节 |
| `selection.py` | 选择器解析（组/名字/模式） | 只选你点名的机器，不做关联 |
| `reach.py` | BMC 的 ICMP 可达性检查 | `list`/`ping` 命令用；不涉及凭据 |
| `scan.py` | 探测各集合端点，记录每个 chassis 的能力图 + 设备身份/ODM | **唯一的缓存写入者**；其余命令经 `rung_chassis`/`odm_for` 读 |
| `model.py` | `Device`、`platform_for`、`odm_for` | 根据 BMC 身份和库存覆盖分类 |
| `ladder.py` | "按顺序试，返回第一个非空" | 收集器的核心控制流 |
| `transport/redfish.py` | Redfish `get/get_raw/post/patch` | `verify=False`，连接超时 3s |
| `transport/ipmi.py` | ipmitool 封装，`sdr` / `raw` | 双模式传密码，见第 5 节 |
| `transport/racadm.py` | Dell racadm | 不可用时 `available()` 返回 False |
| `caps/common.py` | Reading 构造、health/speed/num、集合/传感器枚举、`rung_chassis` | 共享工具 |
| `caps/{fan,psu,temp}.py` | 三个传感器能力的回退阶梯 | 见第 4 节 |
| `subjects/common.py` | 信息对象工具：`resource`/`entries`/`log_lines`/`column_t`/`jq`/`post`/`patch` | 逐行输出的共享工具 |
| `subjects/{power,eventlog,inventory}.py` | 三个信息对象的 handler；nvsw 转 ODM 的 `nvswitch_power`、ps 走固定 NVIDIA 路径、compute 转 ODM | 见第 4b 节 |
| `subjects/{boot,bootorder,netboot,macs,bios}.py` | 五个启动对象的 handler（仅 compute），转 `device.odm.<subject>` | 见第 4c 节 |
| `console.py` | `nodeconsole`：`_argv`/`open_one`/`close_all`/`attach`（IPMI SOL，legacy 端口 + pty 交互） | 见第 4d 节 |
| `oem/base.py` | `Oem` 基类（GBXX 参考）+ `power`/`nvswitch_power`/`eventlog`/`firmware_inventory`/`boot`/`bootorder`/`macs`/`bios`/`netboot`/`vm` + `_ipmi_raw` | 端点缺失时 vendor 回退到这里 |
| `oem/{hgx,nvl}/<odm>.py` | 单个 ODM 的控制动作/专用 chassis/信息覆盖 | 读数据主要靠通用阶梯 |
| `runtime.py` | `build_device`（附 scan profile）、`Runner`/`InfoRunner` 并行、`Scanner`、错误隔离 | 每台独立 try/except |
| `report.py` | 传感器行/汇总（按对象选字段顺序）+ 信息逐行（`format_lines`） | 兼容 `grep -v ,OK` |

## 4. 收集阶梯（回退顺序）

每个能力都是"依次尝试，返回第一个有数据的后端"，用 `ladder.first` 实现。
这样即使 ODM 判断错误，只要有一条通用路径通，就能读到数据。

- **fan**：`ThermalSubsystem/Fans`（逐成员取 `.SpeedPercent.SpeedRPM|Reading`）
  → `Thermal.Fans[].Reading` → `Sensors`（名字含 `fan`）→ IPMI `sdr type Fan`。
- **psu**：`PowerSubsystem/PowerSupplies`（取值 `PowerMetrics.InputWatts` 等；
  powershelf 状态取 `Oem.deltaenergysystems.Power` → `On/Off`）
  → (powershelf) Delta `Oem/deltaenergysystems/AllSensors`
  → `Sensors`（名字含 `psu/hsc/pwr`）→ IPMI `sdr type Power Supply`。
- **temp**：`Thermal.Temperatures` → `ThermalSubsystem/ThermalMetrics`
  → `TelemetryService/MetricReports`（按名字过滤 temp 关键词）
  → `Sensors`（名字/`ReadingType` 含 temp）→ IPMI `sdr type Temperature`。

每个阶梯函数都遍历 `chassis_list(device, DEFAULT_CHASSIS)`，即"探测到的 chassis +
`config.DEFAULT_CHASSIS`"，因此 `Chassis_0` / `1` / `System.Embedded.1` /
`DGX` / `PDB_0` / `PowerShelf_0` 都能命中。若存在 scan 缓存，则改走
`rung_chassis(device, key)`：只遍历"该阶梯的 key 在缓存里为非空/存在"的 chassis，
并在缓存表明该端点整体缺失时短路，避免无谓探测（见第 5 节"扫描缓存"）。

**Reading 统一结构**：`{id, name, value, health}`。health 取
`.Status.Health or .Status.State or "Unknown"`；powershelf 特判为 `On/Off`。

## 4b. 信息对象（power / eventlog / inventory）

信息对象是**逐行输出**的：handler 返回一串**完整输出行**（已带 `<node>: `，
eventlog 的 BMC/HMC 带 `[BMC]`/`[HMC]` 前缀），`report.render` 检测到结果里有
`lines` 就原样打印，不再走 CSV/汇总模型（见 `report.format_lines`）。这样做的目的是
与旧脚本逐字对齐，`grep -v ,OK`、`awk -F:` 之类既有管道不受影响。

派发规则（`subjects/{power,eventlog,inventory}.py`）：

- `runtime.InfoRunner` 按平台、OEM、设备角色和代际选择适配器。
- NVSwitch 和 Power Shelf 各自分派。NVIDIA 旧版路径保留在基础适配器里，其他设备按
  Redfish 资源发现。共享 BMC 时可用 `system_id`、`manager_id` 或 `chassis_id` 指定设备。
- Compal NVSwitch 可从 `Systems` 发现 `Systems/Self` 并读取电源状态。旧 `nvswpower.cpl`
  的 `reseat=FullPowerCycle` 是 NVL 接口参考，启用写操作前需按实际机型验收。
- `Oem` 基类就是 **NVIDIA GBXX 参考实现**（旧顶层脚本 + `func.sh`）：
  - `power`：IPMI `power <action>`，失败输出 `N/A`（对应旧脚本 `|| echo 'N/A'`）；
    `reseat` 走 GBXX 辅助电源环（`nodereseat`/`func.sh`）；DGX 覆盖为
    `Chassis/BMC` 的 `AuxPowerCycleForce`，Aivres 覆盖为 IPMI `raw 0x3c 0x28`，
    SMC/Lenovo 各自实现；
  - `nvswitch_power`：旧版 NVIDIA 使用 `Systems/System_0` 的 `ComputerSystem.Reset`，支持 `on/off/cycle`。
  - `eventlog`：默认读 `HGX_Baseboard_0/LogServices/EventLog/Entries`（`[HMC]`），
    `bmc` 走 IPMI `sel list`（`[BMC]`），`clear` 清 `Systems/system`、BMC SEL/EventLog
    与 HGX EventLog；
  - `firmware_inventory`：`UpdateService/FirmwareInventory`，行 `name:Id Version`
    （冒号后**无空格**，对应旧 `query_firmware`）。
- 各 vendor 覆盖**仅在其 HGX x86 脚本不同处**（如 Dell iDRAC Sel、Lenovo
  `PlatformLog`、SMC `Log1`、AMI/GIGABYTE `Managers/1/SEL`、DGX BIOS+HGX），端点缺失
  时回退 `super()`（GBXX 约定）。

改信息对象前，**先回读对应旧脚本**确认端点、字段与分隔符（空格 / 逗号 / tab /
`column -t` 对齐），不要凭字段名猜。`subjects/common.py` 的 `column_t` 近似
`column -t`，`jq`/`jq_out` 近似 `jq`（注意 `jq -r` 与 `jq` 的引号差异，见 SMC/NVSwitch
的 `PowerState` 带引号）。

## 4c. 启动对象（boot / bootorder / netboot / macs / bios）

启动对象**只对 compute tray 有意义**：`config.INFO_SUBJECTS` 里把
`boot/bootorder/netboot/macs/bios/vm` 标为 `("compute",)`，因此只会生成 `node*` 前缀
的命令，`nvsw*`/`ps*` 不生成。输出规则同信息对象（逐行、已带 `<node>: `）。

- **boot** — `Oem.boot`：IPMI `chassis bootdev` 失败或不可用时，按机型系统 ID 或唯一 Systems 成员定位，并验证 BMC 公布的目标后提交 Redfish `Once`。库存 `system_id` 直接走 Redfish。两条路径都失败会显示各自的错误。
- **bootorder** — `Oem.bootorder`：`BootOptions?$expand` 取 `Id`/`DisplayName`/
  `UefiDevicePath`，再用 `Systems/<id>` 的 `Boot/BootOrder` 逐项匹配（先按去掉
  `Boot` 前缀的数字 `grep -w`，再退回整串）。基类字段带 JSON 引号（对应 `jq` 不加
  `-r`）；ODM 用 `bootorder_quoted=False` / `bootorder_sep=","` / `bootorder_expand`
  改为裸字段。
- **macs** — `Oem.macs`：从 BootOptions 的 `UefiDevicePath` 里挑出 PXE 项，按旧脚本
  `sed`/`awk` 的字段序取 MAC 与 bus（注意：旧管道实际把 **IPv4 字段**当 macA，因此
  输出里的 "mac" 只有在 `UefiDevicePath` 中 MAC 位于 IPv4 之前时才是真正的 MAC），
  再补 `Managers/<bmc>/EthernetInterfaces` 的 BMC MAC。
- **bios** — `Oem.bios`：`Systems/<id>/Bios` 的 `Attributes` 逐键 `key=value`，
  按厂商 grep 掉噪声键。`base`/`pending` 只在 Lenovo/SMC 有写入路径。
- **netboot** — `subjects.netboot` 只读列出 PXE 项，以有效 MAC 将启动项与
  `boot_nics` 发现的网卡关联。`prefer` 在整批预检通过后更新持久顺序。Supermicro
  使用 `FixedBootOrder` 和 `UEFINetwork`，NVL 使用 BMC 公布的 BootOrder 设置资源
  或系统 `BootOrder`。提交前检查顺序是否变化，提交后回读。旧 `pxe` 走 OEM 动作。
- **vm** — `Oem.vm(device, action, options)`：虚拟媒体（**写操作**）。基类对应
  NVIDIA/AMI：动态发现非 `HGX` 的 `BMC_ID` 与首个 `CD*`/`USB*` 接口，`enable` 走
  `AMIVirtualMedia.EnableRMedia`，`mount`/`eject` 走 `VirtualMedia.InsertMedia`/
  `EjectMedia`；Compal/Netweb/Aivres/SMC/Dell 各自覆写端点与动作集（Dell 走
  `racadm remoteimage`）。`options.image`/`vm_user`/`vm_password` 来自 `nodevm` 的
  位置参数与 `--vm-user`/`--vm-password`（`arguments._add_info_opts` 对 `vm` 追加）。

> 已知偏差（与旧脚本不完全逐字一致）：
> - SMC `nodenetboot` 与 `nodebios base` 的旧脚本**没有** `<node>: ` 前缀，框架会给
>   每行补前缀；
> - Lenovo `nodebios base` 的 pretty JSON 用 `json.dumps(indent=2)` 近似 `jq` 的格式；
> - `nodevm` 的 AMI `EnableRMedia` 在旧脚本里带 `If-None-Match: not_match` 头，当前
>   transport 不支持自定义头（见下）；Aivres `media`/`insert`/`eject` 的原始响应
>   用紧凑 JSON 近似旧脚本直出的 curl body。

> `nodebootorder`/`nodemacs`/`nodebios`/`nodevm status` 是只读；`nodenetboot prefer`/`pxe` 与
> `nodebios base`、`nodevm` 的 `enable`/`mount`/`insert`/`eject`/`umount`/
> `stop` 会**修改** BMC/BIOS。

## 4d. 控制台（nodeconsole）

`nodeconsole` 不在 `SENSOR_COMMANDS`/`INFO_COMMANDS` 里，而是 `arguments` 里的独立
子命令，由 `cli._cmd_console` 直接处理（在 info 命令检查之前派发）。`console.py`
只依赖 `transport` 与 `runtime`（不碰 sensors/subjects），分两条路径：

- **legacy 端口**（默认）：`open` 先 `sol deactivate`（cipher 17）再
  `sol activate usesolkeepalive`（cipher 17，`-N 5 -R 5`），**继承终端 stdio**直接接管，
  多选时只用第一个节点；`close` 对所选节点用 `ThreadPoolExecutor(jobs)` 并行
  `sol deactivate`（无 cipher、无 `-N/-R`），合并 stdout+stderr 后逐行打印
  `<node>: <line>`，空行跳过（对应旧脚本 `xargs -I {} printf`）。`_argv` 构造
  `ipmitool -I lanplus -H ... -U/-P`（`--ipmi-env` 时 `-E` + `IPMI_PASSWORD` 环境变量）。
- **`--console`/`--interactive`**：`attach` 用 `pty.fork()`+`execvpe` 起一个可交互
  SOL，记录 JSON Lines（`_Log`，默认 `logs/sol_<label>_<stamp>.jsonl`，0600），
  `_relay` 做裸终端中继并把 `Ctrl-]`（`\x1d`）当本地断开；断开后按
  `--reconnect-attempts`/`--reconnect-delay` 重连（`--console` 默认开启重连）。
  要求恰好一个节点且 stdin/stdout 都是 TTY，否则 rc 2。`_reap` 负责发送 `~.`
  退出码并回收子进程。

> 与旧脚本的偏差：旧 `nodeconsole` 无参 `exit 1`，框架约定无参打印用法并
> `rc 0`（与其它命令一致）。

## 5. 关键约定

### 开发原则（务必遵守）

- **注释用 `#`**：`inventory.ini`（及其模板/生成器）沿用 `#` 注释，与旧
  `hosts.list`/INI 习惯一致。整行以 `#`（或 `;`）开头、或行内出现 `#` 之后的内容
  一律忽略；解析与生成都要遵守，任何后续新增的清单/配置文件也照此处理。
  不要用 `//`、`--` 或其它风格另起一套。
- **必须并行**：所有"逐节点"操作（fan/psu/temp/控制/`ping`…）跨节点必须并行，
  等价于旧脚本里的 `xargs -P`（默认 `jobs=18`），**不得串行**逐台执行。
  单节点内部按阶梯顺序回退属于正常（一个节点的多后端探测本身就是有序的），
  但节点之间不能排队。实现一律走 `runtime.py` 的 `ThreadPoolExecutor(max_workers=jobs)`。
- **hostlist 双向往返**：既要 SLURM 风格**展开**（`expand`，喂给操作），
  也要 SLURM 风格**合并**（`compact`，`list` 默认输出），且 `expand(compact(x)) == x`。

### 公共框架与厂商适配

- **公共层定义兼容性契约。** 命令派发、适配器基类、共享传输和数据模型构成框架。
  已有的参数和默认值、适配器入参与返回值、输出结构、错误和回退语义都属于契约。
  新增厂商或机型时，沿用这些接口与规范。
- **厂商差异放在适配器。** 通过现有扩展点处理厂商专用端点、字段映射、固件差异和能力判断。
  按平台、OEM、角色和代际注册适配器，并让实现遵循公共契约。
  例如，Lenovo GB300 从 `BootOptions` 补充 `nodebootorder` 名称和路径的逻辑
  放在 `oem/nvl/lenovo.py`，影响范围限于该适配器。
- **公共接口变更须说明理由并验证兼容性。** 先说明现有扩展点为何不足，列出受影响的
  调用方、适配器和外部行为。优先采用向后兼容的扩展。确需破坏兼容性时，明确迁移路径。
  除验证目标机型外，还要为受影响的现有实现补充或运行回归测试，按第 7 节做代表性实机验证，
  并记录缺少实机的组合。单一厂商的验证结果只覆盖该厂商的被测机型。

### inventory 语法与优先级

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

- **注释**：整行 `# ...` / `; ...` 与行内 `# ...` 均忽略（`#` 之后即注释）。
- 地址键：`host=` / `bmc_ip=` / `ansible_host=` / `ip=`，或第 2 个裸 token。
- 变量优先级：`[all:vars]` < 组 `:vars` < 行内 `key=value`。
- 角色来自分组（`compute/nvswitch/powershelf`），缺失时按名字模式兜底。
- 角色凭据字段：compute `bmc_*`、nvswitch `nvswitch_bmc_*`、
  powershelf `powershelf_bmc_*`（缺失回退 `bmc_*`）。
- `[nodehealth:defaults]` 提供 jobs/timeout/node_timeout/summary 默认值，
  未显式传参时生效。
- `node_timeout` 是**每台机器的总墙钟预算**（秒，默认 30）：用尽后 Redfish/IPMI
  不再发起新请求，正在进行的请求也被夹到剩余时间，避免慢 BMC 拖死整批；
  仅能力命令生效，`scan` 不设（本身就是长任务），且**没有对应 CLI flag**。
- `hosts.list` 生成器按 `DeviceType` 归组：`PhysicalNode→compute`、
  `PowerShelf→powershelf`、`Switch` 名含 `-gpu-nvl-` → `nvswitch`，否则 `switch`。

### hostlist 展开与合并

自实现（不依赖外部 GPL 工具），双向 SLURM/scontrol 风格：

- `expand`（展开，喂给操作）：`node[01-03]`、`node[1-3]`、`gpu[001-003,007]`、
  `x{8..10}`、括号可嵌套、顶层逗号分隔、去重保序。范围按低位宽度补零。
  顶层逗号切分对 `[]`/`{}` 内的逗号免疫（`n[01,03-05]` 是单个表达式）。
- `compact`（合并，`list` 默认）：把一批主机名折叠成
  `vdc1-pro2-n[03-10]-gpu-cp-[01-18]`，等价旧 `nodelist` 的 `pshbak -c`。
  保证 `expand(compact(x)) == x`。

`nodelist` 输出与旧脚本一致：每角色一行、角色名右对齐 15 列（用 hosts.list 的
DeviceType：`PhysicalNode` / `Switch` / `PowerShelf`）：

```
   PhysicalNode: nfs01,vdc1-pro2-n[03-10]-gpu-cp-[01-18],...
         Switch: vdc1-pro2-n[03-10]-gpu-nvl-[01-09],...
     PowerShelf: vdc1-pro2-n[03-10]-gpu-pwr-[01-08],...
```

`nodeping` 输出与旧脚本的 `dshbak -c` 一致：按状态分组，逐组打印 `----` 围栏、
合并后的主机列表、围栏，最后是状态行（`BMC pingable` / `BMC unreachable` /
`No BMC IP found`）。

两者都不再打印额外的 stderr 统计行，保证 `nodelist 2>/dev/null` 与旧脚本逐字节相同。

`nodeping` 旧流程是"先合并保存、再展开逐个 ping"；本工具同样支持把合并后的
表达式（含括号内逗号、多区间）直接作为选择器交给 `ping` 或检查命令。

### 主机选择

选择器支持：组名 / 单主机名 / hostlist 模式，逗号分隔可混用。
角色由**命令前缀**决定（`node*`/`nvsw*`/`ps*`）；compute / nvswitch / powershelf
**各自有独立的 psu/temp/fan 传感器**，不能混在一起看，因此：

- 不带 noderange → 查该角色的整个分组；
- 带 noderange → 先解析，再按该角色过滤（跨角色的主机会被跳过并在 stderr 提示）；
- `nodelist`/`nodeping` 不限定角色，覆盖整个 inventory。

```sh
reachable=$(./nodeping --long vdc1-pro2-t10-gpu-cp-[01-18] \
            | awk -F: '/pingable/{print $1}' | paste -sd, -)
./nodetemp "$reachable" --errors-only
```

### 平台、OEM、角色与缓存

`runtime.build_device` 读取 BMC 服务根和首个 Manager/System 的身份信息。`model.platform_for` 区分 HGX（B200/B300）和 NVL（GB200/GB300），`model.odm_for` 独立识别厂商。角色来自 inventory 的显式 `role=` 或角色组。`oem.for_device` 用平台和 OEM 选择适配器。同一 NVL 平台的不同 OEM 可以走不同适配器。库存可用 `platform=` 和 `oem=` 显式覆盖。

同一厂商有多种平台时，可以在 `oem/__init__.py` 中用 `(platform, oem, role, generation)` 注册专用适配器。Dell NVL 计算节点由 `oem/nvl/dell.py` 提供 Redfish 虚拟介质状态查询，写操作需验证后再启用。 跨代共用的适配器把 `generation` 设为 `None`。设备身份与接口可参考 [NVIDIA RMS 硬件兼容表](https://docs.nvidia.com/rms/documentation/reference/hardware-compatibility-list/)。写操作需要按设备和固件版本验证后再注册。

公开的 BMC 接口参考

- [NVIDIA NVSwitch Redfish URI list](https://docs.nvidia.com/networking/display/nvidiaswitchbmcusermanualv8800021040/supported%2Bredfish%2Bscheme%2Buris)
- [Lenovo GB300 NVL72 management interfaces](https://pubs.lenovo.com/gb300-nvl72/management_options)
- [HPE GB300 NVL72 BMC Redfish](https://support.hpe.com/hpesc/public/docDisplay?docId=sd00007150en_us&docLocale=en_US&page=GUID-1D3E5F93-529F-40DD-9E81-3D8DE8FDF8FF.html)
- [Dell XE9780 iDRAC](https://www.dell.com/support/product-details/en-us/product/poweredge-XE9780/resources/manuals)
- [Lenovo HGX B300 XClarity](https://pubs.lenovo.com/sr680a-v4/configure_the_firmware)
- [HPE HGX B300 Redfish](https://support.hpe.com/hpesc/public/docDisplay?docId=sd00007670en_us&docLocale=en_US&page=GUID-2D1518D5-6372-401B-AE97-5F40677DFD0D.html)
- [Supermicro Redfish API](https://www.supermicro.com/en/solutions/management-software/redfish)

### Blackwell 计算机型接入范围

核对 [HGX 认证机型](https://docs.nvidia.com/certification-technology/certified-systems/nvidia-certified-systems/)、
[NVL Qualified Systems](https://marketplace.nvidia.com/en-us/enterprise/qualified-system-catalog/) 和
[RMS 管理兼容表](https://docs.nvidia.com/rms/documentation/reference/hardware-compatibility-list/)。

| 机型 | 接入范围 |
| --- | --- |
| NVIDIA DGX B200/B300 | 归于 HGX。活动启动顺序缺失时读取 `Systems/DGX/SD`。 |
| HPE XD685 / XD690 HGX | XD685 优先 iLO `Systems/1`。XD690 走 OpenBMC 发现。 |
| NVIDIA GB200/GB300 NVL | 共用 `oem/nvl/nvidia.py`，保留 `gb200` 兼容路由。 |
| HPE GB200/GB300 NVL72 | `oem/nvl/hpe.py` 优先 `Systems/System_0`，支持 inventory 覆盖。 |
| Cisco UCS C880A M8 HGX B300 | 优先读取 `Systems/DGX`。 |
| Dell XE9712 NVL | AMI 与 iDRAC 使用标准资源发现。仅凭机型名确定平台，代际以实际报告为准。 |
| Supermicro NVL | `oem/nvl/smc.py` 使用标准只读发现。 |
| Wiwynn、Aivres、KAYTUS NVL | `oem/nvl/wiwynn.py`、`aivres.py`、`kaytus.py` 使用标准只读发现。 |
| Wistron NVL | 沿用现有 Wistron 适配器。 |

Aivres `KRS8000` / `KRS8000V3` 为 GB200，`KRS8500V3` 为 GB300。Aivres 和
KAYTUS 保留独立 OEM 标识。必要时用 `platform=`、`oem=`、`generation=`、`system_id=`
指定设备。新路径待实机验收，写能力按第 7 节单独验证。

### Wistron GB300 NVL 实机接口

Wistron `GB300 Bianca PDB`（BMC `01.05.26.0305`）的主系统是 `Systems/system`，
关联管理器为 `Managers/BMC_0`。独立的 `Systems/HGX_Baseboard_0` 由
`Managers/HGX_BMC_0` 管理，并公布 GPU 资源。固件可能把同一个模块序列号
填到多颗 GPU 上。此时 `nodeinventory --gpu` 将逐卡序列号标为缺失，并显示逐卡 UUID。

实测 `nodebmc info/time`、`nodefw show`、`nodemon` 和配套 Delta 电源架的
`psfan` 都能读取数据。`nodemon` 读取托盘总功率及两路 PDB HSC 功率。
`nodebios --set` 可写入 pending，再恢复原值。当前固件不具备 `base` 和 `bf3`
预设需要的字段。PXE 验证用 `nodenetboot prefer --mac` 提升一个端口并回读完整
`BootOrder`，然后恢复原顺序，并确认已有的启动覆盖保持原值。

### HGX B300 实机接口

Supermicro `SYS-822GS-NB3RT`（BMC `01.12.02.03`）使用主机机箱 `Chassis/1`，
其 GPU 底板还报告 HSC/HSCC。`nodepsu` 对主机电源按序列号合并新旧资源。
Aivres/KAYTUS `KR9288-X3-A0-F0-01`（BMC `2.03.ef`）使用 `Systems/1`
和 `Managers/1/VMMService`；`Systems/NV` 提供 GPU 序列号。
两款机器已实测 BIOS 暂存、PXE 设置和风扇切换。Supermicro 写入 `Bios/SD`，
KR9288 写入 `Bios/Settings` 时需要父 `Bios` 的 ETag。Supermicro 的介质
`stop URL` 按精确 URL 分离指定槽，固件可能保留 URL 配置。挂载需等待 Redfish 任务成功并核对 `Inserted`。实机上，KR9288 完成了 NFS 挂载、推出和原挂载恢复，Supermicro 通过 Apache 标准 HTTP 端口完成了 ISO 插入和推出。KR9288 的 `nodenetboot prefer` 把 `NetworkBbsPriority` 写入 `Bios/Settings`，使用父 `Bios` 的 ETag；`status` 可以读回待生效顺序。此操作调整网络启动组内的顺序，硬盘与网络启动组的相对位置保持原样。

### 按机型和角色实现命令

| 平台 | 应覆盖的命令 |
| --- | --- |
| HGX | 每个机型实现计算节点适用的 `node*` 命令和动作。 |
| NVL | 每种机柜方案覆盖计算托盘的 `node*`、配套 NVSwitch 的 `nvsw*`、配套 Power Shelf 的 `ps*`。 |

用下面的 OEM 能力清单和对应角色的现有命令核对覆盖范围，逐项记录已实现、
待实现和设备不支持的命令或动作，并单独记录实机验证状态。NVL 按配套设备各自的
OEM、型号及接口选择实现。同一 OEM 的不同代际或机型如有接口差异，分别适配。
接口一致时复用标准 Redfish、IPMI 或 NVOS 路径。实现后按第 7 节完成只读实机验收。

### Power Shelf PMC 接入

Power Shelf 的 OEM 以 PMC 报告的身份为准，必要时用 inventory 的 `oem=liteon`、
`oem=delta`、`oem=megmeet` 或 `oem=flex` 覆盖。Lite-On 和 Delta 各用专用适配器。
Megmeet 和 Flex 分别在 `oem/nvl/megmeet.py` 和 `oem/nvl/flex.py` 接入通用发现。
未识别厂商使用通用适配器，具体字段和能力以设备实际响应为准。
旧设备未识别 OEM 或只报告 NVIDIA/DGX 时，保留现有 Delta PSU 传感器的只读回退。

新增 PMC 时，先验证标准 Redfish 资源，再按需要在 `oem/__init__.py` 注册
平台/代际适配器或 OEM/角色适配器。专用 PSU 读取在 OEM 回退表注册。
记录 PMC 型号、固件和实际接口，保留 `ps*` 命令契约。写操作需单独验证。
Delta 的固件清单回退有公开文档依据，Delta、Megmeet、Flex 仍待本站实机验证。

[NVIDIA Power Shelf 厂商与机型资料](https://docs.nvidia.com/multi-node-nvlink-systems/nvfupd-guide/examples/powershelf.html)

### OEM 能力清单

以原始 nodetools 脚本为对照。每个机型按能力确认接口。标准 Redfish 查询由共用层实现，OEM 模块覆盖已确认的差异。

| 能力 | 读取入口 | 写入或待补 |
| --- | --- | --- |
| BIOS | `nodebios` 当前值，`pending` 待读取的设置 | 已有 OEM 动作保留。逐项写入、应用时机和重启条件按机型补齐 |
| 启动 | `nodebootorder` 持久顺序，`nodenetboot` 启动与 PXE 信息 | `nodeboot` 一次性覆盖。持久重排、网口绑定按 OEM 补齐 |
| 资产和固件 | `*inventory` 版本，`--details` 厂商/型号/序列号，计算节点 `--gpu`、`nodefru`、`nodefw show` | 完整 FRU 和部件号按机型补齐 |
| MAC 和网络 | `nodemacs` 主机 MAC，部分旧 OEM 适配可读 BMC/PXE MAC | BMC/PXE 通用查询、网络配置和连线诊断按接口补齐 |
| 温度、风扇、PSU | `*temp`、`*fan`、`*psu`，含健康状态 | 风扇调速走 OEM。PSU 和温度只读 |
| 虚拟介质 | `nodevm` 查询 | 已有 OEM 支持挂载、推出等动作。新机型按介质协议、启用和插入流程补齐 |
| 日志与任务 | `*eventlog`、`nodetask`、`*inventory --tasks` | 部分适配支持清日志。任务取消与诊断包待补 |
| 电源与控制台 | `*power` 状态，`nodeconsole` SOL | 按机型区分主电源、AUX 与 BMC 重启。NVSwitch/PowerShelf 单独验证 |
| 额外旧工具 | 健康监控、NVLink/UPHY、机箱与平台传感器盘点 | 阈值、遥测周期和告警规则需逐工具对照 |
| 管理控制面 | `nodebmc` 查询 BMC 身份、固件和时钟 | `reset`、`time --set` 按机型支持。账户和网络配置继续按机型补齐 |


`scan` 还会探测 chassis 端点并写入能力映射。缓存键使用厂商、制造商、型号、可用的固件版本以及显式库存覆盖值。普通命令只读映射；没有映射或指定 `--no-cache` 时仍读取设备身份，传感器使用实时发现。

### 扫描缓存（scan）

单个 BMC 会摊开几十个 chassis，哪个 chassis 有什么集合**因厂商而异，且不一定
在 detail 里声明**（Wistron 的 `Sensors` 集合就不出现在 chassis detail 中）。
因此 `scan` 直接探测各集合端点，按 chassis 记录 `Sensors`/`Fans`/`PowerSupplies`/
`Temperatures` 的成员数，以及旧式 `Thermal`/`Power` 单例对象是否存在，连同身份/ODM
一并存到 `~/.cache/nodetools/scan/<identity>.json`（`SCAN_CACHE_VERSION`，改结构时
递增以免误读旧档）。这是工具**唯一的缓存**，且**按机型**而非按地址。

fan/psu/temp 通过 `common.rung_chassis(device, key)` 只遍历"能回答该阶梯"的
chassis（无缓存则回退到实时发现，行为不变）：`Fans`/`PowerSupplies`/`Sensors`/
`Temperatures` 用数量 >0 判定，`Thermal`/`Power` 用存在性。实测 compute 节点
`fan` 0.9s vs 实时 11s、`psu` 8s vs 22s、`temp` 7s vs 17s。同机型重扫任意一台即刷新，
`scan --clear` 删除该机型的模版。

### 所有管理命令的端点选择

这条规则适用于所有访问设备的命令，包括读取状态和执行控制动作。compute tray、
NVSwitch、power shelf 的 Redfish、IPMI 和 OS 接口按同一原则选路。

1. **配置明确时直达。** 用 inventory 中的厂商、平台、代际和角色选适配器。
   由适配器定义该机型和已验证固件的固定接口，inventory 可用 `system_id`、
   `manager_id`、`chassis_id` 指定资源实例。目标明确后，直接读取或操作已知接口，
   省掉与本次操作无关的身份和集合探测。
2. **布局可变时复用模板。** `scan` 发现并保存机型模板。配置或有效模板无法确定
   目标时，执行范围明确的发现。直读端点不再有效时，走该能力的回退或重新发现。
   可用 `scan` 主动刷新模板，支持 `--no-cache` 的命令可用它做实时核对。
3. **写操作保留校验。** 复用已知路径，同时保留动作所需的目标身份、资源和参数
   校验，以及任务或状态回读。目标无法确认时停止写入。提交结果不明确时，先查
   任务或状态，再决定是否重试。
4. **按命令验收。** 保持现有命令接口和输出格式。测试配置齐全时的请求数、
   配置缺失或端点失效时的回退，以及写后的状态验证。端点差异由厂商适配器处理。

每接入一个新机型，按角色逐条检查命令和动作。已验证的固定端点应接入直读，
并在 inventory 模板中给出可用的 `platform`、`oem`、`generation` 配置。
对确实随设备变化的资源，仅发现本次命令需要的子集合。提交前记录请求数，
并测试直读失败时的回退及输出是否完整。

Lenovo GB300 的 `nodefan` 是已落地的只读示例。inventory 指定 `platform=nvl`、
`oem=lenovo`、`generation=gb300` 时，适配器通过 `inventory_fixed_caps` 声明
`fan` 可以跳过启动探测，一次 GET 读取 `/redfish/v1/Chassis/<chassis_id>/Thermal`。
默认机箱为 `Chassis_0`，同一次响应提供 8 路 PWM 和 16 路 RPM。

### IPMI 密码双模式

- 默认：`-U user -P pass`（与旧脚本一致）。
- 环境模式：`--ipmi-env` 或 `NODETOOLS_IPMI_ENV=1` → `-U user -E`，
  密码经 `IPMI_PASSWORD` 传入，不进进程表。

优先级：显式 flag > 环境变量 > inventory。

### 输出

字段顺序按对象选择（`report._FIELDS`）：fan 为
`<node>: <id>,<name>,<speed>,<health>`，psu/temp 为
`<node>: <id>,<health>,<reading>`，与旧脚本一致；字段内空格/逗号替换为 `_`，
因此可 `grep -v ,OK`。`--summary` 输出 `<node>: <CAP>,<role>,total=N,OK=N,nonOK=N`。
空结果会输出 `ERROR no readings ...` 且进程退出码为 1。

## 6. 如何扩展

### 新增一个对象（fan/psu/temp 之外的 subject）

1. 在 `config.CAPS` 加入对象名（`arguments.SUBJECTS` 由它派生）。
2. 新建 `caps/<subject>.py`，实现 `collect(device) -> list[reading]`，
   用 `ladder.first([...])` 串起多个回退步骤；复用 `caps/common.py` 的工具。
3. 在 `caps/__init__.py` 的 `COLLECTORS` 注册。
4. 如需控制动作，在 `runtime.Runner._control` 里加分派。
5. `arguments.SENSOR_COMMANDS` 会为每个前缀自动生成 `<prefix><subject>` 命令。
6. 补 `tests/test_caps.py`：用 `FakeRedfish` 造 JSON 响应断言解析结果。

若对象只对某一角色有意义（如 `inlet` 仅 compute），**不要**加进 `config.CAPS`：
改为在 `arguments.SENSOR_COMMANDS` 里显式登记一条 `<prefix><subject>`（其余
`COLLECTORS`/`report._FIELDS` 照常注册），这样不会给其他角色生成空命令。
需要运行期参数时，收集器从 `device.options` 读取（`runtime.build_device` 已挂载
本次运行的 `Options`），并在 `build_parser` 里为该 subject 单独加 flag。

若对象只落在单个 chassis 上（如 `inlet`），不要用 `temp.collect` 遍历全部 chassis：
BMC 的 `ThermalMetrics` 单次响应就慢（并发轰同一 BMC 还会成倍放大），改为按
`config.DEFAULT_CHASSIS` 优先顺序逐个探测、首个命中即返回（如 `caps/inlet._find`）。
不需要 scan 模板的对象可在 `runtime._PROFILELESS_CAPS` 登记，跳过身份读取。
最好在 `tests/test_caps.py` 用计数 `FakeRedfish` 断言只发了一次请求。

### 新增一个信息对象（subject）

1. 在 `config.INFO_SUBJECTS` 加 `对象→支持角色`，并按需在 `config.INFO_ACTIONS`
   声明动作、`config.INFO_READ_WORDS` 补充"读取"词。
2. 新建 `subjects/<subject>.py`，实现 `collect(device, action, options) -> list[str]`，
   返回**完整输出行**（含 `<node>: ` 前缀）；复用 `subjects/common.py` 的工具。
   若对象有角色专有的固定路径（如 ps 的 NVIDIA 端点），在 `collect` 里先按
   `device.role` 分流，compute 转 `device.odm.<subject>`（power 的 nvsw 转
   `device.odm.nvswitch_power`）。
3. 在 `subjects/__init__.py` 的 `HANDLERS` 注册。
4. `arguments.INFO_COMMANDS` 会为每个支持角色自动生成 `<prefix><subject>` 命令；
   补 `pyproject.toml [project.scripts]` 与仓库根的符号链接。
5. 在 `oem/base.py` 的 `Oem` 加参考实现（GBXX 约定），各 vendor 按旧脚本覆盖；
   端点缺失时回退 `super()`。
6. 补 `tests/test_subjects.py`：`FakeRedfish` 增加 `post()`，样板见现有用例。

### 新增一个角色前缀

在 `config.ROLE_PREFIXES` 加 `前缀→角色`（如 `ib` → `ibswitch`），并在 inventory
里加对应分组；命令与 `_role_hosts` 的过滤会自动生效。

### 新增一个 ODM

1. 新建 `oem/{hgx,nvl}/<odm>.py`，继承 `oem/base.Oem`，设置 `name`、`fan_chassis`，
   按需实现 `fan_control`。
2. 在 `oem/__init__.py` 的 `_MODULES` 注册；如有别名加到 `_ALIASES`。
3. 若该 ODM 的身份字符串特殊，在 `model.SIGNATURES` 里加标记（注意顺序）。
4. 如果它的**读数据**也和通用路径不同，优先在 `caps/*.py` 的阶梯里加一个
   针对性的步骤（参考 Wistron 专用 `Sensors/Chassis_0_FAN_*` 就是走
   `_from_sensors` 命中的），而不是在 ODM 模块里重写整条收集逻辑。
5. 读取和写入都按[所有管理命令的端点选择](#所有管理命令的端点选择)接入。

### Lenovo GB300 compute tray 风扇控制

- `nodefan NODE auto` 用 `ipmitool raw 0x3a 0x05 0x00` 将风扇控制交回 BMC。
- `nodefan NODE max` 先用 `ipmitool raw 0x3a 0x05 0x01` 切换到手动，再用
  `ipmitool raw 0x3a 0x03 0xff 0x64` 设置 100% PWM。写入失败时尝试恢复自动控制。
- `--dry-run` 显示命令顺序。用 `nodefan NODE check` 回读各路 PWM 和 RPM。

`nodefan` 的路径选择见[所有管理命令的端点选择](#所有管理命令的端点选择)。

### 新增一个传输

新增 `transport/<name>.py`，暴露最小接口（如 `get/post`），在 `runtime._device`
里按需装配到 `Device`。错误统一抛 `errors.TransportError`。

## 7. 测试与验证

### 单元测试

复用已有的项目虚拟环境。没有项目环境时可自行创建临时 venv，例如在 BCM 上从仓库根目录执行：

```sh
test_venv=$(mktemp -d "${TMPDIR:-/tmp}/nodetools-test.XXXXXX")
python3 -m venv "$test_venv"
. "$test_venv/bin/activate"
```

激活项目环境后，从仓库根目录安装项目和测试依赖，再运行测试：

```sh
python -m pip install -e . pytest
python -m pytest -q
```

覆盖：hostlist 展开、inventory 解析/优先级/生成、选择器、输出格式与
`grep -v ,OK` 兼容、ODM 注册、三个传感器对象对 `FakeRedfish` 的解析、信息对象
（power/eventlog/inventory 的 IPMI 默认、nvsw/ps 固定路径、Dell/Lenovo/SMC/AMI
覆盖、冒号后空格差异）、scan 缓存（写入/版本不符忽略/`rung_chassis` 命中与回退）、
`odm_for` 只读 profile（有/无/空 odm）、`InfoRunner` 在传输失败且无输出时报
`BMC unreachable`、`nodelist` 无参列出全部、其余命令无参打印帮助并以 0 退出（不报错）、
可达性检查、每节点 deadline
（`subprocess` 打桩，不依赖网络）。缓存测试用 `tmp_path` 覆盖 `SCAN_CACHE_DIR`。

### 真机冒烟（改接口后必做）

```sh
# 从现有 hosts.list 生成 inventory
./nodetools inventory init --from /path/to/hosts.list -o /tmp/nt.ini
./nodetools inventory show -i /tmp/nt.ini | head

# 单台下钻，确认字段（前缀定角色）
./nodefan  vdc1-pro2-t10-gpu-cp-01 -i /tmp/nt.ini
./nodetemp vdc1-pro2-t10-gpu-cp-01 -i /tmp/nt.ini --summary
./pspsu    vdc1-pro2-t10-gpu-pwr-01 -i /tmp/nt.ini

# 批量与只报错
./nodetemp vdc1-pro2-t10-gpu-cp-[01-03] -i /tmp/nt.ini --errors-only
```

**注意**：`--action`（fan `auto/max`）会真正改动设备状态，
测试时先在单台、且确认可回退的机器上做，不要整批跑。

### 风扇调速实机验收

新增或修改风扇调速适配时，每个受影响的 OEM 和机型都要在一台获准调速的代表机上
用实际 CLI 验证。记录机型、BMC 固件版本和测试的风扇。单元测试和 `--dry-run`
只能证明命令选择正确，实机验证应包含以下步骤。

1. 先用 `nodefan check` 记录基线 RPM 和健康状态。BMC 提供调速模式时，也记录当前模式。
2. 实际执行 `nodefan <节点> max`，记录命令结果。等待转速稳定后重新读取同一批风扇，
   核对转速或 BMC 提供的目标 PWM 是否符合最大档预期。
3. 实际执行 `nodefan <节点> auto` 并读回。能查询模式时确认已恢复自动模式，同时检查
   转速和健康状态。测试以恢复计划规定的模式结束。即使最大档只执行了一部分，也要尝试恢复
   并核对结果。
4. 分开记录 CLI 返回、BMC 响应、转速读回和恢复结果。只有写入成功、观察到预期行为，
   且恢复已验证时才标为通过。需要直接调用 BMC 才能恢复时，记录恢复成功，并将 CLI
   操作记为失败。没有可用代表机时标记为待实机验证。

### RemoteMedia 实机验收

新增或修改虚拟介质适配后，在代表性设备上按机型、BMC 固件和共享协议做一次挂载与弹出。

1. 用 `nodevm HOST status` 记录起始槽位状态，选空槽和小型测试 ISO。
2. 用 `nodevm HOST mount URL` 挂载。共享需要认证时，传入 `--vm-user` 和 `--vm-password`。
   再运行 `status`，核对目标槽位为 `inserted=True`，镜像地址对应测试 ISO。
3. 用 `nodevm HOST eject URL` 弹出测试镜像。适配器只支持无 URL 弹出时，
   先确认唯一挂载的是本次测试 ISO，再用 `nodevm HOST eject`。
   再运行 `status`，核对目标槽位为 `inserted=False`，其余槽位保持原状。
4. 记录机型、BMC 固件、共享协议、槽位和两次回读结果。没有代表性设备时标记为待实机验证。

#### CIFS 测试共享

Debian/Ubuntu 可安装 `samba` 和 `smbclient`。创建用于测试的本地账号，
用 `smbpasswd -a ISO_USER` 设置 Samba 密码，并授予该账号目录访问及 ISO 读取权限。
可在现有 Samba 配置中加入以下共享，将 `ISO_USER` 替换为实际账号。

```ini
[share]
    path = /var/www/html/isos/
    read only = yes
    valid users = ISO_USER
```

把测试 ISO 放入共享目录。用 `testparm -s` 校验配置，加载配置后用
`smbclient //SMB_HOST/share -U ISO_USER -c 'ls'` 检查认证及文件可见性。
确认 BMC 自身能访问 Samba 主机的 TCP 445。Wistron GB300 使用
`smb://SMB_HOST/share/probe.iso`，协议支持范围以各适配器的实测为准。

### 按机型做只读实机验收

新增或修改设备接口时，按实际设备型号维护验收记录。HGX 和 NVL 应分别对每个可用机型
选至少一台代表设备，逐个运行该机型适用的 `node*` 只读命令。即使是同一 OEM，
不同代际或使用不同管理接口的机型也分别验证。NVL 还要对该机型配套的每种
NVSwitch 和 Power Shelf 选至少一台代表设备，各跑一遍适用的只读命令。

| 对象 | 只读覆盖范围 |
| --- | --- |
| HGX / NVL 计算节点 | `nodeping`、`nodefan`、`nodepsu`、`nodetemp`、`nodeinlet`、`nodemon`、`nodepower status`、`nodeeventlog status`、`nodeinventory`、`nodebootorder`、`nodenetboot list`、`nodemacs`、`nodebios status` / `pending`、`nodevm status`、`nodefru`、`nodebmc` / `time`、`nodetask`、`nodefw show` / `status` |
| NVSwitch | `nvswpsu`、`nvswtemp`、`nvswpower status`、`nvsweventlog status`、`nvswinventory` |
| Power Shelf | `psfan`、`pspsu`、`pstemp`、`pspower status`、`pseventlog status`、`psinventory` |

同时验证改动涉及的其他只读入口。`nodetask` 先列任务，有记录时用 BMC 返回的真实 ID
查详情。用 `nodenetboot list` 查 PXE 项，用 `nodebootorder` 查完整启动顺序。只读验收不执行电源控制、清日志、改 BIOS 或启动项、
变更虚拟介质、重启 BMC、改时钟或刷写固件。

记录平台、OEM、实际型号、BMC 或 NVOS 版本、代表节点、命令和结果。核对关键字段与
设备接口返回一致，并区分通过、失败、不支持、无实机。没有实机的组合标为待实机验证。

#### HGX 直读覆盖表

两款 B300 实机分别用自动发现和显式 inventory 配置验收
（`platform=hgx`、`generation=b300`、`oem=smc` 或 `oem=aivres`）。
传感器 ID、名称和输出字段一致。确认机箱覆盖后，可设 `temp_chassis_id=1`
读取 `Chassis/1/Thermal`。固定端点无读数时，温度采集会回退到发现路径。

| 机型或适配器 | 适配器内置直读 | 实机风扇 | 实机 PSU | 实机温度 |
| --- | --- | --- | --- | --- |
| Supermicro SYS-822GS-NB3RT | fan, PSU | 6 (6 → 1) | 6 (13 → 8) | 35 (91 ‡ → 1) |
| Aivres/KAYTUS KR9288-X3-A0-F0-01 | fan, PSU | 40 (6 → 1) | 8 (6 → 1) | 71 (12 → 1) |
| AMI HGX | fan, PSU | 待验收 | 待验收 | 待验收 |
| BYD HGX | fan | 待验收 | 待验收 | 待验收 |
| Compal HGX | fan, PSU | 待验收 | 待验收 | 待验收 |
| Dell HGX | fan, PSU, temp | 待验收 | 待验收 | 待验收 |
| Gigabyte HGX | fan, PSU | 待验收 | 待验收 | 待验收 |
| Lenovo HGX | fan, PSU | 待验收 | 待验收 | 待验收 |
| Netweb HGX | fan, PSU | 待验收 | 待验收 | 待验收 |
| NVIDIA DGX B200/B300 | fan, PSU | 待验收 | 待验收 | 待验收 |

单元格为传感器数及自动发现 → 直读的 Redfish 调用数。
‡ Supermicro 自动发现的 91 次调用中，节点时限内收到 33 次 HTTP 响应。
它的 35 个温度传感器中有 1 个在两种路径下都无数值。
其他机型和命令仍需逐项实机验收。

## 8. 代码风格与提交约定

- 只用标准库 + `requests`，不引入额外依赖（保持 `pip install .` 轻量）。
- 不加无关注释；命名跟现有文件保持一致；新逻辑放到对应分层，不堆进 `cli.py`。
- 提交前，在项目虚拟环境里跑完 `python -m pytest -q`。改接口时附真机冒烟证据。
- 提交信息用英文、祈使句、说明"为什么"。
- 作者统一为 `junli-nv <junliz@nvidia.com>`（已写入 `pyproject.toml` 的
  `authors`，并用作 git 提交身份）；不要用其他人的姓名/邮箱署名。
- 真实 `inventory.ini` / `hosts.list` 不入库（已在 `.gitignore`）；不要提交口令。

## 9. 已知限制与后续 TODO

- HGX B200/B300 使用 `node*` 命令，按 OEM 和实际机型补齐只读验收。
  Supermicro 和 Aivres 的传感器实测见上表。
- KR9288（BMC 2.03.ef）已验证持久 PXE 顺序。交换两个网络 BBS 项后，
  待生效设置和上电后的当前 BIOS 顺序均匹配，启动组优先级未变。
  KR9288 适配器仍拒绝 AUX reseat，等待厂商确认该机型的 AUX 接口。
- `Switch` 组里非 `-gpu-nvl-` 的交换机暂归 `switch` 组，尚无对应能力（无 `nvsw*`
  命令命中）。
- 控制动作（`--action`）只覆盖已确证的几条 IPMI raw / Redfish PATCH / racadm；
  新增前请回读对应 OEM 脚本确认字节。
- 角色命令会把跨角色的选择结果过滤掉并在 stderr 提示；`nodelist`/`nodeping` 不限定
  角色，覆盖全部。`nodeping` 与旧脚本一致，用单包 flood 探测
  （`ping -M do -f -c 1 -W <t> -w <t>`），需要与旧脚本相同的权限（root），因此会
  像旧脚本一样产生瞬时突发流量。无 BMC 地址的主机按 `No BMC IP found` 归类
  （`--long` 时逐台显示）。
- 尚未做：`nodesensors`（MetricReports 遥测）、结果落库/告警、`--json` 输出、IB/SPX
  交换机能力、`nodealive`/`nodecable`/`nodeflapping`（SSH）、`nodemon.ipmi`。
- 信息对象的 `clear`（清日志）与 `reseat` 等动作因旧脚本本身依赖外部工具
  （`onecli`、`racadm`）或返回体为空，输出细节未逐一比对；改动前回读旧脚本。

## 接入固件升级适配器

`nodefw show` 和 `nodefw status` 复用 `subjects/firmware.py` 的标准 Redfish 查询。
需要为某个厂商新增固件升级支持时，在对应平台的 OEM 目录实现适配器，在 `firmware_cli.ADAPTERS` 中登记
验证过的系统型号。适配器负责匹配硬件与固件包、校验版本和组件、核对电源和任务状态，
并返回实际提交结果。`check` 与 `update` 共用预检，只有 `update` 执行写入。
