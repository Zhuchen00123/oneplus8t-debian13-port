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
