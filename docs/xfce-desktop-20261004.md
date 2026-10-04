# XFCE 最小桌面安装与显示路径验证（2026-10-04）

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

来源是 `drivers/video/fbdev/core/syscopyarea.c`、`sysimgblt.c`、`sysfillrect.c` 对 `screen_buffer` 为空时的告警：msm 的 DRM fbdev 模拟没有把缓冲映射进内核虚拟地址空间，所以 **fbcon 永远画不出启动日志**；加上控制台默认 600 s 后消隐（观察到 `blank=4`），即使面板正常也不会看到控制台内容。这条与面板链路无关，但必须连同修复。

### 7.6 当前结论与下一步候选实验

结论：**GPU/合成/回读正常，DPU 在出帧，PLL 锁定，面板供电已可强制打开，但面板既不回 DCS 也不回 TE、屏幕无任何可见输出。** 故障位于 DSI 物理链路或面板上电/初始化时序一侧。

候选实验（都需要重新构建内核并部署 boot_a，仍在持续授权范围内，执行前会单独确认目标与回退）：

1. 面板驱动按厂商时序处理供电与复位（取 `vddio`、驱动 `reset-gpios`、加足上电延时），并在 `prepare` 后读一次 DCS ID（0x04/0xDA）把链路是否通变成可判定证据；
2. 用备份里的原厂 `dtbo_a` 反编译对齐 lane 映射/时序/供电（`data-lanes`、lane swap、panel 节点属性）；
3. 暴露并测试驱动的 60 Hz 模式（当前 `samsung_amb655x_mode` 标记 `__maybe_unused`，只暴露 120 Hz），排除 800 MHz PLL 速率相关问题；
4. 修复 fbdev 缓冲映射，使控制台能显示，作为"面板是否真的能显示"的独立判据。

### 7.7 现场状态

测试模块已卸载、`vreg_l14a_1p8` 恢复 0 用户、桌面临时单元停止、LightDM 保持禁用、默认 `multi-user.target`、SSH 正常、控制台取消消隐；本轮无分区刷写、无触摸固件/NV/持久配置改动。

