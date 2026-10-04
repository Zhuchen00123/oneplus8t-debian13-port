# A槽启动记账耗尽与成功标记修复方案（2026-10-04）

## 已查到的状态

限充control镜像首次重启后显示“系统文件被损坏，无法开机”，用户手动返回Fastboot。只读查询确认A槽 `successful=no`、`retry=0`、`unbootable=yes`，当前槽仍为A；这些表示引导程序不再尝试该槽，不表示存储介质或文件系统已损坏。

Fastboot重刷已验证的readonly镜像后，A槽恢复为 `retry=7`、`unbootable=no`，没有切槽。成功启动后主备GPT只读核验得到剩余6次；重新部署**相同SHA-256的原control镜像**后也正常启动，严格USB SSH与限充控制通过。当前只读预览得到A槽可启动、剩余5次、仍未标记successful。该对照支持拒绝启动由槽位记账耗尽引起，而非control镜像本身或存储硬件损坏；未做全盘介质诊断，不能据此保证所有存储内容无损。

固定 [Qualcomm BootLib定义](https://github.com/SHIFTPHONES/android_bootable_bootloader_edk2/blob/7d391d8b1059ed48259728cab004ca6f16ee3de2/QcomModulePkg/Include/Library/PartitionTableUpdate.h) 中，boot分区属性的bit54是成功位、bits51–53是剩余次数、bit55是unbootable；[槽位选择代码](https://github.com/SHIFTPHONES/android_bootable_bootloader_edk2/blob/7d391d8b1059ed48259728cab004ca6f16ee3de2/QcomModulePkg/Library/BootLib/PartitionTableUpdate.c)在尚未成功时递减次数，成功位为1时不再递减。参考源码不是本机ABL二进制的完整证明，但与Fastboot实际字段及本机GPT一致。现有Debian没有Android启动成功登记服务。

## 写入前完成的准备

工具 [mark-kebab-boot-success.py](../scripts/linux/mark-kebab-boot-success.py) 默认只读预览，仅显式 `--apply` 才写。已执行8项Linux临时文件测试，包含成功位唯一变更、8种重试值保留、幂等、主备CRC、写入/回读失败拒绝、坏CRC、冲突槽位和异常范围拒绝。

本机预览已实读并验证当前charge-policy内核、根分区UUID、boot_a设备映射和control镜像45,637,632字节SHA-256；主备GPT的头与条目CRC、磁盘身份、边界和槽位属性一致。拟变更仅为**每份GPT的boot_a条目中1字节里的bit54由0变1**，以及各自的条目CRC和头CRC。A槽保持active、重试数5不变，B槽完整保留；分区名称、GUID、起止位置和布局不变。无需重刷boot、清空数据或切槽。

原始主/备头和条目块均已只读备份到手机私有目录 `/var/backups/kebab-boot-success/20261004-185850/`，并复制为本机 `local/charge-policy-20261004/boot-success-preview-backup.tar`；四份原始块的字节数及SHA-256在主机复核通过。它们含本机元数据，只留私有忽略目录，不上传。

## 拟执行顺序与恢复方案

1. 获得一次GPT启动成功元数据修复的明确授权。既有boot_a/dtbo_a/super授权不扩大解释为GPT写入。
2. 执行前重新核验镜像与主备GPT，保存新的原始块备份；持有与boot更新共用的维护锁。预览与首次写入之间再次读回原始表，发现变更则拒绝。
3. 先更新备份GPT的条目与头，fsync并逐字节读回、CRC核验；再更新主GPT并同样核验。只设置boot_a成功位和相应CRC。
4. 写入或读回错误时使用刚保存的原始块恢复主备表，重新验证。恢复不能确认时明确禁止重启，保留现场与私有备份；不要使用通用“重建分区表”工具猜测布局。
5. 确认主备successful=true、其他字段与boot内容未改变，再正常重启一次，验证严格SSH、当前A槽、启动成功标记保留、重试数不再递减，以及80/75限充服务自动启动与实际停充读回。

脚本不自动重启、不安装每次开机的GPT写服务。以上是申请授权前的方案，实际执行结果如下。

GPT元数据写入存在中断/块设备故障风险。备份、双副本顺序和回读降低风险，不能承诺异常断电绝对无影响。此前完整boot_a及原厂恢复组继续保留。

## 用户授权后实际执行结果

用户明确回复“允许修复并重启验证”。核对主机测试脚本与手机输入SHA-256一致后，将工具安装到root私有路径，只执行一次 `--apply`。

- 执行时另存新原始表备份 `/var/backups/kebab-boot-success/20261004-190231/`；复制到本机 `local/charge-policy-20261004/boot-success-applied-backup.tar`，四份块的字节数和SHA-256通过。
- 主备成功位和两类CRC的逐字节回读通过，输出 `BOOT_A_SUCCESS_BIT_AND_BOTH_GPT_CRCS_VERIFIED`。再次只读预览successful=true，剩余5次，拟变更字节数0（幂等）；boot_a内容SHA-256仍为原control镜像，A/B槽状态和分区布局保留。
- 经一次systemd正常重启，boot_id改变，仍运行同charge-policy内核，严格USB SSH重新连接。主备GPT仍successful=true、retry=5，证明本次正常启动没有再消耗重试次数。
- 常驻限充服务enabled/active，完成三次实际电量采样后owned=1、REG08=0x06、租约83秒、`inhibit_readback_verified=true`；电量计Not charging/0，无failed unit。

此次修复及重启验收已完成；没有注册每次开机改GPT的自动任务。一次授权不扩展为后续任意GPT修改、切槽或分区操作。原始执行、前后核验和备份证据只存 `local/charge-policy-20261004/`。
