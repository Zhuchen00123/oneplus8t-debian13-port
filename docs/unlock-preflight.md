# KB2000 解锁准备与屏幕取证

日期：2026-10-03。用户指令：先解开 Bootloader，再尝试验证屏幕；主机已具备 gh。

## 已核验与已保存

- KB2000，ColorOS `KB2000_14.0.0.602(CN01)`，当前槽位 `_a`；ADB 授权有效。
- `ro.boot.flash.locked=1`、vbmeta locked、verified boot green；`sys.oem_unlock_allowed=1` 仅表示允许解锁。
- 已重启至 Fastboot，并只读核验：`product=kona`、`current-slot=a`、`unlocked=no`、`secure=yes`、`get_unlock_ability=1`；与此前 KB2000 ADB 取证一致。
- 本地 `backups/phone-shared-preunlock-20261003-140933/` 已保存 250 个可读取文件，共 13,499,706 字节，清单记录每个文件的主机端 SHA-256。首次全目录复制失败后，19 项逐项补充复制均成功。
- `Android/data`、`Android/obb`、应用私有数据、账号和原厂分区不属于已完成备份。共享文件副本不能作为完整手机恢复保障。同盘副本未异地保存。
- ADB shell 无 root；原厂 boot 分区读权限被拒绝。此前的项目、源码、构建产物备份不包含手机原厂镜像。

## 操作范围与恢复边界

1. 使用指定序列号的 ADB 重启至 bootloader；用 fastboot 只读核验产品、槽位和锁定状态。
2. 用户接受恢复出厂的数据丢失后，执行指定序列号的 `fastboot oem unlock`。手机若显示确认界面，需由用户按键确认。
3. 解锁可能自动恢复出厂并重启。用户完成初始设置、重新开启 USB 调试并授权电脑后，重新读取锁定属性与屏幕节点。
4. 本次不刷分区、不切换槽位、不使用 unlock_critical、不重新锁定。若取消，使用 `fastboot reboot` 返回现有 ColorOS。
5. 解锁后的恢复目标为现有 ColorOS 出厂状态；个人共享文件可从本地副本复制回去，未备份的应用数据无法恢复。没有已核验的完整原厂救砖包或原厂分区镜像，不承诺完整救砖能力。

## 屏幕判断限制

Bootloader 解锁不会自动赋予 ADB root，也不会自动放开 SELinux。普通 ADB 若仍无法读取面板 ID/序列号，应如实记录；取得额外权限或使用 recovery 需另行准备并审核。面板型号匹配只能支持硬件兼容性判断，不能证明从未换屏。

## 来源与当前状态

[LineageOS 的 KB2000/KB2001/KB2003/KB2005 安装指南](https://wiki.lineageos.org/devices/kebab/install/variant1) 明确使用 `fastboot oem unlock`，并说明清除数据及手机确认、重新开启 USB 调试。仅采用解锁章节，本项目不执行其 LineageOS recovery/DTBO/vbmeta 安装步骤。

用户已确认："解锁呗，没有重要数据"，接受解锁清除数据。

已在重新核验产品 `kona`、`unlocked=no` 后执行指定序列号的 `fastboot oem unlock`，返回 `OKAY`，退出码 0。紧随命令的只读查询仍为 `unlocked=no`，ADB 未列出设备；此时尚在等待手机确认，命令接收成功不能单独作为解锁完成证据。

用户随后报告："手机正在清空或启动"。ADB 恢复连接后，`Get-DeviceInfo.ps1` 实读：`flash.locked=0`、`vbmeta.device_state=unlocked`、`verifiedbootstate=orange`、slot `_a`，ColorOS 构建不变。**解锁成功已验证。**

ADB 仍为 UID 2000，8 个面板/显示/命令行节点仍 Permission denied，实际面板型号及序列号没有读出。SurfaceFlinger 仍报告 1080×2400、60/120 Hz、QCM，不能据此证明原厂屏。补充节点读取遇到 USB 连接中断，不能当作权限或硬件结论。

解锁阶段结束时原厂分区备份仍未完成，尚未执行 root、刷 recovery、关闭 SELinux、刷写镜像、切槽或重新锁定；当时屏幕身份仍未确认。

用户重新开启调试后连接恢复，已补齐此前中断的检查。最新证据 `local/device-20261003-143138-003/` 再次确认 unlocked/orange、UID 2000；原 8 个节点和补充 `/proc/devinfo/display` 均拒绝读取，其他 7 个旧路径不存在。普通 ADB 下此轮屏幕检查完成，仍不能确认原厂身份。继续读取受限面板信息需要另外准备带权限的环境；现有授权不包含 root 或刷 recovery。

用户随后要求继续提供权限，已按 [临时 root 流程](temporary-root-plan.md) 在本机修补匹配的原厂 boot 并临时启动、取得 Shell 授权。后续 [屏幕实测](panel-evidence.md) 支持原厂规格的三星面板；26 个指定原厂分区及本机固件已备份、校验。最终重启退出临时 root，Bootloader 保持 unlocked；先前的“未取得权限/未备份”是解锁阶段历史记录。
