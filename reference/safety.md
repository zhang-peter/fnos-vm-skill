# 安全模型

`fnos-vm` 的写操作直接作用于 libvirt 域和 `/vol{n}/vm/pool/` 磁盘。所有保护都在 CLI 侧实现，服务端不会替你确认。

## 1. 破坏性操作必须显式确认

以下命令必须带 `--yes`，否则参数错误（退出码 2）：

- `delete <name> --yes`
- `snapshot revert <name> <snap> --yes`
- `snapshot delete <name> <snap> --yes`
- `adopt <name> --yes`

执行前会把目标 VM/快照的 name、title、磁盘数量或创建时间写到 stderr；`--json` 模式下这些信息在 `data.before` / `data.snapshot` 中。

`delete` 会删 VM 并删磁盘，不可恢复。运行中的 VM 会被拒绝，先 `stop`。

## 2. 所有权保护

本地 `~/.fnos-vm/config.json` 的 `vms` 字段记录本工具创建/登记过的 VM（internal name + title + uuid + createdAt）。

- 任何写操作（start/stop/reboot/pause/resume/modify/snapshot create/revert/delete/delete）先检查所有权。
- 未登记 VM 默认拒绝，错误 `kind:"safety"`。
- 放行方式：
  - `fnos-vm adopt <name> --yes`：永久登记，之后正常管理；
  - `--force-other`：仅本次放行。
- 现有 `Tiny11` 不会被自动接管；需要时显式 adopt/force。

config 丢失或换机器后，已创建的 VM 会变成「未登记」。此时用 `adopt`，不要用 `--force-other` 长期绕过。

## 3. 路径规则

所有 `--iso`、`--driver-iso`、`--seed`、`--image`、磁盘 `sourcePath` 必须是：

- 以 `/vol{n}/` 开头的绝对 POSIX 路径；
- 不含 `..`、`.` 段、反斜杠、NUL、换行；
- ISO 以 `.iso` 结尾；image 以 `.qcow2`/`.img`/`.raw` 等结尾。

相对路径、Windows 路径、`/tmp/...`、`/vol1/../...` 全部拒绝。

## 4. 建机预检

`create` 在写之前执行：

- `domain/availableTitle` 标题查重；
- CPU ≤ `hardwareInfo.cpu.thread`；
- 内存 ≤ `mem.total` − 所有已定义 VM 的 `memCapacityInBytes` 之和；
- 每个卷的磁盘总量 ≤ `size - used`；
- `hypervisor=kvm` 时 `hostdev/support.kvm` 必须为 true；
- 有网卡时 OVS 已初始化，`source` 在 `/ovs/listDetail` 中存在；
- `bootType=cdrom` 时 OS ISO 非空；
- 不接受的直通字段（`hostpcis`/`hostusbs`）直接拒绝。

任何一项失败返回 `kind:"preflight"`，退出码 1，不创建任何对象。`--dry-run` 可以只跑预检。

## 5. 明确不做

- PCI / USB 直通写操作（`hostdev/sign` 等）。
- OVA 导入/导出。
- 磁盘格式转换。
- `filemanager/dir/delete`。
- 删除任意 NAS 文件；`image push` 只上传，不删除远端文件。

## 6. 令牌

- `ost` 等价于 Web 会话，拿到即可操作 VM；按密码对待。
- 只写入 `~/.fnos-vm/config.json`，Windows ACL/ POSIX 0600。
- 不出现在 stdout、JSON、日志、异常消息中；`login` 只返回 host、端口、VM 数量和配置路径。
- 不要用 `--token` 把值写进 shell 历史；优先 CDP；如必须手动，使用完清理历史。
- 401/10001 直接报「会话失效」，不重试、不降级。
