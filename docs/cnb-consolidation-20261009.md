# CNB 单实例合并（2026-10-09）

10/12 18时更新：原酒馆固定入口的Worker地址发现发布故障已修复，控制器先保存已验证status再进行旧仓库Git发布；共享实例和原隧道保持。见[固定入口修复、验证及限制](cnb-fixed-entry-repair-20261012.md)。

10/12 17时更新：宿舍 Wi-Fi 掉线并回退 gdut 导致 CNB 公网 500/EOF，重新选择既有宿舍网络后原隧道自行恢复；原认证首页与主脚本公网 200 完整通过，地址与共享 workspace 不变。见[本次恢复与限制](cnb-recovery-20261012.md)，下文合并验收为历史记录。

已按用户要求，把现有酒馆、SSH、MC 中继及 `cnb-mcp` 的占位服务／Cloudflare 连接器合并到 **一个 1 核 CNB 开发实例**，复用原酒馆仓库 `zc354/kebab-sillytavern-relay` 的 main 分支。完整账户列表复核仅一条 running；原 MC 和 cnb-mcp 实例均已 closed。保留原仓库、应用数据、凭据和回退资料，没有删除手机或 Azure 的应用数据。

## 服务与端口

| 服务 | 共享实例端口 | 实际服务端／用途 |
| --- | --- | --- |
| SSH 中继 | 9001 | 手机回环 22；原凭据、主机密钥及权限保持 |
| 酒馆中继／HTTPS | 9002／9003 | 手机回环 8000；原 Basic Auth 登录 |
| DSH 中继／HTTPS（10/10 增补） | 9011／9013 | 手机回环3082；社区bridge全范围密码认证，共用同一workspace |
| MC 中继 | 9010 | 手机回环 25565；保留原隧道密钥与好友令牌 |
| 原 cnb-mcp 占位服务 | 9000 | 原二进制独立运行；尚未实现真正 MCP 服务 |
| 原 Cloudflare 连接器 | 出站连接 | 保留原固定域名与隧道凭据，源站仍为回环 9000 |

这些服务使用同一 workspace，不因独立端口和进程再计一个开发实例。酒馆／SSH／MC 保留独立认证通道。模型反代仍在 Azure，酒馆和 DSH 数据仍在手机。10/09合并时未启用DSH公网；随后按用户全天常驻及公网要求部署9011/9013，10/10列表仍仅一个running。见[DSH访问与维护](dsh-always-on-access-20261009.md)。

## 统一恢复与旧保活器

手机 `kebab-strelay-controller.service` 是唯一自动启动 workspace 的控制器：完整分页读取列表，严格匹配共享仓库和 main，拒绝多实例歧义；没有运行环境时最多每 10 分钟发起一次启动。严格核验已有 CNB SSH 主机密钥后，分别配置酒馆／SSH 与 MC／占位服务／Cloudflare 进程；敏感配置经 SSH 标准输入传递，保存在受限私有目录。

MC 规范配置为 `/srv/kebab-strelay/private/mc.env`，原 `/etc/kebab-mcbridge-client.env` 已备份并改为指向它的符号链接，客户端增加 EnvironmentFile 覆盖。既有仪表盘等读取原路径的程序能跟随最新地址。原 MC 和 cnb-mcp 仓库的 `endpoint.txt` 仍为旧客户端发现入口，由手机统一控制器在地址变化后更新并读取远端确认；失败的推送会重试。实际地址和凭据不写本项目公开文档。

已停用可能重新拉起旧实例的机制：

- 手机 `kebab-cnb-keepalive.timer` disabled/inactive；对应 oneshot 停止。
- 既有 ECS 上 `mc-cnb-watch.timer`、`cnb-mcp-watch.timer` disabled/inactive；对应服务停止，原配置保留。
- 原 MC 仓库的 CNB crontab 和 api_trigger_keepalive 配方已从 main 移除，并增加合并说明。

原 cnb-mcp 仓库的 crontab 原本未启用。不要手动启动旧仓库的开发环境或重新启用这些旧保活器。ECS 的历史只读观测／连接实验不负责业务启动，本次没有据历史文档猜测或删除其他服务。

CNB 共享仓库保存可重建脚本与经过核验的二进制；Cloudflare 采用固定 2026.9.3 发行包和原运行文件的 SHA-256 校验。监督进程有独占锁，仅管理自己启动的子进程；没有按二进制名批量杀其他服务。无需额外新增 CNB workspace。

## 实际验收

| 项目 | 结果 |
| --- | --- |
| 完整列表与实例数 | 初始 3 个 running；关闭旧实例后、共享实例重建后及最终复核均 1 个 |
| 酒馆 HTTPS | 返回 401，符合原 Basic Auth；此次没有模拟账号登录或修改聊天 |
| SSH 通道 | 经共享公网 WSS 转发收到手机 SSH-2.0 banner；USB 原严格 SSH 始终用于维护 |
| MC 中继 | 公网返回 426；确认没有 25565 服务后，临时回环 nonce echo 经现有隧道完整往返，探针随后退出 |
| cnb-mcp 占位入口 | 共享 CNB 地址与原 Cloudflare 固定域名均返回 426 |
| 关闭旧实例后 | 上述通道再次通过，固定域名不依赖旧连接器 |
| 整实例自动恢复 | 用户明确批准短暂中断后停止唯一实例；约 123 秒恢复为一个新实例，全部通道恢复、原仓库 endpoint 自动更新；新实例上的上述验收再次通过 |
| 重建后的持续配置 | 巡检发现启动阶段监督进程的相对路径／PID文件归属识别问题，已修复并保存主分支；连续三次配置 ready 且 new_supervisor=false，控制器状态随后恢复刷新 |

重建测试最初被自动审批拒绝，因为停止唯一生产入口超出合并授权；取得用户明确批准后才执行。没有在拒绝后绕过审批。

MC 本体此次仍无 25565 监听，不能把 nonce 验收称为 Minecraft 玩家／世界测试。cnb-mcp 目前是占位服务，不能称为可调用的 MCP。24 小时稳定性、所有好友设备、手机整机重启和实际账单未验收。CNB 平台回收仍会造成恢复窗口，共享实例故障会同时影响这些公网入口。

## 维护与回退资料

查酒馆最新地址仍在项目目录运行：

```powershell
.\scripts\Get-SillyTavernAccess.ps1
```

手机维护查看以下单元即可，不要重新开启旧保活 timer：

```sh
sudo systemctl status kebab-strelay-controller kebab-strelay-ssh kebab-strelay-http kebab-mcbridge-client
sudo journalctl -u kebab-strelay-controller -n 20 --no-pager
```

公开脚本在 `scripts/cnb/`，CNB 两组进程分别位于 `/workspace/sillytavern-relay/` 与 `/workspace/shared-relay/`。手机私有恢复材料在 `/var/backups/kebab-cnb-consolidation-20261009/`，主机凭据、原始配置／列表、二进制、验证及停止结果在受限且 Git 忽略的 `local/cnb-consolidation-20261009/`；文件 hash 与项目快照另行保存。归档是配置备份，不是运行中业务数据的一致性快照。

回退应先停止统一控制器，并依据归档恢复原文件及 MC 单元覆盖；原 MC 环境和 Cloudflare 连接器需要相应恢复。恢复多实例或重启旧保活器会重新增加核时，应作为明确的回退选择，不自动执行。手机内核、分区、充电、温控、校园认证、USB SSH 和图形服务配置未修改。

计量说明沿用[实例与用量核对](cnb-instance-audit-20261009.md)：从三个同规格运行环境降至一个，可减少重复开发核时；并未读取组织实际用量或账单，不保证零费用。
