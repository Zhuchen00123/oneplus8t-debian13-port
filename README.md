# OnePlus 8T / KB2000 → Debian 13

OnePlus 8T 中国版的主线 Linux 移植与备份管理仓库。目标为 Debian 13 arm64，内核尽量新。

**当前阶段：屏幕取证已结束，进入主线 Linux 启动准备。** 已生成 7.2.0 RAM 救援 boot、独立的社区空 DTBO 候选和 Debian 13.7 arm64 基础 rootfs 归档（含 578 个匹配模块）。仅完成主机准备，未在手机启动或刷写。原厂 DTBO 离线检查存在兼容性阻碍，固件/音频/QRTR 集成与持久 rootfs 部署仍待完成。具体镜像、校验、TWRP 取舍和恢复流程见 [启动准备](docs/linux-boot-preparation.md)。

2026-10-03 已确认设备是 KB2000 / ColorOS 14.0.0.602，Bootloader 已解锁。临时 root 实测屏幕 Samsung AMB655X、DCS ID `A5 05 01`，有效序列号按厂商规则解码日期 2020-10-08；**证据支持原厂规格的三星面板，但不能证明从未换屏或完成真伪认证**。[屏幕实测](docs/panel-evidence.md)

社区已具备较完整的显示、触摸、GPU、Wi-Fi 和蓝牙基础。选取最新可用的 SM8250 7.2.0 分支与固定社区补丁作为构建基线，同时追踪 Linux 7.3-rc5。社区成功机型为 KB2003，KB2000 仍需逐项验证。[调查与来源](docs/community-status.md)

已完成调查、源码复现、主机构建验证、Bootloader 解锁和临时 root 取证。26 个指定原厂分区已备份并通过校验，共 9,154,465,792 字节；本机固件也已归档并校验。未刷写镜像或永久安装 root。原厂备份来自运行中的 Android，不保证跨分区原子一致，也不包含全盘/GPT/用户数据。构建成功不等于 Debian 已在手机启动。

已在 Debian 13 WSL 完成 `7.2.0-kebab-debian13+` 的 Image、Image.gz、DTB 和 578 个模块构建，模块 vermagic 全部一致，ARM64 架构及输出 SHA-256 已核验。产物入口：[内核清单](artifacts/7.2.0-kebab-debian13+/f6ac9e9c86d0-064fa50f8341/kernel/kernel-manifest.json)。

## 目录

| 路径 | 用途 |
|---|---|
| `AGENTS.md` | 项目协作规范和设备操作边界 |
| `sources.lock.json` | 固定的第三方源码、提交与版本 |
| `config/` | 本机非敏感配置与 Debian 构建配置 |
| `docs/` | 社区调查、设备取证、移植与恢复记录 |
| `scripts/` | 主机工具与只读手机工具 |
| `sources/` | 下载的第三方 Git 仓库，忽略 |
| `local/` | 本机原始日志与隐私信息，忽略 |
| `artifacts/` | 构建输出，忽略 |
| `backups/` | Git 快照和分区备份，忽略 |
| `firmware/` | 从自己设备提取的固件，忽略 |

## 使用

Windows 工具要求 PowerShell 7，已安装 ADB。先通过 `adb devices -l` 得到序列号，在下列命令中替换 `设备序列号`：

```powershell
pwsh -NoProfile -File scripts/Get-DeviceInfo.ps1 -Serial 设备序列号
pwsh -NoProfile -File scripts/Sync-Sources.ps1
pwsh -NoProfile -File scripts/Backup-Project.ps1
```

Linux 构建使用 Debian 13 WSL，源码和输出放在 Linux 大小写敏感文件系统。使用专用的持久构建目录；重要产物导出回本项目：

```sh
bash /mnt/f/codexprojects/oneplus8t/scripts/linux/prepare-kernel.sh ~/oneplus8t-build
bash /mnt/f/codexprojects/oneplus8t/scripts/linux/build-kernel.sh ~/oneplus8t-build dtb
bash /mnt/f/codexprojects/oneplus8t/scripts/linux/build-kernel.sh ~/oneplus8t-build kernel
```

阶段验证结果在 [validation.md](docs/validation.md)。手机部署的前置条件与恢复要求见 [备份说明](docs/backup-and-recovery.md)，Debian 路线见 [移植计划](docs/porting-plan.md)。

本轮最终候选为 [救援启动清单](artifacts/boot-preparation-20261003/rescue-final/boot-manifest.json)，Debian 基础归档为 [rootfs 导出清单](artifacts/boot-preparation-20261003/debian-base/rootfs-export-manifest.json)。`rescue/`、`rescue-v2/`、`rescue-v3/` 是过程记录，不用于部署。构建脚本没有手机部署命令；`dtbo-empty-community.img` 单独生成不代表已获准刷写。

受限屏幕节点的权限取得和返回原厂 boot 记录见 [临时 root](docs/temporary-root-plan.md)：匹配的原厂 boot 在本机修补并临时启动，UID 0 和实际分区备份已验证；未使用 Direct Install 或刷 recovery。

`kernel/` 与 `dtb/` 构建模式分别导出清单，共享构建目录有互斥锁。早期未分模式的产物目录只作过程记录；以上链接为最终导出。
