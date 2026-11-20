# 关键发现：插 USB 供电开机 → Android 进 charger 模式（2026-11-20）

## 现象
运动世界相关实验在 11/15 记录过 `framework_boot_completed=false`，此前归因不明。本轮 t1 空窗验证复现：
无头 Weston + Waydroid 容器能起、ADB 能连，但 Android 永远不完成启动（等 120 s 仍为空）。

## 根因（真机实测）
宿主 `/proc/cmdline` 带：
- `androidboot.mode=charger`（出现两次）
- `androidboot.startupmode=dc_charger`

手机是在 USB 供电状态下开机的，所以内核把这两个令牌传给了容器里的 Android。
Android init 据此把 `ro.boot.mode` 取成 `charger`，**只执行 `(charger)` 动作，永不 queue `late-init`**
⇒ 没有 vold / zygote / SurfaceFlinger，`sys.boot_completed` 恒空。
dmesg 证据：`processing action (charger)` 之后彻底静默。

## 处理（容器级、可回退、不写持久配置）
`up.sh` 生成 `/srv/kebab-re-20261120/work/lab-cmdline`：删掉两份 `androidboot.mode=charger` 与
`androidboot.startupmode=dc_charger`，保留 `androidboot.mode=normal` 等其余参数；
再 `lxc.mount.entry = <lab-cmdline> proc/cmdline none bind,create=file,optional 0 0` 绑进容器。

**坑**：该 bind 必须可写——Android 13 first-stage init 会 `chmod("/proc/cmdline", 0440)`，
只读 bind 会触发 `InitFatalReboot(signal 6)`（窗口 6 现场，dmesg ts 85208）。

## 结果
修后空窗 16 秒到 `sys.boot_completed=1`（Android 13 / sdk 33），整窗 68 秒，收尾零残留。
后续在不插电状态重启时 cmdline 不再含 charger，该修法与真实 cmdline 等值，无副作用。

## 影响
11/15 的 `framework_boot_completed=false` 很可能就是同一个原因，而不是 Waydroid/内核坏了。
本发现不改变"服务器优先"与不刷写、不重启的边界。
