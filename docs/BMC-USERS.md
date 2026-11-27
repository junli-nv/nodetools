# 通过 OS 管理 BMC 用户

`nodeos` 是 OS 操作的统一入口；`bmc` 是当前功能组，通过 OS SSH 调用本机 IPMI。
`nodebmc` 保留 BMC 接口的信息、时间和重启功能。

```bash
./nodeos 'nvsw[01-09]' bmc adduser nvis 'ExamplePass123!'
./nodeos nvsw01 bmc deluser nvis
./nodeos nvsw01 bmc moduser nvis 'NewExample123!'
./nodeos -x nvsw01 bmc listuser
```

listuser 后可跟用户名筛选。checkuser 用户名 密码可只读验证已有账号。
adduser 自动复用同名账号或选择空槽位，配置管理员权限；compute 配置 SOL，
NVSwitch 跳过。moduser 只改密码并保留权限，目标必须存在。
deluser 先禁用、撤权、清用户名，再回读；不存在时视为已删除。
不修改或删除 root 与保留槽位，不自动改写 inventory。

ID、通道、验证路径自动处理。已知路径直读，未知布局用目标账号有限发现。
增改后的 Redfish 验证失败会报告已完成步骤，不自动重试。
普通帮助展示常见写法；`nodeos HOST bmc --help-full` 提供高级覆盖参数。
OS 凭据沿用 role_os_*、os_* 和 SSH 密钥；非 root 需要非交互 sudo。
设备需要 python3 和 ipmitool，headnode 密码 SSH 登录需要 sshpass。
-x 显示 SSH 目标、IPMI 步骤和验证状态，密码写入脱敏。

功能组注册在 os_cli.RESOURCES，各自负责参数和处理，共用 OS SSH 传输。
账号事务在设备端互斥，避免并发争用槽位。
IPMI 参考：[官方实现](https://github.com/ipmitool/ipmitool/blob/master/lib/ipmi_user.c)。

本轮自动测试覆盖 CRUD、保护、调试和帮助。按用户要求停止访问旧样机；
未完成实机增删改验证，不据此宣称所有固件均已认证。
