# 验证记录

日期：2026-10-03。

## 实机

- ADB 已授权；机型 KB2000、Android 14 / ColorOS 14.0.0.602 已实读。
- flash.locked=1、vbmeta.device_state=locked、verifiedbootstate=green、slot=_a 已实读。
- SurfaceFlinger / DisplayManager 报告 1080×2400、60/120 Hz。
- 面板型号/序列号、设备树和分区读取均被系统拒绝；原厂屏身份与原厂分区备份仍未完成。
- 未执行解锁、root、写入分区、切槽或重启。

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
- 正确 kernelrelease 为 `7.2.0-kebab-debian13+`，产物目录 `artifacts/7.2.0-kebab-debian13+/f6ac9e9c86d0-064fa50f8341/`。首次 kernelrelease 读取早于 Kconfig sync，误标为 7.2.0-gf6ac9e9c86d0；已修正脚本并重新导出，旧目录仅作过程记录。
- DTB 反编译存在地址/尺寸、GENI 多协议节点重号和单子端口的警告，已完整保留日志。尚未执行 dt-schema 绑定验证和实机验证，不宣称结构警告已消除。
- PowerShell 与 Bash 语法检查通过；固定來源 Sync-Sources 在现有仓库上核验通过。
- `tests/Test-HostTools.ps1` 全部通过：4096 字节包含全字节取值的输出完全一致，stderr 分离，超时终止；未提交修改、Unicode 新文件被快照保存，忽略文件被排除，bundle 在全新目录恢复为相同提交和原始内容。
- Get-DeviceInfo 在本机执行成功；分区计划确认 boot_a=sde11、dtbo_a=sde17、super=sda15；实际备份模式在 UID 2000 时按预期提前拒绝，没有生成镜像。
- 原始分区镜像成功读取及两端校验路径尚无具备权限的设备可测。
- 内核 Image 和 modules 构建：进行中，结束后更新。
- 管理仓库阶段提交与快照、完整内核源码归档：进行中，结束后更新。
- Debian rootfs、initramfs、boot.img 与实机启动：未执行。
