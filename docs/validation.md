# 验证记录

日期：2026-10-03。

## Linux 7.2 DTBO 对照成功及回退

- 独立授权后仅写社区无操作 dtbo_a，再临时启动同一救援 boot；ACM 与 USB SSH 实读 7.2.0-kebab-debian13+、OnePlus 8T DT 模型、UFS 分区。super=sda15，7,516,192,768 字节。未挂载或进行手机存储内容 I/O。
- RNDIS EF/04/01、bcdDevice=0102、网络接口先于 ACM 的 RAM 修正使微软内置驱动正常工作；公钥 SSH、严格 host key 验证和远程日志采集成功。主机静态 IP 设置被 Windows 权限拒绝，实际使用链路本地地址；DHCP 未改。
- 用户拔下 USB 后从 Linux 按键返回 Fastboot 成功。原厂 24 MiB dtbo_a 先恢复、Fastboot 写入 OKAY，随后 ColorOS 完成启动、A 槽、unlocked/orange、无临时 su。原厂输入 hash 已核验，设备刷后 hash 回读未验证；其他分区未刷。
- dmesg 被 30,387 条 Synaptics TCM invalid touch report length 消息覆盖，不能据此宣称触摸、早期启动日志或长期稳定性通过。源码 USB 修正尚未进入封存镜像。完整范围与证据入口见 [实机成功记录](ram-boot-success-20261003.md)。

## Linux 7.2 首次 RAM 实机尝试

- 用户明确授权一次 `fastboot boot`，限定不写分区。救援镜像与 boot_a/dtbo_a/recovery_a 恢复副本再次校验通过；设备 KB2000、ColorOS 14.0.0.602、A 槽、unlocked/orange、无临时 su、电量 100%。
- Bootloader 实读 product=kona、current-slot=a、unlocked=yes、is-userspace=no、max-download-size=805306368（十进制字节）。候选 18751488 字节、SHA-256 f339e7d993c8c4d3bf6527e706c4219c0d6da7d07bd569331cba54def057260f。
- 一次临时 boot：Sending OKAY 0.399s、Booting OKAY 0.092s；随后 getvar 仍可访问 Bootloader。用户确认屏幕一直为 Fastboot，主机没有新增 Linux RNDIS/ACM。不能宣称进入 Linux、SSH 可用或 UFS 已枚举。
- 执行 `fastboot reboot`，ADB 核验 sys.boot_completed=1、原厂构建匹配、slot=_a、unlocked/orange、UID 2000、临时 su 不存在。原厂恢复已实测；本次没有 flash/erase/set_active 或主机网络配置。
- 无 root 状态下 `/sys/fs/pstore` 列表 Permission denied，未读取内核日志。未确认失败原因，不能仅凭原厂 DTBO 离线不兼容宣布 DTBO 为根因。详细记录见 [RAM 首启](ram-firstboot-20261003.md)。
- 首启失败后仅做主机排查：固定公开 Qualcomm 衍生 ABL 参考提交 7d391d8b1059ed48259728cab004ca6f16ee3de2，确认参考 CmdBoot 在 BootLinux 前回复 OKAY。按参考 v2 公式直接读取候选头，DTB 偏移 18,616,320 处实读 FDT magic，131,941 字节 DTB 在镜像内；候选 hash 不变。参考源码与 KB2000 OEM ABL 的等价性未确认，不宣称定位实机根因。

## 实机

- ADB 已授权；机型 KB2000、Android 14 / ColorOS 14.0.0.602 已实读。
- 解锁前 flash.locked=1、vbmeta.device_state=locked、verifiedbootstate=green、slot=_a 已实读。
- SurfaceFlinger / DisplayManager 报告 1080×2400、60/120 Hz。
- 初次普通 ADB 阶段面板型号/序列号、设备树和分区读取均被系统拒绝；当时原厂屏身份与原厂分区备份未完成。
- 用户确认清空数据后执行 fastboot oem unlock；重新连接实读 flash.locked=0、vbmeta.device_state=unlocked、verifiedbootstate=orange、slot=_a，ColorOS 正常启动，Bootloader 解锁已验证。
- 解锁后 ADB 仍为 UID 2000，8 个面板/显示/命令行节点仍 Permission denied；屏幕身份未确认。补充节点读取遭遇 device not found，单独记录为连接中断。
- 用户重新开启 USB 调试后 ADB 恢复，完整复查 16 项：9 项 Permission denied、7 项 No such file or directory，无成功面板读取；再次验证 unlocked/orange。最新原始证据与 SHA-256 清单位于 `local/device-20261003-143138-003/`。
- 最新证据的 9 个文件 SHA-256 复核通过，并与解锁原始记录一同复制到 `backups/device-evidence-20261003-143248/`，共 22 个文件；证据副本再次核验。此备份不包含分区镜像，未提交或上传隐私日志。
- 解锁阶段结束时尚未执行 root、刷写镜像、切槽或重新锁定；后续授权的临时 root 实测另见下文。
- 解锁前保存 250 个可读取共享文件，共 13,499,706 字节；19 项补充复制均成功，250 个主机端 SHA-256 再次复核通过。应用私有数据和原厂分区不在该备份中。
- 临时权限准备阶段：原厂 fastbootd 不支持 fetch；已按范围从匹配指纹的官方 OTA 提取 boot/recovery/dtbo/vbmeta，操作数据和输出 manifest SHA-256 全部通过。官方 Magisk v30.7 在本机 UID 2000 工作目录生成候选，手机/主机 hash 相同，结构对比确认仅 ramdisk 改变、原厂内核/DTB/命令行等保留；此时尚未启动候选或验证 root，之后的实测如下，详见 temporary-root-plan.md。
- 后续实测：官方管理应用安装成功；候选两次 fastboot boot 均返回 OKAY，系统启动完成并出现临时 Magisk su，SELinux 仍 Enforcing。期间一次重启退出临时环境，再次临时启动后 su 恢复；Shell 授权仍退出码 13 / Permission denied，未确认 UID 0，未生成原厂分区镜像。
- Backup-Partitions 增加可选 SuExecutable 参数，以支持本机 `/debug_ramdisk/su` 路径。语法、注入字符串拒绝和 26 个 by-name 只读计划核验通过；当时仍待 Shell 授权，之后真实镜像备份成功流程已验证。
- 用户开启 Shell 授权后，实测 UID 0 / Magisk SELinux 域，SELinux 保持 Enforcing。面板唤醒后读到 AMB655X/samsung1024、DCS `A5 05 01`、有效序列号与 60/120 Hz；依据固定厂商驱动解码生产日期 2020-10-08，完整序列号未提交，结论见 panel-evidence.md。
- SuExecutable 路径的真实二进制备份已通过：26 个所选分区共 9,154,465,792 字节，每项设备前后/主机 SHA-256 相同、大小正确，最终状态 verified_requested_partitions。boot_a 与对应官方 OTA boot 相同；跨分区原子一致性与真实恢复仍未验证。
- root 面板证据及厂商解码源码副本已保存至 `backups/panel-root-evidence-20261003-154203/`，最初 8 个文件，随后加入 2 个退出 root/设置恢复记录，最终 10 个文件。副本逐项核验。原始序列号、分区镜像、校准/NV 与所有隐私日志保持忽略，不上传。
- 本机固件 tar 467,082,752 字节、2,601 条目，设备前后和主机 hash 相同、tar 列表正常；`backups/firmware-20261003-154349/` 的副本 hash 再次匹配。逐文件签名及主线兼容性未验证。
- 诊断结束后恢复 USB 亮屏原值 0，并正常重启。最终 ColorOS 启动完成、UID 2000、PATH 无 su、`/debug_ramdisk/su` 不存在，Bootloader 仍 unlocked / orange、slot `_a`，临时 root 退出和设置恢复已实测。
- 厂商公开序列号实现的末尾保留字段存在读取范围问题，日期字段仍在有效范围；未把完整 64 位返回值当作可信唯一硬件身份或原厂认证，限制记入 panel-evidence.md。

## 来源与主机

- Git 2.51.0.windows.1；PowerShell 7.6.5；ADB 37.0.0。
- Debian 13 WSL 已存在；初始缺少内核编译工具。
- 社区最高 SM8250 tag 7.2.0 和最新主线 RC 7.3-rc5 已通过实际远程 Git refs 核对。
- 两个来源仓库已下载并固定提交。Windows checkout 有内核大小写文件冲突，因此只从 Git 对象生成 Linux 工作树，不能用该 Windows 工作树构建。
- 原 WSL 代理端口不可用，清华镜像包下载返回 403；使用本次命令独立的官方 Debian 源与 DIRECT 选项成功安装 Clang/LLVM 19.1.7、Make、DTC、pahole 和开发库，保留原有源配置。
- 临时目录在 WSL 重启时被清理，已改用 `/home/zhuchen/oneplus8t-build` 持久构建目录。输入使用 Git 对象重新检出，保留 Windows 来源工作树。

## 构建、工具与快照

- 23 个社区输入（21 个补丁、DTS、配置）与 APKBUILD 中 SHA-512 全部一致。
- 21 个社区补丁成功应用；另提交整份 DTS。源码 HEAD：`f6ac9e9c86d0c92ef6db1c4f5a4c3ccdc77d1d5a`，共 22 个提交。
- Debian 配置合并成功；TFA9872 未启用、TFA2=m、Venus 禁用。
- DTB 构建成功，SHA-256：`acf27ff2119adf524c5e171f413a2f6cca2b1b253f371380fd134a1ad49695ef`。
- 最终配置 SHA-256：`064fa50f83412466337bc1f72e91ff3414631ba3de1590724aeffa99cdde367a`。
- 正确 kernelrelease 为 `7.2.0-kebab-debian13+`，最终产物目录 `artifacts/7.2.0-kebab-debian13+/f6ac9e9c86d0-064fa50f8341/kernel/`，DTB 独立导出在同级 `dtb/`。首次 kernelrelease 读取早于 Kconfig sync，误标为 7.2.0-gf6ac9e9c86d0；已修正脚本并重新导出，旧目录仅作过程记录。
- DTB 反编译存在地址/尺寸、GENI 多协议节点重号和单子端口的警告，已完整保留日志。尚未执行 dt-schema 绑定验证和实机验证，不宣称结构警告已消除。
- PowerShell 与 Bash 语法检查通过；固定來源 Sync-Sources 在现有仓库上核验通过。
- `tests/Test-HostTools.ps1` 全部通过：4096 字节包含全字节取值的输出完全一致，stderr 分离，超时终止；未提交修改、Unicode 新文件被快照保存，忽略文件被排除，bundle 在全新目录恢复为相同提交和原始内容。
- Get-DeviceInfo 在本机执行成功；分区计划确认 boot_a=sde11、dtbo_a=sde17、super=sda15；实际备份模式在 UID 2000 时按预期提前拒绝，没有生成镜像。
- 初期没有具备权限的设备，原始分区成功读取路径当时未测；后续临时 root 下 26 个分区的真实读取与前后/主机校验已通过，见实机记录。
- 内核 Image、Image.gz、全部模块及 DTB 完整构建成功。最终 ELF 为 ELF64 / AArch64，Image 的 ARM64 启动头 magic 为 ARMd。
- 578 个已暂存模块逐一通过 vermagic 核验，统一为 `7.2.0-kebab-debian13+ SMP preempt mod_unload aarch64`；模块包移除了指向主机构建目录的 build/source 链接。
- Image 46,465,536 字节，SHA-256 `dacddbd0622b19c370d37487f57f71fcf79044caec7897184042459f14dedbe7`；Image.gz 15,284,240 字节，SHA-256 `a8461a0c793b1324b6af051463e528f819fdabc6d3203e6e5012b191de2fa6de`。
- 最终导出包含模块、System.map、配置、build-info、反编译 DTS、ELF header 与模块核验 JSON。kernel/ 和 dtb/ 清单全部输出的 SHA-256 在 Windows 复核通过，二者 DTB 一致。
- 全量编译无 error，社区 `drivers/power/supply/qcom_fg.c` 有两个 missing-prototypes 警告；DTB 反编译结构警告仍保留。未宣称告警已修复或已完成绑定验证。
- 导出按模式分别存放，避免清单互相覆盖；共享 Kbuild 目录加了互斥锁，实际验证并发请求会在 Kbuild 前被拒绝。
- 管理仓库初始化提交 `2fae839` 已完成，23 个文件；仅用单次自动化提交身份，无全局身份变更、无远程推送。
- 管理仓库 Git bundle / 工作树 ZIP 快照已生成并核验；真实源码、固件、日志、分区镜像没有进入 Git。
- 完整内核源码快照 `backups/kernel-20261003-133027-886586819/` 已生成：压缩源码 337,961,632 字节、101,088 个 tar 条目、22 个提交补丁、构建配置与 metadata。gzip/tar 完整性通过，全部文件的 SHA-256 在 Windows 再次核验通过。只含本地已有历史；手机分区不在该备份内。
- 构建产物成组快照 `backups/build-20261003-135806-926/` 已完成，包含 Image、DTB、模块、配置、清单与导出记录；每个副本 SHA-256 与核验清单一致。损坏来源文件的负例测试也通过：在创建备份目录前拒绝。
- 初次内核构建阶段尚未准备 Debian rootfs/initramfs/boot.img；本轮准备进展见下文，实机 Linux 启动仍未执行。
- 普通 shell 初次读取 vendor 固件被拒绝；之后已通过授权的临时 root 取得并校验固件归档，见实机记录。

## 主线 Linux 启动准备（主机阶段）

- 用户结束屏幕取证；未继续读取面板节点。ADB 只读复核 KB2000、ColorOS 14.0.0.602、a、unlocked/orange、正常开机、UID 2000/无临时 su。
- WSL 远程 refs 再查仍为固定 7.2.0 内核/bring-up 提交。本轮未升级或改动已构建内核。
- 本机原厂 boot header v2、4096 页和地址解析成功，与 boot_a 备份 hash 一致；原厂 OS 编码 12.0.0/2024-08 保留，未作为 Debian 版本。
- Debian 13.7 arm64 debootstrap/第二阶段成功；官方 Release 签名与包校验通过，133 已安装包、137 认证输入包（含被 security 更新替代的旧包）归档。rootfs 中 578 个模块逐项 vermagic 再次通过；基础 tar/gzip 完整性通过。尚未集成固件/ALSA UCM/QRTR，未选根文件系统分区或 UUID。
- RAM rescue initramfs 为 ARM64 BusyBox/Dropbear及 8 个完整 USB 依赖模块，压缩模块已解压以兼容 BusyBox。镜像无客户端私钥/固件/NV，不挂载手机存储，密码及转发禁用，host key 在 RAM 动态生成。
- 最终 boot 18,751,488 字节，SHA-256 `f339e7d993c8c4d3bf6527e706c4219c0d6da7d07bd569331cba54def057260f`；地址/版本与原厂一致，AOSP 独立解包内核、DTB、initramfs 的 hash 全相同。最终组只用 `rescue-final/`。
- 原厂 DTBO 19 条单独 libfdt 应用到当前主线 DTB 全部失败，FDT_ERR_BADOFFSET/__symbols__ 诊断只存本地。社区空覆盖候选按锁定 Git 对象、APKBUILD SHA-512 和原始 dtc 配方生成，284 字节；离线覆盖后板级树不变。ABL 选择/临时 boot 路径/空覆盖接受均未实机验证，未刷 DTBO。
- 5 个 AOSP 实际打包正/负例通过：正常 v2、内核载荷损坏拒绝、错误地址拒绝、截断 DTB 拒绝、不支持 header 拒绝。
- QEMU loopback：ARM64 二进制、运行脚本语法、USB 模块依赖、非交互正确公钥 SSH、错误 key 拒绝、密码禁用通过。额外 PTY 探测失败（TIOCSCTTY/pts），保留记录，不标为通过；首次实机采用 SSH 命令采证，交互终端待测。USB/SM8250/ABL/UFS/内核启动没有被 QEMU user-mode 验证。
- 26 个原厂分区共 9,154,465,792 字节主机大小/SHA-256 全量复核通过。`Backup-LinuxPreparation.ps1` 语法及真实 36 文件成组复制/副本 hash 验证通过，保存到 `backups/linux-preparation-20261003-175305-853/`；仍为同 F 盘副本，不代表独立介质或完整恢复实测。
- TWRP 未安装。官方目录最高为 3.7.0_11-0（2022-10-04），未验证 ColorOS 14 兼容性；保留原厂 recovery。
- 成组备份损坏源的负例通过：在创建目标快照前拒绝。主机 mkbootimg/debootstrap/QEMU/cpio/gpgv 的 dpkg 文件校验无差异；无残留 QA 挂载。主机 Git 全局 autocrlf=true，但项目已有完整 LF 规则，本轮原样保留 `.gitattributes`，不改用户全局设置。
- 本轮未临时启动 Linux、未刷写/擦除/切槽/重新锁定、未改 SELinux/主机网卡。镜像与后续恢复/授权范围见 [启动准备](linux-boot-preparation.md)。

## Debian 13 / 7.2 服务器集成

- 用户选择 7.2 主线、参考 jfbobier 社区方式，服务器优先。远程 main/tag 均仍为 `2e5e065da8ea8d242ecc34bfe064b88257797aa9`；保持已有内核及 578 模块，不运行社区刷机或 SSH 部署脚本。
- 最终组 `artifacts/server-port-20261003/ready/`：boot 45,617,152 字节、initramfs 30,189,082 字节、4 GiB ext4 的 sparse 1,092,640,156 字节、文件树 tar 608,974,799 字节。UUID 与 boot cmdline/fstab 一致，AOSP 解包/逐模块 vermagic/5 阶段 e2fsck/sparse 往返 SHA-256 均通过。[完整 hash 与限制](debian-server-port.md)
- Debian 签名包集成 firmware-atheros/firmware-qcom-soc `20250410-2`，7 个公共固件路径齐备；本机归档大小/SHA-256 再校验，ADSP/CDSP/SLPI/GPU zap 共 59 个文件，split ELF header/hash/各分段大小通过。未进行 OEM 签名密码学或实机加载验证。
- 195 个已安装包；USB RNDIS/ACM、networkd/无线 DHCP/resolved、SSH 公钥、QRTR namespace、社区蓝牙地址与可选 UCM/DP helper 已集成。扬声器与蜂窝驱动不自动加载；无 NV/EFS/SIM PIN 自动化，Venus 驱动保持禁用。没有据此声称 Wi-Fi/音频/蜂窝实机可用。
- 6 个 firmware 正/负例及 5 个 AOSP boot 正/负例通过。ARM64 OpenSSH 在私有 mount namespace/QEMU 下正确 key 可执行命令、错误 key 拒绝、密码禁用；QA 私钥和 /proc 挂载已清理。PTY 未测为通过，先前救援 PTY 失败记录仍有效。
- 离线组验收通过：全部文件 hash、initramfs 必要 USB/固件/fsck、原始社区文件 hash、无 SSH host key 或 QA key、空 machine-id/D-Bus 关联、无 Wi-Fi 凭据。构建主机补齐 initramfs-tools-core 0.148.4 和 libsparse 1:34.0.5-12；不改全局软件源。
- 只读手机预检确认 KB2000/ColorOS 14.0.0.602、a、unlocked/orange、UID 2000/无 su、电量 100%；不再读取面板。原始日志仅 local/。本轮没有临时启动 Linux、分区写入、切槽、SELinux 或主机网卡修改。
- 成组备份 `backups/server-20261003-193710-499/` 文件副本 hash 通过，原厂 boot_a/dtbo_a/recovery_a/super 再核对大小/hash 通过。原厂镜像复用既有位置，同盘副本不是独立介质或实际恢复验证。
- 持久 rootfs 的 super 目的地只作方案，尚未获准。先执行有独立授权的 RAM 救援首启；DTBO 与持久分区各自单独授权。

## 首启前模块二进制与原厂恢复格式

- 已对实际服务器 rootfs 的 578 个模块逐文件核对压缩字节；服务器 initramfs 的 200 个模块、RAM 救援的 8 个模块逐一核对原始 ELF 字节，与固定内核组全部相同；6 个必需 USB 依赖齐全。硬件加载仍未测，原 Linux 候选没有被修改。
- `Test-ModulePayloads.py` 9 项通过：usr-merge 路径、旧版本/路径逃逸拒绝、压缩格式变化、同版本数据变更、缺失/重复模块的真实 tar 输入拒绝。
- `prepare-stock-recovery.py` 实际生成 boot_a/dtbo_a/recovery_a 副本及 whole-super sparse 7,481,440,240 字节。独立 framing/尺寸核验通过；simg2img 展开到 7,516,192,768 字节，hash 与原厂 super 一致；原备份转换前后 hash 不变。
- `Test-StockSparse.py` 6 项通过，包含原生工具转换/解码及错误尺寸、截断、未知 chunk、错误 payload 长度、尾随数据拒绝。AOSP sparse schema 固定在来源锁定文件。
- 仅验证主机文件；没有临时启动/刷写/切槽或新的手机命令。原厂恢复模式、下载分片、硬件恢复未验证；首次 RAM 启动仍等待明确授权。
- Windows 对恢复组所有导出文件再次核对大小/SHA-256 通过。`backups/recovery-readiness-20261003-200643-068/` 保存恢复清单和模块核验报告的已核对元数据副本；镜像实体仍在 artifacts/ 和 WSL 原有位置，元数据快照不包含镜像实体，不是独立介质备份。
