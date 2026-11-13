# 主线内核 Android 分析与自动化实验环境路线（2026-11-10）

> **2026-11-12 执行更新**：本文是历史路线，不把“缺 binderfs 必须换内核”作为当前结论。r4 三个真实传统 Binder 节点已通过 ioctl，实际 Debian Waydroid 1.6.3 有 MAINLINE 传统节点路径；host 音频也已有后续双向证据。Android 启动/agent/Frida 的实测状态以[本轮执行记录](<android-lab-r4-execution-20261112.md>)为准，未验收项目不得由配置推断。2026-11-13已在r4真正启动Android13，完成3个完整启停周期、8次真实UI和Frida3次，见[真启动验收](<android-lab-r4-boot-20261113.md>)；modem旧故障仍单列。

## 0. 目的变更

- **服务器（保留、优先）**：Debian 13 arm64 常驻服务器、远程桌面与网络出口，任何 Android 实验都不得让它降级。
- **Android 实验环境（新增）**：用主线内核（当前 7.2）驱动一个 Android 系统，与 Debian 同机运行，供 Linux 侧 agent 做两件事：
  1. **高权限、多硬件 Android 应用的动态逆向与行为分析**：root、Frida、文件系统直读、网络抓包、硬件（相机/传感器/音频/蓝牙）行为观察。
  2. **自动操控 Android 应用完成任务**：adb + uiautomator 层级/截图/输入注入，或 accessibility/Appium，让 agent 打开、点击、输入、校验并产出报告。
- **形态选择：Waydroid 容器优先**。理由：与 Debian 共享同一内核与网络，agent 可直接访问 /var/lib/waydroid/data、binder、adb、logcat；双启动进 stock Android 会让 Linux 侧 agent 停摆，且不是主线内核。
- **明确不做**：不追求“日常 Android 手机”；**暂不迁 7.3**（7.3 只在出现 Android 实验环境关键修复时再评估）。stock Android 双启动只作为“必须用 vendor 硬件”的后备选项，默认不做。

## 1. 验收定义

| 能力 | 验收标准 |
| --- | --- |
| Android 运行时 | Waydroid 启动，能安装并运行普通 arm64 APK；设置、浏览器、目标 App 可正常打开 |
| 高权限 | waydroid shell 可 root；adb root/su 可用；可 push 并运行 frida-server，能 attach 目标进程 |
| agent 控制 | adb connect 192.168.240.112:5555；input tap/swipe/keyevent/text；uiautomator dump；screencap；am start；pm install；logcat；dumpsys |
| 取证 | host 可读 /var/lib/waydroid/data；host 可抓 waydroid0/容器流量；可 dump 进程内存与文件 |
| 音频 | Android 应用可播放/录音（依赖 host 声卡先打通） |
| 相机 | Android 相机类应用可预览/拍照；先用 Waydroid 的 V4L2 透传（有限 external webcam），libcamera HAL 后置 |
| 传感器 | Android 应用可读 accel/gyro/mag/light/proximity；需自建 HAL 桥接（P1） |
| 服务器优先 | Waydroid 有 CPU/内存限额，按需启停；服务器服务与网络不回归 |

明确不作为门槛：蜂窝通话/短信/移动数据、指纹、DRM/Play Integrity——Waydroid 容器内不可行；GPS 已在 host 侧打通（SDX55 GNSS + kebab-gps.service + GeoClue），Waydroid 侧只需 GNSS HAL 读 host NMEA socket，无需外接模块。

## 2. 现状（2026-11-10 只读实测）

| 项 | 状态 | 缺口/证据 |
| --- | --- | --- |
| 内核 binder | CONFIG_ANDROID_BINDER_IPC=y，/dev/binder 存在 | **CONFIG_ANDROID_BINDERFS 未开**；Waydroid 需要 binderfs，须做 r5 候选内核（仅改配置） |
| PSI/cgroup | CONFIG_PSI=y、cgroup v2 可用 | Android lmkd/资源限额基础具备 |
| ashmem | 7.2 已无 | Waydroid 用 memfd + libashmem，预期兼容，待实测 |
| Debian 版本 | Debian 13.7 (Trixie) | Waydroid 在 **trixie-backports**；当前 backports 未启用，apt 无 waydroid 包 |
| 容器网络工具 | 未装 nft/iptables | Waydroid 依赖 iptables/nft 做 NAT；安装时补齐 |
| 磁盘 | / 仅 846M 空闲，/srv 199G | Waydroid 镜像与数据放 /srv/waydroid，/var/lib/waydroid 用 bind mount |
| 图形 | /dev/dri/card0、renderD128；Phosh/phoc Wayland 会话在跑 | 可跑 Waydroid UI；服务器模式需 headless Wayland（weston/sway headless） |
| 音频 | 无声卡；WCD938x 已绑定，TFA2 模块未加载 | **阻塞 Android 音频**；先做 host 音频（P0） |
| 相机 | imx471 已绑定，CAMSS 14 个 video 节点 | Waydroid 目前用 V4L2 透传作受限 webcam（issue #519）；libcamera Android HAL 仍是 open feature request |
| 传感器 | SSC 62 实例，私有脚本可读 | waydroid-sensors 依赖 host sensorfw（Sailfish），Debian 没有；需自写 HAL/适配层 |
| 蓝牙/NFC | hci0 up；nfc0 存在 | 蓝牙可经 BlueZ 桥接（待验）；NFC HAL 后置 |
| GPS | **host 已适配**：SDX55 GNSS 常驻 kebab-gps.service（1 Hz fix、/run/kebab-gps.json 与 /run/kebab-gps.sock），GeoClue 已接入 | Waydroid 侧只缺 Android GNSS HAL：读 host NMEA socket 转发即可，无需外接模块；A-GPS/XTRA 仍被固件拒绝、冷启动约 2 分钟 |
| 网络 | usb0 192.168.8.0/24、wlan0 192.168.1.0/24 | Waydroid 默认 192.168.240.0/24，不冲突 |
| 7.3 | fork 的 MR !34 仍未合并 | 不迁；仅观察 |

### 2.1 剩余硬件缺口（2026-11-10 只读复核，按新优先级）

**P0（内建，当前迭代；不再“按需”）**

- **音频**：无声卡；WCD938x 已绑定，但 TFA9874 功放未绑定（i2c 15-0034/0035 无驱动），dmesg 报 codec dai not found；TFA2 驱动已编入（CONFIG_SND_SOC_TFA2=m），缺 DTS 绑定、UCM/PipeWire。扬声器/听筒/麦克风均不可用。
- **相机（本轮只做前摄）**：前摄 imx471 驱动已编入（CONFIG_VIDEO_IMX471=m）、DTS 已完整（cci1 0x10、csiphy4 4-lane、MCLK4 19.2MHz）、CAMSS 已编入（CONFIG_VIDEO_QCOM_CAMSS=m，14 节点）；缺 host 抓帧与 libcamera/相机用户空间。后摄（主摄/超广角/微距/黑白，具体型号待确认）无 DTS/驱动，另行排期；闪光灯无 LED class 设备。
- **传感器**：SSC 62 项功能可用，但走私有 SMEM gate + hexagonrpcd，不是上游驱动，也无标准 IIO/输入层消费者；需产品化为稳定 host 接口 + Android sensors HAL。

**P1（稳定性/体验）**

- Wi-Fi RDDM 固件崩溃（ath11k）未闭合，靠 PS-off 规避。
- 触摸幽灵触与长期稳定性未闭合。
- GPS 体验：冷启动约 2 分钟、A-GPS/XTRA 注入被固件拒绝、Wi-Fi/基站融合未做（定位通路已通）。
- 显示只暴露 120Hz，60Hz 未做。
- USB-C 音频/DP alt mode 未适配。
- 蓝牙音频 A2DP/SCO 未验证。
- 热管理/降频策略未验证。

**P2（后续，单独排期）**

- 蜂窝通话/短信/移动数据：SDX55 MHI 已起，但无 ModemManager/RIL/APN。
- 振动：AW8697 无驱动/DT。
- 指纹：Goodix 屏下，无驱动/设备节点/固件。
- NFC 用户空间：内核 nxp-nci/nfc0 已有，无 neard/上层。
- 快充：OnePlus Warp 私有协议未适配。
- 休眠/待机：s2idle 未验证。

> 说明：真正缺“可下载固件 blob”的主要是指纹与部分相机传感器；音频/传感器更多是 DTS、HAL 与用户空间集成。

## 3. 主线策略

- 继续 7.2 社区 fork（贴主线）；**暂不迁 7.3**。
- 只为 Android 实验环境做最小内核改动：新增 r5 候选配置（CONFIG_ANDROID_BINDERFS=y，其余保持 r4），离线构建 → 临时启动验证 → 再决定是否长期保留；现役 r4 不动。
- 纯 torvalds 主线仍只做并行实验，不进入日常构建。
- 上游化默认不做（Nura 禁止 AI 辅助贡献；mainline 政策另议）。

### 3.1 内核构建取舍（回答“r5 是不是大翻新”）

- **r5 不是翻新**：同一源码 commit（88903750a310）、同一 patch 集、同一 LOCALVERSION，唯一实质改动是 CONFIG_ANDROID_BINDERFS=y。
- **r4 配置已满足 Waydroid 其余前置**（已审计 out/.config）：PSI=y、cgroup v2（CGROUPS/CGROUP_DEVICE/CGROUP_FREEZER/CGROUP_CPUACCT/CGROUP_SCHED/MEMCG）、namespaces（USER/PID/NET/UTS/IPC）、BRIDGE/VETH/TUN=m、NETFILTER/NF_NAT/IP_NF_TARGET_MASQUERADE/NETFILTER_XT_MATCH_COMMENT=m、SECCOMP/SECCOMP_FILTER、MEMFD_CREATE、OVERLAY_FS=m；唯一缺口是 BINDERFS。
- **大翻新对两个目标都不划算**：服务器承担回归风险；Android 侧缺的是 HAL/用户空间（音频、相机、传感器），不是内核版本。7.3/mainline 重基也不会自动带来这些硬件。
- **相机/音频/传感器改为内建**（2026-11-10 用户决定，不再“按需”）：作为 P0 固定工作流，与 r5/Waydroid 并行推进；音频/传感器以 DTS + 用户空间/HAL 为主，相机需补后摄驱动/DTS，工作量最大。
- **r5 / r6 分工**：r5 = binderfs（解 Waydroid，保持最小 diff）；r6 = 相机后摄驱动/DTS + 音频 DTS + 传感器标准化（内核侧），紧随 r5，不再等“某个 App 需要”。两者都保留 r4 回退。
- **两轨制**：r4 = 服务器稳定基线（冻结，只接受明确批准的安全/稳定性修复）；r5/r6 = Android 实验环境增量（验证后可长期保留，保留 r4 镜像回退）。

## 4. 实施路线（P0 执行计划见 [P0 计划](android-lab-p0-plan-20261110.md)，待审核）

### P0（当前迭代：底座 + 三项内建）

**P0-A 底座（解 Waydroid）**

1. **r5 候选内核**：只开 CONFIG_ANDROID_BINDERFS=y；构建、临时启动、验证 mount -t binder binder /dev/binderfs 与 Waydroid 初始化；失败可回退 r4。
2. **安装 Waydroid**：启用 trixie-backports，装 waydroid 与 iptables/nft 依赖；镜像/数据放 /srv/waydroid，/var/lib/waydroid bind mount；验证 GPU、网络 NAT、waydroid show-full-ui。
3. **root + Frida + adb 控制链**：waydroid shell/adb root、frida-server arm64、adb 连接；写 agent 工具脚本（uiautomator dump、screencap、input、am/pm、logcat、dumpsys）。
4. **资源隔离**：Waydroid 用 cgroup 限制 CPU/内存，按需启动（systemd unit），服务器服务优先。

**P0-B 内建硬件（与 A 并行，r5 后立即出 r6 候选）**

5. **音频**：TFA2 DTS 绑定 + UCM + PipeWire/PulseAudio；host 扬声器/听筒/麦克风验收；Android 音频 HAL 走 PulseAudio。
6. **传感器**：把 SSC 私有链路产品化为稳定 host 接口（IIO/iio-sensor-proxy 或等价服务）；写 Android sensors HAL（accel/gyro/mag/light/prox）；保留现有门控回退。
7. **相机（前摄优先）**：前摄 imx471 走 CAMSS/V4L2 出图并接入 Android V4L2 透传；**本轮验收只要求前摄**；后摄（主摄/超广角/微距/黑白）确认型号后另行排期，不阻塞前摄；闪光灯随后。

### P1（完整性与自动化）

8. **蓝牙**：BlueZ ↔ Android BT HAL（含 A2DP/SCO 验证）。
9. **GPS Android HAL**：读 host /run/kebab-gps.sock（host 已通）。
10. **USB 设备透传**：vendor.img 权限 XML + persist.waydroid.uevent。
11. **agent 自动化框架**：uiautomator2/Appium 或自研 accessibility 服务；截图 + 视觉/OCR；任务脚本与失败恢复。
12. **稳定性**：Wi-Fi RDDM、触摸幽灵触、热管理。

### P2

13. 蜂窝（容器不可行；如必须，stock Android 双启动 + PC 侧 agent 远程 adb）。
14. 指纹/DRM/Play Integrity（Waydroid 不支持；stock 后备）。
15. NFC 用户空间、振动（需 AW8697 驱动）、快充、休眠。
16. 上游化与可复现打包。

## 5. 与服务器共存

- **不重启进 Android**：agent 常驻 Debian；Waydroid 是容器，随用随起。
- **资源限额**：Waydroid 限制 CPU 配额与内存，避免与 DSH/酒馆/三档 UI 抢资源；低负载时可整体 stop。
- **网络**：容器 NAT 段避开 192.168.8.0/24 与 192.168.1.0/24；抓包点 waydroid0；不把容器桥接进 USB 网段。
- **存储**：/srv/waydroid 存镜像与数据；根分区只放 bind mount 点。
- **安全**：容器 root ≠ host root；不向容器暴露 host 设备/网络；Frida 仅本地 adb；实验 App 视为不可信。

## 6. 里程碑

| 里程碑 | 内容 | 验收 |
| --- | --- | --- |
| M0（2—3 周） | r5 binderfs + Waydroid + root/adb/Frida；音频内建（host 声卡/UCM/PipeWire + Android 音频）；传感器 host 稳定接口 | agent 能装 App/Frida；扬声器/麦克风可用；accel/gyro/mag/light/prox 可读 |
| M1（再 2—3 周） | 前摄 CAMSS/V4L2 出图 + Android V4L2 透传；Android sensors HAL；蓝牙；抓包/取证；后摄另行排期 | 前摄在 host 与相机 App 可预览/拍照；Android SensorManager 有数据；行为分析报告可产出 |
| M2 | agent 在真实 App 上自动完成任务（打开/点击/输入/校验） | 端到端任务成功率与失败恢复可评估 |
| M3 | 按需评估 stock Android 后备或 7.3 | 仅在容器确实无法覆盖时启动 |

## 7. 待确认

1. **后摄排期**：本轮只验收前摄；后摄放到下个迭代，还是等某个目标 App 明确需要再补？
2. **音频验收**：扬声器/听筒/麦克风都算通过，还是先扬声器+麦克风？
3. **传感器验收**：host 标准接口 + Android HAL 都要，还是先 Android HAL？
4. **agent 控制接口偏好**：adb+uiautomator、Appium/UI Automator2、scrcpy、还是自研 accessibility APK？
5. **r5/r6 执行计划**：见 [P0 计划](android-lab-p0-plan-20261110.md)，按停点逐项批准。

## 8. 边界与风险

- binderfs 需要新内核候选；现役 r4 与服务器组不动，失败可回退。
- Waydroid 镜像（LineageOS 系）的许可与来源需记录；不预装 GApps。
- V4L2 相机是“受限 webcam”，无完整 3A；传感器 HAL 需要开发，不能假设开箱即用。
- 容器内高权限只作用于 Android；不要把 host 的 /dev、网络或密钥暴露给容器。
- 服务器优先；任何 Android 实验都不得改变现有网络、充电、三档 UI 与常驻服务。
