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
