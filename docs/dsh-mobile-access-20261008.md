# DSH 手机浏览器入口准备（2026-10-08）

**10/10 更新：**用户随后明确要求全天常驻、局域网及公网访问，已完成部署，原自动审批阻塞已解除。社区bridge及9011/9013服务与原共享CNB共用一个实例；用户确认gdut手机能登录并聊天。当前状态、真实WS/模型验收和回退见[常驻与手机访问](dsh-always-on-access-20261009.md)。下文是10/08准备阶段，不再表示等待授权或公网服务不存在。

用户要求另一台手机直接通过浏览器访问服务器。已安装回环桥接并通过本地认证验证；**独立公网转发服务尚未创建或启用**。自动审批拒绝了启用 root 常驻公网转发的命令，要求用户明确批准持久公网访问 DSH 模型设置、工作区和工具执行的具体范围；已提交确认请求。等待明确答复期间不启动公网入口。

## 已完成的本地准备

- 固定官方 `@wenbin_wb/dsh-bridge@2.12.2`，registry SHA-512 与下载包一致；加入最小补丁形成 `2.12.2-kebab.1`，通过官方 DSH plugin 命令装入 web profile。
- [补丁](../patches/dsh/0001-bridge-loopback-auth.patch)：仅 `127.0.0.1:3082` 监听；初始化即从私有配置载入强制认证，缺失时不启动桥接；关闭回环免密和回环管理员 token 签发；登录 cookie 为 HttpOnly / Secure / SameSite=Strict。
- 预设密码登录、全范围门禁及独立管理密码，配置与凭据均 0600；无 Cloudflare 或其它新增免费中继。密码仅在 `/srv/dsh/private/mobile-access.json`，没有输出。
- 原 DSH 3080 仍仅回环；模型默认保持 `cline-pass/deepseek-v4.1-flash`。仅重启 DSH，原酒馆、MC、Azure 转发、内核、电源和网络配置不变。
- 实际本地验证：登录成功、经桥接 HTML 200／38,697 字节、settings/describe 成功；匿名入口/API 401，loopback-token 403。Secure cookie 的本地协议检查由测试程序显式传回 cookie，不宣称 HTTP 浏览器已登录；生产入口计划使用 HTTPS。
- `/srv/dsh/ops` 已改为 root:dsh 0750，防止应用账号替换通过 sudo 调用的管理脚本。

上游 SHA-256 `406e73cfa3890d773c9bc34d9d90e553ae0fe24968d8ee7f3d3ea621e7c9ec21`；受控 tarball SHA-256 `ac805a2647d33baffe92e47538c882e493721ba93478e3f7e32b970fbbd2884b`。包、元数据、准备命令和私有备份留在被忽略的 `local/dsh-deployment-20261008/`。官方许可和作者保留，不直接更新覆盖受控补丁。

## 待批准的部署范围

1. 在现有专用 CNB 环境启动 DSH 自己的进程和凭据命名空间：9011 为 WSS，9013 为 HTTPS 浏览器入口。不修改原仓库、启动脚本、9001／9002／9003 或其进程，不新建第三方账号或仓库。
2. 手机 `/srv/kebab-dshrelay` 保存 root 管理的控制器和独立隧道凭据；启用 `kebab-dshrelay-controller` 与 `kebab-dshrelay-client` 两个 root 常驻服务。其职责仅为受限配置、CNB 进程准备和字节转发，DSH 的工具仍由普通用户 dsh 执行。
3. 独立 controller 只读取原 CNB 地址发现结果，沿用既有环境恢复；新环境就绪后重建自己这组进程、刷新 DSH 外部 URL。它不停止或启动原环境；地址变更恢复尚未实测。
4. 公网请求经 CNB TLS → 独立 WSS → 回环密码网关 → DSH。本机模型/账号 key 不发送给 CNB 控制代码；CNB 终止 HTTPS 和转发字节，不是端到端加密至 DSH 的架构。

代码和单元在 [独立 relay](../scripts/dsh/relay/controller.py)，已静态语法检查并上传暂存，尚未部署运行。密码持有者将能访问 DSH 配置、工作区和工具，这是自动审批要求明确确认的权限范围。

批准后必须继续真实公网验证：匿名拒绝、密码登录、设置／模型／目录、实际对话、SSE/WebSocket、桥接与独立隧道重启、原进程未变化。另一台手机的实际网络／触控访问和长稳需分别记录，不把电脑协议测试当作手机人工验收。

## 备份与回退

当前准备配置已归档并完整读回，Windows 副本在私有目录；SHA-256：`3fcaaab8576d66541bf01f16b99062df1423d9ce272406ae22a7f50b6e2d0e3b`。这是运行中配置归档，不是会话一致性或整盘快照，包含访问凭据，不能上传。

安装前的全局 patch 已保存在 `/srv/dsh/backups/before-public-cordis.patch.yml-*`。撤回桥接应先保存后来新增的配置，再通过 DSH plugin 管理器移除该 bundle、恢复相应 patch、只重启 DSH；不可覆盖之后的会话或渠道凭据。公网单元当前不存在，不执行它们的启停作为既成事实。
