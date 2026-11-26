# Changelog

## 未发布

修复 `vnc --keys` 的两处按键注入缺陷（2026-11-26）：

- `RfbClient.type_text` 原先对字母一律 `tap(char.lower())`，**大写字母被静默降级成小写**；`text:Ubuntu2604` 实际键入 `ubuntu2604`。重置密码时这会写入错误的大小写，且用同样的注入方式"验证"会得到假阳性（这次密码排障就踩了）。现在大写字母会按住 Shift 输入；非 ASCII 字母直接抛 `RfbError`，不再落到 `unknown key name`。
- `_parse_key_sequence` 对 `text:`/`type:` 的载荷做了 `strip()`，**行尾空格被吞掉**：`text:echo ,key:ls` 实际输入 `echo`（没有空格），拼出来就是 `echols`。现在载荷逐字保留，只对前缀做判断。
- 新增 `tests/test_vnc.py`（12 例）：键盘事件的 keysym 序列（小写/大写/混合大小写往返/Shift 符号/数字空格/非 ASCII/不支持字符），以及按键序列解析（首尾空格、大小写、混合序列、空 token）。测试总数 50 → 62。
- **已实测确认的边界**：fnOS 的 VNC WebSocket 网桥会丢弃修饰键。`text:echo AbC` 在 guest 里得到 `abc`；换成大写 keysym（`key:A`）同样只出 `a`；`key:capslock` 包住也没用。因此 `--keys` 无法输入大写字母，密码类输入必须走 SSH（`chpasswd`）。客户端仍按 RFB 约定发送 Shift（换到标准 VNC 服务端即可生效），并新增 `capslock` 键名。见 `reference/troubleshooting.md`。
文档补充（2026-11-11，真机验证 `ubuntu-26.04`）：

- 记录 `/domain/modify` 的磁盘扩容用法：`--file` 传 `disks[]`（整体替换）并改 `capacityInBytes`，服务端实际执行 `qemu-img resize`，为真扩容而非只改元数据。见 `reference/api.md` §5.1、`reference/commands.md` 的 `modify`。
- 记录坑：**qcow2 v2 带内部快照时无法 resize**（`qemu-img: Can't resize a v2 image which has snapshots`），需先 `snapshot delete`；失败为整单原子回滚，CPU/内存不会半改。
- 记录扩容后 guest 内仍需 `growpart` + `resize2fs`，并建议单独提交扩容、加 swap 兜底。

## 0.1.0 — 2026-11-09

首个可用的 `fnos-vm` Skill：

- `ost` Web 会话鉴权，支持 `login --token` / `login --from-cdp` / `logout`。
- M1：`host`、`list`、`info`、`snapshot list`、`vnc`。
- M2：`create`、`start`、`stop`（含 `--force`）、`reboot`、`pause`、`resume`、`modify`、`delete`、`wait`。
- 快照：`create` / `list` / `revert` / `delete`。
- 安全：所有权登记与 `--force-other`、`--yes` 确认、路径校验、创建预检。
- 镜像：qcow2 下载/扩容、cloud-init NoCloud seed（纯 Python ISO9660+Joliet，卷标 UCS-2BE `CIDATA`）、`image autoinstall`、fnos-cli 上传、`image prepare`。
- VNC：RFB over WebSocket，`--screenshot` 截图、`--keys` 键盘序列（GRUB/安装器自动化）。
- 测试：50 个测试（payload/ISO/client/CLI/images/autoinstall/VNC），3 个真机测试可选。
- 项目更名：项目目录 / 仓库改为 `fnos-vm-skill`；Skill 标识 `fnos-vm`、CLI 命令、Python 包 `fnos_vm`、DSH 安装目录 `~/.dsh/skills/fnos-vm` 保持不变。
- 修复：Windows `fnos-vm.cmd` 在 `if (...)` 块里展开 `%ERRORLEVEL%`，会在 python 运行前就定值，导致退出码恒为旧值（正常失败也返回 0）。改为 goto 分支后退出码与 CLI 契约一致，并加了回归测试。



