# 酒馆迁移到 OnePlus Debian（2026-10-05）

酒馆与持久数据已实际部署到手机；模型反代、账号池和嵌入网关继续留在 Azure。Azure 原酒馆保留并运行，本次没有下线授权。这里不含实际地址、凭据或私人剧情；当前地址见私有 `local/sillytavern-migration-20261005/ACCESS.md`。

## 固定版本与目录

- SillyTavern 1.18.0，提交 `51ad27fb86d39a3daca3adaa970375c9670c12df`，保留线上前端、聊天保护、向量及其它源码补丁。
- Azure 与手机均使用 Node.js 20.20.2 / npm 10.8.2。依据该提交的 [package.json](https://github.com/SillyTavern/SillyTavern/blob/51ad27fb86d39a3daca3adaa970375c9670c12df/package.json)，Node 要求 >=20。手机使用官方 ARM64 发行包，校验官方 SHA-256；按来源 lockfile 执行 `npm ci --omit=dev`，未复制 node_modules。
- 应用 `/srv/sillytavern/app`，实际数据 `/srv/sillytavern/app/data`，保护脚本与历史 `/srv/sillytavern/ops`，Node `/srv/sillytavern/node`，私有日常备份 `/srv/sillytavern/backups`。
- `/srv` 独立 ext4 已实读验证；部署前剩约219GiB。应用以 `sillytavern` 普通用户运行，systemd 检查挂载、自动重启并限制日志速率。没有改分区、内核、充电、温控、电源键、USB SSH或既有基础监控。
- root 控制器与其私有凭据独立放 `/srv/kebab-strelay/private`，父目录由root控制，普通应用用户不能替换。转发代码独立于MC原脚本。

## 来源备份

经用户同意，仅暂停 Azure 酒馆与会写其数据的守护任务约112秒；网关保持运行。冻结快照取于2026-10-05 19:05北京时间，归档与逐文件哈希核验后立即恢复Azure。

本机私有 `local/sillytavern-migration-20261005/source.tar.gz` 为868116130字节，SHA-256 `11bb915713edf1e4197347a633aa00f65b5f08d1a3c10e686fb0cf0e41f38221`。3,946个常规文件的大小和SHA-256逐项核验、gzip/tar可读；Windows再次核验后才经USB SSH传输。文件清单在同目录 `manifest.json`。完整来源包也包含发现阶段备份的网关恢复资料；**手机恢复包排除了Azure反代目录，反代未部署到手机**。所有私有资料在Git忽略目录，仅当前Windows用户和SYSTEM可访问。 完成本地归档再核验后，已删除本次新建的Azure临时暂存副本；未删除原部署、原数据或既有备份。新增手机专用Azure密钥也已保存本机私有回退副本。

恢复前手机逐成员核验酒馆及ops文件。正常运行后有一个角色卡的当前聊天指针，以及一个聊天的生成/Qvink元数据变化；图像像素、消息条数、消息正文逐哈希一致，未把运行后的元数据强行回灌。访问日志和Webpack缓存重建；ST自身保留策略清理了4个旧自动备份副本，它们仍在不可变来源归档。不能将运行后所有文件描述为永久字节不变，也不能把日常运行中tar备份说成一致性快照。

## 访问与认证

在项目目录运行：

```powershell
.\scripts\Get-SillyTavernAccess.ps1
```

脚本从本机受限私有文件读取已配置的CNB令牌，通过官方API找到专用环境，再严格核验CNB SSH主机密钥读取当前HTTPS地址；不需要USB，也不输出令牌。用正常Chrome/Edge等浏览器打开该地址，输入**原Azure酒馆的登录账号和密码**。登录后刷新一次加载新连接配置。两份酒馆此后独立保存，后续聊天应使用手机入口。

手机主动WSS/443出站；专用CNB仓库和1核环境只转发字节，不保存聊天、角色、API账号或嵌入数据。9001为独立SSH隧道，9002为独立HTTP隧道，9003为浏览器HTTPS转发入口；与MC是不同环境、进程和随机令牌。

酒馆仍仅绑定回环。该版本只有 `listen=true` 才注册Basic Auth，因此设置 `listen=true` 同时明确 `listenAddress=127.0.0.1/::1`，保持原Basic登录凭据、回环白名单和CSRF。参见 [官方远程连接保护](https://docs.sillytavern.app/usage/remoteconnections/)。未认证的页面、脚本和扩展API均返回401，无CSRF的POST返回403。

Azure模型9878、摘要55994、嵌入8099经受限SSH加密转发到手机回环18078、15994、18099，设置和连接配置已改为这些**实际通向Azure**的地址。专用Azure公钥获用户另行授权，禁止shell/PTY/agent/X11，PermitOpen仅三个网关目标；未开启新公网监听。实测shell及网关范围外SSH22目标都被拒绝。原私钥没有复制到手机。不要把这些回环地址理解为网关已经迁移。

## 启停与维护

先连接原有管理通道：

```powershell
ssh -F local/ssh/kebab-server.conf kebab
```

手机上：

```bash
sudo systemctl status kebab-sillytavern kebab-st-azure-provider kebab-strelay-controller
sudo systemctl restart kebab-sillytavern
sudo systemctl stop kebab-sillytavern
sudo systemctl start kebab-sillytavern
sudo cat /srv/kebab-strelay/private/status.json
sudo journalctl -u kebab-strelay-controller -n 30
```

保护与备份timer：`kebab-st-chat-guard.timer`、`kebab-st-memory-config-guard.timer`、`kebab-st-memory-source-audit.timer`、`kebab-st-backup.timer`。前三项保留来源行为并适配普通用户及新嵌入端口；来源审计既有待复核事项没有被重置或自动确认。日常备份是运行中归档；新的严格一致性快照仍需短暂停止写入。当前本机来源备份与手机日常备份不是离机多介质备份。

需要经CNB远程SSH维护时，在项目目录第一个终端运行下列转发器，保持该终端开启：

```powershell
.\scripts\Start-SillyTavernSshForward.ps1
```

另一个终端运行（沿用原手机密钥和严格主机校验）：

```powershell
ssh -F local/ssh/kebab-server.conf -o HostName=127.0.0.1 -o Port=2222 -o BindAddress=127.0.0.1 -o HostKeyAlias=192.168.8.1 kebab
```

HostKeyAlias对应现有USB SSH配置的手机身份；不会修改配置文件。转发器只监听电脑回环2222，断线后重新发现CNB地址；Ctrl+C结束。交付的地址查询和SSH转发代码已实际完成公网登录复验。

## 实际验收

| 项目 | 结果 |
|---|---|
| 应用版本、ARM64依赖、普通用户、挂载依赖 | 通过；未升级酒馆 |
| 公网CNB认证、CSRF、刷新读取 | 通过401/200/403及重新读取 |
| 角色/聊天/世界书/预设 | 36张卡、36个聊天文件、23本世界书、43个传统预设；全部文件恢复前核验 |
| 旧聊天实际读取 | 公网认证API读到含67条记录的旧聊天；未保存测试聊天 |
| 扩展与静态资源 | 22项发现记录；4个第三方扩展、4个用户扩展入口与来源哈希一致；实际页面CSS/JS均可获取 |
| 手机生成 → Azure原反代 | 两次限定短提示词请求返回200和非空文本；未改历史聊天。请求max_tokens=16，但上游返回超过该上限且completion_tokens=0，计量不可靠，未继续试压 |
| Azure嵌入网关 | 200，1024维、有限非零值；没有写旧向量索引 |
| CNB SSH | 公网WSS真实SSH登录、手机host key、aarch64和PTY通过；新增隧道重启后重新登录通过 |
| 专用CNB实际重建 | 经单独确认停止一次专用环境；手机自动拉起新环境、地址发生变化，约93秒恢复401认证入口；原MC环境未停止 |
| 手机服务重启后数据 | 酒馆进程重启后复验；没有重启整台手机 |

**后续Chrome人工验收：**用户实际发现会话卡死，手机已部署预热取消/作用域及批量计数补丁，随后用户确认可进入并正常切换会话；详见[性能修复记录](sillytavern-performance-20261005.md)。代理浏览器控制连接没有接通，不能声称自动化GUI通过。清理临时诊断后的最终刷新、输入及常用扩展按钮仍待确认。**未测试：**另一台校外/移动网络设备的独立访问；长期SSE、性能、温升、真实手机重启；记忆候选→最终prompt→回答的质量。

CNB有回收和额度边界，保活与恢复不能保证永不断线；现已实际验证一次自动恢复，不是永久在线证明。正常状态下控制器每分钟检查，没运行环境才请求拉起，并对start动作设置至少10分钟间隔；令牌失效或SSH主机密钥变化时停止自动信任，需要维护。

## 回退

只暂停本次新增服务和timer，不碰MC或基础服务：

```bash
sudo systemctl disable --now kebab-strelay-controller kebab-strelay-ssh kebab-strelay-http
sudo systemctl disable --now kebab-st-azure-provider kebab-sillytavern
sudo systemctl disable --now kebab-st-chat-guard.timer kebab-st-memory-config-guard.timer kebab-st-memory-source-audit.timer kebab-st-backup.timer
```

保留手机目录以免丢失迁移后的新聊天。Azure原酒馆和原数据可继续访问；如要撤销新增网关授权，只删除authorized_keys里本次 `kebab-st-providers-20261005` 标记的公钥行，**不**整文件覆盖原授权。停止或删除专用CNB环境需用户确认；不要停错原MC环境。没有改DNS、卸载Azure部署、关闭VM、取消资源或推送GitHub。
