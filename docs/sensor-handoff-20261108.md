# 传感器接入 · 交接文档（2026-11-08）

> 给新会话的起点。读完本文即可继续，不需要回溯之前的对话。
> 更完整的技术演进见 [sensor-registry-interface-20261107.md](sensor-registry-interface-20261107.md)。

## 一、目标与硬约束

- 目标：让 OnePlus 8T（KB2000 / kebab）Debian 13 上的**真实**加速度计 / 陀螺 / 光照数据可用。
- 硬约束：
  - **绝不用模拟或虚构数据冒充真实传感器**；
  - 原厂固件、persist、校准数据不得公开或提交；
  - 区分「已验证 / 推断 / 未完成」，不夸大进度。

## 二、仓库与私有资产

| 项 | 位置 |
| --- | --- |
| 仓库 | `F:\codexprojects\oneplus8t`，分支 `codex/sensors-20261107` |
| 技术演进 | `docs/sensor-registry-interface-20261107.md`（第一~十六节） |
| 源码树 | `local/sensors-20261107/hexagonrpc-kebab-v4/`（gitignored） |
| 版本 tar / 证据 | `local/sensors-20261107/*.tar`、`local/sensors-20261107/evidence/` |

设备侧部署目录：`/var/lib/kebab-ssc-listener/{bin,payload}`，systemd unit **`kebab-ssc-listener.service`**（enabled，开机自启）。

## 三、设备与访问

- OnePlus 8T，Debian 13，7.2 主线内核；五个 `kebab-*` 业务应保持 active、`failed=0`。
- SSH 只有 Windows OpenSSH 可用（WSL 直连不通）：
  - pwsh：`ssh -b 192.168.1.195 kebab`、`scp -o BindAddress=192.168.1.195 ...`
  - WSL 内：`/mnt/c/Windows/System32/OpenSSH/ssh.exe`
- **重启已获用户长期授权**，可自行执行 `sudo systemctl reboot --no-block`。

## 四、已走通的链路（有设备日志证据）

1. 开机自启 unit + 重启后，SLPI 上的传感器子系统会**主动来要配置**（之前 attach 而不重启时，它永远不来）。
2. DSP 通过 `apps_std` **成功写入** `temp.json`——`fwrite` 方法号 5 猜对，文件真实落在私有副本 `/var/lib/kebab-ssc-listener/payload/sensors/`。
3. `apps_std::stat` 已能服务：lenient 分支 + 强制 96 字节输出缓冲，def 为 `(0,1,0,1)`。
4. DSP 已进入**平台识别**阶段，会 `stat /sys/devices/soc0/{soc_id,revision,platform_version,platform_subtype_id}`。
5. **未通过**：注册表目录枚举，10 次 `Could not read from directory: Bad file descriptor`。
6. **未到达**：`sns_registry` 属性查询。它要问的属性名是 **`hw_id`**（不是早先猜的 `persist.vendor.*`）。

## 五、已证伪的假设（别再重试）

| 假设 | 结论 |
| --- | --- |
| `readdir` 句柄宽度 u64 → u32 | 无关，现象不变 |
| `hexagonfs_mapped` 的 `DIR*` 损坏 | 无关，底层 `readdir()` 正常 |
| `closedir` 时序 / "不真关"绕法 | 无关，已回滚 |
| 槽位撞车（`pin_dir_high` 把目录句柄搬到 128–255） | 隔离生效（opendir 返回 fd 255），但 10 次 EBADF 一次没少 |

## 六、当前唯一待解问题

那 10 次 `readdir` 用的是**句柄 3**，而我们从未把 3 作为目录句柄给出（3 是低位临时打开的槽号）。

探针已确认：`readdir virt_fd=3` 时 `slot=(nil)`，且发生在 `opendir` 之前。

两个候选：

1. `apps_std::stat` 成功返回时把槽号泄漏进了返回缓冲区；
2. `opendir`(26) / `readdir`(28) 的方法号与厂商 IDL 不符，DSP 实际在调别的方法。

**建议下一步**：给 `apps_std_stat` 成功路径加探针（打印输出缓冲前几个字），并给 `opendir` 打印入参路径。当前部署的 r25 已有 `stat ok: ...` 探针，但该 boot 打印 0 次——先确认 DSP 是否走到那一步。

## 七、构建与部署

设备上**没有 meson/ninja**，用 gcc 直编。

```bash
# 打包（在 WSL，项目根下）
cd /mnt/f/codexprojects/oneplus8t/local/sensors-20261107
tar -cf /tmp/k.tar --exclude=build --exclude=.git hexagonrpc-kebab-v4
cp /tmp/k.tar ./k-new.tar     # 供 Windows scp 使用

# 上传 + 构建（pwsh）
scp -o BindAddress=192.168.1.195 F:\codexprojects\oneplus8t\local\sensors-20261107\k-new.tar kebab:/tmp/k.tar
ssh -b 192.168.1.195 kebab "rm -rf /tmp/bx && mkdir -p /tmp/bx && tar -xf /tmp/k.tar -C /tmp/bx && cd /tmp/bx/hexagonrpc-kebab-v4 && nice -n 15 gcc -O2 -w -D_GNU_SOURCE -o /tmp/hx hexagonrpcd/aee_error.c hexagonrpcd/apps_std.c hexagonrpcd/interfaces.c hexagonrpcd/hexagonfs.c hexagonrpcd/hexagonfs_mapped.c hexagonrpcd/hexagonfs_plat_subtype_name.c hexagonrpcd/hexagonfs_virt_dir.c hexagonrpcd/iobuffer.c hexagonrpcd/listener.c hexagonrpcd/localctl.c hexagonrpcd/rpcd.c hexagonrpcd/rpcd_builder.c hexagonrpcd/sns_registry.c libhexagonrpc/*.c -Iinclude -Ihexagonrpcd -Ilibhexagonrpc 2>&1 | grep ' error'; ls -l /tmp/hx && sudo install -m 755 /tmp/hx /var/lib/kebab-ssc-listener/bin/hexagonrpcd && echo deployed && sudo systemctl reboot --no-block"

# 验证
ssh -b 192.168.1.195 kebab "sudo journalctl -u kebab-ssc-listener.service -b -o cat | grep -E 'sns-registry|stat ok|readdir virt_fd|Bad file'"
```

## 八、迭代纪律（都是吃过的亏）

- **一次只改一个变量**，改完必须重启验证——DSP 初始化每个 boot 只发生一次，重启 unit 不会重来。
- 加探针先确认 include：`hexagonfs_mapped.c` 缺 `<stdio.h>` 曾导致构建失败，而 `;` 串联让**没编出来的旧二进制被装上**。
- 构建结果要显式检查，别用 `grep -c` 当判据（无匹配时返回 1，会被误判为失败或串过）。
- 判断错了就回滚，不要留下未验证的 hack。

## 九、安全红线

- 不重启 remoteproc、不切内核、不刷分区、不改 persist / 校准数据；
- 写入只允许落在 `/var/lib/kebab-ssc-listener/payload` 私有副本内；
- 不伪造传感器数据；结论必须标注「已验证 / 推断 / 未完成」。

## 十、回退方式

```bash
ssh -b 192.168.1.195 kebab "sudo systemctl stop kebab-ssc-listener && sudo systemctl disable kebab-ssc-listener"
# 彻底清除：
ssh -b 192.168.1.195 kebab "sudo rm -f /etc/systemd/system/kebab-ssc-listener.service && sudo systemctl daemon-reload && sudo rm -rf /var/lib/kebab-ssc-listener"
```
