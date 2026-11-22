# 开发者文档

本文说明 nodetools 的设计思路、模块职责、扩展方式和验证方法，供后续继续开发。

## 1. 背景与目标

原来排查 GB200/GB300 靠一堆按 OEM 分叉的 shell 脚本（`nodefan.*`、`nodepsu.*`、
`nodemon.*`、`pspower.*` …），同一件事在每家 ODM 下都是另一份文件，维护成本高。
nodetools 把它们收敛成一个 **能力优先（capability-first）** 的 Python 工具：

- 一条命令 `nodetools <cap> <selector>`，cap 先于设备；
- 读取路径做成 **回退阶梯**，一套实现覆盖所有 ODM；
- 设备、凭据、角色、ODM 都从 inventory + 运行时探测得到，不再写死在脚本里。

第一期只做 **fan / psu / temp**，但结构预留了继续加能力（电源控制、BMC 信息、
按键/复位、IB/SPX 交换机等）。

### 接口知识的来源

各 OEM 的 Redfish 端点、控制命令、sensor 命名不是猜的，来自对现有脚本的通读：

- 顶层 `nodefan/nodepsu/nodesensors/nodemon`、`func.sh`（GB200 常量）、`passwd.sh`；
- powershelf / NVSwitch：`psmon`、`pspsu`、`pspower`、`nvswmon`；
- 各 OEM 模板：`oem/{ami,byd,dell,dgx,gb200,gigabyte,lenovo,smc}/`；
- GB300 变体（Wistron/Gigabyte/SMC/Inventec 的 sensor 名与逐 sensor 路由）：
  `nodefan.Wistron`、`nodemon.Wistron`、`nodemon.v2.Wistron`、`nodemon.ipmi.Wistron`、
  `pspower.Wistron`。

> 改接口前，先回去读对应脚本确认字段，不要凭字段名猜测。

## 2. 架构总览

```
nodetools (入口)
  └─ nodetools_runtime.cli
       ├─ arguments.py   解析参数
       ├─ inventory.py   解析/生成 inventory
       ├─ selection.py   选择器 → 主机列表（含同行自动关联）
       ├─ reach.py       BMC ICMP 可达性检查（list/ping 命令用）
       ├─ runtime.py     并行引擎：建 Device、探测 ODM、跑能力/控制
       │    ├─ model.py       角色/ODM 探测 + 缓存
       │    ├─ caps/*.py      能力收集器（回退阶梯）
       │    ├─ oem/*.py       ODM 差异（附加路径 + 控制动作）
       │    └─ transport/*.py Redfish / IPMI / racadm
       └─ report.py      输出格式化
```

一次调用的数据流：

```
argv
 → Inventory.from_file(inventory.ini)         # 分组 + 变量 + 默认值
 → selection.select(...)                       # 名字/组/模式 → Host[]
 → Runner.run(hosts)  (ThreadPoolExecutor)
     每台: 解析角色凭据 → 建 Redfish+Ipmi → Device
           ├─ cap=oem: probe_redfish + classify
           ├─ 有 --action: 探测 ODM → oem.get(odm).fan_control/psu_control
           └─ 否则: caps.collector_for(cap)(device) → readings[]
 → report.render(results)                      # 行 / 汇总
```

## 3. 模块职责

| 文件 | 职责 | 备注 |
| --- | --- | --- |
| `config.py` | 常量：能力名、默认 jobs/timeout、缓存目录、角色↔分组、默认 chassis | 调参入口 |
| `errors.py` | 异常层级 | `CapUnsupported` 表示"该 ODM 不支持此动作" |
| `hostlist.py` | 主机名范围展开 | 见第 5 节，带单测 |
| `inventory.py` | inventory 解析 + `hosts.list` 生成器 | 语法见第 5 节 |
| `selection.py` | 选择器解析 + 同行自动关联 | `--no-expand` 关闭关联 |
| `reach.py` | BMC 的 ICMP 可达性检查 | `list`/`ping` 命令用；不涉及凭据 |
| `model.py` | `Device`、`probe_redfish`、`classify`、ODM 缓存 | 探测失败回退 `unknown` |
| `ladder.py` | "按顺序试，返回第一个非空" | 收集器的核心控制流 |
| `transport/redfish.py` | Redfish `get/get_raw/post/patch` | `verify=False`，连接超时 3s |
| `transport/ipmi.py` | ipmitool 封装，`sdr` / `raw` | 双模式传密码，见第 5 节 |
| `transport/racadm.py` | Dell racadm | 不可用时 `available()` 返回 False |
| `caps/common.py` | Reading 构造、health/speed/num、集合/传感器枚举 | 共享工具 |
| `caps/{fan,psu,temp}.py` | 三个能力的回退阶梯 | 见第 4 节 |
| `oem/base.py` | `Oem` 基类 + `_ipmi_raw` | |
| `oem/<odm>.py` | 单个 ODM 的控制动作/专用 chassis | 读数据主要靠通用阶梯 |
| `runtime.py` | 角色凭据解析、并行、错误隔离 | 每台独立 try/except |
| `report.py` | 输出行/汇总 | 兼容 `grep -v ,OK` |

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
`DGX` / `PDB_0` / `PowerShelf_0` 都能命中。

**Reading 统一结构**：`{id, name, value, health}`。health 取
`.Status.Health or .Status.State or "Unknown"`；powershelf 特判为 `On/Off`。

## 5. 关键约定

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
```

- 地址键：`host=` / `bmc_ip=` / `ansible_host=` / `ip=`，或第 2 个裸 token。
- 变量优先级：`[all:vars]` < 组 `:vars` < 行内 `key=value`。
- 角色来自分组（`compute/nvswitch/powershelf`），缺失时按名字模式兜底。
- 角色凭据字段：compute `bmc_*`、nvswitch `nvswitch_bmc_*`、
  powershelf `powershelf_bmc_*`（缺失回退 `bmc_*`）。
- `[nodehealth:defaults]` 提供 jobs/timeout/expand/summary 默认值，
  未显式传参时生效。
- `hosts.list` 生成器按 `DeviceType` 归组：`PhysicalNode→compute`、
  `PowerShelf→powershelf`、`Switch` 名含 `-gpu-nvl-` → `nvswitch`，否则 `switch`。

### hostlist 展开

自实现（不依赖外部 GPL 工具）：`node[01-03]`、`node[1-3]`、`gpu[001-003,007]`、
`x{8..10}`、括号可嵌套、顶层逗号分隔、去重保序。范围按低位宽度补零。

### 主机选择与同行自动关联

选择器支持：组名 / 单主机名 / hostlist 模式，逗号分隔可混用。
默认会为选中的 compute 主机自动补上**同一行**的 `-gpu-pwr-*`（psu/temp）和
`-gpu-nvl-*`（temp）——行键 = 名字按 `-gpu-` 截断。`--no-expand` 关闭。

`list` / `ping` 命令**不做**同行自动关联（`expand=False`）：列出/探测的就是你
选中的主机本身。典型流程是先用 `ping` 过滤出可通节点，再喂给检查命令：

```sh
reachable=$(./nodetools ping vdc1-pro2-t10-gpu-cp-[01-18] \
            | awk -F: '/pingable/{print $1}' | paste -sd, -)
./nodetools all "$reachable" --errors-only
```

### ODM 探测与缓存

`probe_redfish` 拉取 `/redfish/v1`、`Managers/1`、`Systems/1`、`Chassis/*` 的
Vendor/Model/Manufacturer，拼成字符串后按 `model.SIGNATURES` 匹配（具体厂商在前，
`nvidia` 兜底 `gb200`）。结果缓存到 `~/.cache/nodetools/oem/<ip>`；`--no-cache` 绕过。
**读数据不依赖 ODM**，探测只影响控制动作和 `oem` 能力输出。

### IPMI 密码双模式

- 默认：`-U user -P pass`（与旧脚本一致）。
- 环境模式：`--ipmi-env` 或 `NODETOOLS_IPMI_ENV=1` → `-U user -E`，
  密码经 `IPMI_PASSWORD` 传入，不进进程表。

优先级：显式 flag > 环境变量 > inventory。

### 输出

`<node>: <CAP>,<role>,<id>,<name>,<value>,<health>`，字段内空格/逗号替换为 `_`，
因此可 `grep -v ,OK`。空结果会输出 `ERROR no readings ...` 且进程退出码为 1。

## 6. 如何扩展

### 新增一个能力（cap）

1. 在 `config.CAPS` 加入能力名。
2. 新建 `caps/<cap>.py`，实现 `collect(device) -> list[reading]`，
   用 `ladder.first([...])` 串起多个回退步骤；复用 `caps/common.py` 的工具。
3. 在 `caps/__init__.py` 的 `COLLECTORS` 注册。
4. `arguments.CAP_COMMANDS` 会自动带上（它由 `CAPS` 推导）。
5. 补 `tests/test_caps.py`：用 `FakeRedfish` 造 JSON 响应断言解析结果。

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
python3 -m pytest -q          # 当前 38 passed
```

覆盖：hostlist 展开、inventory 解析/优先级/生成、选择器与同行关联、输出格式与
`grep -v ,OK` 兼容、ODM 注册、三个能力对 `FakeRedfish` 的解析、`list`/`ping`
命令与可达性检查（`subprocess` 打桩，不依赖网络）。

### 真机冒烟（改接口后必做）

```sh
# 从现有 hosts.list 生成 inventory
./nodetools inventory init --from /path/to/hosts.list -o /tmp/nt.ini
./nodetools inventory show -i /tmp/nt.ini | head

# 单台下钻，确认 ODM 与字段
./nodetools oem  vdc1-pro2-t10-gpu-cp-01 -i /tmp/nt.ini --no-expand -x
./nodetools fan  vdc1-pro2-t10-gpu-cp-01 -i /tmp/nt.ini --no-expand
./nodetools temp vdc1-pro2-t10-gpu-cp-01 -i /tmp/nt.ini --no-expand --summary
./nodetools psu  vdc1-pro2-t10-gpu-pwr-01 -i /tmp/nt.ini --no-expand

# 批量与只报错
./nodetools all vdc1-pro2-t10-gpu-cp-[01-03] -i /tmp/nt.ini --errors-only
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

- B200/B300（BXX 普通 HGX 机型）**本期未做**；`model` 已能识别通用厂商，后续加
  BXX 的命名/来源与对应阶梯即可。
- powershelf ODM 目前多归为 `unknown`（Delta 电源），只影响 `oem` 输出，不影响
  读 psu。
- `Switch` 组里非 `-gpu-nvl-` 的交换机暂归 `switch` 组，尚无对应能力。
- 控制动作（`--action`）只覆盖已确证的几条 IPMI raw / Redfish PATCH / racadm；
  新增前请回读对应 OEM 脚本确认字节。
- `ping` 用单包 ICMP（`ping -c 1 -W <t>`），不是旧 `nodeping` 的 flood ping；
  不需要 root，也不会有突发流量。无 BMC 地址的主机输出 `No BMC IP found`。
- 尚未做：结果落库/告警、`--json` 输出、IB/SPX 交换机能力。
