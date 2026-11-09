# OnePlus 8T 常驻个人 Agent 方案调查（2026-10-08）

需求：同时承担个人助理（聊天、长期记忆、提醒、检索）与服务器／编程助手（巡检、执行限定脚本、修改指定项目代码）。本轮只读项目文档并调查官方资料，没有连接手机做新巡检、安装软件、调用现有模型或发送消息。

结论：优先在独立目录试用 **nanobot v0.3.5 + 云端模型 + 现有 NapCat 的独立接入**；**OpenClaw** 是更完整的备选；偏重编程执行、技能积累可优先评估 **Hermes Agent**。24 小时常驻可以是低负载等待消息／定时唤醒，不需要让模型全天连续推理。这台机器的主要限制是温升、供电与校园网络恢复，而非仅仅内存容量。

## 后续偏好：轻量且生态可用

用户进一步明确“要轻一点的，生态没那么差的”。据此将首轮范围收窄为nanobot和PicoClaw：**兼顾现成接入、可扩展性及维护方便，仍优先nanobot；更重视最小运行时和极低占用，评估PicoClaw。** 不把OpenClaw／Hermes的完整安装作为首轮默认方案。

nanobot先只启用NapCat、记忆、提醒及少量受限工具，云端推理；WebUI按需访问，不部署本地生成模型、浏览器服务或大量MCP子进程。v0.3.5仍有多项Python及文档处理基础依赖，停用功能并不会删除这些必装依赖，不能把当前版本称为极简单文件程序。[依赖证据](https://github.com/HKUDS/nanobot/blob/v0.3.5/pyproject.toml)

PicoClaw的生态入口并不只限其自有插件：官方文档提供MCP、ClawHub／GitHub技能发现和安装，因此适合追求更小运行时的试验。但技能依赖、宿主工具名称和权限仍需逐项核验，不等于完整兼容OpenClaw插件；MCP启动的Python／Node／浏览器服务也会增加整套占用。具体功能应对选用tag复核，当前main文档不代表任意发布版。[工具与技能配置](https://github.com/sipeed/picoclaw/blob/main/docs/reference/tools_configuration.md)

两者在本机的空闲及实际任务RSS都未测；不采用旧“4000行”或“10MB”宣传作为当前完整部署的资源承诺。

## 本机条件与证据边界

| 条件 | 最新相关文档记载 | 对部署的影响 |
| --- | --- | --- |
| 平台 | Debian 13 arm64，SM8250，`7.2.0-kebab-debian13-r3-slider+` | 使用 Linux ARM64 服务端安装路径；不是 Android／Termux 环境 |
| 内存 | 10/07约11GiB总内存、9.7GiB可用 | 足够评估一个云端推理的助理；这是历史采样，不是本轮实测余额 |
| 存储 | 10/07根剩约2.3GiB，`/srv`剩约214GiB；模型试跑后约212GiB | 应用、虚拟环境、缓存、记忆和备份放独立`/srv`目录，避免填满7GiB根盘 |
| 服务管理 | 10/08掉电恢复后14项常驻服务active、9项timer active，无failed unit | 延续普通用户、独立systemd、挂载依赖和日志限额的方式 |
| 消息入口 | 已有NapCat／QQ适配器；10/08进程在运行但账号尚未登录 | 可评估复用协议入口；不能把进程active当QQ可用或免扫码保证 |
| 网络 | 校园认证恢复、HTTPS、NTP及CNB入口在10/08恢复检查通过 | agent应优先使用出站连接；消息长连与模型请求仍需单独验证 |
| 模型通路 | 酒馆使用受限SSH转发访问Azure模型、摘要和嵌入网关 | 可作为模型接入候选，但未验证agent所需工具调用／结构化参数；不直接复用酒馆账号配置 |
| 本地推理 | ONNX Q8文本／视觉嵌入短测通过；初始连续请求CPU7采样74°C触发停止 | 默认用云端大模型；本地嵌入仅按需、串行评估，尚无本地生成模型验收 |
| 无人值守 | 有低电量掉电、一次原因未定重启、QQ会话失效记录；长期供电／温升未完整验收 | 应做逐步运行验收，不能直接宣称7×24可靠性已通过 |

本机依据：[当前状态](project-status-20261004.md)、[交接](handover-20261004.md)、[酒馆迁移](sillytavern-migration-20261005.md)、[性能修复](sillytavern-performance-20261005.md)、[桌面与机器人恢复](desktop-apps-recovery-20261006.md)、[嵌入短测](embeddinggemma-q8-trial-20261007.md)、[外部供电核验](power-path-verification-20261006.md)。文件中的旧“当前”按各自日期理解，10/08的2%电量／USB offline不是本轮实时状态。

## 候选比较

以下排序是针对本项目的判断，功能来自官方资料；全部尚未在本机安装验收。资源比较不包含大模型、浏览器、NapCat或MCP子进程，也没有把其它平台的宣传数字当作本机RSS。

| 方案 | 个人助理与执行能力 | ARM64／运行依赖 | 与现有QQ的关系 | 本项目判断 |
| --- | --- | --- | --- | --- |
| **nanobot** | WebUI／终端、长期记忆Dream、cron／heartbeat、文件／shell、MCP、模型路由 | v0.3.5要求Python≥3.11；发布说明列出Linux ARM64 wheel；有systemd部署说明 | v0.3.5文档直接支持NapCat正向WebSocket／OneBot v11 | **首选试用**，两类需求与现有入口匹配，Python环境容易独立维护 |
| **DeepSeek Harness（dsh）** | Web工作区、代码／工具执行、可组合Cordis插件、MCP、定时任务；长期记忆可外接 | 当前源码Node要求`^22.19.0或≥24`，系统原生包有Linux arm64；本机未测 | 已发现社区`dsh-onebot-qq`插件，支持NapCat正向／反向WS；兼容性未验 | **执行与插件扩展的重要候选**；开发预览，实际占用及个人助理接入需验证 |
| **OpenClaw** | 多渠道Gateway、工作区／记忆、Skills、工具权限、heartbeat及持久定时任务 | 官方有ARM64指南；当前Node支持24.16+（24.x）或26.1+，推荐26 | 官方QQ插件使用QQ Bot API；普通QQ账号的NapCat接入需另核插件／桥接 | **功能优先备选**，运行时和插件管理更多，不能使用酒馆Node20 |
| **Hermes Agent** | 记忆／用户档案、会话检索、技能积累、MCP、cron、local／Docker／SSH等执行后端 | 官方列Linux aarch64为Tier 1；完整安装会准备独立Python／Node等依赖 | 已有官方QQ Bot API适配器；不等于现有NapCat可直接接入 | **执行与技能积累优先**，适合编程工作；首轮只启用需要的工具 |
| **AstrBot** | QQ聊天、人格／知识库、MCP／Skills、Agent工具及插件、FutureTask | Python3.12+；本机Python依赖安装、插件及镜像架构尚需验证 | 官方OneBot v11反向WebSocket教程直接覆盖NapCat | **QQ体验优先**；主动定时能力官方仍标实验性，关键任务宜用独立timer |
| **PicoClaw** | Go实现，记忆、工具／MCP、定时任务、多个渠道 | 有Linux arm64预编译包，不需要Python／Node主运行时 | OneBot及官方QQ Bot为不同接入方式 | **低占用实验候选**；官方仍提示v1.0前不要用于生产，不选作首个关键常驻助理 |
| **QwenPaw（CoPaw入口现跳转至此）** | 控制台、多渠道、记忆、Skills／MCP、定时与工作流 | 官方快速入门要求Python3.11～3.13；本机ARM64安装组合未验 | 官方文档同时提供QQ Bot及NapCat／OneBot v11 | **界面／工作站功能优先备选**，范围较大，需要审查依赖及默认功能 |
| **ZeroClaw** | Rust运行时、记忆、工具、安全策略、cron／事件触发SOP、Web控制台 | 具体ARM64发布资产及本机sandbox能力需部署前确认 | 本轮未核实与当前NapCat的直接兼容 | **后备研究候选**，本轮证据不足以排在可直接匹配入口的方案之前 |

官方证据：

- nanobot：[v0.3.5发布说明](https://github.com/HKUDS/nanobot/releases/tag/v0.3.5)、[固定版本依赖](https://github.com/HKUDS/nanobot/blob/v0.3.5/pyproject.toml)、[固定版本消息渠道](https://github.com/HKUDS/nanobot/blob/v0.3.5/docs/chat-apps.md)、[自动任务](https://github.com/HKUDS/nanobot/blob/v0.3.5/docs/automations.md)、[systemd部署](https://github.com/HKUDS/nanobot/blob/v0.3.5/docs/deployment.md)。PyPI元数据仍标Alpha；“首选试用”不等于生产稳定性认证。
- OpenClaw：[ARM64指南](https://docs.openclaw.ai/install/raspberry-pi)、[Node兼容策略](https://docs.openclaw.ai/install/node-compatibility)、[heartbeat](https://docs.openclaw.ai/gateway/heartbeat)、[定时任务](https://docs.openclaw.ai/automation/cron-jobs)、[官方QQ插件](https://docs.openclaw.ai/channels/qqbot)。
- Hermes：[官方仓库](https://github.com/NousResearch/hermes-agent)、[平台支持](https://hermes-agent.nousresearch.com/docs/getting-started/platform-support)、[Gateway与systemd](https://hermes-agent.nousresearch.com/docs/user-guide/messaging)、[执行后端](https://hermes-agent.nousresearch.com/docs/user-guide/configuration)、[cron](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron)、[QQ Bot接口](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/qqbot)。
- AstrBot：[官方仓库](https://github.com/AstrBotDevs/AstrBot)、[NapCat接入](https://docs.astrbot.app/platform/aiocqhttp.html)、[主动能力及实验性标记](https://docs.astrbot.app/use/proactive-agent.html)。
- PicoClaw：[官方README及部署成熟度提示](https://github.com/sipeed/picoclaw)、[本轮latest指向v0.3.1](https://github.com/sipeed/picoclaw/releases/tag/v0.3.1)、[OneBot接入](https://github.com/sipeed/picoclaw/blob/main/docs/channels/onebot/README.md)。README中的10～20MB是上游说明，不是本机带全套工具的测量。
- QwenPaw：[旧CoPaw仓库入口](https://github.com/agentscope-ai/CoPaw)、[当前仓库](https://github.com/agentscope-ai/QwenPaw)、[快速入门](https://qwenpaw.agentscope.io/docs/quickstart)、[QQ及OneBot渠道](https://qwenpaw.agentscope.io/docs/channels)。
- ZeroClaw：[官方仓库与安全／SOP说明](https://github.com/zeroclaw-labs/zeroclaw)。

本轮检索到的nanobot latest为v0.3.5、PicoClaw latest为v0.3.1；不能仅依据README旧新闻判断最新发布。除nanobot固定tag资料外，其它多数链接是滚动文档，实施时仍需锁定选用版本与依赖，不能把main的新功能默认为任意稳定包都包含。

## dsh补充调查

用户进一步询问dsh。本轮核对的项目为DeepSeek官方[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)，此前候选列表遗漏，现补充。

它以Cordis插件树组成运行时，提供Web、headless、sdk、sdk-minimal及acp等profile。手机端应优先评估Web服务端或按需组合的profile；使用Web服务端可不启动Electron桌面应用，但插件化并不证明整套依赖／内存必然小于nanobot或OpenClaw，当前没有本机测量支持这种排序。[架构](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md)、[CLI运行模式](https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/cli/README.md)

当前master的[根依赖声明](https://github.com/deepseek-ai/deepseek-harness/blob/master/package.json)要求Node`^22.19.0 || >=24.0.0`，不能沿用酒馆Node20。[系统原生包](https://github.com/deepseek-ai/deepseek-harness/blob/master/native/system/README.md)提供Linux x64／arm64的Landlock与文件锁组件，但对应本机配置、选用版本、PTY和所有插件仍须短测，不把源码支持矩阵当部署通过。

需要区分三种能力：

- **定时任务**：官方提供持久定时任务，文档说明Host重启后仍可恢复，周期任务补交最近一次遗漏。`headless`是一次任务后退出的模式；内置schedule不能单独放到纯headless／SDK组合，因为投递需要Host Web Session控制器和持久化后端。若采用极简定时脚本，另用systemd timer启动单次任务即可，但它与完整个人助理常驻会话是不同部署结构。[调度实现及限制](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/schedule/schedule/README.md)
- **长期记忆**：有[官方第三方记忆MCP接入指南](https://deepseek-harness.github.io/deepseek-harness/guide/mcp-memory)，参考配置默认关闭；上游数据库、模型、嵌入和账户由提供方负责，不能说默认配置已具备完整个人记忆。
- **QQ渠道**：社区[dsh-onebot插件](https://github.com/mario841859784/dsh-onebot/blob/main/README.en.md)宣称支持NapCat／OneBot11、正向及反向WS、重连、按聊天的agent会话等；它不是DeepSeek官方渠道实现，本轮没有审计源码或验证与本机适配器的共存。不能因官方核心支持插件就自动信任插件权限。

DeepSeek[官方产品页](https://deepseek.com/en/harness/)标为公开预览，定时任务标实验性；仓库README说明可能发生不兼容变更。本轮releases列表首项为`dsh-v0.2.1-alpha.1`，latest跳转到列表，不能表述为已获得稳定发布。试用应锁定版本、审查所需插件，不能常驻使用浮动`npx latest`。[发布列表](https://github.com/deepseek-ai/deepseek-harness/releases)

按“轻量＋生态可用”的要求，dsh可以与nanobot一起进入首轮短名单：**以现成QQ／记忆／提醒助理为主，nanobot接入更直接；以编程／服务器工具和可组合插件为主，dsh值得优先试测。** 若最看重极小运行时，PicoClaw仍是另一方向。选择dsh并不要求必须使用DeepSeek模型，官方配置指南覆盖其它提供方及OpenAI兼容端点。[模型配置入口](https://deepseek-harness.github.io/deepseek-harness/en/guide/quickstart)

### dsh桌面端在本机的可用性

结论：**当前官方Electron桌面版不能直接作为Debian ARM64应用安装使用。** 官方[Desktop README](https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/desktop/README.md#packaging)明确说明Linux不是受支持的桌面发布目标，当前目标只有mac-arm64、mac-x64、win-x64。macOS arm64包不能因CPU架构相同而用于Linux。

不仅缺少现成安装包：[桌面路径解析源码](https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/desktop/scripts/desktop-build-paths.mjs)会拒绝linux-arm64，[开发启动器](https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/desktop/scripts/dev.ts)也调用该解析器。因此不能承诺直接clone后运行start:desktop就可用；若坚持原生Electron桌面，需要另做Linux目标、配套运行时、打包和实际Wayland／输入验收。源码含Linux分支不等于桌面发行版受支持。

较合适的路径是手机独立运行dsh Web服务，用现有Phosh中的Firefox访问回环地址，可再制作桌面入口；后端由独立systemd常驻，关闭浏览器不停止后端。官方说明桌面壳封装完整Web应用，Web可提供主要工作区及任务界面，但不能据此宣称所有桌面专属能力相同。本机已安装Firefox／Phosh，DSI scale=2；dsh实际页面、窄屏布局、触摸、中文软键盘、占用与温升本轮尚未测试，也未安装服务或图标。

### 是否适合24小时个人助理

后续按用户要求使用gh调查社区插件，已找到文件型／SQLite记忆、个人任务工作台、OneBot桥、Host主动调度、持久自治和成套飞书私人助理。最新补充见[dsh个人Agent插件调查](dsh-personal-agent-plugins-20261008.md)，它覆盖此前只依据官方核心与MCP记忆指南所作的接入成本判断。

当前判断：dsh已具备可组合成个人agent的现成模块，适合进入本项目轻量试跑；初始可选Web服务＋文件型记忆＋一个OneBot桥＋官方调度。它的插件生态不必从零搭建，但组合成熟度、版本兼容、占用及7×24恢复仍需本机验证。不仅凭默认核心缺少完整记忆就将其排在nanobot之后。

其记忆参考配置默认关闭，上游存储及召回策略需要接入；QQ依赖社区插件；调度虽然有持久化机制，任务结果经过QQ端投递、掉线恢复、去重仍须验证。还应区分保存任务目标与自动恢复执行：[官方goal包说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/goal/goal/README.md)指出目标状态可以跨进程重启保存，但继续执行的许可是进程本地状态，goal服务本身不调度任务。不能把“goal还在”描述为重启后自行继续干活。

nanobot也有Alpha标记，本项目没有验证任一框架的长期稳定性。推荐nanobot的理由是现成助理功能和当前NapCat接入更直接，预计集成工作较少；dsh的优点是执行工作区、可组合插件及扩展自由度，代价是需要维护更多集成部件。本轮没有新的本机资源／恢复测试，不能据此给出RSS大小或无人值守可靠性排名。

## 推荐部署结构

建议第一版只部署一个agent运行时，分开保存助理聊天与编程任务的会话／工作区。多运行时互相调用、浏览器自动化、本地生成模型可留到后续。

消息来自QQ／网页／飞书等入口，交给手机上的独立agent服务；手机持有自己的记忆、任务状态和工具权限，推理请求发到经过兼容性验证的云端提供商。巡检先由本地脚本读取现有状态，只有发生变化或用户布置任务时才唤醒模型。

建议目录为`/srv/personal-agent/`，内部区分运行环境、私有配置、记忆／会话、编程工作区及备份。使用独立普通用户；systemd明确依赖`/srv`挂载并检查挂载点，失败重启设退避。应用启动不依赖用户一直登录：nanobot默认用户服务应配置linger，或编写等效的系统级普通用户服务。这里是部署建议，没有创建目录、用户或unit。

首先用Python虚拟环境／独立运行时加systemd，安装前检查依赖和磁盘预算。Docker是可选的执行隔离手段，不能直接假定本机已安装或所有镜像支持arm64。systemd重启策略负责进程恢复，不保证聊天平台登录、模型可用或任务不重复。

### QQ与网页入口

1. **QQ作为日常入口**：nanobot可连接NapCat正向WebSocket；AstrBot和QwenPaw文档提供反向WebSocket。先只读确认现有NapCat配置及适配器连接方式，再添加独立端点／token和本人白名单，不覆盖原`/cnb`机器人。使用明确的助理触发条件，避免两个程序重复响应或互相触发。即使协议兼容，仍需验证并发订阅及重连。
2. **区分两类QQ**：NapCat使用已登录的普通QQ账号；官方QQ Bot API需要开放平台的App ID／Secret等条件。框架写“支持QQ”不表示能接管当前账号。现有掉电后扫码需求属于平台登录边界，不能由agent的systemd自启消除。
3. **网页用于维护与备用聊天**：仅绑定回环，经严格SSH转发访问。若以后需要公网，沿用现有CNB恢复模式另行设计独立认证与通道，不直接把酒馆网页的转发目标换成agent，也不把无鉴权控制台公开。
4. **可选飞书／钉钉出站长连**：nanobot文档有飞书WebSocket和钉钉Stream接入，可减少对公网入站的依赖；仍需用户创建应用、配置权限并验证校园网络可达。

### 模型与记忆

- 首选云端生成模型。具备中文聊天能力不等于适合执行工具，需验证多轮工具调用、参数类型、失败重试及长任务终止。
- 当前Azure通路可以做候选，不保证现有反代正确转发`tools`／`tool_calls`等agent字段。先用无副作用工具做测试；现有酒馆生成返回的token上限／usage异常，不能直接用于费用预算或上下文上限证明。
- 为agent分配独立凭据与额度，测试SSE、超时、429及断线；回退模型不能悄悄改变权限或无限重试。
- 先用框架自带记忆和会话检索，再决定是否增加向量库。不要直接导入酒馆私聊／角色记忆作为个人助理知识；需要用户选定资料和用途。
- EmbeddingGemma可作为后续按需文本索引候选，限定并发并错峰；本轮不把现有短测扩大为常驻模型／GPU或NPU加速已通过。

### 服务器与编程执行边界

首版服务器工具只读取`/run/kebab-health/status.json`、限定服务的状态／摘要日志和磁盘余额；使用小型确定性采集脚本，避免LLM不断轮询整机。编程工具仅能写独立项目工作区，保留diff及验证输出。

若以后需要服务重启，使用逐项允许的包装命令；agent不获得任意sudo、Docker socket或现有基础服务的配置写权限。充电／温控、电源键、USB SSH、内核、分区及校园认证不在普通个人agent工具写集内。工具权限应由实际文件权限／隔离机制限制，不能只在提示词里约定。网页、邮件和检索内容属于外来数据，不是提升权限的指令。

这两类工作可以在一个运行时内路由到不同会话／工作区；若所选版本无法对工具权限作可靠区分，再拆成两个不同系统用户的实例。框架的“多agent”名称本身不是权限隔离证据。

## 24小时运行方式与费用

常驻服务平时等待消息；定时器执行确定性采样，模型只处理需要理解／决策的内容。建议初始关闭无目标的自动思考，只启用用户确实需要的提醒、日报和变更告警。

OpenClaw官方常规heartbeat默认30分钟一次，某些认证方式默认1小时；前者调度频率为48次／天、30天约1,440次，后者为24次／天、约720次。实际模型请求次数还受跳过条件、重试和每次工具循环影响。这只是调度频率，不是月费估计。[依据](https://docs.openclaw.ai/gateway/heartbeat)

费用估算应按实际输入／输出token、提供商单价、工具调用及重试统计；还要考虑搜索／浏览器等独立收费。当前网关usage不可靠，应先核实上游计量。建议限制任务轮数、并发、最长时长及每日预算，并在触及预算时暂停非必要自动任务。没有调查或承诺固定月费。

为运行时设置CPU／内存／进程限制，先低并发短测后按实际峰值调整；这些限额是管理策略，不是温度保护。屏幕按原有操作使用，不为agent修改显示、充电或温控。QQ和模型入口均应有退避重连、健康标记；CNB回收不影响本地任务状态，但可能中断网页／远程管理入口。

## 分阶段验收建议

| 阶段 | 要验证的结果 |
| --- | --- |
| 安装与模型短测 | 固定版本／依赖／hash；ARM64正常启动；普通用户；模型工具调用、SSE、超时／失败处理通过 |
| 聊天与记忆 | 本人白名单；陌生人不能调用；重启agent后能检索之前明确保存的偏好；原QQ命令保持可用 |
| 提醒与服务器工具 | 一次提醒／日报能投递；重复事件不重复执行；读取状态成功；未允许的文件写入／命令被实际拒绝 |
| 程序恢复 | 单独重启agent后任务仍在；模型短时失联后退避恢复；运行中任务有明确终态或可恢复状态 |
| 重连与持久性 | 消息连接断开重连；遗漏任务按指定规则补跑或跳过；涉及消息发送时有去重／状态记录，不能承诺天然exactly-once |
| 有界运行 | 先2小时，再24小时，最后72小时低负载运行；记录服务恢复、内存、CPU、温度采样、日志增长和费用，不主动高负载压测 |
| 设备级恢复 | 在用户协调的正常重启中检验/srv、校园网络、QQ登录、agent任务恢复；不自动重启手机或重复造成掉电 |
| 备份恢复 | 记忆／数据库使用合适的一致性备份；保存版本与配置；备份离机并实际恢复测试，不只在同盘复制 |

热症状复现、未知重启或电池异常应先停止本次agent负载并按原项目流程排查；手机传感器采样不能代替USB接口表面温度或长期供电验证。

## 最终选型建议

先试 **nanobot v0.3.5**：独立Python环境与普通用户、本人QQ／备用WebUI、云端模型、基础记忆、一个提醒和只读巡检工具。它能用较少接入工作覆盖本次两类目标，但Alpha标记及本机长期运行仍需验证。

如果后续更重视完整生态、丰富渠道与多工作区管理，选 **OpenClaw**，为它独立准备兼容Node，不升级或替换酒馆Node20；若更重视执行工作、技能自我积累和丰富终端后端，选 **Hermes**，先限制本地执行范围并单独解决消息入口。

如果核心体验是QQ陪伴／群聊与中文插件，可改选 **AstrBot**，关键备份／巡检调度继续由独立systemd timer承担。PicoClaw留作节省资源的实验；QwenPaw、ZeroClaw属于其它侧重点的备选。本轮没有锁定下载来源到`sources.lock.json`，因为尚未下载或决定部署任何第三方运行时，也没有Git提交／推送。
