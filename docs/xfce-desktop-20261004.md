# XFCE 最小桌面安装与显示路径验证（2026-10-04）

> 阶段记录提示：这是桌面安装、黑屏定位及修复的阶段记录。早期黑屏结论已被后续修复与用户实机确认替代；当前是无桌面服务器模式，短按切屏另有服务。 最新入口见[当前状态](project-status-20261004.md)与[交接](handover-20261004.md)。

本轮在持久 Debian 13（`7.2.0-kebab-debian13-r1+`，根 `/dev/sda15`）上安装最小 XFCE 桌面，并对"图形栈"与"物理屏幕输出"分别取证。**结论：软件包安装、Xorg/XFCE 会话、GPU 渲染与帧缓冲回读通过；内屏物理输出未通过（用户报告黑屏），DSI 链路与 DCS 读命令存在超时错误。** 本轮没有写分区、没有改槽位、没有清数据、没有动触摸固件/NV，保留原服务器启动模式（`multi-user.target`，LightDM 保持禁用）。

## 1. 安装内容与核验

- 离线包集合在主机准备并整体传入手机 `/tmp/kebab-xfce-packages`（161 个 `.deb` + `SHA256SUMS`），手机端 `sha256sum -c SHA256SUMS` 全部 OK。
- 安装使用 `apt-get --no-download --no-remove --no-install-recommends`，预演计划断言 `0 upgraded, 161 newly installed, 0 to remove and 0 not upgraded`，实际安装与计划一致：不升级、不删除已有包。
- 安装期间临时放置 `/usr/sbin/policy-rc.d`（exit 101）阻止服务自动启动，脚本退出时删除；脚本前后均断言该文件不存在。
- 关键版本（`dpkg-query`）：`lightdm 1.32.0-6+b2`、`xfce4 4.20.1`、`xfce4-session 4.20.2-2`、`xserver-xorg-core 2:21.1.16-1.3+deb13u4`、`xserver-xorg-input-libinput 1.5.0-1`。
- `dpkg --audit` 无输出；`systemctl get-default` 仍为 `multi-user.target`；`ssh.service` active；根分区 `/dev/sda15 3.9G 2.1G 1.8G 55%`。
- LightDM 安装后被显式 `systemctl disable lightdm.service`，默认不进入图形登录，服务器行为不变。

## 2. 有界桌面会话验证

以一次性 transient 单元运行，不允许其成为默认启动项：

```sh
sudo -n systemd-run --unit=kebab-xfce-test --property=RuntimeMaxSec=20min \
  --property=TimeoutStopSec=15s /usr/sbin/lightdm --config=/tmp/xfce-test.conf
```

- 自动登录 `debian`，会话 `xfce`，`X -nolisten tcp`（配置 `local/xfce-20261004/xfce-test.conf`）。
- Xorg 使用 modesetting 驱动接管 `/dev/dri/card0`，日志 `modeset(0): glamor X acceleration enabled on FD650`（Adreno 650），即渲染走 GPU 而非 llvmpipe。
- `xrandr`：`DSI-1 connected 1080x2400+0+0`，唯一模式 `1080x2400 120.00*+`；`DP-1 disconnected`。
- `loginctl` 出现 manager/seat0/user 三个会话；主要进程 RSS：`xfce4-session` 77.7 MB、`xfdesktop` 59.5 MB、`xfce4-panel` 37.8 MB、`xfwm4` 34.5 MB、`xfsettingsd` 22.4 MB、`Thunar --daemon` 21.8 MB、`Xorg` 121.5 MB（RSS 含共享页，仅作量级参考）。
- 单元内存峰值 171.6 MB、CPU 3.415 s；整机 `free -m` 由 547 MB 升至约 750 MB（总 11613 MB），空闲内存充足。
- 只读帧缓冲取证：通过 `XGetImage` 抓取 root window，得到 1080x2400、32bpp、TrueColor，掩码 `ff0000/ff00/ff`；导出 PNG 156,745 字节，SHA-256 `cc82b3ef1b2c5e8ee95dfbf0bf40c5b01e7714dad59f1a258d9905db8b1255fe`（`local/xfce-20261004/xfce-desktop.png`），画面为完整 XFCE 桌面（面板、xfdesktop 图标、壁纸、时钟）。
- 会话在 20 分钟上限被 systemd 停止：`Service reached runtime time limit. Stopping.`、`Failed with result 'timeout'`。这是设计内的有界退出，不是崩溃。

**以上证明"渲染+合成+回读"链路可用，不能证明内屏真的点亮。**

## 3. 物理屏幕输出未通过（用户报告黑屏）

用户在实际机器上确认屏幕是黑的。现有证据链：

1. `[1.015166] DSI PLL(0) lock failed, status=0x00000000`，调用点 `dsi_phy_driver_probe+0x330`——发生在 PHY probe 阶段，寄存器读出全 0；尚未证明这一条本身是否良性。
2. 面板上电序列（prepare→on）在启动期**没有**任何错误行，说明 bring-up 的 DCS 写路径当时是通的；也没有 `Failed to initialize panel`。
3. DCS **读**命令超时：读取 `/sys/class/backlight/ae94000.dsi.0/actual_brightness`（驱动内实现为 DCS 0x52 读）得到 `Connection timed out`，内核同时记录
   `dsi_cmds2buf_tx: cmd dma tx failed, type=0x6, data0=0x52, len=4, ret=-110` 与 `msm_dsi_host_cmd_rx: Read cmd Tx failed, -110`。
4. 强制一次输出重启（`xrandr --output DSI-1 --off` 再 `--mode 1080x2400 --rate 120`，同时 `xset s off`、`xset -dpms`）后出现：
   `DCS long write (cmd 0x53/0x51...) ret=-110`、`[drm:drm_panel_disable] failed to disable backlight: -110`、
   `DCS long write (cmd 0x9f, 3 bytes) failed: -110`、`Failed to un-initialize panel: -110`，
   随后 DPU 报 `frame done timeout`、`failed wait_for_idle: id:35 ret:-110`。重新打开输出本身返回成功，面板状态回到 enabled/dpms On，但屏幕仍黑。
5. 重启输出后 `actual_brightness` 的返回由 `ETIMEDOUT` 变为 `No data available`，说明读通道仍无有效应答。

综合判断：软件合成与扫描输出在运行，**面板侧没有可用的响应与可见输出**；黑屏不是"XFCE 没启动"造成的，也不等于硬件损坏——同一面板在 ColorOS 下正常。该问题属于 DSI/面板链路，需要单独的内核调试，本轮不做未经验证的寄存器或固件改动。

## 4. 触摸状态

- 当前绑定仍是内建 `synaptics-tcm`，未加载 r2 测试模块；内建驱动持续打印 `invalid touch report length: 14`（本次 boot ring 中 80 条），与已定位的"7 字节前缀 + 7 字节对象"布局不符的结论一致。
- 本轮没有取得用户确认的真实单指/双指/抬起采集，**触摸仍未验收**。候选模块哈希 `2a7d0e72…cd0b2f` 与采集脚本仍在手机 `/tmp`，可按既有流程在用户配合时复测。

## 5. 现状与回滚

- 保留：`multi-user.target` 默认、LightDM 禁用、SSH 正常、根分区可写、无分区刷写。
- 再次启动桌面（有界、可中断）：
  `scp` 两个脚本后执行 `bash /tmp/start-desktop-test.sh`；停止用 `systemctl stop kebab-xfce-test.service`。
- 卸载桌面：用同一批本地 `.deb` 做 `apt-get remove`，或按 `local/xfce-20261004/xfce-packages-before.txt` 对比；本阶段没有改动用户数据目录以外的系统配置。
- 证据文件：`local/xfce-20261004/`（安装日志、`xinput/xrandr`、Xorg/LightDM 日志、帧缓冲 PNG、显示运行时 JSON）。私有日志不入 Git。

## 6. 未验收项

内屏物理输出、触摸、音频、蜂窝/NV、充电、长期稳定性均未验收；GPU/Vulkan 仍只到"GLES 绘制+回读通过、Vulkan 仅枚举"的水平。

## 7. 黑屏深入排查（2026-10-04 第 5 轮）

目标是把"面板没点亮"从软件层逐步隔离到物理链路层。全部操作可回退，无分区刷写。

### 7.1 面板供电轨从未被取用（已证实，但不是唯一原因）

- 设备树给面板声明了 `vddio-supply = <&vreg_l14a_1p8>`，但 `panel-samsung-amb655x.c` 全文没有任何 `regulator`/`supply` 取电代码。
- 运行时证据：`/sys/kernel/debug/regulator/regulator_summary` 中 `vreg_l14a_1p8` 用户计数为 **0**（未使能）；同组其它显示相关轨道（`vreg_l9a_1p2`、`vreg_l5a_0p88`、`vreg_s4a_1p8` 等）均在使用中。
- 用临时诊断模块 `panel-vddio-test`（`of_regulator_get()` 取 `vddio` 并 `regulator_enable()`）把该轨拉高：计数由 0→1，`is_enabled=1`；模块 `023528cd63a4e51a27fdf67dde65ba53ba2103edb512bd65cd5b31f9fb9add6d`。
- 结果：**面板行为没有任何变化**（读数仍失败、仍无 TE、屏幕仍黑）。因此"缺电"是真实缺陷，但不足以解释黑屏。测试后已 `rmmod`，轨道回到 0 用户。

### 7.2 直接写扫描输出缓冲仍然全黑（用户确认）

- 停掉 X，确认 `card0-DSI-1` connected/enabled/dpms On、`crtc` 有 1080x2400 fb、控制台取消消隐（`/sys/class/graphics/fb0/blank=0`）。
- 用户态 `mmap(/dev/fb0)`（1080x2400、32bpp、pitch 4352、10,444,800 字节）写入**白底 + 中间红条**，绕过 X/glamor 与合成器。
- 同一时刻 DPU 在正常出帧：`encoder-0/status` 的 `vsync` 计数 1993→2113（约 30 次/秒），`underrun: 0`、`frame_done_cnt: 0`。
- **用户实机确认：仍然全黑。** 说明故障在 DPU 之后（DSI 链路/面板一侧），与桌面、合成器或"没画东西"无关。

### 7.3 已排除：PHY PLL 不是运行期阻塞点

- 每次启动在 1.02 s 都有 `DSI PLL(0) lock failed, status=0x00000000`，调用点 `dsi_phy_driver_probe+0x330`（PHY probe 阶段），随后是 `clk_core_disable/unprepare` 的 WARNING。
- 但两次 `/sys/kernel/debug/dri/0/kms` devcoredump（不同时间点）里 PLL 状态寄存器 `dsi0_pll` `0x1b0`（`COMMON_STATUS_ONE`）都读到 `0x00000051`，**bit0=1 即 PLL 已锁定**；`dsi0_ctrl`/`dsi0_lane` 寄存器也已按 DSC 4 lane 配置。
- 结论：probe 期那条报错是探测顺序造成的现象，运行期 PLL 正常，不能当作黑屏根因。

### 7.4 面板在 DSI 返回通道上完全不应答

- 读 `/sys/class/backlight/ae94000.dsi.0/actual_brightness`（驱动内为 DCS 0x52 读）依次出现：`Connection timed out`、`No data available`，内核记录
  `msm_dsi_host_cmd_rx:Invalid response cmd`；伴随 `mipi_dsi_set_maximum_return_packet_size` 也失败（`data0=0x2 ... ret=-110`）。
- 命令模式面板的帧完成依赖面板 TE：X 运行期间持续 `frame done timeout` + `failed wait_for_idle ret:-110`，`vsync` 计数长时间停在 837 不动 → 面板没有回 TE。
- 上电初始化写序列（prepare/on）自身**不报错**，那只是主机把字节推进 FIFO 的完成，不能证明面板收到。

### 7.5 另一条独立缺陷：内核无法向 DRM 帧缓冲绘制

每次启动都有：

```
[ 1.816291] fb0: sys_imageblit: framebuffer is not in virtual address space.
[ 4.131245] fb0: sys_fillrect: framebuffer is not in virtual address space.
```

**第 6 轮更正**：告警条件不是缓冲为空，而是 `if (!(p->flags & FBINFO_VIRTFB))`（`sysfillrect.c:21`）——msm 的自定义 fbdev（`msm_fbdev.c`）在 `drm_fb_helper_fill_info()` 之后没有补上 `FBINFO_VIRTFB`，而它其实已经把 `fbi->screen_buffer = msm_gem_get_vaddr(bo)` 设好了。因此**告警是虚警：`sys_*` 仍会继续调用底层 `fb_fillrect()` 正常绘制**。实测：帧缓冲里有 119,521 个非零字节且随时间小幅变化，说明控制台确实在画。屏幕上看不到控制台文字的真实原因是 VT 状态——X 把 `tty7` 留在活动状态且该 VT 没有控制台，而控制台文字在 `tty1`；测试中 `chvt 1` 在当前环境未生效（活动 VT 仍报 `tty7`），VT 切换需另行排查。`vtcon1`（frame buffer device）`bind=1` 正常。

### 7.6 当前结论与下一步候选实验

结论：**GPU/合成/回读正常，DPU 在出帧，PLL 锁定，面板供电已可强制打开，但面板既不回 DCS 也不回 TE、屏幕无任何可见输出。** 故障位于 DSI 物理链路或面板上电/初始化时序一侧。

候选实验（都需要重新构建内核并部署 boot_a，仍在持续授权范围内，执行前会单独确认目标与回退）：

1. 面板驱动按厂商时序处理供电与复位（取 `vddio`、驱动 `reset-gpios`、加足上电延时），并在 `prepare` 后读一次 DCS ID（0x04/0xDA）把链路是否通变成可判定证据；
2. 用备份里的原厂 `dtbo_a` 反编译对齐 lane 映射/时序/供电（`data-lanes`、lane swap、panel 节点属性）；
3. 暴露并测试驱动的 60 Hz 模式（当前 `samsung_amb655x_mode` 标记 `__maybe_unused`，只暴露 120 Hz），排除 800 MHz PLL 速率相关问题；
4. 修复 fbdev 缓冲映射，使控制台能显示，作为"面板是否真的能显示"的独立判据。

### 7.7 现场状态

测试模块已卸载、`vreg_l14a_1p8` 恢复 0 用户、桌面临时单元停止、LightDM 保持禁用、默认 `multi-user.target`、SSH 正常、控制台取消消隐；本轮无分区刷写、无触摸固件/NV/持久配置改动。

## 8. 黑屏根因定位与修复尝试（2026-10-04 第 6 轮）

### 8.1 用户实机照片：面板本体与链路正常，ABL 能显示

用户提供的实机照片（`local/`，仅本地保存）显示内屏正常点亮并显示开机阶段画面：OnePlus 标志、`Orange State / Your device has been unlocked and can't be trusted / Your device will boot in 5 seconds`、`Powered by android`。拍照时刻为设备时间 03:12:47，距本轮重启约 3 分钟，即 **Linux 已经运行，而面板仍停在 ABL 留下的画面**。

由此可以确定：

- 面板本体、供电与 DSI 物理链路**没有硬件故障**（同一面板在 ColorOS/ABL 下能正常显示）；
- 面板处于**自刷新**状态：GRAM 里保留 ABL 写入的开机画面，且 Linux 送出的帧从未被它接受；
- "黑屏"的准确描述是"Linux 阶段面板不更新（并在此前的关开实验后一度整屏熄灭）"，不是面板坏。

### 8.2 根因候选一：DSC slice-per-packet 不匹配（已实施并部署）

- 从本机原厂 `dtbo_a` 备份解析出 19 个 overlay，其中命中本机 cmdline 的面板节点
  `qcom,mdss_dsi_oplus20828samsung_amb655x_1080_2400_cmd` 明确声明
  **`qcom,mdss-dsc-slice-per-pkt = <2>`**（同节点还有 `qcom,mdss-dsc-encoders = <2>`、
  `slice-height = 0x1e`(30)、`slice-width = 0x21c`(540)、`bit-per-component/pixel = 8`、
  `block-prediction-enable`、panel `0x438 x 0x960` = 1080x2400）。
- 内核侧 `panel-samsung-amb655x.c` 原来的注释是"`dsi->dsc_slice_per_pkt` 留 0，msm 当 1 用；
  原始 TODO 要 2，但 6.8 已知可用构建没有这个字段"，而 msm 在
  `dsi_host.c:1713` 取该字段、1715-1716 在 0 时替换为 1，并用它计算
  `bytes_per_pkt`（`slice_chunk_size * dsc_slice_per_pkt`）与 `pkt_per_line`
  （`slice_per_intf / dsc_slice_per_pkt`）——**打包方式与面板要求不一致时面板无法解码**。
- 修复：probe 中在 `mipi_dsi_attach()` 之前显式 `dsi->dsc_slice_per_pkt = 2;`
  （补丁 `patches/local/0003-drm-panel-amb655x-dsc-slice-per-pkt.patch`，
  SHA-256 `59ed5109e405f0cff330ce8437b8de0416120f0886081c862485137f8eecc19a`；
  内核提交 `d6ab94b80a118dd80fb5d26ee3882f046f8a820e`）。
- 硬件侧已被证实生效：修复前后两份 devcoredump 对比，`dsi0_ctrl` 的 `0x2a0` 由
  `00000000 39003941 0000021c` 变为 `00000000 39003901 0000021c`（第二字低字节 0x41→0x01），
  说明 DSC 打包相关寄存器确实按新值编程。
- 部署与重启：候选引导镜像 45,617,152 字节、SHA-256
  `5bfa275b841b426326c4e4ec76fb7d62aa2c1492e3682e4876d112081f49270c`，
  复用已部署 r1 的 initramfs 与 cmdline（`root=UUID=bc52fbbe-…`），
  在手机内写入 `/dev/disk/by-partlabel/boot_a` 后按 45,617,152 字节回读，哈希与输入一致；
  重启后内核 `7.2.0-kebab-debian13-r1+` `#3 SMP PREEMPT Sun Oct 4 11:00:29 CST 2026`。
- 结果：**仍未点亮**。新内核下 DPU 依然只在空转：`vsync` 计数冻结、`frame_done_cnt` 累计到 217 次
  `frame done timeout`、`failed wait_for_idle ret:-110` 反复，DCS 读仍 `Connection timed out`，
  面板继续显示 ABL 画面。DSC 打包本身是正确的修正，但不足以让面板接入。

### 8.3 根因候选二：面板复位 GPIO 从未被驱动（已实施，构建部署中）

- 驱动此前**既不申请也不驱动** `reset-gpios`：probe 里只有一段注释（"devicetree 里留着是为了文档和
  将来的修复"），`samsung_amb655x_reset()` 里三行 `gpiod_set_value_cansleep()` 全被注释，
  只留下三次 `usleep_range()`。
- 厂商节点给出确切依据：`qcom,mdss-dsi-reset-sequence = <0x01 0x0a 0x00 0x01 0x01 0x0a>`
  即"高 10 ms → 低 1 ms → 高 10 ms"（reset 为 ACTIVE_LOW，物理高=不复位），与驱动里被注释掉的
  调用序列完全一致；同节点还声明 `qcom,mdss-dsi-te-using-te-pin`、
  `te-pin-select = 1`、`te-dcs-command = 1`、`platform-te-gpio = <0x42>`(gpio66)、
  `platform-reset-gpio = <0x4b>`(gpio75)、`platform-panel-vout-gpio = <0x18>`(gpio24)、
  `qcom,mdss-dsi-init-delay-us = <0x3e8>`。
- 判断：ABL 交接后（或 ABL 关闭自身 DSI 接收端后）面板只做 GRAM 自刷新，**必须复位才能重新
  锁定链路并接受内核的初始化序列与帧**。上游当年跳过复位是因为当时 DSC 打包错误、复位后表现为花屏，
  现在打包已按厂商值修正，复位成为必须的一步。
- 修复：probe 中 `devm_gpiod_get(dev, "reset", GPIOD_OUT_LOW)`，`reset()` 中恢复厂商脉冲
  （补丁 `patches/local/0004-drm-panel-amb655x-drive-reset-gpio.patch`）。

### 8.4 供后续使用的厂商参数（本次解析原厂 dtbo 得到）

- DSC：`encoders = 2`、`slice-height 30`、`slice-width 540`、`slice-per-pkt 2`、
  `bit-per-component 8`、`bit-per-pixel 8`、block prediction 开。
- 时序（120 Hz 项）：`panel-width 0x438`(1080)、`panel-height 0x960`(2400)、
  h-pulse 24 / h-back-porch 24 / h-front-porch 52、v-pulse 4 / v-back-porch 8 / v-front-porch 4、
  `qcom,mdss-dsi-panel-phy-timings = [00 24 0a 0a 26 25 09 0a 06 02 04 00 1e 1a]`、
  `bl-max-level = 0xfff`(4095)、`init-delay-us 1000`。
- 驱动的 0x9e PPS 负载与厂商 `qcom,mdss-dsi-on-command` 中的序列逐字节一致（前 40+ 字节已核对）。

### 8.5 现场状态与回退源

- 当前 `boot_a` 为 DSC 候选（内核 `d6ab94b8`）；回退源齐备：
  r1 引导镜像 `artifacts/server-r1-20261003/ready/boot-server.img`
  （SHA-256 `f899299d2b9663e78f0e0553e26d99d95170a4b3ed2f557778abd8d9f78450d5`）与原厂
  `backups/partitions-20261003-152513-342/boot_a.img`。
- 全程只写 `boot_a`，未动 super/dtbo/槽位；测试用临时模块已卸载，`vreg_l14a_1p8` 已回到 0 用户。
- 面板当前仍显示 ABL 画面或测试画面；`/tmp` 为易失目录，脚本与镜像在重启后需重新拷贝。

## 9. 黑屏修复生效（2026-10-04 第 6 轮续，用户实机确认）

### 9.1 根因

黑屏由两处驱动缺陷叠加造成，两处都修才点亮：

1. **DSC 打包与面板要求不一致**：厂商 dtbo 节点
   `qcom,mdss_dsi_oplus20828samsung_amb655x_1080_2400_cmd` 声明
   `qcom,mdss-dsc-slice-per-pkt = <2>`，而驱动把 `dsi->dsc_slice_per_pkt` 留 0，
   msm 退回 1 打包（`bytes_per_pkt = slice_chunk_size * 1`），面板无法解码。
2. **面板复位 GPIO 从未被驱动**：驱动既不申请 `reset-gpios`（probe 里只有一段说明注释），
   也不发复位脉冲（`samsung_amb655x_reset()` 里三行 `gpiod_set_value_cansleep()` 全被注释）。
   ABL 交接后面板停在 GRAM 自刷新状态，继续显示 ABL 留下的开机画面，不接受内核的初始化序列与帧。

只修第 1 处不够（第 8.2 节：DCS 读仍超时、`frame_done_cnt` 涨到 217、屏幕仍停在 ABL 画面）；
补上复位脉冲后面板立即开始应答。

### 9.2 最终部署

- 内核提交 `746acf8f065e…`（在其上是 `d6ab94b80a11…` 的 DSC 修正），对应补丁
  `patches/local/0003-drm-panel-amb655x-dsc-slice-per-pkt.patch`（SHA-256 `59ed5109…cc19a`）与
  `patches/local/0004-drm-panel-amb655x-drive-reset-gpio.patch`（SHA-256 `824bb7c0…5a221f`）。
- `boot_a` 写入引导镜像 45,617,152 字节，SHA-256
  `ccacb17a809fedd3f2912ec9961e1324962c11564a9514373d3fe536a8c2b3dd`（复用已部署 r1 的 initramfs 与
  cmdline `root=UUID=bc52fbbe-…`），按同一字节范围回读哈希一致；只写 `boot_a`，super/dtbo/槽位未动。

### 9.3 修复后证据

- **面板开始在 DSI 返回通道应答**：`/sys/class/backlight/ae94000.dsi.0/actual_brightness`（驱动内 DCS 0x52 读）
  由修复前的 `Connection timed out` / `No data available` 变为正常返回 **39936**，亮度寄存器 4095。
- **帧正常完成**：`encoder-0/status` 显示 `vsync` 持续递增、`underrun 0`、**`frame_done_cnt: 0**`，
  不再出现 `frame done timeout` 与 `failed wait_for_idle ret:-110`。
- **用户实机确认**：用户态 `mmap(/dev/fb0)` 写入"白底 + 中间红条"后，用户确认看到该画面。
- **完整桌面栈**：同一内核上有界 XFCE 会话启动成功，`DSI-1 connected 1080x2400+0+0`、
  唯一模式 `1080x2400 120.00*+`，`kebab-xfce-test.service` active，Xorg 单实例运行。

### 9.4 仍未修复的独立问题

- fbdev 的 `framebuffer is not in virtual address space` 告警仍在（msm fbdev 缺 `FBINFO_VIRTFB` 标志），
  但已核实是虚警、`sys_*` 绘制照常执行；真正让"启动时看不到控制台文字"的是 VT 状态：X 退出后
  `tty7` 仍为活动 VT 且其上没有控制台，`chvt 1` 在本环境未生效，需另行排查；
- 面板 `vddio-supply`（`vreg_l14a_1p8`）仍未在驱动中申请，运行时用户计数 0；
- 驱动只暴露 120 Hz 模式（`samsung_amb655x_mode` 为 `__maybe_unused`）；
- 启动期 `DSI PLL(0) lock failed status=0x00000000` 与 `clk_core_disable/unprepare` WARNING 仍在
  （运行期 PLL 实测已锁定）；
- 触摸仍未按真实手指操作验收；
- 桌面会话当前以有界 transient 单元运行，LightDM 仍禁用、默认目标仍为 `multi-user.target`。

### 9.5 回退

`boot_a` 回退源：r1 引导镜像 `artifacts/server-r1-20261003/ready/boot-server.img`
（`f899299d…78450d5`）或本轮 DSC 候选（`5bfa275b…f49270c`）；原厂引导镜像在
`backups/partitions-20261003-152513-342/boot_a.img`。回退只需重写 `boot_a`。



