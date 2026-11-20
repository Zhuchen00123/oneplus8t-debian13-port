# 运动世界 7.4.01 真机实测授权记录（2026-11-20）

> 本文记录用户对本轮真机工作的**明确授权与旧约束解除**，用于替代旧实验目录里的 `REVIEW_HOLD` 与 `waydroid-container` 的 mask 封锁。旧实验的脚本与规矩**不再作为本轮的准入条件**；本轮用全新工作树（`/srv/kebab-re-20261120`）重写执行链。

## 1. 用户授权（原话要点，2026-11-20）

1. 手机已通过 USB 连接电脑（SSH 别名 `kebab`，现役 `r4` 内核）。要求**说人话、聚焦真机实操**，不再做离线 WSL 模拟或空转审计。
2. **放行真机无头（Headless）Waydroid 窗口**：允许在确认基础服务正常后，临时解除 `waydroid-container` 的 mask 与旧 `REVIEW_HOLD` 限制；电量 80% 触发停充属正常，不得当作异常拒止。
3. 用户后续澄清：**旧的实验规矩和封条不用管了**，这些限制不用遵守，可以写进 md（即本文）。
4. 网络：**同局域网 + 临时降级 USB 路由**（手机与电脑同一网段，不换 SSID）。

## 2. 旧约束的当前状态

| 旧约束 | 状态 | 说明 |
|---|---|---|
| `/srv/kebab-sportsworld-lab-20261115/HOLD`（独立审核前禁止执行） | **作废** | 不再阻塞本轮；收尾**不需要**恢复。旧脚本不复用，旧 HOLD 目录保持原样即可。 |
| `waydroid-container.service` 被 `mask` | **解除** | 允许 `systemctl unmask`，收尾**不要求**重新 mask。为避免开机自启占用常驻服务器资源，保持 `disable`（不 enable）。 |
| 旧 launcher 的 journal/ownership/回滚链 | **不复用** | 本轮在新树重写最小 up/down 脚本；不调用被 quarantine 的 `sw-ondevice-*.sh`。 |

## 3. 仍然生效的硬约束（本轮自我约束，非旧规矩）

1. **只用无头 Weston**（`--backend=headless`）；严禁物理屏幕 / DRM / VT 路径。
2. **单窗口 ≤300 秒**：用 `systemd-run --property=RuntimeMaxSec=300 --property=KillMode=control-group` 硬超时。
3. **窗口收尾**：停容器（`waydroid container stop` / `lxc-stop -k`）、卸载 `/var/lib/waydroid` 绑定、杀掉 weston；不留后台进程。
4. **不重启设备、不刷写分区/GPT、不改持久网络配置**；常驻服务器服务（SSH / 限充 / 温控 / modem / GPS / SSC）不得受影响。
5. 根分区只剩约 166 MB，**所有新文件只写 `/srv/kebab-re-20261120`**。
6. 网络改动仅限窗口内临时降级 `usb0` 默认路由（让 `wlan0` 的 `192.168.1.1` 接管）与临时 DNS，收尾按前像还原；不得影响 USB SSH。
7. 结论必须来自真机命令输出，逐条给证据路径；失败/阻塞如实标注，不把推断写成实测。
8. 不下载新的 Frida/二进制；只用设备上既有的、此前审计过的资产；找不到即报 BLOCKED。

## 4. 工作树与证据

- 工作树：`/srv/kebab-re-20261120/{work,userdata,runtime,bin,logs,evidence}`
- 镜像：`/srv/waydroid-r4-lab-20261112/images/{system.img,vendor.img}`
- 旧用户数据（只读复制源）：`/srv/waydroid-r4-user-20261112/data`（含已安装的 `com.zjwh.android_wh_physicalfitness`）
- 团队：`kebab-sportsworld-re-20261120`（device / bpf；7 任务 DAG）

## 5. 边界

本授权只覆盖：无头 Waydroid 单实例、运动世界 7.4.01 冷启动与三步实测（/proc 取证 → 纯内核 tracepoint → 宿主 eBPF TLS）、以及 r4 短板与 r5 清单。
**不覆盖**：物理显示/DRM/VT 试验、多容器/Redroid、持久刷写换核、关闭 TLS 校验、伪造定位/步数、提交业务成绩。
