# 社区与主线支持调查

调查日期：2026-10-03。来源版本见 `sources.lock.json`。本文的“工作”是对应维护者的报告，不代表本机 KB2000 已验证。

## 结论与基线

OnePlus 8T 已有可复用的社区主线移植，当前实际选择是 **SM8250 7.2.0 社区分支 + 固定的 kebab bring-up 补丁集 + Debian 13 用户空间**。

Linux 官方主线最新 RC 为 7.3-rc5；查验 master `e767a4ea70a3992c37ed604157d32f0dfbf9b1e3` 的 qcom DTS 目录，尚无 `sm8250-oneplus-kebab.dts`。[官方目录](https://github.com/torvalds/linux/tree/e767a4ea70a3992c37ed604157d32f0dfbf9b1e3/arch/arm64/boot/dts/qcom)

社区 SM8250 仓库发布的最高版本标签为 `sm8250-7.2.0`，提交 `71f4068cb254730bcf334cde9bdd6ca183e4fcd0`；由实际 `git ls-remote` 查验。该分支包含 kebab 的设备描述，属于贴近主线的社区 fork。[SM8250 仓库](https://gitlab.postmarketos.org/soc/qualcomm-sm8250/linux/-/tree/sm8250-7.2.0)

最新 kebab 实验仓库在 2026-10-02 仍有更新。本项目固定 `2e5e065da8ea8d242ecc34bfe064b88257797aa9`，包含 21 个常规内核补丁、3 个可选补丁、整份设备树与用户空间配置。其硬件验证对象为 KB2003。[固定版本](https://github.com/jfbobier/Oneplus-8T-kebab-pmos-bringup/tree/2e5e065da8ea8d242ecc34bfe064b88257797aa9)

## 三种路线

| 路线 | 查验结果 | 对本项目的作用 |
|---|---|---|
| Linux 官方主线 7.3-rc5 | 有 SM8250 基础，缺少 kebab 完整 DTS | 对照新变化、后续升级实验 |
| SM8250 7.2.0 + kebab 实验补丁 | 有较完整硬件支持，需设备固件与用户空间 | 当前构建与移植基线 |
| Armbian oneplus-kebab | 支持 trixie；SM8250 current=6.18、edge=6.19 | Debian 集成、initramfs、固件和槽位策略参考 |

Armbian 的 board 配置明确包含 `trixie`，kernel family 配置仍指定 6.18/6.19。这不符合用户尽量采用新内核的首选要求，因此参考其 Debian 集成方式，采用 7.2 社区内核进行构建验证。[board 配置](https://github.com/armbian/build/blob/b3b1f3e5134f807342d452956b2c7b8afdfff34f/config/boards/oneplus-kebab.conf)，[内核版本配置](https://github.com/armbian/build/blob/b3b1f3e5134f807342d452956b2c7b8afdfff34f/config/sources/families/sm8250.conf)

## 硬件支持与限制

以下集中概括固定实验仓库 README 的报告：显示、触摸、GPU、Wi-Fi、蓝牙、NFC、双扬声器、数字麦克风、USB OTG、电量、DP 视频/音频、前摄可用；SDX55 能启动并识别 SIM/PIN。蜂窝数据和 GPS 尚未打通，后摄与通话音频路由未完成。充电没有内核控制，Venus 硬解码会触发固件故障和重启，应关闭。显示存在时钟警告，DP 首次插入可能需要重新探测。以上均需在 KB2000 复测。[维护者硬件状态](https://github.com/jfbobier/Oneplus-8T-kebab-pmos-bringup/blob/2e5e065da8ea8d242ecc34bfe064b88257797aa9/README.md)

较早的 Armbian 维护者在 2025-07-31 只确认显示、触摸、Wi-Fi、蓝牙，明确表示蜂窝和相机不可用。该记录说明基础移植较早已存在，不能覆盖 2026 年实验分支的新进展。[维护者回复](https://forum.armbian.com/topic/54193-anyone-using-oneplus-8t/?do=findComment&comment=223250)

设备 Wiki 的旧 postmarketOS URL 目前重定向至 Nura Wiki。页面同时有旧 4.19 字段、主线包链接和 2026-09-22 的新进度，信息粒度不同；版本选择以仓库标签与代码为准。[设备页及重定向目标](https://wiki.nura.eco/wiki/OnePlus_8T_(oneplus-kebab))

## 补丁与集成注意事项

- 补丁、DTS、kernel config 和用户空间应一起固定。完整 DTS 覆盖可能遮蔽新版本的上游修正，升级时需要结构比较。
- 实验仓库的 APKBUILD 使用 Alpine/postmarketOS 7.2.0；本项目复用其源码基础，另做 Debian 内核配置、模块打包、initramfs 与用户空间集成。
- 社区 `kebab-build.sh` 默认刷机；`--no-flash` 仍通过 SSH 部署 APK/模块。不要用它进行“仅构建”任务。
- Kernel Image、DTB 与 modules 必须来自同一构建。只换 boot.img 会留下旧模块，造成网络和音频的假回归。
- 固件从自己的 vendor/modem 分区提取；无线/GPU 中可发行的部分可使用发行版 linux-firmware 包。Android 厂商树用于参数参考，不用来冒充主线。
- 7.3 实验要先审查社区分支在 vanilla 主线以外的全部基础改动；只移植这 21 个补丁到 vanilla 7.3 并不足以保证支持完整。

## 访问和证据边界

postmarketOS MR !6411 可读，2025-04-14 已提出改用主线并链接触摸/显示工作；MR !6867 正文被 Anubis 阻挡，搜索摘要提示启动回归，但未作为确定事实采用。[!6411](https://gitlab.postmarketos.org/postmarketOS/pmaports/-/merge_requests/6411)，[!6867](https://gitlab.postmarketos.org/postmarketOS/pmaports/-/merge_requests/6867)

本次未验证发行版仓库当前发布包、社区镜像或实际固件的安全性。没有自动提交上游或对维护者发消息。
