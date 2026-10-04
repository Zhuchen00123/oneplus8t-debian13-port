# OnePlus 8T / KB2000 → Debian 13

OnePlus 8T 中国版（kebab / SM8250）的主线 Linux 移植与服务器维护项目。

**2026-10-04 当前状态：手机已持久运行 Debian 13 arm64，具备部署和调试服务的基础条件；长期无人值守与公网 MC 服务器尚未验收。** 当前内核为 `7.2.0-kebab-debian13-r2-charge-policy+`。阅读旧文档时，以[当前状态](docs/project-status-20261004.md)和[最新交接](docs/handover-20261004.md)为准。

## 当前完成与限制

| 项目 | 已验证 | 尚待完成 |
| --- | --- | --- |
| 启动与管理 | 持久Debian、严格USB SSH、根读写、正常重启；A槽成功标记修复后重试数保持5 | 内核升级仍需成组核验与恢复点 |
| 显示与触摸 | 修复成组内建；真实单指、双指、抬起及重启后显示/触控通过 | 间歇误触根因、长期稳定性；目前只暴露120Hz |
| GPU | Adreno 650 GLES绘制和像素回读通过，Vulkan已枚举 | 完整Vulkan和实际服务器负载测试 |
| 存储 | 系统盘7GiB，显示6.9GiB、剩约3.9GiB；/srv约220GiB可用；开机挂载及32MiB持久性通过 | 性能基准、离机数据备份和恢复演练 |
| 限充 | 80%停充/75%恢复策略常驻；224秒续约、停止与进程丢失恢复通过；当前停充位读回有效 | 真实80/75完整充电周期、插拔、长期供电与热安全 |
| 电源键 | 短按亮屏/熄屏，服务器继续运行；用户实测通过，已配置服务自启 | 新增服务的重启验收；面板完全断电未验证，实际亮度读回不可靠 |
| 本地状态面板 | 全屏两页只读指标、真实显示切页通过；熄屏暂停采样；面板自启、XFCE按需切换 | 联合重启与长期亮屏温升未验收；扫描仍120Hz |
| Wi-Fi与时钟 | 曾完成校园认证并实测外网/NTP；无线配置保留 | 最近外网探测转认证门户；本轮NTP仍未同步，需恢复并复验 |
| MC与穿透 | Java版、校园流量、参考既有443/WSS方案的目标已确定 | 手机当前无Java/25565监听；服务端、玩家入口和负载未验收 |

用户曾报告屏幕下部靠近USB处烫手；拔线冷却并部署切屏服务后确认当前已不烫手。**发热原因仍未确定，不能据此宣称长期插电安全或已实现旁路供电。** 见[局部发热记录](docs/local-heating-20261004.md)。

## 流程图与文档入口

- [Figma / FigJam：项目交付与服务器运行流程](https://www.figma.com/board/dIWts4DnLVD4Z0PCGHFpmc)
- [流程图说明与仓库内 Mermaid](docs/project-flows-20261004.md)
- [当前状态、验证边界与待办](docs/project-status-20261004.md)
- [新会话交接与操作边界](docs/handover-20261004.md)
- [全屏状态面板与按需XFCE](docs/status-panel-20261004.md)
- [文档索引](docs/README.md)
- [阶段验证汇总](docs/validation.md)、[移植路线](docs/porting-plan.md)

历史进展：原厂调查与备份 → RAM对照 → 持久r1 → 显示/触摸r2 → 限充与启动记账修复 → 存储扩容 → 短按切屏。旧文中的“尚未启动”“未限充”“4GiB镜像”只描述对应阶段；旧内建触摸失败不代表当前仍用模块接管。

## 目录与隐私

| 路径 | 用途 |
| --- | --- |
| sources.lock.json | 来源提交、补丁、配置及验证快照 |
| config/、patches/、scripts/ | 配置模板、补丁、主机构建与明确用途的维护工具 |
| docs/、docs/diagrams/ | 脱敏状态、流程图源文件、阶段证据与交接 |
| sources/、artifacts/ | 本地源码和成组构建产物，忽略 |
| local/、firmware/、backups/ | 原始记录、固件、镜像与私有备份，忽略 |

公开内容限于源码、脚本、配置模板、流程和脱敏记录。可用镜像、固件、NV/EFS、密钥、序列号、账号凭据和个人文件不提交、不上传。原厂26个分区备份及运行中文件归档不是全盘/原子快照，同盘副本不等于独立介质恢复保障。

## 使用与构建

当前手机处于Linux；维护使用本机私有SSH配置，入口为local/ssh/kebab-server.conf，不复制或上传密钥。ADB示例仅适用于原厂Android/历史阶段。设备写入前按[规范](AGENTS.md)核验实际目标、输入哈希、授权和恢复点。

构建使用Debian WSL的大小写敏感文件系统，先DTB后内核。r2-charge-policy是当前配方；下面只做主机构建：

```bash
bash scripts/linux/prepare-kernel.sh /home/<user>/oneplus8t-build-charge r2-charge-policy
bash scripts/linux/build-kernel.sh /home/<user>/oneplus8t-build-charge dtb
bash scripts/linux/build-kernel.sh /home/<user>/oneplus8t-build-charge kernel
```

Windows工具要求PowerShell 7。项目快照使用scripts/Backup-Project.ps1；终端没有pwsh时应使用已安装PowerShell 7完整路径，不把要求7.x的脚本交给Windows PowerShell 5.1。

运行中的7GiB系统、/srv数据盘及新增服务不能等同于旧4GiB封存rootfs。旧r1/r2镜像保留恢复与复现用途，当前组和运行后配置见[交接](docs/handover-20261004.md)。不同构建的boot、DTB、模块和initramfs不得混用。
