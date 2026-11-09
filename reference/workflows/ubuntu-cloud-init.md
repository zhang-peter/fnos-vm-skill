# Ubuntu 云镜像 + cloud-init（M3）

目标是无人值守：qcow2 云镜像 + NoCloud seed ISO → 导入建机 → 启动 → cloud-init 配置用户/SSH → 直接 SSH。

> 状态：工具链已实现；seed ISO 和 qcow2 import 已分别验证。**完整的 Ubuntu 下载/启动/SSH 未在本机执行**——用户明确要求不要下载 Ubuntu。下面命令可直接使用；第一次跑建议先在测试卷/测试 VM 上做。

## 1. 准备镜像

```bash
# 24.04 minimal 约 260 MB；server 约 620 MB
fnos-vm image download ubuntu-24.04-minimal --dest ./ubuntu.img
fnos-vm image resize ./ubuntu.img --size 20G
```

`image resize` 只改 qcow2 头的 virtual size；启动后 cloud-init 的 growpart 会把根分区扩到 20G。不要缩小，带 backing file 的镜像不支持。

## 2. 生成 seed ISO

有 SSH 公钥（推荐）：

```bash
fnos-vm image seed --output ./seed.iso \
  --user ubuntu \
  --ssh-key-file ~/.ssh/id_ed25519.pub \
  --hostname ubuntu
```

没有 SSH key 时可临时用密码（仅测试）：

```bash
fnos-vm image seed --output ./seed.iso \
  --user ubuntu --password 'ChangeMe-123' --hostname ubuntu
```

静态地址（推荐，避免找不到 DHCP 租约）：

```bash
fnos-vm image seed --output ./seed.iso \
  --user ubuntu --ssh-key-file id_ed25519.pub --hostname ubuntu \
  --static-ip 192.168.1.250 --prefix 24 --gateway 192.168.1.1 \
  --dns 192.168.1.1 --mac 4e:cd:ff:aa:bb:01
```

生成的是卷标 `CIDATA` 的 ISO9660+Joliet 镜像，含 `user-data`、`meta-data`，静态配置时还含 `network-config`。

## 3. 上传

需要 fnos-cli Skill 已安装并登录（profile 默认 `fnos`）：

```bash
fnos-vm image push ./ubuntu.img --remote-dir /vol1/1000/Temp
fnos-vm image push ./seed.iso --remote-dir /vol1/1000/Temp
```

没有 fnos-cli 时，用你熟悉的方式把两个文件放到 `/vol1/...`，只要路径是 NAS 绝对路径即可。

## 4. 建机

```bash
fnos-vm --json create \
  --name ubuntu \
  --image /vol1/1000/Temp/ubuntu.img \
  --disk-size 20G \
  --seed /vol1/1000/Temp/seed.iso \
  --cpu 2 --mem 4G \
  --os-type linux --os-version ubuntu \
  --firmware efi --chipset q35 \
  --network eno1-ovs:e1000@4e:cd:ff:aa:bb:01 \
  --dry-run
```

先看 `--dry-run` 的 payload 和 preflight；确认后去掉 `--dry-run` 执行。

要点：

- `--seed` 映射到 `cdroms.driverFilePath`（第二光驱槽）；`osFilePath` 留空。
- `--image` 映射为 `disks[0].actionType=import`、`sourcePath=/vol1/...`。
- `--disk-size` 必须 ≥ 镜像 virtual size；导入后 info 的 `capacityInBytes` 应等于它。
- 静态 IP 时，`--network` 的 MAC 必须与 seed 的 `network-config` 匹配。

## 5. 启动与验证

```bash
fnos-vm start ubuntu --wait 120
fnos-vm wait ubuntu --state running --timeout 30
```

等 30–90 秒让 cloud-init 完成，然后：

```bash
ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no ubuntu@192.168.1.250
cloud-init status --long
lsblk
```

预期：

- `cloud-init status` 为 `done`；
- 根分区已扩展（`lsblk` 看到 20G 左右）；
- 用户 `ubuntu` 可 sudo；
- SSH 公钥登录成功。

如果连不上：

1. `fnos-vm vnc ubuntu --check` 确认控制台可用；
2. `fnos-vm --json info ubuntu` 检查 `cdroms.driverFilePath` 是否指向 seed；
3. 检查 seed 是否卷标 `CIDATA`（`tar -tf seed.iso` 能看到 `user-data`/`meta-data`）；
4. 检查 `--network` MAC 与 seed 的 `network-config` 一致；
5. 尝试 DHCP 版 seed（去掉 `--static-ip/--mac`）。

## 6. 清理

```bash
fnos-vm stop ubuntu --wait 60
fnos-vm delete ubuntu --yes
# 视需要删除 /vol1/1000/Temp/ubuntu.img 和 seed.iso（本工具不删远端文件）
```

## 已知限制

- 本流程使用官方 cloud image，不是交互式 ISO 安装器。
- 云镜像默认 `ubuntu` 用户；换用户名时改 `--user`。
- `image resize` 只改 qcow2 virtual size，不保证分区自动扩展；cloud-init/growpart 负责。
- 未验证 26.04、非 qcow2 格式（vmdk/vhdx）和 OVA。
