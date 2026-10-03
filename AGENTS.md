# OnePlus 8T 主线 Linux 移植项目

## 全局协作规范

- 默认使用简体中文。
- 主代理负责规划、视觉任务、架构与跨模块决策、审核和最终交付。
- `explorer` 用于只读检索与取证；`worker` / `deepseek_worker` 用于边界清晰的局部执行任务。
- 命名子代理必须从空白上下文启动，任务正文必须自包含，不使用完整历史 fork。
- 多个写任务只有在文件写集互不重叠时才能并行。
- 子代理完成后，主代理必须复核改动与验证结果，并及时结束已完成线程。
- 保留用户已有改动；危险、破坏性或影响范围不明确的操作必须先确认。
- 仅在用户或适用规范明确要求委派时启动子代理。

## 目标与优先级

- 设备：OnePlus 8T 中国版 KB2000，代号 kebab，SM8250 / Adreno 650。
- 用户目标：Debian 13 arm64，内核尽量新；先核对社区进度，再做移植、备份与 Git 管理。
- 当前优先事项：核查实际屏幕；软件报告的分辨率/刷新率不能证明屏幕原装或从未更换。
- 2026-10-03 解锁后取证：ColorOS `KB2000_14.0.0.602(CN01)`，Bootloader unlocked / verified boot orange，ADB 仍为 UID 2000；无原厂分区备份、无可用 root。后续操作前重新核验状态。
- 最新可用 SM8250 社区基线为 `sm8250-7.2.0`；最新主线参考为 `v7.3-rc5`。这些是本次调查快照，更新前重新查证。
- KB2003 的成功记录不能当成 KB2000 / ColorOS 14 的测试结果。

## 来源与移植方法

- 优先读取 Linux 官方源码、SM8250 社区源码、OnePlusOSS 和维护者的一手记录。
- 在 `docs/community-status.md` 标注调查日期、提交、来源与限制，区分官方主线、社区主线分支和 Android 厂商内核。
- 第三方仓库和版本固定在 `sources.lock.json`。源码下载到被忽略的 `sources/`，不得把内核源码直接重复提交到管理仓库。
- 不直接运行社区的刷机脚本。部分脚本默认刷机，即使 `--no-flash` 仍会向手机部署模块。
- 主机准备、源码准备、构建、打包、手机部署分别执行；本项目构建脚本只操作构建目录。
- 构建并校验设备树后再编译内核和模块；内核、DTB、模块、配置、固件路径和 boot image 参数必须作为一组保存。
- Debian 13 需要自己的 initramfs、firmware、udev、ALSA UCM 与启动/槽位策略，不能直接安装社区的 Alpine APK 当作移植完成。
- 一次修改处理一个可验证问题；社区补丁保留作者与许可证。升级内核时检查重复补丁、DTS 漂移和模块兼容性。

## 设备操作边界

- 已授权的 ADB 只读调查、源码下载、构建和本地备份可继续执行。
- 解锁/重新锁定 Bootloader、刷写/擦除任何分区、写 super、切换槽位、改分区布局或清除用户数据，必须在列明目标、备份和恢复方案后取得明确授权。
- 2026-10-03 用户已明确要求先解锁 Bootloader 再尝试屏幕取证；执行前保存可读数据并确认其接受恢复出厂造成的数据丢失。该授权不包含 root、关闭 SELinux、刷 recovery、其他分区刷写或重新锁定。
- 随后用户要求继续提供屏幕取证权限；本轮按 `docs/temporary-root-plan.md` 准备匹配原厂 boot 的 Magisk 临时启动，用途限本机屏幕取证和原厂分区备份。不得将此扩展为永久 root、Direct Install、刷 recovery、关闭 SELinux、刷写其他分区或重新锁定。手机安装/身份验证与 Shell 授权提示需用户处理。
- 手机命令必须指定设备序列号；原始日志、序列号和实际面板序列号仅存 `local/`。
- 分区路径来自本机 `/dev/block/by-name/`，不得照抄 KB2003 的 `/dev/sda14`：本机实际 `super` 指向 `/dev/block/sda15`。
- 原厂分区备份必须使用原始二进制流，校验字节数和设备端/主机端 SHA-256；权限拒绝或读取失败不得生成“成功”记录。
- 用户数据备份与原厂分区备份分别记录，解锁会清除用户数据。未备份不能假装已经有恢复保障。
- 固件、分区镜像、EFS/NV、IMEI、ICCID、PIN、密钥及个人文件不得提交 Git 或上传。

## Git、备份与验证

- 管理仓库 `main` 保存规范、来源锁定、工具和验证记录；阶段改动用主题分支和小提交。
- 内核改动在 Linux 文件系统中的独立仓库维护，用补丁导出到本项目；不在 Windows 默认大小写不敏感的工作树构建内核。
- 提交前检查差异、敏感文件忽略规则和对应验证；不覆盖用户的 Git 身份，不擅自创建远程仓库或推送。
- 每个阶段创建项目 Git bundle 和当前工作文件快照，校验后再标记完成。`backups/` 的同盘副本需用户另行复制到外部介质。
- 执行过、仅静态检查过、社区声称支持、尚未测试、被硬件/权限阻塞的事项必须分别说明。

## 常用入口

- `README.md`：项目现状与快速入口。
- `docs/device-assessment.md`：本机屏幕、Bootloader 与分区取证。
- `docs/community-status.md`：社区与主线支持调查。
- `docs/porting-plan.md`：Debian 13 移植路线和验收。
- `docs/backup-and-recovery.md`：备份与恢复要求。
- `docs/git-workflow.md`：仓库管理与恢复步骤。
- `scripts/Get-DeviceInfo.ps1`：只读设备调查。
- `scripts/Sync-Sources.ps1`：固定来源下载和核验，不重置已有改动。
- `scripts/Backup-Partitions.ps1`：有权限时只读备份指定分区。
- `scripts/Backup-Project.ps1`：管理仓库快照。
- `scripts/Backup-BuildArtifacts.ps1`：按产物清单校验并保存内核/DTB/模块成组快照。
- `scripts/linux/prepare-kernel.sh`、`build-kernel.sh`：仅在主机准备/构建。
- `scripts/linux/backup-kernel.sh`：完整源码、配置与补丁快照。
