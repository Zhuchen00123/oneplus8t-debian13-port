# 文档索引

## 当前入口

更新于2026-10-05。状态和交接文件保留10/04文件名，其顶部10/05增补为最新入口；基础系统历史验收与本次酒馆验收分别记录。

| 文档 | 内容 |
| --- | --- |
| [当前状态](project-status-20261004.md) | 通过、部分通过、未验收与待办 |
| [最新交接](handover-20261004.md) | 新会话入口、恢复点、授权与分工 |
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
