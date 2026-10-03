# Debian 13 移植路线

目标：KB2000 / kebab 上运行 Debian 13 arm64，逐步采用最新可用内核。每个阶段保留日志、配置、提交、SHA-256 与可复现命令。

## 0. 屏幕与恢复前置条件

已解锁并通过临时 root 读取三星 AMB655X、DCS ID 和面板序列号，证据支持原厂规格，装配/更换历史仍未确认。26 个指定原厂分区已备份、逐项大小及设备前后/主机 SHA-256 通过；本机固件归档也已取得，签名和 Linux 兼容性未逐文件验证。仍需离线/静止状态的跨分区一致性与实际恢复验证，不能视为全盘或完整救砖保证。后续分区写入仍需具体方案和单独授权。

确认 KB2000、ColorOS 14 的固件与 KB2003 社区基线差异；不能先降级或换 ROM 来“试一下”。

## 1. 可复现源码与内核

1. 固定 SM8250 7.2.0 和 bring-up 提交，下载后核验实际 SHA。
2. 在大小写敏感的 Linux 文件系统准备独立源码工作树，保留纯基线标签。
3. 按顺序应用 21 个内核补丁，保留作者；单独提交整份 DTS 的引入。可选补丁不默认启用。
4. 采用社区已测配置，再合并 `config/debian13.config`。检查 TFA9872 保持禁用、Venus 保持禁用、UFS/USB 网络路径可进 initramfs。
5. 构建 DTB；检查 compatible、显示/触摸节点、固件路径、供电以及 reserved-memory。
6. 构建 Image/Image.gz、全部模块与对应 DTB，记录 kernelrelease。导出配置、build-info、完整日志和校验和。
7. 检查新主线/SM8250 tag，建立 7.3 实验分支；审查社区基础差异后逐项重放，删除已合并补丁。

验收：源码提交固定、补丁全部可应用、DTB 编译成功、所需 Image 和模块构建成功。仅这一步成功仍不代表实机能启动。

## 2. Debian 用户空间与 boot image

- 使用 Debian trixie arm64 rootfs；优先评估 Armbian kebab 对 Debian 的集成，将新内核、设备用户空间配置与三方 Debian rootfs 组合。
- 系统启动依赖包括 systemd、udev、initramfs-tools、kmod、SSH、USB gadget、网络配置、QRTR/rmtfs/tqftpserv/pd-mapper 与 ALSA UCM；按真实日志缩小必要服务集合。
- Alpine APKBUILD 仅作构建/服务参考：转换依赖和文件布局，模块按 Debian 约定安装，明确新内核升级会如何更新 boot.img。
- 来自自己设备的签名 ADSP/CDSP/SLPI/GPU/SDX55 固件只存 `firmware/`。不能用 KB2003 任意固件替换 KB2000 实机固件。
- 构建 boot.img 前读取/解析本机原厂 boot image，确认 header、页大小、offset、dtb 和 cmdline。社区参数存于配置，但尚未实机核对。
- 初期使用最小 initramfs 提供 UFS、必要固件、USB gadget 和救援 shell。rootfs 的 UUID 在打包时生成并验证。
- 在恢复方案明确前不自动写 super；临时 `fastboot boot` 本身也须确认 Bootloader/ABL 支持、所需 DTBO 状态和用户授权，不能假设一定可用。

验收：boot.img、initramfs、rootfs、modules 的版本/路径一致；保留全部打包参数。不得包含默认公开密码、私钥或设备私密 NV。

## 3. 实机启动和逐项验收

| 次序 | 验证 | 证据 |
|---|---|---|
| 1 | 仍可进入 Bootloader/原厂恢复；确认实际槽位 | 只读 getvar 与恢复流程记录 |
| 2 | 最小内核+initramfs 启动，USB 网络可达 | dmesg、设备树、远程 shell |
| 3 | UFS 和 Debian 根文件系统启动 | systemd 启动日志、挂载、UUID |
| 4 | 显示、亮度、触摸、GPU | 日志与实际触摸/图形验证 |
| 5 | Wi-Fi、蓝牙、NFC | 实际连接与多次冷启动 |
| 6 | 扬声器和麦克风 | 双通道/录音样本及 ALSA UCM |
| 7 | 电量、USB 角色、挂起/唤醒、DP | 供电/功耗观察及长时间测试 |
| 8 | 前摄；蜂窝/后摄作为独立研究 | media graph、采样和 modem 日志 |

测试前先比对 `uname -r`、模块 vermagic、kernelrelease 和树哈希。一次只修一个子系统，保存恢复点。

## 4. 内核更新策略

尽量新的顺序为：最新经 KB2000 验证的 SM8250 分支 → 新主线适配实验 → 新版本完整回归。当前 7.2.0 是可复用基础，7.3-rc5 是较新的比较目标。不得为了版本号直接丢掉显示、触摸和启动依赖。

每次升级先查 tag 是否实际发布，检查补丁 APPLIES/CONFLICT/REDUNDANT、DTS 漂移和配置变更。保存旧 Image、DTB、modules、initramfs、boot.img 与来源 SHA，再做实机更新。
