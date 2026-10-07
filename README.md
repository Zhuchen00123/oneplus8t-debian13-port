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

默认配方保留 r1 仅应用 0001；显式选择 `r2` profile 可成组应用显示与内建触摸四项修复。r2 已完成本机真实单指/双指/抬起、重启和 USB SSH 验证，旧模块接管服务已禁用。[r2 构建与验收](docs/r2.md)。镜像、固件、身份及原始证据只留本地。

## EmbeddingGemma 2 ONNX Q8 试跑

2026-10-07：新增固定来源的文本与图片嵌入有界测试。普通大核单线程短查询约0.59秒，低预算图片约8秒；仅为小样例验收，未部署生产服务。详见[实测与限制](docs/embeddinggemma-q8.md)及[复验步骤](scripts/embeddinggemma/README.md)。
