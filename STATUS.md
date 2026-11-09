# STATUS

## 已完成

- M1 只读：`host` / `list` / `info` / `snapshot list` / `vnc`；真机验证。
- M2 生命周期：`create` / `start` / `wait` / `stop`（含 `--force`）/ `pause` / `resume` / `reboot` / `modify` / `snapshot create|revert|delete` / `delete`；真机验证。
- M3 官方 ISO 安装：Ubuntu 26.04.1 LTS live-server ISO + autoinstall，真机安装完成，SSH/静态 IP/sudo/重启验证。
- M4 VNC：RFB over WebSocket，Raw framebuffer 截图（纯 Python PNG）和键盘注入（`--keys`）；GRUB 编辑与安装器确认已实际使用。
- 鉴权：`login --token` 已验证；`login --from-cdp` 用 headless Edge + 本地 mock API 端到端验证（设置 `ost` cookie → CLI 读取 → 校验 API → 保存配置）。
- 安全：所有权保护、`--yes`、路径校验、创建预检；已用 `stop Tiny11` 验证拒绝逻辑。
- 镜像：qcow2 下载/扩容、纯 Python ISO9660+Joliet seed（卷标已修正为 UCS-2BE `CIDATA`）、`image autoinstall`、`image push` 调 fnos-cli。
- 测试：50 个测试运行：47 个通过，3 个真机测试默认跳过（真机只读测试已单独跑通）。
- 项目更名（2026-11-09）：项目目录 `fnos-vm` → `fnos-vm-skill`；Skill 标识 / CLI / Python 包 / 安装目录仍为 `fnos-vm`。更名后在新路径复跑单元测试通过。
- 启动器修复（2026-11-09）：`scripts/fnos-vm.cmd` 的退出码原先被 cmd 的 `%ERRORLEVEL%` 提前展开吞掉；已改为 goto 分支并加回归测试（`tests/test_cli.py::test_windows_launcher_exit_codes`）。
- 资源调整（2026-11-11）：`ubuntu-26.04` 由 2 vCPU / 4 GiB / 30 GiB 调为 8 vCPU / 16 GiB / 150 GiB。磁盘扩容通过 `modify --file` 改 `disks[].capacityInBytes`，服务端真实执行 `qemu-img resize`；qcow2 v2 带内部快照时会失败，已记录到 [API 契约](reference/api.md) 与 [命令说明](reference/commands.md)。

## 验证记录（2026-11-08/09，trim.vm 0.9.3，fnOS 1.2.0800）

| 项 | 命令/方法 | 结果 |
|---|---|---|
| 鉴权 | `Authorization: trim <ost>` 直连 | 200，`code:0` |
| 宿主机 | `fnos-vm --json host` | KVM/IOMMU true，OVS `eno1-ovs`，32 GiB，`/vol1` 886.8 GiB |
| 列表 | `fnos-vm --json list` | `Tiny11` / `<internal-name>` / SHUTOFF |
| 详情 | `fnos-vm --json info Tiny11` | 与 Web 界面一致 |
| VNC | `fnos-vm vnc Tiny11 --check` | `101 Switching Protocols` |
| 创建 | `create --name dsh-selftest-cli --disk 1G --cpu 1 --mem 1G --no-network` | 成功，`volumePath=/vol1/vm/pool/...qcow2` |
| 开机/等待 | `start --wait 30` / `wait --state running` | RUNNING |
| 暂停/恢复 | `pause` / `resume` | PAUSED → RUNNING |
| 重启 | `reboot --wait 60` | RUNNING |
| 强制关机 | `stop --force --wait 30` | SHUTOFF |
| 修改 | `modify --cpu 2 --mem 2G --desc ...` | 2 vCPU / 2 GiB / desc 更新 |
| 快照 | `snapshot create/list/revert/delete` | 全部 `code:0`，最终清空 |
| 删除 | `delete --yes` | 成功，列表恢复只剩 Tiny11 |
| 所有权 | `stop Tiny11`（未登记） | `kind:"safety"`，退出码 1 |
| qcow2 import | 合成 64 MiB qcow2 → `/domain/create` | 成功，服务端复制到 `/vol1/vm/pool/` |
| seed ISO | `image seed` + `tar -tf` | `user-data`/`meta-data` 可见；静态配置含 `network-config` |
| image prepare | 本地 HTTP qcow2 → download + resize + seed | 64 MiB → 20 GiB virtual，seed 生成成功 |
| fnos-cli 上传 | `image push seed.iso` | 上传成功 |
| ISO 安装 | Ubuntu 26.04.1 live-server + autoinstall | 成功装成 `ubuntu-26.04`（internal `<internal-name>`） |
| 安装后验证 | `ssh ubuntu@192.168.1.250` | Ubuntu 26.04.1 LTS，kernel 7.0.0-30-generic，30G 磁盘，SSH active |
| 重启验证 | `fnos-vm reboot` + SSH | 重启后公钥登录恢复 |
| sudo | `sudo -S id -u` | 0 |
| 清理 | seed ISO / HTTP 容器 / 临时文件 | 已清理；ISO 保留 |
| 规格调整 | `modify ubuntu-26.04 --cpu 8 --mem 16G` | 8 vCPU / 16 GiB；guest `nproc`=8、`free -h` 15Gi |
| 磁盘扩容 | `modify --file` 将 `capacityInBytes` 30 GiB → 150 GiB | 服务端 `qemu-img resize` 成功；`growpart`+`resize2fs` 后 `/` 147G，可用 125G |
| 扩容前置条件 | 带内部快照时执行同一 `modify` | `ok:false`，`qemu-img: Can't resize a v2 image which has snapshots`；整单未生效，info 仍为旧值 |
| swap | `/swapfile` 8 GiB + 写入 `/etc/fstab` | 与原有 `/swap.img` 合计 11 GiB |

## 安装出的 VM

| 项 | 值 |
|---|---|
| title / internal name | `ubuntu-26.04` / `<internal-name>` |
| 规格 | 8 vCPU / 16 GiB / 150 GiB virtio（2026-11-11 扩容；原 2 vCPU / 4 GiB / 30 GiB） |
| 地址 | `192.168.1.250/24`，网关 `192.168.1.1`，MAC `4e:cd:ff:aa:bb:01` |
| 登录 | `ubuntu`，SSH key `~/.fnos-vm/<vm>_ed25519`；密码为安装时设置，未写入仓库 |
| 启动 | UEFI/q35，安装后已清空两个光驱，默认硬盘启动 |
| 安装方式 | `image autoinstall` seed + GRUB `autoinstall ds=nocloud`；实际因 26.04 seedfrom 限制，第一次用 `ds=nocloud` 失败后改用 `ds=nocloud-net` HTTP seed 完成 |

## 未完成/未验证

- **Ubuntu 云镜像的完整启动/cloud-init/SSH**：用户明确要求不要下载 Ubuntu 云镜像，因此未执行。工具链（download/resize/seed/push/import）已分别验证，命令见 [Ubuntu 云镜像工作流](reference/workflows/ubuntu-cloud-init.md)。
- OVA、PCI/USB 直通、磁盘格式转换：按计划刻意不做。
- 26.04 镜像的下载 URL 未验证；本次使用 NAS 上已有的 26.04.1 live-server ISO。
- RFB 客户端只支持 None 认证与 Raw 编码；不支持 VNC 密码认证、Tight/ZRLE 等编码。

## 测试命令

```powershell
python -m unittest discover -s tests -v
$env:FNOS_VM_LIVE="1"
python -m unittest tests.test_live -v
```
