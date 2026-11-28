# Redfish 归档清单

按平台、代际、厂商、角色和型号组织；同型号不同固件合用一份型号清单。

```text
<platform>/<generation>/
  MANIFEST.json
  SHA256SUMS
  *.redfish.json.gz
  manifests/<oem>/<role>/<model>.json
```

- `MANIFEST.json`：schema 2 总索引，只有型号、固件摘要及子清单路径。
- 型号清单：schema 2，保留各固件的 `samples`、采集范围、精确请求、回放和覆盖缺口。
- 总索引 `missing_firmware` 对应子清单 `missing_archives`；部分采集和未验证范围如实保留。
- `SHA256SUMS`：覆盖总索引、所有型号清单和压缩归档，路径相对平台/代际目录。
- `samples[].file` 同样相对平台/代际目录；已有归档文件名和路径不变。

旧 schema 1 顶层的 `samples` 已迁入型号清单。读取程序应先遍历
`models[].manifest`，再读取各型号的 `samples`。

新增证据时更新对应型号清单、总索引的固件摘要及校验和。保持 JSON 可读；
Redfish 响应继续用 gzip。自动测试检查引用、身份和固件一致性、遗漏文件、
校验和、认证覆盖，以及全部已声明的严格回放。

索引入口：[HGX B300](hgx/b300/MANIFEST.json)、[NVL GB300](nvl/gb300/MANIFEST.json)。

从仓库根目录运行全部检查：

```bash
python3 -m pytest -q
git diff --check
```

也可在对应平台/代际目录运行 `sha256sum -c SHA256SUMS`。
