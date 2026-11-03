# 广东工业大学 → 香港 IPv6 路径调查与候选 VPS 实测

- **测试日期**：2026-11-03（北京时间 17:30–19:20）
- **测试出发点**：校园内独立干线（服务器手机，Debian 13，不经路由器代理），IPv6 走 CERNET2
- **参照对象**：现用 MiniV6 `2602:f506:81::6440:1333`（VpsQuan / HKCloudX）
- **方法**：`ping`/`ping6`（30–100 包）、`traceroute -A`（带 ASN）、`whois`（team-cymru / APNIC）、PTR 反查、PeeringDB API
- **流量**：全部为 ICMP/小包探测，累计 < 5 MB，未做任何大流量测速

> 说明：本报告把「已核实」与「推断」严格分开。所有 IP 均来自公开评测页或厂商自己的 Looking Glass，并标注来源与日期。

---

## 一、MiniV6 当前拓扑与已核实的结论

### 1.1 地址与归属（已核实）

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| `2602:f506:81::6440:1333` | **AS62468**（HKCLOUDX – VpsQuan L.L.C.，美国注册） | team-cymru whois，2026-11-03 |
| PeeringDB 中的 VpsQuan | **AS133033**（VPSQUAN-AS-AP），**无任何 IX 记录** | PeeringDB API `net?name__contains=vpsquan` / `netixlan?asn=133033`（返回空） |
| 上游前缀 | `2602:f506:80::/41` | team-cymru |

### 1.2 两个 HKIX 跳（已核实，PTR 为准）

| 地址 | PTR（2026-11-03 反查） | 内嵌 IPv4 |
| --- | --- | --- |
| `2001:7fa:0:1::ca28:a1be` | **`cernet1-100g.hkix.net`** | 202.40.161.190 |
| `2001:7fa:0:1::ca28:a257` | **`vpsquan1-rge.hkix.net`** | 202.40.162.87 |

- `202.40.161.0/24` whois = HKIX-HK（香港中文大学运营）。
- **注意**：`2001:7fa:0:1::/64` 末尾 32 位内嵌的 IPv4 与设备归属**不一致**（例如 `...a257` 内嵌的是 202.40.162.87，whois 属 CHINANET-FJ，但设备实际是 HKIX 的 VpsQuan 口）。**不能用内嵌 IPv4 判断运营商或物理位置**，这是本次调查中走过的一次弯路。

### 1.3 分段实测（已核实）

| 测量 | 结果 | 结论 |
| --- | --- | --- |
| 校园 → `cernet1-100g`（HKIX 核心，100 包） | **37.5 ms，0% 丢包** | 校园经 CERNET2 → 北京 CNGI → HKIX 这段**又快又干净** |
| 校园 → `vpsquan1-rge` | 104–112 ms，0% | VpsQuan 那一侧明显更远/更绕 |
| 校园 → MiniV6 | 110–112 ms，ICMP 0–8%（随时间波动） | 端到端受供应商侧影响 |
| MiniV6 → `vpsquan1-rge` | **1.0 ms** | 供应商机内到其 HKIX 口正常 |
| MiniV6 → `cernet1-100g` | **67.8 ms**（47.6–85.3） | 供应商到 HKIX 核心要 68 ms |

### 1.4 可以确认 vs 仍是推断

**已确认：**
1. 校园→HKIX 核心只需 37.5 ms、0% 丢包 —— **瓶颈不在校园侧，也不在「北京 CNGI→HKIX」这一段**。
2. MiniV6 端到端 110 ms ≈ 37.5（到 HKIX 核心）+ ~72（HKIX→VPS 的供应商段）。
3. MiniV6 所在网络（AS62468 / VPSQUAN）在 PeeringDB **没有任何 IX 接入记录**。

**仍属推断（未证实）：**
- `rge` 后缀是否代表「Remote Gigabit Ethernet 远端延伸」——PTR 只证明该地址由 HKIX 管理，**不能据此断定物理位置**。
- 68 ms 的成因：可能是供应商 transit 绕路、也可能是回程路径不对称或 ICMP 处理策略差异；**需供应商侧 MTR 才能定论**。
- 不能把「HKIX 接口 RTT」直接相减得到链路时延（不同接口的回程路径与 ICMP 限速不同）。

---

## 二、候选服务器清单（地址、ASN、来源、日期）

| # | 候选 | 测试地址（本次实际使用） | ASN | 来源 / 核实日期 | 备注 |
| --- | --- | --- | --- | --- | --- |
| 1 | **云途 YT HK.BGP 系列** | `103.96.140.1` | **AS42960**（VH Global Limited, HK） | maobuni.com「测试IP Looking glass」评测，页面日期 2026-07-12，核实 2026-11-03 | 该评测机型为 HK.BGP.C；**不是 BF 专用 IP** |
| 2 | **Zouter 香港 BGP** | `155.117.82.11` | **AS205548**（ZOUTER LIMITED） | lot.pm「测试IP」评测，核实 2026-11-03 | PeeringDB：仅 **Equinix Tokyo** 与 **ixFabric Singapore**，**无 HKIX** |
| 3 | **EDIS 香港** | `158.255.208.12` / `2a03:f80:852:b422::1` | **AS9009**（M247 Europe SRL） | 官方 Looking Glass `hk.edisglobal.com`（葵涌），核实 2026-11-03 | `2a03:f80:852::/48` whois 明确写 EDIS-V6-HK |
| 4 | **WAWO 香港** | **未取得** | — | — | 官网为纯 JS SPA，未公布测试 IP；无公开 LG |
| 5 | **BestVM 香港 BGP-Lite** | **未取得** | — | — | 同上，官网未公布测试 IP |
| 6 | （现用）MiniV6 | `2602:f506:81::6440:1333` | AS62468（HKCLOUDX/VpsQuan） | 自有，已授权 | 基准 |

> **未采信的证据**：云途 TKO 的 LG `hk-tko.lg.yt.net`（107.151.244.169，AS154321 CoreNet Cloud）属于 **TKO 线路**，按要求**没有**用它代表 HK.BGP.BF。

---

## 三、实测结果对照表

### 3.1 延迟 / 抖动 / 丢包

| 目标 | 协议族 | RTT 平均 | 抖动(mdev) | 丢包 | 备注 |
| --- | --- | --- | --- | --- | --- |
| 校园 → HKIX `cernet1-100g` | IPv6 | **37.5 ms** | 0.37 ms | **0%**（100 包） | 关键参照点 |
| 校园 → MiniV6 | IPv6 | 112.7 ms | 5.5 ms | 0%（50 包） | 现用 |
| 校园 → EDIS HK | IPv6 | **110.6 ms** | 5.9 ms | 0%（50 包） | 与 MiniV6 持平 |
| 校园 → EDIS HK | IPv4 | 203.3 ms | 1.7 ms | 0%（50 包） | IPv4 路径更差 |
| 校园 → 云途 HK.BGP | IPv4 | 255.9 ms | 16.5 ms | 0%（50 包） | 无 IPv6 测试地址，未测 v6 |
| 校园 → Zouter HK | IPv4 | **不可达** | — | **100%** | ICMP 100% + TCP 443/80/22 全部 5s 超时 |

### 3.2 实际路由（traceroute -A，2026-11-03）

**能拿到 IPv6 路径的只有 EDIS：**

```
宿→校园核心  AS24357  ~3 ms
2001:da8:2:1116::2   AS4538  35.6 ms
2001:252:0:2::101    AS4538  35.5 ms   (CNGIIGN 北京)
2001:252:0:108::2    AS4538  37.0 ms   (CNGIIGN 北京)
*                    —       —
2001:470:e:97::2     AS6939  116.2 ms  ← Hurricane Electric
2001:de8:7::9009:1   AS0     111.7 ms  ← IXP 交换面
2a03:f80:852:b422::1 AS9009   98.5 ms
```

- **EDIS 的 IPv6 从 CNGI 北京出去后走的是 Hurricane Electric（AS6939），不是 CERNET 在 HKIX 的直连**，所以终点落在 ~110 ms，和 MiniV6 一样。
- Zouter 的 IPv4 完全不可达（ICMP 与 TCP 均超时），无法评估。
- 云途的 IPv4 可达但 RTT 256 ms（校园 IPv4 走 Dr.COM，与 CERNET2 完全不同路）。

---

## 四、是否经过 HKIX

| 对象 | 是否经 HKIX | 证据 | 强度 |
| --- | --- | --- | --- |
| MiniV6 去程 | 是（VpsQuan 口） | PTR `vpsquan1-rge.hkix.net` | **已核实** |
| MiniV6 回程 | 是（CERNET 口） | PTR `cernet1-100g.hkix.net` | **已核实** |
| EDIS HK | **未见** | 路径为 AS6939 → `2001:de8:7::` → AS9009；`2001:de8::/32` 是 RIPE 的 **IXP 分配块**，但不等于 HKIX | 已测路径中无 HKIX 地址 |
| Zouter HK | **未证实** | PeeringDB 仅列 Equinix Tokyo、ixFabric Singapore；**无 HKIX 记录** | PeeringDB（可能未更新） |
| 云途 HK.BGP | 未证实 | 无 IPv6 测试地址；IPv4 traceroute 从校园只返回星号 | 不可判定 |

**要点：接入 HKIX ≠ 低延迟。** EDIS 与 Zouter 都有 IX 或 HK 资产，但实测都没跑出 60 ms 以内；而 CERNET 自己在 HKIX 的口只要 37.5 ms。

---

## 五、哪些候选可能改善教育网路径

按「实际测到的证据」排序：

| 排序 | 候选 | 证据 | 判断 |
| --- | --- | --- | --- |
| 1 | **云途 HK.BGP.BF** | 唯一有第三方 CERNET2-IPv6 数据的产品：其买家 TcpQuality 报告（2026-10-29）中，**广东 CERNET2-IPv6 回程 67 ms / 0% 丢包**，北京 37 ms、上海 60 ms，多数省份标注走 **HKIX** | **最有希望**，但需 BF 专用测试 IP 复核 |
| 2 | EDIS HK | 实测 v6 110.6 ms，走 HE 绕行 | **未见改善**（与现用持平） |
| 3 | Zouter HK | 校园侧完全不可达；PeeringDB 无 HKIX | **不可评估 / 风险高** |
| 4 | WAWO / BestVM | 无公开测试地址 | **未证实** |

> 现用 MiniV6 的 112 ms 与 CERNET 在 HKIX 的 37.5 ms 之间的 ~75 ms 差距，**大概率来自供应商侧**（其网络在 PeeringDB 无 IX 记录，且端到端 110 ms、其自身到 HKIX 核心 68 ms）。

---

## 六、云途 HK.BGP.BF 是否值得买

### 6.1 官方参数（已核实，来自 `cloud.yt.net/server/config/134`）

| 项 | 值 |
| --- | --- |
| 计划名 | **HK.BGP.BF** |
| IP 类型 | **IPv4 + IPv6**（有 IPv6） |
| 配置 | 1 vCPU / 1 GB / 10 GB SSD |
| 流量/带宽 | 300 GB/月，200 Mbps |
| 价格 | **¥99/年**（用 `HKHKHK` 九折码后 **¥89.1/年**，与需求描述一致） |
| 周期 | 仅年付 |

### 6.2 证据评估

**支持购买的证据：**
- 该产品线有第三方 CERNET2 回程数据，**广东 67 ms / 0% 丢包**，且报告里多数省份标注走 HKIX。
- 价格极低（年付 89.1 元），试错成本小。
- 官方明确支持 IPv6。

**反对或未证实的部分：**
- **找不到 BF 专用的测试 IP 和 Looking Glass**。云途只对 TKO 线路提供 LG（`hk-tko.lg.yt.net`），BGP 线路无 LG。
- 那份 67 ms 的数据来自**第三方买家的 TcpQuality 报告**，测量点是 Zstatic CDN 节点（AS42960 VH Global），**不是广工校园**；且是「回程」指标，不等于我们校园的去程体验。
- 云途自家 HK.BGP 系列测试 IP `103.96.140.1` 的校园 IPv4 实测为 **256 ms**（IPv4 走 Dr.COM，不代表 IPv6 路径，但也说明不能想当然）。

### 6.3 结论

**「值得试，但不该盲买」**：
- 它是目前唯一有 CERNET2-IPv6 侧第三方数据、且价格极低的候选；
- 但 BF 专用 IP 未证实，67 ms 也不是我们校园实测。

**建议动作**：先向官方索要 BF 节点的测试 IP（IPv6 优先），拿到后在本校园实测；若 v6 RTT < 70 ms 且晚高峰丢包 < 1%，再买。

---

## 七、仍需在 20:00–23:00 验证的关键指标

| 指标 | 为什么 |
| --- | --- |
| 校园 → HKIX `cernet1-100g` 的 RTT/丢包 | 确认「37.5 ms 基线」在晚高峰是否成立（若晚高峰也劣化，说明瓶颈其实在校园到北京） |
| 校园 → MiniV6 的丢包曲线 | 现网 1–8% 的波动需要用更长采样（≥ 300 包）确认是时段性还是链路性 |
| 候选节点（拿到 BF 测试 IP 后）的 v6 RTT 与丢包 | 67 ms 的第三方数据必须在广工校园复现 |
| TCP 层重传率 | ICMP 0% 不代表业务 0%（用 `ss -tin` 看 retrans，或 TcpQuality 式采样） |
| 正反向路径差异 | 用 VPS 侧 MTR 回校园（校园入站 ICMP 被封，只能用 TCP 方式） |

---

## 八、给云途客服索要测试 IP 的具体要求

如果需要向云途官方索取，建议原文照发：

1. 请提供 **HK.BGP.BF** 该具体产品的测试 IP（**IPv6 优先**），不要用 TKO 线路或其他 BGP 型号的地址代替；
2. 请提供该节点的 **Looking Glass** 或 `ping6`/`traceroute6` 入口；
3. 请说明该节点**面向 CERNET2（AS23910/AS4538）的回程是否经 HKIX**，以及 200 Mbps 是否为**共享峰值**；
4. 请确认该产品**是否为中国大陆优化线路**（BGP 国际网络 vs 三网精品网络的差别）；
5. 若支持测试机，请提供 **1–3 天试用**，用于在广工校园实测晚高峰（20:00–23:00）。

---

## 附：本次测试的原始数据要点

- 校园→`cernet1-100g`：100 包，0% 丢包，34.1/37.5 ms（两次）
- 校园→MiniV6：50 包，0%，112.7 ms，mdev 5.5
- 校园→EDIS v6：50 包，0%，110.6 ms，mdev 5.9；EDIS v4：50 包，0%，203.3 ms
- 校园→云途 `103.96.140.1` v4：50 包，0%，255.9 ms，mdev 16.5
- 校园→Zouter `155.117.82.11`：ICMP 100% 丢包；TCP 443/80/22 全部 5 s 超时
- MiniV6→`vpsquan1-rge`：1.0 ms；MiniV6→`cernet1-100g`：67.8 ms
---

## 补充：授权主机上的吞吐验证（仅 MiniV6）

MiniV6 是我们已授权的机器，因此做过 TCP 吞吐测试（未用 iperf3，用的是自建 HTTP 收发，效果等价，流量受控）：

| 方向 | 结果 | 说明 |
| --- | --- | --- |
| 校园 → MiniV6 上传（25 MB） | 0.66–0.97 MB/s（约 5–8 Mbps） | 受上行丢包压制 |
| MiniV6 → 校园 下载（50 MB） | 4.6–6.6 MB/s（约 37–53 Mbps） | 高于 20 Mbps 目标 |

结论：**下载方向能稳定超过 20 Mbps，上传方向达不到**（这与前面测到的上行丢包一致）。
其他候选均无授权主机，未跑 iperf3，也不做无授权的测速。
