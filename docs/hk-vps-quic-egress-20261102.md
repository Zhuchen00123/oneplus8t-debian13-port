# 手机服务器承载 QUIC 出口并接管 DSH 远程入口（2026-11-02）

## 已完成

用户要求：把 hysteria2 部署到**连在 E8820S 路由器上的 OnePlus 8T 服务器**（USB 供网），保持"走有线"，全天常驻，并让**电脑的 DSH 通过这条通道穿透**、优先使用 dsh-bridge 插件。

现已完成三件事：

1. 手机侧常驻 hysteria2 客户端，连接香港 VPS（`2602:f506:81::6440:1333:24443`），提供 SOCKS5 与 TCP 转发。
2. 路由器为 USB 口补齐 IPv6（ULA）+ NAT66 + 源地址默认路由，使手机经 USB 走 IPv6 出网；手机侧另补回程路由，让局域网设备能访问手机上的转发端口。
3. 插件自建隧道改为经手机转发接入 VPS，并新增 `https://fuckdsh.lugang.xyz`（Let's Encrypt，IPv6 直连）作为唯一公网入口；明文 `:3000` 已关闭。

## 链路

```text
手机/浏览器 ──HTTPS(TLS1.3)──► 香港 VPS nginx:443
                                  │ 127.0.0.1:3000（隧道服务，仅回环）
                                  ▼
                            dsh-bridge 隧道服务 ──QUIC(hysteria2)──► 手机 usb0
                                                                        │ USB
                                                                        ▼
                                                                   E8820S ──► 电脑插件 ──► DSH:19388
```

备用入口 `https://dsh.lugang.xyz`（Cloudflare 隧道）保留，供纯 IPv4 网络使用。

## 手机侧改动

| 项 | 内容 |
| --- | --- |
| 程序 | `/srv/apps/hysteria/hysteria`（官方 linux-arm64，22,151,330 B） |
| 配置 | `/srv/apps/hysteria/client.yaml`（600，debian:debian） |
| 服务 | `/etc/systemd/system/hysteria-client.service`，已 enable，Restart=always |
| 监听 | SOCKS5 `192.168.8.1:12080`；TCP 转发 `192.168.8.1:13001 → 127.0.0.1:3000` |
| 出口 | 经 `fd00:8::2` → `fd00:8::1`（路由器）→ WAN |

systemd-networkd 新增两个 drop-in：

- `60-router-ipv6.conf`：usb0 静态 `fd00:8::2/64`，默认路由 via `fd00:8::1` metric 50。
- `61-lan-return.conf`：`192.168.1.195/32 via 192.168.8.2 dev usb0`，修复电脑访问 `192.168.8.1` 时回包走 Wi-Fi 导致握手失败的问题。

原 Wi-Fi IPv6 默认路由（metric 1024）保留为回退；`wlan-gdut` 仍为 `IPv6AcceptRA=no`，不参与出口选择。

## 路由器侧改动

| 项 | 内容 | 备份 |
| --- | --- | --- |
| usb0 IPv6 | `network.kebab_usb.ip6addr='fd00:8::1/64'` | `/root/kebab-v6-20261102-142725/` |
| NAT66 | `firewall.kebab6nat`：family ipv6、src wan、src_ip `fd00:8::/64`、MASQUERADE | 同上 |
| 默认路由 | `default from fd00:8::/64 dev pppoe-wan metric 600` | 同上 |
| 兜底守护 | `/etc/init.d/kebab-v6-guard` + `/usr/sbin/kebab-v6-guard`（`ip monitor` 监听 usb0，缺地址/路由即补） | 新增文件 |
| 事件钩子 | `/etc/hotplug.d/net/90-kebab-v6`、`/etc/hotplug.d/iface/91-kebab-v6-addr` | 新增文件 |
| Clash 规则 | `DOMAIN,fuckdsh.lugang.xyz,DIRECT`、`DOMAIN,dsh.lugang.xyz,DIRECT`（profile 首部） | `party-profile.yaml.bak-20261102-164503`、`-165539` |

关键点：校园下发的默认路由是 `from 240c:cd22:...` 的**源地址限定**路由，ULA 源地址匹配不到，必须单独加一条；不加即表现为路由器对 `fd00:8::2` 回 ICMPv6 "No route"。

## 香港 VPS 侧改动

| 项 | 内容 |
| --- | --- |
| DNS | `fuckdsh.lugang.xyz` AAAA → `2602:f506:81::6440:1333`，**仅 DNS（灰云）**，TTL 120 |
| 证书 | Let's Encrypt，`/etc/letsencrypt/live/fuckdsh.lugang.xyz/`，有效期 2027-01-31，`certbot.timer` 自动续期 |
| 443 站点 | `/etc/nginx/sites-enabled/fuckdsh-tls`：SNI 分流 → `127.0.0.1:3000`，含 `proxy_set_header Origin "http://127.0.0.1:19388"` |
| 80 站点 | `/etc/nginx/sites-enabled/fuckdsh-acme`：仅 `/.well-known/acme-challenge/`，供续期 |
| 隧道服务 | `/opt/dsh-tunnel/server.mjs` 监听地址由 `0.0.0.0` 改为 `127.0.0.1`（备份 `server.mjs.bak-*`） |
| IPv6 转发 | `dsh-tunnel-v6.service`（socat）已 disable + stop |

## 插件侧

`C:\Users\<用户>\.dsh\dsh-bridge\config.json` 的 `customTunnel.serverUrl` 改为 `ws://192.168.8.1:13001/connect`，`autoStart` 保持 true。备份 `config.json.bak4-*`。

隧道服务在 VPS 上收到的客户端全部来自 `127.0.0.1`（hysteria 回环转发），`/healthz` 显示 `clients:1`。

## 实测

### 手机 → 香港 VPS（QUIC）

| 场景 | 结果 |
| --- | --- |
| USB 有线，下载 50 MB | **9.3 MB/s**（5.37 s） |
| USB 有线，上传 20 MB | **5.3 MB/s**（3.78 s） |
| 同上，hysteria 进程 CPU | 16.4%（空闲 0 tick / 10 s） |
| Wi-Fi 路径，下载 | 1.5–1.8 MB/s |
| Wi-Fi 路径，同路径 TCP 直连 | 4.7 MB/s |

结论：在这个校园链路上，QUIC 经 Wi-Fi 反而慢于 TCP；换到 USB 有线后 QUIC 才发挥出 9 MB/s。**"走有线"是性能前提，不是可选项。**

### 电脑 → 手机 → VPS

| 场景 | 结果 |
| --- | --- |
| 电脑经手机 SOCKS 下载 50 MB | 8.35 / 8.23 MB/s |
| 电脑经手机 SOCKS 访问外网 | HTTP 204 / 200，约 1.0 s |

### DSH 入口

| 入口 | 页面 | 285 KB 资源 | 说明 |
| --- | --- | --- | --- |
| `https://fuckdsh.lugang.xyz`（IPv6 直连） | **0.34–0.47 s** | 0.78–0.81 s | 主入口 |
| 同入口，经路由器 Clash 代理时 | 0.87–1.37 s | ~1.0 s | 已加 DIRECT 规则修复 |
| 明文 `:3000`（已关闭） | 0.42 s | 0.69 s | 无 TLS/无 Origin 覆写，WS 不可用 |
| `https://dsh.lugang.xyz`（Cloudflare） | 0.74–3.3 s | ~0.3 MB/s | 仅 IPv4 兜底 |

### HTTPS 代价

| 项 | 数据 |
| --- | --- |
| HTTP → HTTPS（手机直连） | 0.24 s → 0.34 s，**+0.10 s** |
| TLS 握手 | `time_appconnect` 0.169 s（约 1 RTT） |
| 证书校验 | `ssl_verify_result=0` |

### 隧道吞吐上限

插件隧道单流边际吞吐约 0.72 MB/s、固定开销约 0.30 s/请求（285 KB 资源 0.69 s）。同一链路 SOCKS 可达 8.3 MB/s，说明瓶颈在插件 WS 协议逐块往返，**不在手机、不在 QUIC、不在 CPU**（VPS 6%、手机 16%、电脑空闲；6 路并发互不拖慢）。

## 排障记录

1. **`Illegal instruction`（路由器）**：官方 mipsle 版本是硬浮点，E8820S 无 FPU；仅自编译 softfloat 可运行，但峰值只有 0.8 MB/s，已放弃在路由器上跑 hysteria。
2. **手机访问 `192.168.8.1` 超时**：手机回程路由走 Wi-Fi，源地址变成 `192.168.1.122`，TCP 握手失败；补 `61-lan-return.conf` 后恢复。
3. **路由器 ICMPv6 "No route"**：校园默认路由带源地址限定；补 `from fd00:8::/64` 默认路由后恢复。
4. **裸 hotplug 对链路抖动不触发**：实测 `/tmp/kebab-v6.log` 无事件；改用 `ip monitor` 守护后，人为 down/up 8 秒可自动补回地址。
5. **页面"正在重新连接"**：DSH 的 `/api/remote.mux` 是 WebSocket 且校验 `Origin`，经域名访问时被 403 拒绝；nginx 覆写 `Origin` 为 `http://127.0.0.1:19388` 后恢复 101。
6. **DSH 流量绕道 AWS 新加坡**：路由器 Clash 的 fake-IP 模式下，原 `IP-CIDR6,...,no-resolve` 规则对域名流量无效，流量落到 MATCH 走了代理节点；改为 `DOMAIN,fuckdsh.lugang.xyz,DIRECT` 后出口恢复为校园 IPv6。

## 边界与未验证

- **插件重连上限**：`RECONNECT_DELAY=5000`、`MAX_RECONNECT_ATTEMPTS=5`，断开超过约 25 秒插件会进入 error，需手动点一次"开启"。该行为写死在插件源码中，本轮未修改。
- **物理拔插**：16:38 实测一次真实拔插，netifd 完整 ifup，路由器 IPv6 自动恢复、hysteria 未重启、插件自动重连、公网页面 200，无需人工干预。**仅一次采样。**
- **链路抖动**：人为 down/up 8 秒时 netifd 只恢复 IPv4，IPv6 由 `kebab-v6-guard` 补回（日志 `addr-restored`）。
- **长期稳定性**：24 小时常驻、电量/温度长期趋势、证书首次自动续期均未观察。
- **备用入口**：`dsh.lugang.xyz` 仍依赖电脑上的 cloudflared 连接器与 Cloudflare 边缘，速度受其限制。
- **校园网路径**：服务器手机的 `wlan-gdut` 未认证，无法用它验证"教学楼直连 VPS"的真实体验；该结论来自用户日常手机实测（可打开）。
- **旧 443 默认站点**：VPS 上仍保留自签证书 + basic auth + 反代 `127.0.0.1:31810` 的旧 SSH 链路站点，未清理。

## 凭据

本文件不记录任何密码、令牌或私钥。相关凭据保存在：

- 手机：`/srv/apps/hysteria/client.yaml`（600）
- 路由器：Clash/Mihomo 密钥在 `/etc/nikki/run/config.yaml`
- VPS：`/etc/nginx/dsh/`、`/etc/letsencrypt/`

## 备份与回退

### 手机

```bash
sudo systemctl disable --now hysteria-client
sudo rm -f /etc/systemd/system/hysteria-client.service
sudo rm -rf /srv/apps/hysteria
sudo rm -f /etc/systemd/network/10-kebab-usb.network.d/60-router-ipv6.conf \
           /etc/systemd/network/10-kebab-usb.network.d/61-lan-return.conf
sudo systemctl daemon-reload && sudo networkctl reload
sudo networkctl reconfigure usb0
```

### 路由器

```bash
/etc/init.d/kebab-v6-guard disable
rm -f /etc/init.d/kebab-v6-guard /usr/sbin/kebab-v6-guard
rm -f /etc/hotplug.d/net/90-kebab-v6 /etc/hotplug.d/iface/91-kebab-v6-addr
uci delete firewall.kebab6nat; uci delete dhcp.kebab6
uci delete network.kebab_usb.ip6addr
uci commit; /etc/init.d/firewall reload; /etc/init.d/network reload
# Clash 规则：恢复 party-profile.yaml.bak-20261102-164503 后重启 nikki
```

### VPS

```bash
rm -f /etc/nginx/sites-enabled/fuckdsh-tls /etc/nginx/sites-enabled/fuckdsh-acme
nginx -t && systemctl reload nginx
# 恢复隧道服务监听：cp /opt/dsh-tunnel/server.mjs.bak-<时间戳> /opt/dsh-tunnel/server.mjs
# systemctl restart dsh-tunnel
```

DNS 记录与证书如需撤销：删除 Cloudflare 的 AAAA 记录，`certbot delete --cert-name fuckdsh.lugang.xyz`。

## 结论

- 手机（8 核 A77 + AES）足以常驻 hysteria2：空闲 0% CPU、53 MB 内存、温度 36–40 °C。
- 有线 QUIC 出口 9.3 MB/s 下载 / 5.3 MB/s 上传，明显优于路由器（0.8 MB/s）与 Wi-Fi 路径（1.5 MB/s）。
- DSH 主入口页面 0.34–0.47 s，HTTPS 只多 0.1 s；大文件吞吐受插件协议限制（0.72 MB/s），需要时走手机 SOCKS（8.3 MB/s）。

## 校园 ↔ InstaBox 经香港 VPS 中转（2026-11-03 新增）

### 背景

实测 InstaBox 云端 VM（AWS 俄勒冈）与校园网**双向不可达**：校园不接受入站，VM 的 AWS 安全组也不放行入站。两边唯一共同可达的点是香港 VPS，因此用 VPS 做中转。

### 拓扑

```text
InstaBox VM ──SSH:22022(IPv6)──► 香港 VPS [::]:22022
                                     │ socat → 127.0.0.1:22022
                                     │ sshd 远程转发（反向隧道，由手机发起）
                                     ▼
                                  手机 kebab-debian13:22
                                     │ usb0 / 路由
                                     ▼
                          192.168.1.0/24（电脑 DSH、路由器）
```

关键点：**校园侧主动出站建隧道**，所以不需要校区开放任何入站端口。

### 组成

| 位置 | 组件 | 说明 |
| --- | --- | --- |
| 手机 | `/etc/systemd/system/vps-relay-tunnel.service` | `ssh -N -R 127.0.0.1:22022:127.0.0.1:22 relay@2602:f506:81::6440:1333`，Restart=always，已 enable |
| 手机 | `/root/.ssh/vps_relay` | 专用密钥，只用于建这条隧道 |
| VPS | 用户 `relay` + `/var/lib/relay/.ssh/authorized_keys` | 限制 `restrict,port-forwarding,permitlisten="127.0.0.1:22022"` |
| VPS | `/etc/systemd/system/relay-22022.service` | `socat TCP6-LISTEN:22022,fork,reuseaddr,ipv6only=1 TCP4:127.0.0.1:22022`，已 enable |
| 手机 | `~debian/.ssh/authorized_keys` | 追加 `instabox-relay` 公钥 |
| 本地 | `F:\codexprojects\instabox\.local-secrets\campus_relay` | 对应私钥 |

注意 `ipv6only=1`：不加会和 sshd 已占用的 `127.0.0.1:22022` 冲突（双栈绑定覆盖 IPv4）。

### 从 InstaBox 使用

```bash
# 直接登录校园手机
ssh -p 22022 -i campus_relay debian@2602:f506:81::6440:1333

# 端口转发到电脑 DSH
ssh -L 19388:192.168.1.195:19388 -p 22022 -i campus_relay -N -f debian@2602:f506:81::6440:1333
curl http://127.0.0.1:19388/

# 端口转发到路由器
ssh -L 8080:192.168.1.1:80 -p 22022 -i campus_relay -N -f debian@2602:f506:81::6440:1333
```

### 实测（2026-11-03）

| 测试 | 结果 |
| --- | --- |
| InstaBox → 中转 → 手机 SSH | `HOST=kebab-debian13`，成功 |
| 手机 → 电脑 DSH:19388 | HTTP 401（需认证），7 ms（局域网内） |
| InstaBox → 转发 → 电脑 DSH | HTTP 401，0.76 s |
| InstaBox → 转发 → 路由器:80 | **HTTP 200，0.73 s** |

单次请求约 0.73–0.76 s，主要成本是 161 ms 的跨境 RTT 加上隧道与 SSH 握手。

### 安全与回退

- 手机 sshd：`passwordauthentication no`、`permitrootlogin no`，仅密钥。
- 对外只开 22022，落到手机 sshd；VPS 上的 `relay` 用户被限制只能监听 `127.0.0.1:22022`。
- 回退：`systemctl disable --now vps-relay-tunnel`（手机）与 `systemctl disable --now relay-22022`（VPS），再删 `/var/lib/relay` 与 `/etc/systemd/system/relay-22022.service`。

## 用 InstaBox VM 的网络上网（美国出口，2026-11-03 新增）

### 原理

VM 入站被封，但它能出站到香港 VPS。于是让 **VM 向 VPS 建反向 SSH 隧道**，把 VM 自己的 sshd 发布到 VPS 上，本地再对这条连接做 `ssh -D`，得到出口在美国的 SOCKS5。

```text
浏览器 ──SOCKS5 127.0.0.1:1080──► ssh -D ──► 香港 VPS [::]:22222
                                                 │ socat → 127.0.0.1:22222
                                                 ▼
                                          VM sshd（反向隧道，VM 发起）
                                                 ▼
                                        AWS 俄勒冈出口（16.148.25.106）
```

### 组成

| 位置 | 组件 | 说明 |
| --- | --- | --- |
| VPS | `relay-22222.service` | `socat TCP6-LISTEN:22222,...,ipv6only=1 → 127.0.0.1:22222`，已 enable |
| VPS | 用户 `relay` 的第二条 authorized_keys | `restrict,port-forwarding,permitlisten="127.0.0.1:22222"`，密钥 `instabox-tunnel` |
| VM | `/tmp/tun_key` + `setsid ssh -R 127.0.0.1:22222:127.0.0.1:22` | 由脚本注入并拉起 |
| VM | `~/.ssh/authorized_keys` | 追加 `instabox-login` 公钥 |
| 本地 | `scripts\Start-UsEgress.ps1` | 一键完成 VM 侧配置并打印 SOCKS 命令 |
| 本地 | `.local-secrets\vps_tunnel` / `vm_login` | 两把私钥（目录已 gitignore） |

### 使用

```powershell
powershell -NoProfile -File F:\codexprojects\instabox\scripts\Start-UsEgress.ps1
# 输出最后一行给出：
# ssh -D 1080 -p 22222 -i .local-secrets\vm_login -N user@2602:f506:81::6440:1333
```

然后把浏览器或系统代理设为 `socks5h://127.0.0.1:1080`。

### 实测（2026-11-03）

| 项目 | 结果 |
| --- | --- |
| 出口 IPv4 | `16.148.25.106`（AWS 俄勒冈） |
| 出口 IPv6 | `2600:1f14:34cf:7b1d:c073::14d` |
| google.com | HTTP 200，1.49 s |
| example.com | HTTP 200，1.07–1.40 s |
| 10 MB 下载 | 2.25 MB/s（约 18 Mbps） |

### 边界

- **只在 VM 运行期间有效**：VM 暂停或重建后隧道消失，需重新运行脚本。
- 吞吐约 2.25 MB/s，瓶颈是「校园 → VPS」与「VPS → VM」两段叠加，且是单 TCP 流；浏览网页足够，大文件下载不划算。
- 出口是数据中心 IP（AWS），部分站点会触发风控/验证码。

### 安全与回退

- 暴露的 22222 是 **sshd**（密钥登录，密码认证已关闭），不是开放代理。
- VM 上的 `tun_key` 放在 `/tmp`，仅在 VM 生命周期内存在。
- 回退：`systemctl disable --now relay-22222`（VPS），删除 `/var/lib/relay` 中的第二条授权；VM 侧无需处理（临时环境）。

### 常驻化（2026-11-03 晚）

按用户要求"打通链路即可、暂不用于直接上网"，把一次性脚本改成**常驻链路**：

| 项 | 变化 |
| --- | --- |
| VM 侧 | 新增 `/etc/systemd/system/vps-link.service`（`Restart=always`），密钥与 known_hosts 放在 `/data/home/.ssh/`，`systemctl enable` 已开启 |
| 本地脚本 | `scripts\Start-UsEgress.ps1` 改为安装并启动该单元，不再用 `nohup` |
| 用途 | 只保持"VM sshd 发布在 `2602:f506:81::6440:1333:22222`"，SOCKS 仍可用但不再优化 |

验证：

| 测试 | 结果 |
| --- | --- |
| 手动 kill 隧道进程 | `NRestarts=1` 后自动恢复，链路可用 |
| VM 挂起（`suspend`）后恢复（`start`） | `vps-link` 仍 active，从校园侧 SSH 仍返回 `LINK_AFTER_RESUME` |
| 校园侧连通性 | `ssh -p 22222 ... user@2602:f506:81::6440:1333` → 正常 |

说明：

- VM 闲置 30 分钟仍会自动挂起，但**恢复后链路自动可用**（已验证一次）。
- 若 VM 被销毁/重建，`/etc` 可能不保留，重跑一次脚本即可（幂等，约 10 秒）。
- 暂不做 QUIC 出口或吞吐优化。

## 美国出口：TCP SOCKS 与 QUIC 对比（2026-11-03 晚）

在链路空闲时交替测量两种出口方式（浏览器侧都是 SOCKS5，出口都是 AWS 俄勒冈 `16.148.25.106`）：

| 指标 | **TCP SOCKS**（`socks5h://[VPS]:1080`） | QUIC 中转（hysteria → VPS → wg0 → VM） | 直连参考 |
| --- | --- | --- | --- |
| example.com 页面 | 1.89 / 2.05 / 2.07 s | 1.90 / 1.92 / 2.05 s | 1.08 s |
| 10 MB 单流 | 1.93 / 2.10 / 2.13 MB/s | 1.32 / 1.34 / 1.35 MB/s | — |
| 50 MB 单流 | **4.10 MB/s**（12.2 s） | 1.65 MB/s（30.3 s） | — |
| 2×5 MB 并行 | 1.2 + 1.1 MB/s | — | — |

结论：

- **QUIC 没有优势**：页面延迟持平，吞吐反而慢 1.5–2.5 倍。原因是这条链路已经是「QUIC 一跳 + WireGuard 一跳」两段 UDP 叠加，RTT 120 ms 且带内偶有丢包，QUIC 拥塞控制吃亏。
- **保留 TCP SOCKS**：协议开销最小、无需本地客户端，`socks5h://dsh:<密码>@[2602:f506:81::6440:1333]:1080`。
- 本机 hysteria（QUIC 实验）已停止；配置与凭据见 `instabox\.local-secrets\us-quic.yaml`、`us-socks.txt`。

## 校园 ↔ VPS 链路劣化测量（2026-11-03 晚）

同一天内同一路径（服务器手机直连 VPS，绕过路由器代理）的对比：

| 指标 | 早上基线 | 当晚（链路空闲） | 变化 |
| --- | --- | --- | --- |
| TCP 连接 RTT（中位） | **77 ms** | **122 ms** | **+45 ms（+58%）** |
| RTT 分布 | 稳定 | p90 126 ms；100 次中 95 次落在 100–150 ms；**p99 1.11 s，2 次 >400 ms** | 出现偶发秒级停顿 |
| 50 MB 下载 | 9.3 MB/s | **8.6–8.7 MB/s**（3 次） | −7% |
| 上传 | 5.3 MB/s | 未复测 | — |
| ICMPv4 到 VPS | — | 162 ms，0% 丢包，mdev 0.17 ms | 稳定 |

用户后台下载期间的对照：

| 指标 | 空闲 | 下载中（WAN 3.5–4.9 MB/s） |
| --- | --- | --- |
| 校园 → VPS RTT | 121 ms | 127 ms |
| 经美国出口开页面 | ~1.9 s | 2.15–2.41 s |

结论：

- **主要劣化是延迟，不是带宽**：+58% RTT、偶发 1.1 s 停顿；吞吐只降 7%。
- 用户下载会额外叠加排队延迟，但只贡献约 +6 ms RTT；页面变慢主要来自基线 RTT 本身的抬升。
- 由于页面加载要多次往返，45 ms 的 RTT 抬升直接体现为 1.1 s → 1.9 s 的页面耗时。
- 校区到 1.1.1.1 / Google DNS 等外部参考点不可达，无法取得同链路的第三方对照；判断基于同一 VPS 的纵向对比。

## 上传速度复测与第三方测速端点（2026-11-03 晚）

### 端点可达性筛查（从校区手机实测）

| 端点 | 地址 | 可达 | TCP 连接 |
| --- | --- | --- | --- |
| Cloudflare Speed | speed.cloudflare.com | ✅ v4/v6 | v6 120 ms / v4 244 ms |
| OVH | proof.ovh.net | ✅ v4 | 181 ms |
| 中科大镜像 | mirrors.ustc.edu.cn | ✅ v6 | 49 ms |
| 清华 TUNA | mirrors.tuna.tsinghua.edu.cn | ✅ v6 | 41 ms |
| Tele2 | speedtest.tele2.net | ❌ | — |
| Linode 新加坡/东京 | speedtest.*.linode.com | ❌ | — |

结论：校区可用的可靠端点以 **Cloudflare Speed**（支持上传/下载、IPv6）为主，CERNET 镜像可作国内对照但需要有效大文件路径。

### 上传实测

| 方向 | 工具 | 结果 |
| --- | --- | --- |
| 校区(手机) → HK VPS | HTTP POST 50 MB | **0.79 / 0.86 / 0.94 MB/s** |
| 校区(PC) → HK VPS | HTTP POST 50 MB | **0.81 / 0.88 MB/s** |
| 校区(手机) → Cloudflare | POST 50 MB | 1.81 / 2.70 MB/s |
| 校区(PC) → Cloudflare | POST 50 MB | **4.64 / 5.75 MB/s** |
| 校区(手机) ← Cloudflare | GET 50 MB | 8.62 / 9.02 MB/s |
| 校区(手机) ← HK VPS | GET 50 MB | 8.6–8.7 MB/s |

接收端已排除瓶颈：VPS 上的接收器本机自测 **386–434 MB/s**；VPS 负载 0.00、网卡无 errors/drops。

### 结论

| 方向 | 早上 | 现在 | 变化 |
| --- | --- | --- | --- |
| VPS → 校区（下载） | 9.3 MB/s | 8.6 MB/s | −7% |
| **校区 → VPS（上传）** | **5.3 MB/s** | **0.79–0.94 MB/s** | **−84%** |
| 校区 → Cloudflare（上传，对照） | — | 4.6–5.75 MB/s | 正常 |

- 劣化是**方向性的**：下行基本不变，**上行塌陷**。
- 校区整体上行没坏（到 Cloudflare 仍有 ~5 MB/s），所以问题出在**校区 → 香港这条特定路径的上行方向**，而不是校区出口总带宽或 VPS 接收能力。
- 与 RTT 从 77 ms 升到 122 ms、偶发 1.1 s 停顿一致，符合该方向拥塞/丢包的特征。

### 复测命令（可复用）

```bash
# 下载（Cloudflare，IPv6）
curl -6 -k --resolve speed.cloudflare.com:443:[2a06:98c1:58::da] \
  -o /dev/null -w '%{speed_download}\n' 'https://speed.cloudflare.com/__down?bytes=50000000'

# 上传（Cloudflare，IPv6）
curl -6 -k --resolve speed.cloudflare.com:443:[2a06:98c1:58::da] \
  -X POST --data-binary @50mb.bin -o /dev/null -w '%{speed_upload}\n' https://speed.cloudflare.com/__up
```

## 上传方向深挖（2026-11-03 晚）

### 协议确认

对 VPS 的所有上传测试都是 **IPv6**（VPS 的 IPv4 入站被 NAT 屏蔽，`curl -4 198.44.170.21:22` 连接耗时 0、`code=000`）。Cloudflare 对照做了双栈：**v6 3.10 MB/s、v4 2.93 MB/s**，两者接近，说明差异不来自协议族。

### 测试矩阵

| 测试 | 结果 |
| --- | --- |
| TCP 上传 → VPS（HTTP POST，12.5–50 MB） | **0.36–0.97 MB/s** |
| TCP 上传 → VPS，4 条并发流 | 合计 **1.09 MB/s**（单流 0.24–0.30） |
| 裸 socket 上传（不经 HTTP） | 0.36 MB/s |
| QUIC（hysteria）上传 5 MB | **1.26 MB/s**（TCP 同期 0.86） |
| TCP 下载 ← VPS（对照） | 8.6 MB/s |
| TCP 上传 → Cloudflare v6 / v4 | 3.10 / 2.93 MB/s |
| UDP 洪水，VPS 收端持续实测 | **11.2 MB/s**（连续 9 秒稳定） |

### 根因证据

| 观察 | 数值 | 含义 |
| --- | --- | --- |
| ICMPv6 校园→VPS 丢包 | 1.7%（60 包）/ 6.7%（15 包，突发） | 该方向**确有丢包** |
| 上传时 RTT | 115 ms（空闲 117 ms） | **无 bufferbloat**，不是排队 |
| UDP 乱序（~2000 包/秒） | out_of_order = 0 | 不是重排问题 |
| TCP 重传比 | 0.00% / 0.02% / 6.41%（三次） | 丢包呈突发，多数时间表面干净 |
| VPS 接收窗口 `rcv_space` | **163–172 KB**，`rcv_ssthresh` 859 KB | 窗口被压住，rwnd/RTT ≈ 1.5 MB/s |
| VPS 接收器本机自测 | 386–434 MB/s | 排除服务端处理瓶颈 |
| VPS 负载 / 网卡 | 0.00 / errors=drops=0 | 排除 VPS 故障 |

### 结论

1. **瓶颈是「校园 → 香港 VPS」方向的随机丢包**（1.7–6.7%），叠加 117 ms RTT。
2. CUBIC 在此条件下吞吐按 `MSS/(RTT·√p)` 量级坍塌，实测被压在 **0.5–0.9 MB/s**；接收窗口只有 ~170 KB（≈ rwnd/RTT 1.5 MB/s）进一步封顶。
3. 同一时刻 UDP 能跑 **11.2 MB/s**、QUIC 能跑 **1.26 MB/s**，证明**路径本身有带宽**，是 TCP 的丢包响应把吞吐打没了。
4. 不是校区总上行问题：到 Cloudflare 上传仍有 **3 MB/s**。
5. 不是 VPS 问题：本机接收 400 MB/s、AWS 的 VM 早先上传到 VPS 有 10.8 MB/s。

### 可用手段（按实测收益排序）

| 手段 | 实测效果 |
| --- | --- |
| 改走 QUIC（hysteria）上传 | 0.86 → **1.26 MB/s**（+47%） |
| 4 条并发 TCP | 0.9 → 1.09 MB/s（+20%，帮助有限） |
| 先传到 Cloudflare 再中转 | 上行可用 ~3 MB/s（比直连快 3–6 倍） |
| 降丢包（换线路/换落地） | 治本，需换 VPS 或换出口 |

> 结论：直连上传慢是**线路丢包**导致，客户端调参（拥塞控制、窗口、并发）收益有限。

### 补充：路径 MTU 是 1484，不是 1500

用 `ping -6 -M do` 逐档探测校园→VPS 的 PMTU：

| 报文总长 | 结果 |
| --- | --- |
| 1448 / 1458 / 1468 / 1476 / 1480 / 1484 | ✅ 通过 |
| 1488 / 1500 / 1520 | ❌ 100% 丢包 |

- 实际 PMTU = **1484 字节**（非标准值），≥1488 的包直接黑洞，且没有 ICMPv6 PTB 回传（被过滤）。
- 内核后来通过路由缓存学到了：`ip -6 route get` 显示 `mtu 1484`，MSS 自动降到 1424，所以**日常 TCP 不会完全卡死**，只是在大包/首次连接时会出现停顿。
- 曾尝试把路由 MTU 手动降到 1400 复测：吞吐反而降到 0.21 MB/s（包数变多、丢包按包计），说明**丢包是按包而不是按字节**发生的，缩小 MSS 没有收益。
- 该值大概率来自校区侧的 PPPoE/隧道封装，属于线路特性，客户端无法改。

## 新日本 VPS（newnb-253）链路实测（2026-11-03 晚）

### 机器

| 项 | 值 |
| --- | --- |
| 主机 | newnb-253，Debian 13，jp2 节点 |
| 配置 | 1 vCPU（AMD EPYC 7R13）/ 464 MB / 7.7 GB |
| 地址 | IPv4 64.204.48.125，IPv6 2a14:7586:6109:1::2c0 |
| 自身出网 | Cloudflare 50 MB：**262 MB/s**；到香港 VPS：**12.0 MB/s** |

### 路由改动（已做，均带备份）

| 改动 | 位置 | 备份 |
| --- | --- | --- |
| 加 `IP-CIDR,64.204.48.125/32,DIRECT`、`IP-CIDR6,2a14:7586:6109:1::/64,DIRECT` | `/etc/nikki/profiles/party-profile.yaml` | `party-profile.yaml.bak-20261103-082646` |
| 加 WAN 入站 ICMPv6 echo 规则 | `/etc/config/firewall` | `firewall.bak-20261103-082848` |

改动后 PC 可直连该机（v4/v6 均 `SSH_OK`）。

### 链路质量

| 路径 | RTT 平均 | 抖动 | 丢包 |
| --- | --- | --- | --- |
| 校园 → 日本 VPS（IPv6，100 包） | **92.6 ms** | **1.29 ms** | **4%** |
| 校园 → 日本 VPS（IPv4，100 包） | 92.8 ms | 3.49 ms | 19% |
| 校园 → 香港 VPS（IPv6，100 包，对照） | 117.9 ms | 4.56 ms | 10% |
| 日本 VPS → 香港 VPS | 55.2 ms | 0.078 ms | 0% |
| 日本 VPS → 校园路由器 | **100% 丢包** | — | 上游阻断（香港发起同样 100% 丢包） |

### 但大流量下完全不能比

| 测试 | 日本 VPS | 香港 VPS |
| --- | --- | --- |
| 校园 → VPS 上传（25 MB） | 0.35–0.81 MB/s | 0.5–1.0 MB/s |
| **VPS → 校园下载（10 MB）** | **0.03–0.15 MB/s** | **3.8–8.6 MB/s** |
| 改用 MTU 1400 复测 | 0.09 MB/s（无效） | — |

从日本 VPS 侧抓连接状态，原因是明确的：

```
cubic  rtt:174ms  cwnd:11  ssthresh:9  bytes_retrans:92560 / 1347310 (6.9%)
retrans:0/65  reordering:7  reord_seen:45  send 718594bps
```

拥塞窗口被压到 11 个 MSS（约 15 KB），重传率 6.9%，吞吐自然只有 ~0.1 MB/s。

### 结论

- **空载延迟日本更好**（92.6 ms 对 117.9 ms），抖动也更小，适合轻交互。
- **大流量完全不行**：VPS → 校园方向丢包严重（重传 6.9%），吞吐只有香港的 1/30。
- 因此**不建议把 DSH 隧道或 hysteria 迁到这台**；香港 VPS 仍是批量传输与主通道的更好选择。
- 日本这台适合：低延迟交互、或作为到 Cloudflare/香港的中转（它到这两处都很快）。

## 新香港 lite VPS（hklite）实测（2026-11-03 晚）

### 机器与交接

| 项 | 值 |
| --- | --- |
| 主机名 | hklite-20260822055314 |
| 系统 | Ubuntu 22.04.5 LTS，kernel 5.15 |
| 配置 | 2 vCPU（Intel Xeon Gold 6138）/ 1963 MB / 20 GB |
| 地址 | IPv4 39.109.51.10；IPv6 **2403:4fc0:0:201::26c** |
| 自身出网 | Cloudflare 86.5 MB/s；到旧香港 VPS 62.5 MB/s |

交接动作（已做）：

- 已把 root 密码改成随机强密码（凭据存 `local/secrets/hklite-root.txt`，该目录在 .gitignore 内）
- 已安装公钥 `C:\Users\15185\.ssh\id_ed25519_hklite`，v4/v6 免密登录均验证通过
- 路由器已加 `IP-CIDR,39.109.51.10/32,DIRECT` 与 `IP-CIDR6,2403:4fc0:0:201::/64,DIRECT`（备份 `party-profile.yaml.bak-20261103-085108`）

### 链路质量（校园视角，同一时段 A/B）

| 指标 | 旧香港 2602:f506 | **新香港 lite 2403:4fc0** | 日本 2a14:7586 |
| --- | --- | --- | --- |
| ICMPv6 RTT（100 包） | 117.9 ms | **116.0 ms** | 92.6 ms |
| 抖动（mdev） | 4.56 ms | **0.52 ms** | 1.29 ms |
| IPv6 丢包 | 10% | **0%** | 4% |
| 下载 50 MB | **5.32 MB/s** | 4.55–4.68 MB/s | 0.03–0.15 MB/s |
| 上传 25 MB | 0.66–0.74 MB/s | **0.95–0.97 MB/s** | 0.35–0.81 MB/s |
| ICMPv4 | — | 227.9 ms / 6.7% 丢包 | 92.8 ms / 19% |

### 结论

- **新香港 lite 的 IPv6 链路最稳**：抖动 0.52 ms（旧香港 4.56）、丢包 0%（旧香港 10%）。
- **上传比旧香港快约 30%**（0.97 对 0.70 MB/s），下载略慢（4.6 对 5.3 MB/s）。
- IPv4 明显更差（228 ms、6.7% 丢包），**应当只走 IPv6**。
- 日本机器虽然空载延迟最低，但大流量下不可用（见上一节）。

选型建议：**新香港 lite 适合做主通道或至少做备份**（稳定性和上传更好）；旧香港在下行大文件上仍略占优。

### 旧香港 VPS 时序复测（2026-11-03 17:00–17:13，北京时间）

校园链路当时几乎空闲（WAN 50–188 KB/s 下行、8–95 KB/s 上行），排除本地占用干扰。

| 时间 | RTT | 上传（10 MB） | 下载（20 MB） |
| --- | --- | --- | --- |
| 17:06:13 | 112.0 ms | 879 KB/s | 6.07 MB/s |
| 17:07:21 | 113.4 ms | **242 KB/s** | 5.68 MB/s |
| 17:08:58 | 111.3 ms | 878 KB/s | 6.00 MB/s |
| 17:10:05 | 112.7 ms | **193 KB/s** | 6.38 MB/s |
| 17:11:52 | 113.3 ms | 862 KB/s | 6.15 MB/s |
| 17:12:59 | 110.8 ms | 852 KB/s | 6.18 MB/s |

全天纵向对比：

| 时段 | RTT | 下载 | 上传 |
| --- | --- | --- | --- |
| 早上（基线） | **77 ms** | **9.3 MB/s** | **5.3 MB/s** |
| 下午 16:40–16:55 | 117.9 ms（p50 122） | 5.32 MB/s | 0.66–0.74 MB/s |
| 傍晚 17:00–17:13 | 111–113 ms | 5.7–6.4 MB/s | **0.19–0.96 MB/s（双峰）** |

结论：

- **确实和时间有关**：早上到傍晚，RTT 从 77 ms 抬到 112 ms（+45%），下载从 9.3 掉到 6 MB/s（−35%），上传从 5.3 掉到 ~0.9 MB/s（−83%）。
- 但同一时段内**不是平滑劣化**：RTT 和下载高度稳定（±2%），**上传在 ~0.86 MB/s 与 ~0.2 MB/s 两个状态间跳变**，像是队列/丢包阈值触发的拥塞窗口塌陷。
- 该行为与「上行方向丢包打死 CUBIC」的判断一致：链路余量小，稍一拥塞就掉档。
- 与另一台香港节点同一时刻对比：17:00 前后两台都掉到 3–8% 丢包（前者几分钟前还是 0%），说明**丢包主要是校园侧国际链路时段性变差，不是某台机器的属性**。

### 定时复测（已配置）

为验证「早上是否真的更好」，做了一条可复用的采样脚本：

```bash
# 用法：rounds gap_seconds
bash scripts/hk-vps-linktest.sh 6 45
```

输出格式（每轮一行，自动起停远端测速服务）：

```
time      loss   rtt(ms)  up(KB/s)  down(KB/s)
17:20:03  6.7%   102.550  164       5942
```

已挂两个定时任务（Asia/Shanghai）：

| 时间 | 任务 |
| --- | --- |
| 2026-11-04 09:00 | 晨间复测（与昨天傍晚对比） |
| 2026-11-04 20:00 | 傍晚复测（与当天早上对比） |

任务会自动把结果追加到本文档并提交。

## 校园 → 香港 VPS 的实际链路（2026-11-03 追踪）

从服务器手机（不经路由器代理）向 VPS 做 `traceroute6`，并与 ASN/地理信息对照：

| 跳 | 地址 | AS / 网络 | 地理 | RTT |
| --- | --- | --- | --- | --- |
| 1 | `fd00:8::1` | 校园路由器 | 校园 | 0.6 ms |
| 2–5 | `2001:da8:2018::*` | AS23910 CNGI-CERNET2 | 校园核心 | 2–9 ms |
| 6–8 | `2001:da8:a2::*` | AS23910 | **广州**（GDUT 出口） | 3–17 ms |
| 9–10 | `2001:da8:2:b104::1`、`2001:da8:257:0:6100:64:4:246` | **AS4538 CERNET 主干** | **北京** | 4–31 ms |
| 11 | `2001:da8:2:1116::2` | AS23910 CERNET2 主干 | 天津/北京 | **36 ms**（+32 ms） |
| 12–13 | `2001:252:0:2::101`、`2001:252:0:109::2` | **AS23911 CNGI 北京国际出口** | **北京** | 36–39 ms |
| 14 | `*`（不回 ICMP） | 国际段隐藏跳 | — | ~70–100 ms |
| 15 | `2001:7fa:0:1::ca28:a257` | 香港 transit（APNIC 2002 分配） | **香港** | **110 ms**（+73 ms） |
| 16 | `fd10:1234:5678:ffff::` | VpsQuan 内网 ULA | 香港 | 111 ms |
| 17 | `2602:f506:81::6440:1333` | **AS62468 HKCLOUDX – VpsQuan L.L.C.** | 香港 | 110 ms |

### 结论：IPv6 国际出口在北京，不是广州直连

```
广州校园 ──(CERNET2 主干，广州→北京)──► 北京 CERNET/CNGI 出口 ──(国际段)──► 香港
   0.6 ms             +32 ms                     +73 ms
```

- 110 ms 的 RTT 中，**校园只占 4 ms**，广州→北京占 **32 ms**，北京→香港占 **73 ms**。
- 广州到香港直线距离约 130 km，正常直连 RTT 应 ~10–30 ms；**绕行北京导致额外约 80–100 ms**。
- 同一时段对三个目的地做 traceroute，**前 13 跳完全一致**，说明这不是某台 VPS 的问题，而是校区 IPv6 统一走北京 CNGI 出口：

| 目的地 | 出口后的 transit | 落地 RTT |
| --- | --- | --- |
| 香港 VPS | `2001:7fa::`（香港 transit） | 110 ms |
| 日本 VPS | `2400:8800::`（SoftBank JP） | 89–168 ms |
| Cloudflare | `2001:470::`（Hurricane Electric） | 162 ms |

### 对丢包的含义

- 6–8% 的丢包最可能出现在**北京 CNGI 出口 → 香港**这段国际链路（跳 13→15，中间被隐藏）。
- 中间跳普遍不回 ICMP echo / time-exceeded，无法做逐跳丢包测量；只能确认丢包发生在国际段之后（因为香港落地点本身响应正常）。
- 这也解释了为什么「换一台香港 VPS」不一定能解决问题——**瓶颈在校区到北京的出口和北京到香港的国际段，不在 VPS 侧**。

### 是否走 HKIX？——两个方向都走，只是端口不同

反查 PTR 才能确定设备归属（比解内嵌 IPv4 可靠得多）：

| 方向 | 该跳地址 | **反向 DNS** | 含义 | RTT |
| --- | --- | --- | --- | --- |
| **去程**（校园→VPS）第 15 跳 | `2001:7fa:0:1::ca28:a257` | **`vpsquan1-rge.hkix.net`** | HKIX 上 VpsQuan 的 peering 路由 | 110 ms |
| **回程**（VPS→校园）第 4 跳 | `2001:7fa:0:1::ca28:a1be` | **`cernet1-100g.hkix.net`** | HKIX 上 CERNET 的 100G 端口 | 71–79 ms |

结论与更正：

- **两个方向都在 HKIX 交换**，只是端口不同：去程经 CERNET 的 HKIX 端口进、VpsQuan 的 HKIX 端口出。
- 110 ms 的构成仍是：校园 4 ms + 广州→北京 32 ms + **北京→HKIX 73 ms**。
- **更正**：先前按 `2001:7fa:0:1::` 末尾 32 位「内嵌 IPv4」解码，把去程那一跳判成「中国电信福建（CT163）」，是**误判**。这些 IPv4 的 whois 归属与设备实际归属并不一致，应以 PTR 为准。
- 由于两端都在香港同一个交换中心（HKIX），**瓶颈不在香港落地，而在「北京 CNGI ↔ HKIX」这条国际链路上**。

### 逐段定位：瓶颈其实在 VPS 供应商送包的最后一段（2026-11-03 17:40 前后）

把「校园→HKIX」和「HKIX→VPS」拆开分别测，结论和之前推测的相反：

**从校园（服务器手机）测：**

| 目标 | RTT | 丢包 |
| --- | --- | --- |
| HKIX `cernet1-100g.hkix.net`（CERNET 的 100G 口） | **34.1–37.5 ms** | **0%（100 包）** |
| HKIX `vpsquan1-rge.hkix.net`（VpsQuan 的口） | 104–112 ms | 0% |
| VPS 本体 `2602:f506:81::6440:1333` | 110–112 ms | 1–8% |

**从 VPS 反向测：**

| 目标 | RTT | 丢包 |
| --- | --- | --- |
| `vpsquan1-rge`（自己的 HKIX 口） | **1.0 ms** | 0% |
| `cernet1-100g`（CERNET 的 HKIX 口） | **67.8 ms**（47.6–85.3） | 0% |
| 自己的网关 `2602:f506:80::1` | 2.6–14.2 ms | 0% |

### 结论：北京 CNGI → HKIX 不是瓶颈，反而很快

- **校园 → HKIX 核心只有 37 ms、丢包 0%**。这段（校园 → 北京 → CNGI → HKIX）包含在 37 ms 里，**没有额外 73 ms**。
- **VPS → HKIX 核心要 68–77 ms**，而它到自己的 HKIX 口只要 1 ms。
- 所以 110 ms 的拆解应改成：

| 段 | 耗时 |
| --- | --- |
| 校园 → 北京 CNGI → HKIX 核心 | **37.5 ms** |
| **HKIX 核心 → VPS（供应商这一跳）** | **~73 ms** |
| 合计 | ~110 ms |

- 主机名里的 **`rge`**（Remote Gigabit Ethernet）说明：HKIX 用 RGE 把交换平面延伸到远端机房，VpsQuan 的「HKIX 口」并不在 HKIX 核心机房，而是远程延伸，实测离核心近 70 ms。
- 因此**要降低延迟，得换一家真正在 HKIX 核心机房（或与 CERNET 有直连）的供应商**；换校园侧手段没用。
## 晚高峰连续采样（2026-11-03 19:47–22:53，MiniV6）

每 10 分钟一轮 ICMP（100 包 / 1200 B），每 3 轮追加一次 5 MB 上传 + 10 MB 下载。共 **21 轮**，其中 7 轮含吞吐。

### 总体

| 指标 | 均值 | 最小 | 最大 |
| --- | --- | --- | --- |
| ICMP 丢包 | **4.6%** | 2% | **18%**（仅 1 次） |
| RTT | **106.4 ms** | 92.1 ms | 117.4 ms |
| 抖动(mdev) | 8.1 ms | 3.3 ms | 14.1 ms |
| 下载 | **6.82 MB/s** | 5.57 MB/s | 7.56 MB/s |
| **上传** | **0.40 MB/s** | **0.056 MB/s** | **0.81 MB/s** |

### 分小时

| 小时 | 样本 | 丢包 | RTT |
| --- | --- | --- | --- |
| 19 时 | 2 | 3.0% | 106.7 ms |
| 20 时 | 6 | 4.8% | 108.9 ms |
| 21 时 | 7 | 5.1% | 104.6 ms |
| 22 时 | 6 | 4.3% | 105.8 ms |

### 吞吐原始记录（7 轮）

| 时间 | 上传 | 下载 |
| --- | --- | --- |
| 19:47 | 810 KB/s | 6.18 MB/s |
| 20:14 | 777 KB/s | 7.24 MB/s |
| 20:42 | 341 KB/s | 7.56 MB/s |
| 21:09 | **112 KB/s** | 7.53 MB/s |
| 21:38 | 586 KB/s | 6.60 MB/s |
| 22:05 | **57 KB/s** | 5.57 MB/s |
| 22:34 | 185 KB/s | 7.10 MB/s |

### 结论

1. **下载非常稳**：5.57–7.56 MB/s，全晚波动 ±15%，且**与时段无关**（20 点峰值时段反而略高）。
2. **上传极不稳定**：57–810 KB/s，**相差 14 倍**，且**与小时、与 ICMP 丢包率都不相关**。
   - 22:05 丢包只有 4%，上传 57 KB/s；19:47 丢包 2%，上传 810 KB/s。
   - 21:38 丢包 18%，上传反而有 586 KB/s。
3. **ICMP 平均丢包率不能预测上传速度** —— 说明杀吞吐的不是"平均丢包"，而是**短时突发丢包/队列溢出**，20 pps 的 ICMP 采样看不到。
4. 因此"晚高峰一定更差"的假设**不成立**：20 点的下载是今晚最好的之一，21 点丢包最高但吞吐正常。

### 待补的一环

要解释上传的 14 倍波动，需要看 **TCP 层重传**（`ss -tin` 里的 `retrans` / `bytes_retrans`），而不是 ICMP 丢包率。建议下次采样时在传输过程中抓一次 VPS 侧 `ss -tin`。
