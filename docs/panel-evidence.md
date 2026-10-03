# KB2000 屏幕实测结论

日期：2026-10-03。在匹配当前 ColorOS 的临时 Magisk 环境中，用户允许 Shell 后实测 `uid=0(root)`、SELinux `Enforcing`。读取来自本机，不使用其他手机的序列号或维修记录。

## 已取得证据

| 项目 | 实际结果 |
|---|---|
| `/proc/devinfo/lcd` | Device version `AMB655X`，manufacture `samsung1024` |
| `/proc/devinfo/tp` | `SY_SAMSUNG`，版本 `0xEE01301D`，固件路径 `tp/19805/FW_S3908_SAMSUNG.img` |
| 面板 DCS ID | `A5 05 01` |
| 面板序列号 | 已成功读取；完整值只在忽略的本地原始证据中保存 |
| 按驱动规则解码的生产日期 | `2020-10-08` |
| DRM 状态与模式 | connected，1080×2400，60/120 Hz |

实际 ID 读取需要面板处于开启状态。最初熄屏时 panel_id 返回空内容；唤醒后返回上述三个字节。空内容不能当作 ID=0 或硬件缺失。取得 root 后 `/proc/devinfo/Screen`、`/proc/devinfo/display` 确认为不存在；此前普通 shell 的拒绝不能据此判断是否存在。

## 来源和解释

[OnePlusOSS 固定厂商驱动](https://github.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8250/blob/0a301570ef70f6f9bfe1840451c9d41f5ddce6b8/kernel/msm-4.19/techpack/display/oplus/oplus_display_private_api.c#L868) 的 panel_id 读取路径分别请求 DCS `0xDA/0xDB/0xDC`，并要求面板开启。因此该结果比仅查看配置分辨率提供了更多硬件响应证据。

[同一驱动的序列号读取与编码](https://github.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8250/blob/0a301570ef70f6f9bfe1840451c9d41f5ddce6b8/kernel/msm-4.19/techpack/display/oplus/oplus_display_private_api.c#L701) 将 AMB655X 的日期字段从寄存器响应取出，按年、月、日、时、分、秒及保留字节排列。驱动示例和注释确定年份偏移为 2011；本机年月日字段解码为 2020-10-08。这是按公开厂商规则得到的解码结果，不是独立的售后认证。

公开实现还存在解码边界限制：普通分支请求 17 字节，而 AMB655X 从索引 11 开始，第二个保留字段取索引 17，超出本次请求的范围。日期字段仍位于有效范围内；末尾保留字段不能作为可信认证依据，实际量产二进制是否另有修正未核实。因此不把完整 64 位返回值当作经过验证的唯一硬件身份。

**结论：本机报告的三星 AMB655X 型号、实际 DCS 响应和可读取序列号，与 OnePlus 8T 的原厂三星面板配置一致，证据支持“原厂规格的三星面板”。软件取证不能证明手机从未换屏，也不能排除同型号原厂备件或能够模拟响应的替代件。** 全新设备标签、维修记录和售后核验才能补充装配/更换历史。

原始证据入口由 `local/current-panel-root-evidence.txt` 指向，包含普通 root 读取、面板唤醒后的读取和脱敏判断。序列号、实际硬件原始记录只保存在本地，不提交或上传。
