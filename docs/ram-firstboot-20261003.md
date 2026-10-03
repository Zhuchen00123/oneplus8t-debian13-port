# Linux 7.2 RAM 首启记录

日期：2026-10-03。目标 KB2000 / ColorOS 14.0.0.602；只执行用户明确授权的一次临时启动，不写分区。

## 实际结果

救援候选 `artifacts/boot-preparation-20261003/rescue-final/boot-rescue.img`，18,751,488 字节，SHA-256 `f339e7d993c8c4d3bf6527e706c4219c0d6da7d07bd569331cba54def057260f`；执行前再次校验，原厂 boot_a/dtbo_a/recovery_a 恢复副本 hash 也重新通过。

设备起始为 KB2000、原厂构建匹配、A 槽、unlocked/orange、UID 2000、无临时 su、电量 100%。ADB 重启至 Bootloader，指定设备实读 product=kona、current-slot=a、unlocked=yes、is-userspace=no、max-download-size=805306368 字节。

一次 `fastboot boot` 返回：Sending OKAY（0.399s）、Booting OKAY（0.092s），退出码 0。随后 Windows 仍枚举 Android Bootloader Interface，未新增 Linux USB 网络或 ACM；指定设备的 getvar 仍成功。用户确认屏幕一直是 Fastboot 界面。

因此本次 **Linux 启动未验证成功**，没有获得 `/init`、uname、SSH 或 UFS 日志。不能把 Fastboot OKAY 当作内核运行证据，也不能由此确认内核是否执行过早期指令。

执行 `fastboot reboot` 返回原厂 Android，实读 sys.boot_completed=1、构建 KB2000_14.0.0.602(CN01)、slot=_a、unlocked/orange、UID 2000、PATH 无 su、debug_ramdisk/su 不存在。没有 flash/erase/set_active，没有刷 DTBO/boot/super/recovery/vbmeta，没有调整电脑网络。

原始日志仅保存在 `local/linux-ram-firstboot-*/`，运行目录指针 `local/ram-firstboot-current.json`。Android 普通 shell 无权列出 `/sys/fs/pstore`，不能声称取得崩溃日志；不为此扩大先前临时 root 的授权。

## 已复核与仍未知

候选 header v2、4096 页、base 和各加载地址与本机原厂以及固定社区 deviceinfo 一致；DTB 含社区板级 qcom,msm-id / qcom,board-id。封存候选的 AOSP round-trip 与模块字节核验已通过，没有因本次失败修改候选。

社区安装步骤要求 flash_dtbo，固定 APKBUILD 将 empty.dts 编成 284 字节原始 DTB；本机原厂 19 个 DTBO 条目离线覆盖到此主线 DTB 全部失败。这使 DTBO/ABL 路径成为值得单独控制变量验证的候选问题，**尚不能确认是此次失败根因**。启动参数、ABL 选择/加载方式与早期内核故障仍未排除。[固定社区安装步骤](https://github.com/jfbobier/Oneplus-8T-kebab-pmos-bringup/blob/2e5e065da8ea8d242ecc34bfe064b88257797aa9/README.md)、[DTBO 配方](https://github.com/jfbobier/Oneplus-8T-kebab-pmos-bringup/blob/2e5e065da8ea8d242ecc34bfe064b88257797aa9/device/oneplus-kebab/APKBUILD)。

## 可审阅的下一步：单独 DTBO 对照实验，未授权

本次“不写分区”授权不涵盖以下流程；取得新授权前仅准备，不执行。也不把这份计划当成因果证明。

1. 先确认并实际验证用户能从异常状态按键返回 Bootloader。当前通过软件重启成功回原厂，不等于已验证异常状态的按键恢复。无法可靠回 Bootloader则不开展写入实验。
2. 重新核验原厂构建、A 槽、解锁状态、下列候选和恢复副本、手机分区名称及容量。使用同一指定序列号。
3. 唯一试验目标为 `dtbo_a`：写入社区空 DTBO，然后临时启动同一个已封存 RAM 救援 boot；不刷 boot，不切槽，不写 super、vbmeta、recovery 或 userdata。社区原始 DTB 是否被本机 ABL 接受未验证。
4. 若 Linux 可达，仅做 USB/SSH/内核/UFS 枚举；不挂载存储，不部署持久 rootfs。若失败，保存症状并返回 Bootloader。
5. **回 Android 前先在 Bootloader 恢复原厂 dtbo_a**，然后 reboot 并核验原厂启动。授权必须同时覆盖实验写入和恢复写入。若无法恢复，停止进一步写入并报告实际状态；原厂 Android 可启动性不能在恢复 DTBO 前承诺。

| 对象 | 路径 | 字节数 / SHA-256 |
|---|---|---|
| 试验 DTBO | `artifacts/boot-preparation-20261003/rescue-final/dtbo-empty-community.img` | 284 / `08f1551f1dd69a5799d3440c0dff7c183cbadad0764dd0ef7a21a1757661077f` |
| 原厂 DTBO 恢复 | `artifacts/stock-recovery-20261003/dtbo_a.img` | 25,165,824 / `3e4a4aadeed79d5293bb2bfa655fafb7d40f7cb5ee0b1fd67bf20e1c02792706` |

boot_a、recovery_a 和完整原厂 super 恢复素材也在独立恢复组，未改这些分区时不安排无故重刷。备份未包含全盘/GPT/完整引导链，仍在同盘，完整实机恢复未验证；不能描述为完整救砖保证。

项目 [AGENTS.md](../AGENTS.md) 要求：“刷写/擦除任何分区……必须在列明目标、备份和恢复方案后取得明确授权。”本步骤会写 dtbo_a，故必须单独申请，不受首次 RAM 启动授权覆盖。
