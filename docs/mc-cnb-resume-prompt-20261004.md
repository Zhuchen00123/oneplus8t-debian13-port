# 新会话提示词：MC Java 与 CNB 443 隧道

复制下面内容到新会话：

```text
请继续 OnePlus 8T 的 MC Java 服务端与 CNB 穿透工作。默认使用简体中文，先读取项目 AGENTS.md、README.md、docs/r2-integrated-20261004.md、docs/gdut-wifi-20261004.md 和本提示词，核对实际设备状态后执行。

主项目：F:\codexprojects\oneplus8t
参考方案：F:\codexprojects\mcfuwuqi\cnb-tunnel

目标与选择：
- MC Java 版，电脑 Java 客户端；尚未确定游戏版本、原版/插件/模组和玩家规模，请先核对我的既有 MC 项目，再澄清必要选择。
- 手机直接连接 gdut 校园 Wi-Fi，进出流量使用校园网，不依赖电脑共享或蜂窝网络。
- 暂时没有公网 VPS/设备，优先省钱。明确不试 playit 等免费中继平台，参考我自己的 cnb-tunnel 方案，经 WSS/443 主动出站。
- 另一个会话正在处理电池、限充、温度及服务器基础配置。你只负责 MC 和隧道，不要改充电/电源/温度策略、基础监控、journald、SSH 密钥或 USB 管理配置，也不要在基础配置会话未确认前进行长时间游戏负载测试。
- 已安装基础配置：kebab-health.timer每分钟只读采样，配置/etc/kebab/server-health.json、程序/usr/local/sbin/kebab-health、状态/run/kebab-health/status.json；目标80%但charge_limit_enforced=false，当前内核MP2762A只提供OTG而非充电管理，不能称已限充。四类软件休眠已masked，时区Asia/Shanghai。详情docs/server-base-20261004.md，这些文件属于基础配置会话，不要覆盖。旧r2封存镜像未重打包。

实际设备状态：
- KB2000 / SM8250，持久 Debian 13 arm64，运行 7.2.0-kebab-debian13-r2+，约 11 GiB 内存。
- 显示和触摸修复已内建；旧 kebab-touch-fix.service 已禁用，绑定 synaptics-tcm。存在过间歇指针漂移，短测未再复现，不可称长期稳定。
- 根分区是本机 /dev/sda15，ext4 当前约 3.9 GiB、剩余约 1.7 GiB；尚未扩容。不要照抄其他机型的分区编号，不改分区布局或 userdata。
- 固定 USB 管理链路和严格保存 SSH 主机密钥正常。维护命令使用原项目私有配置：ssh -F F:\codexprojects\oneplus8t\local\ssh\kebab-server.conf kebab。不要复制、展示或上传密钥。
- gdut 已通过用户手动校园门户认证，实际 Wi-Fi DHCP/网关/DNS、外部 HTTP/HTTPS和 NTP通过；RTC仍是1970。不得把当前手机当 Android。校园认证凭据不在聊天或项目文件中；重启后认证是否保留未验收，不要无故断开会话。
- IPv4为校园内网地址；手机有全球IPv6，网关和公网IPv6的ICMP出站通过，但移动网络入站 TCP25565测试失败。测试手机截图确认它有公网IPv6，失败具体位置未确定，不能直接断言校园网全面封禁。
- 443实测：socket显式绑定wlan0，IPv4直连Debian官网、kernel.org、清华官网的TCP443、TLS1.3标准证书验证、HTTP200均通过；IPv6域名没有取得地址，指定一个公网IPv6的TCP443超时，不代表所有IPv6目标都不可用。
- 临时IPv6探针已停止，25565无测试监听，运行时限导致的failed状态已清理。目前未安装MC、Tailscale、ZeroTier、playit或frp，未创建新的CNB环境、隧道或公网游戏入口。

CNB参考方案的已读信息：
- README描述自写Rust mcbridge：CNB上 serve :9000，由CNB公网代理提供 wss://<环境ID>-9000.cnb.run；服务端侧 client 主动通过WSS连接CNB，把多路复用流映射到本地 127.0.0.1:25565；玩家Windows端 forward监听127.0.0.1:25565后经WSS连CNB。
- 旧MC本体跑在魔搭DSW（README提到NeoForge），这次应把服务端/client落点改为手机ARM64。不要误把旧DSW当作这次部署目标。
- 需要继续检查 src/main.rs、dialer.rs、ws.rs、tunnel.rs、http.rs、Cargo.toml、cnb-side/start-bridge.sh、dsw-side/start-client.sh 和 .cnb.yml。此前只读过README、Cargo.toml和build-client.sh，尚未审核协议或移植源码。
- TLS feature、ARM64编译、证书校验、认证、断线重连、会话清理、背压与资源上限都需实际核验；不要只看“443可以访问网页”就称WSS隧道或MC已通过。
- README说明仓库默认WST_SECRET/FWD_TOKEN是公开值，不应当成私有凭据；新部署应使用自己的私有配置，不能把令牌、cookie或私钥提交Git或粘贴聊天。
- README记载CNB开发环境有回收/最长运行时限，环境重建后公网地址会变，玩家端endpoint.txt、手机client和CNB端必须一致。须重新查官方当前规则和实际环境，不把旧额度/保活结论当成现行保证，不承诺7×24常驻。
- 参考目录是detached HEAD，README.md已有用户改动，禁止覆盖、重置或擅自推送这个参考仓库。不要直接运行旧脚本：它们可能推送仓库、重建环境或SSH修改旧DSW。

协作与实施要求：
1. 当前基础配置会话仍在使用主项目工作树。请建立或复用独立工作树/checkout进行MC文件开发，不切换主项目当前工作树分支，也不要覆盖另一个会话的修改。
2. 你负责独立MC/隧道脚本、服务文件和 docs/mc-* 记录；公共README/AGENTS/设备状态配置由主会话整合，不并行改这些文件。私有证据可用原项目 local/mc-cnb-20261004/，必须忽略。
3. 先完成参考代码/现有来源审查和WSS接入可行性，再做ARM64客户端与本地回环测试，然后接CNB真实链路，最后用MC真实状态握手/玩家连接验收。按当前游戏版本选择Java，不盲用最新版。
4. 安装/构建和手机部署分开。保留可回退备份，systemd资源边界和优雅停服/世界备份要明确，避免把世界填满仅1.7GiB空闲的根文件系统。MC协议、模组兼容性和实际延迟须验证，不能拿固定HTTP探针代替游戏验收。
5. 手机现有已授权维护可以继续；不得擅自切槽、写NV/vbmeta/recovery/userdata、刷分区或改硬件充电策略。需要学校或CNB网页登录时由我在官方页面操作，不要求我发送token或密码。
6. 管理仓库是私有历史，不直接推送。已有公开脱敏仓库仅允许构建脚本/通用配置/补丁/思路，禁止上传可用镜像、固件、游戏世界、原始日志、身份与凭据；如需发布，独立白名单审核，不复制原历史。

先给出你从本地参考代码确认的链路、需要迁移到手机的最小部分、现有CNB环境是否可复用，以及尚缺的必要信息；不要重复让我购买VPS或尝试我已拒绝的免费中继平台。
```
