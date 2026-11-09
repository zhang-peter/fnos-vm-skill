# 官方 Ubuntu ISO + autoinstall（VNC）

目标：用官方 live-server ISO 装一台可 SSH 的 Ubuntu，不需要交互点安装器。

> 实测：Ubuntu 26.04.1 LTS（Resolute Raccoon），fnOS 1.2.0800 / trim.vm 0.9.3，2026-11-09。

## 1. 准备

- ISO 已在 NAS，例如 `/vol1/1000/Temp/ubuntu-26.04.1-live-server-amd64.iso`
- 本机有 SSH key；没有就 `ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""`
- 规划静态地址（示例 `192.168.1.250/24`，网关 `192.168.1.1`）和 MAC（示例 `4e:cd:ff:aa:bb:01`）

## 2. 生成 autoinstall seed

```bash
fnos-vm image autoinstall \
  --output ./seed.iso \
  --user ubuntu \
  --password 'ChangeMe-2604' \
  --ssh-key-file ~/.ssh/id_ed25519.pub \
  --hostname ubuntu2604 \
  --static-ip 192.168.1.250 --prefix 24 --gateway 192.168.1.1 \
  --mac 4e:cd:ff:aa:bb:01 \
  --timezone Asia/Shanghai \
  --storage-layout direct \
  --shutdown poweroff
```

生成的是卷标 `CIDATA` 的 ISO9660+Joliet JSON，包含 `user-data`（`#cloud-config` + `autoinstall:`）和 `meta-data`。`shutdown: poweroff` 让安装完成后 VM 自动关机，便于切换启动顺序。

上传：

```bash
fnos-vm image push ./seed.iso --remote-dir /vol1/1000/Temp
```

## 3. 创建 VM

```bash
fnos-vm create --name ubuntu-26.04 --cpu 2 --mem 4G --disk 30G \
  --iso /vol1/1000/Temp/ubuntu-26.04.1-live-server-amd64.iso \
  --seed /vol1/1000/Temp/seed.iso \
  --network eno1-ovs:e1000@4e:cd:ff:aa:bb:01 \
  --os-type linux --os-version ubuntu \
  --firmware efi --chipset q35 --bus virtio
```

`--iso` 放第一光驱（`osFilePath`），`--seed` 放第二光驱（`driverFilePath`）。

## 4. 在 GRUB 注入 autoinstall

```bash
fnos-vm start ubuntu-26.04 --wait 30
fnos-vm vnc ubuntu-26.04 --screenshot grub.png --wait 10
```

看到 GRUB 菜单后：

1. `e` 进入编辑
2. 向下到 `linux /casper/vmlinuz ...` 行
3. `End`，再按 3 次 `Left`（移到 `---` 前）
4. 输入 `autoinstall ds=nocloud`（seed ISO 卷标是 `CIDATA`，cloud-init 会按标签找到它）
5. `Ctrl-X` 启动

一条命令：

```bash
fnos-vm vnc ubuntu-26.04 --keys \
  'key:e,key:down,key:down,key:down,key:end,key:left,key:left,key:left,text:autoinstall ds=nocloud,key:ctrl+x'
```

> 本次实际安装时，旧 seed 的 Joliet 卷标编码有误，`ds=nocloud` 没有找到 seed；后来修正为 UCS-2BE，并用 `blkid` 验证显示 `LABEL="CIDATA"`。如果按标签仍找不到，直接用下面的 HTTP seed fallback。

### 26.04 的 seedfrom 坑

Ubuntu 26.04 的 cloud-init `NoCloud` **不再接受 `s=/dev/sr0` 这类设备路径**，会报：

```
DataSourceNoCloudNet only uses seeds starting with ('http://','https://','ftp://','ftps://') - /dev/sr0 is not valid
```

两种可行方式：

1. **按卷标发现**：seed ISO 卷标必须是 `CIDATA`，kernel 只加 `autoinstall ds=nocloud`。本工具的 ISO 已修正 Joliet 卷标为 UCS-2BE，`blkid` 显示 `LABEL="CIDATA"`。
2. **HTTP seed**：在 VM 能访问的主机上把 `user-data`/`meta-data` 放到 HTTP 根，kernel 参数用：
   ```text
   autoinstall "ds=nocloud-net;s=http://<seed-host>:8000/"
   ```
   注意 GRUB 用引号包住含 `;` 的参数，否则 `;` 会被 GRUB 当作命令分隔符。

## 5. 处理确认提示

某些 26.04 组合会显示：

```
Confirmation is required to continue.
Add 'autoinstall' to your kernel command line to avoid this
Continue with autoinstall? (yes|no)
```

在 tty1 输入 `yes` 回车即可：

```bash
fnos-vm vnc ubuntu-26.04 --keys 'text:yes,key:enter'
```

> 如果 `autoinstall` 已在 cmdline 仍出现这个提示，按提示确认即可继续；安装仍按 autoinstall 配置执行。

## 6. 等待完成并切回硬盘

autoinstall 完成后 `shutdown: poweroff`，VM 变为 `SHUTOFF`。确认状态：

```bash
fnos-vm wait ubuntu-26.04 --state stopped --timeout 900
```

清掉光盘并改为默认硬盘启动。`bootType` 传 `"default"`/`"disk-0"` 可能被服务端拒绝（错误 12130），实测要传空串：

```json
{
  "bootType": "",
  "bootIndex": 0,
  "cdroms": { "osFilePath": "", "driverFilePath": "" }
}
```

```bash
fnos-vm modify ubuntu-26.04 --file bootfix.json
fnos-vm start ubuntu-26.04 --wait 60
```

## 7. 验证

```bash
ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no ubuntu@192.168.1.250
cat /etc/os-release
lsblk
df -h /
systemctl is-active ssh
sudo -n true || echo 'sudo needs password'
```

实测结果（2026-11-09）：

- `PRETTY_NAME="Ubuntu 26.04.1 LTS"`，kernel `7.0.0-30-generic`
- `vda` 30G，`vda1` 1G vfat `/boot/efi`，`vda2` 28.9G ext4 `/`
- `eth0` 静态 `192.168.1.250/24`
- SSH 公钥登录成功；`sudo` 密码可用；重启后 SSH 自动恢复
- 安装介质/seed/临时 HTTP 服务均已清理

## 8. 清理

```bash
# 删除安装用的 seed（可选；ISO 是用户文件，不要擅自删）
fnos-cli --profile fnos --allow-insecure-http file rm --yes /vol1/1000/Temp/seed.iso
# 删除临时 HTTP seed 服务（如果用了容器）
fnos-cli --profile fnos --allow-insecure-http docker container rm --yes <id>
```

VM 本身保留；删除 VM 用 `fnos-vm delete ubuntu-26.04 --yes`（会删磁盘）。
