---
name: fnos-vm
description: "使用 fnos-vm 管理飞牛 fnOS 的虚拟机：列表、详情、新建、开关机、重启、暂停/恢复、删除、快照、VNC 地址、宿主机能力探测，以及云镜像/cloud-init seed 准备。用户提到飞牛虚拟机、fnOS VM、QEMU/KVM 虚拟机，或需要在 NAS 上安装 Ubuntu 时使用。"
---

# fnOS 虚拟机

`fnos-vm` 通过飞牛「虚拟机」应用自己的 HTTP API（`/vm/api/v1`）管理 VM。它**不需要 NAS root、不需要 SSH 到宿主机**，只使用 Web 会话的 `ost` 令牌。API 契约来自 trim.vm 0.9.3 的实测，详见 [API 记录](reference/api.md)。

## 启动器

本 Skill 目录下的 `scripts/fnos-vm.py` 是主程序，Python 3.9+，只用标准库。先用绝对路径定位入口：

- Windows：`<SKILL_DIR>\scripts\fnos-vm.cmd`
- macOS/Linux：`<SKILL_DIR>/scripts/fnos-vm`

下文用 `fnos-vm` 代指该入口。首次运行先执行 `fnos-vm --version` 和 `fnos-vm --help`。如果入口提示找不到 Python，设置 `FNOS_VM_PYTHON` 为 Python 解释器路径，或直接运行 `python scripts/fnos-vm.py`。

## 连接与授权

VM 服务只认 `Authorization: trim <ost>`；`ost` 是浏览器 HttpOnly cookie，不是 fnos-cli 的 OAuth token。两条获取方式：

1. **CDP 自动读取（推荐）**
   - 完全退出 Edge（包括后台/托盘），然后启动：
     ```powershell
     & "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222
     ```
   - 在这个 Edge 里登录 `http://<host>:5666/`，保持页面打开。
   - 执行：
     ```bash
     fnos-vm login --host <host> --from-cdp
     ```
   - 如果 CDP 不在默认端口，用 `--cdp http://127.0.0.1:<port>`。
   - 没有现成 CDP 时，`--launch` 会打开一个受控配置的 Edge，等待你在其中登录。

2. **手动粘贴**
   - 浏览器开发者工具 → Application/Storage → Cookies → 找到 `ost`，复制值。
   - ```bash
     fnos-vm login --host <host> --token '<ost>'
     ```
   - 不要把 token 写进聊天、脚本或仓库；它会进入命令历史/进程参数，无法承诺零留存。

保存位置：`~/.fnos-vm/config.json`（Windows `C:\Users\<user>\.fnos-vm\config.json`），Windows 用 `icacls`、POSIX 用 `0600` 收紧到当前用户。`logout` 删除配置。任何接口返回 `code:401/10001` 时退出码为 `3`，必须重新 `login`，不要静默重试。

## 输出约定

- 非 TTY（agent 捕获输出）默认 `--json`；人打开终端时默认表格。显式用 `--json` / `--table` 覆盖。
- `--json` 时 stdout 只有一个 JSON 文档，日志走 stderr。
- 读命令：`{"ok":true,"data":...}`。
- 写命令：`{"ok":true,"data":{"action":...,"before":...,"after":...}}`，便于核对操作前后状态。
- 错误：`{"ok":false,"error":{"kind":"auth|business|argument|safety|network|preflight","code":...,"msg":"..."}}`。
- 退出码：`0` 成功，`1` 业务/安全/网络失败，`2` 参数错误，`3` 鉴权失效。

## 命令

```bash
fnos-vm host                         # KVM/IOMMU/OVS/卷/内存 + 已分配
fnos-vm list [--json]                # 名称/标题/系统/状态/CPU/内存/磁盘
fnos-vm info <name|title>            # 完整设备模型 + VNC 地址
fnos-vm vnc <name|title> [--check]   # ws(s)://<host>/vm/api/v1/vnc/<vncName>
fnos-vm vnc <name> --screenshot console.png
fnos-vm vnc <name> --keys 'key:e,key:down,key:end,text:autoinstall ds=nocloud,key:ctrl+x'
fnos-vm wait <name> --state running --timeout 120
```

```bash
fnos-vm create --file vm.json
fnos-vm create --name ubuntu --cpu 2 --mem 4G --disk 20G --iso /vol1/1000/iso/ubuntu.iso
fnos-vm create --name ubuntu --image /vol1/1000/Temp/ubuntu.img --disk-size 20G \
  --seed /vol1/1000/Temp/seed.iso --cpu 2 --mem 4G
fnos-vm create ... --dry-run        # 只打印 payload 和预检结果，不写
```

```bash
fnos-vm start <name> [--wait 30]
fnos-vm stop <name> [--force] [--wait 30]
fnos-vm reboot <name> [--wait 60]
fnos-vm pause <name>
fnos-vm resume <name>
fnos-vm modify <name> --cpu 4 --mem 8G --desc '...'
fnos-vm modify <name> --file disk.json     # disks 全量替换；改 capacityInBytes 会真扩容（qemu-img resize）
fnos-vm delete <name> --yes
```

```bash
fnos-vm snapshot list <name>
fnos-vm snapshot create <name> --desc 'before upgrade'
fnos-vm snapshot revert <name> <snap> --yes
fnos-vm snapshot delete <name> <snap> --yes
```

```bash
fnos-vm image list
fnos-vm image download ubuntu-24.04-minimal --dest ./ubuntu.img
fnos-vm image resize ./ubuntu.img --size 20G
fnos-vm image seed --output ./seed.iso --user ubuntu --password '...' \
  --ssh-key-file ~/.ssh/id_ed25519.pub --hostname ubuntu
fnos-vm image seed --output ./seed.iso --user ubuntu --ssh-key-file id.pub \
  --static-ip 192.168.1.250 --gateway 192.168.1.1 --mac 4e:cd:ff:aa:bb:01
fnos-vm image autoinstall --output ./seed.iso --user ubuntu --password '...' \
  --ssh-key-file ~/.ssh/id_ed25519.pub --hostname ubuntu \
  --static-ip 192.168.1.250 --gateway 192.168.1.1 --mac 4e:cd:ff:aa:bb:01
fnos-vm image prepare ubuntu-24.04-minimal --dest ./ubuntu.img --disk-size 20G \
  --ssh-key-file id.pub --hostname ubuntu --remote-dir /vol1/1000/Temp
fnos-vm image push ./ubuntu.img --remote-dir /vol1/1000/Temp
```

`image download` 的别名可用 `fnos-vm image list` 查看；也可直接传 https URL。

## 安全规则

1. `delete`、`snapshot revert`、`snapshot delete`、`adopt` 必须带 `--yes`；执行前会打印/返回将被销毁的对象。
2. 只允许写本工具创建的 VM。现有 VM（例如 `Tiny11`）默认受保护；需要管理时用 `fnos-vm adopt <name> --yes` 登记，或对单次操作加 `--force-other`。
3. 所有 ISO/镜像/seed 路径必须是 `/vol{n}/...` 绝对路径；拒绝相对路径、`..`、反斜杠和空值。
4. 建机前预检：标题查重、CPU ≤ 宿主机线程、内存 ≤ 总量 − 已分配、磁盘 ≤ 卷剩余、KVM/OVS 可用、bootType 与 ISO 一致。预检失败退出码 `1`。
5. 不做 PCI/USB 直通、OVA 导入导出、磁盘格式转换、`filemanager/dir/delete`。
6. 删除会同时删磁盘；运行中的 VM 先 `stop`。没有确认前不要执行 `delete`。
7. `image download` 可能下载数百 MB；只在用户明确同意下载该镜像时执行，不要为了“验证”自动下载。

## 典型工作流

### 只读巡检

```bash
fnos-vm --json host
fnos-vm --json list
fnos-vm --json info Tiny11
fnos-vm vnc Tiny11 --check
```

### 建一台空壳机并清理

```bash
fnos-vm create --name dsh-empty --cpu 1 --mem 1G --disk 1G --no-network
fnos-vm start dsh-empty --wait 30
fnos-vm wait dsh-empty --state running --timeout 30
fnos-vm stop dsh-empty --force --wait 30
fnos-vm delete dsh-empty --yes
```

### 云镜像 + cloud-init（无人值守）

1. 下载并扩容 qcow2：
   ```bash
   fnos-vm image download ubuntu-24.04-minimal --dest ./ubuntu.img
   fnos-vm image resize ./ubuntu.img --size 20G
   ```
2. 生成 seed ISO（cloud-init NoCloud，卷标 `CIDATA`）：
   ```bash
   fnos-vm image seed --output ./seed.iso --user ubuntu \
     --ssh-key-file ~/.ssh/id_ed25519.pub --hostname ubuntu
   ```
3. 上传到 NAS（需要 `fnos-cli` Skill 已安装并登录）：
   ```bash
   fnos-vm image push ./ubuntu.img --remote-dir /vol1/1000/Temp
   fnos-vm image push ./seed.iso --remote-dir /vol1/1000/Temp
   ```
4. 建机并启动：
   ```bash
   fnos-vm create --name ubuntu --image /vol1/1000/Temp/ubuntu.img \
     --disk-size 20G --seed /vol1/1000/Temp/seed.iso \
     --cpu 2 --mem 4G --os-type linux --os-version ubuntu
   fnos-vm start ubuntu --wait 120
   ```
5. 从 DHCP/静态地址 SSH；详细排错见 [Ubuntu 工作流](reference/workflows/ubuntu-cloud-init.md)。

### 官方 ISO 无人值守安装（VNC + autoinstall）

Ubuntu 26.04 live-server ISO 可用 autoinstall seed + VNC 驱动 GRUB。完整步骤见 [ISO autoinstall 工作流](reference/workflows/iso-autoinstall.md)，核心命令：

```bash
# 1. 生成 autoinstall seed（卷标 CIDATA）
fnos-vm image autoinstall --output seed.iso --user ubuntu --password '...' \
  --ssh-key-file id.pub --hostname ubuntu2604 \
  --static-ip 192.168.1.250 --gateway 192.168.1.1 --mac 4e:cd:ff:aa:bb:01
fnos-vm image push seed.iso --remote-dir /vol1/1000/Temp

# 2. 建机（ISO 第一光驱，seed 第二光驱）
fnos-vm create --name ubuntu-26.04 --cpu 2 --mem 4G --disk 30G \
  --iso /vol1/1000/Temp/ubuntu-26.04.1-live-server-amd64.iso \
  --seed /vol1/1000/Temp/seed.iso \
  --network eno1-ovs:e1000@4e:cd:ff:aa:bb:01 \
  --os-type linux --os-version ubuntu

# 3. 启动后在 GRUB 按 e，光标移到 linux 行，追加 autoinstall 后 Ctrl-X
fnos-vm start ubuntu-26.04 --wait 30
fnos-vm vnc ubuntu-26.04 --screenshot grub.png
fnos-vm vnc ubuntu-26.04 --keys \
  'key:e,key:down,key:down,key:down,key:end,key:left,key:left,key:left,text:autoinstall ds=nocloud,key:ctrl+x'

# 4. 若 installer 提示 Continue with autoinstall? (yes|no)
fnos-vm vnc ubuntu-26.04 --keys 'text:yes,key:enter'

# 5. 安装完成会自动 poweroff；清掉光盘并从硬盘启动
fnos-vm modify ubuntu-26.04 --file bootfix.json   # {"bootType":"","cdroms":{"osFilePath":"","driverFilePath":""}}
fnos-vm start ubuntu-26.04 --wait 60
ssh -i id_ed25519 ubuntu@192.168.1.250
```

`vnc --keys` 支持逗号分隔的 `key:<name>`、`combo:ctrl-x`、`text:<literal>`、`sleep:<seconds>`；`--screenshot` 保存当前画面。GRUB 编辑器中 `key:left` 三次是为了把 `autoinstall` 插到 `---` 之前。

### 快照

```bash
fnos-vm snapshot create Tiny11 --desc 'before update' --force-other
fnos-vm snapshot list Tiny11
fnos-vm snapshot revert Tiny11 snap-20261109-001408 --yes --force-other
fnos-vm snapshot delete Tiny11 snap-20261109-001408 --yes --force-other
```

## 参考

- [API 契约与实测载荷](reference/api.md)
- [安全模型](reference/safety.md)
- [命令与字段说明](reference/commands.md)
- [Ubuntu 云镜像工作流](reference/workflows/ubuntu-cloud-init.md)
- [官方 ISO autoinstall 工作流](reference/workflows/iso-autoinstall.md)
- [生命周期自测](reference/workflows/lifecycle.md)
- [排错](reference/troubleshooting.md)
