# 主机构建流程

此处描述配方和输入依赖，不提供可用镜像。所有自己的原厂 boot、固件、SSH 公钥、模块包、基础 rootfs 及验证清单须在本地准备，构建输出保持忽略。

## 1. 来源与工具

Windows 需要 PowerShell 7、Git；Linux 构建在 Debian WSL 的 ext4 中进行。常用依赖包括 Clang/LLVM、make、gcc、flex/bison、DTC、pahole、Python 3.11+、kmod、mkbootimg、cpio、QEMU user/static、debootstrap、gpgv、e2fsprogs、initramfs-tools-core 和 Android sparse 工具。按当前发行版核验包来源和版本。

```powershell
pwsh -NoProfile -File scripts/Sync-Sources.ps1
```

Sync-Sources 只同步固定的 linux-sm8250 与 kebab-bringup 来源，不重置已有修改。内核来源在 Windows 上不检出，prepare-kernel 从 Git 对象建立 Linux 工作树。

## 2. 内核

在 WSL、仓库根目录运行：

```bash
PROJECT=$(pwd)
BUILD="$HOME/oneplus8t-build"
bash "$PROJECT/scripts/linux/prepare-kernel.sh" "$BUILD"
bash "$PROJECT/scripts/linux/build-kernel.sh" "$BUILD" dtb
bash "$PROJECT/scripts/linux/build-kernel.sh" "$BUILD" kernel
```

要求全新源码目录。先核验上游 21 个补丁、DTS 与配置，再合并 Debian 配置。默认 local_kernel_patches 仅含 0001；其他补丁见 patches.md。选择 `prepare-kernel.sh "$BUILD" r2` 可应用已锁定的四项显示/触摸修复及独立 r2 配置。build-kernel 检查源码提交、工作树和配置 hash，并锁住共享 Kbuild 目录。输出以 kernelrelease/commit/config 区分并保存 manifest。

内核升级或额外补丁应用后必须更新构建元数据并重新生成 Image、DTB、全部模块和依赖它们的 initramfs。不能仅刷一个新 boot 后继续使用旧模块。

## 3. Debian 与固件

prepare-debian-rootfs.sh 接收全新 Linux 工作目录和用户自己的 Ed25519 公钥，验证 Debian 签名来源、完成 arm64 debootstrap/QEMU，并禁用密码/root SSH。finalize-debian-rootfs.py 安装同组内核、模块、DTB 和基础 USB 配置，导出基础归档。

prepare-server-rootfs.sh 在新的 /home/<user>/oneplus8t-server-* 目录中扩展已验证的基础导出；目前显式要求基础 kernelrelease=7.2.0-kebab-debian13+。它不是任意内核版本的自动升级程序。

stage-device-firmware.py 接收自己的已验证归档及 manifest，核对 split ELF metadata 与分段。integrate-server.py 集成 USB/SSH、QRTR、UCM、签名 regdb、蓝牙有界地址初始化、GPU 诊断和时钟服务。不默认启用蜂窝/NV/扬声器，Venus 保持禁用。

所有包与固件输入、UUID、公钥和私有 manifest 只留本地。公钥不需要放入此 Git 仓库。

## 4. boot / initramfs / ext4

config/boot-kb2000.json 是脱敏模板；stock_boot_sha256 保留为占位符，必须依据自己的已核验原厂 boot 设置。保留严格 hash/布局校验，不用公共模板替代本机调查。

- build-rescue-initramfs.py：选择同组的 USB 依赖模块，生成 RAM BusyBox/Dropbear 环境
- build-boot-candidate.py：严格解析原厂 v2 布局，AOSP 打包/解包，并生成独立社区 no-op DTBO
- build-server-candidate.py：检查集成、模块、固件和用户空间，再生成持久 initramfs、boot 和 UUID 对齐的 ext4/sparse
- verify-server-candidate.py：检查实际组的 hash、UUID、服务/固件和身份初始化
- verify-module-payloads.py：逐个比较 rootfs、server initramfs 与 rescue initramfs 模块的实际字节
- prepare-stock-recovery.py：仅在主机转换并核验自己的原厂恢复组

持久打包还依赖本地产生的 rescue-final/dtbo-empty-community.img；路径是配方约定，不是本仓库分发文件。使用 --help 查看每个工具当前参数。

## 5. 测试和验收

主机可运行的测试包括：

```bash
python3 tests/Test-BootImage.py
python3 tests/Test-FirmwareLayout.py
python3 tests/Test-ModulePayloads.py
python3 tests/Test-StockSparse.py
python3 scripts/linux/test-bluetooth-addr.py
```

以上使用合成数据或模拟 btmgmt，不验证手机硬件。Test-TcmReports.py 与 test-tcm-protocol.c 需要对应已打补丁的内核源码，分别检查实际函数/共享解析头。Rescue/ServerUserspace 测试需要本地 rootfs、工具和隔离环境。

部署是一项独立的设备操作，此仓库不提供批量刷写脚本。核验型号、分区标签、槽位、输入 hash 与备份恢复路径后，按设备所有者的实际授权范围执行。原厂整机恢复未作为本项目已通过的验收项。

已有离线 rootfs 的新内核组可用 `install-kernel-group.py` 校验安装；rootfs 必须是独立构建目录，不能指向运行系统。`build-server-candidate.py --rootfs-uuid <本机已核对的 UUID>` 支持原位 boot/modules 升级时保持根 UUID，默认仍生成新 UUID。[r2 配方与限制](r2.md)。
