# MC 手机服务端 + 443 出站反向隧道 —— 实施计划

- 日期：2026-10-04
- 状态：**计划阶段**。除只读探测外，未向手机部署任何文件、未新建/修改任何 CNB 环境、未购买任何服务。
- 工作树：独立 worktree，分支 `codex/mc-443-tunnel-20261004`
- 前置文档：本阶段开工时的两份内部交接记录（`mc-cnb-resume-prompt` / `gdut-wifi`），**未包含在本仓库中**；
  它们属于私有工作记录，其结论已在本文件内自洽重述，阅读本文件不需要它们。

---

## 0. 硬约束（用户已明确）

1. **手机是 MC 服务端**，不是 VPS，不是 ECS。
2. **不使用 ECS（云服务器）作为方案的一部分**（可另外申请作为一次性观察点，但当前未获授权）。
3. **不安装免费 MC 中继**（playit 等）。
4. **不购买 VPS**（当前；若将来购买，本计划允许落点可替换）。
5. **手机镜像仍在迭代**（另一会话正在完善充电驱动）→ **Phase 3 之前不动手机**。
6. MC 会话**不得修改**手机的充电/电源/温控/基础监控/SSH/USB 配置。
7. 学校账号密码不进聊天、不进项目文件；凭据只放 `/etc/*.env`（600）与 `local/`。

---

## 1. 已核实的事实（均已实际执行并有返回）

### 1.1 CNB 中继环境**仍然活着**（重要）

```
GET https://zzsh2enrz7-9000.cnb.run/status   → HTTP 200
  mcbridge 中继状态
  魔搭 DSW 隧道 : ❌ 未连接（DSW 实例已被回收，符合预期）
  玩家/好友连接 : 0 条
```

- 说明 CNB 上的 `mcbridge serve` **正在运行**，公网入口存在且手机可直达。
- **陷阱**：本地仓库里的 `endpoint.txt` 是**过期**的（`ha09ne7cql-9000.cnb.run`，我实测返回 302 `VSCODE_NO_INFO` + WS 404 已死）；
  远端仓库里的是 `zzsh2enrz7-9000.cnb.run`。**任何以本地文件为准的做法都会指向一个死地址。**

### 1.2 手机可正常调用 CNB OpenAPI

```
https://api.cnb.cool/       → HTTP 200, connect 0.125 s
GET /workspace/list?status=running（无令牌）
   → HTTP 401 {"errcode":16,"errmsg":"The task will not run because the user is not logged in."}
```

拿到规范鉴权错误 = 链路通、端点正确。

### 1.3 CNB OpenAPI 端点（取自 `https://api.cnb.cool/swagger.json`，199 条路径中筛出）

| 方法 | 路径 |
|---|---|
| GET | `/workspace/list` |
| POST | `/workspace/stop` |
| POST | `/workspace/delete` |
| POST | `/{repo}/-/workspace/start` |
| GET | `/{repo}/-/workspace/detail/{sn}` |
| POST | `/{repo}/-/build/start` |

### 1.4 手机读取中继地址的通路可用

```
GET https://cnb.cool/zc354/mmmmcccccbuild/-/git/raw/main/endpoint.txt → 200
  endpoint=zzsh2enrz7-9000.cnb.run:443/fwd_2b7e91c4d6a80f53
```

### 1.5 校园网延迟实测（手机直连，5 次 TCP 握手，已排除 DNS）

| 方向 | 目标 | 延迟 |
|---|---|---|
| 国内 | cnb.run / baidu / tencent | **9–24 ms**（极稳） |
| 香港 | Fastly 香港边缘 | 72–89 ms |
| 香港 | HKBN | 181–309 ms（抖） |
| 新加坡 | OVH SG | 188–285 ms |
| 新加坡 | Linode SG | 471–648 ms |
| 日本 | Linode Tokyo | 495–584 ms |
| 美国 | Cloudflare 边缘 | 177–259 ms |

→ **延迟上 CNB（国内）远优于香港，香港远优于东南亚。**

### 1.6 入站能力（IPv4 已定论，IPv6 未定论）

| 测试 | 结果 |
|---|---|
| 手机(校园 Wi-Fi) → 宿舍有线公网 IPv4 `<宿舍出口>:8080` | Timeout |
| 本机(宿舍有线) → 校园 Wi-Fi 公网 IPv4 `<校园出口>:8080` | False |
| 手机(校园 Wi-Fi) → 宿舍有线 PC IPv6 `240c:cd22:…:8081` | **HTTP 200 成功**（跨网段） |
| 校外 → 手机 IPv6（第 1 次） | 无效：测试设备仍在校园网 `2001:da8` 内 |
| 校外 → 手机 IPv6（第 2 次，用户操作） | 用户报"三个端口都超时"，但**手机探针日志零命中**，且未取得"测试设备确有 IPv6"的证据 → **不能判定为"入站被拦"** |
| 主机 → 手机 IPv6（对照） | 失败 `WSAENETUNREACH`；`Get-NetIPAddress` 读不到任何全局 IPv6 → **主机 IPv6 已消失，极可能是用户所挂梯子所为** |

**境外观测点尝试（均因工具原因失败，非结论）：**

| 途径 | 结果 |
|---|---|
| `api.microlink.io` 抓取 | ❌ **它自己没有 IPv6**（`ipv6.google.com`、`[2606:4700:4700::1111]`、`v6.ident.me` 全部 `statusCode=None`），无法用于 IPv6 探测 |
| `r.jina.ai` | ❌ 从本机与手机均不可达 |
| `api.allorigins.win` | ❌ 从手机不可达 |
| `check-host.net` | ❌ 其解析器无法解析 sslip.io 形式的 IPv6 域名 |
| `urlscan.io` | ❌ 现需 API key |
| 公开 IPv6 端口检测器（ipv6scanner / port.tools / subnetonline） | ❌ 均为 JS 表单或不可达，无可用 GET 接口 |

结论：**IPv4 入站两边都不通（已定论）；IPv6 入站仍未知**，但已排除"校内网段间被拦"。
待补数据：测试设备的 `https://ipv6.icanhazip.com` 结果（显示运营商地址如 `2409:`/`240e:` 才算有效测试）。
**该未知量不阻塞隧道方案**——隧道两条路都要建；若 IPv6 入站放行，只是多一条更优的直连路径。

### 1.7 主机与手机资源

- 主机：有 `rustc`/`cargo`（目标仅 `x86_64-pc-windows-msvc`、`x86_64-unknown-linux-musl`）、Miniconda Python、Docker（**守护进程未运行**）；**无 Go / clang / gcc / zig**。
- 手机：`/dev/sda15` 3.9 G，**剩余 1.7 G**，8 核。→ **不能在手机上编译 Rust。**

---

## 2. 未核实 / 未知

1. CNB 环境当前的**已运行时长**与下次回收时间（无公开 API 读取）。
2. 朋友端 `mcbridge forward` 是否仍可用（未实机验证）。
3. 手机上 MC Java 能否正常运行（未安装、未评估版本与内存）。
4. 校园网 **IPv6 入站**是否放行（唯一一个能改变整体方案的未知量）。
5. CNB 现有环境的 `WST_SECRET` 是否仍为公开默认值（需与环境配置对比）。

---

## 3. 目标架构

```
玩家 Windows: 定制客户端（mcbridge forward 改版）
   └─ wss://<envid>-9000.cnb.run/f/<token>/<session>/up|down
                        ▲
        [CNB 容器] mcbridge serve :9000      ← 公网入口（腾讯云，校园实测 9–24 ms）
                        ▲
                        │ 反向隧道 wss，手机主动出站 443
   手机 Debian 13 (kebab): <手机侧客户端> → 127.0.0.1:25565（MC 服务端）
```

- CNB 侧 `serve` 与朋友端 `forward` **沿用现有实现**，不重写。
- 唯一新增件：**手机侧客户端**（当前 DSW 扮演的角色）。
- 世界存档在手机上（ext4 持久），CNB 重建**不影响存档**。

---

## 4. 分阶段计划

### Phase 0 —— 冻结与隔离（已完成）
- 独立工作树 `local/worktree-mc`（在 `local/` 内，被 gitignore，不产生主仓库噪声，也不影响另一会话的分支与改动）。
- 已完成：源码 `mcbridge-rs` 复制进工作树；保活脚本骨架已写（未测试）。

### Phase 1 —— 手机侧客户端（**不碰手机**）
1. **选型决策**（见 §7 决策 A）：Python 纯标准库 vs Rust 交叉编译。
   - 现状：主机无 C 工具链、Docker 未运行、无 Go → **Rust 交叉编译到 aarch64 需要 ring/OpenSSL 的 C 工具链，当前不可行**。
   - 推荐：**Python 纯标准库实现**（`socket` + `ssl` + 手写 WebSocket 帧 + `threading`）。理由：零编译、零依赖、用系统 OpenSSL、可直接在主机上用同一份代码对 CNB 跑通后再拷到手机。
2. 在主机上实现并对**已存活的 CNB 环境**做真实联调（不消耗额外环境）：手机侧客户端可以先在主机上跑，连 `zzsh2enrz7-9000.cnb.run`，目标是手机上一个临时 TCP 服务（先用 `nc`，不装 MC）。
3. **验收**：CNB `/status` 页显示「隧道已连接」；从朋友端 `forward` 能穿到临时 TCP 服务并收到预期回显。

### Phase 2 —— 保活与地址同步（**不碰手机**）
- 完善已起草的 `mc/cnb-keepalive/kebab-cnb-keepalive.sh`：
  - `workspace/list` 探测 → 不在则 `workspace/start`
  - 拉远端 `endpoint.txt` → 与本地配置比对 → 变了就更新并重启客户端
  - 防抖（每小时最多 N 次重启）+ journald 日志
- **验收**：在主机上以 `--dry-run`/mock 方式验证解析、比对、防抖三分支；**不真调 `workspace/start`**（避免无谓重建把当前可用环境弄没）。

### Phase 3 —— 手机部署（**等手机镜像稳定后再做**）
- 新增物仅三件：`/usr/local/sbin/kebab-mcbridge-client`（脚本或二进制）、`kebab-mcbridge-client.service`、`kebab-cnb-keepalive.{service,timer}`。
- **不触碰**内核/无线/USB/充电/电源/温控/SSH/基础监控。
- **回滚**：`systemctl disable --now` 两个 unit + 删除三个文件，无残留。
- **验收**：手机重启后自动连上；`/status` 显示隧道已连接；`systemctl list-units --failed` 为空；手机根分区剩余空间不因部署而紧张。

### Phase 4 —— 端到端联调（MC 就绪后）
- 手机安装 MC Java 服务端 + 配置 `online-mode`/白名单。
- **验收**：朋友端能进世界；记录实际 in-game ping、掉线重连行为、CNB 重建时的断线时长。

### Phase 5 —— 朋友端定制
- 在现有 `mcbridge.exe` GUI 基础上：地址自动拉取（含"本地文件过期"防护）、在线/离线状态、一键复制 `127.0.0.1:25565`。
- **验收**：朋友无需人工输入地址，中继地址变化后能自愈。

---

## 5. 为什么是 CNB 而不是 VPS

| | CNB 免费 | 国内 VPS | 香港 VPS |
|---|---|---|---|
| 校园实测延迟 | **9–24 ms** | ~15 ms | 72 ms+（线路差更糟） |
| 常驻 | ❌ 18 h 硬上限，需自动重建 | ✅ | ✅ |
| 地址稳定 | ❌ 每次重建变 | ✅ | ✅ |
| 玩家需装客户端 | 需要 | 不需要 | 不需要 |
| 合规风险 | 擦边（见下） | 无 | 无 |

**风险（必须知情）**：CNB 官方[《推荐用法》](https://docs.cnb.cool/zh/workspaces/working-principle.html)只描述"建分支→开发→提 MR→自动回收"的开发流程；
[《工作区回收机制》](https://docs.cnb.cool/zh/workspaces/workspace-recycling.html)明确 18 h 上限、10 min 空闲回收、4–6 点强制回收。
把开发环境当常驻游戏中转**不在推荐用法内**，存在被判滥用而清理的可能。

**结论**：CNB 适合"零成本先把链路跑通并验证"；架构上把落点做成**可替换**（手机端只改一个 host 参数），将来加一台 VPS 即可升级为 7×24。

---

## 6. 风险与回滚

| 风险 | 影响 | 缓解 |
|---|---|---|
| CNB 环境被回收/清理 | 隧道中断 | Phase 2 保活自动重建；地址自愈；**存档不受影响** |
| 18 h 重建 | 玩家掉线几分钟 | 重建前后自动同步；可写清"每天会断一次" |
| 公开默认密钥 | 被扫到就能连中继 | 上线前替换私有密钥 |
| 本地 `endpoint.txt` 过期 | 指向死地址 | **一律以远端为准**，禁止以本地文件判定 |
| 手机根分区仅剩 1.7 G | 装 MC 可能不够 | Phase 4 前先算清 MC + JRE 体积；必要时清理或扩容 |
| 手机镜像迭代中 | 部署可能被覆盖/干扰 | **Phase 3 前不动手机**；与基础会话约定可写窗口 |
| Python 客户端性能 | 玩家多时可能吃力 | 先用小规模验证；不行再换 Rust（那时可借 Docker） |

**全局回滚**：手机侧只有三个新增文件 + 两个 unit，全部可 `disable` + 删除；CNB 侧不新建环境，改动仅限于仓库文件。

---

## 7. 待用户决策

**A. 手机侧客户端用什么语言？**
- **Python 纯标准库**（推荐）：零编译、马上能在主机验、手机直接跑；代价是性能上限低、需要 Python 常驻。
- **Rust（沿用 mcbridge）**：与现有代码同源、性能好；代价是**当前无法交叉编译**（缺 C 工具链、Docker 未运行），需要你启动 Docker Desktop 让我用 `rust-musl-cross` 镜像，或在手机上装 rustc（要占掉本就紧张的 1.7 G）。

**B. 落点最终形态？**
- 只用 CNB（免费，接受每天断一次）；
- 还是"先用 CNB 跑通，之后加一台国内 VPS 升级为 7×24"（架构已保证可无缝替换）。

**C. 是否要顺手把校园网 IPv6 入站这件事测掉？**
- 你之前那次测试设备仍在校园网内，结论无效。如果 IPv6 入站其实放行，**整个隧道都不需要**，玩家可直连。
- 需要：一台关掉 Wi-Fi、只走移动数据的设备，打开 `http://[2001:da8:2018:2232:203:7fff:fe12:fbfb]:8080/`（探针现已关闭，需要时我 10 秒重启）。

**D. MC 版本与规模？**
- Java 版原版还是模组服？预计几人同时在线？这决定 JRE 版本、内存占用和根分区是否够用。

---

## 8. 文件布局（工作树内）

```
mc/
  PLAN.md                       ← 本文件
  mcbridge-rs/                  ← 参考实现源码（已复制，未改动）
  cnb-keepalive/
    kebab-cnb-keepalive.sh      ← 已起草，未测试
    （待补：.env.example / .service / .timer / install.sh）
  client/                       ← 待写：手机侧客户端（Phase 1）
```

## 9. 明确不在本计划内

- 购买任何 VPS、注册任何新账号。
- 使用 ECS 作为方案组件。
- 安装免费 MC 中继平台。
- 修改手机内核、无线、USB、充电、电源、温控、基础监控、SSH 配置。
- 修改 CNB 仓库的现有环境配置（密钥替换放在上线前单独一步，且需你确认）。

---

## 10. Phase 1 验证记录（2026-10-04，未碰手机）

### 10.1 中继协议级验证：通过 ✅

用本机 Python/OpenSSL 直接对接活着的 CNB 中继（`zzsh2enrz7-9000.cnb.run:443`），
以 `/status` 页面是否翻转作为判据：

| 测试 | `/status` 显示 | 结论 |
|---|---|---|
| 基线 | `❌ 未连接` | — |
| 正确密钥 `wst_9f3a7c2e5b18d460` 开 `/t/<secret>/kebab/up` | `⚠️ 只有一半（已注册）` | UP 流注册成功，密钥正确 |
| 关闭该连接 | `❌ 未连接` | 状态回收正常 |
| **错误密钥** 开 `/up` | `❌ 未连接` | **隧道密钥被强制校验**（负对照通过） |
| 正确密钥同时开 `/up` + `/down` | **`✅ 已连接`** | **整条隧道建立成功** |

**结论：TLS 路径、密钥、路由格式 `/t/<secret>/<session>/up|down`、中继服务本身全部可用；
唯一缺口是手机侧的客户端进程。**

配套证据文件：
- `mc/tests/relay-probe.py`：TLS + WS 升级探测
- `mc/tests/relay-auth-test.py`：鉴权活性探测（灵敏度不足，保留作历史）
- `mc/tests/relay-secret-enforce.py`：**以 `/status` 翻转为准的鉴权判定（推荐）**

### 10.2 协议要点（从源码核实，写客户端时必须一致）

- **配对靠帧里的 id，不靠 session 名**：好友侧 `forward` 的 session 是随机的
  （`dialer.rs:211 random_session()`），服务端按 frame id 分发，因此
  `client --session` 与好友侧**无需一致**。
- 帧格式：`[type:1][id:4BE][payload]`；类型 `1=OPEN 2=DATA 3=CLOSE 4=PING 5=PONG`。
- 路径：隧道 `/t/<secret>/<session>/up|down`；好友 `/f/<token>/<session>/up|down`。
- 空密钥用 `_` 占位，否则路径塌成 3 段会被当成兼容模式（`tunnel.rs:551`）。
- PING 每 **20 秒**发一次（`tunnel.rs:571`）。
- 默认端口并不统一：`client` 默认 **7000**、`forward` 默认 **9000**、`serve` 默认 9000
  → 一律显式传 `--port 443`。

### 10.3 主机侧观察到的两个现象（**结论已修正**）

**(1) schannel TLS 失败 —— 是 DSH 沙箱所致，不是本机故障** ⚠️（原先误判为"系统 TLS 损坏"）

现象：

```
mcbridge.exe client/forward : TLS 握手失败: 安全包中没有可用的凭据 (os error -2146893042 = SEC_E_NO_CREDENTIALS)
Invoke-WebRequest https://www.baidu.com/  → 基础连接已经关闭: 接收时发生错误
Invoke-WebRequest https://www.qq.com/     → 同上
Invoke-WebRequest https://cnb.cool/       → 同上
curl.exe https://www.baidu.com/           → 000
```

定因实验：

| 证据 | 结果 | 含义 |
|---|---|---|
| `curl.exe --noproxy '*' https://www.baidu.com/` | `schannel: AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS (0x8009030e)` | **在"获取凭据"阶段就失败，尚未触碰网络** |
| 同机 Python/OpenSSL（同一网络） | baidu / qq / cnb.cool 全部 **HTTP 200**（TLS 1.2 / 1.3） | 网络、证书链、TLS 协商**全部正常** |
| Clash 系统代理 `127.0.0.1:7897` | TCP 可达 | 代理不是原因 |
| `%APPDATA%\Microsoft\Crypto\RSA` | 可写 = **False** | schannel 无法创建凭据材料 |
| `C:\ProgramData\Microsoft\Crypto\RSA\MachineKeys` | 可写 = **False** | 同上 |
| FIPS 策略 | `Enabled = 0` | 排除 |

**修正结论：坏的只是受限 shell（沙箱不允许写 crypto 密钥库）；
不能据此判定用户机器有问题。** 若是系统级损坏，浏览器与 Clash 早已一并失效。

**影响（修正后）**：
- `mcbridge.exe` 那两次失败是**本 shell 环境所致**，**不构成好友端有缺陷的证据**；
  好友端应在朋友自己的电脑上验证。
- 本机侧做 e2e 时，所有 TLS 角色统一用 Python/OpenSSL（不受影响）。

**给用户的独立自检命令**（普通 PowerShell，不经 DSH）：

```powershell
Invoke-WebRequest https://www.baidu.com/ -UseBasicParsing | Select-Object -ExpandProperty StatusCode
```

预期 `200`。

**(2) 本机无全局 IPv6 —— 属实，但原因待定**

```
TcpClient(IPv6)            → WSAENETUNREACH
ipconfig /all              → 各适配器只有 fe80:: 链路本地地址，无全局地址
Get-NetIPAddress / Get-NetRoute → 拒绝访问（沙箱限制）
```

上一轮本机还有 `240c:cd22:138f:a3b5:604c:9561:954b:7a8d`。原因**未定**，两个候选：

- **Clash**：系统代理 `ProxyEnable=1 → 127.0.0.1:7897`，`Clash Party` (pid 19524) 正在运行；
  不少客户端会主动禁用 IPv6 或接管路由。
- **线路本身**：本次观察到 `PPP adapter Dr.COM`（PPPoE 拨号），
  宿舍有线走 Dr.COM 认证的 PPP 会话，这类会话通常不下发 IPv6 前缀。

这也可能是 §1.6 那次"对照实验"失败的原因，但**不能据此断言**。

### 10.4 Phase 1 剩余工作

1. 写手机侧客户端（拟 Python 纯标准库：`socket` + `ssl` + 手写 WS 帧 + `threading`）。
2. 在主机上跑通完整 e2e：假 MC ← Python client ← CNB ← Python forwarder ← 测试连接
   （好友角色也先用 Python 实现，以绕开本机 schannel 故障；真实好友端另测）。

---

## 11. Phase 2 & 3 记录：CNB 保活已部署（2026-10-04 深夜）

**状态：已部署并启用。** 全量说明见 `mc/cnb-keepalive/README.md`。

### 11.1 交付物（手机侧）

```
/usr/local/sbin/kebab-cnb-keepalive.sh           0755
/etc/kebab-cnb-keepalive.env                     0600  ← CNB_TOKEN 在这里
/etc/systemd/system/kebab-cnb-keepalive.service
/etc/systemd/system/kebab-cnb-keepalive.timer    enabled/active，2 分钟一轮
```

源码在 `mc/cnb-keepalive/`：脚本、配置模板、service、timer、幂等安装脚本、README。
测试在 `mc/tests/test-cnb-keepalive.sh`（**48 项，主机与手机各跑一遍全过**）。

### 11.2 真机证据（不是 mock）

环境被回收后重新拉起，远端 `endpoint.txt` 写入了新 envid，本地还是死的旧地址：

```
sd2xm49rnt-9000.cnb.run  → HTTP 426（活）
zzsh2enrz7-9000.cnb.run  → HTTP 302（死）

地址变化：zzsh2enrz7-9000.cnb.run:443 → sd2xm49rnt-9000.cnb.run:443
✅ 已按新地址重启
[客户端] 已连上中继 sd2xm49rnt-9000.cnb.run:443（session=kebab）→ 127.0.0.1:25565
[中继 /status] 魔搭 DSW 隧道 : ✅ 已连接
```

沙箱自愈：把 `MCB_HOST` 故意改回死地址，经**真实 systemd 单元**（带 `ProtectSystem=full`）
跑一轮后自动改回新地址并重连，零只读报错。

### 11.3 测试抓到的三个真 bug（都已修）

1. **"远端给了活的新地址 → 直接退出、永不同步"** —— 原顺序是"先判活性再比地址"，
   环境重建后新地址是活的，脚本就以为一切正常直接退出，客户端却还拿着旧地址重连。
   保活看起来完全正常，实际永远修不好。改为**先同步地址、再判活性**。
2. **"历史错误被当成当前故障 → 每 2 分钟无脑重启客户端"** —— 原本数最近 30 行的
   "隧道错误"，但那些是连上之前的历史，已被后来的成功连接覆盖。
   结果隧道每 2 分钟被自己断一次，比不保活还糟。改为只看**最后一次成功连接之后**的日志。
3. **`sed -i` 在 systemd 沙箱下必然失败** —— 它是"同目录建临时文件再 rename"，
   而 `ProtectSystem=full` 让 `/etc` 只读；即使 `ReadWritePaths` 放开了那个**文件**，
   rename 仍要写**目录**。改为读进内存、原地写回。

顺带把稳态日志改成"只在状态变化时打"（2 分钟一轮重复打会把 journal 一天刷 700 多行），
每轮探测细节降级到 `--verbose`。

### 11.4 令牌

`CNB_TOKEN` 已配置（600），取自 `F:\codexprojects\mcfuwuqi\.cnb-token`（源文件未改动）。
验证：`GET /workspace/list?status=running` → HTTP 200，响应含 `zc354/mmmmcccccbuild`。

### 11.5 未验证 / 待办

- **`workspace/start` 只在 mock 里验证过**：真机触发需要环境处于停止状态，
  而它现在正驮着隧道，不适合为测试停掉。等自然回收时自动走一遍。
- 重启后 `OnBootSec=90s` 行为未实机验收。
- **密钥轮换**：`wst_9f3a7c2e5b18d460` / `fwd_2b7e91c4d6a80f53` 仍是公开默认值，
  上线前要连同 CNB 侧 `start-bridge.sh` 一起换掉（需用户确认后再动 CNB 仓库）。
- 手机根分区与 MC 安装（Phase 4）仍未开始。
