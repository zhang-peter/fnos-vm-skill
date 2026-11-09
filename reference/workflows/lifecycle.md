# 生命周期自测（M2）

目标：证明 create → start → wait → stop → delete 全链路可用，且只触碰临时 VM。

前置：已 `login`，`host` 显示 KVM=true、OVS 已初始化、`/vol1` 有空间。

```bash
fnos-vm --json host
fnos-vm --json list
```

## 1. 创建空壳

```bash
fnos-vm --json create \
  --name dsh-empty \
  --cpu 1 --mem 1G --disk 1G \
  --os-type linux --os-version other \
  --no-network \
  --desc 'fnos-vm lifecycle self-test'
```

预期：`code:0`，`data.after.state=SHUTOFF`，`data.after.disks[0].volumePath` 位于 `/vol1/vm/pool/`，本地 config 的 `vms` 新增该 internal name。

## 2. 开机并等待

```bash
fnos-vm --json start dsh-empty --wait 30
fnos-vm --json wait dsh-empty --state running --timeout 10
```

预期：`after.state=RUNNING`；空壳无引导盘，CPU 可能 100%，属正常。

## 3. 强制关机

```bash
fnos-vm --json stop dsh-empty --force --wait 30
```

预期：`after.state=SHUTOFF`。

## 4. 修改（关机态）

```bash
fnos-vm --json modify dsh-empty --cpu 2 --mem 2G --desc 'modified'
```

预期：`after.cpuCoreCnt=2`、`memCapacityInBytes=2147483648`、`desc=modified`。

## 5. 快照

```bash
fnos-vm --json snapshot create dsh-empty --desc 'before test'
fnos-vm --json snapshot list dsh-empty
SNAP=$(fnos-vm --json snapshot list dsh-empty | jq -r '.data[0].name')
fnos-vm --json snapshot revert dsh-empty "$SNAP" --yes
fnos-vm --json snapshot delete dsh-empty "$SNAP" --yes
```

预期：create 返回 `createdTime`/`vmState`；revert/delete 后 list 为空。

## 6. 删除

```bash
fnos-vm --json delete dsh-empty --yes
fnos-vm --json list
```

预期：VM 消失，磁盘文件由服务端删除；本地 `vms` 移除。

## 已验证

2026-11-09 在 trim.vm 0.9.3 / fnOS 1.2.0800 上执行过完整链路（临时 VM 名 `dsh-selftest-cli`、internal name `rjbnc8m0`），全部返回 `code:0`，结束后 `list` 只剩 `Tiny11`。所有权保护也用 `stop Tiny11` 验证：默认返回 `kind:"safety"`，退出码 1。
