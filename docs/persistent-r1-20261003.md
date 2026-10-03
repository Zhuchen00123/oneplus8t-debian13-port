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

当前 r1 已完成主机打包；持久手机部署尚未执行。后续记录必须分别列明 Fastboot 实际写入、Linux kernelrelease、根 UUID/设备、USB DHCP/SSH、Wi-Fi 和 I/O/负载。此前 RAM 成功不能代替本组验收。

封存校验已通过：全部持久组文件 SHA-256、boot/fstab UUID、USB/fsck/固件内容；rootfs 578、server initramfs 200、RAM initramfs 8 模块实际字节均与新内核一致，实际持久 initramfs USB 脚本与修正源码一致。最初非 root 的 cpio 解包因设备节点权限失败，保留原目录并在新的 WSL root 审计目录完成；未忽略此失败或沿用不完整结果。

新 RAM/服务器成组快照：`backups/persistent-r1-20261003-220740-832/`，54 文件复制后逐份 hash 一致；完整内核源码快照 `backups/kernel-20261003-215257-924571348/` 已校验。刷写前 Fastboot 实读 product=kona、slot=a、unlocked=yes、is-userspace=no，super=7 GiB、boot_a=96 MiB、dtbo_a=24 MiB。原厂 boot_a / dtbo_a / whole-super sparse 输入重新核验通过。
