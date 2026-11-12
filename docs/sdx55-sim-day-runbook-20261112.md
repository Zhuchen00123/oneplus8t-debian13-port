# SDX55 插卡当天 runbook（2026-11-12）

> 目标：**插卡 → 可验证的主机 LTE 数据**。当前唯一物理阻塞是 SIM 卡不在位；本文件把散在[执行记录](sdx55-modem-bringup-20261112.md)与[数据面/MM 可行性](sdx55-modemmanager-20261112.md)里的步骤收成一张可照做的清单。全部命令都在设备上以 `debian` 用户 + `sudo -n` 执行，不刷机、不改分区、不写 NV。

## 0. 前提（插卡之前）

1. **先插卡再开机**。社区实测 MCFG 在 mission mode 后约 15 s/24 s 各跑一次，卡若仍 PIN 锁着就永不激活配置；**MCFG 无法运行时重触发**，热插卡等于白插，插了也必须重启。
2. （可选，仅在卡有 PIN 时）把 PIN 写进 root-only 文件：
   ```sh
   sudo install -m 600 /dev/null /etc/kebab-sim-pin
   sudo sh -c 'printf "%s\n" "<PIN>" > /etc/kebab-sim-pin'
   ```
   不留这个文件是**安全默认**：脚本会跳过 PIN 自动验证，不会赌 PUK。
3. **给"慢卡"留出时间窗**（本轮新增）：`sim-provision.sh` 默认在无卡/卡未就绪时 <0.2 s 退出（这是常态，不能拖慢每次开机）。插卡当天请先建一个标志文件，让它在开机时**最多等 12 s 抓卡**，再把 provisioning 会话赶在 MCFG 的 15 s/24 s 之前建立：
   ```sh
   sudo touch /etc/kebab-modem-sim-expected
   ```
   实测：无标志 113 ms 返回；有标志 12.4 s 返回（等满窗口）。**代价**：bring-up 会调两次 sim-provision（置 online 前后各一次），所以带标志开机/重启会多约 **24 s**——实测 `systemctl restart kebab-sdx55-modem` 带标志 **28 s**（两次等待 18:27:17→18:27:33→18:27:45，unit `Result=success`）。插卡验证完把标志 `sudo rm` 掉即可回到快路径。
4. 确认没有别的进程在抢 LOC/NAS（`kebab-gps.service` 是正常持有者，不要同时跑 `qmicli` 的 LOC 命令）。
5. 不改 `/etc/modprobe.d/90-kebab-server.conf` 的 blacklist 语义；modem 由 `kebab-sdx55-modem.service` 显式拉起。

**开跑前先过一遍 pre-flight**（只读，26 项：开机链 / 卡与 PIN / 数据面工具 / **runbook 里会敲的每个工具都在** / QMAP 备选就绪 / 打补丁的 MM / 状态决策；FAIL 说明前提没满足，WARN 是要知道的点）：

```sh
sudo sh /usr/local/lib/kebab-modem/sim-preflight.sh
```

它会检查：开机链是否 enable、三个支撑 unit 是否在、`KEBAB_SDX55_ONLINE=auto`、`sim-provision.sh` 与 PIN 文件权限（0600）、数据面工具、data.service 是否误 enable、MM 的 4 个 .deb 及其校验和，**当前 boot 里是否已经能看见卡**（若是，说明卡是开机后插的，必须现在重启），以及 SIM 当天的等待标志是否就位。无卡、无标志时实测输出 **26 passed, 0 warnings, 0 failed** + 两行 `INFO`（第 56 轮彩排时标志已在位，同一份检查给 **27 passed** —— 标志那条从 `INFO` 变 `PASS`）（3b/3c/3d 三组检查 runbook 会敲的工具、QMAP 备选与打补丁的 MM 是否就位，见[数据面文档](sdx55-modemmanager-20261112.md) §4.31）。

**先跑这一条**（本轮新增：把下面的步骤串起来并留下证据包；无卡时会明确回 `verdict: FAIL reason: no SIM card...` 并以 1 退出，不会假装成功）：

```sh
sudo kebab-sim-day --hold 180
#   --hold 180 是真的把呼叫按住 180 秒：起来先探一次（probes_at_start），
#   按住期间每 15 s 采样（samples.tsv），结束时再探一次；两种结果都进 summary。
#   产物目录：/srv/modem-YYYYMMDD/simday-<UTC 时间戳>/
#   preflight.txt / status-before.txt / dataup.log / probes.txt /
#   status-after.txt / dmesg-tail.txt / summary.txt / run.log / samples.tsv /
#   nas.txt / dns.json / tcp.json / manifest-check.txt / SHA256SUMS
```

若 `start` 报的是订阅相关原因，可先加 `--bind-subscription primary`（该旋钮已在无卡状态验证可用：固件默认 `any`，bind 返回 success 且能读回，见[数据面文档](sdx55-modemmanager-20261112.md) §4.5）。

判定只看 `summary.txt` 的 `verdict`：`PASS` = bearer 有地址，且 **ping 或一次 DNS 查询**任一成功（运营商过滤 ICMP 时不会再误报，见[数据面文档](sdx55-modemmanager-20261112.md) §4.23）；`FAIL` 会给出 `reason`（无卡 / 无地址 + `start_error` / 有地址但 ICMP 与 DNS 都没回应）。它**双栈**：profile 3 是 `ipv4-or-ipv6`，`data-up` 现在**有 v4 配 v4、有 v6 配 v6、都有就都配**（`ip -6 … nodad`）；只有 v6 时不再判死，只打印 `note : bearer is IPv6-only; IPv4 targets will not work`。`summary.txt` 多了 `addr6:` 一行，探针在有 v6 地址时会补 `ping -6`（网关 / `2400:3200::1` / `2001:4860:4860::8888`），**任一地址族 ping 通即 PASS**。v6 侧的 TLV 编号（0x25/0x26/0x27/0x28）来自 libqmi IDL 的 Get Current Settings 段，不是猜的（MM 文档 §4.18）。

**不碰默认路由与 DNS**，结束后自动停呼叫并清理接口。

下面是逐步手工做法，用于排障或分步验证。

## 0. 插卡前先彩排一次成功路径（2 分钟，不碰基带）

`sudo kebab-bearer-rehearse.sh wlan0` 会用与 `kebab-sim-day` **同一段**探测代码打 wlan0，验证 ping/DNS/TCP/PMTU 四条判据与 `verdict` 的成功分支真的能跑通。期望 `result   : REHEARSAL PASS`。它不写任何基带证据，也不碰 `mhi_hwip0`。

再跑一次**流程彩排**（假承载，仍然不碰 modem）：

```
sudo kebab-sim-day --fake-bearer --hold 20 --out /tmp/fake-bearer
```

期望 `verdict: REHEARSAL`、`bearer_source: fake (no QMI, plumbing test)`、`addr: 10.99.13.2/30`、`sample_trend: N samples`，收尾 `addr=[] mtu=16384 state=down`。它验证的是**中段**：等地址 → 探针 → summary → 清理。假承载永远不会写出 `verdict: PASS`。

## 0.5 文档与设备对一遍（可选，30 秒）

`sudo python3 /usr/local/lib/kebab-modem/runbook-lint.py <文档>` 会把文档里所有 `kebab-*` 命令抽出来，逐个确认程序/子命令存在、以及已安装的 `kebab-*` 单元的 `ExecStart` 真的可执行。**文档漂移在插卡当天代价最高**，而读一遍页面证明不了它（§4.31 抓到过：某单元的安装步骤只装了 `.service`、没装它启动的脚本）。

## 0.8 开机时间线（第 56 轮实测，带 `/etc/kebab-modem-sim-expected`）

插卡当天按这个对照，**慢不等于坏**：

```
[  5 s] modprobe mhi / mhi_sahara_modem / mhi_wwan_ctrl / mhi_pci_generic ok
[ 15 s] modem QMI ready (qrtr node 3)
[ 30 s] [sim] no SIM ... skipping        <- 无卡时 12 s 慢卡等待满
[ 31 s] operating mode set to online
[ 43 s] kebab-sdx55-modem.service Finished (Result=success)
[ 70 s] GNSS 开始出星（先十几颗，再涨到 20+）
[ ~90 s] mmcli -L 稳定出现 Modem/0
```

三条读日志的规矩：

1. **看 `journalctl -o short-monotonic`**：这台机器没有 RTC，开机后时钟会向前跳几十分钟，墙钟时间不可比。
2. MM 开机后前 10 s 会连报 `port qrtr3 timed out`，之后恢复正常——那是启动竞态，`qmicli`/`sim-day` 不受影响。
3. 有卡时开机可能比上表多 12–24 s（两次 `sim-provision` 等卡），**这是设计**，为了不放过 MCFG 的 15 s/24 s 窗口。

## 1. 开机后 60 秒内的检查

```sh
systemctl status kebab-sdx55-modem --no-pager | head -5     # 应为 active (exited)
journalctl -u kebab-sdx55-modem -b --no-pager | tail -20    # 看 QMI ready / opmode / sim 行
journalctl -t kebab-sim -b --no-pager | tail -10            # 卡是否 present、会话是否激活、PIN 是否验证
```

预期顺序：`bringing up SDX55` → `modem QMI ready after Ns` → `[sim] card present in physical slot 1`（有卡时）→ `state: opmode=... sim_cards_present=1` → `operating mode set to online`。

**一条命令的自检**（13 项，含"WDS bind 成功、start 到 call 层、TLV 自检"、社区那条成功判据（DMS capabilities 列出 LTE）、`mhi_hwip0` 在位、MM 忽略 `mhi_swip0`）：

```sh
sudo sh /usr/local/lib/kebab-modem/selftest.sh
```

无卡时应输出 **13 passed, 0 failed**（我们实测的结果）；插卡后它会自动切到严格模式（要求 `start result=0`），任何一项 FAIL 都直接指向 runbook §6 的对应行。

**卡出现得慢怎么办**：UIM 在开机后可能要 12–20 s 才报卡（社区脚本最多等 120 s）。`kebab-sim-day` 已经内置：首快照没卡、且存在 `/etc/kebab-modem-sim-expected` 时，它会先等 **20 s** 再看一次（`--wait-sim SEC` 可改；没有该标志则**不等**，保证离线跑得快）。summary 里 `sim_wait:` 与 `sim_slots_at_start:` 会写清楚「等过没等到」还是「根本没等」。详见[数据面文档](sdx55-modemmanager-20261112.md) §4.22。

**意外重启后先看这里**（不是常规步骤，但顺序很重要）：`sudo kebab-modem-status | grep pstore`。本板 ramoops 是配好的（4 MiB `ramoops@b0000000`、`CONFIG_PSTORE_RAM=y`、console 后端开），所以内核 panic 理应留下记录；`records=0` 说明这次重启要么不是内核 panic，要么记录在重启时丢了（详见[数据面文档](sdx55-modemmanager-20261112.md) §4.20）。有记录就先读 `/sys/fs/pstore/*`，**读完再碰 modem**——它可能已经解释了刚才发生的事。

## 2. 卡与 PIN

```sh
qmicli -d qrtr://3 --uim-get-slot-status      # 应出现 Card status: present
qmicli -d qrtr://3 --uim-get-card-status      # Card state / Application state / PIN1 state / Primary GW
```

判定：

| 现象 | 含义 | 处理 |
| --- | --- | --- |
| `Card state: present` + `Primary GW: session exists` | 正常 | 继续 |
| `present` 但 `Primary GW: session doesn't exist` | 未绑定订阅 | `sim-provision.sh` 应已自动激活；没有就手工 `--uim-change-provisioning-session=activate=yes,session-type=primary-gw-provisioning,slot=1,aid=<AID>` |
| `PIN1 state: enabled-not-verified` | 卡还锁着 | 若 `/etc/kebab-sim-pin` 在且 retries==3，重跑 `sim-provision.sh`；否则手工 `--uim-verify-pin=PIN1,<PIN>,session-type=primary-gw-provisioning`，**最多错一次** |
| `PIN1 retries` < 3 | 已错太多次 | **停手**，用 PUK 手工解锁，别再自动试 |
| `no-atr-received` | 卡没被识别 | 重新插卡/换槽；别反复重启 |

> 解析口径已对照 qmicli 1.36.0 源码核实过（不是猜的）：`--uim-get-slot-status` 的槽号在第 3 列（`Physical slot N:`），`--uim-get-slot-status` 卡片状态是 `Card status: present`；`--uim-get-card-status` 里已有会话时打印 **`Primary GW:   slot 'N', application 'M'`**（脚本按 `Primary GW:\s+slot` 判断），AID 打印成 **`Application ID:` 的下一行**（脚本取下一行并去掉空格/冒号），PIN 是 `PIN1 state: 'enabled-not-verified'` + `PIN1 retries: '3'`。

## 3. 注册与能力（MCFG 是否真的生效）

```sh
qmicli -d qrtr://3 --dms-get-operating-mode        # online
qmicli -d qrtr://3 --dms-get-band-capabilities    # 应仍为 18 个 LTE band + NR
qmicli -d qrtr://3 --dms-get-capabilities         # 无卡时已能读到 Networks: cdma20001x, evdo, gsm, umts, lte, 5gnr
qmicli -d qrtr://3 --nas-get-serving-system       # 应从 not-registered-searching 变 registered
```

**无卡时已经能读到的基线**（插卡后这些字段应当只增不减）：`Data Service: 'non-simultaneous-cs-ps'`、`SIM: 'supported'`、`Networks: 'cdma20001x, evdo, gsm, umts, lte, 5gnr'`、`Max TX/RX channel rate: 1800000/3100000`。社区 KB2003 的 `Networks` 是空的（NV 未 provision），本机是满的——**这也是"本机不需要写 NV"的直接证据**。

**判据**：`Registration state: 'registered'` 且 `--nas-get-signal-info` 的 LTE RSSI/RSRP 有值。若仍是 `not-registered-searching` 或 `limited`：先看 `--nas-get-system-info` 的拒绝原因，再确认 §2 的卡状态（MCFG 没激活时就是 never-registered）。

## 4. 数据面（本项目的最终目标）

> 插卡后**先留一份基线**：`sudo python3 /usr/local/lib/kebab-modem/kebab-qmi-wds.py params` 与 `... profile`。现在（无卡）的记录是：默认 profile=3、附着 PDN 列表 `[1]`（profile 1 的 APN 为空）、`attach-params`=error 74 information-unavailable、max 速率 0.2/0.3 Mbps（驻留 GSM）。**注册到 LTE 后 max 速率应跳到几百 Mbps**；若仍是 0.2/0.3，说明根本没上 LTE。详见[数据面文档](sdx55-modemmanager-20261112.md) §4.6。
> **附着起不来时的已验证杠杆（无卡演练过）**：如果 `data-up` 反复 `error 14`/`call-failed`，而 `uim-get-slot-status` 是 `present`、`nas-get-serving-system` 已经 registered，那就试 `sudo python3 /usr/local/lib/kebab-modem/kebab-qmi-wds.py attach-set --list 3 --apply`（把附着 PDN 从空 APN 的 profile 1 换到 CMNET profile 3），可能要让 modem 重新附着（重启 `kebab-sdx55-modem` 或整机）。**改完务必记录 `params` 前后两份输出**；回到基线是 `attach-set --list 1 --apply`。机制与判据见[数据面文档](sdx55-modemmanager-20261112.md) §4.11。

**第 0 步：确认已经注册**（2026-11-13 新增，见[数据面文档](sdx55-modemmanager-20261112.md) §4.21）。WDS Start 需要一个已附着的 modem；开机后它还在搜网时 `start` 会报 **error 14 / call-end 3 no-service**，看起来跟 APN 错一模一样。先看：

```sh
qmicli -d qrtr://3 --nas-get-serving-system | grep -i 'registration state'
#   要看到 registered 或 registered-roaming 再往下走
```

`kebab-sim-day` 已经内置这一步（默认最多等 **60 s**，`--wait-reg SEC` 可调；超时也会继续跑，但 `summary.txt` 的 `serving_before_data:` 与 `next_steps:` 会直接说明是「没注册」而不是「APN 错」）。手动 `data-up` 时请自己先确认。

```sh
sudo python3 /usr/local/lib/kebab-modem/kebab-qmi-wds.py up --profile 3
```

预期输出：

```
bind   : result=0 error=0 (none)
start  : result=0 error=0 (none) profile=3
handle : <非 0>
settings: result=0 error=0 (none)
  tlv 0x.. ...
```

- **profile 3 = CMNET**，是从固件自己的 profile 表读出来的（`--wds-get-profile-list=3gpp`：1=fota/ia、2=ims、3=CMNET(default/supl/hipri/ut)、4=CMWAP(mms)、5=SOS）。也可以 `--apn <APN>` 覆盖。
- `start` 失败时的 error 对照：**14 = CALL_FAILED**（呼叫被拒；看 call-end-reason：3=no-service 表示还没注册，10=authentication-failed 表示 APN 鉴权问题，7=access-failure）；**70 = INVALID_OPERATION**（没先 bind，或端点类型/接口号不对）；**52 = DEVICE_NOT_READY**（modem 没 online）。
- 工具已把 bind→start→settings→配接口合成一条命令（同一 socket，进程活着呼叫才在）：

```sh
sudo python3 /usr/local/lib/kebab-modem/kebab-qmi-wds.py data-up --profile 3
#   bind/start/settings 全绿后自动：addr replace <ip>/<prefix> → 设 MTU → ip link up →（--route 时）route replace default via <gw>
#   之后进入“holding”循环（每 5 s 一行），Ctrl-C 或 SIGTERM 时自动 stop 呼叫、删地址/路由、接口置 down
```

先干跑看它准备配什么（不落地）：`data-up --profile 3 --dry-run`；只想跑固定时长：`--hold-seconds 60`。用别的接口名：`--ifname <if>`。

**注意**：QRTR 里"客户端就是 socket"，所以 **data-up 进程必须活着**，呼叫才保持；把它放进 `systemd-run --unit=kebab-data` 或用 tmux/nohup 挂着即可，退出即断线（这也是一条干净的停止方式）。

想让它常驻（开机或手动拉起后一直保持，掉线 30 s 重试）：把仓库里的 `config/kebab-sdx55-data.service` 装到 `/etc/systemd/system/`，然后 `systemctl enable --now kebab-sdx55-data`。这个单元**默认不装也不 enable**——没卡时 `start` 会一直 error 14，单元只会反复重启刷日志。**实测过一遍**（2026-11-12，无卡）：装上后 `systemctl start` → 退出码 3、`Failed with result 'exit-code'`、**30 s 后自动重试**（restart counter 1）、两次失败后 `mhi_hwip0` 仍是 `DOWN` / 无地址 / MTU 16384，失败没有留下任何残留；测完已把单元移除、`daemon-reload`。所以判断标准很简单：**先手动 `data-up` 能 hold 住，再装这个单元**。`systemctl stop` 会走 SIGTERM → 自动 stop 呼叫 + 删地址 + 接口 down。

**DNS 不自动改**：`settings` 里会打印 `dns1_str`/`dns2_str`，但 `data-up` **不碰系统 DNS**（这台机器默认 DNS 由 systemd-resolved 管、且当前解析走的是不通的 USB 路由器 DNS）。要按名字测试就显式指定，例如 `curl --resolve example.com:443:<ip> https://example.com/`，或临时 `resolvectl dns <ifindex> <dns1>`（改完记得还原）。运营商 DNS 是否可用，等真卡通了再验。

数据"通了一部分但 ping 不通"时的第一件事（比重启整机便宜得多）：

```sh
sudo kebab-modem-mhi-diag --samples 5 --interval 2
#   traffic-flowing / idle / bearer-down / tx-only-suspected-wedge
```

判定与处置见[数据面文档](sdx55-modemmanager-20261112.md) §4.2。

**raw-IP 的已知风险**：这个内核没有 QMAP/mux，`mhi_net` 只做 raw-IP；社区记录过"传输到 ~10 MB 后下行 wedged"。判据是 ping 突然全丢、`ip -s link` 的 RX 不再增长但 TX 还在增；出现就重跑 `data-up`，别整机重启。**先证明能 ping 通再谈速度。**

收尾：

```sh
sudo python3 /usr/local/lib/kebab-modem/kebab-qmi-wds.py stop --handle <N>
sudo ip addr flush dev mhi_hwip0 && sudo ip link set mhi_hwip0 down
```

> 数据起来之后立刻复查一次格式：`sudo python3 /usr/local/lib/kebab-modem/kebab-qmi-wds.py format` 应显示 `link-layer: raw-ip` 且两个 `aggregation: disabled`。若显示 qmap，说明有东西动过格式（我们的内核没有 QMAP 支持，那时 RX 会是乱包），先切回 raw-ip 再继续。详见[数据面文档](sdx55-modemmanager-20261112.md) §4.8。

## 4.4 summary.txt 怎么读

`kebab-sim-day` 打完证据包后，`$OUT/summary.txt` 里除了 `verdict/reason`，还有三行是给"下一步"用的：

- `call_end:` —— data-up 日志里的呼叫结束原因（如 `3 = no-service (generic)`），配合 §4.1 的表分流；
- `diag:` —— `mhi-diag` 的四态结论（`traffic-flowing` / `tx-only-suspected-wedge` / `idle` / `bearer-down`）；
- `next_steps:` —— **一句话命令级的建议**，直接抄进终端。分支映射见[数据面文档](sdx55-modemmanager-20261112.md) §4.12；
- `sim_wait:` / `sim_slots_at_start:` —— 有没有为「慢卡」等过、等的时候看到几个槽位（`0s` = 没等）；
- `tcp:` / `pmtu:` —— **一条完成的 TCP 握手**（`ok 1.1.1.1:443`）与**按接口 MTU 的 DF ping**（`ok 1500`）。TCP 是三项证据里最强的一条（有状态）；`pmtu: unknown` 只表示网关卡 ICMP，不是故障；MTU 通告错时的典型形态是「DNS 通、小请求过、大响应卡」——三者并列才能指到分片而不是 APN（[数据面文档](sdx55-modemmanager-20261112.md) §4.26）；
- `dns:` —— 一次绑在承载口上的 DNS 查询（`ok via <server>`）；用它和 `ping:` 一起看：`ping: no dns: yes` 说明只是 ICMP 被过滤，数据是通的；
- `samples_every:` / `sample_trend:` —— 按住期间每 N 秒采一次 `mhi-diag`，这里给出「第一个状态 -> 最后一个状态、变了几次、rx/tx 速率」。**先通后 wedge 会体现为一次状态变化**，一次性快照看不出来（[数据面文档](sdx55-modemmanager-20261112.md) §4.24）；
- **`manifest-check.txt` / `SHA256SUMS`** —— 证据包在收尾时**自己验过一遍**（`manifest check: ok (N files …)`）。出包后在任何机器上 `cd <目录> && sha256sum -c SHA256SUMS` 应全 OK；**若这里不是 ok，先别信包里的任何结论**——被截断的 `probes.txt` 读起来和「没有数据」一模一样（[数据面文档](sdx55-modemmanager-20261112.md) §4.34）；
- **同一时间只能跑一个**：`sim-day` 用 `/run/kebab-sim-day.lock` 做单实例锁，第二个实例直接 `rc=3` 并打印持有者 `pid … since …`（两次运行会抢 `mhi_hwip0` 与那条 WDS 呼叫，产出的证据互相矛盾）。如果提示里的 pid 已经是僵尸：`sudo rm -f /run/kebab-sim-day.lock`；
- `default_routes: usb0/100 wlan0/600` / `bearer_default_route: no` —— 收尾时的默认路由快照（原文在 `routes.txt`）。**`bearer_default_route` 应当是 `no`**：`sim-day` 从不装默认路由；变成 `yes` 说明有别人动了出口（手跑的 `--route`，或被启用的 MM multiplex）。**给数据面加默认路由一定要带 metric**（`data-up --route` 现在默认 `--route-metric 1000`，比上联大 ⇒ 只当备份；`0` 会被拒绝，见[数据面文档](sdx55-modemmanager-20261112.md) §4.38）；
- `bearer_lost: yes|no` / `baseline_mtu: <n>` —— 「先通后坏」的判定结果、以及收尾会把接口 MTU 还原到哪个值（驱动默认 16384；若日志出现 `baseline: … looks like residue`，说明上一次运行没收拾干净，本次会替你改回默认）。**`bearer_lost: yes` 时 `next_steps` 会直接指向 `samples.tsv`/`wedge_dump` 与 QMAP，不要再去改 APN**；
- `held: <n>s` / `probes_at_start: …` —— 实际按住了多久、刚起来时通不通。**结束时的 `ping: no` 加上 `probes_at_start: ping=yes` 就是「先通后坏」**（`reason:` 会写 `BEARER LOST during the hold`），此时该走 §4.5 的 QMAP 路线，而不是去改 APN；
- `residue_after_stop:` —— 停呼叫之后承载口上是否还有我们留下的地址。正常收尾是 `none`；若 `data-up` 被 `SIGKILL`（卡在 QMI 读里）则显示 `removed <地址>`，说明**兜底守卫替你清了**，不要让「零残留」的判断变成假话（[数据面文档](sdx55-modemmanager-20261112.md) §4.32）；
- `modem_gone:` / `modem_gone_at:` —— 按住期间承载口是否从 sysfs 消失（`yes` 时附「第几秒」，相对第一个样本）。**这是与 wedge 不同的故障**：死后每行样本都只是普通的 `bearer-down`，光看表格看不出何时死的；此时 `next_steps` 只给一条建议 —— **reboot**（host 侧救不回），现场在 `modemdied-*.txt`（含 dmesg 末 30 行），见[数据面文档](sdx55-modemmanager-20261112.md) §4.30；
- `wedge_dump:` —— 第一次看见 wedge 时自动冻结的现场文件（`wedge-<时间戳>.txt`：MHI 通道 rp/wp、单帧判定、`ip -s link`、dmesg 末尾）；`none` 表示没触发。**卡住那一刻的环指针**比收尾时更有价值，因为环可能已经动过、modem 可能已经 reset（[数据面文档](sdx55-modemmanager-20261112.md) §4.27）；
- `wedge_seen:` —— 采样期间是否出现过 `tx-only-suspected-wedge`；是 `yes` 时即使 `verdict: PASS`，`reason:` 也会附上 `WEDGE SEEN during the hold`，不要当成全绿；
- `nas:` —— 起承载那一刻的 RF 上下文：`rat=gsm plmn=460/0 state=limited sig=RSSI=-95 dBm`。**`rat=` 不是 `lte` 就说明还没上 LTE**（这时 WDS 报 error 14 与 APN 无关），原始 qmicli 输出在证据目录的 `nas.txt`（[数据面文档](sdx55-modemmanager-20261112.md) §4.25）；
- `serving_before_data:` —— 拉起承载**之前**那一刻的注册状态（`registered` / `not-registered-searching` / …）。它是「error 14 到底是没注册还是 APN 错」的分水岭：这里不是 `registered*` 而 `next_steps` 却说改 APN，说明脚本判断有 bug。

分支逻辑本身有离线测试：`sudo sh /usr/local/lib/kebab-modem/test-sim-day.sh`（无卡也能跑，**81 项断言**：`derive_next` 8 项、QMAP 闸门 5 项、`registration_ok` 5 项、`sim_slots_from_status` 4 项、`wait_sim_default` 1 项、`bearer_ok` 4 项、`sample_trend` 4 项、NAS 解析 6 项、`wedge_dump` 5 项、`probe_bearer` 2 项、`gw_candidate` 3 项、`elapsed_since`/`first_epoch` 6 项、modem 消失 2 项、`residue_cleanup` 3 项、locale 1 项、链路名回读 2 项）。

## 4.5 如果数据起来后 wedge（raw-IP 的已知故障形态）

现象（Apollo 同族设备实测过、我们的驱动与环分析也对得上）：**能 ping 通一小会儿、传了约 10 MB 后彻底不动**，`ip -s link` 的 TX 还在涨或停住，RX 不涨。这时先分诊，不要急着换方案：

```
sudo kebab-modem-mhi-diag --samples 3 --interval 2
```

- `tx-only-suspected-wedge` 且 `ul_pending` **逼近 128 TREs、`device_consumed` 不涨** ⇒ **调制解调器不再取 UL 环**，是 modem 侧停摆，不是内核发包问题（AP→环已由 `kebab-modem-tx-probe` 证明）；
- `dl` 的 rp 还在动但 `ip -s link` 的 RX 不涨 ⇒ 收包侧问题（封装不匹配之类）；
- `bearer-down` ⇒ 呼叫早就掉了，先看 `data-up` 日志里的 `call-end-reason`。

**确认是 modem 停取 UL 后，按顺序试**：

```
# 0) 先确认这条内核 + 这份 libqmi 能建 rmnet 链路（自动加载/卸载模块，不碰基带）
sudo kebab-modem-qmap linktest          # 期望 4 个 PASS + "result: PASS"
# 1) 切 QMAP（固件侧）
sudo python3 /usr/local/lib/kebab-modem/kebab-qmi-wds.py format-set --ul qmap --dl qmap --apply
# 2) 加载 rmnet 并建链路（mux 必须与 data-up 的 --mux 一致）
sudo kebab-modem-qmap load
sudo kebab-modem-qmap up --mux 1 --mtu 1500
#    ↑ 输出 `link up: <名字> over mhi_hwip0 ...`：名字不保证是 rmnet0
#    （ModemManager 的承载也叫 rmnet0，撞名时这里会是 rmnet1…）
_if=$(sudo kebab-modem-qmap status | sed -n "s/.*state *: .*LINK=\([^ ]*\).*/\1/p" | head -1)
# 3) 用那个名字承载
sudo python3 /usr/local/lib/kebab-modem/kebab-qmi-wds.py data-up --mux 1 --ifname "${_if:-rmnet0}"
```

**不要开 `ingress-commands`**（`qmap.sh up --commands` 也不要）：它让内核处理 MAP 控制帧并回 ACK，但这条路径在本机**没有验证过**，唯一一次实验以硬重启收场（MM 文档 §4.20）。固件当前自报 `flow-control = 0 (disabled)`，默认 `off` 就是正确姿态；`qmap.sh` 的默认值已是 `off`。

`linktest` 走的是 ModemManager 用的那条 libqmi 路径（`qmi_device_add_link_with_flags`），它会顺带告诉你内核收到的 flags：`flags=0` 应该是 `<INGRESS_DEAGGREGATION>`，`flags=6` 应该多出 `INGRESS_MAP_CKSUMV4,EGRESS_MAP_CKSUMV4`（详见 MM 文档 §4.17）。注意链路名是**前缀+序号**不是前缀+mux，MTU 从父口继承（16384→16380），真正的 MTU 要设在链路上。

回滚：`data-up` 退出后 `sudo kebab-modem-qmap down; sudo kebab-modem-qmap unload`（`down` 只删 state 文件里记的那条链路；**没有 state 时会拒绝猜**，见 MM 文档 §4.19——它不会去删 ModemManager 的 `rmnet0`），再把格式切回 `--ul disabled --dl disabled --apply`（`kebab-modem-qmap format raw-ip` 一行也行，需加 `--apply`）。

**同一件事已封装成一条命令**（2026-11-13 实测）：

```
sudo kebab-sim-day --qmap --hold 120
```

`--qmap` 会自己做完 `format-set qmap` → `qmap load` → `rmnet0 over <iface> mux 1` → `data-up --ifname rmnet0 --mux N`，并把每一步写进 `qmap.log`；**退出时无论成功失败（含 Ctrl-C / SIGTERM，SIGTERM 已用一个慢速副本实测）都会还原**：格式切回 raw-ip/disabled、删 `rmnet0`、卸 `rmnet`，还原结果落 `qmap-after.txt`，`summary.txt` 多一行 `qmap: armed: rmnet0 over mhi_hwip0 mux 1`（或 `setup failed rc=N`）。`--mux N` 可改 mux id（默认 1，必须与 `data-up` 那个一致——脚本内部已保证）。无卡实测：`format` 读回 `ul=5/dl=5`、`rmnet0 … mtu 1500`、`start_error=14`，退出后 `link-layer raw-ip / ul-disabled / dl-disabled`、无 `rmnet0`、无 `rmnet`、`SELFTEST PASS`。

**注意**：`--qmap` 是**失败之后的降级路径**，不是默认路径。raw-IP 能通就别开它——切了 QMAP 而 `data-up` 里忘了 `--mux`，包会被当成 mux 0 的 QMAP 帧发出去，两端都对不上。
**记录要点**（写进当天的证据包）：切之前/之后的 `format` 输出、`mhi-diag --json`、`ip -s link show rmnet0`、`ping -I rmnet0` 结果。若 QMAP 仍然 wedge，那说明问题不在封装层，下一站是 `mhi_net` 的 RX 聚合与 modem 固件版本。

## 5. ModemManager（可选，数据能通再做）

MM 的作用是自动重连/拨号/给 NetworkManager 供 bearer；本项目**不依赖它**也能验证数据。要做就按顺序：

```sh
# 1) 规则必须先于 MM 生效（EFS 被 AT 探测会 ERRFATAL）
sudo install -m 644 config/mm/77-mm-ignore-sdx55-efs.rules /etc/udev/rules.d/
sudo install -m 644 config/mm/77-mm-sdx55-fusion.rules    /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger

# 2) 包：本机没有可用的公网出口（默认路由/DNS 都指向 USB 路由器，实测不通），所以**不要**直接 apt-get；
#    四个 .deb 已备在 /srv/modem-20261112/mm-debs/（离线装，依赖已核过，dpkg --no-act 无报错）
sudo dpkg -i /srv/modem-20261112/mm-debs/*.deb
#    真要在线装：wlan0 出口是通的，但 DNS 被 usb0 的 ~. 抢走，需要
#    curl --interface wlan0 --resolve deb.debian.org:443:151.101.2.132 -O <deb url> 逐个下

# 3) 单元依赖（注意 Debian 的单元名是大写 ModemManager.service）
sudo install -d /etc/systemd/system/ModemManager.service.d
sudo install -m 644 config/mm/zz-kebab-mm.conf /etc/systemd/system/ModemManager.service.d/
# 单元 + 它 ExecStart 指向的助手脚本，两个都要装（只装 .service 会得到一个永远起不来的单元）
sudo install -m 644 config/mm/kebab-mm-restart-once.service /etc/systemd/system/
sudo install -d /usr/local/lib/kebab-mm
sudo install -m 755 config/mm/kebab-mm-restart-once.sh /usr/local/lib/kebab-mm/
sudo systemctl daemon-reload
sudo systemctl enable --now ModemManager

# 4) 验收
mmcli -L
mmcli -m any --signal-get 2>/dev/null | head -20
```

细节与已知限制见 `config/mm/README.md` 与[数据面与 MM 可行性](sdx55-modemmanager-20261112.md) §3；`kebab-mm-restart-once` 是给 DKMS/驱动晚到时重拉 MM 用的一次性助手，不需要就别 enable。

- **规则必须先于 MM**：EFS 端口被 AT 探测会 ERRFATAL（11/10 已踩过一次）。
- 两条 Debian 补丁（Apollo 的）**本轮未安装**；先不给 MM 打补丁也能看到 modem，只是 fusion 后的数据路径识别可能不完整。详见[数据面与 MM 可行性](sdx55-modemmanager-20261112.md) §3。
- 回退：`sudo systemctl disable --now ModemManager`、删除三条规则、`udevadm control --reload-rules`；modem 本身不受影响。

**QMAP 绝不能在没有卡/没有注册时武装**（2026-11-13 实测教训）：无卡跑 `--qmap` 时 WDS start 不回包（`timeout waiting for msgid 0x0020`），约 30 s 后基带 `Device died`，MHI 自恢复失败——`pcieport: retraining failed` → `Recovery failed: -25`，modem 从 PCI 上消失、node 3 没了、GNSS 一起死。**host 侧 remove/rescan 与控制器 unbind 都救不回，只能 reboot**。现在 `kebab-sim-day --qmap` 有**三道门**（内核 tun 预检 / 必须有卡 / 必须已注册：NAS `serving` 得是 `registered*`），任一不过就只跳过 QMAP、完全不碰 modem；`summary.txt` 多两行 `qmap_selftest:` 与 `modem_alive:`。有卡但还没注册时 Start 同样会失败，而这次故障正是「Start 失败 + qmap 格式」造成的，所以没注册也不许切。顺带记牢插卡当天的顺序：**先 raw-IP 打通并 ping 通，只有在证实「能通但会 wedge」时才切 QMAP**。详见 MM 文档 §4.15。
**插卡前想先确认 QMAP 的内核侧没坏**：`sudo python3 /usr/local/lib/kebab-modem/qmap-local-test.py`（默认 `both`，约 10 秒）。`tun: PASS` 是**双向闭环**——写一个 MAP 封装的 ICMP 进 `/dev/net/tun`，内核经 rmnet 解封装、投递、应答、再封装回来，断言 mux/长度/校验和/地址互换/ICMP reply/载荷标记；`veth: PASS` 是出方向的字节级对照（MAP 头 4 字段、MTU 1496）。用完整链路、自动删设备卸模块，不碰 `mhi_hwip0`。它**不**验证 modem 侧，详见 MM 文档 §4.14。
**两条已经装好的前提**（2026-11-13，详见 MM 文档 §5.10/§5.11）：

* `77-mm-sdx55-swip-ignore.rules` 把 `mhi_swip0` 标成 `ID_MM_PORT_IGNORE`——否则 MM 可能把承载建到那个不承载流量的口上（`mmcli` 里现在是 `mhi_hwip0 (net), mhi_swip0 (ignored)`）；
* QMAP multiplexing 的 drop-in 在 `/usr/local/lib/kebab-mm/dropins/zzz-kebab-mm-multiplex.conf`，**默认不装**。只有 raw-IP 真 wedge 了才 `kebab-modem-qmap load` 再把它装进 `/etc/systemd/system/ModemManager.service.d/`，然后 `daemon-reload` + 重启 MM。
## 6. 失败分支速查

| 症状 | 最可能原因 | 下一步 |
| --- | --- | --- |
| `Card status: absent` | 卡没插好/槽选错 | 重插；`--uim-get-slot-status` 看两个槽 |
| 卡 present 但 `Primary GW: session doesn't exist` | 固件自动 provisioning 缺失 | 手工激活 primary-gw 会话 |
| PIN1 `enabled-not-verified` 且已错过 MCFG | 卡在开机后才就绪 | 重启（MCFG 不重跑） |
| `Networks:` 空 / band 空 | MCFG 未激活 | 先解决卡/PIN，再重启 |
| `start` error 14 + `verbose cm/acl-failure` | 访问控制/订阅检查失败（无卡，或卡在但订阅没绑上） | §2 的槽状态与 provisioning 会话 |
| `start` error 14 + `cm/no-pdp-context-activated`、`esm-*`、`pdp-activate-max-retry-failed` | 网络拒绝激活 PDP | APN/profile（3=CMNET），换 `--apn` 或 profile |
| `start` error 14 + `generic/authentication-failed` | 该 APN 需要鉴权 | `--auth pap|chap` + `--auth-user/--auth-pass` |
| `start` error 14 + `generic/no-service` | 没注册/无服务 | 看 §3 |
| `start` error 70 | 没 bind 或端点参数错 | 用 `up`（同一 socket bind→start） |
| handle 拿到但 settings 空 | 呼叫建立中/失败 | 隔 2 s 再 `settings`；仍空看 call-end |
| ping 全丢、RX 不增长 | raw-IP wedge | stop 后重新 up，必要时重启 |
| `mhi1` 消失 | MHI 掉线 | **不要重载模块**（QRTR 端点不会回来），只能整机重启；健康巡检会记 `action=modem-gone` |

`data-up`/`up` 失败时会把原因解成名字，例如无卡时的真实输出：

```
call-end-reason: 3 = no-service (generic)
verbose-call-end: type=3 (cm) reason=1042 (acl-failure)
```

`type=3` 是 **CM（呼叫管理）**，`1042 = acl-failure` 是访问控制/订阅检查失败——看到它就是"卡/订阅"问题，而不是 APN 或射频。完整原因→动作对照表在 [数据面文档](sdx55-modemmanager-20261112.md) §4.4，表本身来自 `config/libqmi/wds-call-end-reasons.json`（缺该文件时退回裸数字）。

> 插卡前可以先测一次“我们这半条路”：`sudo kebab-modem-tx-probe --apply`（临时给 `mhi_hwip0` 加地址、ping 一个必然丢包的邻居，然后还原）。实测 7 包 ↔ UL 环 +112 B（7 个 16 B TRE），AP 侧 TX 路径已证明可用；无卡时 `ul_pending` 会一路涨到 128 TRE 满环，属正常。详见[数据面文档](sdx55-modemmanager-20261112.md) §4.7。

## 7. 不要做的事

- 不要写 NV/EFS/persist、不要 PDC 激活、不要改分区或刷机：本机 band/NV 已正常（见执行记录 §3）。
- 不要"先 offline 再 online"：实测会 `QMI error (60) InvalidTransition`，只能重启。
- 不要反复重启：每次重启都会重新跑 MCFG，卡状态没改时只是重复浪费。
- 不要自动重试 PIN：唯一的安全阀是 `retries==3`，一旦不是 3 就人工处理。
- 不要把 PIN/ICCID/IMEI/IMSI 写进日志或文档；`sim-provision.sh` 已对 PIN 做 `<pin>` 掩码。

## 8. 验收口径

**"主机 LTE 数据可用"** = 同时满足：
1. `--uim-get-card-status`：`Card state: present`、`Application state: ready`、PIN1 已通过；
2. `--nas-get-serving-system`：`registered`，且有 LTE 信号读数；
3. `kebab-qmi-wds.py up --profile 3`：start 成功、`handle` 非 0、`settings` 返回 IPv4 参数；
4. `mhi_hwip0` 配好地址后能 `ping` 通网关与公网 IP，并能解析/访问一个真实 URL（例如 `curl -sS --interface mhi_hwip0 https://example.com -o /dev/null`）。

只做到 1–3 不算；只证明"能注册"也不算。**不得把主机数据说成"Android Telephony 可用"**，SMS/5G/IMS/VoLTE/语音/音频是另外的目标，本轮不涉及。

## 9. 当前基线（无卡，2026-11-12 17:59Z）

- `kebab-sdx55-modem` active，QMI 9 s 就绪、opmode online、GNSS 23–27 颗可见；
- band 18 个 LTE + NR 5 个；`--dms-get-capabilities` = `cdma20001x, evdo, gsm, umts, lte, 5gnr`；
- WDS bind 成功、start = CALL_FAILED(no-service)、settings = OUT_OF_CALL；profile 3 = CMNET；
- soak2（2 h）进行中，前 48 样本：mhi1/online/node 100%，errfatal 0，卫星 23–27；
- 电池 96%、温度 34.6–41.1 °C、`dmesg` errfatal/device died 0 次。
