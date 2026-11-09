# 命令与字段

所有命令都接受全局选项：`--host`、`--port`、`--scheme`、`--config`、`--timeout`、`--insecure`、`--json`、`--table`、`--debug`。全局选项放在子命令前后都可以（`wait` 的 `--timeout` 是等待超时）。

## 连接

### `login`

```bash
fnos-vm login --host <host> --from-cdp [--cdp http://127.0.0.1:9222] [--launch]
fnos-vm login --host <host> --token <ost>
```

| 选项 | 说明 |
|---|---|
| `--from-cdp` | 从 CDP 读取 `ost`/`fnos-token` cookie |
| `--cdp` | CDP 端点，默认 `http://127.0.0.1:9222` |
| `--launch` | CDP 不在时启动受控 Edge |
| `--edge-path` | 指定 msedge/chromium 路径 |
| `--wait-timeout` | 等待 cookie 的秒数，默认 180 |
| `--no-validate` | 保存前不调用 API |

### `logout`

删除 `~/.fnos-vm/config.json`，包括本地所有权登记。

## 只读

### `host`

返回：

```json
{
  "cpu": {"name": "...", "core": 8, "thread": 16},
  "memory": {"totalBytes": 34359738368, "allocatedBytes": 4294967296, "freeBytes": 30064771072},
  "kvm": true, "iommu": true, "sriov": false,
  "ovs": {"initialized": true, "networks": [{"name": "eno1-ovs", "state": "connected"}]},
  "volumes": [{"index": 1, "path": "/vol1", "sizeBytes": ..., "usedBytes": ..., "freeBytes": ...}],
  "vms": {"count": 1, "allocatedMemoryBytes": 4294967296}
}
```

### `list`

返回列表，每个元素是 `/domain/list` 与 `/domain/info` 的合并（info 成功时含 CPU/内存/磁盘/网络）。`stateText` 是英文状态标签。

### `info <ref>`

`ref` 可以是 internal name、完整 title、大小写不敏感的 title，或唯一 title 前缀。返回完整 info + `managedByThisTool` + `vncUrl`。

### `vnc <ref> [--check] [--screenshot PATH] [--keys SPEC] [--wait SECONDS]`

```json
{"url":"ws://host:5666/vm/api/v1/vnc/<internal-name>","vncName":"<internal-name>","checked":true,"status":"RFB session","screenshot":"console.png","framebuffer":{"width":1280,"height":800},"keys":["key","text"]}
```

- `--check` 仅做 WebSocket 握手。
- `--screenshot` 连接 RFB、请求 Raw framebuffer 并保存 PNG（纯标准库写 PNG）。
- `--keys` 驱动键盘，逗号分隔：
  - `key:enter` / 裸 `enter`：单键
  - `combo:ctrl-x`：组合键（`+` 或 `-` 分隔）
  - `text:...`：逐字符输入（支持 US 键盘常见符号）
  - `sleep:1.5`：等待
- `--wait` 在连接后、发送按键前等待。
- 例：
  ```bash
  fnos-vm vnc ubuntu --keys 'key:e,key:down,key:end,text:autoinstall ds=nocloud,key:ctrl+x'
  fnos-vm vnc ubuntu --screenshot console.png
  ```
- RFB 只支持 None 认证与 Raw 编码；VNC 密码认证不支持。

### `wait <ref> --state running --timeout 120 [--interval 2]`

轮询 `/domain/info`。支持 `running`/`stopped`/`paused`/`crashed`/`blocked` 及原始大写状态。超时返回 `kind:"business"`、`code:"timeout"`。

## 生命周期

### `create`

```bash
fnos-vm create --name <title> [--file vm.json] [--dry-run]
  [--os-type linux|windows|others] [--os-version <v>]
  [--cpu N] [--mem 4G] [--disk 20G[@vol1]]...
  [--image /vol1/.../x.qcow2 --disk-size 20G]
  [--iso /vol1/.../x.iso] [--driver-iso ...] [--seed /vol1/.../seed.iso]
  [--network eno1-ovs[:e1000][@mac]]... [--no-network]
  [--bus virtio|scsi|sata|ide] [--hypervisor kvm|qemu]
  [--chipset q35|i440fx] [--firmware efi|bios] [--video vga|virtio|vmvga|qxl]
  [--boot default|cdrom|disk-0] [--autostart|--no-autostart]
  [--clipboard|--no-clipboard] [--desc TEXT]
```

- `--file` 可以是 API 形状 JSON，也接受便捷字段：`name`（=title）、`cpu`、`memory`/`mem`、`disks[].size`、`iso`、`driverIso`、`seed`、`image`、`imageSize`/`diskSize`、`network`/`networks`。
- `--image` 是 **NAS 上的远端路径**；本地文件先用 `image push` 上传。
- `--disk-size` 是导入磁盘的容量；cloud image 建议先 `image resize` 到目标容量。
- 不传 `--disk` 且不传 `--image` 时创建空壳机（可用但不可启动 OS）。
- 不传 `--network` 时默认接第一个 OVS 网络；`--no-network` 创建无网卡 VM。
- `--dry-run` 返回 payload + preflight facts，不写。

### `start|stop|reboot|pause|resume`

```bash
fnos-vm start <ref> [--wait 30] [--interval 2]
fnos-vm stop <ref> [--force] [--wait 30]
fnos-vm reboot <ref> [--wait 60]
fnos-vm pause <ref>
fnos-vm resume <ref>
```

- 所有写操作默认拒绝未登记 VM，除非 `--force-other`。
- `stop --force` 调 `/domain/forceshutdown`；否则 `/domain/shutdown`。
- 不带 `--wait` 时等待约 0.7s 后取 after；异步关机可能仍是 RUNNING/SHUTTING_DOWN。

### `modify`

```bash
fnos-vm modify <ref> [--cpu N] [--mem 4G] [--desc TEXT] [--title NEW]
  [--autostart true|false] [--clipboard true|false] [--boot ...] [--file raw.json]
```

基于当前 `/domain/info` 构造完整 payload，删除 `memCapacityUnit`/`memCapacity` 后回传。改 title 前会查重。仅关机态可由服务端接受。

`--file` 在完整 payload 上做顶层键覆盖（`payload.update`），因此能改 CLI 未暴露的字段；其中 `disks`、`networks` 是**整体替换**，必须把其它盘/网卡原样带上。

#### 磁盘扩容

```bash
# 1) 关机：modify 与 resize 都只接受 SHUTOFF
fnos-vm stop ubuntu --wait 60

# 2) 有内部快照必须先删：qcow2 v2 带快照时 qemu-img resize 会失败
fnos-vm snapshot list ubuntu
fnos-vm snapshot delete ubuntu <snap> --yes

# 3) 取当前 disks，只改 capacityInBytes（字节），其余字段原样保留
fnos-vm --json info ubuntu

cat > disk.json <<'EOF'
{"disks":[{"pool":1,"volumePath":"/vol1/vm/pool/<uuid>-xxxx.qcow2","busType":"virtio","capacityInBytes":161061273600}]}
EOF
fnos-vm --json modify ubuntu --file disk.json

# 4) 开机
fnos-vm start ubuntu --wait 60
```

guest 内再扩分区（以 `vda` + 根分区 `vda2` 为例，分区号以 `lsblk` 为准）：

```bash
lsblk                      # 确认 vda 已是新容量
sudo growpart /dev/vda 2
sudo resize2fs /dev/vda2   # ext4 在线扩容
df -h /
```

- 服务端实际执行 `qemu-img resize`，是**真扩容**，不是只改元数据。
- 失败时返回 `ok:false`，整单不生效（CPU/内存也不会改），常见原因是 qcow2 v2 带内部快照。
- 只能扩不能缩；`capacityInBytes` 是字节，不是 `150G` 这种字符串。
- 扩容建议单独提交一次 `modify`，确认成功后再改 CPU/内存。
- 扩容后记得确认 guest 内已 `growpart`+`resize2fs`，否则 `df` 仍是旧容量；必要时加 swap 兜底。

### `delete`

```bash
fnos-vm delete <ref> --yes [--force-other]
```

运行中先 stop。成功后从本地所有权登记移除。

### `adopt`

```bash
fnos-vm adopt <ref> --yes
```

把现有 VM 登记为本工具管理；之后可正常写操作。不会修改 VM。

## 快照

```bash
fnos-vm snapshot list <ref>
fnos-vm snapshot create <ref> --desc "..." [--snap-name NAME] [--force-other]
fnos-vm snapshot revert <ref> <snap> --yes [--force-other]
fnos-vm snapshot delete <ref> <snap> --yes [--force-other]
```

- `create` 默认 snapshotName 为 `snap-YYYYmmdd-HHMMSS`；重名自动加 `-2`。
- 在线快照可能返回 12053；先关机。
- `list` 不需要所有权。

## 镜像

### `image list`

列出内置别名和 URL。

### `image download <alias|url> --dest PATH [--sha256 HEX] [--force]`

流式下载 + SHA-256 校验；`.part` 原子替换。别名包括 Ubuntu 22.04/24.04/24.04-minimal/26.04、Debian 12/13、Alpine 3.22。**是否下载由使用者决定。**

### `image resize <qcow2> --size 20G [--output PATH]`

只改 qcow2 头的 virtual size，不写数据；要求无 backing file，且不能缩小。云镜像启动后由 cloud-init growpart 扩展分区。

### `image seed --output seed.iso ...`

| 选项 | 说明 |
|---|---|
| `--user` | cloud-init 用户，默认 `ubuntu` |
| `--password` | 明文密码（写入 `chpasswd`，不建议；不能含 `:`） |
| `--ssh-key-file` | 公钥文件，可重复 |
| `--ssh-key` | 公钥字面量，可重复 |
| `--hostname` | 主机名 |
| `--static-ip` / `--prefix` / `--gateway` / `--dns` / `--mac` | 按 MAC 匹配的静态网络配置 |
| `--network-config` | 直接使用给定的 netplan/network-config v2 文件 |

生成 ISO9660+Joliet，卷标 `CIDATA`，文件 `user-data`、`meta-data`、可选 `network-config`。

### `image autoinstall --output seed.iso ...`

生成 Ubuntu Subiquity autoinstall 的 NoCloud seed：

```bash
fnos-vm image autoinstall --output seed.iso --user ubuntu --password '...' \
  --ssh-key-file id.pub --hostname ubuntu2604 \
  --static-ip 192.168.1.250 --gateway 192.168.1.1 --mac 4e:cd:ff:aa:bb:01 \
  --storage-layout direct --timezone Asia/Shanghai --shutdown poweroff
```

| 选项 | 说明 |
|---|---|
| `--password` / `--password-hash` | 明文密码会自动用 OpenSSL/`crypt` 生成 SHA-512 crypt；也可直接给 `$6$...` |
| `--ssh-key-file` / `--ssh-key` | 授权公钥，可重复 |
| `--storage-layout` | `direct`（默认）/ `lvm` / `zfs` |
| `--static-ip` / `--prefix` / `--gateway` / `--dns` / `--mac` | 写入 autoinstall `network` |
| `--shutdown` | `poweroff`（默认，便于切启动顺序）或 `reboot` |
| `--package` / `--late-command` | 可选包/安装后命令 |

`identity.password` 必须是 crypt 哈希；`--password` 在 Windows 上优先调用 Git 自带 OpenSSL。

### `image push <file> --remote-dir /vol1/... [--profile fnos] [--fnos-cli PATH]`

调用 `fnos-cli file upload --overwrite replace`。需要 fnos-cli Skill 已登录；没有时给出替代说明。

### `image prepare <alias|url> --dest PATH --disk-size 20G ...`

下载 + 扩容 + 生成 seed；给 `--remote-dir` 时顺带上传，并打印建议的 `create` 命令。支持 `--user`、`--password`、`--ssh-key-file`/`--ssh-key`、`--hostname`、`--force`。

## JSON 形状

- 读：`{"ok":true,"data": ...}`
- 写：`{"ok":true,"data":{"action":"start","target":{...},"before":{...},"after":{...}}}`
- 错误：`{"ok":false,"error":{"kind":"...","code":...,"msg":"..."}}`
