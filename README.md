# OnePlus 8T / KB2000 → Debian 13

OnePlus 8T 中国版的主线 Linux 移植与备份管理仓库。目标为 Debian 13 arm64，内核尽量新。

**当前阶段（2026-10-04）：Debian 13 / `7.2.0-kebab-debian13-r1+` 持久运行，USB DHCP/严格 SSH/根读写/正常重启通过。蓝牙地址服务卡住已修复并重启复验，Adreno 650 / Mesa GLES 绘制与像素回读通过。** 相机工具/IPA 已补齐但捕获缓冲分配失败；NFC 驱动、SSC 服务存在，功能未测。timesyncd 已启用但尚无联网同步；Wi-Fi 认证稍后配置。较长日志再次出现 TCM 无效触摸报文，DSI 警告仍在；音频、蜂窝/NV、触摸、充电和长期稳定性未验收。手机保留 Linux，本轮无分区刷写。[最新盘点](<docs/userspace-audit-r1-20261004.md>)、[r1 部署历史](<docs/persistent-r1-20261003.md>)。既定 dtbo_a/boot_a/super 部署及原厂恢复持续授权不变。

触摸优先修复：已查明本机 TCM 是 7 字节前缀 + 7 字节对象，旧驱动布局不匹配；r2 候选通过 ASan/UBSan、匹配模块编译及三轮报告启停短测。长采集零事件，真实单指/双指/抬起尚未验收；原驱动已恢复，未部署候选或刷固件。[触摸协议记录](<docs/touch-protocol-r2-20261004.md>)。

最小 XFCE 桌面已装：离线 161 包、不升级不删除、LightDM 保持禁用，默认仍是 `multi-user.target`。有界会话实测 Xorg modesetting + glamor（Adreno 650）在 `1080x2400@120Hz` 工作，XFCE 组件正常、整机约 750 MB 内存，并用 `XGetImage` 抓到 156,745 字节桌面帧（SHA-256 `cc82b3ef…b1255fe`）；**但用户确认内屏仍是黑的**，日志显示 DSI PLL probe 锁定失败、DCS 读超时 `-110`、强制输出重启后写入也超时，物理屏幕输出未验收。[桌面安装与显示验证](<docs/xfce-desktop-20261004.md>)。

历史 RAM 阶段：原厂 DTBO 下首次启动失败，单独授权的社区空 DTBO 对照成功；先恢复原厂 DTBO、再确认 ColorOS 正常启动。旧封存 boot/rootfs 保持原样，r1 已成组重构建并部署。[RAM 成功与回退](docs/ram-boot-success-20261003.md)、[服务器集成历史](docs/debian-server-port.md)

2026-10-03 已确认设备是 KB2000 / ColorOS 14.0.0.602，Bootloader 已解锁。临时 root 实测屏幕 Samsung AMB655X、DCS ID `A5 05 01`，有效序列号按厂商规则解码日期 2020-10-08；**证据支持原厂规格的三星面板，但不能证明从未换屏或完成真伪认证**。[屏幕实测](docs/panel-evidence.md)

社区已具备较完整的显示、触摸、GPU、Wi-Fi 和蓝牙基础。选取最新可用的 SM8250 7.2.0 分支与固定社区补丁作为构建基线，同时追踪 Linux 7.3-rc5。社区成功机型为 KB2003，KB2000 仍需逐项验证。[调查与来源](docs/community-status.md)

已完成调查、源码复现、主机构建验证、Bootloader 解锁、临时 root 取证、主线 RAM 启动及 r1 持久部署。26 个指定原厂分区已备份并通过校验，共 9,154,465,792 字节；本机固件也已归档并校验。实际 Linux 部署只写 super / dtbo_a / boot_a，没有切槽、写 recovery / vbmeta / NV、清空 userdata 或永久安装 Android root。原厂备份来自运行中的 Android，不保证跨分区原子一致，也不包含全盘/GPT/用户数据。

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

当前持久部署来自 [r1 成组清单](<artifacts/server-r1-20261003/ready/server-manifest.json>)；该封存镜像未包含 10-04 用户空间补项和运行后配置，本轮没有重新打包。旧 [服务器组](<artifacts/server-port-20261003/ready/server-manifest.json>)、[早期 RAM 组](<artifacts/boot-preparation-20261003/rescue-final/boot-manifest.json>) 仅作历史记录，不混用 boot/模块。旧 [基础归档](<artifacts/boot-preparation-20261003/debian-base/rootfs-export-manifest.json>) 保留作复现输入。构建脚本仅操作主机；设备写入遵守已有明确授权边界。

受限屏幕节点的权限取得和返回原厂 boot 记录见 [临时 root](docs/temporary-root-plan.md)：匹配的原厂 boot 在本机修补并临时启动，UID 0 和实际分区备份已验证；未使用 Direct Install 或刷 recovery。

`kernel/` 与 `dtb/` 构建模式分别导出清单，共享构建目录有互斥锁。早期未分模式的产物目录只作过程记录；以上链接为最终导出。
