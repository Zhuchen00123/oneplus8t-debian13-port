# 备份与恢复

## 当前恢复范围（2026-10-04）

手机已持久运行r2-charge-policy，系统盘7GiB、/srv数据盘约220GiB可用。先读[最新交接](handover-20261004.md)，不要将下方2026-10-03的准备状态当作当前状态。

- 原厂26个指定分区备份已校验，但不含userdata、GPT或完整引导链。原Android userdata未备份，已按一次明确授权格式化，不能承诺恢复其个人数据。
- [成功位修复](boot-success-repair-20261004.md)执行前另有本机主备GPT原始块双端备份和校验；它不是所有磁盘的全盘镜像，也不是后续GPT写入授权。
- [存储扩容](storage-expansion-20261004.md)前保存当前Debian文件归档：1,780,637,710字节、33,313成员，完整读取和SHA-256通过。归档来自运行中系统，不是原子块快照；随后新增的按键服务须由其专题记录及配置模板补齐。
- 项目bundle和工作文件快照、源码与成组构建备份分别保存；可用镜像/私有配置不进Git。旧4GiB rootfs不等同于当前7GiB系统及/srv数据盘，恢复不能混用内核、DTB、模块和initramfs。
- 同盘副本仍需复制到独立介质；MC世界离机备份及完整恢复演练尚未验收。已经执行的正常重启/单项读回不能代替全套恢复测试。

## 原厂备份与准备阶段

2026-10-03 主线 Linux 准备阶段已再次复核全部 26 个原厂镜像的大小/SHA-256。新增成组快照 `backups/linux-preparation-20261003-175305-853/`：36 文件，含最终 RAM 救援候选、Debian 基础归档、原厂 boot_a/dtbo_a/recovery_a 恢复副本，源/副本均通过。没有新增手机写入或恢复实测；具体临时启动、DTBO 变更与回退边界见 [启动准备](linux-boot-preparation.md)。

当前原厂分区备份状态：**26 个指定分区已完成并校验**。通过已授权的临时 Magisk root 读取，清单 `backups/partitions-20261003-152513-342/manifest.json` 状态为 `verified_requested_partitions`，共 9,154,465,792 字节。每份镜像大小正确，设备读取前 SHA-256、主机 SHA-256 与设备读取后 SHA-256 全部一致。未包含 userdata/GPT/全部引导链；来自运行中的 Android，不保证跨分区原子一致。项目源码备份与手机备份分别记录，原始清单和镜像均不进 Git。

所选范围：boot/dtbo/vbmeta/vbmeta_system/recovery 两个槽位、super、persist/persist_bkp、modem 两个槽位、modemst1/2、fsg/fsc、4 个 mdm1m9kefs、mdm_oem_dycnvbk/stanvbk、mdm1oemnvbktmp。boot_a 副本与本机构建的官方 OTA boot.img 相同；临时 root 未永久写入该启动分区。

## 解锁前

备份照片、文档和应用允许导出的数据；核验恢复账号、2FA 与应用私有数据的恢复方式。Android 14 上不能把旧 `adb backup` 当作完整备份。官方/系统的本地备份也需要复制到电脑/外部介质并确认可读。

Bootloader 解锁会清除用户数据。2026-10-03 用户明确要求先解锁，再尝试屏幕取证；当前准备和执行状态见 [解锁记录](unlock-preflight.md)。该指令不包含刷 recovery、切换槽位和刷镜像。[Android 官方规则](https://source.android.com/docs/core/architecture/bootloader/locking_unlocking)

## 原厂分区备份

取得合法 root 或有权限的恢复环境后，用 `scripts/Backup-Partitions.ps1` 按本机 by-name 读取；该脚本不提权、不刷写、不重启手机。`-RootMode Su` 仅使用设备已经具备的 su，`Direct` 要求 ADB shell 原本就是 UID 0。

先用 `-Plan` 显示要读的名称与目标。计划不表示镜像已备份；执行版要求 root、设备型号匹配、每个分区存在且可读取，完成时核对字节数以及两端 SHA-256。读取超时或哈希不符会中止，`.partial` 文件不能当作恢复镜像。

本机临时 Magisk 环境把 su 放在 `/debug_ramdisk/su`，ColorOS 默认 PATH 未包含该目录。取得用户授权并验证 UID 0 后，可使用 `-RootMode Su -SuExecutable /debug_ramdisk/su`；该参数仅接受 `su` 或不含 shell 元字符的绝对路径，工具仍不自行提权或安装 root。

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

2026-10-03 已从匹配本机指纹的厂商 OTA 提取 boot/recovery/dtbo/vbmeta，所选数据与输出镜像均通过 manifest SHA-256。来源、限制和临时 root 候选备份见 [准备记录](temporary-root-plan.md)。这些是官方包中的恢复资料，不是从手机读取的原厂分区备份；没有 EFS/NV/persist，也未验证整个 OTA 签名，不能标记完整救砖资料已齐备。

随后取得临时 root，已另行读取上述实际原厂分区。主线移植固件归档位于 `firmware/kb2000-14.0.0.602-20261003-154007/`：467,082,752 字节、2,601 个 tar 条目，设备前后归档 hash 与主机 hash 一致，主机 tar 列表验证通过，符号链接保留且未跟随。副本 `backups/firmware-20261003-154349/` 再次校验；这验证传输一致性，不表示逐份固件签名或 Linux 兼容性已验证。校准/NV 仍仅属于这台手机，不混用其他设备。

### 原厂 super 恢复格式准备

2026-10-03 已在主机用 `prepare-stock-recovery.py` 生成 `artifacts/stock-recovery-20261003/`。输入为实际已验证的 boot_a/dtbo_a/recovery_a/super 原厂备份，转换前后原文件 hash 不变；未使用其他型号镜像，没有读取 NV 或操作手机。

whole-super sparse 为 7,481,440,240 字节，SHA-256 `fb2c62cb888897d8552a53b5d6fac61fc2ec5974dc7c3ae51d939352dadc4787`。独立 sparse framing/尺寸检查通过，simg2img 解码后与 7 GiB 原厂 super 的 hash `60f091b129aa2e090c6954689b7a520d98d34c0075712e0461c85c2c5c53155a` 一致。原始/解码副本留在 WSL `/home/zhuchen/oneplus8t-recovery-20261003/`，导出副本在 F 盘；独立介质状态未确认，实际恢复未测。

```sh
python3 /mnt/f/codexprojects/oneplus8t/scripts/linux/prepare-stock-recovery.py \
  --partition-backup /mnt/f/codexprojects/oneplus8t/backups/partitions-20261003-152513-342 \
  --work /home/zhuchen/oneplus8t-recovery-new \
  --output /mnt/f/codexprojects/oneplus8t/artifacts/stock-recovery-new
```

工具只准备并校验主机文件，没有 Fastboot/ADB 或自动恢复命令；必须选择全新目录。手机恢复还需要确认实际模式、目标、槽位、下载上限和具体授权，不能把本次离线校验标记为“恢复成功”。

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
