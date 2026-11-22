# 开发者文档

本文说明 nodetools 的设计思路、模块职责、扩展方式和验证方法，供后续继续开发。

## 1. 背景与目标

原来排查 GB200/GB300 靠一堆按 OEM 分叉的 shell 脚本（`nodefan.*`、`nodepsu.*`、
`nodemon.*`、`pspower.*` …），同一件事在每家 ODM 下都是另一份文件，维护成本高。
nodetools 把它们收敛成一个 **按角色分前缀** 的命令族，命名沿用旧脚本的约定：

- 命令名 = 前缀 + 对象：**前缀定角色**，**对象定主体**；
  - `node*` → compute tray，`nvsw*` → NVSwitch，`ps*` → power shelf；
  - 对象：`fan` / `psu` / `temp`（传感器）与 `power` / `eventlog` / `inventory`（信息）；
  - 例：`nodepsu`、`nvswfan`、`pspsu`、`nodepower`、`nvswinventory`。GBXX 三个前缀都要；BXX 只用 `node*`。
- 读取路径做成 **回退阶梯**，一套实现覆盖所有 ODM；
- 设备、凭据、角色、ODM 都从 inventory + 运行时探测得到，不再写死在脚本里。

已实现两组对象：**fan / psu / temp**（传感器，三种角色各一套）与
**power / eventlog / inventory**（信息，三种角色各一套），结构预留了继续加对象
（BMC 信息、按键/复位、IB/SPX 交换机等）。

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
角色命令用 `cli._role_hosts` 解析：无 noderange 时默认取该角色分组，点名了
则在解析后再按角色过滤（跨角色的主机被跳过并提示）。

## 2. 架构总览

```
nodetools (入口)
  └─ nodetools_runtime.cli
       ├─ arguments.py   解析参数
       ├─ inventory.py   解析/生成 inventory
       ├─ selection.py   选择器 → 主机列表
       ├─ reach.py       BMC ICMP 可达性检查（list/ping 命令用）
       ├─ scan.py        Redfish 端点扫描 + 缓存（scan 命令写，能力读）
       ├─ runtime.py     并行引擎：建 Device、探测 ODM、跑能力/控制
       │    ├─ model.py       角色/ODM 探测 + 缓存
       │    ├─ caps/*.py      传感器收集器（回退阶梯）
       │    ├─ subjects/*.py  信息对象（power/eventlog/inventory，逐行输出）
       │    ├─ oem/*.py       ODM 差异（附加路径 + 控制动作 + 信息覆盖）
       │    └─ transport/*.py Redfish / IPMI / racadm
       └─ report.py      输出格式化（传感器 CSV/汇总 + 信息逐行）
```

一次调用的数据流：

```
argv
 → Inventory.from_file(inventory.ini)         # 分组 + 变量 + 默认值
 → selection.select(...)                       # 名字/组/模式 → Host[]
 → Runner.run(hosts) / InfoRunner.run(hosts)  (ThreadPoolExecutor)
     每台: 解析角色凭据 → 建 Redfish+Ipmi → Device（附 scan profile）
           ├─ 传感器 --action: 探测 ODM → oem.get(odm).fan_control/psu_control
           ├─ 传感器否则: caps.collector_for(cap)(device) → readings[]
           └─ 信息命令: compute 探测 ODM → subjects.handler_for(subject)(device, action)
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
| `scan.py` | 探测各集合端点并缓存每个 chassis 的能力图 | `scan` 命令写；能力经 `rung_chassis` 读 |
| `model.py` | `Device`、`probe_redfish`、`classify`、ODM 缓存 | 探测失败回退 `unknown` |
| `ladder.py` | "按顺序试，返回第一个非空" | 收集器的核心控制流 |
| `transport/redfish.py` | Redfish `get/get_raw/post/patch` | `verify=False`，连接超时 3s |
| `transport/ipmi.py` | ipmitool 封装，`sdr` / `raw` | 双模式传密码，见第 5 节 |
| `transport/racadm.py` | Dell racadm | 不可用时 `available()` 返回 False |
| `caps/common.py` | Reading 构造、health/speed/num、集合/传感器枚举、`rung_chassis` | 共享工具 |
| `caps/{fan,psu,temp}.py` | 三个传感器能力的回退阶梯 | 见第 4 节 |
| `subjects/common.py` | 信息对象工具：`resource`/`entries`/`log_lines`/`column_t`/`jq`/`post` | 逐行输出的共享工具 |
| `subjects/{power,eventlog,inventory}.py` | 三个信息对象的 handler；nvsw/ps 固定 NVIDIA 路径，compute 转 ODM | 见第 4b 节 |
| `oem/base.py` | `Oem` 基类（GBXX 参考）+ `power`/`eventlog`/`firmware_inventory` + `_ipmi_raw` | 端点缺失时 vendor 回退到这里 |
| `oem/<odm>.py` | 单个 ODM 的控制动作/专用 chassis/信息覆盖 | 读数据主要靠通用阶梯 |
| `runtime.py` | `build_device`（附 scan profile）、`Runner`/`InfoRunner` 并行、`Scanner`、错误隔离 | 每台独立 try/except |
| `report.py` | 传感器行/汇总（按对象选字段顺序）+ 信息逐行（`format_lines`） | 兼容 `grep -v ,OK` |

## 4. 收集阶梯（回退顺序）

每个能力都是"依次尝试，返回第一个有数据的后端"，用 `ladder.first` 实现。
这样即使 ODM 探测错误，只要有一条通用路径通，就能读到数据。

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

- `nvsw*` / `ps*` 是 NVIDIA 专有基础设施，**固定走 NVIDIA Redfish 路径**，不按厂商派发：
  - power：`Systems/{System_0|system}/Actions/ComputerSystem.Reset`，状态读 `PowerState`；
  - eventlog：`Systems/System_0/LogServices/EventLog`（nvsw）、
    `Managers/PMC_0/LogServices/EventLog`（ps）；
  - inventory：`UpdateService/FirmwareInventory`（`Id`+`Version`）。
- `node*`（compute）按 **ODM** 派发：`runtime.InfoRunner` 在 `role == "compute"` 时探
  测 ODM 并 `device.odm = oem.get(...)`，再调 `device.odm.<subject>(device, action)`。
- `Oem` 基类就是 **NVIDIA GBXX 参考实现**（旧顶层脚本 + `func.sh`）：
  - `power`：IPMI `power <action>`，失败输出 `N/A`（对应旧脚本 `|| echo 'N/A'`）；
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

### ODM 探测与缓存

`probe_redfish` 拉取 `/redfish/v1`、`Managers/1`、`Systems/1`、`Chassis/*` 的
Vendor/Model/Manufacturer，拼成字符串后按 `model.SIGNATURES` 匹配（具体厂商在前，
`nvidia` 兜底 `gb200`）。结果缓存到 `~/.cache/nodetools/oem/<ip>`；`--no-cache` 绕过。
**读数据不依赖 ODM**，探测只影响控制动作（`--action`）。有 scan 缓存时直接用其中的
身份，不重复遍历 chassis。

### 扫描缓存（scan）

单个 BMC 会摊开几十个 chassis，哪个 chassis 有什么集合**因厂商而异，且不一定
在 detail 里声明**（Wistron 的 `Sensors` 集合就不出现在 chassis detail 中）。
因此 `scan` 直接探测各集合端点，按 chassis 记录 `Sensors`/`Fans`/`PowerSupplies`/
`Temperatures` 的成员数，以及旧式 `Thermal`/`Power` 单例对象是否存在，存到
`~/.cache/nodetools/scan/<ip>.json`（`SCAN_CACHE_VERSION`，改结构时递增以免误读旧档）。

fan/psu/temp 通过 `common.rung_chassis(device, key)` 只遍历"能回答该阶梯"的
chassis（无缓存则回退到实时发现，行为不变）：`Fans`/`PowerSupplies`/`Sensors`/
`Temperatures` 用数量 >0 判定，`Thermal`/`Power` 用存在性。实测 compute 节点
`fan` 0.9s vs 实时 11s、`psu` 8s vs 22s、`temp` 7s vs 17s。重新 `scan` 即刷新
（机器有变动时），`scan --clear` 删除。

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

### 新增一个信息对象（subject）

1. 在 `config.INFO_SUBJECTS` 加 `对象→支持角色`，并按需在 `config.INFO_ACTIONS`
   声明动作、`config.INFO_READ_WORDS` 补充"读取"词。
2. 新建 `subjects/<subject>.py`，实现 `collect(device, action, options) -> list[str]`，
   返回**完整输出行**（含 `<node>: ` 前缀）；复用 `subjects/common.py` 的工具。
   若对象有角色专有的固定路径（如 nvsw/ps 的 NVIDIA 端点），在 `collect` 里先按
   `device.role` 分流，compute 再转 `device.odm`。
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

1. 新建 `oem/<odm>.py`，继承 `oem/base.Oem`，设置 `name`、`fan_chassis`，
   按需实现 `fan_control` / `psu_control`。
2. 在 `oem/__init__.py` 的 `_MODULES` 注册；如有别名加到 `_ALIASES`。
3. 若该 ODM 的身份字符串特殊，在 `model.SIGNATURES` 里加标记（注意顺序）。
4. 如果它的**读数据**也和通用路径不同，优先在 `caps/*.py` 的阶梯里加一个
   针对性的步骤（参考 Wistron 专用 `Sensors/Chassis_0_FAN_*` 就是走
   `_from_sensors` 命中的），而不是在 ODM 模块里重写整条收集逻辑。

### 新增一个传输

新增 `transport/<name>.py`，暴露最小接口（如 `get/post`），在 `runtime._device`
里按需装配到 `Device`。错误统一抛 `errors.TransportError`。

## 7. 测试与验证

### 单元测试

```sh
python3 -m pytest -q          # 当前 73 passed
```

覆盖：hostlist 展开、inventory 解析/优先级/生成、选择器、输出格式与
`grep -v ,OK` 兼容、ODM 注册、三个传感器对象对 `FakeRedfish` 的解析、信息对象
（power/eventlog/inventory 的 IPMI 默认、nvsw/ps 固定路径、Dell/Lenovo/SMC/AMI
覆盖、冒号后空格差异）、scan 缓存（写入/版本不符忽略/`rung_chassis` 命中与回退）、
`nodelist`/`nodeping` 命令与可达性检查、每节点 deadline（`subprocess` 打桩，不依赖
网络）。缓存测试用 `tmp_path` 覆盖 `SCAN_CACHE_DIR`。

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

**注意**：`--action`（fan `auto/max`、psu `on/off/cycle`）会真正改动设备状态，
测试时先在单台、且确认可回退的机器上做，不要整批跑。

## 8. 代码风格与提交约定

- 只用标准库 + `requests`，不引入额外依赖（保持 `pip install .` 轻量）。
- 不加无关注释；命名跟现有文件保持一致；新逻辑放到对应分层，不堆进 `cli.py`。
- 提交前：`python3 -m pytest` 全绿；改接口时附真机冒烟证据。
- 提交信息用英文、祈使句、说明"为什么"。
- 作者统一为 `junli-nv <junliz@nvidia.com>`（已写入 `pyproject.toml` 的
  `authors`，并用作 git 提交身份）；不要用其他人的姓名/邮箱署名。
- 真实 `inventory.ini` / `hosts.list` 不入库（已在 `.gitignore`）；不要提交口令。

## 9. 已知限制与后续 TODO

- B200/B300（BXX 普通 HGX 机型）**本期未做**；BXX 只需 `node*` 前缀，后续加 BXX 的
  命名/来源与对应阶梯即可。
- powershelf ODM 目前多归为 `unknown`（Delta 电源），只在内部影响控制动作，不影响
  读 psu。
- `Switch` 组里非 `-gpu-nvl-` 的交换机暂归 `switch` 组，尚无对应能力（无 `nvsw*`
  命令命中）。
- 控制动作（`--action`）只覆盖已确证的几条 IPMI raw / Redfish PATCH / racadm；
  新增前请回读对应 OEM 脚本确认字节。
- 角色命令会把跨角色的选择结果过滤掉并在 stderr 提示；`nodelist`/`nodeping` 不限定
  角色，覆盖全部。`nodeping` 与旧脚本一致，用单包 flood 探测
  （`ping -M do -f -c 1 -W <t> -w <t>`），需要与旧脚本相同的权限（root），因此会
  像旧脚本一样产生瞬时突发流量。无 BMC 地址的主机按 `No BMC IP found` 归类
  （`--long` 时逐台显示）。
- 尚未做：`boot`/`bootorder`/`netboot`/`macs`/`bios` 信息对象（第二批）、
  `nodesensors`（MetricReports 遥测）、结果落库/告警、`--json` 输出、IB/SPX
  交换机能力。
- 信息对象的 `clear`（清日志）与 `reseat` 等动作因旧脚本本身依赖外部工具
  （`onecli`、`racadm`）或返回体为空，输出细节未逐一比对；改动前回读旧脚本。
