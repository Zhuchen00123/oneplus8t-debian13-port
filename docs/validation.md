# 验证记录

日期：2026-10-03。

## 实机

- ADB 已授权；机型 KB2000、Android 14 / ColorOS 14.0.0.602 已实读。
- 解锁前 flash.locked=1、vbmeta.device_state=locked、verifiedbootstate=green、slot=_a 已实读。
- SurfaceFlinger / DisplayManager 报告 1080×2400、60/120 Hz。
- 面板型号/序列号、设备树和分区读取均被系统拒绝；原厂屏身份与原厂分区备份仍未完成。
- 用户确认清空数据后执行 fastboot oem unlock；重新连接实读 flash.locked=0、vbmeta.device_state=unlocked、verifiedbootstate=orange、slot=_a，ColorOS 正常启动，Bootloader 解锁已验证。
- 解锁后 ADB 仍为 UID 2000，8 个面板/显示/命令行节点仍 Permission denied；屏幕身份未确认。补充节点读取遭遇 device not found，单独记录为连接中断。
- 用户重新开启 USB 调试后 ADB 恢复，完整复查 16 项：9 项 Permission denied、7 项 No such file or directory，无成功面板读取；再次验证 unlocked/orange。最新原始证据与 SHA-256 清单位于 `local/device-20261003-143138-003/`。
- 最新证据的 9 个文件 SHA-256 复核通过，并与解锁原始记录一同复制到 `backups/device-evidence-20261003-143248/`，共 22 个文件；证据副本再次核验。此备份不包含分区镜像，未提交或上传隐私日志。
- 未执行 root、刷写镜像、切槽或重新锁定。
- 解锁前保存 250 个可读取共享文件，共 13,499,706 字节；19 项补充复制均成功，250 个主机端 SHA-256 再次复核通过。应用私有数据和原厂分区不在该备份中。
- 后续临时权限准备：原厂 fastbootd 不支持 fetch；已按范围从匹配指纹的官方 OTA 提取 boot/recovery/dtbo/vbmeta，操作数据和输出 manifest SHA-256 全部通过。官方 Magisk v30.7 在本机 UID 2000 工作目录生成候选，手机/主机 hash 相同，结构对比确认仅 ramdisk 改变、原厂内核/DTB/命令行等保留；候选尚未启动、root 尚未验证，详见 temporary-root-plan.md。

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
- 原始分区镜像成功读取及两端校验路径尚无具备权限的设备可测。
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
- Debian rootfs、initramfs、boot.img 与实机启动：未执行。
- 额外检查的 vendor 固件目录也返回 Permission denied，尚未提取本机签名固件。
