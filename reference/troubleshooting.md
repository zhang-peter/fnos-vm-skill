# 排错

## 鉴权

### `kind:"auth"` / HTTP 401 / code 10001

`ost` 过期或不是这个服务的会话。

1. 浏览器重新登录 `http://<host>:5666/`。
2. `fnos-vm login --host <host> --from-cdp`。
3. 再跑 `fnos-vm host` 验证。

不要用 `osrt`、fnos-cli 的 OAuth token 或 `Bearer`；它们对 trim.vm 无效。

### `login --from-cdp` 找不到 CDP

- 必须完全退出 Edge（包括后台/托盘），再用 `--remote-debugging-port=9222` 启动；Edge 已在运行时这个参数会被忽略。
- 检查 `http://127.0.0.1:9222/json/version` 是否返回 JSON。
- 换端口用 `--cdp http://127.0.0.1:<port>`。
- 不想折腾现有 Edge：`fnos-vm login --host <host> --from-cdp --launch`，在打开的受控窗口里登录。

### CDP 里没有 `ost`

- 确认登录的是同一个 host/端口（默认 5666）。
- `--host` 必须与实际 NAS 匹配；多 host 时 cookie domain 可能不同。
- 登录后刷新一次 `/vm` 页面，再重试。

## 业务错误

| 现象 | 处理 |
|---|---|
| `12021` VM 不存在 | 用 `list` 看 internal name；`info` 支持 title/前缀 |
| `12030/12031` 开机态不可编辑/删除 | 先 `stop`，必要时 `--force` |
| `12041` 磁盘空间不足 | `host` 看卷剩余；减小 `--disk`/`--disk-size` |
| `12044` IDE 最多 4 块盘 | 改用 `--bus virtio` 或 `sata` |
| `12053` 不支持在线快照 | 关机后 `snapshot create` |
| `12061` 虚拟网络不存在 | `host` 看 OVS 网络名，或 `--no-network` |
| `preflight` | 按 `details` 数组逐条修正；`--dry-run` 可重复预检 |

## 超时

- `wait` 超时：返回 `code:"timeout"` 和当前状态；关机慢时增大 `--timeout`，或 `stop --force`。
- 网络超时：检查 `http://<host>:5666/vm/api/v1/domain/list` 是否可达；HTTPS 自签名加 `--insecure`。
- 下载超时：`image download --timeout 300`；网络中断会删除 `.part`，需要重下。

## 创建成功但本地未登记

如果 `create` 返回成功但 config 写入失败（权限/磁盘满），VM 已在 NAS 上。用：

```bash
fnos-vm adopt <internal-name> --yes
```

不要重复 create，否则会重复占磁盘。

## VNC

- `vnc --check` 返回 101 说明 WebSocket + 鉴权 OK；浏览器直接打开 `ws://` URL 不会显示画面。
- `vnc --screenshot` / `--keys` 使用内置 RFB 客户端（None 认证、Raw 编码）；VNC 密码、Tight/ZRLE 等编码不支持。
- 若 401，重新 login；若 404，确认 VM 存在且 internal name 正确。

## 镜像/seed

- `tar -tf seed.iso` 应看到 `user-data`、`meta-data`（大小写不敏感）。
- cloud-init 不执行：确认 seed 在 `cdroms.driverFilePath`，卷标 `CIDATA`，且 VM 从导入磁盘启动。
- 静态 IP 不生效：`network-config` 的 MAC 必须与 `networks[].macAddr` 完全一致；网卡名由 `set-name: eth0` 固定。
- `image resize` 报 backing file：该 qcow2 是链接克隆，先 flatten（需要 qemu-img），本工具不处理。

## Python/入口

- `fnos-vm: Python 3.9+ was not found`：设置 `FNOS_VM_PYTHON` 为解释器绝对路径，或直接 `python scripts/fnos-vm.py`。
- Windows 执行策略阻止 `.ps1`：用 `.cmd` 或 `python scripts/fnos-vm.py`。
- `--json` 输出被日志污染：确认没有把 stderr 重定向到 stdout（`2>&1`）；`--json` 的日志本来就在 stderr。
