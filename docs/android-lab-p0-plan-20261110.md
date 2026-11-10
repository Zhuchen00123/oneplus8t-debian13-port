# Android 实验环境 P0 执行计划（2026-11-10，待审核）

状态：方向已获批准；**本文件只是计划，尚未执行任何构建、安装、刷写或设备变更**。审核通过后再按停点逐步执行。

## 0. 目标与边界

- 目标：在现役 r4（7.2.0-kebab-debian13-r4-venus+）之外，做出一个可用的 Android 实验环境：
  - Waydroid 容器与 Debian 同机同内核；
  - root、Frida、adb/uiautomator 控制链；
  - 尽可能多的硬件对 Android 可见；
  - 服务器始终优先，不因 Android 实验降级。
- 非目标：日常 Android 手机；蜂窝通话/短信/移动数据；指纹；DRM/Play Integrity；迁移 7.3。
- 原则：现役 r4 与服务器组不动；先离线构建，再临时启动；每一步有验证与回退；每个“需批准”停点等用户确认。

## 1. 硬件可见性矩阵（“尽可能多”）

| 硬件 | 容器内可行性 | 计划路径 | 优先级/工作量 |
| --- | --- | --- | --- |
| 显示/触摸/GPU | 原生支持 | Waydroid 窗口 + freedreno/Mesa/minigbm | P0，低 |
| 音频 | 可支持 | host 声卡先通（TFA2+UCM+PipeWire），Waydroid 走 PulseAudio | P0，中 |
| 网络 | 原生支持 | Waydroid NAT（192.168.240.0/24），避开 USB/Wi-Fi 网段 | P0，低 |
| 输入/键盘 | 原生支持 | Waydroid 输入/软键盘 | P0，低 |
| 相机 | 可支持（受限） | Waydroid V4L2 透传（external webcam），CAMSS+imx471；libcamera HAL 后置 | P1，中 |
| 传感器 | 可支持（需开发） | 自写 HIDL/AIDL sensors HAL，后端读我们的 SSC（accel/gyro/mag/light/prox） | P1，高 |
| 蓝牙 | 可支持（需验证） | host BlueZ/hci0 与 Android BT HAL 桥接 | P1，中高 |
| GPS | 可支持（需外接源） | USB/BT NMEA GPS → host gpsd → Android GNSS HAL；手机内置 GNSS 在 SDX55 内、主线未启用 | P1，高 |
| USB 设备 | 可支持（需改 vendor 镜像） | vendor.img 权限 XML + persist.waydroid.uevent；USB host/设备透传 | P1，高 |
| NFC | 困难 | host nfc0 + Android NCI HAL；或放弃 | P2，很高 |
| 电池/温度 | 可桥接 | host power_supply → Android Battery HAL | P2，中 |
| 振动 | 缺驱动 | 先做 AW8697 驱动，再做 vibrator HAL | P2，很高 |
| 蜂窝/通话/SMS | 不可行 | 无 RIL、无 ModemManager 路径；stock Android 后备才有 | 不做 |
| 指纹 | 不可行 | 主线无驱动 | 不做 |
| DRM/Play Integrity | 不可行 | Waydroid 无 TEE/证书链 | 不做 |

## 2. P0 分步计划

### S0 准备与基线（只读）

- 记录：uname、boot_id、r4 build-id/config SHA、boot_a/dtbo SHA、服务 PID、磁盘余量。
- 定位 r4 构建树（WSL 内按 kernelrelease 找）；若不存在，按 sources.lock + 0001–0009 重建 r4 基线并以 build-id/config SHA 校验。
- 工具检查：host adb/fastboot；手机侧 weston、pipewire、iptables/nft 是否可装。
- 产出：baseline-r4.json；不改变设备。

### S1 r5 内核候选（离线构建，不上机）

- 新增配置片段 config/debian13-r5-binderfs.config，唯一实质改动：CONFIG_ANDROID_BINDERFS=y。
- 复用 r4 构建树：merge_config → make olddefconfig → 构建 Image/dtbs/modules；若基线需重建，按 r4 既有链路（基线 83d0c460 + 0008/0009 + bringup 21 patch）重建，并校验 r4 build-id/config SHA。
- 校验：out/.config 有 BINDERFS=y 且与 r4 config 的 diff 只有该项；模块 vermagic/数量；boot 镜像 SHA。
- 产出：r5 候选 Image/dtb/modules/boot 镜像 + SHA；不改变设备。
- 回退：删除候选树；r4 不动。

### S2 临时启动验证（需批准；fastboot boot，不刷分区）

- fastboot boot r5 候选（仅内存启动）；USB 网络起后 SSH。
- 验证：uname 为 r5；mount -t binder binder /dev/binderfs 成功；/dev/binderfs 可读写；waydroid init 不再报 binderfs 缺失。
- 完成后正常 reboot 回 r4，核对 boot_id 与服务。
- 风险：启动挂起；回退：断电/正常重启即回 r4（未写分区）。
- 停点：执行前需明确“开始 S2”。

### S3 安装 Waydroid（需批准；设备写）

- 启用 trixie-backports（仅新增源）；apt update；apt install -t trixie-backports waydroid iptables（或 nftables）。
- 存储：/srv/waydroid 存镜像与数据；/var/lib/waydroid 做 bind mount（根分区仅 846M）。
- 初始化：waydroid init（下载系统/厂商镜像，约 1–2GB）。
- 显示：优先在现有 Phosh 会话内跑；服务器模式用 headless weston（1080×2400）。
- 启动：waydroid session start / container start；验证 waydroid status、adb devices。
- 验证：装一个测试 APK；screencap 取图；uiautomator dump 出层级。
- 风险：镜像下载、依赖、网络；回退：stop 服务、卸载包、保留或删除 /srv/waydroid。
- 注意：只用 -t trixie-backports 安装，不整体升级。

### S4 root / Frida / agent 控制链（需批准）

- waydroid shell（root）与 adb root 验证；push frida-server arm64（版本与 host frida-tools 匹配）；attach 测试。
- 新增 scripts/android/：agent-adb、ui-dump、screencap、tap/swipe/type、launch/stop、frida-attach；统一输出 JSON 到 /srv/android-lab/。
- 验证：对演示 App 完成“打开→点击→输入→截图→校验”闭环。

### S5 host 音频（需批准；可单独做）

- modprobe snd_soc_tfa2；检查 /proc/asound/cards 与 i2c 15-0034/0035 绑定。
- 安装 pipewire/wireplumber（或 PulseAudio）；UCM/ALSA 直测；低音量短测扬声器与麦克风。
- 再验 Android 音频（Waydroid 用 host PulseAudio/PipeWire）。
- 风险：TFA2 为 DSP-bypass，无热保护；先小音量、短时。回退：rmmod。

### S6 验收与停点

- 记录：版本、镜像 SHA、Waydroid 镜像来源、adb/Frida 版本、测试 App、截图/层级/日志。
- 通过后停在 P0；P1（相机/传感器/蓝牙/GPS/USB）另立计划与停点。

## 3. GPS 专项

> **2026-11-10 晚更新**：SDX55 GNSS 已在主线 7.2 上实测打通（室外稳定 1 Hz fix，HDOP 1.5），常驻 `kebab-gps.service` + GeoClue 已接入。下文“没有 GNSS 设备 / D 极高不建议”是当日早先的评估，已被推翻；最新结果见 [SDX55 GNSS 可行性文档](sdx55-gnss-feasibility-20261110.md) §21。

### 事实

- Waydroid 镜像默认没有 GNSS HAL，Android 的 GPS provider 不会出数；但 Waydroid 的架构允许把 GNSS HAL 放进容器（社区已有 gpsd 版 Android GNSS 1.1 HAL 的先例；该实现已停更，需移植或重写最小 HAL）。
- 本机主线 7.2 **没有** GNSS 设备：OnePlus 8T 的 GNSS 接收机在 SDX55 调制解调器内；主线未启用 SDX55（PCIe 端点无驱动、无固件/MHI），所以即使 HAL 完美，也没有手机自带 GPS 源。**（2026-11-10 晚已推翻：SDX55 已由主线驱动，GNSS 实测出 fix；见 §21。）**
- 因此“GPS 必要”意味着需要额外定位源，而不是打开 Waydroid 某个开关。

### 可选路径

| 方案 | 做法 | 精度/真实性 | 工作量 | 适用 |
| --- | --- | --- | --- | --- |
| A 网络定位 | microG/UnifiedNlp + Wi-Fi 地理库（BeaconDB 等） | 粗（百米级），非 GPS | 中（装 microG/配后端） | 只要 App 能拿到 location |
| B Mock 定位 | 开发者选项 + mock app，agent 注入坐标/轨迹 | 假，App 可检测 mock | 低 | 自动化测试、可复现轨迹 |
| C 外接 GPS | USB/BT NMEA 接收机 → host gpsd → Android GNSS HAL | 真实 GPS | 高（硬件 + HAL 移植） | 需要真实定位 |
| D 主线启用 SDX55 GNSS | 固件 + MHI/QMI LOC | 真实，用手机自带 | **已实测打通（2026-11-10 晚）** | 常驻 `kebab-gps.service` + GeoClue 已部署 |

建议：**D 已打通，优先用手机自带 GNSS**（`/run/kebab-gps.json` + GeoClue）；C 可作为备份/对比（一个 USB/蓝牙 GPS 模块 + gpsd + HAL）；A 作为过渡，B 用于自动化确定性。

## 4. 服务器优先与安全

- Waydroid 按需启动（systemd unit 默认 disabled），cgroup 限制 CPU/内存；实验结束 stop。
- 不重启进 Android；agent 常驻 Debian。
- 容器 NAT 不接入 USB/Wi-Fi 网段；抓包只在 waydroid0。
- 容器 root ≠ host root；不暴露 host /dev、密钥、网络；实验 App 视为不可信。
- 所有设备写入前记录回退命令与 SHA。

## 5. 审批清单（执行前逐项确认）

- [ ] S1 r5 离线构建（不改设备）
- [ ] S2 fastboot boot 临时启动 r5（不刷分区）
- [ ] S3 启用 backports 并安装 Waydroid（含 1–2GB 镜像下载）
- [ ] S4 安装 Frida/agent 控制脚本
- [ ] S5 加载 TFA2、安装 PipeWire（可单独批准）
- [ ] GPS 方案：外接 GPS / 网络定位 / mock（三选一或组合）
- [ ] P1 硬件顺序：相机、传感器、蓝牙、GPS、USB、NFC

## 6. 产出与时间

- 预计 M0 1–2 周（不含 P1）。
- 产出：r5 候选镜像与 SHA、Waydroid 可用环境、agent 控制脚本、验收报告、回退记录。
- 本计划不改变现役 r4；任何“长期保留”都要另一次批准。
