# 通过 OS 管理 BMC 用户

compute tray 和 NVSwitch 共用 `nodebmc HOSTS user`，通过 OS SSH 调用本机
`ipmitool -I open`，不依赖原 BMC root 的 Redfish 登录权限。

```bash
# 预览，不修改设备
./nodebmc 'COMPUTE,SWITCH' user --name nvis --sol

# 交互输入新账号密码，再执行
read -r -s -p 'New BMC password: ' NEW_BMC_PASSWORD
export NEW_BMC_PASSWORD
./nodebmc 'COMPUTE,SWITCH' user --name nvis --sol --password-env NEW_BMC_PASSWORD --apply
unset NEW_BMC_PASSWORD
```

将 `--apply` 换成 `--verify-only` 可只读检查现有账号及 Redfish。
默认复用唯一同名账号，否则优先选择空闲 ID 8，再选择其他空槽位。
可用 `--id`、`--channel` 指定槽位及通道。槽位冲突、重名或保留 ID 会停止该节点。
工具不修改 root，不删除其他账号，不自动改写 inventory。
对已有同名账号执行 apply 会更新其密码和管理员权限。

`--sol` 在 compute 上启用 SOL 通道及用户 payload 权限并回读。
NVSwitch 跳过所有 SOL 操作，混合批次也如此；不修改 BIOS 串口设置。
`--jobs` 指定并发，默认 4；逐机输出 JSON，任一失败返回非零。
部分成功会列出已完成步骤，不自动重试或回滚密码变更。

OS 登录优先使用角色字段 `compute_os_*`、`nvswitch_os_*`，
然后使用通用 `os_host`、`os_user`、`os_password`/`os_pass`。
host 默认节点名，账号未配置时使用当前执行账号，支持 SSH 密钥。
可用 `--os-user`、`--os-password-env` 覆盖；OS 和 BMC 凭据独立。
非 root OS 用户需要非交互 sudo。设备 OS 需要 python3 和 ipmitool；
密码 SSH 登录还需要 headnode 安装 sshpass。
新 BMC 密码通过 SSH stdin 传递，不写入 SSH 命令文本或代码，最长 20 UTF-8 字节，
不接受控制字符。Redfish 直读 inventory system_id 或 OEM 固定资源；
必要时用 `--verify-path /redfish/v1/Systems/RESOURCE` 指定。

## 验证范围

451 项自动测试通过。新增测试覆盖真实 CLI 派发、共享 SSH stdin、角色差异、
槽位冲突、写入顺序、部分失败、SOL 回读和 Redfish 错误路径。
两类样机均通过 OS/IPMI 只读预览；NVSwitch 现有 nvis 的 Redfish 验证通过。
compute 样机 SOL 已启用，但 headnode 到其正确 BMC 地址的 HTTPS 连接超时。
本次未执行实机账号写入，不能声称写入和所有机型均已认证。
