# 主线 Linux 启动准备

日期：2026-10-03，Asia/Shanghai。用户已结束屏幕取证，当前目标是准备 KB2000 的 Debian 13 主线 Linux 启动与后续刷写。

后续用户已确定以 7.2 为主并用于 Linux 服务器，固件和持久系统的主机进展见 [服务器移植](debian-server-port.md)。下文保留最初 RAM 救援候选的限制与首次启动授权流程；本机 Linux 首启仍未执行。

**本轮仅完成主机准备和 ADB 只读复核。未临时启动 Linux、未安装 TWRP、未刷写/擦除/切槽。** 内核仍是社区 `7.2.0-kebab-debian13+`，不是 Android 厂商内核，也不声称 kebab 已完整合入官方主线。

## 准备状态

| 项目 | 当前证据 |
|---|---|
| 本机 | KB2000、ColorOS 14.0.0.602、slot a、unlocked/orange、UID 2000、临时 su 已退出 |
| 内核/DTB/模块 | 原固定构建 `f6ac9e9c86d0c92ef6db1c4f5a4c3ccdc77d1d5a`；578 模块版本一致 |
| 原厂 boot | 匹配 OTA 的 boot 与 boot_a 备份相同；header v2、4096 页、全部地址已解析核验 |
| RAM initramfs | Debian arm64 BusyBox/Dropbear、8 个匹配 USB 模块及依赖；仅 RAM，不挂载手机分区、不启动 DSP/蜂窝服务 |
| SSH | 镜像只有公钥，禁用密码和端口转发；每次启动在 RAM 生成 host key |
| Debian 基础 rootfs | 13.7 arm64，133 已安装包，137 份认证 deb 输入保留；含同组内核、DTB、578 模块与 USB/SSH 配置 |
| 原厂备份 | 26 份，共 9,154,465,792 字节；本轮主机再次逐项复核大小和 SHA-256 全通过 |
| 阶段备份 | `backups/linux-preparation-20261003-175305-853/`，36 文件，源/副本校验通过，含原厂 boot_a/dtbo_a/recovery_a 恢复副本 |

基础 rootfs 是文件系统归档，**不是可直接刷到 super 的分区镜像**。安装位置、UUID、持久系统 boot/initramfs 尚未确定；本机固件部署、ALSA UCM、QRTR/rmtfs/tqftpserv/pd-mapper 等未集成。不能称为完整可用的 Debian 手机系统。

仅 `artifacts/boot-preparation-20261003/rescue-final/` 为最终救援候选；rescue/v2/v3 目录为过程记录，不部署。

## 最终产物

以下路径相对 `artifacts/boot-preparation-20261003/`。

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| `rescue-final/boot-rescue.img` | 18,751,488 | `f339e7d993c8c4d3bf6527e706c4219c0d6da7d07bd569331cba54def057260f` |
| `rescue-final/initramfs-rescue.gz` | 3,322,872 | `a4173b0f515310b0630dc099b5fe919c015940fee97a5ee4c0d31393ff234dee` |
| `rescue-final/dtbo-empty-community.img` | 284 | `08f1551f1dd69a5799d3440c0dff7c183cbadad0764dd0ef7a21a1757661077f` |
| `debian-base/debian13-arm64-base.tar.gz` | 220,764,405 | `eade8fcd2fb5ef3b9afe69c10048e70cc4357590ac02efa13363bf69c41ba3ee` |
| `debian-base/authenticated-packages.tar.gz` | 53,771,285 | `a3245ab22cb6c852cc293226bd52b14c24b3226badd97a3951548ec68de74464` |

完整分组、打包参数和限制见 [boot 清单](../artifacts/boot-preparation-20261003/rescue-final/boot-manifest.json)、[rootfs 清单](../artifacts/boot-preparation-20261003/debian-base/rootfs-export-manifest.json)、[本机 boot 参数](../config/boot-kb2000.json)。

实测 base=0、kernel=0x8000、ramdisk=0x01000000、tags=0x100、dtb=0x01f00000。保留原厂头内 12.0.0 / 2024-08 编码，它不是 Debian 版本。候选 cmdline 为 `console=tty0 loglevel=7 ignore_loglevel panic=15 rdinit=/init`，不沿用 Android 私有 cmdline。v2 地址与原厂一致，AOSP 独立解包的内核/DTB/ramdisk 与输入逐份相同。[AOSP 格式](https://source.android.com/docs/core/architecture/bootloader/boot-image-header)

## 原厂 DTBO 是独立问题

原厂 dtbo_a/匹配 OTA 含 19 个条目；逐个使用 libfdt `fdtoverlay` 应用到当前主线 DTB 全部失败，诊断包括 `FDT_ERR_BADOFFSET` 和缺少 `/__symbols__`。表、条目和日志只存 `local/stock-dtbo-mainline-probe-20261003/`。

不能据此默认原厂 DTBO 可复用。ABL 的条目选择、临时 `fastboot boot` 是否加载原厂 DTBO、覆盖失败时的行为均未验证；离线结果不能单独证明手机一定无法临时启动或一定会绕过 DTBO。

固定社区 APKBUILD 将 `sm8250-oneplus-kebab-empty.dts` 通过 dtc 编成原始无操作 DTB，安装名为 dtbo.img；不是自行发明的“零条目 Android DTBO 表”。本轮从锁定 Git 对象读取原文件并核对 APKBUILD SHA-512；生成的独立候选离线覆盖后板级树不变，保留提交及 MIT 来源。[固定社区配方](https://github.com/jfbobier/Oneplus-8T-kebab-pmos-bringup/blob/2e5e065da8ea8d242ecc34bfe064b88257797aa9/device/oneplus-kebab/APKBUILD)

**社区空 DTBO 在本机 ABL 上未验证，未写入。** 生成不代表已授权更改 dtbo_a；更改后原厂 Android 也可能不能启动，回 Android 前要先恢复原厂 DTBO。

## TWRP 的取舍

用户的“bwrp”按可能指 TWRP 处理，未解释为安装授权。TWRP 不是 ABL 加载主线内核的必要依赖；本机已有原厂 recovery/fastbootd 及 recovery 副本。

2026-10-03 官方目录最高仍是 `twrp-3.7.0_11-0-kebab.img`（2022-10-04）。未验证其在 **KB2000 / ColorOS 14.0.0.602** 的兼容性，网页更新日期不能证明镜像已适配 Android 14。官方说明 8T 有独立 recovery，永久安装会改该分区；动态分区的单独刷写需 fastbootd。[官方设备说明](https://twrp.me/oneplus/oneplus8t.html)、[实际下载目录](https://dl.twrp.me/kebab/)

先保留原厂 recovery。若后续确需恢复环境，再核验可信镜像、固件版本并取得对应授权，先临时启动测试兼容性，再决定是否安装。原厂 recovery 的完整恢复能力同样未验证，不能当作全盘救砖保证。

## 首次实机方案（尚未授权或执行）

第一步建议仅临时加载 RAM 救援 boot，不改分区。执行前重新核对机型、构建、slot、root 退出、候选 hash、恢复副本和电量，并确认可按键返回 Bootloader。进入后只读核验 product、current-slot、unlocked、max-download-size；须仍对应 KB2000/kona、a、unlocked 且下载上限足够。状态改变则重新准备。

以下仅为取得这一步明确授权后的手动流程：

```powershell
$Serial = (Get-Content ./local/linux-boot-preflight-20261003.json -Raw | ConvertFrom-Json).serial
adb -s $Serial reboot bootloader
fastboot -s $Serial getvar product
fastboot -s $Serial getvar current-slot
fastboot -s $Serial getvar unlocked
fastboot -s $Serial getvar max-download-size
# 再次确认状态及候选 hash 后：
fastboot -s $Serial boot ./artifacts/boot-preparation-20261003/rescue-final/boot-rescue.img
```

成功须进入 `/init`、USB gadget/ACM 出现、SSH 命令可达并采到 kernelrelease/DTB/UFS 枚举日志。Fastboot Booting OKAY 本身不代表 Linux 已启动。失败先返回 Bootloader/重启原厂 Android；第一步没有计划中的分区写入，**不自动刷恢复镜像**。若仍异常，保留实际症状再调查。

若原厂 DTBO 阻挡，另行批准第二步：只将上述社区候选写到当前 **dtbo_a**，再临时加载同一 boot；不刷 boot、不切槽、不改 vbmeta/recovery/super/userdata。必须先实测按键返回 Bootloader，确认槽位仍 a，并校验下列副本。

| 恢复对象 | 字节数 | SHA-256 |
|---|---:|---|
| `stock-restore/dtbo_a.img` | 25,165,824 | `3e4a4aadeed79d5293bb2bfa655fafb7d40f7cb5ee0b1fd67bf20e1c02792706` |
| `stock-restore/boot_a.img` | 100,663,296 | `cb4e546fe00141cb315cdaf22980bcba6a96e82d6365350311697f9579816991` |
| `stock-restore/recovery_a.img` | 104,857,600 | `6286ad199599cf15fc4a9634d213432985167ed05dd2cf59e2c57e8a20f59072` |

副本位于 `backups/linux-preparation-20261003-175305-853/`。第二步更改 DTBO，所以实验后**先在 Bootloader 恢复原厂 dtbo_a，再 reboot 回 ColorOS**，不能承诺直接重启即恢复 Android。恢复 DTBO 也须包含在第二步授权范围和日志中。boot/recovery 未改，不安排无故重刷；若不能可靠回 Bootloader，不进行第二步。

super、userdata 或其他 rootfs 布局是更后的独立方案。备份未包含全盘/GPT/全引导链，未验证跨分区原子一致或完整恢复；同 F 盘副本需另存独立介质，另一 boot 槽不能保护 super 布局。

## 连接与测试

RAM 端 `192.168.8.1/24`，RNDIS + ACM；电脑 USB 网卡计划为 `192.168.8.2/24`，无默认网关。Windows 驱动识别待实机测试，本轮没改主机网络。实际用户拥有的私钥为 `local/ssh/kb2000-linux-rescue`，不提交/上传/嵌入镜像；沙箱用户生成的旧 preflight key 不部署。

首次先用非交互 SSH 采日志，核对/接受本次 RAM host key 指纹后：

```powershell
ssh -T -i ./local/ssh/kb2000-linux-rescue root@192.168.8.1 "uname -a; cat /proc/partitions; cat /run/usb-gadget.log; dmesg"
```

原始输出含硬件信息，仅保存 `local/`。USB `/dev/ttyGS0` 物理串口 shell 和显示控制台均未实机验证。实验 shell 不自行挂载 Android 分区、改 NV 或执行存储写入。

QEMU 已通过 ARM64 程序执行、脚本语法、8 个 USB 模块依赖、正确公钥远程命令、错误密钥拒绝和禁用密码。**额外交互 PTY 探测在 QEMU/chroot 失败**（TIOCSCTTY、/dev/pts 路径诊断）。init 已包含 devpts，但不能宣称交互终端通过；失败日志保留，实机首轮使用非交互 SSH，PTY 待真实内核检查。QEMU user-mode 不是 SM8250/ABL/UFS/USB/显示硬件仿真。

5 个 AOSP 实际打包正/负例通过：v2 round-trip、载荷损坏、错误地址、截断 DTB、错误 header。主机测试不等同实机成功。

## 复现入口

Linux 工作目录 `/home/zhuchen/oneplus8t-debian13-20261003`；rootfs prepare/finalize 使用主机 root，不在 Windows 文件系统生成 Linux 文件树。

- `prepare-debian-rootfs.sh`：签名验证、arm64 debootstrap + QEMU、deb 保留、密钥登录，要求全新目录。
- `build-rescue-initramfs.py`：核验原内核清单，仅提取并解压匹配 USB 模块，复制 ARM64 依赖/公钥。
- `build-boot-candidate.py`：核验本机 profile，AOSP 打包/解包，成组保存全部内核产物，按 Git 对象生成单独空 DTBO。
- `inspect-stock-dtbo.py`：主机覆盖诊断。
- `finalize-debian-rootfs.py`：安装同组模块并导出归档，不选择手机分区。
- `Backup-LinuxPreparation.ps1`：先核验全部源，再复制并复核成组备份，无手机命令。

构建工具在 `scripts/linux/`，备份工具在 `scripts/`。版本见 `sources.lock.json` 和产物清单。官方软件源签名/下载内容已验证，旧包和 security 升级包都保留；不宣称以后下载的仓库内容相同。没有运行社区部署脚本，没有发维护者消息、建立远程或推送。
