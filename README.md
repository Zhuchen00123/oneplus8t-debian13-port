# OnePlus 8T / KB2000 → Debian 13

OnePlus 8T 中国版的主线 Linux 移植与备份管理仓库。目标为 Debian 13 arm64，内核尽量新。

**服务器就绪盘点：基础系统/SSH/存储/限充正常，适合部署和调试服务；尚未完成长期无人值守及公网MC验收。** 最新重启后校园HTTPS探测超时、HTTP转认证门户、NTP未同步，需重新认证/排查；真实80/75周期、持续负载、MC/隧道和离机数据备份仍待验证。[当前就绪记录](docs/server-readiness-20261004.md)。

**最新存储：系统文件系统已扩至7GiB，df显示6.9GiB、约3.9GiB可用；用户授权清空原Android userdata后建成约224.46GiB ext4数据分区，挂载/srv，实际约220GiB可用。** 正常重启自动挂载、32MiB fsync/直接读回、分区表未变、USB SSH与限充自启通过。MC服务与世界文件应使用/srv并要求挂载存在。[容量与扩容记录](docs/storage-expansion-20261004.md)。

**最新限充部署：80%停充／75%恢复策略已常驻启用。** `7.2.0-kebab-debian13-r2-charge-policy+` 已实机完成224秒续约、停止服务与杀进程到期恢复测试，当前高电量停充读回通过；用户授权的A槽启动成功标记修复及一次正常重启已通过，重试数保持5，USB/SSH和策略服务自动恢复。真实80/75完整充电周期、插拔与长期负载仍未验收。[限充v1记录](docs/charge-policy-v1-20261004.md)、[启动记账修复](docs/boot-success-repair-20261004.md)、[基础配置](docs/server-base-20261004.md)。MC/穿透已在本会话暂停，[另开会话提示词](docs/mc-cnb-resume-prompt-20261004.md)已准备。

**最新 r2（2026-10-04）：显示与触摸修复已成组内建，`7.2.0-kebab-debian13-r2+` 匹配模块、USB SSH、面板/GLES、真实单指/双指/抬起、两次正常重启和文件持久性通过；旧触摸接管服务已禁用。** 曾发生无操作指针漂移，后续 60 秒静置零事件，用户重启前后确认触控正常且不漂移；间歇误触根因和长期稳定性仍未确定。[r2 成组记录](docs/r2-integrated-20261004.md)。下文为 r1 历史，不代表当前仍由模块接管。

**Wi-Fi 最新阶段：手机已直连 gdut，用户完成校园门户认证，实际外网 HTTP/HTTPS、DNS、NTP 通过。** USB SSH 保持可用；RTC 未修复，重启后认证保留与长期无线稳定性未验收。用户准备部署 MC，穿透方案待选，尚未安装客户端。[GDUT 接入记录](docs/gdut-wifi-20261004.md)。

**当前阶段（2026-10-04）：Debian 13 / `7.2.0-kebab-debian13-r1+` 持久运行，USB DHCP/严格 SSH/根读写/正常重启通过。蓝牙地址服务卡住已修复并重启复验，Adreno 650 / Mesa GLES 绘制与像素回读通过。** 相机工具/IPA 已补齐但捕获缓冲分配失败；NFC 驱动、SSC 服务存在，功能未测。timesyncd 已启用但尚无联网同步；Wi-Fi 认证稍后配置。较长日志再次出现 TCM 无效触摸报文，DSI 警告仍在；音频、蜂窝/NV、完整80%限充和长期稳定性未验收（已完成一次有界停充/恢复验证，触摸已修复并实机验收）。手机保留 Linux，本轮仅为充电候选测试写入并已回滚 `boot_a`，未改生产充电策略。[最新盘点](<docs/userspace-audit-r1-20261004.md>)、[r1 部署历史](<docs/persistent-r1-20261003.md>)。既定 dtbo_a/boot_a/super 部署及原厂恢复持续授权不变。

**触摸已修复并实机验收**：本机 TCM 是 7 字节前缀 + 7 字节对象布局，旧 builtin 驱动把 14/21 字节报文判为非法导致不可用；修正补丁按该布局解析，编译为模块 `tcm_r2_test.ko` 安装到 `/lib/modules/…/extra/`，由 `kebab-touch-fix.service` 开机时接管 `13-004b`。有界采集在真实触摸下录得 5,209 个事件、两指并存 tracking id，用户两次确认（部署后、重启后）桌面点按与拖动可用。内建（编入内核）路线因 ABL 拒绝该引导镜像而暂缓，原因未查明。[触摸协议记录](<docs/touch-protocol-r2-20261004.md>)。

最小 XFCE 桌面已装：离线 161 包、不升级不删除、LightDM 保持禁用，默认仍是 `multi-user.target`。有界会话实测 Xorg modesetting + glamor（Adreno 650）在 `1080x2400@120Hz` 工作，XFCE 组件正常、整机约 750 MB 内存，并用 `XGetImage` 抓到 156,745 字节桌面帧（SHA-256 `cc82b3ef…b1255fe`）；**内屏黑屏已定位并修复**：厂商 dtbo 要求 `qcom,mdss-dsc-slice-per-pkt = 2`（驱动留 0 被当成 1）且面板 `reset-gpios` 从未被驱动，两处一起修正后面板开始应答 DCS 读（`actual_brightness` 由超时变为 39936）、`frame_done_cnt` 归零，用户实机确认看到白底+红条测试画面，同一内核上 XFCE 桌面在 `1080x2400@120Hz` 运行。[桌面安装与显示验证](<docs/xfce-desktop-20261004.md>)。

第 5 轮黑屏排查进一步排除软件层：面板 `vddio-supply`（`vreg_l14a_1p8`）在驱动里从未被取用（用户计数 0，用临时模块强制拉高后面板行为无变化）；PHY PLL 每次启动 probe 报 lock failed，但两次 devcoredump 中 `dsi0_pll` 状态寄存器 bit0 均为 1（运行期已锁定）；停 X 后直接向扫描输出缓冲写入白底+红条、DPU 正常出帧（`vsync` 递增、无 underrun/超时），用户确认**仍然全黑**，且面板对 DCS 读（0x52）始终不应答、不发 TE。另发现 fbcon 无法向 DRM 帧缓冲绘制（`framebuffer is not in virtual address space`）的独立缺陷。结论：故障位于 DSI 物理链路/面板上电初始化一侧，下一步为候选内核实验。[详见文档](<docs/xfce-desktop-20261004.md>)。

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
