# OnePlus 8T 主线 Linux 移植项目

## 全局协作规范

- 默认使用简体中文。
- 主代理负责规划、视觉任务、架构与跨模块决策、审核和最终交付。
- `explorer` 用于只读检索与取证；`worker` / `deepseek_worker` 用于边界清晰的局部执行任务。
- 命名子代理必须从空白上下文启动，任务正文必须自包含，不使用完整历史 fork。
- 多个写任务只有在文件写集互不重叠时才能并行。
- 子代理完成后，主代理必须复核改动与验证结果，并及时结束已完成线程。
- 保留用户已有改动；危险、破坏性或影响范围不明确的操作必须先确认。
- 仅在用户或适用规范明确要求委派时启动子代理。

## 目标与优先级

- 设备：OnePlus 8T 中国版 KB2000，代号 kebab，SM8250 / Adreno 650。
- 用户目标：Debian 13 arm64，内核尽量新；先核对社区进度，再做移植、备份与 Git 管理。
- 屏幕取证阶段已于 2026-10-03 按用户要求结束，不再继续身份/序列号取证。当前优先事项：准备 Debian 13 的主线 Linux 启动与后续刷写，先最小 RAM 救援环境，再确定根文件系统部署位置。
- 2026-10-03 实测：ColorOS `KB2000_14.0.0.602(CN01)`，Bootloader unlocked / verified boot orange；临时 Magisk root 已用于屏幕取证和 26 个指定原厂分区备份，所有副本通过字节数及设备前后/主机 SHA-256。重启退出临时 root，后续操作前重新核验状态。
- 屏幕已读到 Samsung AMB655X、DCS ID `A5 05 01`，序列号按厂商规则解码日期 2020-10-08；证据支持原厂规格的三星面板，不能据此证明从未换屏或完成真伪认证。完整序列号只存本地。
- 最新可用 SM8250 社区基线为 `sm8250-7.2.0`；最新主线参考为 `v7.3-rc5`。这些是本次调查快照，更新前重新查证。
- KB2003 的成功记录不能当成 KB2000 / ColorOS 14 的测试结果。
- 主线救援 boot、独立社区空 DTBO 候选及带 578 个匹配模块的 Debian 基础 rootfs 已在主机准备，入口 `docs/linux-boot-preparation.md`。未在手机启动 Linux、未刷 DTBO/boot/super。原厂 19 个 DTBO 条目离线应用均失败；临时启动是否绕过它们、社区空 DTBO 是否被本机 ABL 接受均未验证。
- 用户随后明确以 7.2 为主，参考 jfbobier 仓库移植为 Linux 服务器。主机服务器集成入口 `docs/debian-server-port.md`，最终组为 `artifacts/server-port-20261003/ready/`；4 GiB ext4/sparse、无线/GPU 和 59 个本机 OEM 文件、USB/SSH/Wi-Fi/QRTR/UCM 已集成。蜂窝/NV/扬声器不默认加载，Venus 保持禁用。用户明确授权一次 RAM 首启、不写分区；已执行，fastboot 返回 OKAY，但手机仍在 Bootloader，用户看到一直是 Fastboot 界面，Linux 未启动验证。已重启并确认原厂 ColorOS 完成启动，slot a、unlocked/orange、无临时 su。记录 `docs/ram-firstboot-20261003.md`。该授权未包含任何 DTBO/boot/super 刷写，后续分区操作须另行授权。
- 首启前已实际核对 rootfs 578、server initramfs 200、RAM rescue 8 个模块的二进制内容与原内核组一致。原厂恢复组在 `artifacts/stock-recovery-20261003/`，whole-super sparse 往返到 7 GiB raw 的 hash 与原备份相同；仅主机格式验证，未实机恢复，不改变手机操作授权边界。
- 用户随后明确回复“允许你btbo”，授权单次 dtbo_a 对照及原厂恢复，按键回 Bootloader 已实测可用。实验现已完成：只写社区空 dtbo_a、临时启动同一 7.2 RAM boot，ACM/USB SSH 实读内核和 UFS，super=sda15/7 GiB；未挂载手机存储。RNDIS function EF/04/01 且网络先于 ACM 的 RAM 修正使微软内置驱动正常工作；主机静态 IP 设置被 Windows 权限拒绝，实际使用链路本地 SSH。用户拔下 USB 后成功从 Linux 按键返回 Fastboot；已先恢复原厂 24 MiB dtbo_a，再确认 ColorOS 完成启动，A 槽、无临时 su。原厂输入 hash 与 Fastboot 恢复成功已核验，手机端刷后 hash 未回读。本次单次授权已消耗，不涵盖后续 boot/super/vbmeta/recovery/userdata 写入或切槽。入口 `docs/ram-boot-success-20261003.md`。
- r1 主机进展：TCM 完整四字节头计数及边界修复已导出锁定补丁，实际函数 ASan/UBSan 回归通过，旧代码被同套用例检出。r1 内核提交 `65dad81b2b498007e4972c45f94644f2f5d430f9`、`7.2.0-kebab-debian13-r1+` 已构建；新 RAM 组 `artifacts/boot-r1-20261003/rescue/` 和持久组 `artifacts/server-r1-20261003/ready/` 固化 USB 描述符/顺序修正，持久组增加 USB DHCP。新组 rootfs 578、server initramfs 200、RAM initramfs 8 模块实际字节均与新内核匹配，启动打包/固件/UUID/用户空间 SSH 检查已通过，源代码及产物成组快照已校验。旧 ready 组保持封存，不能混用。
- 最新设备状态：持续授权下已实际刷 super、dtbo_a、boot_a，保持 A 槽，手机留在持久 Debian 13。USB 自动 DHCP `.2`、SSH/sudo/真实 PTY、可写 `/dev/sda15` 根 UUID、64 MiB fsync/直接读取/重启后同 hash 均通过；两次主动正常重启后仍能严格 SSH 连接。boot_a / dtbo_a 候选确切字节范围的设备回读 hash 与输入一致，不代表完整分区尾部或已运行 super 的全 hash 相同。额外一轮关机由用户短按电源键触发，logind 记录确认正常关机；已加载 HandlePowerKey=ignore，并写入主机构建脚本，物理短按复测未做。RTC 为 1970，系统时钟已校准并保存标准 clock epoch，重启通过；Wi-Fi/NTP 按用户要求稍后配置。当前采样无 TCM invalid-length 洪泛或活动 Oops/panic，仍有两条 DSI 时钟启动 WARNING，触摸事件、充电及长期负载未验收。时钟和 logind 手机运行后配置不在原 r1 封存镜像中。入口 `docs/persistent-r1-20261003.md`。后续 Linux 维护经已固定 USB 源地址/保存的 SSH 主机密钥连接，不把当前手机误当作仍在 Android。

- 2026-10-04 最新维护：手机仍在上述持久 Debian r1；旧 Android/未启动 Linux/单次授权不足是历史阶段。蓝牙地址 oneshot 卡住已定位为 BlueZ 5.82 标准输入与未配置控制器查询兼容问题，采用外部超时/全局查询/异步重枚举等待，正常重启约 4 秒成功，未做无线配对。补齐 Mesa、Vulkan、相机工具/IPA、Python、timesyncd 共 140 个新增包，无升级/删除；GLES 硬件绘制和像素回读普通用户/重启后通过，Vulkan 仅枚举。相机 root 捕获仍缓冲分配失败零帧，NFC 仅驱动绑定、SSC 仅服务存在。regdb 已切换内核信任的 upstream 签名配对，未关闭校验；timesyncd 启用但尚未联网同步。较长日志已再次出现大量 TCM 无效报文，旧“无洪泛”仅早期窗口；DSI WARNING 仍在。Wi-Fi 认证、触摸/充电、音频、蜂窝/NV、长期稳定性未验收。本轮没有刷分区，也没有重打包原封存镜像；运行后私有配置已归档校验，构建配方已同步。入口 [最新盘点](<docs/userspace-audit-r1-20261004.md>)。

- 2026-10-04 触摸 r2 候选：只读查询确认 KB2000 TCM 的 7 字节手势前缀 + 7 字节对象，14/21 字节合法；修正运行配置解析、命令 framing 和启停零 ACK 独占轮询。共享解析头 ASan/UBSan 固定用例及 120,000 个随机/变异用例通过，匹配临时模块 W=1 无警告，三轮短测六次启停 ACK 成功。较早三轮 60 秒采集均零事件，物理操作未确认，不能宣称触摸可用。测试模块卸载、builtin 恢复；内核 taint O/E 仍在，未刷分区/触摸固件/持久配置/NV。候选仅导出补丁并记录独立 candidate_kernel_patches，生产 r1 仓库和自动应用清单不变。先协调用户实际单指/双指/抬起验证，通过后再构建部署。入口 [触摸候选](<docs/touch-protocol-r2-20261004.md>)。

- 2026-10-04 触摸修复部署与验收：确认内建 `synaptics-tcm` 把本机 7 字节前缀 + 7 字节对象布局的 14/21 字节报文判为非法（长时间刷 `invalid touch report length`），但 IC 一直正常上报（用户触摸时刻录得 length 21）。用[补丁 0002](<patches/local/0002-input-synaptics-tcm-runtime-layout-candidate.patch>)编译的模块 `tcm_r2_test.ko`（`2a7d0e726638ac3f418285cf2206f6db764703f6cf97e92691d4d52f74cd0b2f`）安装到 `/lib/modules/7.2.0-kebab-debian13-r1+/extra/`，新增 `kebab-touch-fix.service`（`/usr/local/sbin/kebab-touch-fix.sh`）开机 `modprobe` 后把 builtin 从 `13-004b` 解绑并绑定修正驱动。**实机证据**：有界三轮 45 s 采集在真实触摸下录得 5,209／40／0 个事件（含 `ABS_MT_TRACKING_ID 0`、`ABS_MT_POSITION_X/Y`、`BTN_TOUCH 1/0`、抬起 `-1`），收尾 `RESTORED_BIND synaptics-tcm CLEANUP_ERRORS []`；部署后 150 s 采集出现两个并存 tracking id（多指）；用户两次确认（部署后、重启后）桌面点按与拖动可用；重启后 `kebab-touch-fix.service` active、绑定 `tcm-r2-test`。**内建路线被 ABL 拒绝**：同一补丁编入内核得到 `32d0dc7c445a`、引导镜像 `d6151db1…`（仅内核段大 2,328 字节、头字段/偏移/总长一致、`gzip -t` 通过），写入 `boot_a` 后 ABL 报"系统文件被损坏，无法开机 / The current image(boot/recovery) have been destroyed"；同法此前两次均能启动，原因未查明，已用 fastboot 刷回 `ccacb17a…` 恢复（fastboot 判定可用，用户按键进入 bootloader 实测有效）。日志中 `invalid configured touch report` 与 `failed to receive report: -71` 与可用性无关（两种状态下都出现）。未刷分区、未写触摸固件/NV；触摸精度、边缘、三指以上手势、掌拒与长期稳定性未验收。入口 [触摸协议记录](<docs/touch-protocol-r2-20261004.md>)。

- 2026-10-04 XFCE 桌面：持久 Debian r1 离线安装 161 个包（`--no-download --no-remove --no-install-recommends`，0 升级/0 删除，`dpkg --audit` 干净），LightDM 显式禁用，默认仍 `multi-user.target`，SSH 与根分区不受影响。以 `systemd-run --unit=kebab-xfce-test --property=RuntimeMaxSec=20min` 有界运行 LightDM：Xorg modesetting + glamor 在 Adreno 650（日志 `glamor X acceleration enabled on FD650`）上工作，`DSI-1 1080x2400@120Hz` 为唯一模式，XFCE 4.20 组件齐全，整机内存约 750 MB／单元峰值 171.6 MB，`XGetImage` 只读抓到 1080x2400 桌面帧（PNG 156,745 字节，SHA-256 `cc82b3ef…b1255fe`）。**用户实机确认内屏仍然黑屏**：启动期 `DSI PLL(0) lock failed status=0x00000000`，DCS 读（0x52）返回 `-110`，强制 `xrandr` 关开输出后 DCS 写 0x51/0x53/0x9f 也超时并触发 DPU `frame done timeout`；面板上电写序列本身无报错，问题定位在 DSI/面板链路而非 XFCE 或安装。因此只可声明“渲染/合成/回读通过”，不得声明屏幕点亮；触摸真实操作本轮未取得用户确认，仍未验收。本轮无分区刷写、无触摸固件/NV/持久配置改动。入口 [桌面安装与显示验证](<docs/xfce-desktop-20261004.md>)。

- 2026-10-04 黑屏第 5 轮排查：面板 `vddio-supply`（`vreg_l14a_1p8`）在 `panel-samsung-amb655x.c` 中从未被取用，运行时用户计数为 0；用临时模块 `panel-vddio-test`（`023528cd…9add6d`）强制拉高到 1 后面板行为无任何变化（读数仍失败、无 TE、仍黑），已 `rmmod` 复原。启动期 `DSI PLL(0) lock failed status=0x0` 属 probe 现象：两次 devcoredump 中 `dsi0_pll` `0x1b0`（`COMMON_STATUS_ONE`）读到 `0x51`，bit0=1 表示运行期 PLL 已锁定。停 X、取消控制台消隐后由用户态 `mmap(/dev/fb0)` 写入白底+红条（1080x2400/32bpp/pitch 4352），同时 `vsync` 计数 1993→2113 递增、`underrun 0`、`frame_done_cnt 0`，**用户实机确认仍然全黑**；面板对 DCS 读 0x52 始终 `Connection timed out`/`No data available`/`Invalid response cmd`，也不回 TE。另确认独立缺陷：`fb0: sys_imageblit/sys_fillrect: framebuffer is not in virtual address space`，fbcon 无法绘制 DRM 帧缓冲。结论：故障在 DSI 物理链路/面板上电初始化一侧；候选实验（面板取电+复位时序+DCS ID 校验、按原厂 dtbo 对齐 lane/时序、暴露 60Hz 模式、修 fbdev 映射）见 `docs/xfce-desktop-20261004.md`。本轮无分区刷写、无触摸固件/NV/持久配置改动，模块已卸载、现场已复原。

- 2026-10-04 黑屏修复（第 6 轮）：用户实机照片显示内屏在 ABL 阶段正常显示开机画面、Linux 运行约 3 分钟后仍停在 ABL 画面——面板本体/供电/DSI 链路与 GRAM 自刷新正常，故障是内核送出的帧从未被面板接受。根因两处叠加、缺一不亮：(1) 原厂 dtbo 节点 `qcom,mdss_dsi_oplus20828samsung_amb655x_1080_2400_cmd` 声明 `qcom,mdss-dsc-slice-per-pkt = <2>`，驱动把 `dsi->dsc_slice_per_pkt` 留 0 被 msm 当 1 用（`bytes_per_pkt`/`pkt_per_line` 由该值推导），补丁 `patches/local/0003-drm-panel-amb655x-dsc-slice-per-pkt.patch`（`59ed5109…cc19a`，内核 `d6ab94b80a11`）；**单修此项仍全黑**（`frame_done_cnt` 涨到 217、DCS 读仍超时、面板停在 ABL 画面）。(2) 驱动既不申请也不驱动 `reset-gpios`（probe 只有注释、`reset()` 中三行 `gpiod_set_value_cansleep()` 被注释），ABL 交接后面板停在 GRAM 自刷新；按厂商 `qcom,mdss-dsi-reset-sequence = <1 10 0 1 1 10>` 恢复脉冲，补丁 `patches/local/0004-drm-panel-amb655x-drive-reset-gpio.patch`（`824bb7c0…5a221f`，内核 `746acf8f065e`）。部署 `boot_a` 引导镜像 45,617,152 字节 SHA-256 `ccacb17a…c2b3dd`（复用 r1 initramfs 与 cmdline），写入后按同范围回读一致，只写 boot_a。修复后证据：`actual_brightness` 由 `Connection timed out` 变为返回 39936、`vsync` 递增、`frame_done_cnt: 0`、不再出现 `frame done timeout`；**用户实机确认看到白底+红条测试画面**，同一内核上有界 XFCE 会话在 `DSI-1 1080x2400@120Hz` 运行（`kebab-xfce-test.service` active、LightDM 仍禁用）。回退源：r1 `artifacts/server-r1-20261003/ready/boot-server.img`（`f899299d…`）或 DSC 候选（`5bfa275b…`），原厂在 `backups/partitions-20261003-152513-342/boot_a.img`。仍未修复的独立问题：fbcon 无法绘制 DRM 帧缓冲、`vreg_l14a_1p8` 未被驱动申请、只暴露 120Hz 模式、启动期 PLL probe 报错、触摸未按真实手指操作验收。

## 来源与移植方法

- 2026-10-04 最新服务器就绪只读盘点：系统/SSH/7GiB根与/srv约220GiB/80-75限充服务正常，无failed unit；当前停充读回有效，真实跨阈值及长期负载仍未验收。存储重启后Wi-Fi服务active、DNS可解析，但绑定wlan0的Debian/kernel.org IPv4 HTTPS均超时，9.9.9.9 HTTP转校园内网门户；NTPSynchronized=no，多个NTP回复超时。应先重新认证/排查，不能沿用之前“校园外网/NTP通过”的结果。本次手机无Java/25565监听，MC/公网入口未就绪；只读盘点没有替用户登录或改配置。入口docs/server-readiness-20261004.md。

- 2026-10-04 最新存储：用户明确允许清空userdata并采用7GiB系统盘+约224GiB数据盘。已保存当前Debian私有文件归档并完整读/hash核验（非原子块快照、不包含userdata），根/dev/sda15 ext4在线扩至7GiB，df6.9GiB/剩约3.9GiB；仅格式化已核验/dev/sda28 userdata为ext4，按UUID挂载/srv，df约220GiB可用。一次正常重启后自动挂载、32MiB fsync/直接读回、主备sda GPT哈希完全未变、A槽成功标记/retry5、严格USB SSH与限充自启通过。没有改GPT布局或boot。实际mkfs提示原厂userdata偏离512KiB最小I/O对齐393216字节但4096-byte逻辑/物理对齐，未重分区，性能未测。MC会话请用/srv数据盘并为服务设置挂载依赖/挂载点检查，不覆写存储配置；不再依据旧3.9GiB总容量规划。入口docs/storage-expansion-20261004.md；本次userdata授权已执行，不是后续任意擦除授权。

- 2026-10-04 最新限充部署：control镜像相同SHA-256在恢复A槽重试数后正常启动，首次ABL损坏提示对应successful=no/retry=0/unbootable=yes；没有存储损坏证据。224秒实机续约/服务停止/杀进程90秒到期恢复通过，用户已授权测试通过后开启常驻，现80/75策略enabled/active且高电量停充读回通过。用户另行明确允许一次A槽成功位修复并重启验证：主备GPT原始块已双端备份校验，仅设置boot_a bit54并更新对应CRC，不切槽/改布局；主备逐字节读回通过，正常重启后successful=true、retry仍5、严格USB SSH与服务自启通过。该一次授权已执行，不是后续任意GPT写入授权；未部署开机自动GPT写入服务。真实80/75完整循环、插拔、长期负载仍未验证。入口 docs/charge-policy-v1-20261004.md、docs/boot-success-repair-20261004.md；下方18:45状态为历史。

- 2026-10-04 18:45 限充v1检查点：90秒可续约内核 `7.2.0-kebab-debian13-r2-charge-policy+` 已完整构建；131,608个实际寄存器核心用例、9项策略、8项部署保护、5项健康联动测试通过。578模块与200 initramfs模块核验通过，同组readonly镜像实机启动、严格SSH、allow_control=0、接口444通过。服务已安装但disable。用户已明确授权约4分钟有界续约/停止/进程丢失恢复测试，通过后开启常驻80/75策略，后续不重复申请该范围授权。control镜像写boot_a并回读一致后正常重启，USB/RNDIS/SSH/Fastboot均未重新出现，已请求用户确认屏幕与插线；实际状态未确认，测试尚未开始、常驻限充未生效。先恢复管理链路，不把新内核默认只读通过当作control模式通过。入口 docs/charge-policy-v1-20261004.md，旧只读候选和完整boot备份保留，本轮未写GPT/misc或其它分区。

- 2026-10-04 当前分工：用户暂停本会话MC/穿透、另开会话使用 docs/mc-cnb-resume-prompt-20261004.md，当前会话负责服务器基础与80%限充目标。已部署只读kebab-health一分钟timer（RAM状态、告警变化才输出详情）、mask四类软件休眠、Asia/Shanghai时区；既有电源键ignore和journal64/16MiB保留。实际MP2762A驱动只提供OTG、BQ电量计只读、TCPM可写属性是PD协商，没有经验证的电池停充接口，80%未实现，未写充电寄存器/阈值。多轮timer、约6.5MiB峰值、无failed unit通过，未主动重启校园会话。入口 docs/server-base-20261004.md。MC新会话不能改充电/电源/温控/基础监控/SSH/USB配置，必须隔离工作树和写集。

- 2026-10-04 后续网络选择：用户提供移动网络有公网 IPv6 的截图，但校外连接临时25565探针失败；手机 IPv6 网关及公网 ICMP 回复通过，不足以证明入站可达。用户明确“不试免费中继，试试443出站”，不得擅自安装 playit/免费中继。已关闭临时探针；socket 绑定 wlan0 直连 Debian/kernel.org/清华的 IPv4 TCP443、TLS1.3标准证书和HTTP200全部通过，IPv6域名无地址、固定公网IPv6 TCP443超时，不能断言整体封禁。反向443隧道仍需用户的公网接收端，家里/朋友设备条件待确认；尚未部署MC或穿透客户端。入口 docs/gdut-wifi-20261004.md。

- 2026-10-04 GDUT 首次接入：用户要求手机直连大学城/番禺 `gdut`；r2 ath11k/QCA6390 实际扫描、5 GHz 关联、DHCP/网关/DNS 已通过，启用 wpa_supplicant@wlan0，USB SSH 不变。用户已通过私有 SSH SOCKS 浏览器完成门户认证，实际外网 HTTP/HTTPS、DNS、NTP 通过（系统时钟同步，不代表 RTC 修复）。临时桌面正常 20 分钟超时状态已清除，无失败系统单元。重启后认证状态/长期无线稳定性未验收。用户明确目标为 MC Java 版、无 VPS、优先省钱和校园流量；全球 IPv6 静态探针临时在 TCP25565 监听、本机访问通过，等待用户从另一台手机移动数据验证校外入站，不能宣称穿透已通。尚未部署 MC 或穿透客户端；Tailscale 官方登录/软件源首轮探测超时。手机约 11 GiB 内存，当前根文件系统剩余约 1.7 GiB，未扩容。学校账号密码不发聊天、不写项目文件；原始日志/地址/浏览器 profile 只留 local。入口 docs/gdut-wifi-20261004.md。

- 2026-10-04 最新 r2：`7.2.0-kebab-debian13-r2+` 将 0001/0003/0004/0002-r2 成组内建，独立 578 模块树、持久 initramfs 200、RAM 8 模块实际字节核验通过。手机只更新 boot_a 和新增 r2 模块；两次正常重启、严格 USB SSH、面板/GLES、32 MiB 重启后直接读取校验通过。旧 `kebab-touch-fix.service` 已禁用，内建 `synaptics-tcm` 绑定，旧 r1 树/服务文件保留；回退 r1 须重新启用服务。真实单指 908 事件/18 次建立与抬起，双指 3,197 事件/同时两触点，重启后 2,305 事件/20 次建立与抬起；用户重启前后确认正常。曾报告完全不碰屏幕时指针漂移，后续协调静置 60 秒零事件、松手不漂移，根因与长期稳定性尚未确定；较长窗口配置报文拒绝及 -71 仍出现。入口 `docs/r2-integrated-20261004.md`，此为当前状态，前文 r1/内建失败记录为历史。

- 优先读取 Linux 官方源码、SM8250 社区源码、OnePlusOSS 和维护者的一手记录。
- 在 `docs/community-status.md` 标注调查日期、提交、来源与限制，区分官方主线、社区主线分支和 Android 厂商内核。
- 第三方仓库和版本固定在 `sources.lock.json`。源码下载到被忽略的 `sources/`，不得把内核源码直接重复提交到管理仓库。
- 不直接运行社区的刷机脚本。部分脚本默认刷机，即使 `--no-flash` 仍会向手机部署模块。
- 主机准备、源码准备、构建、打包、手机部署分别执行；本项目构建脚本只操作构建目录。
- 构建并校验设备树后再编译内核和模块；内核、DTB、模块、配置、固件路径和 boot image 参数必须作为一组保存。
- Debian 13 需要自己的 initramfs、firmware、udev、ALSA UCM 与启动/槽位策略，不能直接安装社区的 Alpine APK 当作移植完成。
- 一次修改处理一个可验证问题；社区补丁保留作者与许可证。升级内核时检查重复补丁、DTS 漂移和模块兼容性。

## 设备操作边界

- 2026-10-03 用户随后明确持续授权：“以后的dtbo和持久系统都不用单独授权，可以直接执行”。该用户指令覆盖后续 DTBO 测试及既定 Debian 持久部署所需的 dtbo_a、boot_a、super 写入和对应原厂恢复；不再为这些既定操作重复申请权限。执行前仍核验目标/槽位、输入 hash、备份和恢复流程，并保留日志。该授权不扩大为解锁/重新锁定、切槽、改分区布局、清空 userdata 或写 vbmeta/recovery/NV。先前“单次授权已消耗”的记录是历史范围，不能用来再次阻止已持续授权的操作。

- 已授权的 ADB 只读调查、源码下载、构建和本地备份可继续执行。
- 解锁/重新锁定 Bootloader、刷写/擦除任何分区、写 super、切换槽位、改分区布局或清除用户数据，必须在列明目标、备份和恢复方案后取得明确授权。
- 2026-10-03 用户已明确要求先解锁 Bootloader 再尝试屏幕取证；执行前保存可读数据并确认其接受恢复出厂造成的数据丢失。该授权不包含 root、关闭 SELinux、刷 recovery、其他分区刷写或重新锁定。
- 随后用户要求继续提供屏幕取证权限；本轮按 `docs/temporary-root-plan.md` 准备匹配原厂 boot 的 Magisk 临时启动，用途限本机屏幕取证和原厂分区备份。不得将此扩展为永久 root、Direct Install、刷 recovery、关闭 SELinux、刷写其他分区或重新锁定。手机安装/身份验证与 Shell 授权提示需用户处理。
- 手机命令必须指定设备序列号；原始日志、序列号和实际面板序列号仅存 `local/`。
- 分区路径来自本机 `/dev/block/by-name/`，不得照抄 KB2003 的 `/dev/sda14`：本机实际 `super` 指向 `/dev/block/sda15`。
- 原厂分区备份必须使用原始二进制流，校验字节数和设备端/主机端 SHA-256；权限拒绝或读取失败不得生成“成功”记录。
- 用户数据备份与原厂分区备份分别记录，解锁会清除用户数据。未备份不能假装已经有恢复保障。
- 固件、分区镜像、EFS/NV、IMEI、ICCID、PIN、密钥及个人文件不得提交 Git 或上传。

## Git、备份与验证

- 管理仓库 `main` 保存规范、来源锁定、工具和验证记录；阶段改动用主题分支和小提交。
- 内核改动在 Linux 文件系统中的独立仓库维护，用补丁导出到本项目；不在 Windows 默认大小写不敏感的工作树构建内核。
- 提交前检查差异、敏感文件忽略规则和对应验证；不覆盖用户的 Git 身份，不擅自创建远程仓库或推送。
- 每个阶段创建项目 Git bundle 和当前工作文件快照，校验后再标记完成。`backups/` 的同盘副本需用户另行复制到外部介质。
- 执行过、仅静态检查过、社区声称支持、尚未测试、被硬件/权限阻塞的事项必须分别说明。

## 常用入口

- `README.md`：项目现状与快速入口。
- `docs/device-assessment.md`：本机屏幕、Bootloader 与分区取证。
- `docs/community-status.md`：社区与主线支持调查。
- `docs/porting-plan.md`：Debian 13 移植路线和验收。
- `docs/backup-and-recovery.md`：备份与恢复要求。
- `docs/git-workflow.md`：仓库管理与恢复步骤。
- `scripts/Get-DeviceInfo.ps1`：只读设备调查。
- `scripts/Sync-Sources.ps1`：固定来源下载和核验，不重置已有改动。
- `scripts/Backup-Partitions.ps1`：有权限时只读备份指定分区。
- `scripts/Backup-Project.ps1`：管理仓库快照。
- `scripts/Backup-BuildArtifacts.ps1`：按产物清单校验并保存内核/DTB/模块成组快照。
- `scripts/linux/prepare-kernel.sh`、`build-kernel.sh`：仅在主机准备/构建。
- `scripts/linux/backup-kernel.sh`：完整源码、配置与补丁快照。
