# dsh 个人 Agent 插件调查（2026-10-08）

用户要求用环境中的gh寻找dsh个人agent插件。本轮执行GitHub公开仓库搜索、固定提交README／包清单查询，并只读检查部分关键实现；没有安装、构建或运行插件，没有连接手机、发送消息或调用模型。

结论：**dsh社区确实已有个人助理模块，也有接近成套的个人助理插件。对“轻量＋QQ＋记忆＋提醒＋工具执行”，已有可进入短测的组合，不必从零编写整套助理。** 之前只依据官方核心／第三方MCP指南判断“记忆需要另搭、接入成本较大”，低估了原生插件生态，现据实际仓库调查补充修正。生态已有候选不等于组合经过本机验收。

## 调查方法与限制

- gh 2.96.0；查询包括dsh memory、personal assistant、onebot、proactive、heartbeat、companion及插件索引。
- 对11个候选查询README、包清单或明确的功能缺口；重点读取auto-memory入口、proactive配置／唤醒及muse恢复钩子。
- 本机沙箱默认连接现有代理失败，随后只读网络查询经工具审批成功；没有改变代理或GitHub认证配置。
- 下面以实际读取的commit SHA为准。GitHub updatedAt可以因Issue等变化更新，不用它当代码提交日期；star数不作为成熟度证明。
- 包清单少依赖是静态事实，不代表整套RSS、CPU或token消耗已测。测试文件存在不代表本轮运行测试或证明可靠。
- 公开查询元数据／包清单与关键源码留在Git忽略的local/dsh-plugin-research-20261008/；本文件不含账号、token或实际手机地址。

## 适合轻量组合的插件

| 插件 | 已核对能力 | 依赖／运行方式 | 对本机的建议 |
| --- | --- | --- | --- |
| AskTheWay/dsh-auto-memory | 分类型记忆文件、MEMORY.md索引、用户／项目范围、按步骤注入、Web管理、可选会话结束总结 | 0.6.1；额外直接运行依赖为schemastery和yaml，另有dsh宿主peer依赖；不需要embedding或独立记忆服务器 | **首选记忆试验候选**。默认autoSummarize=false；索引注入默认4096字节，先保持后台总结关闭 |
| Phant0Meow/dsh-meow-memory | 七层SQLite记忆、BM25／近期权重检索、人格／用户资料、项目记忆、反思与dream | 0.30.0；使用node:sqlite；包清单没有dependencies段，构建时有宿主／esbuild依赖 | 更丰富的记忆候选。默认反思和dream开启；低成本试验先关自动整理，并核验选用Node的SQLite与当前dsh兼容 |
| csyangwen/dsh-memory-evolve | 分层记忆、技能／待办、提示词管理、会话协作及外部AI调度 | 0.1.0、private包；包清单没有dependencies段，GitHub／本地安装路径；多项能力默认关闭 | **综合功能备选**。范围明显大于纯记忆插件，不与另一个记忆引擎叠装；缺少清晰peer版本范围不能当作自动兼容 |
| john-walks-slow/dsh-proactive | Host级闹钟、一次／循环／cron、冷会话resume、安静时段、投递限额、静默退出、Web管理 | 0.2.3；未声明直接dependencies，依赖dsh宿主能力，peer围绕0.2.0-rc.1／Cordis4.0.4 | 需要主动跟进时的候选。先限唤醒频率；不是“不输出就不计费” |
| ttmouse/dsh-muse | 持久人类授权、goal恢复武装、routine定时闭环、静默跟进、附带文本记忆工具 | 0.1.0-rc.7；多个包／goal家族，peer围绕0.2.0-rc.2；部分文档提到跨版本豁免 | **第二阶段自主任务候选**。能补核心重启后goal执行授权缺口，但安装组合与事件／外部通知还需验证 |
| Dely0/dsh-personal-workbench | 日历、任务树、自然语言录入／澄清、AI执行／复盘、日报周报、到期提醒 | 1.16.5；直接dependencies为React，Web客户端及宿主peer；数据在本地workbench目录 | **可选任务面板**。不必为了后台QQ聊天常驻加载；手机窄屏／触控待测 |
| JackZo400/dsh-onebot | 标准OneBot11正向WS／HTTP、私聊／群聊、每聊天agent、重连及回声过滤 | 0.1.0；包清单无dependencies，Node内置fetch／WebSocket | **最小QQ桥候选**。README作者也推荐其它覆盖更广的实现；是否适配当前dsh和NapCat必须实测 |

固定证据：

- [auto-memory README](https://github.com/AskTheWay/dsh-auto-memory/blob/c5454206a55564d20e709318d854bde0f5c3c794/README.md)、[依赖](https://github.com/AskTheWay/dsh-auto-memory/blob/c5454206a55564d20e709318d854bde0f5c3c794/package.json)、[默认配置及注册实现](https://github.com/AskTheWay/dsh-auto-memory/blob/c5454206a55564d20e709318d854bde0f5c3c794/src/index.ts)。
- [meow-memory README](https://github.com/Phant0Meow/dsh-meow-memory/blob/8a53cc99a2a3b870eeaa38a33ca396228d6cb66c/README.md)、[依赖](https://github.com/Phant0Meow/dsh-meow-memory/blob/8a53cc99a2a3b870eeaa38a33ca396228d6cb66c/package.json)。
- [memory-evolve README](https://github.com/csyangwen/dsh-memory-evolve/blob/e6f6b84c56f0fc18ba4a6a1a5959fbf86ea9ec8b/README.md)、[包清单](https://github.com/csyangwen/dsh-memory-evolve/blob/e6f6b84c56f0fc18ba4a6a1a5959fbf86ea9ec8b/package.json)。
- [proactive README](https://github.com/john-walks-slow/dsh-proactive/blob/9ec11e69a29e06f8db562356ec8623fb4773a9fb/README.md)、[依赖](https://github.com/john-walks-slow/dsh-proactive/blob/9ec11e69a29e06f8db562356ec8623fb4773a9fb/packages/dsh-proactive/package.json)、[配置](https://github.com/john-walks-slow/dsh-proactive/blob/9ec11e69a29e06f8db562356ec8623fb4773a9fb/packages/dsh-proactive/src/config.ts)、[唤醒实现](https://github.com/john-walks-slow/dsh-proactive/blob/9ec11e69a29e06f8db562356ec8623fb4773a9fb/packages/dsh-proactive/src/wake.ts)。
- [muse README](https://github.com/ttmouse/dsh-muse/blob/2d44c429c1af02f2da2283066490f29918dbea94/README.md)、[恢复钩子](https://github.com/ttmouse/dsh-muse/blob/2d44c429c1af02f2da2283066490f29918dbea94/muse/src/index.ts)、[范围与缺口](https://github.com/ttmouse/dsh-muse/blob/2d44c429c1af02f2da2283066490f29918dbea94/docs/gaps.md)。
- [workbench README](https://github.com/Dely0/dsh-personal-workbench/blob/787b3d8829066e8fb9a5a627f3cd5538f5327826/README.md)、[依赖](https://github.com/Dely0/dsh-personal-workbench/blob/787b3d8829066e8fb9a5a627f3cd5538f5327826/package.json)。
- [OneBot README](https://github.com/JackZo400/dsh-onebot/blob/eb449d46410dbfe565f3c6078e033d22c5c9a6d2/README.md)、[包清单](https://github.com/JackZo400/dsh-onebot/blob/eb449d46410dbfe565f3c6078e033d22c5c9a6d2/package.json)。

本机还可复核先前已发现的[mario841859784/dsh-onebot](https://github.com/mario841859784/dsh-onebot)：其文档覆盖正向／反向WS、较完整的媒体工具和命令。本轮未重新锁定该仓库提交或审计源码，不用其功能说明代替当前手机适配测试。首版只能选一个负责助理消息的桥，保留原/cnb机器人，验证是否发生重复响应／回声。

## 找到的成套或领域助理方案

| 项目 | 能做什么 | 本轮筛选结果 |
| --- | --- | --- |
| benz-ai-x/dsh-24-PA | “24私助”飞书＋本地双入口，六类业务worker、任务／提醒／笔记／摘要、PostgreSQL账本及outbox | 功能最接近成套私人助理；作者说明可在模型离线时投递固定提醒。但多worker、数据库和飞书凭据不符合本轮轻量QQ优先；不能据README宣称已在本机通过 |
| ChengqianHuang/dsh-personal | 自然语言维护经历、项目、任务、博客、想法和每日记录；单SQLite库 | 个人资料工具模块，不是已核实的全天消息／主动调度方案；jieba-wasm直接依赖，源码构建模式，schema变更需迁移安排 |
| HiQ-AI/dingtalk-dsh-assistant | 钉钉群聊常驻助理与任务看板 | 包清单使用PowerShell启动／安装脚本、workspace依赖及旧dsh0.1.2开发依赖，许可标UNLICENSED；本轮不推荐直接移到Debian |
| xmanrui/dsh-im | 多渠道IM接入、上下文增强、工作区／模型／会话绑定和主动消息路线 | 扩展渠道时可选，4.37.0包清单有多平台SDK／邮件库及可选sharp；兼容声明是dsh0.2.0-rc.2。QQ文档和依赖指向官方QQ机器人路线，不能当当前NapCat替代 |

固定证据：[24私助](https://github.com/benz-ai-x/dsh-24-PA/blob/763c8227902937fd6b807d8788e07b6dcdf7d46f/README.md)、[个人资料插件](https://github.com/ChengqianHuang/dsh-personal/blob/13b12b6ed31a6c99193ece203741fe7a8bdfe588/README.md)、[钉钉包清单](https://github.com/HiQ-AI/dingtalk-dsh-assistant/blob/806ef59714fbc7e899574c1dfd44500e7054c0b1/package.json)、[多渠道IM依赖与兼容声明](https://github.com/xmanrui/dsh-im/blob/0dbc2d46af9c4e9142f930d85ecc483d3d971f40/package.json)。

24私助仓库本轮查询创建于10/05，muse创建于10/04，file-only auto-memory创建于9/22。早期项目有实用设计与测试入口，但不能把近日提交或丰富README当长期生产成熟度证明。许可证API未识别不等于确定无许可；24私助许可本轮未进一步核验。插件处于DeepSeek命名空间或使用相似包名，也不证明由DeepSeek官方发布。

其它检索到的heartbeat、persona、语音、桌宠及内容发现插件，本轮没有逐一读源码；不将它们全部计作适合本机的已验证生态。社区索引可从[awesome-dsh-plugin](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin)继续查找，收录不是安全或兼容性认证。

## 关键事实与实施取舍

### 少依赖不等于少调用模型

auto-memory源码默认autoSummarize=false，可先让模型在用户明确要求时保存偏好，避免额外后台总结调用；记忆注入仍会占用正常生成的输入token。

proactive配置默认maxDeliveriesPerDay=50、maxWakeupsPerHour=60、bootOverduePolicy=fire。源码注释明确前者计算可见聊天文本投递数量，不是token／金额预算；静默唤醒仍进入agent／模型流程。因此不能只把每日消息数量调低就宣称费用受控。部署前应限制唤醒次数、最长回合、模型预算，并决定重启后的过期任务是补跑、只提示还是丢弃。

meow-memory默认reflect=true、dream.enabled=true；这些设计适合自动维护，但首轮应关掉非必要整理，再测记忆质量与额外调用。关闭开关是否完全阻止相关任务，需在实际profile验证。

### 跨重启自治有插件补充，但需要明确恢复语义

muse源码的默认自治开关为false，routine轮询默认60秒；恢复钩子读取持久授权，监听agent创建／idle，仅对active、未达到回合上限且尚未武装的goal恢复。它没有把配置开关当作人类授权。说明文档的旧缺口清单之前仍保留，阅读时必须先看10/05增补，不能把已经补上的routine恢复再次描述为完全未实现。

这提供了核心goal“只持久保存状态、继续许可不跨进程”之外的补充路径，但本轮未运行真实重启闭环，也未验证该路径与QQ桥、主动插件、调度和模型选择同时使用时的行为。不要第一版同时开启多个自动续跑／心跳机制。

### 版本兼容是组合成立的前提

多个插件围绕dsh0.2.0-rc系列，官方核心当前已见0.2.1-alpha系列；有的声明范围较宽，有的没有peer范围。预发布semver与实际DSH兼容门禁必须核验，不能凭“≥某版本”或package.json外观推断一定兼容。不直接照作者建议豁免核心版本门禁，也不在未知冲突时混入重复宿主运行时。

## 推荐给本机的组合

**第一阶段：dsh Web＋AskTheWay/dsh-auto-memory＋一个NapCat／OneBot桥＋官方schedule。**

- 继续用独立普通用户和/srv应用／数据目录，systemd管理后端；浏览器只作界面。
- 只对本人开放助理命令／消息，保留原/cnb机器人。
- 云端模型推理，初始关闭自动记忆总结及高频heartbeat，不添加向量数据库、本地生成模型或浏览器执行服务。
- 官方schedule完成用户确实布置的提醒／日报；服务器巡检先由确定性脚本采集，变化才进入agent。
- 先测记忆跨会话／跨进程读回、QQ断线／登录恢复、任务去重、无权限命令被拒绝和服务独立重启。

**第二阶段按实际需求选一项**：主动跟进选proactive；长任务跨重启继续选muse；需要任务日历界面选workbench；更丰富的记忆可换meow-memory或memory-evolve。不一次叠满，也不同时让几个引擎对同一聊天自动存储／注入／总结。

因此，dsh可以作为本项目个人agent的正式试验候选；选择它不等于先承诺7×24可靠性或比nanobot内存更低。已有足够现成模块可开小规模试跑，剩余关键工作是锁定兼容版本、选择最小组合、检查插件权限并取得本机验证。总方案和系统边界见[原调查](personal-agent-options-20261008.md)。
