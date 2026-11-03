# 文档索引

## 当前入口

- [11/03 校园↔InstaBox 经香港 VPS 中转：手机反向 SSH 隧道 + VPS socat 22022，InstaBox 可登录校园手机并转发访问 DSH／路由器；含 InstaBox 到 VPS 的 161 ms／15 MB/s 实测](hk-vps-quic-egress-20261102.md)

- [11/02 手机服务器承载QUIC出口并接管DSH入口：hysteria2常驻、USB有线IPv6与NAT66、QUIC下载9.3MB/s／上传5.3MB/s；插件隧道改经手机转发，新增Let's Encrypt的IPv6直连入口并关闭明文3000端口](hk-vps-quic-egress-20261102.md)

- [11/02 CommandCode改走路由器Clash：精确域名规则、DNS及USB fake-IP HTTPS入口；Node实际命中FSCloud并取得JSON200，DSH未改配置／重启](dsh-commandcode-router-proxy-20261102.md)

- [11/02 USB路由器同时供电／联网：默认IPv4及DNS走USB，精确连接跟踪确认测试HTTPS直连；80%停充、电池净充电约0W，USB输入总功率未测，原IPv6保留](usb-router-20261102.md)

- [10/28 固件／内核服务器稳定性：当前轻载正常，发现重启观察器预算耗尽、根分区84%及未闭合的驱动／恢复风险](server-stability-review-20261028.md)

- [10/28 Wi-Fi健康：PS-off运行近6天，无新增转储；现存约41小时日志未见崩溃，采集器及双路连接正常](wifi-ps-off-health-20261028.md)

- [10/22 日本VPS继续复测：手机长文件下行13.31–13.70Mbps／上行13.25–13.30Mbps，确认透明代理；路由器直连下行18.34／2.17Mbps波动](vps-phone-speed-20261022.md)

- [10/22 已转用PS-off持续方案：双路关闭省电、启动前钩子与常驻守护，原模块／固件及应用保持，含回退入口](wifi-ps-off-adopt-20261022.md)

- [10/22 STA省电关闭已试机：600秒／57次健康核验未崩，已恢复原组，稳定修复未证](wifi-ps-off-candidate-20261022.md)

- [10/22 原驱动MHI取证：15分钟未复现／未见转换；devcd20同型坏环但SFR不足以定1656，已清理并核验服务](wifi-mhi-evidence-20261022.md)

- [10/22 WMI跟踪已实机：自然1656、9029条记录、RDDM前73.392秒无发送尝试；已恢复原组](wifi-wmi-trace-trial-20261022.md)

- [10/22 7.2 WMI跟踪候选构建：两项诊断开关、成对模块及符号核验；1699协议值／53字段继续对照相同](wifi-72-tracing-build-20261022.md)

- [10/22 7.2回归排查：18次自然RDDM、8份新1656、事件4真值反例及功能门控筛查](wifi-72-regression-triage-20261022.md)

- [10/22 6.18.43／7.2无线栈对照：AMSS相同、9个HAL环函数相同，未发现可直接移植的1656修复](wifi-618-driver-comparison-20261022.md)

- [10/21 6.18.43临时启动：早期缺固件、根就绪重试BHI失败，已回原组并核验双STA／服务／配置](wifi-kernel-618-trial-20261021.md)

- [10/21 社区Wi-Fi组合调查：Armbian 6.18.43／6.19候选、官方AMSS重验及已有修复排除](wifi-community-combinations-20261021.md)

- [10/21 OEM三件套+r3实机重试：11分钟运行通过、退出仍RDDM、原组恢复](wifi-oem-trial-20261021.md)

- [10/21 四份已保存Wi-Fi转储：三份1656复核、超时计数修正与独立1545定位](wifi-saved-dumps-analysis-20261021.md)

- [10/15 自然1656内部指针特殊模式、固件事件双样本验证及19:31强161恢复验收](wifi-ring-fallback-localization-20261015.md)

- [10/15 慢速TLS正文截断已独立复现、修正部署与四入口/实际慢读验收](lan-tls-response-integrity-fix-20261015.md)

- [10/15 两轮辅助组件隔离、退出异常记录、12:57新自然故障与原生漫游拒绝原因](wifi-exit-board-isolation-20261015.md)

- [10/15 校园原生后台扫描已保留、300秒实扫/四入口及独立速度验收](campus-native-roaming-repair-20261015.md)
- [10/15 PCI诊断首次抓到候选退出完整转储、异常指令定位及原组恢复](wifi-pci-exit-diagnostic-20261015.md)
- [10/15 原厂候选56分钟完成、退出仍RDDM、恢复原组双161及最终速度](wifi-oem-targeted-observation-result-20261015.md)
- [10/15 内网TLS入口关闭阻塞的实机复现、修复和四入口验收](lan-tls-shutdown-fix-20261015.md)

- [10/15 VPS误判/TLS恢复/重复网络重启的控制器修正、新候选轮及第二份同签名转储](wifi-oem-trial-controller-fix-20261015.md)

- [10/14先恢复原强161信道、四入口复核及获准的原厂长对照进展](wifi-oem-targeted-60min-result-20261014.md)

- [10/14具体断言分支定位、原厂候选对应代码变化及采集缺口修正](wifi-assert-localization-20261014.md)

- [10/14 19:02首份自然转储完整捕获、具体assert线索及日志缺项](wifi-natural-coredump-20261014.md)

- [10/14采集缺口补救：手机本机24小时自动保存、31项测试与实机运行](wifi-phone-local-capture-20261014.md)

- [10/14 17:38新自然崩溃、校园弱AP切回与速度恢复](wifi-natural-crash-recovery-20261014-evening.md)

- [10/14无线取证继续、转储优先工具与15分钟实际观察](wifi-evidence-capture-followup-20261014.md)

- [10/14无线自然崩溃与退出故障区分、转储缺口及定位条件](wifi-crash-localization-20261014.md)

- [10/14手机校园入口双向短测、完整性及路径界限](campus-bidirectional-speed-20261014.md)

- [10/14日本VPS当前手机/路由器速度、直连下行异常与代理对照](vps-current-speed-20261014.md)

- [10/14第三轮原厂长观察、实际580秒加载、性能比较与恢复](wifi-oem-extended-observation-result-20261014.md)
- [10/14已批准10分钟原厂RAM观察、设备到期保护及准备状态](wifi-oem-extended-observation-plan-20261014.md)
- [10/14早晨校园弱AP恢复及第二轮原厂固件短测](wifi-oem-ram-trial-followup-20261014.md)
- [10/14原厂固件首轮RAM试验、实际失败与原组恢复](wifi-oem-ram-trial-result-20261014.md)
- [QCA6390固件与官方逐字节核验、r3内核稳定性排查（10/13，修复尚未验收）](wifi-firmware-stability-20261013.md)

- [双 Wi-Fi 两轮161/20提速、保留配置及手机固件崩溃取证（10/13，稳定性未解决）](campus-wifi-fix-20261013.md)

- [晚间宿舍/校园内网文件传输与网关延迟复核（10/13）](lan-campus-transfer-recheck-20261013.md)

- [日本VPS↔路由器 WireGuard 实测与晚间TCP对照（10/13）](wireguard-speed-20261013.md)

- [日本VPS↔路由器校园PPPoE双向测速与现有代理对照（10/13）](vps-router-speed-20261013.md)

- [宿舍5GHz信道/带宽矩阵、165/20保留与iperf3核验（10/13）](wifi-configuration-comparison-20261013.md)

- [宿舍无线改信道与 DSH 静态压缩修复（10/13）](wifi-dsh-loading-fix-20261013.md)

- [DSH 局域网加载与手机无线传输瓶颈核验（10/13）](dsh-lan-transfer-bottleneck-20261013.md)

- [DSH 与酒馆宿舍、校园网 HTTPS 直连入口](lan-web-access-20261012.md)

- [DSH 官方浏览器 alpha 更新、社区预览与人工接管复查（10/12）](dsh-browser-update-20261012.md)

- [日本 VPS＋Cloudflare 长期入口、自启与退出恢复](vps-cloudflare-always-on-20261012.md)

- [手机经日本 VPS 与正式 Cloudflare Tunnel 的6小时网页试验](vps-cloudflare-webrelay-trial-20261011.md)

- [手机 DSH 官方浏览器自动化、截图范围与真实验收](dsh-browser-official-20261010.md)

- [DSH 人工预览插件市场、实时星数与适配比较](dsh-browser-market-20261010.md)

- [手机 DSH 五个日常/coding skills、显式笔记与真实验收](dsh-daily-skills-20261010.md)

- [手机文档处理与日常办公 skills/MCP 候选](dsh-office-skills-mcp-20261010.md)

- [手机 DSH 常驻环境、gh 与项目目录](dsh-phone-runtime-20261010.md)

- [DSH 搜索组合、500k/80k 模型与思考档位](dsh-search-stack-20261010.md)

更新于2026-10-10。状态和交接保留10/04文件名，以顶部最新增补为准；最新为 DSH 官方浏览器、日常技能和手机常驻环境验收。

| 文档 | 内容 |
| --- | --- |
| [当前状态](project-status-20261004.md) | 通过、部分通过、未验收与待办 |
| [最新交接](handover-20261004.md) | 新会话入口、恢复点、授权与分工 |
| [DSH 第一阶段](dsh-phase1-20261008.md) | ARM64 安装、开发工具、Azure clinepass、认证访问、验收与回退 |
| [电脑 DSH 插件盘点](dsh-desktop-plugin-inventory-20261010.md) | Desktop/Web/TUI 配置、启用状态、工作流能力与长期记忆缺口 |
| [编程与日常扩展候选](dsh-coding-daily-stack-20261010.md) | MCP、工程/文档skills、记忆/任务插件与轻量组合取舍 |
| [DSH 远程与搜索插件](dsh-remote-search-20261008.md) | 电脑实际配置、远程入口候选、Devin／Firecrawl／AnySearch、rc.2 兼容与部署边界 |
| [DSH 常驻与手机访问](dsh-always-on-access-20261009.md) | 社区bridge、宿舍/gdut双STA、HTTPS/WS认证、单实例公网与恢复验收 |
| [DSH 手机浏览器入口准备](dsh-mobile-access-20261008.md) | 历史准备与补丁来源；后续已授权并部署 |
| [CNB 单实例合并](cnb-consolidation-20261009.md) | 酒馆／SSH／MC及原MCP占位入口共用1核实例，旧保活器停用，重建恢复通过 |
| [CNB 实例与用量核对](cnb-instance-audit-20261009.md) | 合并前3个、合并后1个运行实例；核时计量口径 |
| [CNB 到校园网测速](cnb-speed-20261011.md) | 沿用 sushewifi 标准：宿舍路径约16.8、PPPoE约11–12、gdut WLAN约2.4–6.6 Mbps，容器侧无限速 |
| [个人 agent 方案](personal-agent-options-20261008.md) | 轻量方案调查与本机边界 |
| [DSH 个人 agent 插件](dsh-personal-agent-plugins-20261008.md) | 记忆、QQ、主动调度插件的固定来源和候选组合 |
| [三段键内建与重启](slider-builtin-20261006.md) | r3 实际内建、初始档位、三档触摸、充电和服务恢复、旧组回退 |
| [下档应用与恢复](desktop-apps-recovery-20261006.md) | Firefox ESR／官方 ARM64 QQ、此前意外重启及恢复过程 |
| [酒馆迁移与维护](sillytavern-migration-20261005.md) | 精确版本、数据/扩展、CNB访问、启停、私有备份与回退 |
| [酒馆会话性能修复](sillytavern-performance-20261005.md) | 卡死根因、已部署补丁、13项回归、Chrome人工确认与待验收项 |
| [项目流程](project-flows-20261004.md) | FigJam、Mermaid、交付及运行流程 |
| [验证汇总](validation.md) | 最新索引与历史记录 |
| [移植路线](porting-plan.md) | 当前进度及后续路线 |

## 当前专题

- [酒馆迁移](sillytavern-migration-20261005.md)、[会话卡死与预热优化](sillytavern-performance-20261005.md)；地址查询使用根目录 `scripts/Get-SillyTavernAccess.ps1`，实际地址/凭据不写公开文档
- [显示/触摸r2](r2-integrated-20261004.md)、[显示排查](xfce-desktop-20261004.md)、[触摸协议](touch-protocol-r2-20261004.md)
- [全屏只读状态面板、按需XFCE与启停入口](status-panel-20261004.md)
- [限充v1](charge-policy-v1-20261004.md)、[启动成功标记](boot-success-repair-20261004.md)
- [扩容](storage-expansion-20261004.md)、[电源键](powerkey-screen-20261004.md)、[局部发热](local-heating-20261004.md)
- [服务器基础](server-base-20261004.md)、[就绪盘点](server-readiness-20261004.md)
- [校园Wi-Fi](gdut-wifi-20261004.md)、[MC暂停交接](mc-tunnel-paused-20261004.md)、[MC提示词](mc-cnb-resume-prompt-20261004.md)

## 规范与来源

[备份恢复](backup-and-recovery.md)、[Git管理](git-workflow.md)、[社区调查](community-status.md)、[设备调查](device-assessment.md)、[面板证据](panel-evidence.md)。版本及社区结论是注明日期/提交的调查快照，升级前重新核对。

## 历史阶段

| 阶段 | 记录 | 阅读限制 |
| --- | --- | --- |
| 原厂调查与备份 | [临时root](temporary-root-plan.md)、[解锁预检](unlock-preflight.md) | 手机现为Linux，原Android命令与单次授权不可直接复用 |
| 主机准备 | [启动准备](linux-boot-preparation.md)、[服务器集成](debian-server-port.md) | 未启动/未部署/4GiB候选是历史状态 |
| RAM启动 | [首次失败](ram-firstboot-20261003.md)、[对照成功](ram-boot-success-20261003.md) | 后续已持久部署，授权范围见AGENTS.md |
| 持久r1 | [r1部署](persistent-r1-20261003.md)、[用户空间盘点](userspace-audit-r1-20261004.md) | 当前为charge-policy内核，不用r1触摸接管 |
| 早期充电 | [调查](charge-control-research-20261004.md)、[30秒候选](charge-control-candidate-20261004.md) | 已有后续v1常驻，不把早期未实现当当前结论 |

local中的旧交接、原始日志、凭据和恢复材料是私有证据，不复制到公开图表。

MC最新修复交接在 `local/worktree-mc/mc/FIXES-20261005.md`；`local/ssh443-trial-20261005/README.md`是已清理的临时SSH试验，酒馆后续独立常驻通道见迁移专题。现有FigJam/流程图仍为10/04版本，本次未更新。
