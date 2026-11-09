# DSH 远程访问与搜索插件调查（2026-10-08）

本轮读取电脑的 DSH desktop/web profile、实际安装包及插件声明，再核对公开 GitHub 源码和 npm 发布版本。没有捕获到桌面端窗口，因此以下是配置和源码取证，不是 GUI 或服务器运行验收。没有安装插件、复制凭据或改动手机服务。手机仍使用第一阶段 DSH 0.2.0-rc.2 / Node 24 / 默认 DeepSeek v4.1 Flash。

## 电脑上的实际配置

| 项目 | 本地证据与含义 |
| --- | --- |
| `@wenbin_wb/dsh-bridge` | desktop profile 的 bundles 已加入，patch `disabled=false`；实际安装包 2.12.0。它对应设置中的“远程访问”，不是模型反代插件 |
| `dsh-devin-search` | desktop profile 的 bundles 已加入；实际安装包 0.2.9。提供 `web_search` 与 `code_search` |
| Firecrawl MCP | desktop 和旧 web profile 均配置 `mcp-firecrawl`，使用官方 `@deepseek-ai/dsh-mcp-client`，`streamable-http` 连接 `mcp.firecrawl.dev`，配置了 Authorization header |
| `@anysearch/anysearch-dsh` | 旧 web profile 的 bundles 已加入，实际安装包 0.1.4；desktop profile 未加入 |

仅提取名称、版本、bundle 状态及公开服务域名；没有展示 header、API key、登录 token、完整配置或聊天。配置存在不能证明当前进程正在使用或账号仍有效。

## 远程访问候选

| 插件／查询到的发布版本 | 能力与本机取舍 |
| --- | --- |
| **dsh-bridge 2.12.2** | 电脑已有的方案。移动网页／PWA、二维码、访问门禁、自建隧道及多种 IM 接入；直接依赖为 qrcode、ws。源码含 Linux ARM64 的 cloudflared 分发映射，但不代表本机隧道验收。关键限制：代理硬编码监听 `0.0.0.0`，门禁缺省关闭；必须先做受控适配或隔离，不能直接照默认设置部署到校园网络 |
| **ds-harness-remote 0.5.0** | 将服务器作为 Host，电脑 DSH 桌面端／Web／Android 客户端连接同一远程工作区；作者声明端到端加密、优先 P2P、无需 Host 开放公网监听。明确覆盖 DSH 0.2.0-rc.2；默认连接作者托管 Relay，需要账号；提供可自建的最小 Server。依赖含 werift，方案组件较多。按既有“不试免费中继”边界，不能默认启用托管 Relay；自建 Azure Relay 是另一个部署任务 |
| **dsh-zen-remote 2.0.0** | 同包 host/client 两种角色；手机 PWA、配对网关、桌面侧栏远程会话、审批、终端、可选推送。peer 明列 0.2.0-rc 家族；源码默认网关 `127.0.0.1`、端口 3088，不自动共享新会话。可作为利用现有通道的候选；尚未核验我们的桌面版本与服务端组合，推送和额外网关不在本轮运行范围 |
| dsh-full-remote 0.3.12 | 配对／设备会话、反向代理、远程设置及可选隧道；但 README 主要验收 DSH 0.1.2-rc.1，peer 上限 `<0.2`，不作为当前 rc.2 的首选 |
| dsh-mobile-plugin 0.1.9 | 手机配对与出站 Relay；README 实测基线为 DSH 0.1.0-rc.6，兼容当前 rc.2 未证实，不作为首选 |

选择应按入口区分：**浏览器打开服务器**可先评估熟悉的 dsh-bridge，但须修正其默认监听和认证；**电脑桌面端直接管理服务器会话**更适合评估 ds-harness-remote 或 dsh-zen-remote。不要同时叠加多个代理／认证层。

当前 `.\scripts\Open-Dsh.ps1` 已提供经过认证的浏览器访问，远程插件的价值在于移动界面、配对、会话管理和桌面客户端连接，不是首次获得远程连接。校园公网入口仍优先复用既有受控 CNB/Azure 通道；新增服务必须独立端口和配置，不能覆盖酒馆或 MC 中继。不开启插件自带免费 Cloudflare／托管 Relay，不能将仓库 README 中的安装脚本直接当成项目部署步骤。

## 搜索方案

| 方案 | 能力、兼容及建议 |
| --- | --- |
| **dsh-devin-search 0.2.9** | 与电脑当前版本一致；peer 明确列出 DSH 0.2.0-rc.2，Cordis ≥4.0.4，本机锁定版本满足基础声明。原生 `web_search`、可选 `code_search`；直接 dependency 只有 ignore，搜索由云端承担。需 Devin 授权，支持服务器授权码回填模式。依赖非公开 Devin/Windsurf 接口，README 也承认协议和额度未长期保证 |
| **Firecrawl 官方远程 MCP** | 复用电脑现有接入方式，通过 DSH 官方 MCP client 接入官方服务；可搜索、抓取和解析页面，不必在手机另跑爬虫。官方 API-key 接入使用 `/v2/mcp` + Bearer header，OAuth 是独立地址；凭据应只留在受限存储。现有账号、额度及手机的真实工具调用尚未验证 |
| AnySearch 0.1.6 | 网页／垂直搜索、URL Extract、批量搜索，README 提供匿名体验。当前 package peers 最高声明到 0.1.6-alpha.2，并依赖旧 `dsh-tool-web` 等包；没有覆盖当前 0.2.0-rc.2，不能把电脑旧版的配置直接移植或强行绕过门禁 |

第一轮搜索推荐评估 **Devin 网页搜索 + Firecrawl 抓取/补充搜索**。若只需要网页搜索，Devin 设 `webSearch=true`、`codeSearch=false`：代码搜索会把相关代码片段发给云端，当前个人 agent 需求不必默认打开。Devin bundle 会将原生 `web.searchProvider` 指向 devin，保留 HTTP fetch；不要再让另一个插件同时覆盖同一个原生 provider。

clinepass 模型接入不等于获得某个搜索服务的登录／API 权限，也不能据 DeepSeek 模型名称认定内置 DeepSeek 搜索已可用。

## 来源与复验

均固定本次读取的 GitHub 提交；下载证据和源码位于 Git 忽略的 `local/dsh-remote-search-20261008/`，公开 lock 见 `sources.lock.json` 的 `dsh_remote_search_research`。以下是作者文档／静态实现，尚未在手机安装验收：

- [dsh-bridge README](https://github.com/wenbin-wb/dsh-bridge/blob/a4726a6542bea19d0a18a148035fe1120c6a537a/README.md)、[全网卡监听实现](https://github.com/wenbin-wb/dsh-bridge/blob/a4726a6542bea19d0a18a148035fe1120c6a537a/lib/index.js#L694)。电脑安装包 auth manager 构造函数的默认 enabled=false 与当前源码说明一致；未读取电脑实际门禁状态，不等于说电脑当前没开门禁。
- [ds-harness-remote 中文说明](https://github.com/liguobao/ds-harness-remote/blob/6a9113255d46c6bcd751909b646cc67ea1909c01/README.zh.md)、[默认服务和角色配置](https://github.com/liguobao/ds-harness-remote/blob/6a9113255d46c6bcd751909b646cc67ea1909c01/packages/plugin/src/config.ts)。
- [dsh-zen-remote 中文说明](https://github.com/KyoMio/dsh-zen-remote/blob/536068d2e6ff961d45d42bc6c9459d24d94cb578/README.zh-CN.md)、[默认回环配置](https://github.com/KyoMio/dsh-zen-remote/blob/536068d2e6ff961d45d42bc6c9459d24d94cb578/src/config.ts)。
- [full-remote 兼容说明](https://github.com/JUANWANG-BUAA/dsh-full-remote/blob/deba626e7922726c2f0a283fa784d5bda53471f1/docs/compatibility.md)、[mobile-plugin 验收基线](https://github.com/april-jk/dsh-mobile-plugin/blob/489a1a2c0f12246e478a93eb25683f87c1f33832/README.md)。
- [Devin 搜索 README](https://github.com/mimimaster/dsh-devin-search/blob/7dfdf835c2601ad3a1ac29ede1520e3e66a4d036/README.md)、[精确 peer 范围](https://github.com/mimimaster/dsh-devin-search/blob/7dfdf835c2601ad3a1ac29ede1520e3e66a4d036/package.json)、[原生搜索切换 patch](https://github.com/mimimaster/dsh-devin-search/blob/7dfdf835c2601ad3a1ac29ede1520e3e66a4d036/cordis.patch.yml)。
- [Firecrawl 官方 MCP 文档](https://docs.firecrawl.dev/mcp-server)。
- [AnySearch README](https://github.com/anysearch-team/anysearch-dsh/blob/788804a7c1fff2860e6c29451e48076ad5c4619a/README.md)、[当前 peer 范围](https://github.com/anysearch-team/anysearch-dsh/blob/788804a7c1fff2860e6c29451e48076ad5c4619a/package.json)。

需要后续实际验证：rc.2 插件安装门禁、ARM64 启动、远程设置／目录／会话／审批、SSE/WebSocket 重连、搜索登录与真实工具返回、资源和温升。插件声明兼容不是本机实测通过，未验证远程服务与收费额度。
