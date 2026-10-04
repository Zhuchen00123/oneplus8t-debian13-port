# OnePlus 8T / KB2000 → Debian 13 arm64

OnePlus 8T 中国版 KB2000（kebab / SM8250 / Adreno 650）的社区主线 Linux 移植配方与研究记录。服务器优先，逐步验证显示、触摸和外设。

本仓库只发布文本形式的构建脚本、校验程序、通用配置、源码补丁和移植思路，不发布可用 boot/rootfs/DTB/模块镜像、原厂分区、固件、设备日志或私有配置。由经过审核的文件白名单导出，使用独立的初始 Git 历史。

## 入口

- [项目结构](docs/architecture.md)
- [主机构建流程](docs/build.md)
- [驱动修复与应用范围](docs/patches.md)
- [已验证状态和限制](docs/status.md)
- [脱敏与发布范围](docs/privacy.md)
- [来源和许可证说明](NOTICE.md)

## 基线

来源调查快照：2026-10-03，固定于 sources.lock.json。

- SM8250 社区分支 sm8250-7.2.0，固定提交 71f4068cb254730bcf334cde9bdd6ca183e4fcd0
- jfbobier kebab bring-up，固定提交 2e5e065da8ea8d242ecc34bfe064b88257797aa9
- OnePlusOSS Android 厂商源码仅作协议、面板和设备树参数参考
- Debian 13 trixie arm64 用户空间

这里采用接近主线的社区 fork，不能当作 kebab 已完整合入官方 Linux 主线。社区 KB2003 验收不能替代 KB2000 的实测。

## 目录

| 路径 | 职责 |
|---|---|
| config/ | Debian 内核配置、软件源和待本地校准的 boot 参数模板 |
| sources.lock.json | 上游版本、补丁 hash、应用范围 |
| patches/local/ | TCM 报文解析与 AMB655X 显示修复源码差异 |
| scripts/linux/ | 内核、rootfs、initramfs、boot 和固件布局的主机配方与诊断工具 |
| scripts/ | 来源同步、二进制进程工具、项目与产物备份 |
| tests/ | 主机校验与回归测试 |
| docs/ | 脱敏后重写的工程说明 |

构建目录必须在 Linux 大小写敏感文件系统。内核、DTB、模块、initramfs、rootfs 必须成组核验；同一个 kernelrelease 不保证模块 BTF 兼容。

脚本不是整机刷写工具。主机构建不触发 ADB/fastboot/SSH 手机部署；运行时 USB/蓝牙/救援脚本供用户明确选择后集成到目标系统，GPU 诊断会访问执行它的机器的图形设备。

当前配方默认应用补丁 0001；0002 的内建路线及 0003/0004 显示修复需要在独立内核分支审查应用、更新构建元数据并重新构建整组。此导出不声称一条命令复现最后运行的设备状态。
