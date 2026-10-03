# Debian 13 / Linux 7.2 服务器移植

日期：2026-10-03。用户明确选择以 7.2 为主，重点参考 jfbobier 的 kebab bring-up。目标先是无桌面服务器：SSH、USB 网络、Wi-Fi、存储和持续运行；不以蜂窝、相机、音频作为首启门槛。

## 来源与当前边界

本轮 WSL `git ls-remote` 核对 main 和 `kernel-7.2.0-r26` 均为 `2e5e065da8ea8d242ecc34bfe064b88257797aa9`。继续使用已有内核提交 `f6ac9e9c86d0c92ef6db1c4f5a4c3ccdc77d1d5a`、`7.2.0-kebab-debian13+` 和 578 个匹配模块；没有重新拼接或升级内核。

参考：[固定社区源码](https://github.com/jfbobier/Oneplus-8T-kebab-pmos-bringup/tree/2e5e065da8ea8d242ecc34bfe064b88257797aa9)、[固件说明](https://github.com/jfbobier/Oneplus-8T-kebab-pmos-bringup/releases/tag/kernel-7.2.0-r26)、[设备配方](https://github.com/jfbobier/Oneplus-8T-kebab-pmos-bringup/blob/2e5e065da8ea8d242ecc34bfe064b88257797aa9/device/oneplus-kebab/APKBUILD)。文件从锁定 Git 对象读取，保留原始内容、出处和仓库许可证，不运行社区刷机/SSH 部署脚本，不在 Debian 安装 Alpine APK。

**仅主机集成。手机仍是 KB2000 / ColorOS 14.0.0.602、slot a、unlocked/orange、UID 2000、无临时 su；本轮只读复核电量为 100%。未临时启动 Linux，未刷写或切换槽位。** 社区 KB2003 的硬件结果不能代替本机验证。

## 已集成的内容

| 部分 | Debian 服务器候选 |
|---|---|
| 基础系统 | Debian 13 trixie arm64，延续已验证基础系统；无桌面，默认 multi-user.target |
| 软件来源 | Debian 签名仓库 main / non-free-firmware，认证 deb 输入归档；未改主机全局软件源 |
| USB | RNDIS + ACM；192.168.8.1/24，电脑计划 192.168.8.2/24，无默认网关；initramfs 提前建立 gadget，systemd 接管同一实例 |
| Wi-Fi | firmware-atheros、iw、rfkill、wpasupplicant、wireless-regdb、networkd DHCP、resolved DNS；尚未写入 SSID/密码 |
| SSH | debian 用户、公钥认证、禁止 root/密码登录；首次启动生成 host key，machine-id 清空，D-Bus 使用同一 machine-id |
| GPU/蓝牙通用固件 | Debian firmware-qcom-soc + firmware-atheros；7 个社区要求路径存在并记录 SHA-256；SQE 头内版本字段读为 0x112 |
| OEM 固件 | 本机已校验归档中的 ADSP/CDSP/SLPI、GPU zap，59 个 metadata/分段/ELF 文件；原始字节不变 |
| QRTR | qrtr-tools 1.1-2+b1 的 qrtr-ns 服务；本内核 CONFIG_QCOM_PD_MAPPER=y，不另启动重复的用户态 pd-mapper |
| 可选设备配置 | 社区蓝牙地址 helper/udev、DP 模式 helper、ALSA UCM；配置与依赖已放入系统，不代表硬件已可用 |
| 服务器策略 | 不自动加载 mhi_pci_generic、snd_soc_tfa2；未部署 SDX55/NV 服务和本机 Venus 固件，Venus 内核驱动仍禁用 |
| 日志 | 持久 journal 上限 64 MiB，RAM journal 上限 16 MiB |

firmware-atheros / firmware-qcom-soc 当前锁定输入版本为 `20250410-2`；wireless-regdb 为 `2026.05.30-1~deb13u1`。APK 包名不能直接用于 Debian。

本机 DSP 是 split ELF：`.mdt` 包含 ELF 头和 OEM hash/signature metadata，`.bNN` 包含分段。7.2 `drivers/soc/qcom/mdt_loader.c` 接受 `.mbn` 名称的分段元数据，并按 program-header 索引派生 `.bNN` 文件名。本项目把原 `.mdt` 字节复制到 DTS 所需的 `postmarketos/*.mbn`，连同分段部署，逐段核对大小、头和签名元数据；不是仅改一个文件名就丢掉分段，也没有重新签名。**未做 OEM 签名密码学验证或实机认证。**

音频 UCM 已集成，但扬声器驱动缺少温度/振膜保护，服务器默认不加载。rmtfs、tqftpserv-sdx55、MHI EFS、UIM/PIN 自动化不作为本阶段服务器服务启用；本轮不承诺蜂窝、音频或传感器功能完成。

## 可复现主机流程

主机需要已有 debootstrap/QEMU、mkbootimg/unpack_bootimg、dtc、e2fsprogs、initramfs-tools-core（lsinitramfs）和 Android libsparse 工具；本轮 libsparse 来自 Debian 签名包 `1:34.0.5-12`。构建目录在 Linux 文件系统；所有工具不包含 ADB、fastboot 或 SSH 手机部署命令。

```sh
P=/mnt/f/codexprojects/oneplus8t
B=/home/zhuchen/oneplus8t-server-new-build
# 以 root 身份运行；目的地必须全新。
bash "$P/scripts/linux/prepare-server-rootfs.sh" \
  "$P/artifacts/boot-preparation-20261003/debian-base" "$B"
python3 "$P/scripts/linux/stage-device-firmware.py" \
  --archive "$P/firmware/kb2000-14.0.0.602-20261003-154007/firmware.tar" \
  --manifest "$P/firmware/kb2000-14.0.0.602-20261003-154007/manifest.json" \
  --rootfs "$B/rootfs" --report "$B/firmware-staged.json"
python3 "$P/scripts/linux/integrate-server.py" \
  --build-dir "$B" --bringup "$P/sources/bringup"
unshare --mount --propagation private python3 "$P/tests/Test-ServerUserspace.py" \
  --rootfs "$B/rootfs" --report "$B/userspace-qa.json"
python3 "$P/scripts/linux/build-server-candidate.py" \
  --build-dir "$B" \
  --kernel-artifacts "$P/artifacts/7.2.0-kebab-debian13+/f6ac9e9c86d0-064fa50f8341/kernel" \
  --stock-boot "$P/backups/partitions-20261003-152513-342/boot_a.img" \
  --output-dir "$P/artifacts/server-port-new-build"
python3 "$P/scripts/linux/verify-server-candidate.py" "$P/artifacts/server-port-new-build"
```

本轮过程目录 `candidate/`、`final/`、`complete/` 留作诊断；最终交付只认 `artifacts/server-port-20261003/ready/server-manifest.json`。后续构建必须使用新的目录，不覆盖旧组。

## 根文件系统与部署含义

持久候选是 **4 GiB ext4**，提供文件树 tar 和 Android sparse ext4 镜像；不是带 GPT 的全盘镜像，也不是 Android 动态 super 容器。UUID 在打包时生成，同时写入 fstab 和 boot cmdline，UFS/ext4 驱动已内建。initramfs 含 USB 依赖、GPU/DSP split 文件和 fsck.ext4。实际 boot header、地址和 OS 字段与已测 KB2000 boot 保持一致。

参照社区，持久根文件系统的**候选目的地**是本机 7 GiB 的 `super`（Android 实测 `/dev/block/sda15`）；Linux 枚举路径可能不同，启动用 UUID，不照抄 KB2003 的 `/dev/sda14`。写入后会替换 Android 的动态分区内容。super 是共享分区，不能承诺切回另一 boot 槽就恢复 Android。剩余 userdata 不改动，也不自动扩容或重分区。

**这个目的地尚未获准写入。** 先通过 RAM 首启验证内核和 USB，再按真实枚举和日志决定持久部署。后续 boot_a、dtbo_a、super 的每项写入需列出目标、hash、恢复流程并取得明确授权；不顺带写 vbmeta/recovery/userdata。

## 下一步：只临时启动 RAM 救援候选

候选仍是既有 `artifacts/boot-preparation-20261003/rescue-final/boot-rescue.img`，18,751,488 字节，SHA-256 `f339e7d993c8c4d3bf6527e706c4219c0d6da7d07bd569331cba54def057260f`。它只在 RAM 运行，不挂载手机存储，不启动 DSP/蜂窝或 NV 服务；不会直接运行持久服务器 rootfs。

授权范围应明确为：重新检查设备/电量/备份 → reboot bootloader → 指定同一序列号只读 getvar → 临时 `fastboot boot` 此救援镜像 → 只读确认 kernelrelease、USB SSH、UFS 枚举。ADB/Fastboot 需显式指定序列号，原始输出仅存 local/。电脑 USB IPv4 配置另行说明，不修改默认网关。

失败先按键返回 Bootloader、重启原厂 Android，不自动刷入镜像。原厂 DTBO/ABL 问题若阻挡，另行申请仅 dtbo_a 的方案，不能扩大本次临时启动授权。[首次启动及 DTBO 回退细节](linux-boot-preparation.md)

恢复素材：已校验的原厂 boot_a / dtbo_a / recovery_a / super；原始清单 `backups/partitions-20261003-152513-342/manifest.json`。后续写 super 时需要原厂 super 的 sparse 转换与往返校验，完整恢复步骤尚未实际执行。26 份分区备份不含 GPT/完整启动链/用户数据，不是完整救砖保证。

## 验收边界

本轮主机验收包括：分段 ELF 的缺失/截断/metadata 不符负例、真实 ARM64 sshd 正确/错误公钥、密码禁用、578 模块 vermagic、AOSP 解包、ext4 只读 fsck、sparse 往返 SHA-256、initramfs 必要文件、根 UUID 与 SSH 身份检查。PTY 没有在本轮标为通过；此前 user-mode PTY 失败记录保留。

实机待测：ABL/DTBO、真正启动、USB 枚举与重连、Wi-Fi 认证/DHCP/DNS、UFS I/O、温度和充电、连续 24 小时负载。容器需要的 namespaces、MEMCG、seccomp、overlay、veth/bridge/NAT 配置已存在；没有据此声称 Docker 已跑通。

最终 hash、验证输出、备份和提交见本文件后续实测记录以及 `docs/validation.md`。镜像含本机 OEM 固件，仅本地使用，不进入 Git 或上传。

## 本轮交付记录

最终 WSL 构建目录：`/home/zhuchen/oneplus8t-server-20261003-ready`。195 个已安装包。initramfs build log 无警告；标准 Debian initramfs 包含 early CPIO 和压缩主归档，不能当成纯 gzip 解包。

| 最终文件 | 字节数 | SHA-256 |
|---|---:|---|
| boot-server.img | 45,617,152 | `2bd36f97287c984014c3e38e5e837f2553350a0f56d3ad38a001479c77f3d38d` |
| initramfs-server.gz | 30,189,082 | `481bd909ef92335830d3c8ff85807ea39ec4249edf765f00225ad1f24b144fe6` |
| rootfs-server.sparse.img | 1,092,640,156 | `9263b7b481c652903e6fc6cd634539d8ba0ec210e39c63c2b9e3ad62922a175c` |
| debian13-server.tar.gz | 608,974,799 | `be78d21a53b1ff9dc2d576ce9c8b4dbdbcb412b5ea30e92c62360386c03c107c` |
| server-authenticated-packages.tar.gz | 191,188,776 | `07d43b41ff4bbc230c6a4a4a010eca00710aae7461bc144528c4ed67dcabfabf` |

根 UUID：`0c3633e6-842d-4fca-8803-742b7a0c61f1`；4 GiB raw ext4 SHA-256 `546e2406b0d0fcc204313fc51df42f9ba17b2c8dee51425348382da52fae2904`。raw 在 WSL 保存，Windows 交付为 sparse，往返 raw hash 相同。e2fsck 5 阶段通过；占用 308123 / 1048576 个 4096-byte 块。

`verify-server-candidate.py` 已实际通过全部 manifest hash、boot/fstab UUID、initramfs USB/fsck/OEM split 文件、rootfs 原始社区内容与固件、SSH/机器身份和 Wi-Fi 无凭据检查；`Test-FirmwareLayout.py` 6 项、`Test-BootImage.py` 5 项通过。ARM64 OpenSSH 正确 key 登录、错误 key 拒绝、密码禁用均通过，PTY 仍未宣称通过。

成组快照：`backups/server-20261003-193710-499/`，复制前/后 hash 通过；原厂 boot_a、dtbo_a、recovery_a、7 GiB super 在原备份位置重新大小/hash 验证通过。快照复用原厂恢复文件位置，不再次复制 super；仍在同一 F 盘，需要独立介质副本才能提供磁盘故障恢复保障。

一次 WSL 多层引号使包安装准备误写主机 policy-rc.d，复制检查立即失败；确认该文件是本次新建后已清理，主机不再留此启动阻断文件。后续使用脚本文件调用，QA 私钥/挂载均清理。全程没有手机分区写入。
