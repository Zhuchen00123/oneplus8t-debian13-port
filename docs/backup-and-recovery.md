# 备份与恢复

当前原厂分区备份状态：**未完成**。ADB shell 无 root 且 Bootloader locked，读取 boot_a 被系统拒绝。项目源码备份与手机备份必须分别记录。

## 解锁前

备份照片、文档和应用允许导出的数据；核验恢复账号、2FA 与应用私有数据的恢复方式。Android 14 上不能把旧 `adb backup` 当作完整备份。官方/系统的本地备份也需要复制到电脑/外部介质并确认可读。

Bootloader 解锁会清除用户数据。2026-10-03 用户明确要求先解锁，再尝试屏幕取证；当前准备和执行状态见 [解锁记录](unlock-preflight.md)。该指令不包含刷 recovery、切换槽位和刷镜像。[Android 官方规则](https://source.android.com/docs/core/architecture/bootloader/locking_unlocking)

## 原厂分区备份

取得合法 root 或有权限的恢复环境后，用 `scripts/Backup-Partitions.ps1` 按本机 by-name 读取；该脚本不提权、不刷写、不重启手机。`-RootMode Su` 仅使用设备已经具备的 su，`Direct` 要求 ADB shell 原本就是 UID 0。

先用 `-Plan` 显示要读的名称与目标。计划不表示镜像已备份；执行版要求 root、设备型号匹配、每个分区存在且可读取，完成时核对字节数以及两端 SHA-256。读取超时或哈希不符会中止，`.partial` 文件不能当作恢复镜像。

```powershell
# 序列号从 adb devices -l 获取；恢复环境型号可能不同，要先调查再显式传入。
$parts = @('boot_a','boot_b','dtbo_a','dtbo_b','vbmeta_a','vbmeta_b',
  'vbmeta_system_a','vbmeta_system_b','recovery_a','recovery_b','super',
  'persist','persist_bkp','modem_a','modem_b','modemst1','modemst2',
  'fsg','fsc','mdm1m9kefs1','mdm1m9kefs2','mdm1m9kefs3','mdm1m9kefsc',
  'mdm_oem_dycnvbk','mdm_oem_stanvbk','mdm1oemnvbktmp')
& ./scripts/Backup-Partitions.ps1 -Serial 设备序列号 -Partition $parts -Plan
# 在已有权限的环境、确认计划后，去掉 -Plan 再执行。
```

推荐在写入停止、分区未挂载的恢复环境备份。运行中的 EFS/NV 可能正在更新，即使单个文件哈希通过也不保证跨分区原子一致；应记录取得备份的状态。userdata 是加密分区，复制原始镜像通常不能代替应用/个人数据的可恢复备份。

本机 super 指向 sda15，与社区示例 sda14 不同。`super` 还包含动态分区元数据，覆盖它会影响两个槽位共有的 Android 分区布局；仅保留另一个 boot 槽不能保证 Android 可恢复。

对 EFS/NV、persist、调制解调器校准只做本机备份，不混用其他手机的镜像。abl/xbl/tz 等引导链、GPT 与 userdata 不在工具默认范围；工具无默认分区列表，需按恢复方案显式指定，不能声称这份建议列表是全盘镜像。

## 恢复资料

- 获取与 KB2000、地区、当前构建和回滚限制匹配的原厂完整恢复资料；记录可信来源、版本、校验和与解包参数。
- 审核 raw/sparse 格式、super 元数据、fastboot/fastbootd 所需模式、A/B 槽位与 AVB 链。
- 原厂恢复工具、非官方 MSM/EDL 包需要单独验证，不在初始化期间自动下载或运行。
- 恢复方案应写明要恢复的具体分区、顺序和槽位，逐个与 manifest 核对。项目不提供自动恢复/批量刷写脚本。
- 不在未完整恢复匹配签名引导链的状态重新锁定 Bootloader。

## 项目备份

`Backup-Project.ps1` 保存全部本地 Git refs 的 bundle、当前可跟踪/未忽略文件的 ZIP 快照、状态及 SHA-256。私密日志、固件、镜像和下载源码在忽略目录中，需按类别独立保存。源码的固定 Git 对象可另行用 `git archive` 导出；在 Windows 导出要用工具 `--output` 或原始二进制流，避免文字重定向损坏文件。

备份目录位于同一块 F 盘，仅保护误操作和版本回退。复制一份到外部硬盘或其他独立介质，并校验 manifest 中的 SHA-256，才具备磁盘故障后的恢复保障。

完整已打补丁源码可另行保存，不依赖远程服务恢复：

```sh
bash /mnt/f/codexprojects/oneplus8t/scripts/linux/backup-kernel.sh ~/oneplus8t-build
```

它保存 Git 对象中的完整源码归档、构建配置、来源 metadata、22 个本地提交补丁与校验和，并检查 gzip 完整性和 tar 条目。它不包含手机镜像，也不声称保存了浅克隆基线以前的全部内核 Git 历史。

已编译内核、DTB、模块和配置用单独的快照成组保存；工具先验证来源清单，再逐项复制并复核 SHA-256：

```powershell
& ./scripts/Backup-BuildArtifacts.ps1 -ArtifactDirectory ./artifacts/7.2.0-kebab-debian13+/f6ac9e9c86d0-064fa50f8341/kernel
```
