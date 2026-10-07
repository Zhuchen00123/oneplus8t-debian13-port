# OnePlus 8T / KB2000 → Debian 13

OnePlus 8T 中国版（kebab / SM8250）的主线 Linux 移植与服务器维护项目。

10/06三段键现已内建于 `7.2.0-kebab-debian13-r3-slider+`。正常重启后自动识别、三档事件、真实界面切换和触摸已通过；原 r2 组保留回退。见[内建与重启验收](docs/slider-builtin-20261006.md)。

10/06下档已装 Firefox ESR 和官方 ARM64 QQ。此前非主动重启根因未定，三段键冷启动故障已修复并完成上述内建；酒馆和机器人恢复过程见[应用、故障与恢复](docs/desktop-apps-recovery-20261006.md)。

**10/06三段键三模式已部署：上档触控终端、中档仪表盘、下档Phosh手机桌面；下档点击输入框自动弹出键盘。** 换档保留应用，图形故障可自动回退旧面板；r3 正常重启后的真实界面／触摸已通过。软件蓝牙输入栈就绪，真实蓝牙键盘及长期温升待验收。见[实施与维护](docs/three-mode-ui-20261006.md)。

**2026-10-05 酒馆迁移记录：Azure 酒馆及数据已迁到手机，CNB 访问与自动恢复已实测，会话卡死修复后用户确认能进入并正常切换会话。** 模型反代仍留 Azure，原酒馆和原数据保留；长期无人值守未验收。阅读旧文档时，以[当前状态](docs/project-status-20261004.md)和[最新交接](docs/handover-20261004.md)最新增补为准。

## 当前完成与限制

| 项目 | 已验证 | 尚待完成 |
| --- | --- | --- |
| 启动与管理 | 持久Debian、严格USB SSH、根读写、正常重启；A槽成功标记修复后重试数保持5 | 内核升级仍需成组核验与恢复点 |
| 显示与触摸 | 修复成组内建；真实单指、双指、抬起及重启后显示/触控通过 | 间歇误触根因、长期稳定性；目前只暴露120Hz |
| GPU | Adreno 650 GLES绘制和像素回读通过，Vulkan已枚举 | 完整Vulkan和实际服务器负载测试 |
| 存储 | 系统盘7GiB、数据盘约220GiB；10/05采样根剩约3.7GiB、/srv剩约216GiB；历史开机挂载及32MiB持久性通过 | 性能基准、独立介质备份和恢复演练 |
| 限充 | 80%停充/75%恢复策略常驻；224秒续约、停止与进程丢失恢复通过；当前停充位读回有效 | 真实80/75完整充电周期、插拔、长期供电与热安全 |
| 电源键 | 10/04短按亮灭曾由用户实测通过；10/06当前服务active，保持原控制与配置 | 10/05failed为历史状态；长期与完全断电仍未验收 |
| 三档本地界面 | r3 内建三段键；上档触控终端／中档panel／下档Phosh；正常重启后用户三档界面／触摸确认，换档保留应用，图形故障回退通过 | 真实蓝牙键盘、逐项终端手势／组合键、应用触控和长期温升待测；扫描仍120Hz |
| Wi-Fi与时钟 | 10/05校园认证维护active；酒馆CNB公网访问、Azure模型与嵌入请求实际通过 | NTP本轮未重新验收；历史门户故障不能当作当前外网结论 |
| MC与穿透 | 独立任务已修复MC中继；10/05酒馆最终巡检原MC客户端和CNB保活active，未改原通道 | MC修复交接仍记录未安装Minecraft；本轮未复验Java/25565或玩家入口 |
| 酒馆迁移 | 保持1.18.0精确提交及Node20.20.2；ARM64重装依赖、普通用户与独立systemd；36角色卡、36聊天、23世界书、43传统预设及原扩展已恢复 | Azure原部署保留；新旧实例此后独立保存，记忆质量仍待验收 |
| 酒馆访问与性能 | CNB登录/CSRF、真实生成与嵌入通过；专用环境停止后约93秒自动恢复；卡死修复后用户确认正常进入/切换 | 清理诊断后的最终刷新、输入和扩展按钮待确认；另一校外设备、长期SSE/温升、整机重启未测 |

用户曾报告屏幕下部靠近USB处烫手；拔线冷却并部署切屏服务后确认当前已不烫手。**发热原因仍未确定，不能据此宣称长期插电安全或已实现旁路供电。** 见[局部发热记录](docs/local-heating-20261004.md)。

## 流程图与文档入口

- [Figma / FigJam：项目交付与服务器运行流程](https://www.figma.com/board/dIWts4DnLVD4Z0PCGHFpmc)
- [流程图说明与仓库内 Mermaid](docs/project-flows-20261004.md)
- [当前状态、验证边界与待办](docs/project-status-20261004.md)
- [新会话交接与操作边界](docs/handover-20261004.md)
- [酒馆迁移、访问、启停、私有备份及回退](docs/sillytavern-migration-20261005.md)
- [酒馆会话卡死修复、性能证据与验收限制](docs/sillytavern-performance-20261005.md)
- [全屏状态面板与按需XFCE](docs/status-panel-20261004.md)
- [文档索引](docs/README.md)
- [阶段验证汇总](docs/validation.md)、[移植路线](docs/porting-plan.md)

历史进展：原厂调查与备份 → RAM对照 → 持久r1 → 显示/触摸r2 → 限充与启动记账修复 → 存储扩容 → 短按切屏 → 酒馆迁移与会话性能修复。旧文中的“尚未启动”“未限充”“4GiB镜像”只描述对应阶段；旧内建触摸失败不代表当前仍用模块接管。现有FigJam及流程图仍是10/04版本，本次只更新Markdown。

酒馆当前地址可在项目目录运行 `.\scripts\Get-SillyTavernAccess.ps1` 查询，使用原Azure酒馆账号密码登录；实际地址和恢复材料仅在私有 `local/sillytavern-migration-20261005/`。CNB保活与已验证的恢复机制不保证永不断线。

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

EmbeddingGemma 2 ONNX Q8已完成手机文本与图片有界试跑，实测速度、内存、温度与限制见[10/07试跑记录](docs/embeddinggemma-q8-trial-20261007.md)。独立试验环境保留，未切换酒馆或启用常驻嵌入服务。

当前手机处于Linux；维护使用本机私有SSH配置，入口为local/ssh/kebab-server.conf，不复制或上传密钥。ADB示例仅适用于原厂Android/历史阶段。设备写入前按[规范](AGENTS.md)核验实际目标、输入哈希、授权和恢复点。

构建使用Debian WSL的大小写敏感文件系统，先DTB后内核。r2-charge-policy是当前配方；下面只做主机构建：

```bash
bash scripts/linux/prepare-kernel.sh /home/<user>/oneplus8t-build-charge r2-charge-policy
bash scripts/linux/build-kernel.sh /home/<user>/oneplus8t-build-charge dtb
bash scripts/linux/build-kernel.sh /home/<user>/oneplus8t-build-charge kernel
```

Windows工具要求PowerShell 7。项目快照使用scripts/Backup-Project.ps1；终端没有pwsh时应使用已安装PowerShell 7完整路径，不把要求7.x的脚本交给Windows PowerShell 5.1。

运行中的7GiB系统、/srv数据盘及新增服务不能等同于旧4GiB封存rootfs。旧r1/r2镜像保留恢复与复现用途，当前组和运行后配置见[交接](docs/handover-20261004.md)。不同构建的boot、DTB、模块和initramfs不得混用。
