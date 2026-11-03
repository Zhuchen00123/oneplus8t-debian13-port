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
