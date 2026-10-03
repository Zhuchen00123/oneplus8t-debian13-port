# KB2000 实机只读取证

时间：2026-10-03（Asia/Shanghai）。通过已授权 USB ADB 获取；详细原始记录只存 `local/device-20261003/`，不提交序列号。

## 设备和解锁状态

| 项目 | 实际读取 |
|---|---|
| 型号 | KB2000 |
| ADB device / product | OnePlus8T / OnePlus8T_CH |
| SoC board | kona |
| Android / ColorOS | Android 14 / KB2000_14.0.0.602(CN01) |
| 安全补丁 | 2024-08-05 |
| `ro.boot.flash.locked` | 1 |
| `ro.boot.vbmeta.device_state` | locked |
| `ro.boot.verifiedbootstate` | green |
| `ro.boot.slot_suffix` | _a |
| `sys.oem_unlock_allowed` | 1 |
| ADB UID | 2000(shell)，SELinux shell 域 |
| `command -v su` | 未找到 |

结论：Bootloader 仍锁定。“允许 OEM 解锁”为 1 只表示允许执行解锁，不表示已经解锁。解锁会触发数据清除，需另行授权及先备份个人数据。[Android 官方说明](https://source.android.com/docs/core/architecture/bootloader/locking_unlocking)

## 屏幕判断

**目前不能确认屏幕是否原厂，也不能确认是否换过屏。**

SurfaceFlinger 与 DisplayManager 都报告 1080×2400，60 Hz 和 120 Hz 两个模式，采样时 activeMode=120 Hz。系统也报告广色域/HDR。这些符合 OnePlus 8T 的公开规格，但来自驱动与显示服务，兼容替换屏也可能报告相同能力。[原机规格](https://www.oneplus.com/us/8t/specs)

HWC 的显示标识为 `manufacturerPnpId=QCM`、空名称、年份 2006。它不包含真实面板型号或硬件序列号，不能作为三星原厂屏证明。

以下读取均返回 Permission denied：

```text
/proc/devinfo/lcd
/proc/devinfo/Screen
/proc/devinfo/tp
/sys/kernel/oplus_display/panel_id
/sys/kernel/oplus_display/panel_serial_number
/sys/class/drm/card0-DSI-1/status
/sys/class/drm/card0-DSI-1/modes
/sys/firmware/devicetree/base/soc/qcom,dsi-display-primary
/proc/cmdline
```

显示 HAL 的 `IDisplayPanelFeature` 无法由 shell 获取，最近日志也没有可用面板型号；`dumpsys display.smomoservice` 未提供信息。没有修改 sysfs、SELinux、面板寄存器、显示设置、Bootloader 或分区。

原厂 Android 14 的 OnePlusOSS 设备树将 20828 面板描述为 Samsung AMB655X；社区主线驱动也针对它。这是预期配置，**并非本机的实际读取结果**。[固定厂商 DTS](https://github.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8250/blob/0a301570ef70f6f9bfe1840451c9d41f5ddce6b8/vendor/qcom/proprietary/devicetree-4.19/20828/dsi-panel-oplus20828-samsung-amb655x-1080-2400-120fps.dtsi)

如果需要可信的原厂鉴定，下一步是官方售后检查硬件标签、序列号及维修记录。以后在已经合法取得 root/恢复环境且无需为此清除数据时，也可进一步读取面板 ID/序列号；即使 ID 匹配，也不能排除换装同型号原厂屏。

## 分区取证与备份限制

`/dev/block/by-name/` 可枚举，但读取 `boot_a` 被拒绝。本次没有实际原厂分区镜像；权限拒绝不能视为备份成功。

本机关键映射：

| 名称 | 实际目标 |
|---|---|
| boot_a / boot_b | /dev/block/sde11 / /dev/block/sde35 |
| dtbo_a / dtbo_b | /dev/block/sde17 / /dev/block/sde41 |
| vbmeta_a / vbmeta_b | /dev/block/sde16 / /dev/block/sde40 |
| recovery_a / recovery_b | /dev/block/sda16 / /dev/block/sda17 |
| super | /dev/block/sda15 |
| persist / persist_bkp | /dev/block/sda2 / /dev/block/sda13 |
| modem_a / modem_b | /dev/block/sde4 / /dev/block/sde28 |
| modemst1 / modemst2 | /dev/block/sdf2 / /dev/block/sdf3 |
| fsg / fsc | /dev/block/sdf4 / /dev/block/sdf5 |
| mdm1m9kefs1 / 2 / 3 / c | /dev/block/sdf7 / sdf8 / sdf6 / sdf9 |
| mdm_oem_dycnvbk / mdm_oem_stanvbk | /dev/block/sda7 / /dev/block/sda8 |

尤其不能照抄社区 deviceinfo 的 `/dev/sda14`：本机 super 的编号不同，后续始终解析 by-name。
