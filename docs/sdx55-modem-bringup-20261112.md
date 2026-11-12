# SDX55 基带适配执行记录（2026-11-12）

> 状态：**已执行**。模式 A（免刷）：无刷写、无分区写、无 NV/EFS 分区写、无 GPT 变更。共 4 次正常重启（均为验证开机自启链）。
> 结论先行：**modem 现在每次开机自动起来并处于 online，EFS 同步正常，LTE band 能力从 'none' 变成完整列表，GNSS 重新可用；蜂窝数据仍被"无 SIM 卡"这一物理前提阻塞（modem 自报两个物理槽 Card status: absent）。**
> 前置计划：[PLAN 交付](sdx55-plan-20261112.md)。本轮按该计划的 A1/A2 推进，并把 A2 的成果固化成开机单元。

## 1. 本轮新增/修改（设备侧，全部可回退）

| 路径 | 作用 |
| --- | --- |
| `/etc/systemd/system/kebab-sdx55-modem.service` | oneshot + RemainAfterExit，开机拉起 SDX55；ExecStop 反向卸载 |
| `/usr/local/lib/kebab-modem/bringup.sh` | 顺序：RFS/PM 服务 → 4 个 modprobe → EFS 守护 → 等 QMI 真通话 → 决定 DMS 模式 |
| `/usr/local/lib/kebab-modem/teardown.sh` | 停 EFS → 卸载 3 个模块 → 停 RFS/PM |
| `/usr/local/sbin/kebab-modem-status` | 只读状态摘要（模块/端口/node/opmode/band/SIM/注册/GNSS/**数据面**：addr/link/mtu/rx-tx、data.service、SIM-day 标志） |
| `/etc/default/kebab-sdx55` | `KEBAB_SDX55_ONLINE=auto`（默认 online；见 §4 的实测理由） |
| `/etc/systemd/system/kebab-sdx55-health.service` + `.timer` | 每 5 分钟巡检一次：发布 `/run/kebab-sdx55-health.json`；opmode 掉出 online 就重设（GNSS 依赖它）；`mhi1` 消失只告警、**不重启**（模块重载救不回 QRTR） |
| `/usr/local/lib/kebab-modem/health.sh` | 上述巡检脚本（单次执行，可手动跑） |
| `/usr/local/lib/kebab-modem/sim-provision.sh` | 插卡时用：激活 primary-gw 会话 + 受保护地验证 PIN1（**必须在 MCFG 的 15/24 s 窗口之前**）；无卡时 <1 s 退出。bring-up 在置 online 前后各调一次 |
| `/usr/local/lib/kebab-modem/kebab-qmi-wds.py` | 裸 QMI/WDS 客户端（QRTR socket 直连，复刻 Apollo 的 `wds_test`）：同一客户端 bind→start→settings；`info/dms-manufacturer/bind/start/settings/status/up/stop`，另有 `data-up`（自动配 `mhi_hwip0` 并保持呼叫）、`data-down`、`selftest`。无卡时 bind 成功、start=error 14 |
| `/usr/local/lib/kebab-modem/sim-preflight.sh` | 插卡**之前**过的 14 项只读检查（开机链/支撑 unit/ONLINE=auto/PIN 权限/工具/MM deb 校验和/当前 boot 是否已见卡），无卡实测 `14 passed, 0 warnings, 0 failed` |
| `/usr/local/lib/kebab-modem/selftest.sh` | 一条命令自检整条栈（单元/mhi1/QRTR/opmode/band/DMS/WDS bind/start/TLV/**DMS capabilities 的 RAT 列表**/**mhi_hwip0 存在**/**MM 是否忽略 mhi_swip0**/健康 JSON），**无卡实测 13 passed 0 failed**；插卡后自动切严格模式（要求 start 成功） |
| `/usr/local/lib/kebab-modem/mhi-diag.sh`（+ `/usr/local/sbin/kebab-modem-mhi-diag` 别名） | 数据面 wedge 诊断：采样两次 `IP_HW0(100/101)` 的 rp/wp、`states`、接口 rx/tx，给出 `traffic-flowing / tx-only-suspected-wedge / idle / bearer-down`；`--json` 一行输出 |
| `/usr/local/lib/kebab-modem/wds-call-end-reasons.json` | 呼叫失败原因表（由 `scripts/linux/gen-wds-call-end-reasons.py` 从 libqmi `qmi-enums-wds.h` 生成，16.7 KB）：`call_end` 4 组 + `verbose_type` 7 种 + `verbose` 7 张子表，`data-up`/`up` 失败时把 `error 14` 解成 `type=3 (cm) reason=1042 (acl-failure)` 这样的可读结论 |
| `/etc/udev/rules.d/77-mm-ignore-sdx55-efs.rules` | ModemManager 前置规则：`wwan0efs0` 打 `ID_MM_PORT_IGNORE`，避免 MM 的 AT 探测触发固件 ERRFATAL（11/10 踩过） |
| `/etc/udev/rules.d/77-mm-sdx55-fusion.rules` | 把 SDX55 的 wwan 端口与 `mhi_hwip*`/`mhi_swip*` net 端口都打上 `ID_MM_QCOM_SOC=1` + `ID_MM_PHYSDEV_UID=qcom-soc`，让 QRTR 控制面与 mhi_net 数据面合并成同一个 MM 设备；同时 ignore 死掉的 `*qmi*` 端口 |
| `/etc/systemd/system/ModemManager.service.d/zz-kebab-mm.conf` | 让 `ModemManager.service` 排在 `kebab-sdx55-modem.service` 之后 |
| `/etc/udev/rules.d/77-mm-sdx55-swip-ignore.rules` | 把闲置的 `mhi_swip0` 标成 `ID_MM_PORT_IGNORE`：MM 的数据口是“第一个未连接的 NET 口”，两个口时等于掷骰子，只有 `mhi_hwip0` 承载流量（实测 DEBUG 日志 `net/mhi_hwip0: net (data)`） |
| `/usr/local/lib/kebab-mm/dropins/zzz-kebab-mm-multiplex.conf` | 可选 drop-in（**默认不生效**，放在 `/etc` 之外）：给 MM 加 `--test-multiplex-requested`，即社区的 `zz-apollo-multiplex.conf`；启用前必须先 `kebab-modem-qmap load` |
| `/usr/local/lib/kebab-modem/qmap-local-test.py` | 本地 QMAP 验证（不需要卡）：`tun` 模式是**双向闭环**（MAP 封装进 `/dev/net/tun` → rmnet 解封装 → 内核应答 → 再封装回来，断言 mux/长度/校验和/地址互换/ICMP reply），`veth` 模式做出方向字节对照（4 字节 MAP 头、MTU 1496）；结束删设备卸模块 |
| `/usr/local/lib/kebab-mm/build-modemmanager-kebab.sh` | 补丁版 ModemManager 的 `--check`/`--build`/`--revert` 三态脚本（构建、装到 `/usr/local/lib/kebab-mm/`、用 drop-in 覆盖 `ExecStart`；dpkg 文件不动） |
| `/usr/local/lib/kebab-mm/fetch-debs-kebab.py` | 无 DNS/无 apt 环境下的 .deb 解析与获取（读 `Packages.xz`、按 Depends 递归、`--list` 只打印计划、`--apply` 才下载安装） |
| `/usr/local/lib/kebab-mm/patches/mm/0001-qcom-soc-accept-mhi-net-data-ports.patch` | qcom-soc 插件补上 `mhi_net` 数据端口分支（上游通用 QMI 类早已支持，只有这个插件漏了） |
| `/usr/local/lib/kebab-mm/patches/mm/0002-bearer-qmi-bind-pcie-endpoint.patch` | PCIe 端点在未复用的情况下也先 bind，否则 `WDS Start Network` 报 `error 70` |
| `/usr/local/lib/kebab-mm/patches/mm/README.md` | 两条补丁的来源、适用性验证、构建与回退说明 |
| `/usr/local/lib/kebab-modem/test-sim-day.sh` | `kebab-sim-day` 的 `next_steps` 决策逻辑离线测试（源码级 `SIMDAY_LIB_ONLY=1` 后调纯函数 `derive_next`），6 项断言，无卡可跑 |
| `/usr/local/lib/kebab-modem/rmnet/rmnet.ko`（+ `/usr/local/sbin/kebab-modem-qmap` → `qmap.sh`） | 外部构建的 `rmnet` 模块（1,767,968 B，SHA `d8917895…`，vermagic 与运行内核一致）+ QMAP 工具箱：`status/load/unload/up/down/format`。实测 `insmod` 成功、`rmnet0@mhi_hwip0 mux_id 1` 可建可删，**QMAP 不再需要换内核** |
| `/usr/local/lib/kebab-modem/qmap.sh` | 见上一行（与 `rmnet.ko` 同一行的设备侧入口，单独列出以便核对 SHA） |
| `/usr/local/lib/kebab-modem/qcdm.py`（+ `/usr/local/sbin/kebab-modem-qcdm`） | DIAG 通道探针：`verno` 复现回显（rc=2 echo / 3 静默 / 0 有非回显数据）、`capture --out` 存原始字节。实测本固件回"整帧回显"、无 diag 应答、无自发日志 ⇒ 日志抓取暂不可用（见 MM 文档 §4.9） |
| `/usr/local/lib/kebab-modem/qmi-messages.json` | QMI 消息名表（`scripts/linux/gen-qmi-message-names.py` 从 libqmi `data/*.json` 生成 WDS 39 / WDA 6 / DMS 60 条，3,309 B）：把 Get Supported Messages 位图翻成名字；本固件该消息返回 error 71，工具会解释而不是瞎猜 |
| `/usr/local/lib/kebab-modem/tx-probe.sh`（+ `/usr/local/sbin/kebab-modem-tx-probe`） | 证明 AP 侧 TX 路径：默认 dry-run，`--apply` 才临时给 `mhi_hwip0` 加 /30 并 ping，测完必还原（地址/link/MTU）；判据是 tx 包数增量与 UL 环 wp 的 16 字节 TRE 增量对得上（实测 7 包 ↔ +112 B） |
| `/usr/local/lib/kebab-modem/qmi-errors.json` | QMI 协议错误名表（`scripts/linux/gen-qmi-error-names.py` 从 libqmi `qmi-errors.h` 生成，3,760 B，125 条）：`error 74` 打印为 `information-unavailable` 而不是裸数字 |
| `/usr/local/lib/kebab-modem/sim-day.sh`（+ `/usr/local/sbin/kebab-sim-day`） | **插卡当天一条命令**：preflight → 前后 status → `data-up` held → ping/diag/计数 → dmesg → 自动停呼叫 → `summary.txt`+`SHA256SUMS` 证据包；无卡时 `verdict: FAIL`（reason 指出无卡）并退出 1 |
| `config/kebab-sdx55-data.service` | 可选常驻单元：跑 `data-up --profile 3`，失败 30 s 重试，SIGTERM 自动清理。**默认不装不 enable**（无卡会空转），插卡后再装 |
| `/etc/systemd/system/mhi-efs-sync.service` | AP 托管 modem EFS（`-o /var/lib/mhi-efs` 目录模式，**不写分区**）；不服务则首次 EFS-Sync 后 ~73 s ERRFATAL |
| `/etc/systemd/system/tqftpserv-sdx55.service` | QRTR **instance 3** 的 TFTP 文件服务（上游只发 instance 1），把 `modem_a` 只读挂到 `/mnt/modemfw` |
| `/etc/systemd/system/pm-service-native.service` | 原生 Peripheral Manager QMI shim，`DefaultDependencies=no` 抢在 sysinit 起 |
| `/srv/modem-20261112/` | 日志、soak 采样、证据 |

仓库副本：`config/kebab-sdx55-modem.service`、`config/kebab-sdx55.defaults`、`config/kebab-sdx55-health.service`、`config/kebab-sdx55-health.timer`、`config/sdx55-support/{mhi-efs-sync,tqftpserv-sdx55,pm-service-native}.service`（**设备原样，SHA 逐字节核对**，见该目录 README）、`scripts/linux/kebab-modem-{bringup,teardown,status,health,selftest,sim-provision,sim-preflight,mhi-diag}.sh`、`scripts/linux/kebab-sim-day.sh`、`scripts/linux/kebab-qmi-wds.py`、`scripts/linux/kebab-modem-soak-summary.py`、`scripts/linux/kebab-modem-qmap.sh`、`scripts/linux/kebab-qmap-local-test.py`、`scripts/linux/rmnet-link-test.c`。

**仓库 ↔ 设备一致性（2026-11-13 复核）**：38 个文件（21 个脚本/工具 + 3 个数据文件 + 7 个 unit/defaults + 5 个 MM 规则/片段 + 2 条 MM 补丁）逐字节 SHA-256 相同，**38/38 匹配**——仓库副本就是设备在跑的东西，不存在"文档写一套、设备跑另一套"。复现命令（在电脑仓库根目录）：

```sh
# 设备侧
ssh kebab 'sha256sum /usr/local/lib/kebab-modem/*.sh /usr/local/lib/kebab-modem/*.py /usr/local/sbin/kebab-modem-mhi-diag \
  /usr/local/sbin/kebab-modem-status /etc/systemd/system/kebab-sdx55-* \
  /etc/systemd/system/{mhi-efs-sync,tqftpserv-sdx55,pm-service-native}.service \
  /etc/default/kebab-sdx55'
# 本地侧对每个文件做同样的 sha256sum，逐行比对
```

**未改动**：`/etc/modprobe.d/90-kebab-server.conf`（保留 `blacklist mhi_pci_generic`，它只挡 udev 自动加载，显式 modprobe 仍可用——服务器基线语义不变）、Wi-Fi（mhi0/ath11k）、限充、温控、任何分区。

## 2. 开机自启链（实测，4 次重启复现）

`
[16:51:58] modprobe mhi/mhi_sahara_modem/mhi_wwan_ctrl/mhi_pci_generic ok
[16:51:58] modem QMI ready after 9s (qrtr node 3)
[16:52:02] state: opmode=shutting-down sim_cards_present=0 node=3
[16:52:02] operating mode set to online (policy=auto, sim_cards_present=0)
`

- 开机到 modem 可用约 **9–14 s**（Sahara 15 个镜像，含 2 MiB efs1/efs2）。
- 设备节点：`mhi1` + `mhi1_{DIAG,EFS,IPCR,IP_HW0,IP_SW0,MBIM,QMI}`；`/dev/wwan0{qcdm,efs,mbim,qmi}0`；`mhi_hwip0`/`mhi_swip0`（parentdev `mhi1_IP_HW0`，MTU 16384）。
- QRTR node 3 恢复并含 WDS(1)/NAS(3)/UIM(11)/LOC(16)/PDC(36) 等服务。
- `mhi-efs-sync` 在 15.5 s 开始服务；**modem 真的把 EFS 写回来了**：`/var/lib/mhi-efs/m9kefs1`、`m9kefs2` 各 2 MiB（`wrote 2097152 bytes … session complete`）。
- 重启后 `@kebab-gps`、GeoClue、DSH、酒馆、三档、限充、磁力计门控、Venus 全部 active，**无 failed unit**。

## 3. 关键更正：Bands:'none' 是 EFS 喂入问题，不是 policyman/MCFG

历史记录（含社区 KB2003 的 MODEM.md §4.4.4）都把 `Bands:'none'` 归到 Policy Manager / MCFG apply / NV 未 provision。本机实测：

| 时点 | 服务给 modem 的 EFS | `--dms-get-band-capabilities` |
| --- | --- | --- |
| 11/10 早期（GNSS 文档 §9.2） | 512 B 占位 | `Bands:'none', LTE bands:'none'` |
| 11/10 17:15 之后（round-4 换入 2 MiB 真镜像，但当时未复核 band） | 2 MiB ×2 | 未记录 |
| **本轮（2026-11-12）** | 2 MiB ×2 | **`LTE bands: 1,2,3,4,5,7,8,12,17,18,19,20,26,34,38,39,40,41`；NR5G 1,3,41,78,79；完整 2G/3G 波段** |

即：**只要把本机自己的 2 MiB EFS 喂给 modem，band 能力立刻完整**，无需 PDC 激活、无需写 NV、无需换内核、无需 policyman 配置。本机闪存侧 NV/EFS 本来就是满的（stanvbk 45.28%、94 条 `/nv/item_files` 路径、m9kefs1 带 `IMGEFS1F` 头且 99.59% 非零），社区"空白 EFS"的前提不适用于 KB2000。

配合证据：记忆中的 11/10 §9.2 里 `Bands:'none'` 之后，同一晚 §20.3 已经能读到真实服务小区与 4–6 个邻区——现在有了因果解释。

## 4. 关键更正：GNSS 需要 modem 处于 online

这是本轮新发现，且**推翻了"GNSS 与蜂窝射频无关"在本机上的适用性**：

| modem DMS 状态 | LOC 引擎 | `sats_visible` |
| --- | --- | --- |
| `shutting-down`（mission mode 后的固件默认） | state 1，NMEA 通 | **0**（持续 5 分钟） |
| `offline`（被显式设置） | state 1，NMEA 通 | **0** |
| `online` | state 1 | **21→37 颗，GSM RSSI −95/−96 dBm** |

推论：
1. LOC 引擎"能跑"不等于"有星"；AP 侧看到的 `nmea_ok:true`、`engine_state:1` 会在零星状态下也是 true——这正是历史上"常驻 GNSS"看起来正常、实际无 fix 的另一种可能。
2. 因此开机单元必须把 modem 置 online，否则 GNSS 静默为零。已据此定 `KEBAB_SDX55_ONLINE=auto` 的语义为 **online（无 SIM 也 online）**。
3. **不要把 online 改成 offline**：显式设 offline 之后再设 online 会返回 `QMI error (60) InvalidTransition`，只能重启恢复；已在本轮踩到并记录。

## 5. 真正的剩余阻塞：没有 SIM 卡

`
$ qmicli -d qrtr://3 --uim-get-slot-status
  Physical slot 1:  Card status: absent   Slot status: active  logical slot 1
  Physical slot 2:  Card status: absent   Slot status: active  logical slot 2
`

- `--uim-get-card-status` 两槽均 `error: no-atr-received (3)`、UPIN 未初始化、重试 0。
- `--nas-get-serving-system`：`not-registered-searching`、CS/PS `detached`、Detail Status `limited`、Roaming `on`、Current PLMN `460/0` —— 这是**无卡时的受限服务**（能扫到中国移动 PLMN、能读小区），不是注册成功。
- `--nas-get-signal-info`：GSM `RSSI −95…−96 dBm`（射频前端正常）。
- `--nas-get-cell-location-info`：服务小区 CID 292 / PLMN 46000 / LAC 9441 / ARFCN 46，另有 3 个邻区（−91…−95 dBm）——**RF、频段、NV、MCFG 链路全通**。
- `--nas-network-scan` 返回 `InvalidOperation`（无卡受限服务下不允许），不能据此判 RF 故障。

**结论**：蜂窝数据在本机当前无法继续，唯一原因是**物理上没有 SIM 卡**；软件侧从"modem 起来"到"能读小区"的所有环节都已验证通过。等有卡时只需：插卡 → （必要时）重启，单元会看到 `Card status: present` 并保持 online → 注册 → 再谈 APN/WDS。

## 6. 修好一个被忽视的回归：常驻 GNSS 其实是死的

本轮开始时（重启前）实测：`kebab-gps.service` active、socket 存在，但 **`/run/kebab-gps.json` 根本不存在**——守护进程一直在等 QRTR node 3（`WAIT Location service not in qrtr-lookup`），因为 SDX55 从未被拉起。

原因是三重叠加，与 [PLAN §1.3](sdx55-plan-20261112.md) 的推断一致：
1. `90-kebab-server.conf` 把 `mhi_pci_generic` 拉黑（udev 不自动加载）；
2. `mhi-efs-sync`/`pm-service-native`/`tqftpserv-sdx55` 三个服务是 disabled；
3. `kebab-gps.service` 没有任何 SDX55 依赖或前置。

现在由 `kebab-sdx55-modem.service`（`Before=kebab-gps.service`）补齐顺序，重启后 GNSS 在 60–100 s 内出星。

## 7. 稳定性观察

- **soak #1 实测（boot 9bf0b8a7…）**：57 个样本 / uptime 1,066 s，`mhi1` 100% 在位、DMS 100% `online`、QRTR node 3 100% 在位、**每个样本都有卫星（24–25 颗）**、`errfatal` 行数 **0**。
- **soak #2（同一 boot，2026-11-11 17:12:43Z–18:22:03Z）**：**70 个样本 / 4160 s（69 分钟）跨度**（uptime 1,263 s → 5,422 s），**0 异常 0 间隔**；卫星 23–28（平均 26.5）、`mhi1`/`qrtr node 3`/`online` 100%、`errfatal` 0、`wwan_ports=4`/`mhi_devs=10` 恒定。原计划 120 样本（2 h），**主动停在 70**：历史故障点是"首次 EFS sync 后约 706 s"，本段已经是它的 **10 倍**，再采同样的点没有新假设可验。
- 两次 soak 用的是同一份脚本与判据，分析用 `kebab-modem-soak-summary.py`（样本数/间隔缺口/任意一行偏离期望就列出，退出码 0/1）：`HEALTHY samples=70 anomalies=0 gaps=0`。
- 采样落盘：设备 `/srv/modem-20261112/soak-9bf0b8a7.csv`（#1）与 `soak2-9bf0b8a7.csv`（#2），本机副本 `local/modem-20261112/`（列：ts,uptime,mhi1,opmode,node3,sats,fix,efs_sessions,errfatal,qrtr_err,wwan_ports,mhi_devs；#1 少 `qrtr_err` 一列）。
- **跨过历史故障点**：本 boot 的第一次 EFS session 在 106.9 s 完成（导出 `m9kefs2` 2 MiB），而 MODEM.md 记录的失败形态是"首次 EFS sync 后约 706 s ERRFATAL"；soak 已到 1,066 s（即首次 sync 后 ~960 s）仍 `errfatal` 计数 0、链路正常。
- EFS 落盘目录是 `/var/lib/mhi-efs/`（`-o` 目录模式），**未写任何 mdm1m9kefs 分区**；本 boot 观察到 1 个 session（`m9kefs2`），上一 boot 导出过 `m9kefs1`，即"每次 modem 启动至少一次导出"，不是固定 600 s 周期——不要把"session 数=1"读成同步失败。
- 温度/供电：thermal_zone 35–41 °C、电池 96% / Not charging（限充策略在管），Wi-Fi `wlan0` 正常关联，五业务 active，唯一 failed 仍是历史遗留 `kebab-cnb-domain.service`。
- 只读状态工具：`kebab-modem-status`（打印 boot_id/模块/mhi1/端口/opmode/band/SIM 槽/注册/信号/GNSS/EFS session）。

### 7.1 EFS 回写：modem 自己写了什么（下一轮的持久化候选）

modem 每次同步会把自己的 EFS 视图导出到 `/var/lib/mhi-efs/`，本轮抓到（已复制到 `/srv/modem-20261112/efs-export/`）：

| 文件 | SHA-256（前 16） | 说明 |
| --- | --- | --- |
| 服役中的 `/lib/firmware/sdx55m/efs1.bin` | `ec716f403dd0a87e` | 10/03 原厂 Android 备份，2 MiB |
| 服役中的 `efs2.bin` | `f77c8f94c5a557b7` | 同上 |
| 服役中的 `efs3.bin` | `2d183dc42c096e47` | 仍是 512 B 占位（未换） |
| modem 导出的 `m9kefs1` | `305e52051a247a5b` | 2 MiB，头为 `IMGEFS1H` |
| modem 导出的 `m9kefs2` | `1438eab62681524c` | 2 MiB |

两者**整份不同**（cmp 2088640 字节差异）：导出的不是"我们喂进去的那份"，而是 modem 自己整理过的视图。**本轮没有把导出回灌到 `efs*.bin`**——理由是当前"2 MiB 静态镜像 → band 全通"的链路已验证，换变量需要有 SIM 时才能验收 NV/注册是否仍正常；回灌命令与判据写在这里，留给插卡那一轮一起做（前像：上述三个 SHA，失败即 `gps-round4-rollback.sh` 或 `sudo install` 回原文件）。

**慢卡的时间窗（本轮对照社区脚本发现的缺口）**：社区 `kebab-uim-provision` 在开机时**最多等 120 s** 等卡出现，我们原来的 `sim-provision.sh` 是"没有卡就 <0.2 s 退出"（为了不拖慢每次开机）。两者各有道理，但插卡当天慢卡可能在 12–20 s 才被 UIM 报出来，正好落在 MCFG 的 15 s/24 s 窗口里。折中做法：**用标志文件按需开启等待**——

```sh
sudo touch /etc/kebab-modem-sim-expected    # 插卡前一天或插卡后、重启前
```

有它时 `sim-provision.sh` 会以 2 s 为步长等最多 12 s 抓卡（实测：无标志 113 ms 返回，有标志 12.44 s 返回）；没有它就保持原来的快路径。preflight 会检查这个标志（有 → 15 项全 PASS，无 → 14 项 + 两行 INFO 提示）。**开机集成也实测过**：带标志 `systemctl restart kebab-sdx55-modem` 用时 **28 s**、`Result=success`，两次 sim-provision 分别等 16 s / 12 s（18:27:17→18:27:33→18:27:45），即插卡当天开机额外多约 24 s，换来"慢卡也不会错过 MCFG 窗口"。

### 7.2 MCFG 已经在喂（为插卡准备）

- `/lib/firmware/postmarketos/modem_pr` → `/mnt/modemfw/image/modem_pr`（`modem_a` 只读挂载），`mcfg/configs/mcfg_sw/generic/China/{CMCC,CT,CU}` 都在，tqftpserv 按 QRTR instance 3 提供。
- `--pdc-list-configs=platform|software` 返回 `Total configurations: 0`：无卡时 modem 不做 MCFG 选择，这个 0 目前**不能**当作"缺 MCFG"。
- tqftpserv 日志有 `unable to open /readwrite/ota_firewall/ruleset (2), reject`——modem 请求了一个 AP 侧不存在的策略文件，被拒后继续，属噪声。

### 7.3 常驻健康巡检（本轮新增，专治"静默失灵"）

- 单元：`kebab-sdx55-health.timer`（`OnBootSec=3min`、`OnUnitActiveSec=5min`）→ `kebab-sdx55-health.service` → `health.sh`；结果写 journal（tag `kebab-sdx55-health`）与 `/run/kebab-sdx55-health.json`。
- 三种判定：
  - `mhi1` 不在 ⇒ `action=modem-gone`，**只告警不重启**（实测模块重载救不回 QRTR，需整机重启）；
  - opmode 掉出 `online` 且 QRTR node 在 ⇒ 重设 online，`action=reassert-online`（否则 GNSS 静默无星，见 §4）；
  - 其余 `action=none`。
- 为什么要它：§4 那个"GNSS 名义常驻、实际已死"的坑，本质是状态没人看；这个 timer 把"看不见"变成每 5 分钟一条显式记录。
- 首次实测（17:31:15Z）：`{"ts":"2026-11-11T17:31:15Z","mhi1":1,"qrtr_node":"3","opmode":"online","sats_visible":"26","fix":"false","action":"none"}`。
- **18:06Z 起新增三个字段**，为插卡当天留遥测：`sim_present`（`--uim-get-slot-status` 里 present 的槽数，当前 0）、`data_addr`（`ip -4 addr show mhi_hwip0`，当前空）、`data_link`（`operstate`，当前 `down`）。这样"卡插了但没绑定""data-up 起来了但地址没落"都能从一条 JSON 看出来；实测：`{"ts":"2026-11-11T18:06:22Z","mhi1":1,"qrtr_node":"3","opmode":"online","sats_visible":"27","fix":"false","sim_present":0,"data_addr":"","data_link":"down","action":"none"}`。

### 7.4 社区交叉核对与插卡时序（同日补）

- 与 kebab 专属社区仓库（jfbobier/Oneplus-8T-kebab-pmos-bringup）逐文件核对：`tqftpserv`/`pm_service_native`/`mhi_efs_sync`/`diag_reader` **四个源文件 SHA-256 完全相同**，MM 规则同源；内核侧 `no_m3`、`mhi_sahara_modem`、EFS 通道也已在我们的 7.2 树里。详见[社区同类项目交叉核对](sdx55-community-crosscheck-20261112.md)。
- 他们卡在 "NV 未 provision"（EFS 是 512 B 空白占位、IMEI 全 0、MCFG apply 失败、GPS 无星）；本机 EFS 是 2 MiB 真实镜像、band 全量、GNSS 出星，**那条结论不适用于 KB2000**。
- 已把他们的 `kebab-uim-provision` 改编进开机链（见 §1 的 `sim-provision.sh`）：无卡时 <1 s 退出，有卡时抢在 MCFG 前激活 primary-gw 会话并做**受保护的** PIN1 验证（retries==3 才动，日志一律掩码）。
- **MBIM 备选同日排除**：固件没有 `IP_HW0_MBIM` 通道、`mhi_wwan_mbim` 绑定 ENODEV、原始 `wwan0mbim0` 写 MBIM_OPEN 无回应 ⇒ 数据面只剩 raw-IP `mhi_hwip0`。

## 8. 下一步（需要 SIM 才有意义）

0. **先插卡再开机**（或插卡后重启）。社区实测：MCFG 在 mission mode 后约 15 s 与 24 s 各跑一次，**卡若仍 PIN 锁着就永远不激活配置**，之后 `DMS online` 报 DeviceNotReady、band/`Networks` 空、无数据也无 GNSS；**MCFG 无法在运行时重触发**（`--uim-reset`、重开会话都试过无效）。详见[社区核对](sdx55-community-crosscheck-20261112.md)。
1. 确认 `--uim-get-slot-status` 出现 `Card status: present`；若不出现则换槽/换卡再试（本机物理槽 1 为 active）。
2. 若卡有 PIN：把 PIN 写入 `/etc/kebab-sim-pin`（0600 root，永不入库），开机时 `sim-provision.sh` 会在 retries==3 时自动验证一次；**不要**把 PIN 写进聊天/脚本/日志。没有该文件则脚本跳过，此时需要人工 `--uim-verify-pin` 且必须赶在 MCFG 窗口前。
3. 注册：`--nas-get-serving-system` 应从 `limited` 变 `registered`；不在时看 `--nas-get-system-info` 的拒绝原因。
4. 数据面（控制通道已实测，只差 SIM）：`sudo python3 /usr/local/lib/kebab-modem/kebab-qmi-wds.py up --profile 3`（固件自带 profile 3 = CMNET，也可 `--apn`）（同一 socket 连做 bind→start→settings；**PCIe 端点必须先 bind**，否则 start 返回 QMI error 70 = INVALID_OPERATION，绑好之后无卡时是 error 14 = CALL_FAILED）。拿到 settings 后按 raw-IP 配 `mhi_hwip0`。原理与实测证据见[数据面与 MM 可行性](sdx55-modemmanager-20261112.md) §4.1；`InvalidOperation`。流量落 `mhi_hwip0`。先做独立策略路由 + 目标白名单 + 40 MiB 上限，不动默认路由/DNS/IPv6。
5. ModemManager 的完整功课已提前做完，见[数据面与 MM 可行性](sdx55-modemmanager-20261112.md)：两条 Apollo 补丁在 Debian MM 1.24.0 上**原样可用**（先归一 LF），但**仅补丁不够**，还必须加 EFS ignore 与 SDX55 fusion 两条 udev 规则；且**本机内核无 `CONFIG_RMNET`、mainline `mhi_net` 无 QMAP**，所以 `--test-multiplex-requested` 不可用，数据只能走 raw-IP，存在"~10 MB 后 wedged"的已知风险（**解法的完整证据与 r5 清单见 §8.1**）。对应文件已入库到 `config/mm/`（**未安装**，插卡后按该目录 README 执行）。

### 8.1 QMAP/rmnet 路线（内核 r5 候选，数据面稳健性的唯一已知解）

raw-IP 的 "~10 MB 后 wedged" 不是本机瞎猜：**同族设备 Apollo（小米 11，SDX55 走 PCIe/MHI）实测过同一个现象，并用 QMAP 复用解决**。他们的仓库里有三件直接证据：

| 证据 | 内容 |
| --- | --- |
| `linux-postmarketos-qcom-sm8250/config-…` | `CONFIG_RMNET=m`（全文只有这一处 rmnet 相关配置） |
| `apollo-modem-support/rmnet.conf` | `/etc/modules-load.d/rmnet.conf`，内容 7 字节：`rmnet` |
| `apollo-modem-support/zz-apollo-multiplex.conf` | MM drop-in：`ExecStart=/usr/sbin/ModemManager --test-quick-suspend-resume --test-multiplex-requested`，并写明"raw-IP 约 10 MB 后下行永久 wedged，复用后持续传输能活" |
| `modemmanager-patches/UPSTREAM-NOTES.md` | `wds_test --bind=mux --ep-type=pcie --ep-iface=4 --mux-id=0` → `CONNECTED (handle 0x…) address: 10.26.143.7 mtu: 1500`，流量落 `mhi_hwip0` |

**机制（mainline 侧已核对源码，`sources/linux/drivers/net/ethernet/qualcomm/rmnet/`）**：
- `menuconfig RMNET` — "RmNet MAP driver"，帮助文本明确写着 **"RMNET devices can be attached to any IP mode physical device"**，即父设备是 `mhi_hwip0` 这种 raw-IP netdev 也可以；
- `rmnet_newlink()` 要求 `IFLA_LINK`（父设备）+ `IFLA_RMNET_MUX_ID`，在父设备上注册 rx handler，按 mux id 解 MAP 头，`IFLA_RMNET_FLAGS` 控制聚合/数据格式；
- MM 侧调用的是 libqmi 的 `qmi_device_add_link_with_flags()`（见 `patched-src/src/mm-port-qmi.c` 的 `initialize_preallocated_links` / `mm_port_qmi_setup_link`），再由 `WDS Bind Mux Data Port` 把 bearer 绑到该 mux；**Apollo 的补丁 0002 正是让 MM 在 PCIe 上也做 bind**，这是复用能成立的前提。

**本机差什么（已实测，2026-11-13 更新）**：`/proc/config.gz` 里**没有 `CONFIG_RMNET`**（只有 `CONFIG_MHI_NET=m`），`/lib/modules` 下也没有 `rmnet*`；直接 `ip link add … type rmnet` 会被拒绝（同时 `veth` 能建，说明是模块缺失不是 LSM）。

**但不必因此换内核**：`rmnet` 是自包含模块，已用产出当前镜像的同一棵树/同一份 config（HEAD `88903750a310`、config SHA `e2543ac0…`）以 `CONFIG_RMNET=m` **外部构建**出 `rmnet.ko`（1,767,968 B，vermagic `7.2.0-kebab-debian13-r4-venus+ SMP preempt mod_unload aarch64`），实机 `insmod` rc=0、69 个未定义符号全部在 `/proc/kallsyms` 里，随后 `ip link add link mhi_hwip0 name rmnet0 type rmnet mux_id 1` rc=0，`ip -d link show` 显示 `rmnet mux_id 1 <INGRESS_DEAGGREGATION>`。**全程 flashless、未改镜像/分区，rmmod + link del 后零残留**。复现脚本 `scripts/linux/build-rmnet-module.sh`（产物在 gitignore 的 `local/rmnet-build-*/`，不入库），设备侧入口 `kebab-modem-qmap {status,load,unload,up,down,format}`。详见 MM 文档 §4.10。

原记录（保留作为历史判据）：早先 `ip link add … type rmnet` 被拒、而 `veth` 能建，据此判定是模块缺失而非 LSM 拦截；`kebab-modem-status` 至今打印 `rmnet_kernel no`，因为 `rmnet.ko` 不在 `/lib/modules` 里，是手动 `insmod` 的。

**SIM 日的 QMAP 步骤（原「r5 候选清单」，现在不需要改内核、不需要 r5）**：

1. ~~内核配置加 `CONFIG_RMNET=m`~~ —— 已完成，且走的是**外部构建 + `insmod`**（`kebab-modem-qmap load`），不动镜像、不进 `/lib/modules`；
2. `/etc/modules-load.d/rmnet.conf` 仍可选：不写就每次手动 load（当前就是这样，省得无卡开机多一个模块）；
3. 一条命令跑完：`sudo kebab-sim-day --qmap --mux 1 --hold 120` —— 切格式 → 建 `rmnet0` → 用 `rmnet0` 承载 → 退出自动还原（runbook §4.5、MM 文档 §4.13，2026-11-13 无卡演练通过）；
4. 用一次 >=10 MB 的下载做 wedge 对照（raw-IP 应死、QMAP 应活）——这是 QMAP 唯一还没被证明的地方；
5. **固件侧前提已实测（2026-11-13）**：WDA（service 26）在 node 3 端口 63 已注册，`Get Data Format` 返回 **raw-ip / QoS off / UL+DL aggregation disabled** ⇒ 这台机器当前**不是** QMAP 模式，我们走的无帧头通路与固件一致；要 QMAP 必须主机显式 `WDA Set Data Format`（0x0020，Endpoint Info 是 TLV **0x17**；而 `Get` 用 **0x10**，发错 id 会得到 error 48 假象，端点选错同样 48）。因此“bind mux 是否隐含切格式”这个问题被回答了一半：**隐含不成立**，格式得单独切；剩下的一半（Set 之后是否长稳）仍需插卡实测。
6. **能力门控的限制**：本固件不实现 `Get Supported Messages`（WDS/WDA 均 `error 71 invalid-qmi-command`，qmicli 同）⇒ 无法事前查"支不支持 Set Data Format"，只能发了看错误码。

**顺带一条社区不一致**：Apollo 的 UPSTREAM-NOTES 说"远程 EFS 理论是错的，应丢掉那个内核补丁"，但他们的包**仍然发布 `mhi-efs-sync.service` 与 `mhi_efs_sync.c`**；本机实测（无守护时首次 EFS-Sync 后 ~73 s ERRFATAL、NV 不持久）支持"kebab 必须托管 EFS"。这句按"他们指的是自己写的内核 EFS 端口补丁（后来主线已有）"理解，**不要据此删我们的 `mhi-efs-sync`**。

## 9. 回退

`
sudo systemctl disable --now kebab-sdx55-modem   # 停单位并卸载模块
sudo rm /etc/systemd/system/kebab-sdx55-modem.service /etc/default/kebab-sdx55
sudo rm -r /usr/local/lib/kebab-modem /usr/local/sbin/kebab-modem-status
sudo systemctl daemon-reload
`

回到原基线后 modem 不再开机自启、GNSS 仍按老样子（即无星），
`/var/lib/mhi-efs` 与 `/lib/firmware/sdx55m` 保留不删（私有数据）。

## 10. Evidence → Finding → Path

| Evidence | Finding | Path |
| --- | --- | --- |
| 2 MiB EFS 喂入后 `Bands` 从 none → 完整 LTE/NR 列表（§3） | 历史"policyman/MCFG 门"在本机是 EFS 喂入不足的表象 | 不再尝试 PDC 激活/NV 写；保住 2 MiB 镜像 |
| online vs shutting-down 的卫星数差异（§4） | GNSS 依赖 modem RF 在线；LOC "能跑"≠有星 | 开机置 online；禁用 offline 策略 |
| 两槽 `Card status: absent`（§5） | 当前唯一阻塞是物理 SIM | 等卡；软件侧已就绪 |
| 重启前 `/run/kebab-gps.json` 不存在（§6） | 「GNSS 常驻」长期是名义状态 | 新增开机单元 + 顺序依赖 |
| soak 0 errfatal、EFS 多次 session（§7） | 远程 EFS 链路稳定，不写分区 | 保持 `-o` 目录模式 |
