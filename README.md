# OnePlus 8T / KB2000 → Debian 13

11/03 旧香港 VPS 时序复测（17:00–17:13）：RTT 稳定 111–113ms、下载 5.7–6.4 MB/s，上传在 0.86 与 0.2 MB/s 间双峰跳变；全天对比早上 77ms/9.3/5.3 明显更好，说明劣化与时段相关，且是同一条国际链路（两台香港节点同一时刻丢包同步变差）。

11/03 新香港 lite VPS（hklite, 2403:4fc0:0:201::26c）已接管：改密、装公钥、加 DIRECT 规则。实测 IPv6 RTT 116.0ms / 抖动 0.52ms / 丢包 0%，上传 0.95–0.97 MB/s（比旧香港快 30%），下载 4.6 MB/s（略低于旧香港 5.3）。

11/03 新日本 VPS（newnb-253, 2a14:7586:6109:1::2c0）实测：空载 RTT 92.6ms/抖动 1.29ms 优于香港（117.9/4.56），但大流量下 VPS→校园下载仅 0.03–0.15 MB/s（重传 6.9%、cwnd 掉到 11），远逊香港 3.8–8.6 MB/s，结论不迁移。路由器已加两条 DIRECT 规则和 WAN ICMPv6 echo（均带备份）。

11/03 上行深挖结论：校园→VPS 方向存在 1.7–6.7% 随机丢包（117 ms RTT、无 bufferbloat、无重排），CUBIC 因此塌到 0.5–0.9 MB/s；同期 UDP 可达 11.2 MB/s、QUIC 1.26 MB/s、到 Cloudflare 上传 3 MB/s，证明是丢包而非带宽/VPS 问题。

11/03 上传复测：校区→香港 VPS 的上行从早上 5.3 MB/s 塌陷到 0.79–0.94 MB/s（−84%），而下行仍 8.6 MB/s；同期校区→Cloudflare 上传仍有 4.6–5.75 MB/s，说明是校区→香港这一特定方向劣化，非校区总出口或 VPS 接收能力。端点筛查：Cloudflare Speed 可用，Tele2/Linode 不可达。

11/03 美国出口选型完成：TCP SOCKS（VPS:1080 → wg0 → VM）50MB 实测 4.10 MB/s，QUIC 方案仅 1.65 MB/s，页面延迟持平，已停用 QUIC。同日链路劣化测量：校园→VPS RTT 由 77 ms 升至 122 ms（+58%，p99 1.1 s、2/100 秒级停顿），吞吐仅降 7%（9.3→8.6 MB/s），性质是延迟而非带宽。

11/03 InstaBox 美国链路已改为常驻：VM 侧 vps-link.service（Restart=always）把 VM sshd 发布在 VPS:22222，实测 kill 自愈、挂起恢复后仍可用；按用户要求暂不做上网加速。

11/03 已可用 InstaBox 美国 VM 的网络上网：VM 反向 SSH 到香港 VPS 的 22222、本地 ssh -D 建 SOCKS5，出口 16.148.25.106（AWS 俄勒冈），页面约 1.1–1.5 s、下载 2.25 MB/s；一键脚本 scripts/Start-UsEgress.ps1。见[美国出口](docs/hk-vps-quic-egress-20261102.md)。

11/03 InstaBox 云端 VM（AWS 俄勒冈）与校园网实测双向不可达，但到香港 VPS 质量良好（RTT 161 ms、下行 15.0 MB/s／上行 10.8 MB/s、零丢包）；已用香港 VPS 做中转：手机侧反向 SSH 隧道 + VPS socat 22022，InstaBox 可登录校园手机并经转发访问电脑 DSH 与路由器（0.73 s）。见[校园↔InstaBox 中转](docs/hk-vps-quic-egress-20261102.md)。

11/02 已将 hysteria2 常驻到连在 E8820S 上的 OnePlus 8T 服务器并保持 USB 有线：路由器补 usb0 ULA IPv6 与 NAT66、手机补回程路由后，QUIC 下载 9.3 MB/s、上传 5.3 MB/s（同链路 Wi-Fi 仅 1.5 MB/s）。电脑 DSH 的 dsh-bridge 自建隧道改为经手机转发接入香港 VPS，新增 HTTPS 入口 https://fuckdsh.lugang.xyz（Let's Encrypt、IPv6 直连、页面 0.34–0.47 s），明文 3000 端口已关闭；插件 25 秒重连上限与长期稳定性仍待观察。见[部署、实测与回退](docs/hk-vps-quic-egress-20261102.md)。

11/02 已按用户要求让 `api.commandcode.ai` 走路由器 Clash：域名规则及USB入口实测命中FSCloud，DSH同款Node的普通fetch取得JSON200；其它真实IP的USB直连对照通过。仅修改路由器侧，DSH未改配置／重启，IPv6策略未动，真实对话长期稳定性待观察。见[实施与回退](docs/dsh-commandcode-router-proxy-20261102.md)。

11/02 已实现同一根 USB 线由 E8820S 路由器供电并承载手机默认 IPv4 上网／DNS，HTTPS 与两应用 USB 入口通过；后续精确连接跟踪确认无显式代理的 USB IPv4 HTTPS 经 WAN NAT 直连。80秒9采样均80%停充、电池净电流0，电池侧净充电约0W；USB实际输入功率尚无可靠测量。路由器及手机原 Wi-Fi IPv6 保留，校园路由不变；长期供电、断线自动回退和冷启动尚未验收。见[实机结果与回退](docs/usb-router-20261102.md)。

10/22 已按用户选择将双STA省电关闭设为持续运行配置：启动前生效、轻量守护维护，沿用原7.2驱动与原固件，酒馆／DSH及双连接验收通过。诊断候选和试验证据保留；长期稳定及读零根因仍未证。见[部署与回退](docs/wifi-ps-off-adopt-20261022.md)。

10/10 已接入 DSH 官方 browser-use 与 Chrome DevTools provider，手机无头 Chromium 的中文填表/点击/截图、状态保持、会话隔离、HTTPS外网及项目外路径拒绝实测通过。新增浏览器目录兼容补丁和 DSH任务上限512，原桥接补丁与搜索/技能保留。见[官方浏览器使用、验收与回退](docs/dsh-browser-official-20261010.md)。

10/10 已安装五个日常/coding skills，默认 Flash 原生加载、调试修复、四项测试及中文笔记验收通过。显式笔记入口为 `/srv/dsh/workspace/projects/personal-notes`，按用户选择暂不开自动记忆。见[技能使用、来源与回退](docs/dsh-daily-skills-20261010.md)。

10/10 手机端 gh、jq、GitHub 登录和常驻观察已配置，公网 DSH 实际执行通过。新项目放 `/srv/dsh/workspace/projects/<项目名>`，`kebab-dsh doctor` 查健康记录。见[手机常驻环境](docs/dsh-phone-runtime-20261010.md)。

10/10 已接入 Context7、Firecrawl 和 Devin Search，默认 Flash 的实际搜索与最终答复通过；18 个模型统一配置 500k/80k 和七档思考，默认 medium。两个 MCP 直连官方托管，Azure 未新增服务。见[搜索组合、模型设置与维护](docs/dsh-search-stack-20261010.md)。

OnePlus 8T 中国版（kebab / SM8250）的主线 Linux 移植与服务器维护项目。

10/10 DSH 常驻与手机入口已就绪：社区 bridge、宿舍/gdut 双 STA 局域网 HTTPS，以及共用原单实例的 CNB 公网入口。用户确认 gdut 手机能登录并收到回复；公网默认 Flash 实际对话、65秒WS和DSH约16秒正常退出恢复通过。地址/密码见受限 `local/dsh-access-20261009/ACCESS.md`，运行 `scripts/Get-DshAccess.ps1` 查询最新地址。见[常驻、访问与回退](docs/dsh-always-on-access-20261009.md)；新访问链路连续24小时与联合重启仍待观察。

10/09 已将酒馆、SSH、MC 中继和原 cnb-mcp 占位服务／Cloudflare 连接器合并为 **一个 1 核 CNB 实例**，两个旧实例已关闭、旧保活器停用。全部入口及一次约123秒的自动重建恢复实测通过。见[单实例合并与维护](docs/cnb-consolidation-20261009.md)。

10/08个人 agent 第一阶段已安装 DSH Web 0.2.0-rc.2、独立 ARM64 开发工具和常驻服务，接入 Azure clinepass；2 个模型、文件读取与 bash 工具实测通过。在项目目录运行 `.\scripts\Open-Dsh.ps1` 打开认证页面。见[安装、访问和回退](docs/dsh-phase1-20261008.md)；记忆／QQ／主动调度插件和 24 小时稳定性仍待后续阶段。

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
