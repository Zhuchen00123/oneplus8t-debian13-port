# Debian 13 持久部署 r1

2026-10-03：用户明确持续授权“以后的dtbo和持久系统都不用单独授权，可以直接执行”。后续既定 DTBO 测试、`dtbo_a` / `boot_a` / `super` 部署和对应原厂恢复不再重复申请。此授权不扩展为切槽、重新锁定、清空 userdata、改分区布局或写 recovery / vbmeta / NV。

## 主机产物

- 内核提交 `65dad81b2b498007e4972c45f94644f2f5d430f9`，版本 `7.2.0-kebab-debian13-r1+`，578 个匹配模块。社区来源保持锁定，局部补丁和前像 hash 进入 `sources.lock.json`。
- Synaptics TCM IRQ 原先只计 marker/code 两字节，漏计两字节长度。修正接收、复制及解析长度，拒绝短报文、越界和不完整对象，诊断限频；保留原有触摸字段格式。实际驱动函数在 GCC ASan/UBSan 下通过报文回归测试，同套测试检出旧代码错误。这不等于触摸已实机通过。
- DTB SHA-256 与此前 RAM 成功组一致：`acf27ff2119adf524c5e171f413a2f6cca2b1b253f371380fd134a1ad49695ef`。
- RAM boot：`artifacts/boot-r1-20261003/rescue/boot-rescue.img`，18,751,488 字节，SHA-256 `7910744e3a73d5988e2258bd2377243fc13e0b2c222632f07cc8aedc1351e2be`。
- 持久组：`artifacts/server-r1-20261003/ready/`。4 GiB ext4；根 UUID `bc52fbbe-777b-47c8-84fc-ba56db7a8813`，boot/fstab 一致。AOSP 解包、ext4 只读 fsck 和 sparse 往返 hash 已由打包程序核验。
- 保留 59 个本机 OEM 文件及无线/GPU 固件。USB 固化实测可用的 RNDIS EF/04/01 和网络先于 ACM 的顺序；networkd 在 `192.168.8.1/24` 为主机提供 `.2`，不提供默认网关或 DNS。USB DHCP 仍待实机验证。
- 实际 ARM64 sshd 的正确公钥成功、错误公钥拒绝；禁用密码与 root SSH，用户 `debian` 可用 sudo。没有把 SSH 私钥、主机密钥或 Wi-Fi 密码装入镜像。

旧 `server-port-20261003/ready` 组保留作历史输入，不能混用其 boot、模块或 USB 脚本。

## 部署与失败恢复

部署前再次核验 KB2000、A 槽、解锁状态、分区大小、成组产物 hash 和恢复输入。手机日志及序列号仅保存在 `local/`。本机 `super` 实测为 `/dev/block/sda15`，7,516,192,768 字节；刷入 flat ext4 会替换 Android 动态分区，另一 boot 槽不能保护原 Android。

只在本机 Bootloader Fastboot 模式写 `super` 的 Android sparse rootfs、社区空 `dtbo_a`、对应 `boot_a`，不切槽。每步写前记日志，任何失败立即停止后续启动。

如果 Linux 未正常启动，拔下 USB 后使用此前已验证的按键流程回 Fastboot，再插回。确认同一设备和 A 槽，恢复 `super-stock.sparse.img` → `boot_a.img` → `dtbo_a.img`，均来自 `artifacts/stock-recovery-20261003/recovery-manifest.json` 校验组，全部成功后才能重启 Android。不改 recovery / vbmeta 或重新锁定。

原厂 whole-super sparse 已离线往返到原始 7 GiB 镜像并 hash 相同；完整 super 恢复尚未实机执行。26 个原厂分区备份不含 GPT、完整启动链或用户数据，运行中备份也不保证跨分区原子一致。同盘快照不等于外部介质备份。

## 验证状态

当前 r1 已完成持久手机部署，手机留在 Linux；不是一次临时 fastboot boot。实际通过 USB DHCP/SSH、真实终端、根文件系统读写及正常重启。Wi-Fi 按用户“先验证 USB SSH，Wi-Fi 稍后配置”延期。

封存校验已通过：全部持久组文件 SHA-256、boot/fstab UUID、USB/fsck/固件内容；rootfs 578、server initramfs 200、RAM initramfs 8 模块实际字节均与新内核一致，实际持久 initramfs USB 脚本与修正源码一致。最初非 root 的 cpio 解包因设备节点权限失败，保留原目录并在新的 WSL root 审计目录完成；未忽略此失败或沿用不完整结果。

新 RAM/服务器成组快照：`backups/persistent-r1-20261003-220740-832/`，54 文件复制后逐份 hash 一致；完整内核源码快照 `backups/kernel-20261003-215257-924571348/` 已校验。刷写前 Fastboot 实读 product=kona、slot=a、unlocked=yes、is-userspace=no，super=7 GiB、boot_a=96 MiB、dtbo_a=24 MiB。原厂 boot_a / dtbo_a / whole-super sparse 输入重新核验通过。

## 实机结果与操作入口

本次只在 Bootloader Fastboot 向 `super`、`dtbo_a`、`boot_a` 写入，各步 Sending/Writing 均返回 OKAY。super 以 256 MiB 上限拆成五段；未切槽或写其他分区。持久 boot 45,617,152 字节，SHA-256 `f899299d2b9663e78f0e0553e26d99d95170a4b3ed2f557778abd8d9f78450d5`；DTBO 284 字节，SHA-256 `08f1551f1dd69a5799d3440c0dff7c183cbadad0764dd0ef7a21a1757661077f`。在已启动 Linux 中先核验 GPT 标签/分区大小，再回读这两个候选的确切字节范围，hash 与输入一致；这是候选前缀校验，不宣称分区剩余字节被清零。根文件系统已产生正常运行写入，不宣称整个 super 当前 hash 仍等于原镜像。

| 项目 | 本机结果 |
|---|---|
| 系统/内核 | Debian 13，`7.2.0-kebab-debian13-r1+` |
| 根文件系统 | `/dev/sda15`，ext4 `rw,noatime`，上述 UUID，约 2.7 GiB 可用 |
| USB | Microsoft 内置 RNDIS 驱动状态 OK，ACM COM3；主机自动 DHCP 得到 `192.168.8.2/24`，设备 `192.168.8.1/24` |
| SSH | `debian` 公钥登录与 sudo 成功；密码/root SSH 禁用；真实 `/dev/pts/0` 通过 |
| 存储 | 64 MiB 随机文件 fsync 写入、直接读取通过；正常重启后 hash 保持；验证后删除测试文件 |
| 持久启动 | 两次主动 `systemctl reboot` 后，boot ID 改变，USB DHCP/严格主机密钥 SSH/根 UUID 正常恢复 |
| 网络/服务 | ssh、systemd-networkd、USB gadget、logind 正常，无 failed unit；wlan0 和 ath11k 固件出现，未配置 Wi-Fi 认证 |
| 温度 | 短时测试后 CPU 约 34–36°C，电池约 32°C；不代表长期负载或充电通过 |
| 触摸/内核 | 本次采样无旧 invalid-length 洪泛、无活动 Oops/panic；触摸输入事件未测，DSI PLL 重复 disable/unprepare 两条启动 WARNING 仍存在 |

一轮额外关机已明确定位：用户短按电源键，对应 logind 日志 `Power key pressed short`、`Powering off` 及正常关机流程，未发现该轮内核崩溃。已在手机配置 `HandlePowerKey=ignore`，重载后实时 DBus 属性返回 ignore；同一改动进入主机构建脚本。修改后没有再次要求用户实按测试，不能把物理复测标为通过。硬件强制关机流程不受 logind 策略控制。

RTC 实读为 1970 年，原先每次重启 systemd 回到包构建基准日期。已用主机 UTC 校准并保存 `/var/lib/systemd/timesync/clock`；再次正常重启后系统使用该文件的时刻下限。该标准机制见 [systemd v257 手册源码](https://github.com/systemd/systemd/blob/v257/man/systemd-timesyncd.service.xml)。这是保存时钟基准，不能计入断电时间或代替联网 NTP；NTP 与 Wi-Fi 留待后续配置。时钟种子和忽略电源键是手机运行后配置，不在原封存 r1 rootfs 镜像中，恢复该候选后须重设。

首次 SSH 在指定源地址的直接 USB 链路上记录首次见到的主机密钥，没有声称带外认证；后续连接及重启均强制核对保存的密钥。已创建并实测本机连接配置：

```powershell
ssh -F F:\codexprojects\oneplus8t\local\ssh\kebab-server.conf kebab
```

当前不恢复 Android，继续保留运行 Debian。长期稳定性、Wi-Fi 认证/DHCP/DNS、实际充电和触摸事件仍未验收；不是 24 小时稳定服务器认证。原始日志、主机密钥记录、写入日志和原始身份信息仅在 `local/persistent-r1-*` 保存。
