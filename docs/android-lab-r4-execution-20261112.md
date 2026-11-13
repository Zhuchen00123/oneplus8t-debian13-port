# r4 最小 Android 实验环境执行记录（2026-11-12 UTC，历史受阻阶段）

> **当前状态已更新**：用户追加继续授权后，r4上Android13、鉴权ADB、3个完整启停周期、8次真实UI及3次Frida已实测通过。见[2026-11-13真启动报告](<android-lab-r4-boot-20261113.md>)。下方0次/未验收是前一阶段快照，原始事故记录保留，不作为当前结果。

> **本轮未通过验收。** 已安装最小环境、双端核验并初始化镜像，实际尝试3次启动；前2次 LXC 挂载失败，第3次 `/init` 被执行后约20ms退出，未到 Android 框架。随后宿主发生非预期整机复位，已保全证据并停止新增 Android 尝试。没有主动 reboot/fastboot/刷写/换核。不能声称 agent、Frida 或服务器不受影响通过。

## 优先结论

| 问题 | 本轮实际结果 |
|---|---|
| r4 是否启动了 Android？ | **未完成框架启动**。LXC 第3次到 RUNNING、exec `/init` 后立即退出，不等于 Android 启动。尚不能证明 r4 缺能力，更不能据此要求 binderfs/r5。 |
| agent 是否真实操作并验证？ | **没有**。APK已构建、签名验过；真实UI轮数0，XML/状态定位离线测试13项过（10定位+3新旧boot状态）。 |
| Frida 是否实际观测调用？ | **没有**。匹配16.7.19客户端在宿主 import成功，server已备好；没有注入进程，未生成伪造观测结果。 |
| 服务器是否全程正常？ | **不通过**：发生非预期复位。SSH/DSH/酒馆/限充恢复，modem原开机链QMI等70秒失败。QQbot早期因条件跳过，17:30Z补查已自行恢复active（本会话未启动它）。GNSS服务active不能证明实际出星。 |
| 下一步最小必要变更？ | 先单独恢复/核验保护服务基线并评审复位风险；再在维护窗口做一次有早期init/退出状态采集的容器诊断。**无证据需要换r5**。恢复modem/QQbot、任何手机重启/内核试机都未在本轮擅自执行。 |

可复现入口见[安装、启动、停止与回退说明](<../scripts/android-lab/README.md>)；[版本与校验清单](<../config/android-lab-r4-versions-20261112.json>)已固定包、两份镜像及工具/APK。

## 本轮边界与 Git

- 当前内核实读 `7.2.0-kebab-debian13-r4-venus+`；禁止重启、刷写、切槽、DTBO/GPT、r5 安装或切换。
- 新分支 `codex/waydroid-r4-lab-20261113`，起点 `e3e85933f167bb43146b3f001ae7de8a8f9e4c68`；不 commit/push。原有 27 个已跟踪修改完整保留。
- 已创建并验证 Git bundle、两个差异补丁、27 个修改文件 ZIP 逐 SHA 验证。未跟踪文件未删除/覆盖，状态已记录；不是整个未跟踪工作区的完整副本。备份只在被忽略的本地 backups 目录，需用户另存外部介质。
- 设备原始基线留在 `/srv/waydroid-r4-lab-20261112/before.json`，不上传原始进程参数、MAC/IPv6 设备标识、GPS 坐标、音像或凭据。

## 先读材料的分类与修正

| 事项 | 证据等级与结论 |
|---|---|
| r4 传统 Binder | **本轮实测**：三个真实字符设备，major 10 / minor 258、259、260，root:root 0600；逐节点 open + BINDER_VERSION ioctl 返回协议 8，扫描 `/proc/*/fd` 无既有占用。没有 mknod、没有加载 Binder 模块。 |
| Binder 配置 | **运行内核配置实读**：`ANDROID_BINDER_IPC=y`，`ANDROID_BINDER_DEVICES="binder,hwbinder,vndbinder"`，`ANDROID_BINDERFS` 未开。namespace/cgroup v2、memfd、PSI、seccomp 配置具备；不等于 Android 运行通过。 |
| 缺 binderfs 就必须换内核 | [旧路线图](<android-lab-roadmap-20261110.md>)该断言过时。本轮审计实际 Debian Waydroid 1.6.3 的 `drivers.py`：MAINLINE 允许传统三个节点，均存在时不尝试创建 binderfs。**不能因此提前宣布 Android 兼容通过**。 |
| r5 | [r5 首启报告](<r5-first-boot-20261112.md>)及其 trial-result 已实证一次临时启动/返回；不是当前内核，也不是 Android 实测。本轮不复用该启动授权。 |
| host 音频 | [第五轮记录](<audio-agent-execution-round5-20261112.md>)已有 ALSA/PipeWire 双向声学证据，覆盖旧“无声卡/无音频”的快照；stage-1 DSP-bypass，`unattended_ready=fail` 仍成立。本轮不加载 TFA2，不透传音频 socket。 |
| 相机、传感器、GNSS | host 历史能力与 Android HAL 是不同层次，本轮全部不接入。 |
| GPU | **本轮实测**：现有 GBM/EGL shader 探针，FD650 / freedreno / Mesa 25.0.7，三色各 64 像素全匹配，GPU 时间/周期递增。独立 Weston headless 14.0.2 使用 renderD128、GL renderer FD650；不是 llvmpipe。Android 自身图形尚未验。 |

## 基线与安装实绩

- 内存总 12,177,973,248 B，可用 10,608,537,600 B，无 swap；根 ext4 仅约 566 MiB 空余，`/srv` ext4 可用 212,439,351,296 B（约 198 GiB）。镜像、下载、临时 apt 索引、Frida 和 Android 数据均放 `/srv`。
- 核心 DSH、SillyTavern、QQBot、限充、GNSS、SSC、modem、Venus 为原服务；基线 `kebab-cnb-domain.service` 已 failed。`dpkg --audit` 原有两个未配置的 -dev 包（libgudev/libpolkit），未冒充本轮引入，也没有顺带执行 `dpkg --configure -a`。
- 限充：97%，Not charging，0 µA，37.8–37.9°C；owned=1 / REG08=6 / last_error=0 / `inhibit_readback_verified=true`。未写限充、温控或 modem/GNSS 配置。
- 全局 IPv4 forwarding=0。默认 IPv4 USB metric 100 / Wi-Fi metric 600；原 IPv6 保持。默认 DNS 查询超时，绑定 wlan0 + TLS 域名校验 + 固定 Debian CDN IP 的下载成功；没有修改宿主 DNS/默认路由。
- 原有图形会话保留不变；新建无物理显示输出的 headless Weston，避免替换桌面或显示管理器。没有把历史桌面状态当 Android 图形通过。
- 安装了 32 个新包、0 升级/0 删除，增加根目录约 37.1 MiB；包括 Waydroid `1.6.3+ds-2~bpo13+1`、LXC `1:6.0.4-4+deb13u4`、ADB `1:34.0.5-12`、Weston `14.0.2-1`、dnsmasq-base。
- 源是 Debian trixie / backports / security 的**签名 InRelease**，apt 使用系统原生验证器验证本轮独立 file: 仓库，Packages SHA256 → deb SHA256 全部验证。不改 `/etc/apt`。临时 solver status 仅用于计划，排除两件既有损坏 -dev 的依赖；实际 dpkg 数据库未伪造/清除。
- 安装前完整提取 maintainer scripts：Waydroid 与 LXC 默认会 enable/start，故先对五个**新增**单位加 mask，再用仅拒绝这些新增服务的临时 policy-rc.d，结束删除该精确自建文件。四个具体服务实读 masked/inactive；模板 `lxc@.service` 的 is-active 无有效实例不作为失败依据。原包全安装成功，原两个 -dev 问题保持。

## 隔离实现（仍需 Android 实机验证）

- [实验适配入口](<../scripts/android-lab/waydroid_lab_launcher.py>)只在自己的进程内适配已审计的 1.6.3，不改 dpkg 包源文件。传统 Binder、render 节点和 DMA heap 精确白名单；不映射宿主根、整个 /dev、video、物理音频、debugfs、modem、输入设备或凭据目录。
- LXC cgroup2 设备白名单已生成并由LXC执行；父实验单元配置 MemoryMax=3G / MemoryHigh=2500M / CPUQuota=200% / TasksMax=2048，手动临时单元1800秒上限，无自启。Android进程立即退出，未证明正常负载下的限制继承/性能。节点mode/ACL已保存，三次失败均清理并回读三个Binder为0600；新boot禁止重放旧ACL。
- 禁用本轮不需要的 Waydroid hardware-manager 桥接；session 配置移除 Pulse socket。只挂专用 Wayland socket 和自有实验数据。没有全局关闭 AppArmor/SELinux；原 LSM 实读为 lockdown/capability/yama/bpf。
- [网络助手](<../scripts/android-lab/waydroid_lab_network.py>)只拥有 waydroid0 / 192.168.240.0/24 / nft inet `o8t_wd_lab_20261112` / policy table+rule 5240 / 独立 dnsmasq。先核网段、规则/路由表、接口、表名冲突。
- 不设置全局 ip_forward；仅租用 wlan0 每接口 forwarding（原0，临时1，停止恢复0）及新 bridge 的 forwarding。专用策略表把**容器来源**送往 wlan0，不改宿主 main 默认路由；DNSmasq 单独绑定 bridge、上游绑定 wlan0，不读宿主 DNS 配置。
- nft 只准 Android 出公网，阻止 Android 访问宿主其它端口/私网；阻断其它局域网向 Android 的新入站，ADB 不公网/旁路 LAN 开放。宿主到私有容器地址的 ADB 为本地发起。
- **本轮真实网络创建/停止一次通过**：routes/rules/nft/DNS/global forwarding/wlan0 forwarding 六项前后完全一致，bridge 清除。该测试不是 Android 网络验收。

## 自有 APK、agent、Frida

- [自有应用源码](<../scripts/android-lab/test-app/src/org/oneplus8t/agentlab/MainActivity.java>)：文字输入、Apply、结果 `OK:<token>`、可挂接普通 `transform(String)`；零权限，无账号/网络/硬件访问。debuggable 属实验配置。
- [构建器](<../scripts/android-lab/build_test_apk.py>)使用已校验工具，无Gradle/第三方APK；首个APK保留。最终build2将输入框改为普通文本（避免password无障碍语义），12,746 B，SHA256 `6da2295a941abc34daffb9673824b903634aecc630f8306de148600302c5a128`，zipalign/apksigner通过。两份调试签名私钥只留PC私有目录，均未进入Git或归档包。最终APK已部署到数据盘，但未安装进Android。
- [agent 闭环](<../scripts/android-lab/agent_loop.py>)：唯一 token → 精确 resource-id + package → 实时 XML bounds 定位 → 输入读回 → 点击 → 结果 UI 断言 → PNG/XML/argv/UTC 证据。没有固定坐标；返回码不能替代结果断言。定位器10项与boot状态3项共13个正/负测试已过，**不是 UI 三轮实测**。
- [Frida 闭环](<../scripts/android-lab/frida_loop.py>)：自有 App 启动 → 精确 PID 挂接 → hook ready → 同一 agent 操作 → 入参/返回/时间关联 → unload/detach/停止自有进程/删除本轮 adb forward。Python frida 与 server 均固定 `16.7.19`；不要求 frida-tools 版本相同，也没有安装不必要的 frida-tools。
- Linux arm64 客户端已解压到数据盘并实际 import 返回 16.7.19。server 为官方 GitHub release，gh 已核文件名/大小；旧 release 没有平台提供的 digest，记录 TLS 下载后的本地 SHA256，不伪称上游签名校验。**server 尚未在 Android 运行、未 attach**。

## 最终验收矩阵

| 项目 | 结果 |
|---|---|
| 独立分支、用户改动保留、Git bundle/工作文件快照 | 通过，未commit/push |
| 传统Binder真实节点/版本、宿主GPU、专用网络建删 | 实测通过；不代表Android兼容 |
| 32包签名链、system/vendor双端SHA/解包/MAINLINE初始化 | 通过 |
| 自有零权限APK、签名、13项定位与boot状态回归 | 通过离线层 |
| Android framework/ADB/DNS/公网/Android GPU | 失败/未到此阶段 |
| 3轮真实agent、Frida调用、3轮成功启动停止 | 未完成；启动尝试3次、成功0次，UI/attach各0次 |
| Android持续运行 | 未执行；不能拿宿主观察冒充 |
| 三次失败后的本轮网络/挂载/权限清理 | 三次均清理；复位后复查无活动实验对象 |
| 保护服务器全程不受影响 | **失败**：非预期整机复位，modem仍failed；QQbot后来自行恢复 |
| 版本清单、脚本、脱敏证据、原始私有事故证据 | 已归档，原始内容留设备 |

## 已遇失败，按类别保留

- 工具运行器先被 Windows 沙箱阻止启动；用户切换完全权限后继续，未修改 Windows 文件权限。语义检索提供方失败后改本地精确搜索。
- Debian 13 没有 gpgv，使用实际 apt 原生签名验证成功，不装旧验证器、不跳过认证。
- 安装脚本对无实例模板的状态断言过严，dpkg 本身 rc=0、没有重新安装；按实际四个 concrete unit 补验。
- Google SDK URL 初稿用横线拼错得404，查官方 repository XML 后改为下划线的真实URL，保留失败而非反复猜测。
- system 镜像首轮15分钟超时（实测速率约650 KiB/s），保留部分文件，按支持 Range 的同一官方 URL 续传并增大有依据的时间预算；完整 SHA 未通过前绝不解包使用。

## 一手来源

- [Waydroid 官方安装文档](https://docs.waydro.id/usage/install-on-desktops)：Debian13 backports 与手动容器/session顺序（不执行其破坏性清理示例）。
- [Waydroid 上游 Binder 实现](https://github.com/waydroid/waydroid/blob/main/tools/helpers/drivers.py)：本轮同时核对已下载 Debian 包内实际代码，而非只信上游 main。
- [官方 arm64 VANILLA OTA](https://ota.waydro.id/system/lineage/waydroid_arm64/VANILLA.json)、[官方 MAINLINE vendor OTA](https://ota.waydro.id/vendor/waydroid_arm64/MAINLINE.json)。
- [Google SDK 官方目录](https://dl.google.com/android/repository/repository2-1.xml)、[Frida16.7.19官方发布](https://github.com/frida/frida/releases/tag/16.7.19)。

## 事故、最终对账与后续依赖

**必读[非预期复位取证与受阻收口](<android-lab-r4-reset-20261112.md>)**：记录三次失败的新证据、意外复位、modem/QQbot状态、180秒宿主观察、精确清理、恢复边界和音频/传感器/GNSS/相机排序。服务器保护基线fail，Android/agent/Frida验收未完成；不能用已准备代码或包替代通过证据。
