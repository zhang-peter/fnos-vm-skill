# fnos-vm-skill

项目 / 仓库名是 `fnos-vm-skill`；但发布出去的 Skill 标识、CLI 命令、Python 包和 DSH 安装目录仍然是 `fnos-vm`。这跟官方的「仓库 `fnos-cli-skill` + Skill `fnos-cli`」命名方式一致：`-skill` 后缀属于仓库，不属于调用入口。

| 对象 | 名称 |
|---|---|
| 项目目录 / 仓库 | `fnos-vm-skill` |
| Skill 标识（frontmatter `name`） | `fnos-vm` |
| CLI 命令 | `fnos-vm` |
| Python 包 | `fnos_vm` |
| DSH 安装目录 | `~/.dsh/skills/fnos-vm` |

把飞牛 fnOS 的「虚拟机」应用包装成 DSH Skill：用 `/vm/api/v1` HTTP API 管理 VM，不需要 NAS root，也不需要 SSH 到宿主机。

## 状态

- M1 只读能力：**已用真机验证**（`host` / `list` / `info` / `snapshot list` / `vnc --check`）。
- M2 生命周期：**已用真机验证**（创建空壳机、开机、等待、强制关机、修改、快照建/回滚/删、删除，全链路清理）。
- M3 镜像准备：**工具已实现并通过离线/导入验证**；另外用 NAS 上已有的 Ubuntu 26.04.1 live-server ISO + autoinstall 完成了真机安装（SSH/静态 IP/sudo/重启验证）。Ubuntu 云镜像的实际下载未执行——用户明确要求不要下载 Ubuntu。
- M4 VNC：地址、WebSocket 101 握手、RFB Raw 截图和键盘注入已验证（GRUB 编辑/安装器确认实际使用）。

详见 [STATUS.md](STATUS.md)。

## 安装

### 作为 DSH Skill

把本目录放到 DSH 的用户级 skills 根：

```powershell
$dst = "$env:USERPROFILE\.dsh\skills\fnos-vm"
if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
Copy-Item -Recurse -Force . $dst
```

或建立 junction 方便开发：

```powershell
$dst = "$env:USERPROFILE\.dsh\skills\fnos-vm"
if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
New-Item -ItemType Junction -Path $dst -Target (Get-Location)
```

DSH 在会话启动时扫描 skills；新装/改动 SKILL.md 后新开会话生效。

### 直接运行

```powershell
scripts\fnos-vm.cmd --help
# 或
python scripts\fnos-vm.py --help
```

要求 Python 3.9+，无第三方依赖。没有 Python 时设置 `FNOS_VM_PYTHON` 指向解释器。

## 首次连接

```powershell
scripts\fnos-vm.cmd login --host 192.168.1.10 --from-cdp
# 或
scripts\fnos-vm.cmd login --host 192.168.1.10 --token '<ost>'
scripts\fnos-vm.cmd host
```

`ost` 是浏览器 HttpOnly cookie；`fnos-cli` 的 OAuth token、`osrt` cookie、`Bearer` 都不行。配置写入 `~/.fnos-vm/config.json` 并收紧 ACL。

> 本文档与 `reference/` 中的 IP、MAC、主机名、VM 内部名、NAS 路径均为**示例**，请替换成你自己的环境。

## 测试

```powershell
python -m unittest discover -s tests -v
```

可选的真机只读测试：

```powershell
$env:FNOS_VM_LIVE="1"
python -m unittest tests.test_live -v
```

写测试（会创建/删除临时 VM）需要显式开启：

```powershell
$env:FNOS_VM_LIVE="1"
$env:FNOS_VM_LIVE_WRITE="1"
python -m unittest tests.test_live -v
```

## 目录

```
SKILL.md                     Skill 入口与 agent 指令
manifest.json                Skill 元数据
scripts/fnos-vm.py           入口
scripts/fnos_vm/             实现（client/config/payload/ISO/CDP/CLI）
reference/                   API、安全、工作流、排错
tests/                       单元 + mock API + 可选真机测试
STATUS.md                    实现状态与验证证据
```

## 设计要点

- **鉴权**：`Authorization: trim <ost>`，token 只存本地配置，不打印、不写日志。
- **输出**：agent 捕获 stdout 时默认 JSON；人打开终端时默认表格；两者可用 `--json`/`--table` 覆盖。
- **写操作**：返回操作前后快照；删除/回滚/删快照必须 `--yes`。
- **所有权**：本地登记本工具创建的 VM；未登记 VM 默认拒绝写操作，`adopt` 或 `--force-other` 才放行。
- **预检**：标题查重、CPU/内存/磁盘容量、KVM/OVS、路径和 bootType 全部在写之前检查。
- **镜像**：qcow2 下载/扩容、cloud-init NoCloud seed ISO（纯 Python ISO9660+Joliet，无 genisoimage 依赖）、可选 fnos-cli 上传。

## 许可

内部工具；按需修改。

