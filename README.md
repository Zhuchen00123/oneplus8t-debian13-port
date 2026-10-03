# OnePlus 8T / KB2000 → Debian 13

OnePlus 8T 中国版的主线 Linux 移植与备份管理仓库。目标为 Debian 13 arm64，内核尽量新。

2026-10-03 已确认设备是 KB2000 / ColorOS 14.0.0.602，Bootloader 仍锁定。屏幕报告 1080×2400、60/120 Hz，符合原机规格；实际面板型号、序列号不可读，因此**尚不能确认原厂屏幕**。

社区已具备较完整的显示、触摸、GPU、Wi-Fi 和蓝牙基础。选取最新可用的 SM8250 7.2.0 分支与固定社区补丁作为构建基线，同时追踪 Linux 7.3-rc5。社区成功机型为 KB2003，KB2000 仍需逐项验证。[调查与来源](docs/community-status.md)

当前阶段是调查、源码复现和主机构建验证。没有解锁、刷机或写入手机分区；原厂分区备份尚未完成。构建成功也不等于 Debian 已在手机启动。

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

`kernel/` 与 `dtb/` 构建模式分别导出清单，共享构建目录有互斥锁。早期未分模式的产物目录只作过程记录；以上链接为最终导出。
