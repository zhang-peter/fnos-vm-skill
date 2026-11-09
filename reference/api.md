# fnOS VM API 契约（trim.vm 0.9.3）

> 目标：`http://<host>:5666/vm/api/v1`
> 依据：`/vm` 前端 bundle 静态分析 + 2026-11-08/09 真机实测
> 本文件只记录已验证或明确标注为推断的内容。

## 1. 鉴权

```http
Authorization: trim <ost>
```

- `ost` 是登录 fnOS Web 后设置的 **HttpOnly cookie**，长度 44，base64（可含 `=`）。
- 直接对 `http://<host>:5666/vm/api/v1/...` 携带即可；不需要 `Cookie` 头，也不需要浏览器。
- `osrt` 不是这个服务的会话凭据；`Bearer`、`fnos-token`（旧版）也不被 trim.vm 接受。
- 未授权时服务可能返回 HTTP 401 空体，或 `{"code":401,"message":"unauthorized"}`；业务层未登录码是 `10001`。
- token 会过期。任何 401/10001 都重新 `login --from-cdp`，不要重试。

获取方式：

- CDP：`Network.getAllCookies` / `Storage.getCookies`，从受控 Edge 读取 `ost`。
- 手动：DevTools → Application → Cookies → `ost`。

## 2. 响应封套

```json
{ "code": 0, "msg": "success", "data": ... }
```

- `code === 0` 成功；其他值为失败。
- 部分接口成功时 `data` 省略（如 start/stop/delete）。
- 常见错误码：

| code | 含义 |
|---|---|
| 401 / 10001 | 未登录/会话失效 |
| 10002 | 非管理员 |
| 12021 | VM 不存在 |
| 12024 | 无法启动 |
| 12030 / 12031 | 开机态不可编辑 / 不可删除 |
| 12041 | 磁盘空间不足 |
| 12044 | IDE 控制器最多 4 块盘 |
| 12053 | 不支持在线快照 |
| 12061 | 虚拟网络不存在 |
| 12100 | 异步任务不存在 |

服务端 `msg` 可能是英文；CLI 原样透传并附 code。

## 3. 接口

基址 `/vm/api/v1`。前端把每个接口声明为 `"<METHOD> <path>"`；`domain/list` 的字面量误写为 `"Get /domain/list"`，仍按 GET 解析。

### 3.1 虚拟机

| 方法 | 路径 | 入参 | 说明 |
|---|---|---|---|
| GET | `/domain/list` | — | 列表（字段较少） |
| GET | `/domain/info` | `name` | 完整设备模型 |
| GET | `/domain/status` | `names`（逗号分隔） | 状态 + 监控 |
| GET | `/domain/availableTitle` | `name`, `title` | 标题是否可用，`data:boolean` |
| POST | `/domain/create` | 见 §4 | 新建 |
| POST | `/domain/modify` | 完整 info + 变更 | 仅关机态 |
| POST | `/domain/start` | `{name}` | 开机 |
| POST | `/domain/shutdown` | `{name}` | 正常关机 |
| POST | `/domain/forceshutdown` | `{name}` | 强制关机 |
| POST | `/domain/reboot` | `{name}` | 重启 |
| POST | `/domain/pause` | `{name}` | 暂停 |
| POST | `/domain/resume` | `{name}` | 恢复 |
| POST | `/domain/delete` | `{name}` | 删机 + 删磁盘 |

状态：`RUNNING` `SHUTOFF` `PAUSED` `BLOCKED` `CRASHED` `SHUTTING_DOWN` `OVA_EXPORT`。

`/domain/list` 真实返回（2026-11-09）：

```json
{
  "name": "<internal-name>", "uuid": "...", "title": "Tiny11", "desc": "",
  "state": "SHUTOFF", "osType": "windows", "osVersion": "11",
  "totalDisksCapacity": 40, "totalDisksCapacityInBytes": 42949672960,
  "clipboard": true
}
```

`/domain/info` 真实返回（Tiny11）：

```json
{
  "name": "<internal-name>", "uuid": "...", "title": "Tiny11", "desc": "",
  "state": "SHUTOFF", "osType": "windows", "osVersion": "11", "autostart": false,
  "chipset": "q35", "firmware": "efi", "hypervisor": "kvm",
  "cpuMode": "host-passthrough", "cpuCoreCnt": 4,
  "memCapacityUnit": "GiB", "memCapacityInBytes": 4294967296,
  "videoType": "vga", "bootType": "", "bootIndex": 0,
  "networks": [{ "type": "bridge", "source": "eno1-ovs", "nicModel": "e1000", "macAddr": "4e:cd:ff:aa:bb:02" }],
  "disks": [{ "pool": 1, "poolName": "vol1",
              "volumePath": "/vol1/vm/pool/<uuid>-xxxx.qcow2",
              "busType": "sata", "capacityInBytes": 42949672960, "capacityUnit": "GiB" }],
  "cdroms": { "osFilePath": "/vol1/1000/Temp/Tiny11-...iso",
              "driverFilePath": "/vol1/1000/Temp/virtio-win-...iso" },
  "hostpcis": [], "hostusbs": [], "clipboard": true
}
```

注意：

- 内存单位响应是 `GiB`；请求里写 `GB` 也可以，服务端会归一化。
- `bootType` 空串等价于默认引导顺序。
- `disks[].pool` 是 `/system/allVolsInfo` 的 `vols[].index`，不是卷名。
- 两个光驱槽：`osFilePath` 安装介质，`driverFilePath` 驱动盘；cloud-init seed 放第二槽。

### 3.2 快照

| 方法 | 路径 | 入参 |
|---|---|---|
| GET | `/snapshot/list` | `name` |
| POST | `/snapshot/create` | `name`, `snapshotName`, `snapshotDesc` |
| POST | `/snapshot/delete` | `name`, `snapshotName` |
| POST | `/snapshot/revert` | `name`, `snapshotName` |

实测返回：

```json
{ "name": "snap-20261109-001408", "description": "before upgrade",
  "createdTime": 1794154448, "vmState": "SHUTOFF" }
```

在线快照受 `12053` 限制；快照态改硬件受 `12032` 限制。

qcow2 v2 镜像带内部快照时，`/domain/modify` 扩容磁盘会失败（`qemu-img` 不支持），见 §5.1。

### 3.3 宿主机

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/system/hardwareInfo` | CPU/内存/GPU/BIOS |
| GET | `/system/allVolsInfo` | 存储卷 |
| GET | `/hostdev/support` | `{kvm, iommu, sriov}` |
| GET | `/ovs/isInited` | OVS 是否初始化 |
| GET | `/ovs/listDetail` | 虚拟网络 |

`hardwareInfo` 里的 `vm.available` 实测为 0，但与 `hostdev/support.kvm=true` 矛盾；以 `hostdev/support` 为准。

`allVolsInfo` 实测：

```json
{ "count": 1, "vols": [{ "index": 1, "sysname": "dm-0", "state": 0,
  "size": 952202952704, "used": 63734296576 }] }
```

卷路径由 `index` 推导为 `/vol{index}`。

### 3.4 VNC

```
ws://<host>:5666/vm/api/v1/vnc/<name>
wss://<host>:5666/vm/api/v1/vnc/<name>   # https 页面
```

- 实测 `<name>` 为 `domain/info` 的 `name`（本次 `<internal-name>`）；前端 bundle 里变量叫 `vncName`，若未来 info 返回 `vncName`，CLI 会优先使用。
- 握手带 `Authorization: trim <ost>`，实测返回 `101 Switching Protocols`。
- 浏览器里的 WebSocket 不能自定义 Authorization；这个 URL 主要给脚本/RFB 客户端使用，浏览器控制台仍依赖同源 cookie。
- 本工具实现了最小 RFB 客户端（None 认证、Raw framebuffer、键盘事件），`vnc --screenshot` / `--keys` 可用；不支持 VNC 密码、Tight/ZRLE。

## 4. 新建载荷（实测可用）

2026-11-09 用以下载荷成功创建并删除空壳 VM（1 vCPU / 1 GiB / 1 GiB / 无 ISO）：

```json
{
  "title": "dsh-selftest",
  "osType": "linux",
  "osVersion": "other",
  "hypervisor": "kvm",
  "chipset": "q35",
  "firmware": "efi",
  "videoType": "vga",
  "cpuCoreCnt": 1,
  "memCapacity": 1,
  "memCapacityUnit": "GB",
  "memCapacityInBytes": 1073741824,
  "autostart": false,
  "clipboard": true,
  "desc": "temporary",
  "bootType": "default",
  "bootIndex": 0,
  "cdroms": { "osFilePath": "", "driverFilePath": "" },
  "disks": [
    { "busType": "sata", "pool": 1, "capacityInBytes": 1073741824, "actionType": "create" }
  ],
  "networks": [
    { "type": "bridge", "source": "eno1-ovs", "nicModel": "e1000", "macAddr": "4e:cd:ff:..." }
  ],
  "hostpcis": [],
  "hostusbs": []
}
```

- `actionType:"create"` 实测被接受；新建磁盘也可以省略 `actionType`。
- `networks` 可以为 `[]`（无网卡）。
- `cpuCoreCnt` 上限是宿主机线程数；`memCapacityInBytes` 上限是宿主机总量 − 已分配（服务端也会校验）。
- `bootType`：`default` / `disk-<n>` / `cdrom`；仅当 `cdroms.osFilePath` 非空才允许 `cdrom`。
- 磁盘 `busType`：`virtio` / `scsi` / `sata` / `ide`；IDE 最多 4 块。
- 路径必须是 NAS 绝对路径 `/vol{n}/...`；**没有 ISO/OVA 上传接口**，镜像必须先用其他方式放到卷上。

### 4.1 导入 qcow2（实测）

```json
{
  "busType": "virtio",
  "pool": 1,
  "capacityInBytes": 67108864,
  "actionType": "import",
  "sourcePath": "/vol1/1000/Temp/test.qcow2"
}
```

实测：创建成功，`/domain/info` 返回

```json
{ "pool": 1, "poolName": "vol1", "volumePath": "/vol1/vm/pool/<uuid>-xxxx.qcow2",
  "busType": "virtio", "capacityInBytes": 67108864, "capacityUnit": "MiB" }
```

即服务端把 `sourcePath` 导入到 `/vol{n}/vm/pool/`，`capacityInBytes` 使用你给的值。导入磁盘的 `capacityUnit` 可能是 `MiB`，不影响使用。

### 4.2 cloud-init seed

本工具用纯 Python 生成 ISO9660 + Joliet 的 `CIDATA` ISO，包含：

- `user-data`：`#cloud-config`，用户、sudo、SSH key、可选密码；
- `meta-data`：`instance-id`、`local-hostname`；
- 可选 `network-config`：DHCP 或按 MAC 匹配的静态地址。

把 seed ISO 放到 `cdroms.driverFilePath`。cloud-init 的 NoCloud datasource 会扫描块设备寻找卷标 `CIDATA`。

## 5. 修改载荷

前端把 `/domain/info` 的完整对象（删掉 `memCapacityUnit`、`memCapacity`）加上 `name`/`uuid` 回传。实测：

```json
{
  "name": "<internal-name>", "uuid": "...", "title": "dsh-modtest",
  "state": "SHUTOFF", "osType": "linux", "osVersion": "other",
  "autostart": false, "chipset": "q35", "firmware": "efi",
  "hypervisor": "kvm", "cpuMode": "host-passthrough",
  "cpuCoreCnt": 2, "memCapacityInBytes": 2147483648,
  "videoType": "vga", "bootType": "", "bootIndex": 0,
  "networks": [...], "disks": [...], "cdroms": {...},
  "hostpcis": [], "hostusbs": [], "clipboard": true,
  "desc": "after"
}
```

返回 `code:0`；再查 info，CPU/内存/desc 已更新。

### 5.1 磁盘扩容（实测）

`disks` 是**全量替换**：把 `/domain/info` 里的 `disks` 数组原样回传，只改目标盘的 `capacityInBytes`（字节），服务端会真的执行

```
qemu-img resize /vol{n}/vm/pool/<uuid>-xxxx.qcow2 <capacityInBytes>
```

实测（2026-11-10，fnOS 1.2.0800 / trim.vm 0.9.3）：

| 对象 | 变更 | 结果 |
|---|---|---|
| 一次性空壳机 | `capacityInBytes` 1073741824 → 2147483648 | `code:0`，info 回写 2 GiB |
| `ubuntu-26.04` | 32212254720 → 161061273600 | 成功；guest 内 `vda` 150G，`growpart`+`resize2fs` 后 `/` 147G |

失败案例——**qcow2 v2 + 内部快照不能 resize**：

```json
{"ok": false, "error": {"msg": "internal error: Child process (/usr/bin/qemu-img resize /vol1/vm/pool/<uuid>-xxxx.qcow2 161061273600) unexpected exit status 1: qemu-img: Can't resize a v2 image which has snapshots\n"}}
```

同一请求里的 CPU/内存**不会**生效（整单失败，重新 `info` 仍是旧值），所以扩容失败不会留下半改状态。

约束：

- 仅关机态；只能扩不能缩。
- qcow2 有内部快照时先 `snapshot delete`，否则 `qemu-img resize` 直接失败。
- 只改虚拟磁盘容量，guest 内还要 `growpart` + `resize2fs`（ext4 支持在线扩容）。
- 建议扩容单独提交一次 `modify`，避免快照导致的失败连带丢弃 CPU/内存变更。

## 6. 未使用/不做的接口

- `/hostdev/sign`、`/hostdev/lsusb`、`/hostdev/lspci`：PCI/USB 直通，本 Skill 不写。
- `/ova/*`：OVA 导入导出，7 个接口 + 异步任务，不做。
- `/disk/convert-*`：格式转换，慢且占空间，不做。
- `/filemanager/dir/delete`：直接删磁盘目录，等价于 delete 的后果，不做独立命令。
- `/dashboard/summary`：SSE 监控，本版本不用。
