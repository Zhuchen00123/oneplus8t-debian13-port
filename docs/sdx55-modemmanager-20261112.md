# SDX55 数据面与 ModemManager 可行性（2026-11-12）

> 状态：**离线分析 + 设备只读验证**。没有安装 ModemManager，没有改动现有 SDX55 栈（单元/EFS/GNSS 全部保持）。
> 目的：把"插卡后怎么拿到蜂窝数据"这件事提前做完功课，避免插卡时才发现缺件。
> 前置：[开机自启与 band/GNSS 修复](sdx55-modem-bringup-20261112.md)（本轮之后 modem 已能自动 online）。

## 0. 结论（先看这五条）

1. **社区的 ModemManager 补丁在 Debian 13 上可用**：Apollo 的 `0001-qcom-soc-mhi-net.patch` / `0002-bearer-bind-pcie.patch` 针对 MM `d776ea38`（≈1.25.95），但**原样**就能应用到 Debian 的 `modemmanager 1.24.0` 源码；两条 `git apply --check` 都是 rc=0，实际打进去后新增分支落在预期行（见 §2）。**唯一陷阱是行尾**：从 GitHub 以 Windows 默认设置 clone 下来的 `.patch` 是 CRLF，直接 `git apply` 会报 "patch does not apply"，归一成 LF 后即通过。
2. **光打补丁不够**：MM 1.24 的 qcom-soc udev 规则只认 `bam-dmux`/`ipa`/`qcom-q6v5-mss`，而 kebab 的端口驱动是 `mhi_net`(net) 和 `mhi_wwan_ctrl`(wwan)。必须再加 Apollo 的 `77-mm-sdx55-fusion.rules`（已按本机 PCI id 核对：`0x17cb:0x0306`）。
3. **EFS 端口必须 ignore**：本机 11/10 的 ERRFATAL 就是 MM 用 AT 串探测 `wwan0efs0` 触发的（社区文档同样写明："the modem's rmts_srv receives them as garbage and it ERRFATALs"）。这条规则是安装 MM 的**前置条件**，不是可选项。
4. **本机实测证实"原始 QMI 通道死、控制面必须走 QRTR"**：以 root 打开 `/dev/wwan0qmi0` 发 `--dms-get-manufacturer`，10 秒超时（"cancelling the operation"），与 Apollo 的注释一致。所以 MM 的 QMI 端口只能来自 QRTR node（MM 1.24 有 `mm-kernel-device-qrtr.c`，QRTR 节点自带 `ID_MM_QCOM_SOC=1`）。
5. **数据面的硬限制在 kebab 这边**：本机内核 `CONFIG_RMNET` **未开**，而 mainline `mhi_net` 只做 raw-IP（源码里没有任何 QMAP/mux 处理）。因此 Apollo 的 `--test-multiplex-requested` 在 kebab 上**不能用**，数据只能落 raw-IP `mhi_hwip0`，带着社区记录的"传输到 ~10 MB 后下行 wedged"风险。**MBIM 这条备选已在同日实测排除**：固件没有 `IP_HW0_MBIM` 数据通道（`mhi_wwan_mbim` 的 id_table 只匹配该通道，绑定报 ENODEV），对原始 `/dev/wwan0mbim0` 写 MBIM_OPEN 也无任何回应——详见[社区同类项目交叉核对](sdx55-community-crosscheck-20261112.md) §3。

**因此本轮没有安装 MM**：无 SIM 无法验收数据，而 MM 会接管控制面、探测端口，收益低风险高（且设备当前无外网，装包本身要绕）。规则与助手已入库到 `config/mm/`，插卡后按 §3 执行即可。

## 1. 已验证的事实（证据分级）

| 事实 | 证据 | 级别 |
| --- | --- | --- |
| 两条 Apollo 补丁可应用到 MM 1.24.0 | `git apply --check` rc=0；实际 apply 后 `mhi_net` 分支在 qcom-soc 文件 136 行、PCIe bind 条件在 `mm-bearer-qmi.c` 2093/2246 行 | L1 离线复现 |
| CRLF 会导致误判"补丁不适用" | 原始 patch 948 B/1904 B 含 17/40 个 CR；归一后通过 | L1 |
| Debian 的 MM 带 QRTR 与测试选项 | `meson_options.txt`：`tests` 默认 true、`qrtr` 默认 true；`debian/rules` 未关闭；包依赖含 `libqrtr-glib0` | L1 源码核对 |
| MM 1.24 只给 `ipa`/`bam-dmux` 做 net 数据端口 | `mm-broadband-modem-qmi-qcom-soc.c` 128–140 行 | L1 源码核对 |
| QRTR 节点被硬编码进 qcom-soc 插件 | `mm-kernel-device-qrtr.c:148` | L1 源码核对 |
| 本机 `wwan0qmi0` 不回应 | root + `qmicli -d /dev/wwan0qmi0 --dms-get-manufacturer` → 10 s 超时 | L0 本机实测 |
| 本机内核无 CONFIG_RMNET；`mhi_net` 无 QMAP | `/proc/config.gz` 无 RMNET 项；`drivers/net/mhi_net.c` 无 qmap/mux 代码 | L0 本机 + L1 源码 |
| 本机有 MBIM 数据通道 | `CONFIG_MHI_WWAN_MBIM=m`、`/dev/wwan0mbim0` 存在 | L0 |
| 设备无外网（apt/curl 直连失败） | `curl https://deb.debian.org` 000；只有 192.168.8.2:7890 开着但 HTTP/SOCKS 都不通 | L0 |

## 2. 补丁到底改了什么（给未来的自己）

* **0001（qcom-soc 数据端口）**：在 `peek_port_qmi_for_data()` 里，`mhi_net` 驱动复用 IPA 那条分支。必要性来自上游的硬绑定：QRTR 节点一定带 `ID_MM_QCOM_SOC=1` ⇒ 一定走 qcom-soc 插件 ⇒ 该插件只认 `ipa`/`bam-dmux`，于是报 "Unsupported QMI kernel driver for 'net/mhi_hwip0': mhi_net"。
* **0002（PCIe 强制 bind mux data port）**：修好 0001 后，MM 走到 `WDS Start Network` 会被固件拒成 `QMI error 70 InvalidOperation`——这块固件要求 WDS 客户端先绑定到数据端点；上游只在"用了 mux"时才 bind。补丁把条件扩成"PCIe 端点也 bind"，并限定 `QMI_DATA_ENDPOINT_TYPE_PCIE`，不影响 USB 与 SoC 内置 modem。
* 社区还有一个独立验证工具 `wds_test.c`（单客户端 bind+start），实测参数：`--bind=mux --ep-type=pcie --ep-iface=4 --mux-id=0` → `CONNECTED address 10.26.143.7 mtu 1500`，流量走 **`mhi_hwip0`**，`mhi_swip0` 不参与（端点号 4 对应硬件通道）。

## 3. 插卡后的安装顺序（照做即可）

前提：已装 Debian 的 `modemmanager 1.24.0-1+deb13u1`（依赖本机都齐：`libmm-glib0`/`libmbim-glib4`/`libqmi-glib5`/`libqrtr-glib0`/`libgudev-1.0-0`/`libpolkit-gobject-1-0`）。

1. **先放忽略规则，再装/起 MM**（顺序不能反）：
   `install -m 644 config/mm/77-mm-ignore-sdx55-efs.rules config/mm/77-mm-sdx55-fusion.rules /etc/udev/rules.d/`
   `udevadm control --reload-rules && udevadm trigger --subsystem-match=wwan --subsystem-match=net`
2. **MM 排序与参数**：`config/mm/zz-kebab-mm.conf` → `/etc/systemd/system/ModemManager.service.d/`。它把 MM 排在本项目的 `kebab-sdx55-modem.service` 之后，并且**不加** `--test-multiplex-requested`（原因见 §0.5）。
3. **可选但建议**：`config/mm/kebab-mm-restart-once.sh` + 同名 oneshot 单元。社区实测：MM 早于 modem 起来会"十次超时后永久放弃"，而 modem 出 QMI 的时间在不同 boot 间能从 30 s 漂到 234 s；脚本的职责就是在 modem 真起来后把 MM 重启一次，并在注册卡 `searching` 时切一次 radio。
4. **数据要补丁版 MM**：stock MM 只能做注册/SMS/语音（社区原话："Voice, SMS and registration work without them; only the data bearer is affected"）。补丁版构建（设备上或交叉）：
   `cd ModemManager-1.24.0 && git apply -p1 <两个已归一 LF 的 patch> && meson setup build -Dudevdir=/usr/lib/udev ...`
   —— **注意**本机内核没有 CONFIG_RMNET，所以构建与运行都**不要**开 multiplex。
5. **验收判据**（有卡时）：`mmcli -L` 有 Modem → `mmcli -m 0` 的 `registration: home` → 建 bearer 后 `ip -br link` 出现 `mhi_hwip0 UP` 且拿到地址 → `ping -I mhi_hwip0` 通 → 再传 10 MB 观察是否 wedged（社区记录的分界点）。

## 4. 三条数据路径的取舍

| 路径 | 本机可行性 | 说明 / 风险 |
| --- | --- | --- |
| raw-IP（`mhi_hwip0`） | **可用**（需 0002 的 bind，ep-iface=4） | 社区记录"~10 MB 后下行 wedged，只能拆 bearer 恢复"；可用小流量 + 断线重建规避，不能长期大流量 |
| QMAP / rmnet mux | **当前不可用** | 内核无 `CONFIG_RMNET`，且 mainline `mhi_net` 不产生 QMAP 帧；要做得改内核/驱动，属独立课题 |
| MBIM（`/dev/wwan0mbim0`） | **不可用（已实测）** | 固件无 `IP_HW0_MBIM` 通道；`mhi_wwan_mbim` 绑定时 ENODEV；原始端口写 MBIM_OPEN 无回应。见社区核对文档 §3 |

结论：先按 raw-IP 验收"能上网"，把"大流量 wedged"当已知缺陷记录；若 wedged 影响使用，再评估 MBIM 或 QMAP 的内核工作。

## 4.1 数据面控制通道实测（同日新增，已验证到 call 层）

无 SIM 也能验证的部分已全部跑通。结论先说：**WDS 的 bind/start 走 QRTR 时必须“同一客户端”连做**，而 QRTR 里“客户端”就是 socket——这正是 Apollo 要写 `wds_test` 的原因，现在用 `scripts/linux/kebab-qmi-wds.py` 复刻了它。

实测证据（2026-11-12，设备 qrtr node 3）：

| 动作 | 结果 |
| --- | --- |
| `qmicli --client-no-release-cid --wds-bind-mux-data-port=ep-type=pcie,ep-iface-number=4,mux-id=0` | **成功**，CID 1 不释放 |
| 紧接着 `qmicli --client-cid=1 …` 复用 | **失败**：`Unknown client 1 for service wds`（每条 qmicli 都是新 QRTR socket＝新客户端） |
| 裸 QMI：DMS Get Manufacturer（node 3, port 68） | **成功**，返回 `QUALCOMM INCORPORATED` |
| 裸 QMI：WDS Bind Mux Data Port（node 3, port 61；pcie/4/0） | **成功**，result=0 |
| 裸 QMI：同客户端 bind → start（`--apn cmnet`） | start 返回 **error 14 = CALL_FAILED**（无卡时能走到呼叫层） |
| 裸 QMI：start 不先 bind | 返回 **error 70 = INVALID_OPERATION**（说明 bind 确实改变了请求路径） |
| 裸 QMI：Get Current Settings | **error 15 = OUT_OF_CALL**（符合预期：无已建立的呼叫） |
| Packet Service Status | result=0、TLV 0x01=1 → **disconnected**（先前把响应里的 u8 当 u32 解，才误报“无状态 TLV”，已修） |
| 先把 `mhi_hwip0` 置 UP 再 start | 结果不变（仍 error 14）⇒ 主机接口状态不是 start 失败原因，失败点确实在"无 SIM/未注册" |
| `--wds-get-profile-list=3gpp`（只读） | **读得到本机自带的 5 条 profile**：1=`fota/ia`（APN 空）、2=`ims`、**3=`CMNET`（default/supl/hipri/ut，且是默认 profile）**、4=`CMWAP`（mms）、5=`SOS`（emergency）⇒ 数据面用的 APN 不用猜，固件里就是 CMNET |
| `up --profile 3`（bind 后按 profile 起） | start=error 14，**call-end-reason = 3 "no-service (generic)"**，verbose **type=3 = CM（呼叫管理）reason=1042 = `acl-failure`** ⇒ 呼叫确实发起过，被呼叫管理器的访问控制检查拒掉，与无 SIM（无订阅）一致 |

两个容易踩的细节，已写进工具注释：

1. **QMI over QRTR 的帧头是 7 字节**：`flags(1) + txn(2) + msgid(2) + tlv_length(2) + TLVs`。少了 `tlv_length` 的 5 字节版本会被基带**静默丢弃**（实测：5 字节无回应，7 字节立刻回应）。QRTR 传输本身**不带 QMUX 头**，但 QMI 头里的 TLV 长度字段仍然保留。
2. **目标端口不是 QMI service id**：要在 `qrtr-lookup` 里按描述找，例如本机 node 3 上 `Wireless Data Service` → **port 61**、`Device Management Service` → **port 68**；把 service 1/2 直接当端口发出去只会超时（已实测）。

工具用法（`/usr/local/lib/kebab-modem/kebab-qmi-wds.py`，仓库副本 `scripts/linux/kebab-qmi-wds.py`）：

```sh
sudo python3 kebab-qmi-wds.py info                     # 发现 node/端口 + 当前状态
sudo python3 kebab-qmi-wds.py dms-manufacturer         # 帧格式自检
sudo python3 kebab-qmi-wds.py bind                     # pcie/iface 4/mux 0
sudo python3 kebab-qmi-wds.py up --apn cmnet           # bind + start + settings（同一 socket）
sudo python3 kebab-qmi-wds.py settings                 # 取 IP/掩码/网关/DNS
sudo python3 kebab-qmi-wds.py status
sudo python3 kebab-qmi-wds.py stop --handle N
sudo python3 kebab-qmi-wds.py data-up --profile 3   # bind+start+settings+配 mhi_hwip0，进程活着呼叫才在
sudo python3 kebab-qmi-wds.py data-up --profile 3 --dry-run
sudo python3 kebab-qmi-wds.py data-down --handle N
sudo python3 kebab-qmi-wds.py selftest             # TLV 解析 + 前缀计算 + 接口配置/清理自检
```

`data-up` 的接口配置路径已在真实 `mhi_hwip0` 上验证过（加地址/改 MTU/置 up，再删地址/置 down 全部正确；`selftest` 的解析用例也全过）。无卡时它在 `start` 处以 error 14 退出，不会留下地址或路由。

插卡当天用它：`up --profile 3`（固件自带 profile 3 就是 CMNET，比手写 APN 更贴近原厂行为）→ `settings` 拿到 IPv4/掩码/网关/DNS → 按 raw-IP 配 `mhi_hwip0`（`ip addr add`、`ip link set up`、`ip route add default via … dev mhi_hwip0`）。这一步仍然要等 SIM：无卡时 settings 必回 OUT_OF_CALL，start 的 call-end-reason 恒为 "no-service"。

## 4.2 raw-IP 直通的边界：读 7.2 的 `mhi_net.c` 得到的事

插卡当天真正跑流量的不是 QMI，而是 `mhi_net`（IP_HW0 通道 100/101）。把 `sources/linux/drivers/net/mhi_net.c`（7.2.0）读完，边界很清楚：

| 事实 | 影响 / 对策 |
| --- | --- |
| `ndev->type = ARPHRD_RAWIP`、`hard_header_len = 0`、`IFF_POINTOPOINT\|IFF_NOARP` | 只能 raw-IP，不能挂以太网头；ARP/ND 不用管 |
| **默认 MTU = 0x4000（16384）**、`tx_queue_len = 1000` | LTE 承载按 1500 更稳。`data-up` 现在**有 settings MTU 就用 settings，没有就退回 1500**（`--mtu` 可改），不会留 16 KiB |
| TX 满时 `netif_stop_queue`，UL completion 回调里再 `netif_wake_queue` | 正常路径自洽；若卡死会表现为队列永久停 + TX 计数不动 |
| TX 排队失败只累加 `tx_dropped` 并丢包（`net_err_ratelimited "Failed to queue TX buf"`） | 看 `ip -s link show mhi_hwip0` 的 dropped/errors 与 dmesg 即可判断 |
| RX 若超出一个 MHI 缓冲会返回 `-EOVERFLOW`，驱动做 frag_list 重组并 `netdev_warn_once "Fragmented packets received, fix MTU?"` | 看到这条 warning 就是设备侧 MTU 大于主机侧，先把 MTU 调到 1500 |

**MHI debugfs（排查 wedge 的入口，已确认本机存在）**：

```
/sys/kernel/debug/mhi/mhi1/channels   # 每个通道 state/base/len/rp/wp/local rp/local wp/db
/sys/kernel/debug/mhi/mhi1/devices    # DIAG/EFS/MBIM/QMI/IPCR/IP_SW0/IP_HW0 及各自通道号
/sys/kernel/debug/mhi/mhi1/states     # PM state M0/M2/M3、wake、pending packets
/sys/kernel/debug/mhi/mhi1/events     # 事件环 rp/wp
/sys/kernel/debug/mhi/mhi1/regdump
```

开机后（无数据流）实测形状：`IP_HW0(100)`/`IP_HW0(101)` 都是 `state: 0x2`、环 `len: 0x800`；`IP_SW0` 环 `0x400`；`states: PM state: M0 Device: Active MHI state: M0 EE: MISSION MODE wake: false`。**wedge 时看两件事**：①`channels` 里 IP_HW0 的 `rp/wp` 是否还在动、`db` 是否更新；②`ip -s link` 的 RX 是否还涨。两者都停就按 runbook §6 重跑 `data-up`（不要重载模块，QRTR 端点回不来）。

社区记录过"传输 ~10 MB 后下行 wedged"——**本机未复现（没有卡，没法跑量）**，所以这条只能算移植过来的风险提示，不算结论；上面这套观测点是为了真出现时能定位，而不是猜。

**`data-up` 的清理语义（本轮拷问过一遍，改了三处）**：

1. 信号处理器现在**在碰接口之前**安装——否则 systemd stop / Ctrl-C 落在 `configure_iface` 的毫秒窗口里，会把地址留在 `mhi_hwip0` 上；
2. `cleanup_iface` 结尾统一把链路置 down（呼叫结束、地址删掉后它本来就没有存在意义），下一次 `data-up` 自己会再拉起来；
3. `configure_iface` 顺序改为 **加地址 → 设 MTU → 拉 up →（可选）装默认路由**，避免"先 up 后改 MTU"时出现半配置状态。

`selftest` 也补了"**不留下痕迹**"：它会记下 `mhi_hwip0` 原来的 MTU，测完把 MTU、地址、链路状态全部还原（实测 16384 → 测试时 1500 → 还原 16384，地址为空、链路 DOWN）。这样它可以在任何时候反复跑。

**`kebab-sdx55-data.service` 无卡实测（`systemd-analyze verify` rc=0 之后又真跑了一遍）**：装上 → `start` → 退出码 **3**、`Failed with result 'exit-code'` → 30 s 后 `Scheduled restart job, restart counter is at 1` → 再失败；两次失败后接口 `DOWN`、无地址、MTU 16384（零残留）；测完移除并 `daemon-reload`。结论：单元本身没问题，**唯一正确的启用时机是"手动 `data-up` 已经能 hold 住"之后**。

**wedge 判据已做成命令（本轮新增）**：`sudo kebab-modem-mhi-diag [--samples N] [--interval SEC] [--json]` 会同时在两个时刻读 `/sys/kernel/debug/mhi/mhi1/channels` 的 `IP_HW0(100/101)` 的 `rp/wp`、`/states` 的 PM 状态和 `/sys/class/net/mhi_hwip0/statistics/*`，然后给出一个判决：

| verdict | 含义 | 处理 |
| --- | --- | --- |
| `traffic-flowing` | RX 涨了或 DL 环 `rp/wp` 动了 | 正常 |
| `tx-only-suspected-wedge` | TX 涨、RX 不动、DL 环冻结 | 重启呼叫（SIGTERM data-up 或 `systemctl restart kebab-sdx55-data`），**不要整机重启** |
| `idle` | 两轮采样都没动 | 正常（没有流量） |
| `bearer-down` | 接口不是 up | 还没有 bearer，先跑 data-up |

无卡实测（接口 DOWN）：`verdict=bearer-down`、`ul_ring(100) rp=0xffe99000 wp=0xffe990a0`、`dl_ring(101) rp=0xffe98000 wp=0xffe987f0`、`tx=480 pkts=10 / rx=0`（那 10 个 48 B 包是此前 selftest 把链路短暂 up 时的 IPv6 报文，不是故障）。非 root 运行会明确报 `FAIL: ... not readable (need root ...)` 并以 1 退出。`--json` 输出一行，便于插卡后写进脚本判断。

**QMAP/rmnet 是 raw-IP wedge 的唯一已知解，且已在同族设备验证**：Apollo 的 `CONFIG_RMNET=m` + `/etc/modules-load.d/rmnet.conf` + `ModemManager --test-multiplex-requested` 三件套，就是他们的修复；本机内核缺 `CONFIG_RMNET`（`kebab-modem-status` 里 `rmnet_kernel no`），属于**内核 r5 的配置增量**，不是用户态能补的。完整证据、机制（`rmnet_newlink` 要求 IFLA_LINK + mux id）与 r5 候选清单见[执行记录](sdx55-modem-bringup-20261112.md) §8.1。插卡当天先按 raw-IP 验证"能不能通"，把 QMAP 留给 r5。

## 4.3 协议字段已对照 libqmi IDL 逐项核对（2026-11-13）

数据面工具此前是照 Apollo 的 `wds_test` 写的，TLV 编号**没有权威出处**。本轮把 libqmi 的 IDL（`data/qmi-service-wds.json`）与枚举头（`qmi-enums-wds.h`、`qmi-enums.h`）取到本地，逐项核对，结论是**编号全对，但请求掩码缺两个字段、PDP 类型映射写错了**，已修。

| 用途 | 我们收/发的 | libqmi IDL 里的名字 | 结论 |
| --- | --- | --- | --- |
| Get Current Settings（**0x002D**）请求 | TLV `0x10` = u32 掩码 | Requested Settings (guint32) | 编号正确 |
| ↳ 掩码取值 | 原 `0x03FF` → 现 **`0xA3FF`** | bits0–9 = profile/pdp/apn/dns/auth/ip/gateway；**bit13 = MTU**；**bit15 = IP family** | **本轮补齐**，否则永远拿不到真实 MTU 与承载族 |
| 响应 IPv4 地址 | `0x1E` u32 | IPv4 Address | ✔ |
| 响应 IPv4 网关 | `0x20` u32 | IPv4 Gateway Address | ✔ |
| 响应子网掩码 | `0x21` u32 | IPv4 Gateway Subnet Mask | ✔ |
| 响应 MTU | `0x29` u32 | MTU | ✔ |
| 响应 PDP 类型 | `0x11` u8 | WDS PDP Type：0=ipv4,1=**ppp**,2=**ipv6**,3=**ipv4-or-ipv6**,4=non-ip | **原映射错**（把 1 当 ipv6、2 当 ipv4v6），已改 |
| 响应承载族 | `0x2B` u8 | IP Family：0=unknown,4=ipv4,6=ipv6,8=unspecified,12=ethernet | **本轮新增解析** |
| 响应 DNS | `0x15`/`0x16` u32 | Primary/Secondary IPv4 DNS | ✔ |
| Start Network（**0x0020**） | `0x19` u8 = 4/6/8 | IP Family Preference | ✔ |
| | `0x31` u8 = profile | Profile Index 3GPP | ✔ |
| Bind Mux Data Port（**0x00A2**） | `0x10` 序列 u32+u32 | Endpoint Type + Interface Number | ✔ |
| | `0x11` u8 = mux | Mux ID | ✔ |
| 端点类型 | `pcie`=`3` | `QMI_DATA_ENDPOINT_TYPE_PCIE = 0x03` | ✔ |
| Get Packet Service Status（**0x0022**） | 响应 `0x01` u8 | Connection Status：1=disconnected,2=connected,3=suspended,4=authenticating | ✔（此前"u8 当 u32"的修复方向正确） |

**IPv4 字节序也核了**：QMI 里 IPv4 是 little-endian u32，但线上的 4 个字节就是网络序地址。`parse_settings` 用 `<I` 解出整数、再 `<I` 打回字节交给 `inet_ntoa`，等于是"原样往返"，**结果正确**（自检用非对称地址 `10.99.1.2`/`10.99.1.1` 验证过；对称地址如 1.1.1.1 无法暴露字节序错误，这也是保留非对称样本的原因）。

**本轮工具改动**（`scripts/linux/kebab-qmi-wds.py`，设备同路径）：
1. 请求掩码 `0x03FF` → `0x03FF|0x2000|0x8000`，拿到真实 MTU（运营商常是 1430/1500，不再一律 1500）与承载族；
2. PDP 类型按 libqmi 枚举修正，并新增 IP family 打印；
3. `settings` 没有 IPv4 地址时，错误信息带上 `ip_family`（IPv6-only 承载能被一眼认出，而不是误判成"呼叫失败"）；
4. `selftest` 扩充为：`pdp_type=ipv4-or-ipv6`、`ip_family=ipv4`、`mtu=1430`（后两项正是新增字段的回归保护），实测 **SELFTEST PASS**，真机跑完后 `mhi_hwip0` 仍是 DOWN、无地址、MTU 16384。

**另一条旁证**：`qmicli --wds-get-default-settings=3gpp` 读出默认 profile = `APN 'CMNET'`、`PDP type 'ipv4-or-ipv6'`、`Auth 'none'` —— 与 profile 列表里的 3=CMNET 一致，也解释了插卡后应看到 PDP 类型 3、承载族大概率 ipv4。

**本地留档**（gitignore）：`local/libqmi-idl/{wds.json,qmi-enums-wds.h,qmi-enums.h}`，需要复核时不用再联网。

## 4.4 呼叫失败原因已可读（2026-11-13）

此前 `start` 失败只打印 `error=14` 加一个裸数字。1.36 的 `qmicli` 不给解码，我们自己的工具也不该把"为什么失败"留给猜。现在：

```
start   : result=1 error=14 (call-failed) profile=3
  call-end-reason: 3 = no-service (generic)
  verbose-call-end: type=3 (cm) reason=1042 (acl-failure)
```

**这条是本机无卡时的真实输出**（2026-11-13 复测）。它把之前的记录修正清楚：verbose 的 **type=3 是 CM（call manager，呼叫管理）**，不是 3GPP；1042 是 **CM_ACL_FAILURE**，即呼叫管理器的访问控制/订阅检查失败——这正是"卡不在位"应有的形态，而不是 APN 或射频问题。

做法：把 libqmi `qmi-enums-wds.h` 里 579 个枚举值里的四类 call-end 与七类 verbose 子表抽成数据文件（16.7 KB），工具按 `(type, value)` 查名字，查不到就退回数字，不编造。

| 文件 | 作用 |
| --- | --- |
| `scripts/linux/gen-wds-call-end-reasons.py` | 生成器：`python3 gen-wds-call-end-reasons.py qmi-enums-wds.h > wds-call-end-reasons.json`（线性扫描唯一真源，不联网） |
| `config/libqmi/wds-call-end-reasons.json` | 产物：`call_end`{generic 11, cdma 16, gsm-wcdma 37, evdo 7} + `verbose_type`{1 mip,2 internal,3 cm,6 3gpp,7 ppp,8 ehrpd,9 ipv6} + `verbose`{cm 181, internal 66, 3gpp 55, mip 30, ehrpd 16, ppp 6, ipv6 3} |
| `/usr/local/lib/kebab-modem/wds-call-end-reasons.json` | 设备侧安装位置（与工具同目录，工具按 `__file__` 查找） |

**插卡当天怎么用**（看到 `start` 失败时的下一步）：

| 解出的原因 | 含义 | 下一步 |
| --- | --- | --- |
| `cm / acl-failure` | 访问控制/订阅检查失败 | 卡是否真的 present（`--uim-get-slot-status`）、provisioning 会话是否建立 |
| `cm / no-pdp-context-activated`、`esm-*`、`pdp-activate-max-retry-failed` | 网络拒绝激活 PDP | APN/profile 不对或套餐未开通；换 `--apn` 或 profile |
| `generic / authentication-failed` | 鉴权失败 | 该 APN 需要 PAP/CHAP（`--auth`），或卡状态异常 |
| `generic / no-service` | 未附着 | 等注册，看 `--nas-get-serving-system` |
| `internal`、`mip` 等其它 | 固件内部/其它子系统 | 记录原文，查 bring-up §8 的对应条目 |

生成器与数据文件都可复现：`selftest` 现在有 4 条断言（`verbose_type[3]=cm`、`cm[1042]=acl-failure`、`generic[3]=no-service`、`call_end_name(3)`），实测全 ok；数据文件缺失时工具退回裸数字、不报错也不编造。

## 4.5 WDS Bind Subscription：实测可用，默认是 `any`（2026-11-13）

ModemManager 在能看见多个订阅的 modem 上，会先给 WDS 客户端绑一个 subscription 再起呼叫。我们的工具此前没有这一步（照 Apollo 的 `wds_test` 来，它也没做）。本轮把这条消息补上并**在真机上验证过**——不需要 SIM：

```
$ kebab-qmi-wds.py subscription                  # 只读
get    : result=0 error=0 (none) bound=any

$ kebab-qmi-wds.py subscription --type primary   # 先绑再读回
subscr : result=0 error=0 (none) type=primary
get    : result=0 error=0 (none) bound=primary

$ kebab-qmi-wds.py subscription --type secondary
subscr : result=0 error=0 (none) type=secondary
get    : result=0 error=0 (none) bound=secondary
```

三件事因此确定：

1. **固件接受这条消息**（无限卡也返回 success），不是"未支持"；
2. **默认绑定是 `any`（0xFF）**，这就是此前不 bind 也能走到 start 的原因——不是运气；
3. **绑定是 per-client 的**：每次运行都是新 socket，读回的值恰好等于本次刚设的值，说明它不是全局状态（也说明在 `data-up` 里绑定不会污染别的进程）。

协议定义（libqmi `qmi-service-wds.json`）：

| 消息 | id | 内容 |
| --- | --- | --- |
| Bind Subscription | `0x00AF` | 输入 TLV `0x01` = `QmiSubscriptionType` (u32)：`0 default, 1 primary, 2 secondary, 3 tertiary, 0xFF any` |
| Get Bind Subscription | `0x00B0` | 输出 TLV `0x10` = 同一个枚举 |

工具侧：

```sh
kebab-qmi-wds.py subscription [--type default|primary|secondary|tertiary|any]   # 读/绑+读
kebab-qmi-wds.py data-up --profile 3 --bind-subscription primary                # 起呼叫前先绑
```

`--bind-subscription` 默认 `off`（保持与验证过的 Apollo 流程一致：默认 `any` 已能起呼叫）。错误 3/94（not-supported）视为"固件不关心"继续走，其它错误在 `data-up` 里直接失败——因为继续下去 start 只会以同样原因失败。

**实测这条 bind 不能救无卡**：即便显式绑 `primary`，start 仍返回 `error 14` + `cm/acl-failure`（订阅检查失败），这与预期一致——bind 只是把客户端指向某个订阅，订阅本身得存在。所以它是"插卡后若 start 报订阅类错误时的一个已验证旋钮"，不是无卡的解法。

## 4.6 插卡前能查到的最后一批事实（2026-11-13）

新增两条只读命令，把"插卡后才知道"的东西尽量提前：

```
$ kebab-qmi-wds.py params          # 附着目标与速率
WDS parameters (read-only)
  default-profile : result=0 error=0 (none) 3gpp/embedded -> 3
  max-attach-pdns : result=0 error=0 (none) 56
  attach-params   : result=1 error=74 (information-unavailable) (no TLVs)
  attach-pdn-list : result=0 error=0 (none) current=[1] pending=[]
  channel-rates   : result=0 error=0 (none) tx=0.0Mbps rx=0.0Mbps max_tx=0.2Mbps max_rx=0.3Mbps

$ kebab-qmi-wds.py profile         # 每个 profile 真正拨的 APN
profile 1 : result=0 error=0 (none) name='' apn='' pdp=ipv4-or-ipv6 dns1=0.0.0.0 dns2=0.0.0.0
profile 2 : result=0 error=0 (none) name='' apn='ims' pdp=ipv4-or-ipv6 ...
profile 3 : result=0 error=0 (none) name='' apn='CMNET' pdp=ipv4-or-ipv6 ...
profile 4 : result=0 error=0 (none) name='' apn='CMWAP' pdp=ipv4-or-ipv6 ...
profile 5 : result=0 error=0 (none) name='' apn='SOS' pdp=ipv4-or-ipv6 ...
```

四个结论：

1. **默认 profile 就是 3**（`Get Default Profile Number` 的 3gpp/embedded 回答 3）。这独立验证了 `data-up --profile 3` 的选择——不是照抄"profile 列表里第 3 条"，而是问固件"你默认拨哪个"。
2. **LTE 附着 PDN 列表当前是 `[1]`**，而 profile 1 的 **APN 是空的**（名字也是空，只有 PDP 类型 ipv4-or-ipv6）。空 APN 的附着 profile 在这类 ROM 里通常表示"用网络给的默认 APN"（`ia`=initial attach），多数网络能用；但如果插卡后**附着失败或默认承载没有可用 APN**，这就是第一个要动的地方——`Set LTE Attach PDN List` 改成 `[3]`（CMNET）。**本轮没有写**：改它属于会影响附着的持久设置，等真出现症状再动，命令与风险记在下面。
3. **`attach-params` 返回 error 74 = `information-unavailable`**——固件明确说"还没有附着参数"，与无卡一致；不是我们的解析问题（这也是本轮把 125 条协议错误名做出来的直接收益：看到名字而不是"qmi-error-74"）。
4. **`channel-rates` 的 max 只有 0.2/0.3 Mbps**，看着像"这机器只能跑这么点"。合理解释是**当前驻留在 GSM**（受限服务下服务小区是 GSM、RSSI −95 dBm），所以报的是 GSM/EDGE 档速率。插卡并注册到 LTE 后，这两个数应该跳到几百 Mbps —— 这也是一个**可falsify的验收点**：注册后 `params` 里 max_tx/max_rx 若仍是 0.2/0.3 Mbps，说明 modem 其实没上 LTE。

### 备用操作（只在出现症状时做）

```sh
# 读：当前/待生效的附着 PDN 列表
kebab-qmi-wds.py params

# 写（未执行，需明确场景；Set LTE Attach PDN List = 0x0093，输入 0x10 = 数组，元素 u16）
# 1) 只有在"卡正常、注册正常、但默认承载起不来"时才考虑
# 2) 写入后通常要 modem 重新 attach（重启或 offline/online）
# 3) 记下原值 [1]，以便回退
```

**（已更新，2026-11-13）** 写路径已实现为 `attach-set`（默认 dry-run、默认原样写回），并在无卡状态下做过「改成 `[3]` → 读回 `[3]` → 改回 `[1]`」的往返演练，结论与用法见 §4.11。

## 4.7 AP 侧的 TX 路径已被证明（含环占用度量，2026-11-13）

此前所有证据都停在“呼叫能发起”这一层：TLV 到 call、bind 成功、start 被 CM 拒。**“我们的包真的进得去硬件栈吗”** 一直没有独立测量——如果这半条路是坏的，插卡后看到的“没有流量”就没法归因。

新增 `kebab-modem-tx-probe`（默认 dry-run，`--apply` 才动接口，结束必还原）来测这一半：

```
$ sudo kebab-modem-tx-probe --apply --count 5
iface : mhi_hwip0
before: 0 0 768 16                     # rx_bytes rx_pkts tx_bytes tx_pkts
ring  : IP_HW0(100) ... rp: 0xffe99000 wp: 0xffe99100
up    : 10.99.13.2/30
ping  : rc=1 (loss expected: no bearer)
after : 0 0 1284 23
ring  : IP_HW0(100) ... rp: 0xffe99000 wp: 0xffe99170
delta : tx_packets=7 tx_bytes=516
VERDICT: tx-path-ok  (7 packets handed to mhi_net)

restored: 0 0 1284 23
addr    : 
link    : DOWN
mtu     : 16384
```

三个数字互相对上，这不是“计数器涨了”而已：

| 观察 | 数值 | 含义 |
| --- | --- | --- |
| netdev tx | +7 包 / +516 B | 内核把 7 个包交给了 mhi_net |
| UL 环 wp | `0xffe99100` → `0xffe99170` | **+0x70 = +112 B = 7 × 16 B**，正好 7 个 TRE |
| UL 环 rp | `0xffe99000` 未变 | 设备一个都没取走（无承载，固件不读） |

再往前对一次历史：累计 **23 包 ↔ wp−rp = 0x170 = 368 B = 23 × 16 B**。也就是说**这接口上开出过的每一个包都还是一个未消费的 TRE**，一一对应，没有丢、没有合并、没有多算。

结论：**AP 侧这半条路（内核 → mhi_net → MHI UL ring → doorbell）是完全通的**，插卡后若“没有流量”，问题只能在 modem 的承载侧，不在我们这一侧。这正好把排查范围砍掉一半。

### 新增判据：UL 环占用（TRE）

环长 `len: 0x800` = 2048 B = **128 个 TRE**。由此得到两个新事实：

1. **无承载时环会满**：128 个包之后 wp 追平 rp+2048，TX 就只能停住。无卡状态下这无所谓（链路本来就是 down），但它解释了“只在有承载时才安全”这件事；
2. **wedge 有了精确度量**：`kebab-modem-mhi-diag` 现在输出

```
ul_pending             23 of 128 TREs (18%)  device_consumed=0 TREs
```

JSON 里是 `ul_pending_tres` / `ul_rp_tres_delta`。有承载却 `ul_pending` 逼近 128 且 `device_consumed` 不涨，就是**modem 不再取 UL 环**——比“RX 不涨”更早、更确定。`mhi-diag` 的 `tx-only-suspected-wedge` 分支也把这句话打出来了。

（`local rp/wp` 仍然是不可解的内部指针，工具继续跳过它们；`device_consumed` 用 rp 的差值算，单位 TRE。）

## 4.8 WDA：固件数据格式已确认是 raw-IP（2026-11-13）

QMAP 这条备选路线一直有个没人验证的前提：**固件当前到底用什么数据格式**。如果固件本来就在 QMAP 模式，我们的 raw-IP 解析从第一天起就是错的；如果固件支持从主机切换，那 r5 只缺内核那一半。

`qrtr-lookup` 里先确认服务在：

```
       26       1        0    3    63 Wireless data administrative service
```

WDA（service 26）**在 node 3 上注册了**，端口 63 —— QMAP 开关对主机是可达的，不是被 QRTR 挡住的。

### 关键坑：Get 和 Set 的 Endpoint Info 是不同 TLV id

直接发 `Get Data Format` 只得到 `error 48 InvalidArgument`，很容易误判成"不支持"。真实原因：**这条消息必须带 Endpoint Info**（本机有 pcie/4、embedded、usb 多个端点，固件不知道你问哪个）。而 libqmi 里同一个 "Endpoint Info" 序列：

| 消息 | Endpoint Info TLV id | 内容 |
| --- | --- | --- |
| `Get Data Format` (0x0021) | **0x10** | seq{u32 QmiDataEndpointType, u32 interface} |
| `Set Data Format` (0x0020) | **0x17** | 同上 |

（两版 IDL——master 与 1.36.0——在这点上一致；我先按 0x17 发就吃了 48。）

### 实测结果：raw-IP，无聚合，无 QoS

```
$ kebab-qmi-wds.py format
WDA data format (read-only)
  endpoint        : pcie/4 (tlv 0x10)
  get-data-format : result=0 error=0 (none)
  link-layer      : raw-ip
  qos             : off
  ul-aggregation  : disabled
  dl-aggregation  : disabled
  dl-max-datagrams: 0
  dl-max-size     : 0
  ndp-signature   : 0x00000000
  => raw-IP and no aggregation: matches the unframed mhi_net path
```

`qmicli --wda-get-data-format="ep-type=pcie,ep-iface-number=4"` 给同样的答案（独立实现交叉验证）。端点错则明确失败：

```
$ kebab-qmi-wds.py format --ep embedded --iface 4
  get-data-format : result=1 error=48 (invalid-argument)
```

三件事因此确定：

1. **我们走的 raw-IP 就是固件当前的模式**——`mhi_net` 无帧头的数据通路与固件一致，不是碰巧能跑；
2. **QMAP 目前是关的**（ul/dl aggregation 都是 disabled），要用得显式 `Set Data Format`；也就是说 r5 的 QMAP 分支不只要内核 `CONFIG_RMNET`，还要主机主动切格式，**而且可逆**（再 Set 回 raw-ip + disabled）；
3. **插卡后要复查这个值**：任何时候 `format` 显示不是 `raw-ip` + `disabled`，就说明有东西（ModemManager 的 multiplex 补丁？）动过格式，那时 RX 出现乱包是预期内的。

### 一个诚实的限制：本固件不实现 Get Supported Messages

想用 `Get Supported Messages` 事前确认"固件支不支持 Set Data Format"，答案是不行：

```
$ kebab-qmi-wds.py supported --service wda
WDA Get Supported Messages: result=1 error=71 (invalid-qmi-command) count=0
  (this firmware does not implement Get Supported Messages: feature gating stays empirical)

$ qmicli -d qrtr://3 --wds-get-supported-messages
error: couldn't get supported WDS messages: QMI protocol error (71): 'InvalidQmiCommand'
```

WDS 与 WDA 都是 `error 71`，qmicli 也得到同一答案 ⇒ **是固件没实现这条消息**，不是我们的帧格式。因此能力门控只能"发了看错误码"，不能靠位图。工具保留 `supported` 命令（对实现了该消息的固件有意义），并在本机打出这句解释。

## 4.9 DIAG 通道：能打开、能写，但拿不到 DIAG 应答（2026-11-13）

诊断链的最后一块拼图本来是 modem 自己的 diag 日志——真实网络上，QMI 的错误码往往只说"call failed"，真正的 ESM/CM 原因在 diag log 里。本轮尝试打通它，结论是**这台机器上暂时拿不到**，但过程的证据值得留下，免得下次重复。

### 通道确实存在

```
$ ls -l /dev/wwan0qcdm0
crw------- 1 root root 509, 0 ... /dev/wwan0qcdm0

$ sudo grep -o 'DIAG([0-9]*)' /sys/kernel/debug/mhi/mhi1/channels
DIAG(4)
DIAG(5)

$ ls -l /sys/bus/mhi/devices/mhi1_DIAG/driver
-> .../bus/mhi/drivers/mhi_wwan_ctrl
```

`mhi_wwan_ctrl` 把 DIAG 通道绑成了 `/dev/wwan0qcdm0`（EFS 同理是 `wwan0efs0`），所以概念上"打个 QCDM 帧进去、拿版本号回来"应该成立。

### 实测：回的是我们自己的字节

按 DM 协议发 `DIAG_VERNO_F`（命令 0x00，CRC-16/0x8408）：

```
$ kebab-modem-qcdm.py verno
request : 7e0000007e
response: 137d5e0000007d5ef0577e137d5e0000007d5ef0577e
verdict : echo -- the channel returned the request itself; no DIAG
          handler answered
```

去掉转义后，回应是 `13 <我们发的整帧 7e 00 00 00 7e> f0 57 7e`，而且**连回两遍**。换成三个不同帧（乱码 `7e1122337e`、带真 CRC 的 `7e00f8077e`、CRC 全零的 `7e0000007e`）各试一次：

| 发出 | 回应（去转义后） | 尾部 2 字节 |
| --- | --- | --- |
| `7e 11 22 33 7e` | `13 7e 11 22 33 7e` + `a3 a2` + `7e` | `a3a2` |
| `7e 00 f8 07 7e` | `13 7e 00 f8 07 7e` + `0e 50` + `7e` | `0e50` |
| `7e 00 00 00 7e` | `13 7e 00 00 00 7e` + `f0 57` + `7e` | `f057` |

三个样本都成立的事实：**回显的是整帧（含首尾 flag），前缀恒为 0x13**。尾部两字节随帧变化，但用标准 CRC-16 变体（init 0 / 0xFFFF，反射 0x8408，正反序）对整帧、去 flag 体、payload、加 0x13 前缀四种切法都算不出匹配 ⇒ 它不是我们能复现的 DM CRC，更像是**链路层（而非 diag 命令层）的应答**。

### 也没有自发日志流

```
$ kebab-modem-qcdm.py capture --seconds 4     # 空闲
bytes  : 0
$ (data-up 触发一次 start，同时 capture 6 秒)
bytes  : 0        # 同上，没有任何未请求的字节
```

`start` 这次确实走到了 call 层（`error 14` + `cm/acl-failure`），但 diag 通道一个字节都没吐。

### 结论（明确写下，避免重复投入）

1. **DIAG 通道可打开、可写、可读**，但**没有 diag 命令处理器应答**，回显发生在帧层以下；
2. **没有自发日志流**（至少当前 log mask 下），所以"抓 packet log 再离线解"这条路在此固件上目前不成立；
3. 因此 **SIM 日的诊断仍以 QMI 为准**——而 QMI 这一侧现在并不弱：`call-end-reason` + `verbose-call-end` 已经能把"订阅/ACL 失败"这类原因直接说出来（§4.4），比裸 `error 14` 强得多；
4. `kebab-modem-qcdm.py` 保留为探针：`verno` 复现回显（rc=2 表示 echo、3 表示静默、0 表示有非回显数据），`capture` 存原始字节（`--out`），万一将来有别的固件/状态吐出日志，可以立刻收集。

（顺带一个副作用证据：`wwan0qcdm0` 的读写都成功返回，说明 `mhi_wwan_ctrl` 的 QCDM 端口本身没问题——问题在固件侧没有 diag 服务应答。）

## 4.10 QMAP 不再需要换内核：`rmnet` 作为外部模块加载成功（2026-11-13）

§8.1 里 QMAP 一直被写成"内核 r5 的配置增量"，意味着要重编镜像、`fastboot boot`、再部署。本轮把这条前提拆掉了：**`rmnet` 是自包含模块，可以针对运行中的内核自己构建并 `insmod`**，不改镜像、不重启。

### 为什么可行

`drivers/net/ethernet/qualcomm/rmnet/Kconfig` 自己写着 RMNET 设备可以挂在"任意 IP 模式物理设备"上；它 `select GRO_CELLS`，而本机内核 **`CONFIG_GRO_CELLS=y`**（已核 `out/.config`）。所以缺的只是一个 `.ko`。

构建用的是**产出当前运行镜像的那棵树**（`/home/zhuchen/oneplus8t-venus-20261105`，HEAD `88903750a310`，config SHA `e2543ac0…`，`CONFIG_LOCALVERSION="-kebab-debian13-r4-venus"`），所以 vermagic 天然一致：

```
$ scripts/linux/build-rmnet-module.sh            # 在 WSL 里跑，clang 19 + LLVM=1
head   : 88903750a310ded3f8ed7d8682d7181c67a4299e
config : e2543ac04e3ca5ffeabce188a313e4d72f4ae985bc0cfd3732dd12cb91ec4e12
ko     : .../rmnet.ko   1767968 bytes
sha256 : d8917895ceecc7bed58dd343b7a9d5efed136b4faa4e7f580644fe432813f519
vermagic: 7.2.0-kebab-debian13-r4-venus+ SMP preempt mod_unload aarch64
```

（构建用 `make -C <tree> O=<tree>/out ARCH=arm64 LLVM=1 M=<rmnet dir> CONFIG_RMNET=m modules` —— 用命令行覆盖 `CONFIG_RMNET=m` 让 Kbuild 把 `obj-$(CONFIG_RMNET)` 展开成 `obj-m`；`BTF [M]` 也过了。）

### 实机验证（flashless，未改任何镜像/分区）

| 检查 | 结果 |
| --- | --- |
| `uname -r` | `7.2.0-kebab-debian13-r4-venus+`（与 vermagic 完全一致） |
| 69 个未定义符号 | **全部在 `/proc/kallsyms` 里**（含 `gro_cells_init`、`netdev_rx_handler_register`、`hrtimer_setup`、`alt_cb_patch_nops`） |
| `insmod rmnet.ko` | rc=0，`lsmod` 出现 `rmnet 36864 0` |
| `ip link add link mhi_hwip0 name rmnet0 type rmnet mux_id 1` | rc=0 |

```
$ ip -d link show rmnet0
10: rmnet0@mhi_hwip0: <UP,LOWER_UP,M-DOWN> mtu 1500 qdisc pfifo_fast ...
    rmnet mux_id 1 <INGRESS_DEAGGREGATION> ...
```

`rmmod` + `ip link del` 之后零残留（link 与模块都回 0）。模块文件装在 `/usr/local/lib/kebab-modem/rmnet/rmnet.ko`（1,767,968 B，SHA 见上），入口 `kebab-modem-qmap`：

```
$ kebab-modem-qmap status      # 模块/链路/父接口/内核/模块 SHA/调制解调器格式，一次看全
$ kebab-modem-qmap load        # insmod（幂等）
$ kebab-modem-qmap up --mux 1 --mtu 1500   # 建 rmnet0 over mhi_hwip0
$ kebab-modem-qmap down; kebab-modem-qmap unload
```

### QMAP 开关本身也验证了（而且没有改变现状）

新增 `format-set`（WDA **Set Data Format 0x0020**）。为了不把固件改成 QMAP 之后干等一张卡，**默认动作是"把 Get 读到的值原样写回去"**：

```
$ kebab-qmi-wds.py format-set --apply
  current        : link-layer=raw-ip qos=off ul=disabled dl=disabled ndp=0x00000000 dl-max=0/0 ul-max=0/0
  planned        : 同上
  set            : result=0 error=0 (none)
  read back      : identical to what was there before
$ kebab-qmi-wds.py format
  link-layer      : raw-ip        # 仍然是 raw-ip，状态未被改变
```

结论：**写路径可用、幂等、可逆**。真到要切 QMAP 时是 `format-set --link-layer raw-ip --ul qmap --dl qmap --apply`，切回来是 `--ul disabled --dl disabled --apply`。

### 两个新的 TLV 陷阱（同一号码两个含义）

| 位置 | 0x17 | 0x1B |
| --- | --- | --- |
| `Get Data Format` 输出 | UL max datagrams | **flow control** |
| `Set Data Format` 输入 | **Endpoint Info**（seq u32,u32） | **UL max datagrams** |

即 Get 的 Endpoint Info 是 **0x10**（§4.8 已记），Set 的是 **0x17**；而 0x1B 在 Get 里是流控、在 Set 里是上行聚合上限。所以读写两侧的 TLV 映射必须分开建表，不能共用。

### 开关演练：真的切到 QMAP 又切回来（2026-11-13）

§4.10 上面那次「原样写回」只证明了写路径存在。本轮把开关真拨了一遍：

```
$ kebab-qmi-wds.py format-set --ul qmap --dl qmap --apply
  set            : result=0 error=0 (none)
  read back      : CHANGED {'dl': (0, 5), 'dl_max_size': (0, 64512), 'dl_max_dgram': (0, 1),
                            'ul': (0, 5), 'ul_max_dgram': (0, 1), 'ul_max_size': (0, 64512)}
$ kebab-qmi-wds.py format
  link-layer      : raw-ip
  ul-aggregation  : qmap
  dl-aggregation  : qmap
$ kebab-qmi-wds.py format-set --ul disabled --dl disabled --apply   # 切回来
  set            : result=0 error=0 (none)
  read back      : CHANGED {'ul': (5, 0), 'dl': (5, 0)}
```

四条实测事实：

1. **固件接受并真正应用** QMAP（读回 `ul=5`/`dl=5`，不是「收下了但不生效」）；
2. **聚合上限由固件自己填**：`dl_max_dgram=1`、`dl_max_size=64512`、UL 同样 —— 不需要我们猜参数；
3. **`link-layer` 仍然是 `raw-ip`**：QMAP 在本机是 **raw-ip + aggregation=qmap** 的组合，**不是** `802.3`。谁要是按「QMAP 就是 Ethernet 封装」把 link-layer 也改成 802.3，就会得到一个错的封装；
4. **可逆**：切回 `disabled` 后读回 0/0，`selftest` 仍 PASS。切回时那对 max 值会残留在 disabled 状态里但被忽略，可用 `--dl-max-datagrams/--dl-max-size/--ul-max-datagrams/--ul-max-size` 显式写回（本轮已把设备恢复成固件默认 1/64512）。

顺带加了四个调优开关（`--dl-max-datagrams/--dl-max-size/--ul-max-datagrams/--ul-max-size`）与 `--ndp`、`--qos`，实测 `32/32768` 能被接受并读回 —— 若 SIM 日 QMAP 吞吐不理想，这是第一个可调的旋钮。

**当前设备状态**：`link-layer=raw-ip`、QoS off、UL/DL aggregation `disabled`、max `1/64512`（与演练前逐项一致）。

### SIM 日的 QMAP 序列（已就绪，未验证承载）

```
sudo kebab-modem-qmap load
sudo kebab-modem-qmap up --mux 1 --mtu 1500
sudo python3 /usr/local/lib/kebab-modem/kebab-qmi-wds.py format-set --link-layer raw-ip --ul qmap --dl qmap --apply
sudo python3 /usr/local/lib/kebab-modem/kebab-qmi-wds.py data-up --mux 1 --ifname rmnet0
```

**顺序建议**：先用 raw-IP 跑通（`data-up` 默认路径），确认能 ping、能下载；再切 QMAP 复测。**别一上来就 QMAP** —— 现在只有 raw-IP 被证明「能发起呼叫」，QMAP 整条链路（mux 协商 → MAP 头 → rmnet 解聚合）没有任何实测，两个未知量叠在一起会分不清是谁的问题。切 QMAP 之后**不要**去动 `link-layer`（见上一节第 3 条）。

已知限制（诚实）：`rmnet0` 在父接口 `mhi_hwip0` DOWN 时也能 `UP`（显示 `LOWER_UP`），但这只说明链路管理面通了；**MAP 头是否真的按 mux 1 收发，必须等有承载才谈得上验证**。另外 mux id 必须与 `data-up --mux` 一致（Apollo 的 `wds_test` 用的是 0，本工具默认 1，两者都只是约定）。

## 4.11 LTE 附着 PDN 列表：写路径已实现并演练（2026-11-13）

§4.6 里这条一直被写成「杠杆，本轮未写」。现在写完了，而且**在无卡状态下把值改过去、读回、再改回来**，证明它真的生效：

```
$ kebab-qmi-wds.py attach-set --list 3 --apply
  current       : result=0 error=0 (none) current=[1] pending=[]
  planned       : [3] action=1 (none)
  set           : result=0 error=0 (none)
  read back     : current=[3] pending=[]  (matches plan)
$ kebab-qmi-wds.py params | grep attach-pdn-list      # 独立通道复核
  attach-pdn-list : result=0 error=0 (none) current=[3] pending=[]
$ kebab-qmi-wds.py attach-set --list 1 --apply        # 改回基线
  set           : result=0 error=0 (none)
  read back     : current=[1] pending=[]  (matches plan)
```

要点：

1. **立即生效**：`pending` 始终为空，改的就是 `current`；
2. **可逆**：改回 `[1]` 后读回也是 `[1]`，`selftest` 仍 PASS；设备当前状态与演练前一致（`current=[1]`）；
3. **默认安全**：不带 `--list` 时是「把当前值原样写回」，不带 `--apply` 时只 dry-run；
4. 协议细节（`Set LTE Attach PDN List` = **0x0093**）：输入 TLV `0x01` 是 **array，`guint8` 长度前缀 + `u16` 元素**；`0x10` 是 `QmiWdsAttachPdnListAction`（**1 = none**，2 = detach/PDN disconnect）。默认用 1，避免为了改列表而强制断网。

### 插卡后什么时候用它

只在**出现下面两种症状**时改，别预防性地改：

| 症状 | 判据 | 处置 |
| --- | --- | --- |
| 卡能看见、注册正常，但**默认承载起不来**（`data-up` 的 `start` 一直 `error 14`/`call-failed`，或 `settings` 一直 `out-of-call`） | `params` 显示 `current=[1]`、profile 1 APN 为空 | `attach-set --list 3 --apply`（CMNET），**可能需要重启 modem 或整机让附着重来**，然后重跑 `data-up --profile 3` |
| 想验证「附着用的到底是不是 profile 1」 | 改列表后 `params` 的 `current` 变化且行为随之变化 | 同上；若行为不变，说明本网络不依赖这个列表，改回 `[1]` 保持原样 |

**风控**：这个列表影响的是「附着时用哪个 profile」，改错方向可能让本来能工作的附着失败。所以：①只在有症状时改；②改完记录 `params` 前后两份输出；③任何情况下都能用 `attach-set --list 1 --apply` 回到出厂基线。

## 4.12 证据包现在会直接说「下一步做什么」（2026-11-13）

`kebab-sim-day` 原本只给 `verdict/reason`，半夜看还得自己回查文档。本轮把决策逻辑做成**纯函数** `derive_next()`，`summary.txt` 新增三行：

```
call_end: 3 = no-service (generic)     # 从 data-up 日志里抓的呼叫结束原因
diag: unknown                          # mhi-diag 的四态结论
next_steps: sim=absent: 卡必须在开机前插好（MCFG 只在 mission mode 后约 15 s/24 s 各跑一次…）
```

六条分支（实测输出）：

| 证据 | next_steps 指向 |
| --- | --- |
| `sim_present=0` | 卡必须在**开机前**插好（MCFG 时间窗），并先 `touch /etc/kebab-modem-sim-expected` |
| 卡在位 + `start_error=14` | `attach-set --list 3 --apply`（附着改走 CMNET profile 3）+ 重启 modem 后重跑 |
| 卡在位 + 其它 `start_error` | 按 MM 文档 §4.4 的 call-end 表分流，先留 `params`/`subscription` 基线 |
| 有地址 + `tx-only-suspected-wedge` | **切 QMAP 四连**（format-set → qmap load → qmap up → data-up `--mux 1 --ifname rmnet0`） |
| 有地址 + `traffic-flowing` 但 ping 不通 | 查路由/ICMP（环在动说明不是 modem 停摆） |
| 有地址 + `idle`/未知 | 看 `ip -s link` 的 RX 是否在涨，再判方向 |

**为什么做成纯函数**：它不读设备、不写状态，所以每条分支都能在没有卡的情况下验证。仓库里配了 `scripts/linux/test-kebab-sim-day.sh`（源码级 `SIMDAY_LIB_ONLY=1 . sim-day.sh` 后直接调 `derive_next`），实测 **6 passed 0 failed**，路径不存在时 `rc=2`；源码守卫保证「被 source 时不执行任何采集」。

```
$ sudo sh /usr/local/lib/kebab-modem/test-sim-day.sh
ok   no SIM says the card must be in before boot
ok   card + start error 14 points at attach-set
ok   card + other start error points at the call-end table
ok   wedge points at the QMAP sequence
ok   traffic flowing but no ping points at routing
ok   idle/unknown still says to look at the link
6 passed, 0 failed
PASS
```

诚实边界：**只有 no-SIM 那条分支在真机上跑过**（无卡必然走到那里），其余五条是函数级验证 —— 逻辑对，但"到时候真的出现这个证据组合"还要等卡。

### 4.13 `--qmap` 之后：那条三次改动的建议现在是一条命令（2026-11-13）

§4.12 的 `next_steps` 在 `tx-only-suspected-wedge` 时给的是三条命令（切格式、建 rmnet、用 rmnet0 承载），上面 §4.10 的演练也是手敲的。现在 `kebab-sim-day` 加了 `--qmap`，把这三步连还原一起封装：

```
sudo kebab-sim-day --qmap --mux 1 --hold 120
```

顺序：`format-set --ul qmap --dl qmap --apply` → `qmap load` → `qmap up --mux N` → `data-up --ifname rmnet0 --mux N`（`--mux` 只写一次，脚本保证两边一致）。**恢复走 `trap ... EXIT INT TERM HUP`**，所以 Ctrl-C、`timeout`、脚本中途失败都会还原：格式回 `raw-ip/disabled`、删 `rmnet0`、卸 `rmnet`，还原结果写 `qmap-after.txt`，`summary.txt` 里有 `qmap: armed: …` 或 `setup failed rc=N`。**SIGTERM 路径实测过**：用一个把 `data-up` 之前插入 `sleep 12` 的临时副本跑到中途（此时 `rmnet0` 在位、`ul-aggregation=qmap`）再 `SIGTERM`，脚本退出后 `link-layer raw-ip / ul-aggregation disabled`、无 `rmnet0`、无 `rmnet` 模块，`qmap-after.txt` 照写。

无卡实测（2026-11-13）：`qmap.log` 记录 `set result=0` 与读回 `CHANGED {'ul': (0, 5), 'dl': (0, 5)}`、`rmnet0 … mtu 1500 mux_id 1`；`data-up` 仍 `start_error=14`、`call_end: 3 = no-service (generic)`；退出后 `link-layer raw-ip / ul-disabled / dl-disabled`、无 `rmnet0`、无 `rmnet`、`mhi_hwip0` DOWN 无地址、`SELFTEST PASS`、`errfatal` 0、MM 仍见 `Modem/0`。`--dry-run --qmap` 只做 preflight/status，**不碰 modem**（同样实测过）。

## 4.14 QMAP 封装亲测：双向都通了，字节级可对（2026-11-13，不需要卡）

前面几节只证明了「QMAP 开关能切、`rmnet` 模块能加载」，一直没证明**我们的封包到底长什么样、回来的包会不会被拆**。这轮用两种本地设备把两个方向分别钉死，全程不碰 `mhi_hwip0`、不改任何镜像。

脚本：`sudo python3 /usr/local/lib/kebab-modem/qmap-local-test.py [--mode tun|veth|both] [--mux N] [--json] [--keep]`（默认 `both`、`--mux 7`）。

### 出方向（veth）：MAP 头逐字段对上结构体

veth 对是 `ARPHRD_ETHER`，只能看出方向。同一物理口上挂 mux 7 和 9，各发 3 个手工 ICMP，对端抓到的字节：

```
00 07 00 54 | 45 00 00 54 3b 88 40 00 40 01 dc 58 0a 63 07 01 0a 63 07 02 08 00 26 1d ...
\__________/   \______________________________________________________________________/
  MAP header                    内层 IPv4 (ICMP echo)
```

对照 `include/linux/if_rmnet.h`：

```c
struct rmnet_map_header {
    u8     flags;   /* 0x00 = 纯数据，无 pad / 无 command / 无 V5 csum 头 */
    u8     mux_id;  /* 0x07 = 我们给的 mux id（另一条链路是 9） */
    __be16 pkt_len; /* 0x0054 = 84 = 内层 IP 总长 */
} __aligned(1);
```

三处互证：`mux_id` 就是我们设的值、`pkt_len` 等于内层 `tot_len`、**MTU 1496 = 1500 − 4**（驱动为 MAP 头预留 4 字节）。所以本机 QMAP 是 **raw-IP + 4 字节 MAP 头，不是以太网封装** —— 谁按「QMAP = Ethernet」去配就是错的。

### 入方向（tun）：一个来回，四个环节全过

`tun` 的 `IFF_TUN` 模式是 `ARPHRD_NONE`（`/sys/class/net/*/type` = **65534**）、`hard_header_len` 0 —— **和 `mhi_net` 给协议栈的形状完全一样**。所以它是入方向最忠实的本地替身：

1. 往 `/dev/net/tun` 写 `PI(proto=0x00F9 ETH_P_MAP) + MAP(flags=0, mux=7) + IPv4(10.88.7.2→10.88.7.1, ICMP echo request)`；
2. 内核把它交给 `qmapt0` 的 rx_handler（`rmnet`）→ **deaggregation** → 按 mux 7 找到逻辑口 `qmaptu7`；
3. 目标地址 10.88.7.1 就在 `qmaptu7` 上，内核回 ICMP echo reply；
4. 回包走 `qmaptu7` → rmnet egress **重新贴 MAP 头** → 从同一个 tun fd 读出。

实测输出：

```
tun: qmaptu7 over qmapt0 (ARPHRD type 65534) mux 7 mtu 1496 (deagg on)
  reply via MAP: mux 7 proto 0xf9 inner 10.88.7.1 -> 10.88.7.2 icmp_type 0 id 20823 seq 7 marker True
  tun: PASS
```

断言的是完整闭环：`mux == 7`、`pkt_len == 内层长度`、内层 IPv4 校验和有效、地址**已互换**（8.1→8.2 而不是原样）、`icmp_type == 0`（真是 reply）、id/seq 与我们发出的请求一致（`0x5157`=20823、seq 7）、载荷标记 `kebab-qmap-tun-7` 还在。

这比读代码强：`rmnet_map_ingress_handler()` 里的 `ARPHRD_ETHER` 补头分支、`rmnet_map_deaggregate()` 的 `skb_pull`、endpoint 查找、`rmnet_deliver_skb()` 的 GRO 投递，全部真的执行过一遍。

### 噪声与失败是分开记的

tun 设备上内核自己也会排 IPv6（PI proto `0x86dd`），而 IPv6 走 rmnet 出方向时同样会被 MAP 包起来（内层首字节 `0x60`）。这两类都**计噪声不计失败**，否则「测试跑绿」会变成靠运气：脚本只对我们构造的那条 IPv4 流做断言。

### 与 `mhi_net` 的代码对齐（读 7.2 源码）

| 事实 | 位置 | 意义 |
| --- | --- | --- |
| `ndev->type = ARPHRD_RAWIP`、`hard_header_len = 0` | `drivers/net/mhi_net.c:131` | RX 缓冲区开头没有以太网头，等价于我们 tun 测试里的形状 |
| 首字节高半字节 `0x4`→`ETH_P_IP`、`0x6`→`ETH_P_IPV6`、**其它→`ETH_P_MAP`** | `mhi_net_dl_callback()` | 上游专门给 MAP 留的默认分支 |
| `rmnet_rx_handler()` 不看 `skb->protocol` | `rmnet_handlers.c` | skb 到手就按 MAP 解析 |
| 仅 `ARPHRD_ETHER` 才 `skb_push(ETH_HLEN)` | `rmnet_map_ingress_handler()` | 我们不是 ⇒ MAP 头就在偏移 0（tun 测试正是这条路径） |

### 边界（写清楚，别把这条当成承载验证）

* 证明了：本机内核 + 这份 `rmnet.ko` 的 **MAP 封装/解封装、mux 端点查找、ICMP 往返、MTU 余量** 全部正确，且与 `mhi_net` 的 RAWIP 语义一致。
* 没有证明：modem 会不会接受 QMAP、`WDS Start` 是否成功、聚合是否生效（我们两条链路都是 `ul_max_datagrams=1`，本来就没聚合）。
* 用途：插卡当天如果「呼叫起来了但没包」，这条测试能在 10 秒内把「内核封包/拆包写错了」从嫌疑列表划掉，剩下的只可能是承载侧。

## 4.15 QMAP 不能在无卡 modem 上武装：一次真实 ERRFATAL 的完整记录（2026-11-13）

这一节记录的是**我造成的故障**，不是推测。它同时给出了插卡当天的正确顺序和唯一有效的恢复手段。

### 发生了什么

在无卡状态下跑 sudo kebab-sim-day --qmap --mux 1 --hold 15，脚本按设计走完 QMAP 武装。

最后一栏是**跨重启仍可查的 journal 时间线**（重启后 dmesg 会丢，journalctl -b -1 不会）：

| 本地时间（CST） | 事件 |
| --- | --- |
| 04:51:17 | `sim-day --qmap` 启动 |
| 04:51:19–22 | WDA `Set Data Format` → qmap/qmap；`insmod rmnet.ko`；`ip link add ... rmnet0 mux_id 1` |
| 04:51:28 | `data-up` 在同一 socket 上 bind（result=0）后发 WDS Start |
| 04:51:31 | Start 无回应（msgid 0x0020 超时），data-up 退出 rc=1 |
| **04:51:33** | **`mhi-pci-generic 0002:01:00.0: Device died`** —— 在 restore 动手之前 1 s |
| 04:51:33 | MM 才第一次有反应（`Cannot read from istream: connection broken`）；mhi-efs-sync 的通道挂断退出 |
| 04:51:37 | `Recovery failed: -25`，链路没回来 |

**关键否定证据**：整个窗口里 MM 没有发出任何 QMI 请求（它直到 04:51:33 才因连接断开而醒），健康巡检定时器没到点，EFS 守护安静——**host 侧没有任何第三方在碰这个 modem**，死亡直接跟着「qmap 格式 + mux 绑定 + 未成功的 Start」这一串。

```
WDA set format   : ul=qmap dl=qmap          读回 5/5（link-layer 仍 raw-ip）
rmnet0           : over mhi_hwip0 mux_id 1  mtu 1500
data-up bind     : result=0
data-up start    : timeout waiting for msgid 0x0020 (txn 2)   ← 基带没有回应
restore          : format 回 disabled、删链路、卸模块（都成功）
```

约 30 s 后：

```
[14392.643554] mhi-pci-generic 0002:01:00.0: Device died
[14392.643763] mhi-pci-generic 0002:01:00.0: device recovery started
[14394.755709] pcieport 0002:00:00.0: unlocked secondary bus reset via: pci_reset_bus_function+0x1f0/0x20c
[14395.791570] pcieport 0002:00:00.0: broken device, retraining non-functional downstream link at 2.5GT/s
[14396.791566] pcieport 0002:00:00.0: retraining failed
[14396.791609] pcieport 0002:00:00.0: Data Link Layer Link Active not set in 100 msec
[14396.791983] mhi-pci-generic 0002:01:00.0: reset failed
[14396.792110] mhi-pci-generic 0002:01:00.0: Recovery failed: -25
```

结果：mhi1 只剩空壳（没有 netdev、没有 wwan 端口），qrtr-lookup 里 node 3 整体消失（只剩本地 node 1/5 的测试服务），mmcli -L 变成 No modems were found，GNSS 一并失效。**这是 EFS 修复以来第一次 ERRFATAL**（此前 soak #1/#2 共 130 样本、0 次）。

### 为什么会在无卡时发生

QMAP 的文件格式是**基带侧状态机**的一部分：WDA Set Data Format 把它切到 qmap 后，基带会按聚合方式准备数据通路。无卡时 WDS Start 本来就不可能成功，但这次它不是快速失败，而是**不回包**（msgid 0x0020 超时）；挂着的 start + qmap 格式 + 已 bind 的 mux 端点组合把固件推进了 fatal。作为对照：第 28 轮同样的序列在无卡时是**快速返回 error 14 + cm/acl-failure**，没有崩——所以这是**状态相关、非确定性**的，不能靠「上次没事」来赌。

### 恢复：只有重启有效

依次试过、**全部无效**：

| 动作 | 结果 |
| --- | --- |
| echo 1 > /sys/bus/pci/devices/0002:01:00.0/remove 然后 rescan | 设备被移除后不再枚举（链路没 train 起来） |
| echo 1 > /sys/bus/pci/devices/0002:00:00.0/reset | 桥的 reset 不可写（写不进去） |
| 控制器 qcom-pcie 对 1c10000.pcie 做 unbind/bind | unbind 属性写不进去（EACCES）；而且链路已经 failed training，重绑也未必能恢复 |
| systemctl restart kebab-sdx55-modem | 无关——MHI 设备本身没了 |

**sudo systemctl reboot 一次全部恢复**：mhi1 六个通道齐、两个 netdev 在、QRTR node 3 全部服务回来、op_mode=online、band_lte 全 16 个 LTE 频段、errfatal 0、GNSS 出星、MM 重新发现 Modem/0（需要约 35 s 或 mmcli --scan-modems）。

### 已经改掉的（防止再犯）：三道门，缺一不武装

kebab-sim-day 的 `--qmap` 现在要过三个条件，任一不满足都**只跳过 QMAP、绝不动 modem**：

1. **内核侧预检**：qmap-local-test.py --mode tun（抛一个 tun 设备，不碰 modem）。失败说明 rmnet 在这颗内核上不可用，切格式只会更糟。
2. **必须有卡**：sim_present=0 直接拒绝。
3. **必须已注册**（本轮新增）：NAS `serving` 必须是 `registered*`。有卡但没注册时 Start 同样会失败，而这次的故障正是「Start 失败 + qmap 格式」组合造成的——所以没注册也不许切。

逻辑抽成纯函数 `qmap_gate(sim, serving, precheck)`（被 source 时不执行采集），离线断言 5 条：预检失败 / 无卡 / 未注册 → 拒绝理由各不相同，`registered` 与 `registered-roaming` → 放行。测试总数 **12 passed 0 failed**。

三道门都过才走 format-set → rmnet0 → data-up --mux；退出时仍然无条件还原（格式、链路、模块）。新增两行摘要：`qmap_selftest: pass|fail|not-run` 与 `modem_alive: yes|no`；为 no 时 next_steps 直接说「只能 reboot，host 侧 rescan 救不回」。

无卡实测（三道门都生效）：qmap_selftest: pass、日志 `qmap: skipping arming: no SIM card ...`、modem_alive: yes，跑完 ul/dl-aggregation 仍是 disabled、无 rmnet0、无 rmnet 模块、errfatal 0。

### 插卡当天的顺序（照这个来，别反）

1. 先插卡、再开机（MCFG 只在 mission mode 后 15 s/24 s 各跑一次）。
2. kebab-sim-day（**不加** --qmap）：先把 raw-IP 打通、能 ping。raw-IP 是唯一已经端到端验证过的路径。
3. 只有 raw-IP 证实能通但会 wedge（mhi-diag 报 tx-only-suspected-wedge）时，才用 --qmap。此时有卡、有注册，QMAP 是正常路径而不是给基带喂垃圾状态。
4. 万一 modem 还是死了：**reboot**。不要在 host 侧折腾 PCIe，那只是浪费时间。

## 4.16 社区那条「成功判据」在本机通过，并进了自检（2026-11-13）

社区 handoff 把下面这条当成 modem 工程是否完成的唯一判据：

> `qmicli -p -d qrtr://3 --dms-get-capabilities | grep Networks` —— 列出 RAT 就说明 MCFG/NV 那条链是通的，数据与 GNSS 会跟着来。

本机只读实测（`-p` 都不需要）：

```
Networks: 'cdma20001x, evdo, gsm, umts, lte, 5gnr'
Max TX channel rate: '1800000'   Max RX channel rate: '3100000'
Data Service: 'non-simultaneous-cs-ps'   SIM: 'supported'
```

这与已知的三件事互证：全 16 个 LTE 频段 + NR 1/3/41/78/79、GNSS 稳定出星、`op_mode=online`。**他们卡住的「NV 未 provision」形态在本机不成立**，剩下的只有物理卡。

既然它是一条「一眼判断整机 provisioning 有没有坏」的检查，已经进 `selftest.sh`：

```
PASS  DMS capabilities list RATs (networks: cdma20001x, evdo, gsm, umts, lte, 5gnr)
PASS  mhi_hwip0 (IP_HW0 data path) present
PASS  MM ignores mhi_swip0 (will use the right data port)
RESULT: 13 passed, 0 failed
```

新增的三项各有用途：能力集列不出 LTE ⇒ MCFG 没跑成（插卡太晚的典型形态）；`mhi_hwip0` 不在 ⇒ modem 死了（reboot）；MM 没忽略 `mhi_swip0` ⇒ udev 规则丢了，插卡当天会出现「呼叫起来但没包」。

另一个社区事实：他们的内核配置 `kernel/config-postmarketos-qcom-sm8250.aarch64` 里同样有 `CONFIG_MHI_NET=m` + `CONFIG_RMNET=m`，说明 QMAP 也是他们认定的正解——我们只是没改内核，用外部 `rmnet.ko` 达到同样状态（§4.10）。

社区还把我们「没有 `pd-mapper`」列为缺件；实测本机不需要它：SLPI 传感器、GNSS、MHI 都不走 PD 映射，系统里没有任何单元引用它。
## 4.17 ModemManager 那条「建 QMAP 链路」的代码路径已在本机跑通（2026-11-13）

到目前为止我们建过的 rmnet 链路全都是 `ip link add ... type rmnet`（iproute2）。而 ModemManager 的 multiplex 承载走的是**另一条**代码路径：libqmi 的 `qmi_device_add_link_with_flags()` → 自己拼 rtnetlink 消息 → 内核。这条路径在 7.2 + `mhi_net` + 外部 `rmnet.ko` 上能不能成，此前**没有任何证据**——插卡那天如果它不成，MM 的 QMAP 承载会直接失败。

### 怎么测的

新工具 `scripts/linux/rmnet-link-test.c`（设备上 `/usr/local/lib/kebab-modem/rmnet-link-test`，源码同目录留档）用 `qmi_device_new()` 从**一个从不打开的 `/dev` 节点**拿一个 `QmiDevice` 句柄，然后调用与 MM 完全相同的 API：

```c
qmi_device_add_link_with_flags (dev, mux_id, base_ifname, ifname_prefix, flags, NULL, add_done, NULL);
```

句柄不 `open`，所以**没有任何 QMI 报文发到基带**；整个测试只动网口与内核模块。装配成一条命令：

```
sudo kebab-modem-qmap linktest        # 自动 insmod → 建两个链路 → 读内核 flags → 删链路 → 必要时 rmmod
```

### 实测结果（7.2.0-kebab-debian13-r4-venus+）

| libqmi 调用 | 建出的网口 | 内核看到的 flags | MTU |
| --- | --- | --- | --- |
| `add mux 1, flags=0` | `kebt0@mhi_hwip0`（`link/[519]` = `ARPHRD_RAWIP`） | `<INGRESS_DEAGGREGATION>` | 16380 |
| `add mux 2, flags=6` | `kebt1@mhi_hwip0` | `<INGRESS_DEAGGREGATION,INGRESS_MAP_CKSUMV4,EGRESS_MAP_CKSUMV4>` | 16380 |
| `del` 两者 | 都成功，零残留 | | |

`flags=6` 是 libqmi 的 `INGRESS_MAP_CKSUMV4 (1<<1)`\|`EGRESS_MAP_CKSUMV4 (1<<2)`，内核显示的是 `1<<2`/`1<<3` ⇒ **libqmi 确实做了枚举到内核位的转换**，不是把自家位直接塞进去。

### 四条附带事实（都会在插卡当天用到）

1. **`INGRESS_DEAGGREGATION` 默认就是开的**，即使 MM 传 `NONE`（plain QMAP 就是 NONE）——这正是我们用 tun 双向闭环验证过的那种链路形态（§4.14）。
2. **网口名是 `前缀+序号`，不是 `前缀+mux`**：mux 1→`kebt0`、mux 2→`kebt1`。所以 MM 的承载不会叫 `rmnet1`/`rmnet2`，别按 mux 去找名字。
3. **MTU 从父设备继承减 4**：父 `mhi_hwip0` 是 16384 → 链路 16380。真正该生效的 MTU（QMI settings 给的 1430/1500）要**设在链路上**，不是设在物理口上；`data-up` 在 raw-IP 模式设物理口，QMAP 模式下要改设 `rmnet0`/MM 链路上的 MTU。
4. **同一个 mux 不能建第二个链路**（内核 EEXIST，libqmi 报错返回）——第一版测试两条链路都用 mux 1，第二条失败，改成 mux 1 / mux 2 才通过。

### 意义

数据面还剩的三块（MM 建的链路、WDA 的 QMAP 格式、`--test-multiplex-requested` 承载）里，**第一块现在有实机证据**：MM 的 QMAP 承载在插卡当天至少不会卡在“建不出链路”。剩下两块仍然只能等卡。

### 本轮自己的一个失误（记下来，形状很熟）

第一版 `linktest` **打印了 PASS 却返回 1**，而且测试后模块没被卸掉。原因：EXIT trap 里引用了 `cmd_linktest` 的 `local` 变量，函数返回后 trap 才跑，`set -u` 下 `loaded_here: unbound variable`——trap 报错把退出码改成 1，并且 `rmmod` 那行根本没执行。改成“trap 只碰全局变量 + 返回前显式清理并 `trap -`”。和之前的教训同源：**一个静默失败被翻译成了另一个语义**。
## 4.18 数据面补齐 IPv6：profile 3 是 ipv4-or-ipv6，我们此前只认 IPv4（2026-11-13）

固件默认 profile 3（CMNET）的 PDP 类型是 **ipv4-or-ipv6**。此前 `data-up` 只解析 IPv4 的 `0x1E/0x20/0x21`，遇到 v6-only 或 v4 地址缺失的承载会**直接判死**（`settings did not return an IPv4 address`）——那是一个假阴性：呼叫可能是好的，只是地址在 v6 侧。中国移动这类网络 v6 覆盖率很高，插卡当天撞上的概率不低。

### TLV 编号这次是查 IDL 得来的，不是猜的

把 libqmi 的 `data/qmi-service-wds.json` 取下来，直接读 **Get Current Settings（0x002D）的 output 段**：

| TLV | 含义 | 格式 |
| --- | --- | --- |
| 0x1E | IPv4 Address | u32 LE |
| 0x20 / 0x21 | IPv4 Gateway / Subnet Mask | u32 LE |
| **0x25** | **IPv6 Address** | 16 字节地址（network 序）+ **u8 prefix length** |
| **0x26** | **IPv6 Gateway** | 同上（地址 + prefix） |
| **0x27 / 0x28** | **IPv6 Primary / Secondary DNS** | 16 字节 |
| 0x29 | MTU（v4/v6 共用这一个） | u32 |
| 0x2B | IP Family | u8 |

顺带确认了两件事：我们一直在用的 `0x29 = MTU` 是对的；`0x2B = IP family` 也是对的（此前已用 4/6/8/12 的枚举验证过）。

### 改了什么

`kebab-qmi-wds.py`：

- `SETTINGS_TLV` 补 `0x25 ipv6`、`0x26 gw6`、`0x27 dns1_6`、`0x28 dns2_6`；`parse_settings()` 用 `inet_ntop(AF_INET6)` 解地址、取第 17 字节作 prefix（`ipv6_prefix` / `gw6_prefix`）。
- `configure_iface()`/`cleanup_iface()` 增加可选 `v6=(addr, prefix, gw)`：地址用 `ip -6 addr replace … nodad`（点到点承载没必要做 DAD，否则地址会先 tentative 一两秒），路由只在 `--route` 时加。
- `data-up`：**有 v4 配 v4、有 v6 配 v6、两者都有就都配**；只有 v6 时明确打印 `note : bearer is IPv6-only; IPv4 targets will not work` 并继续（不再判死）；两者都没有才 `die(5)`，报错文案也改成 “neither an IPv4 nor an IPv6 address”。`--dry-run` 与 hold 行都会显示双栈。

`kebab-sim-day.sh`：

- 除 `IP` 外再等 `IP6`（`ip -6 … scope global`，天然排除 fe80 链路本地），`summary.txt` 多一行 `addr6:`；
- 有 v6 地址就补三条 v6 探针（网关 / `2400:3200::1` / `2001:4860:4860::8888`），v4 探针照旧；
- 判定改成 **任一地址族 ping 通即 PASS**（`ADDR=${IP:-$IP6}`），reason 里写的是实际用的那个地址。

### 证据（无卡，离线可复现）

`kebab-qmi-wds.py selftest`（加了 IPv6 断言与一次真实接口双栈往返）：

```
  parse ipv6_str: '2001:db8::2' ok
  parse ipv6_prefix: 64 ok
  parse gw6_str: 'fe80::1' ok
  parse dns1_6_str: '2001:4860:4860::8888' ok
  parse v6-only ipv6_str: '2001:db8::9' ok
  parse v6-only has no ipv4: False ok      # 即没有 IPv4 TLV 也不崩
  parse v6-only ip_family: 'ipv6' ok
  iface addr on mhi_hwip0: ok
  iface addr6 on mhi_hwip0: ok
  iface addr6 removed from mhi_hwip0: ok
SELFTEST PASS
```

`kebab-sim-day`（无卡）跑完整流程：`addr: none` / `addr6: none` / `verdict: FAIL reason: no SIM card seen by UIM`，probes 里写 `(no bearer address; probes skipped)` ⇒ **新增的 v6 路径在没有承载时不产生噪声**；离线分支测试仍 **12 passed 0 failed**。

### 又踩了一个和上一轮同型的坑

第一版把 `IP6=$(…)` 写在等待循环体里，而循环有一个“data-up 提前退出就 break”的分支在它**前面**——无卡时必然走那条路，于是 `set -u` 报 `IP6: parameter not set`，脚本在 probes 之前就挂了（`rc=2`，summary 根本没生成）。修法就一行：把 `IP6=""` 和 `IP=""` 一起在循环前初始化。教训同前：**新增变量要先证明它在所有退出路径上都已赋值**，否则 `set -u` 会把“没有承载”翻译成“脚本崩了”。
## 4.19 插卡前最后一个会咬人的细节：`rmnet0` 这个名字是共享的（2026-11-13）

QMAP 那套工具一直把链路名硬编码成 `rmnet0`，`sim-day --qmap` 也跟着假设。问题是**这个名字不是我们的**：ModemManager 的 multiplex 承载用同一套 libqmi API、同一个 `rmnet` 前缀，它建出来的链路**也叫 `rmnet0`**。插卡当天 MM 先起承载、我们再跑 `--qmap`，会发生两件都不好看的事：

1. `ip link add rmnet0` 撞名失败（原来的 `cmd_up` 更糟：它看到 `rmnet0` 已存在就说 “already exists; leaving it alone” 然后**当自己的用**）；
2. 更坏的是收尾：`qmap.sh down` / `sim-day` 的还原逻辑会去 `ip link del rmnet0`，**把 MM 的承载链路删掉**。

### 改了什么

- `qmap.sh up` **不再复用已存在的名字**：从 `rmnet0..rmnet7` 里挑第一个空闲名，建完把 `LINK=/MUX=/PARENT=` 写进 `/run/kebab-modem-qmap.state`，并打印 `link up: <name> over <parent> ...`。
- **mux 冲突直接拒绝**：如果 `$PARENT` 上已经有链路占着同一个 mux id（MM 的承载长得一模一样），`up` 报错退出而不是“合着用”：`mux 1 is already used by 'rmnet0' on mhi_hwip0; refusing to share a link with another owner…`。
- `down` **只删 state 文件里记的那个**；没有 state 文件时**拒绝猜**，把 `$PARENT` 上现有的 rmnet 链路列出来，要求 `down --name <if>` —— 宁可什么都不做，也不能删别人的链路。
- `unload` 的检查从“`$LINK` 还在不在”改成“`$PARENT` 上还有没有 rmnet 链路”，避免在别人链路还挂着时卸模块。
- `--name IF` 可选（`up`/`down`），显式指定时不猜。
- `sim-day` 不再假设名字：从 `qmap.log` 里读回 `link up: <name>`，用它做 `IFACE` 和收尾检查（`qmap-after.txt` 也改成列 `$LINK_PARENT` 上的全部 rmnet 链路）。解析抽成纯函数 `qmap_armed_iface()`，可离线测。

### 实测（四条路径都在设备上跑过）

| 场景 | 结果 |
| --- | --- |
| 无任何 rmnet 链路，`up --mux 1` | 建 `rmnet0`、state 写入 `LINK=rmnet0 MUX=1 PARENT=mhi_hwip0`；`down` 删除并清 state |
| 先手工造一个“MM 的” `rmnet0`（mux 1），再 `up --mux 1` | **拒绝**：`FAIL: mux 1 is already used by 'rmnet0' …`，rc=1，假链路原样保留 |
| 假 `rmnet0` 占着名字（mux 3），`up --mux 2` | 建出 **`rmnet1`**（不碰 rmnet0），state 记 `LINK=rmnet1 MUX=2`；`down` 只删 `rmnet1`，`rmnet0` 还在 |
| 无 state 文件、现场有 `rmnet0`，`down` | 打印 `no state file … rmnet links on mhi_hwip0: rmnet0` + `refusing to guess … use 'down --name <if>'`，rc=0，**什么都没删** |
| 同一场景再加 `down --name rmnet0` | 明确按名字删除 |

`bash -n` 通过（这个脚本是 bash-only，`sh -n`/dash 会在既有语法上报错——本轮一度被这点误导，以为改动把脚本弄坏了）。

### 又踩一个和前面同型的坑

`state_get()` 原本是 `sed … | head -1`。无 state 文件时 `sed` 退出 2，在 `set -e` + `pipefail` 下**整个脚本静默退出 2**：`down` 那句提示一个字都没打印，看起来像“没有状态就什么都不做”，实际是脚本死了。修法是在函数末尾加 `|| true`。这已经是同一形状的第三次：**一个静默失败被翻译成另一个语义**。

### ModemManager 共存也进了证据包

`sim-day` 的 `summary.txt` 新增一行 `mm:`，只取三个字段（`modem.generic.state`、`state-failed-reason`、`bearers`，**不碰 IMEI**），例如无卡时：

```
mm: active failed sim-missing --
```

有卡时若变成 `mm: active registered roaming 1`，就说明 MM 自己起了一个承载 —— 那我们 `data-up` 的 bind 失败就不是 modem 的错，而是争用；这一行让插卡当天能一眼分开。**只记录、不停 MM**（停不停是人的决定）。

### 附带把离线测试加到 14 条

`test-kebab-sim-day.sh` 新增两条：从 `qmap.sh` 输出里读回链路名（`rmnet3`），以及空日志时**不返回名字**（闸门跳过时不能凭空造出一个 iface）。实测 **14 passed 0 failed**。
## 4.20 一次没有日志的硬重启：MAP 控制帧、`flow-control` TLV，和「panic 在这台机器上是隐形的」（2026-11-13）

这一轮有两件事，一件是查出来的事实，一件是我自己造成的重启。先说事实。

### 4.20.1 事实：固件的 `flow-control` 一直是 0，而且我们一直没看见

`Get Data Format` 的输出 TLV `0x1B` 是 **Flow Control**，libqmi 的 IDL 里写得很清楚：**`guint8`**。而我们的解析器把它当 `guint32` 解（`len(v) >= 4`），于是一个 1 字节的 TLV 被**静默跳过**——此前所有 `format` 输出的“没有 flow-control 这一行”，其实是“没解析出来”，不是“固件没发”。修成 u8 之后读到的真实值：

```
  flow-control    : 0 (disabled)
```

这和 `link-layer raw-ip / ul-disabled / dl-disabled` 一起，构成 raw-IP 通道的完整画像。同一形状的错以前犯过一次（`Get Packet Service Status` 的 u8 状态被当 u32，永远打印 “no status TLV”），这次是第二次。

### 4.20.2 由此引出的猜想：kernel 侧的 MAP 命令开关

`rmnet_handlers.c` 里控制帧的处理是这样的：

```c
if (map_header->flags & MAP_CMD_FLAG) {
        if (port->data_format & RMNET_FLAGS_INGRESS_MAP_COMMANDS)
                return rmnet_map_command(skb, port);
        goto free_skb;          /* 静默丢弃，不 ACK */
}
```

也就是说：**QMAP 模式下如果固件发 flow-control 命令，而链路没开 `ingress-commands`，内核会把命令扔掉、永不回 ACK**。这是一个“能跑一阵子然后卡死”的合理机制，值得排除。iproute2 支持这个开关（`ip link add … type rmnet mux_id N ingress-commands on`），实机确认内核位对上了：

```
默认          : <INGRESS_DEAGGREGATION,INGRESS_MAP_COMMANDS>
--no-commands : <INGRESS_DEAGGREGATION>
```

### 4.20.3 然后我把机器搞挂了

为了验证 ACK 路径，我给 `kebab-qmap-local-test.py` 写了一个 `mapcmd` 模式：在 tun 上建 rmnet 链路（`ingress-commands on`），往里写一个手工构造的 MAP flow-control 命令（`struct rmnet_map_control_command`：`command_name=FLOW_DISABLE`、`cmd_type=REQUEST`、`txn=0x11223344`），期望内核回一个 `cmd_type=ACK`、`txn` 相同的帧；再对照一个没开 flag 的链路，期望**没有** ACK；最后测 FLOW_ENABLE 能否把被停住的 TX 队列放出来。

**第一次运行的结果是：机器直接硬重启。**

| 时间 | 事件 |
| --- | --- |
| 05:45:33–34 | 依次 `qmap load` → `up`（ingress-commands on）→ `--no-commands` → `down` → `unload`，正常 |
| 05:45:3x | 上传新版测试脚本、`py_compile` 通过（输出确认收到了） |
| 05:45:3x–4x | `install` 新脚本，随后运行 `--mode mapcmd` |
| 05:45:34 | 上一 boot 的 journal **最后一条**（没有 panic、没有 oops、没有 shutdown 记录） |
| 05:47:11 | 新 boot 起来（与上一条相隔 **97 s**：`panic=15` + 固件与内核启动时间，量级吻合） |

重启后的证据：

- `/sys/fs/pstore/` **空**（挂载着、可读，就是没有记录）；
- **更正（本轮查清）**：`ramoops` **在这块板子上是配好的**——reserved-memory 有 `ramoops@b0000000`（4 MiB、nomap）、`CONFIG_PSTORE_RAM=y`、`CONFIG_PSTORE_CONSOLE=y`、`CONFIG_PSTORE_PMSG=y`，pstore 也正常挂载；开机时驱动打了 5 次 `ramoops: error in header`（社区文档同款「良性陈旧记录」形状）。所以正确的说法是 **「有 pstore，但没有留下记录」**，而不是「panic 看不见」；
- `journalctl -b -1` 在 05:45:34 之后**一个字都没有**；
- `watchdog: Hard watchdog permanently disabled` ⇒ 不是看门狗；
- cmdline 里有 **`panic=15`** ⇒ 内核 panic 会在 15 s 后自动重启，与观测到的间隔吻合；
- `printk.disable_uart=1` ⇒ panic 信息**不上串口**，只能指望 pstore；而这次 pstore 里什么也没有 ⇒ 要么不是 Linux panic（PMIC/硬件复位路径），要么记录在重启时丢了（`error in header` 说明那块 zone 本来就不干净）。**这是本轮自我更正的一处错误结论**；
- 设备回位正常：`opmode=online`、22 颗卫星、MM 重新发现 `Modem/0`、四个单元 active、`errfatal` 0。

一个旁证：重启后 `/usr/local/lib/kebab-modem/qmap-local-test.py` **还是旧版**（`--mode` 里没有 `mapcmd`）——文件写了但没落盘。这与“panic 发生在 install 之后、下一次运行期间”一致（ext4 未提交的写被丢掉）。不过这只是推断，不是证据。

### 4.20.4 结论与决定

1. **`ingress-commands` 的默认值改回 `off`**，并在 `qmap.sh` 里写明原因。`--no-commands`/`--commands` 仍可手动选，但默认不开。
2. **`mapcmd` 测试保留在树里，但标成未验证（UNVERIFIED）**，文件头、`--help` 与函数注释都写明“跑过一次硬重启”。`sim-day` 的预检只用 `--mode tun`，不会碰到它。
3. **没有重跑**。这台是用户的服务器，重跑的代价是又一次不可解释的停机。要真正定位，得先让“下一次”留下证据：pstore 已经在了，缺的是「重启后第一时间去看它」这一步——已把 `pstore records=N` 加进 `kebab-modem-status`（见 4.20.7），下次任何意外重启先跑这一条，有记录就读 `/sys/fs/pstore/*`。若确认 pstore 也没有记录，再考虑 `panic=15` 之外的手段（串口、`ramoops` 大小/console 后端），那属于改引导参数，不在本目标范围内。
4. 对插卡当天的意义：**如果 QMAP 数据流出现“跑一阵子后停住”的现象，不要再假设是 flow-control 命令**——固件当前报的是 `flow-control = 0`，而且打开内核侧 ACK 这条路本身没有被验证过，反而有一次硬重启的记录。

### 4.20.5 这轮的教训

这是第二次“自己把自己搞挂”（第一次是 4.15 的无卡 QMAP）。两次的共同点：**在一个没有 panic 记录的平台上，用“跑一下看看”去验证一个会走内核新路径的猜想**。上次得到的规则是“不要在无卡时武装 QMAP”，这次的规则应该是：**任何会把新路径（MAP 命令、新 flag、新链路类型）喂给内核的实验，先确认 panic 可见（串口或 pstore），否则不做**。
### 4.20.6 重启后的复验（同轮完成）

| 项目 | 结果 |
| --- | --- |
| `kebab-sdx55-modem` / `kebab-gps` / `mhi-efs-sync` / `ModemManager` | 4/4 `active`（重启后自动拉起，无需人工） |
| `opmode` / 卫星 | `online` / 22 颗可见 |
| `selftest.sh` | **13 passed, 0 failed**（rc=0） |
| `test-sim-day.sh` | **PASS**（14 条断言） |
| `kebab-qmi-wds.py selftest` | **SELFTEST PASS** |
| `format` | `raw-ip`、`ul/dl disabled`、**`flow-control: 0`** |
| 残留 | `rmnet=0 links=0 state=0` |
| `dmesg errfatal/device died` | **0** |
| 仓库 ↔ 设备 | **38/38 SHA-256 全同** |
### 4.20.7 让下一次有据可查：`pstore` 进状态面板

既然 ramoops 本来就在，缺的只是「重启后第一时间去看」这一步。`kebab-modem-status` 末尾新增一行：

```
pstore                   records=0
```

非 root 会打印 `unreadable (run as root)`（`/sys/fs/pstore` 是 0750 root）。有记录就直接 `sudo cat /sys/fs/pstore/*`，**读完再碰 modem**。

## 4.21 插卡当天最容易误判的一步：没注册 ≠ APN 错

`WDS Start` 要求 modem 已附着。开机后它通常还在搜网，这时 `start` 报的是：

```
start : result=1 error=14 (call-failed)
  call-end-reason: 3 = no-service (generic)
  verbose-call-end: type=3 (cm) reason=1042 (acl-failure)
```

**和「APN 不对」的报错形状完全一样**（都是 error 14 + CM/ACL），而 `next_steps` 里恰好有一条「试 `attach-set --list 3`」的建议——如果没注册就去改附着 PDN，等于把「等 30 秒就好」误诊成「APN 配错」。这不是理论风险：无卡时我们看到的 error 14 就是这条路径，插卡后如果脚本在附着的半途就开始跑，会一模一样。

**改法**（`kebab-sim-day.sh`）：

1. 新增纯函数 `registration_ok()`（`registered*` ⇒ 已附着；`not-registered-searching`/`limited`/空 ⇒ 没有）与 `serving_now()`（一次只读 `qmicli --nas-get-serving-system`，解析 `Registration state:`）；
2. 新增 `wait_registration SEC`：3 s 一档轮询，`mhi_hwip0` 消失立刻返回（modem 死了不必等满）；
3. 主流程在 QMAP 准备与 `data-up` **之前**等待，默认 **60 s**、`--wait-reg SEC` 可调；超时不中止（承载也许仍能起来），但会记进日志与 summary；
4. `derive_next()` 新增第 9 参 `serving`：**卡在位 + 没有承载 + 未注册** 时，建议从「改 attach-set」改成「先等注册，别改 APN」；只有「已注册 + error 14」才指向 attach-set；
5. `summary.txt` 新增 `serving_before_data:`，把「拉起承载前那一刻的状态」单独留证。

**验证**（无卡，全部离线或只读）：

| 项目 | 结果 |
| --- | --- |
| `test-kebab-sim-day.sh` | **18 passed, 0 failed + 2 项链路名回读 = 20 项，PASS**（新增 `registration_ok` 5 条、「未注册 + error 14」1 条） |
| `serving_now` 真机 | `not-registered-searching`（与我们 NAS 读数一致） |
| `wait_registration 5` | 5 s 后返回 `not-registered-searching`、rc=1（无卡的正确行为） |
| `kebab-sim-day --hold 20`（无卡） | `registration wait skipped: no SIM card`、`serving_before_data: not-registered-searching`、verdict 仍 FAIL/no SIM、接口零残留（DOWN/无地址/MTU 16384） |

**边界**：等待只解决「还没附着」；如果 60 s 后仍 `not-registered-searching`，那是卡/覆盖/频段问题，不是本项目的数据面问题——`next_steps` 会这么写，runbook §4 第 0 步也会让人先看 `registration state`。
## 4.22 慢慢出现的卡不是「没卡」：一次快照定生死的问题

`kebab-sim-day` 判定「有没有卡」原本只做**一次**快照：跑 `kebab-modem-status` → grep `sim_slots_present [1-9]`。这在两种情况下会给出**假阴性**，而两种都恰好发生在插卡当天：

1. 开机后 UIM 还没把卡报出来。社区 `kebab-uim-provision` 为此**最多等 120 s**；我们自己的 `sim-provision.sh` 在 `/etc/kebab-modem-sim-expected` 存在时也会等 12 s——**只有 `sim-day` 不等**。
2. 证据包在「卡其实在、只是晚 15 秒出现」时输出 `verdict: FAIL reason: no SIM card seen by UIM`，`next_steps` 还会让人去重启，把整轮判断带偏。

**改法**（`sim-day.sh`）：

| 组件 | 作用 |
| --- | --- |
| `sim_slots_from_status(text)` | 纯函数：从 `sim_slots_present N` 里取 N，**缺行按 0**（不是 unknown） |
| `sim_now()` | 一次只读 `qmicli -d qrtr://3 --uim-get-slot-status`，数 `Card status: present` |
| `wait_sim_default(flag)` | 纯函数：有 `sim-expected` 标志就给 20 s，否则 0（无卡离线跑必须仍然快） |
| `wait_sim SEC` | 2 s 一档轮询；`mhi_hwip0` 消失立刻返回；打印最后槽位数 |
| 主流程 2a | 首快照没卡且 `WAIT_SIM>0` 时等待，**卡出现就刷新 `SIM`/`REG`**，再进注册等待与 QMAP 闸门 |
| `--wait-sim SEC` | 手动覆盖；`-1` = 自动（默认） |
| summary | 新增 `sim_slots_at_start:` 与 `sim_wait:`，让「等过没等到」和「根本没等」在证据里可区分 |

顺序很重要：2a（等卡）→ 2c（等注册）→ 2b（QMAP 闸门）。QMAP 闸门读的 `REG` 是**等完注册之后**的值，不再用开机快照。

**验证**（无卡）：

| 项目 | 结果 |
| --- | --- |
| `test-kebab-sim-day.sh` | **23 passed, 0 failed + 2 项链路名回读 = 25 项 PASS**（新增 `sim_slots_from_status` 4 条 + `wait_sim_default` 1 条） |
| 纯函数真机取值 | `sim_slots_from_status('sim_slots_present        2')` → `2`；`wait_sim_default 0/1` → `0`/`20` |
| `wait_sim 0` | 立即返回 `0`、rc=1（不空等） |
| 跑 A：无标志 | 总耗时 **2 s**、`sim_wait: 0s`、`sim_slots_at_start: 0`、verdict FAIL/no SIM |
| 跑 B：有标志 | 日志 `waiting up to 20s for UIM (slow card)` → `still no card after 20s (slots=0)`、总耗时 **24 s**、`sim_wait: 20s`；跑完删标志、接口零残留（DOWN / 无地址 / MTU 16384） |

**边界**：等待只解决「固件报得慢」。如果 20 s 后仍然 `slots=0`，那确实是没卡——`verdict` 与 `next_steps` 维持原样，preflight 也会提示「先 `touch /etc/kebab-modem-sim-expected` 再重启」。
## 4.23 ICMP 不是唯一证据：一次 DNS 查询作为承载判据

`kebab-sim-day` 一直用 `ping -I <iface>` 判「承载是否真的通了」。这有个**假阴性**风险：运营商对 ICMP 做策略性丢弃并不罕见（尤其物联网卡/定向套餐），那时数据其实是通的，而我们会写出 `verdict: FAIL … no ping reply`。反过来，ping 通也不证明「应用能解析名字」。

**做法**：新增 `kebab-dns-probe.py`（设备 `/usr/local/lib/kebab-modem/dns-probe.py`），自己拼一条 DNS A 查询、**用 `SO_BINDTODEVICE` 绑在承载口上**发出去，再解析应答。它不读写 `/etc/resolv.conf`，也不经 systemd-resolved —— 只证明「这个口能把一个 UDP 包送到解析器并拿回答案」。

| 组件 | 说明 |
| --- | --- |
| `build_query(name, qid)` | 纯函数，按 RFC 1035 拼 header+qname+qtype；`--selftest` 逐字节校验 `\x07example\x03com\x00` |
| `parse_response(data, qid)` | 纯函数，处理**压缩指针**、跳过 question、只取 A/AAAA；越界/短包**抛错而不是当成「无应答」** |
| `skip_name` | 单独一层，压缩指针与保留 label 类型（0x40/0x80）都显式处理 |
| CLI | `--iface --server --name --timeout --json --selftest`；退出码 0 有答案 / 1 无答案 / 3 自检失败 |

接入 `sim-day`：承载地址就绪后，先问**网络下发的 `dns1`**（WDS Get Current Settings TLV `0x15`），失败再问 `223.5.5.5`；结果写 `dns.json` 与 `probes.txt`，`summary.txt` 新增 `dns:` 行。判定改为纯函数 `bearer_ok(ping, dns)`：**ICMP 或 DNS 任一成功即 PASS**，两者都失败才判定承载不通。

**验证**（无卡 + 一条对照）：

| 项目 | 结果 |
| --- | --- |
| `dns-probe.py --selftest` | **DNS SELFTEST PASS**（4 条布局断言 + 1 条应答解析 + 3 条截断包必须抛错 + 1 条 NXDOMAIN rcode） |
| 绑到 `mhi_hwip0`（无承载） | `[Errno 101] Network is unreachable`，**立即失败不挂住**，rc=1 |
| 绑到 `wlan0` 问 `192.168.8.2` | `rcode=0 answers=1 ips=[…]`，rc=0 —— **工具本身端到端可用**（对照组） |
| 分支测试 | **26 passed, 0 failed + 2 项链路名回读 = 28 项 PASS**（新增 `bearer_ok` 3 条） |
| 无卡 `sim-day` | `dns: no via none`、`ping: no`、verdict 仍 FAIL/no SIM、接口零残留 |
| `errfatal` | 0 |

**边界**：DNS 通只证明「数据面能走一个 UDP 往返」，不证明 TCP/TLS（后者要真实承载才能试）；`example.com` 的答案可能被运营商 DNS 劫持，但**有答案**本身就足以当连通性证据。插卡当天若看到 `ping: no dns: yes`，那说明 ICMP 被过滤而数据没问题，不是故障。
## 4.24 承载要「守着看」：采样序列与 wedge 的可见性

此前 `kebab-sim-day` 对数据面的判断是**一次性**的：跑完 `pill`/`mhi-diag` 各一次，然后下结论。但本机最要命的数据面故障恰恰是**「先通、后 wedge」**（社区与我们在 Apollo 同族上看到的都是 `~10 MB` 之后停住）。一次性快照分不清两种情况：

- 从头就没通过（`diag=bearer-down`，`ping` 全丢）——问题在呼叫/承载；
- **通过一阵子才停**（`diag=tx-only-suspected-wedge`，`ul_pending_tres` 逼近 128）——问题在 UL 环不再被消费。

改成**按住期间持续采样**（默认每 **15 s** 一次，`--sample-every SEC` 可调，`0` 关闭）：后台子进程每隔一段读一次 `mhi-diag --json`，把 `epoch / verdict / rx_bytes / tx_bytes / ul_pending_tres` 追加进 `samples.tsv`；跑完用纯函数 `sample_trend` 汇总成一行：

```
sample_trend: 37 samples, traffic-flowing -> tx-only-suspected-wedge (1 change(s)), rx 0 B/s tx 1180 B/s
```

三个要点：

1. **先采再睡**，所以哪怕只 hold 十秒也有一个 `t≈0` 基准点可以对照；
2. `samples.tsv` 有**预算**（`HOLD+120 s`）和停止标记两条退出条件，脚本被 SIGTERM 时不会留下一个永久采样的孤儿进程；
3. 采样里一旦出现 `tx-only-suspected-wedge`，`summary.txt` 的 `wedge_seen:` 变 `yes`，**即使最终判定 PASS 也会在 `reason:` 里加一句 `WEDGE SEEN during the hold`** —— 一次「通了但中途卡住」的运行不会被写成「全绿」。

顺带修掉一个证据缺口：`mhi diag` 原本只在上层拿到承载地址时才跑，于是无卡/起呼叫失败时 `summary.txt` 的 `diag:` 是 `unknown`。现在**无论如何都跑一次**（无承载时自然给 `bearer-down`），`no bearer` 与「有呼叫但不动」在证据里从此是两回事。

**验证**（无卡 + 函数级）：

| 项目 | 结果 |
| --- | --- |
| 分支测试 | **30 passed, 0 failed + 2 项链路名回读 = 32 项 PASS**（新增 `sample_trend` 4 条：稳态无变化 / wedge 计为 1 次变化 / 吞吐由字节差推出 / 空序列说 `no samples` 而不是编数字） |
| 采样单元（root，1 s×3） | 3 行真实数据 `bearer-down 0 432 9`，`sample_trend` 汇总正确 |
| 预算退出（`never` 停止文件 + 4 s 预算） | 自行停在 5 个样本，不留孤儿 |
| 无卡 `sim-day` 集成 | `samples.tsv` 至少 1 行、`sample_trend:`/`samples_every:` 两行齐全、`diag: bearer-down`（此前是 `unknown`） |
| 接口残留 / `errfatal` | 零残留、0 |

**边界**：采样只能**看见** wedge，不能预防或恢复它；插卡当天如果看到 `wedge_seen: yes`，按 runbook §4.5 走 QMAP 降级路径（且记住 §4.15 的三道门）。
## 4.25 证据包里要有 RF 上下文；解析器要钉住 locale

两件互相独立、但都会在插卡当天改变结论的事。

### 4.25.1 「没数据」先看驻留在哪个 RAT

WDS 的错误码只说明「呼叫没起来」，不说明为什么。真正常见的两种原因是**驻留 RAT 不对**（掉到 GSM/GPRS 上，或还没上 LTE）和**承载/APN 配置**——只看错误码区分不了。第 11 轮就留过一条可证伪的判据：`--wds-get-channel-rates` 若仍是 0.2 Mbps，说明根本没上 LTE。

现在 `sim-day` 在**等注册之后、起承载之前**抓一份 NAS 快照（写入证据目录的 `nas.txt`，并在 summary 里汇总成一行）：

```
nas: rat=gsm plmn=460/0 state=limited sig=RSSI=-95 dBm     # 无卡时的真实读数
nas: rat=lte plmn=460/00 state=full sig=RSSI=-55 dBm RSRP=-85 dBm ...
```

四个纯函数负责解析（`rat_from` / `plmn_from` / `nas_state_from` / `sig_from`，都从 stdin 读 qmicli dump）：

- `rat_from` 取的是 `Radio interfaces` 下面第一个 `[0]: 'xxx'`，**不是**上面那个计数（`'1'`），这是 qmicli 输出里最容易取错的一处；
- `plmn_from` 只认 `Current PLMN:` 块内的 MCC/MNC（后面还有一个 `Full operator code info` 重复块，取第一个块即可）；
- `sig_from` 把所有 `RSSI/RSRP/RSRQ/SINR` 拍平成 `RSSI=-95 dBm` 这样一行，GSM 只有 RSSI、LTE 有四档，缺失的不补。

### 4.25.2 locale 会静默改掉解析结果

`sim-day`/`status`/`selftest`/`mhi-diag`/`qmap`/`preflight` 里大量 `grep`/`sed` 都在匹配**工具的英文输出**——`ping` 的 `0% packet loss`、qmicli 的 `Card status: present`、`Registration state:`。这些字符串随 locale 翻译，一旦有人用中文/德文 locale 跑（或 `sudo -E` 带上环境），匹配会**安静地失败**，症状是「明明通了却判 FAIL」或「明明有卡却报 absent」。

已在六个脚本的 `set -u` 之后加 `export LC_ALL=C LANG=C`（`qmap.sh` 加在 `set -euo pipefail` 之前）：子进程（`ping`、`qmicli`）继承该环境，输出回到英文，解析不再看运气。设备当前是 POSIX/C，所以这是**防将来**的一步，不是修现状。

**验证**：

| 项目 | 结果 |
| --- | --- |
| 分支测试 | **37 passed, 0 failed + 2 项链路名回读 = 39 项 PASS**（新增 NAS 解析 6 条 + locale 断言 1 条） |
| GSM dump（真实无卡读数） | `rat_from`→`gsm`、`plmn_from`→`460/0`、`nas_state_from`→`limited`、`sig_from`→`RSSI=-95 dBm` |
| 合成 LTE dump | `rat_from`→`lte`；`sig_from`→`RSSI=-55 dBm RSRP=-85 dBm RSRQ=-10 dB SINR=18 dB`（四档都保留） |
| 真机 `sim-day` | `nas: rat=gsm plmn=460/0 state=limited sig=RSSI=-95 dBm`，与 `qmicli` 直读一致 |
| 六个脚本加 locale 后 | `bash -n` 全过；`status`/`selftest`（13/13）/`mhi-diag --json`/`qmap status`/`preflight`（14 项）全部照常 |

**边界**：`nas.txt` 只反映**起承载那一刻**的 RF 状态；采样期间 RAT 可能变化，需要看趋势就用 `samples.tsv`（§4.24）。
## 4.26 承载可用性的第三种证明：TCP 握手与 PMTU

§4.23 把判据从「只能 ping」放宽到「ping 或 DNS」，§4.24 让它看得见 wedge。这两条都还在**无连接的证据层**：ICMP echo 和一条 UDP 查询各自不需要对端维持任何状态。真实的数据使用却是有状态的——运营商侧要存在计费/会话表，NAT 要有映射。所以再加**第三种、也是最强的一种**证明：一条**完成的 TCP 握手**。

### 4.26.1 `tcp-probe.py`

`scripts/linux/kebab-tcp-probe.py`（设备 `/usr/local/lib/kebab-modem/tcp-probe.py`）：`SO_BINDTODEVICE` 绑在承载口上、`connect_ex()` 打 443，输出 `{ok, ms, rc, error}`；`--http` 会再发一条 `HEAD /` 并解析状态行（验证「连接后真有 HTTP 应答」，不涉 TLS）。退出码 0 连通 / 1 失败 / 2 绑定错 / 3 自检失败。

四个纯映射被自检钉住：`status_line()`（`HTTP/1.x` 开头才算，裸 LF 也认，`garbage` 必须返回 None）、`rc_name()`（0→connected、111→refused、113→no-route、101→net-unreachable、**11→timeout**——`connect_ex` 在 Linux 上把非阻塞超时报告成 EAGAIN/EINPROGRESS，若照 errno 表直译会打出 `error-11`，看起来像探针自己坏了）。

`sim-day` 在 DNS 之后按 **`<bearer dns>:443` → `1.1.1.1:443` → `223.5.5.5:443`** 依次试，首个成功即停（`TCP_TARGET` 记下是谁），结果写 `tcp.json`。

### 4.26.2 PMTU：为什么「解析得到、网页打不开」

`sim-day` 再加一条 best-effort 的路径 MTU 检查：按接口 MTU 算 `mtu-28`，用 `ping -M do -s <size>` 打承载网关，成功记 `pmtu: ok 1500`，否则 `pmtu: failed 1500 (df ping lost)`。**这条只在网关肯回 ICMP 时有意义**——本机当前上行（USB 路由器）对所有 ICMP 都不回，`pmtu: unknown` 就是诚实的答案，不代表故障。

意义：MTU 通告错了的典型症状正是「DNS 有应答、小请求能过、大响应卡死」。`DNS ok + tcp 443 通 + df ping 失败` 三者并列，才能把问题指向**分片/MSS**而不是 APN。

### 4.26.3 判定与边界

`bearer_ok(ping, dns, tcp)` 现在三条任一成功即 PASS（函数签名向后兼容：第三参缺省 `no`），`reason:` 会写全三项与命中的 TCP 目标。

**边界（必须记住）**：

- TCP 连上 443 ≠ HTTPS 可用，也 ≠ 有带宽；它只证明**一个 stateful 流能建立**。
- 探针目标在运营商侧可能被劫持/代理（本机当前上行就是这样：`1.1.1.1:443` 3 ms 就「连上」，比真实 RTT 还快，`223.5.5.5` 才是 37 ms 的真实路径）⇒ **`tcp: yes` 的值和 `ms` 一起看**，毫秒级连通要么是本地代理，要么是运营商透明代理。
- PMTU 依赖网关卡 ICMP；`unknown` 不是失败。

**验证**：

| 项目 | 结果 |
| --- | --- |
| `tcp-probe.py --selftest` | **TCP SELFTEST PASS** |
| 负面（`mhi_hwip0` down） | `rc=101 net-unreachable`，0 ms，**立即失败不挂住** |
| 正面（`wlan0` → `223.5.5.5:443`） | `TCP OK ... in 8 ms` rc=0 |
| refused 路径（回环关闭端口） | `rc=111 refused` |
| 分支测试 | **38 passed, 0 failed + 2 项链路名回读 = 40 项 PASS**（新增「TCP 握手单独即可证明承载」） |
| 无卡 `sim-day` | `tcp: no none` / `pmtu: unknown`，总耗时不变，接口零残留 |
## 4.27 wedge 要当场取证：第一次看见就冻结状态

§4.24 让采样能**看见** wedge，但取证还留着一个时间差：所有诊断材料都在**运行结束**时才收集（`probes.txt` 的 `mhi diag`、`dmesg-tail.txt`、`ip -s link`）。而这个故障的形态恰恰是「先通、卡住、然后可能自己恢复或彻底死掉」——等到十分钟后收尾，环指针可能已经动过、MHI 可能已经 reset，留下来的是一份**事后的**状态。

现在采样器**第一次**读到 `tx-only-suspected-wedge` 时，立刻调 `wedge_dump` 冻结现场（同一份 `samples.tsv` 目录下 `wedge-<UTC时间戳>.txt`），并把路径写进 `summary.txt` 的 `wedge_dump:` 行：

| 段落 | 内容 | 为什么在这一刻重要 |
| --- | --- | --- |
| `== mhi states ==` | `/sys/kernel/debug/mhi/mhi1/states` | PM 状态、`M0/M2/M3` 计数、pending packets |
| `== channels ==` | `IP_HW0(100/101)`、`IP_SW0`、`DIAG` 的 `rp/wp/len` | **卡住那一刻**的 UL/DL 环位置 |
| `== counters ==` | `mhi-diag` 单帧（四态判定 + TRE 占用） | 与表格里的 `samples.tsv` 行对齐 |
| `== ip -s link ==` | netdev 的 rx/tx/drop | 判断包是否还在进/出协议栈 |
| `== dmesg tail ==` | 内核最后 30 行 | 有没有 `errfatal`/`Device died` 前兆 |

三条设计取舍：

1. **只写一次**：靠 `<out>/.wedge` 标记文件，避免每 15 s 复制一份同样的大文件；
2. **失败不致命**：`wedge_dump` 整体包在 `|| true` 里，取证坏了不能把采样线程一起带走；
3. **`mhi-diag` 路径带默认值**（`${LIB:-/usr/local/lib/kebab-modem}`），这样被 source 的测试环境里也能跑。

**怎么在没有卡的情况下验证触发器**：`sample_loop` 调的是 `$LIB/mhi-diag.sh`，所以把一个**输出假 wedge 判定的 stub** 放进临时目录、`LIB` 指向它，就能让真实采样代码走完整条「看见 wedge → 建标记 → 落盘」的路，而不碰基带。测试里因此多了两条断言：dump 存在、且**只写一次**。

**验证**：

| 项目 | 结果 |
| --- | --- |
| `wedge_dump` 真机（root） | 4,471 B，含 `mhi states` / 六条通道 / 单帧判定 / `ip -s link` / dmesg |
| 触发器（stub diag，1 s 间隔） | `.wedge` 生成、`wedge-*.txt` **恰好 1 个** |
| 分支测试 | **43 passed, 0 failed + 2 项链路名回读 = 45 项 PASS**（新增 5 条：dump 内容/命名/判定段、触发器、只写一次） |
| 无卡 `sim-day` | `wedge_seen: no` / `wedge_dump: none`，证据目录无残留文件 |

**边界**：dump 只能拍到**那一瞬间**仍存在的东西——如果基带此时已经 `Device died`，debugfs 里的通道信息可能已经消失，`dmesg` 才是主要证据；因此它不替代 §4.15 的 `modem_alive` 与重启判据。
## 4.28 有卡之前先彩排一次「成功路径」

§4.22–§4.27 把**失败**路径几乎都演过一遍：无卡、未注册、承载起不来、wedge、modem 死亡。但**成功路径**（地址就绪 → ping → DNS → TCP → PMTU → `verdict: PASS`）从来没真跑过——插卡当天它会是**第一次执行**，而里面还藏着一条从没被真实字符串验证过的判据：`grep -q ' 0% packet loss'`。

### 4.28.1 把探测段提成函数

`sim-day` 的探测段（78 行，v4/v6 ping、DNS、TCP、PMTU）原先内联在主流程里，无法在别的接口上复用。现在提成 **`probe_bearer()`**（`$IFACE`/`$IP`/`$IP6`/`$OUT` 走全局，内部用 `_if`/`_out` 局部变量），主流程只剩一行 `probe_bearer`。行为零变化（无地址时仍只写 `(no bearer address; probes skipped)`）。

### 4.28.2 `kebab-bearer-rehearse.sh`

新工具用 `SIMDAY_LIB_ONLY=1` source 同一个 `sim-day.sh`，然后**对一条不是 modem 的接口**跑同一个 `probe_bearer`：

```
$ sudo kebab-bearer-rehearse.sh wlan0
iface    : wlan0
addr     : 192.168.1.122/24 / 240c:.../128
ping     : yes
dns      : yes via 223.5.5.5
tcp      : yes 223.5.5.5:443
pmtu     : ok 1500
probes   : 61 lines
result   : REHEARSAL PASS (this is wlan0, not the modem)
```

它**故意不是 `sim-day` 的一个开关**：彩排绝不能把 `verdict: PASS` 写进基带证据目录。工具自己建临时目录、退出即删。

四条由此被真实数据验证过的东西：

1. **`0% packet loss` 的匹配**——判据里风险最高的一条字符串，现在对着真实的成功输出验过；
2. DNS 探针在「有承载」时的完整成功路径（`rcode=0`、`answers=1`）；
3. TCP 探针拿到的 `TCP OK ... in N ms`（`223.5.5.5:443`，与 §4.26 的劫持告警一致：`1.1.1.1` 更快、不可信）；
4. **PMTU 的 `ok` 分支**——`wlan0` 的网关答 ICMP，所以 `-M do -s 1472` 真的通；而本机 USB 上行（`192.168.8.2`）不答 ICMP，那里就诚实地是 `unknown`。两种情形并存，正好说明这个字段该怎么读。

反向彩排：`bearer-rehearse.sh mhi_hwip0`（无承载）→ 立刻 `addr: none / none`、`exit 2`，不挂住。

**验证**：

| 项目 | 结果 |
| --- | --- |
| `rehearse wlan0` | **REHEARSAL PASS**：ping/dns/tcp 全 yes、`pmtu: ok 1500`、61 行探针记录 |
| `rehearse mhi_hwip0` | 无地址 → `exit 2`，**毫秒级返回** |
| 提取后无卡 `sim-day` | 与提取前一致：`ping: no` / `dns: no via none` / `tcp: no none` / `pmtu: unknown` / `diag: bearer-down`、`probes skipped` 恰好 1 行 |
| 分支测试 | **45 passed, 0 failed + 2 项链路名回读 = 47 项 PASS**（新增「探测段是可调用函数」「无地址时跳过而不尝试」） |

**边界**：彩排证明的是**这段代码能跑通、这些字符串能匹配**，不是 modem 能通。基带那一侧仍然只有插卡才能证。
## 4.29 用「假承载」把 SIM 日流程的**中段**跑通，并因此抓到一个假绿灯

§4.28 彩排证明的是**探针段**（`probe_bearer`）能跑通。但 `sim-day` 的中段——**等地址 → 跑探针 → 出 summary → 清理**——依然没有真跑过：无卡时 `data-up` 在 `start` 就退出（rc=3），地址永远不出现，那段代码是「理论上对」。

### 4.29.1 `data-up --fake`：合成的承载

给 `data-up` 加 `--fake`：**不发任何 QMI**，直接用一个编好的 settings（`10.99.13.2/30`、`gw 10.99.13.1`、`mtu 1500`、无 v6）走同一条 `configure_iface → hold → SIGTERM → cleanup` 路径。这样进程生命周期、信号处理器、清理三件事都在真接口上被验证了：

```
fake    : synthetic bearer - no QMI was sent
configured: mhi_hwip0 10.99.13.2/30 gw=10.99.13.1 mtu=1500  default-route=untouched
  holding: 1s … 6s
stop    : skipped (fake bearer, nothing was called)
```

`sim-day --fake-bearer` 把它接进主流程，并在 summary 里写 `bearer_source: fake (no QMI, plumbing test)`，**verdict 强制为 `REHEARSAL`**（既不是 PASS 也不是 FAIL）+ `next_steps` 明说「这只证明流程，不证明数据面」。假承载绝不能写出 `verdict: PASS`。

### 4.29.2 抓到的假绿灯：拿自己的地址当网关

第一次跑就撞上一个**真的判据 bug**。当时的逻辑是「没有默认路由时，退而用接口地址当 ping 目标」：

```
GW=$(... default via ...)
[ -n "$GW" ] || GW=$(ip -4 -o addr show dev $IFACE | awk '{print $4}' | sed 's#/[0-9]*##')
```

而 `ping -I <iface> <自己的地址>` 由**本机协议栈**回答 —— 0% 丢包、`ping: yes`，**一个包都没出去**。假承载没有默认路由，正好走这条路，于是 summary 里出现了：

| 字段 | 假承载首跑 | 真值 |
| --- | --- | --- |
| `ping:` | **yes**（假） | no（223.5.5.5 / 8.8.8.8 都不通） |
| `pmtu:` | **ok 1500**（假） | unknown |

插卡当天若承载**暂时没有默认路由**（初始 PDN 起势那几秒很常见），这就会给出一个「网络通了」的假绿灯，而实际零流量。

修法：新增纯函数 **`gw_candidate(default_gw, own_addr)`** —— 有默认网关才返回它，**等于接口自身地址时返回空**；没有默认路由就不 ping 网关，日志写 `no default route on <iface>; only the public resolvers are pinged`。PMTU 段本来就有 `[ -n "$GW" ]` 守卫，于是自动变成 `unknown`（诚实：没有可测的下一跳）。

顺带修掉一处残留的**重复定义**：§4.28 的提取把 `probe_bearer()` 同时留在了 lib 区和调用点，两个定义（后者生效）——功能上没坏，但改动会打到「不生效的那份」上。现已删掉重复，`grep -c '^probe_bearer() {'` = 1。**这是本轮第二个靠「真跑」才发现的问题**。

### 4.29.3 另一处真改进：cleanup 现在恢复 MTU

`cleanup_iface()` 原本只删地址、置链路 down，**MTU 留在承载值 1500**。于是任何一次「跑过承载」之后，`mhi_hwip0` 的 MTU 就永远不再是驱动默认的 16384，而我们多处用「MTU 16384 / 无地址 / DOWN」当作「零残留」的证据——那条证据会**静默失真**。现在配置前记下原 MTU、cleanup 时写回（实测 `16384 → 1500 → 16384`）。

### 4.29.4 验证

| 项目 | 结果 |
| --- | --- |
| `data-up --fake --hold 6` | 配置→按住→清理全程；地址与 MTU 归零 |
| `sim-day --fake-bearer --hold 20` | `verdict: REHEARSAL`、`bearer_source: fake`、`addr: 10.99.13.2/30`、`sample_trend: 3 samples`、收尾零残留 |
| 修自 ping 后同一条命令 | `ping: no` / `pmtu: unknown` / 日志 `no default route on mhi_hwip0` / probes 只剩两个公共解析器 |
| `gw_candidate` 单测 | 3 条：真网关保留、自身地址拒绝、无默认路由返回空 |
| 分支测试 | **48 passed, 0 failed + 2 项链路名回读 = 50 项 PASS** |
| `selftest`（wds） | 仍 **SELFTEST PASS** |

**边界**：假承载证明的是**流程与清理**，不是数据面。它不会也不能替代真卡上的 `addr:`、`ping:`、`dns:`、`tcp:` 四个字段。
## 4.30 「modem 死了」和「wedge 了」是两回事，而且要记下**什么时候**死的

§4.27 的 `wedge_dump` 只覆盖「链路口还在、但不动」。真正更糟的形态是**基带整个消失**（§4.15 那次：`Device died` → `Recovery failed: -25` → PCI 上没了）。这种时候 `samples.tsv` 帮不上忙 —— 死后的每一行都只是普通的 `bearer-down`，**从表格里看不出是哪一秒死的**，而 `dmesg` 到收尾时早被后续日志淹掉了。

### 4.30.1 采样器多看一眼 sysfs

`sample_loop` 每轮除读 `mhi-diag` 外，还检查承载口在不在（`${KEBAB_SYSFS_NET:-/sys/class/net}/$IFACE`；路径可覆盖，测试才能在不碰真机的情况下跑这条分支）。第一次发现不在就：

1. 把当前 epoch 写进 `<out>/.modem-gone`（时间戳，只写一次）；
2. 调同一个 `wedge_dump` 落盘 `modemdied-<UTC>.txt` —— 里面有 **那一刻的** `mhi1/states`、通道 rp/wp、`ip -s link`、**dmesg 末 30 行**。

summary 相应多两行：`modem_gone: yes|no`、`modem_gone_at: +37s`（相对第一个样本）。

### 4.30.2 建议的优先级

`next_steps` 现在按这个顺序判：**modem 消失 → 假承载 → 其它（`derive_next`）**。理由很直接：基带不在时，任何「改 APN / 等注册 / 切 QMAP」的建议都是错的，唯一的动作是 **reboot**（host 侧 rescan/unbind/reset 全部实测救不回，见 §4.15）。这条优先级本身也进了测试（比较两个分支在源码里的行序，而不是只 grep 存在）。

### 4.30.3 接这条线时抓到的一个 bug

`samples.tsv` **第一行是表头**（`epoch verdict rx_bytes tx_bytes ul_pending`）。取「第一个样本的时间」时写成了 `awk 'NR==1{print $1}'`，拿到的是单词 `epoch` —— 于是 `modem_gone_at:` 打印成**空**（端到端跑出来的现象就是 `modem_gone: yes` 但时间空白）。已抽出纯函数 **`first_epoch()`**（取第一个纯数字行）并补 3 条断言：跳过表头、只有表头时返回空、文件不存在时返回空。

### 4.30.4 验证

| 项目 | 结果 |
| --- | --- |
| 端到端（`KEBAB_SYSFS_NET=/nonexistent` + `--fake-bearer`） | `modem_gone: yes` / `modem_gone_at: +0s` / `modemdied-*.txt` 生成 / `next_steps` 指向 reboot |
| 常规无卡跑 | `modem_gone: no`，verdict 仍是 FAIL/no SIM（没被误报） |
| 采样器分支单测 | 用 stub diag + 不存在的 sysfs 目录：`.modem-gone` 有内容、`modemdied-*.txt` **恰好 1 个** |
| `elapsed_since` | 3 条：正常、首个时间缺失、时钟倒退 |
| `first_epoch` | 3 条：跳过表头、只有表头、文件不存在 |
| 分支测试 | **56 passed, 0 failed + 2 项链路名回读 = 58 项 PASS** |

**边界**：判据是「sysfs 里那个网口还在不在」。MHI 正在 `Recovery` 但仍占着 sysfs 的中间态**不会**触发它 —— 那属于 wedge，由 §4.27 的 `wedge_dump` 覆盖；两者互补，不重叠。

### 4.30.5 顺带：社区仓库复查

`git fetch` 社区的 `jfbobier/Oneplus-8T-kebab-pmos-bringup`，**上游没有新提交**（最后一条仍是 2026-10-05 的 `linux_upstream: rebase against msm-next`），本地与 `origin/HEAD` 相差 0。也就是说自 §4.19/§4.21 的交叉核对以来，社区没有新的数据面证据可借；我们的结论（只有物理卡这一道门）依然成立。
## 4.31 文档与设备对一遍：把「runbook 里敲的命令」当成可测试的断言

前面几十轮把**代码**越测越硬，但还有一个从没被检过的对象：**文档本身**。runbook 是插卡当天唯一照着做的页面，它引用的一切（命令名、子命令、单元、它启动的脚本）都要在那天真实存在。读一遍页面**证明不了**这件事 —— 眼睛会跳过不存在的名字。

### 4.31.1 `kebab-runbook-lint.py`

把 markdown 里的命令抽出来逐个核实：

| 抽取来源 | 判定 |
| --- | --- |
| 围栏代码块里的每一行 | **硬检查**：程序必须在 root 的 PATH（文档一律写 `sudo`）或 `/usr/local/{sbin,bin,lib/kebab-*}` 里 |
| 行内 `` `sudo ...` `` 或带参数的行内命令 | 硬检查，另外确认**子命令**在程序正文里出现过（`qmap linktest`、`format-set` 之类） |
| 行内裸名字（`kebab-qmap-tun-7` 这种载荷标记、`kebab-gps` 这种单元） | 软提示：能按单元解析就解析，否则只报 `mention` |
| 明确「讨论但故意不装」的名字 | 白名单，**每条都要写理由**（目前只有社区的 `kebab-uim-provision`，我们用 `sim-provision.sh` 取代） |
| `/etc/systemd/system` 下**所有**已安装的 `kebab-*` 单元 | 每个 `ExecStart` 必须可执行 |

多命令行先说按 `;` `&&` `||` 切开，再剥掉 `sudo`/环境赋值前缀 —— 这两条规则被 `--selftest` 钉住（3 条前缀 + 3 条切分），否则 `qmap down; qmap unload` 那一行会被当成一个叫 `down;` 的子命令。

### 4.31.2 第一次跑就抓到一个真的安装缺口

`kebab-mm-restart-once`（给 DKMS/驱动晚到时重拉 MM 的一次性助手）在 runbook 里的安装步骤只有：

```
sudo install -m 644 config/mm/kebab-mm-restart-once.service /etc/systemd/system/
```

但这个单元的 `ExecStart=/usr/local/lib/kebab-mm/kebab-mm-restart-once.sh`，而**那个脚本从来没被装过**（设备上 `/usr/local/lib/kebab-mm/` 里只有 `ModemManager-patched`、`dropins`、`patches`、两个工具）。照文档做会得到一个**永远起不来的单元**，`enable` 时才会暴露。已改成两行安装（单元 + 助手，`0755`），并在 linter 里加了「已安装单元的 ExecStart 必须存在」这条通用检查 —— 它以后能自动抓同一类问题。

### 4.31.3 pre-flight 也补上了「工具是否都在」

同一个失效模式在**设备侧**也该有守卫：preflight 从 14 项扩到 **26 项**，新增两组 ——

- **3b**：runbook 会敲的 9 个工具逐个存在（`kebab-sim-day`、`kebab-modem-mhi-diag`、`kebab-modem-qmap`、`tx-probe.sh`、`dns-probe.py`、`tcp-probe.py`、`bearer-rehearse.sh`、`runbook-lint.py`、`test-sim-day.sh`）；
- **3c/3d**：QMAP 备选就绪（`rmnet.ko` + `qmap-local-test.py`）、打补丁的 MM 二进制在位。

这样插卡前跑一条 preflight 就能发现「工具没装」，而不是在数据面失败之后才发现。

### 4.31.4 验证

| 项目 | 结果 |
| --- | --- |
| lint 两份文档 | runbook 24 处引用 / MM 文档 27 处，**RESULT: PASS**（0 missing） |
| 已安装单元扫描 | 57 个 `kebab-*` 单元，`ExecStart` 全部可执行 |
| `--selftest` | 6 条（前缀剥离 3 + 命令切分 3）**RESULT: PASS** |
| 首跑发现的缺失 | `kebab-mm-restart-once.sh` 未安装 → runbook 已改两行安装 + linter 新增单元检查 |
| pre-flight | **26 passed, 0 warnings, 0 failed**（原 14 项） |

**边界**：lint 只能证明「名字存在、子命令字样出现在程序里」，**不能**证明参数组合正确 —— 参数级别的正确性仍然只能靠 §4.28 的彩排和 §4.29 的假承载跑一遍。
## 4.32 收尾的最后一手：被 SIGKILL 的 data-up 留下的地址由谁来清

`data-up` 的正常收尾是可靠的（§4.29 已验：地址、MTU、链路都回原状）。但收尾本身也可能**跑不到**：进程卡在一次 QMI 读上、或者 wedge 时被 `sim-day` 的 10 s 等待判定为无响应而 `SIGKILL`（实测退出码 **137**）。这时地址还挂在承载口上 ——

- 下一次 `data-up` 会看到「接口已经有地址」，行为不再是干净的起点；
- ModemManager 若在它之后拿到承载，会面对一个被我们自己配过的 `mhi_hwip0`；
- 而所有「零残留」的判断（`addr=[] mtu=16384 state=down`）都会**变成假话**。

### 4.32.1 `residue_cleanup(iface, orig_mtu)`

停呼叫之后、还原 QMAP 之前加一道守卫：接口上还有 IPv4 地址就删掉、链路置 down、MTU 写回**运行开始前**的值，并打印 `removed <addr>`（干净时什么也不打印）。summary 多一行 `residue_after_stop: none|removed 10.99.13.2/30`，日志同时留一条。

**一个自己踩的时序坑**：`ORIG_MTU` 第一版是在「等地址」的循环之前读的 —— 那时 `data-up` **已经把 MTU 改成 1500 了**，于是守卫「还原」成 1500，看起来修好了、其实留了个更隐蔽的残留。已改成**参数解析后、任何东西碰接口之前**读（第 472 行），并在真机上验证：`16384 → SIGKILL → 16384`。

### 4.32.2 验证

| 项目 | 结果 |
| --- | --- |
| veth 单测（真实接口） | 有地址 → `removed 10.97.0.1/24` 且确实消失；干净接口 → 无输出；接口不存在 → 无输出 |
| 端到端 SIGKILL | `kill -9` data-up（`dataup_rc: 137`）→ `residue_after_stop: removed 10.99.13.2/30`、日志同句、接口 `addr=[] mtu=16384 state=down` |
| 正常收尾 | 守卫不触发，`residue_after_stop: none`，与之前行为一致 |
| 分支测试 | **59 passed, 0 failed + 2 项链路名回读 = 61 项 PASS**（新增 3 条守卫断言） |

**边界**：守卫只处理 **IPv4 地址 + 链路 + MTU**，不重建 QMAP 状态、不重设 IPv6（`data-up` 被 KILL 时 v6 也是它装的，但 v6 残留不影响下一次 v4 起始判断；真要清可以手工 `ip -6 addr del`）。它是一条**兜底**，不是正常路径的替代。
## 4.33 `--hold` 从来没有真的 hold 住：一次 300 秒 soak 暴露的两个 bug

这一轮的起点只是「跑一次长一点的 sampled run，看看流水线稳不稳」。结果第一个 300 秒 soak **82 秒就结束了**，`samples.tsv` 只有 3 行 —— 顺着查下去，是两个真 bug 和一个由此产生的假象。

### 4.33.1 bug 一：`--hold` 只被当成「等地址的超时」

`HOLD` 在全脚本只出现在两处：采样器预算（`HOLD+120`）和等承载地址的 `DEADLINE`。地址在 **1 秒**内就出现了，循环立刻 `break`，于是紧接着跑探针、停呼叫 —— **呼叫只活了 1 秒**。文档里写的「按住期间每 15 s 采样」「`--hold 180` 看看稳不稳」全都名不副实：它从来没有按住过。

改法：地址出现后**先探一次**（`probes.txt` 里记 `### probes t=0`），然后真的按住到 `HOLD` 秒（每 2 秒看一次接口与 data-up 是否还在，消失就提前结束），**再探一次**（`### probes t=<held>s`），最后才停呼叫。summary 新增三行：

- `held: <n>s` —— 实际按住了多久（`HOLD=0` 时是 0）；
- `probes_at_start: ping=… dns=… tcp=… pmtu=…` —— 刚起来时的探针结果；
- 结束那一组探针就是原来的 `ping:`/`dns:`/`tcp:`/`pmtu:`。

这样「通了但中途坏掉」第一次可以被看见：`ping: no` 不再一定意味着「从来没通」——`probes_at_start` 会告诉你它一开始是好的。

**顺带修掉一个必须一起改的地方**：`PING_OK` 原本在 verdict 段才从**整个** `probes.txt` 里 grep `0% packet loss`，即「文件里任何一次成功」—— 如果 t=0 成功、结束时失败，它仍然是 yes，判定会 PASS。现在 `PING_OK` 由每次 `probe_bearer` 自己算（进函数时数一次成功行数、出函数时再数一次，只有本次新增才算成功），所以它描述的是**这一段**。并在 `reason:` 里加一条：**开始成功、结束失败**时写 `BEARER LOST during the hold (worked at t=0, dead at t=Ns)`。

### 4.33.2 bug 二：脚本自己死在半路时，`data-up` 没人停

第一次改完重跑，脚本在 `set -u` 上报错退出（当时 `PROBE0_PING=$PING_OK` 里的 `PING_OK` 还没定义）—— 于是暴露了更严重的一件：**`data-up` 进程留在了后台，继续握着呼叫、地址和 MTU**。设备上查到的直接证据是两个这样的孤儿进程（分别来自两次崩溃的运行），以及 `mhi_hwip0` 的 MTU 停在 **1500**：后来那次「干净」的 300 秒 soak 的 `data-up` 启动时读到 1500，收尾时便「恢复」成 1500 —— **一次崩溃会污染后面每一次运行的基线**。

改法：把原来那行只做 QMAP 还原的 `trap 'qmap_restore' EXIT INT TERM HUP` 换成 `abort_cleanup`，它按顺序做四件事：停 data-up（SIGTERM → 等 5 s → SIGKILL → `wait`）、停采样器、`residue_cleanup` 清地址与 MTU、`qmap_restore`。**挂在 EXIT 上**是关键：bash 的致命错误会走 EXIT trap，所以 `set -u` 这种崩溃也会被兜住。

`residue_cleanup` 也补了一处：原来「没有地址就直接返回」，可 **MTU 本身就是残留**（地址已被 data-up 清掉、MTU 却留在 1500 的情况真的发生过）。现在无论有没有地址都会比对并写回运行前记录的 MTU。

### 4.33.3 三个真机验证

| 场景 | 做法 | 结果 |
| --- | --- | --- |
| 正常按住 | `--hold 20 --sample-every 5` | `held: 20s`、19 个样本、`probes.txt` 两个 `### probes` 段 |
| 人为崩溃 | 复制脚本并在 hold 之后插 `: "${CRASH_NOW:?}"` | rc=2，日志 `abort: stopping data-up (SIGTERM then SIGKILL)`，**零孤儿**、`addr=[] mtu=16384` |
| 中途 SIGTERM | 对脚本发 TERM | data-up 与采样器都退出，`addr=[] mtu=16384 state=down`，脚本仍把 summary 写完 |
| 300 秒 soak | `--hold 300 --sample-every 15` | 见下表 |

300 秒 soak（修好之后、基线 `mtu=16384` 且零孤儿）：`held 301s of 300s requested`、**25 个样本 / 间隔恒 15 s**、`### probes` 两段、`sample_trend: 25 samples, idle -> idle (0 change(s))`、`residue_after_stop: none`、结束时 `addr=[] mtu=16384`、孤儿 0、`errfatal` 0。

### 4.33.4 分支测试

**61 passed, 0 failed + 2 项链路名回读 = 63 项 PASS**（新增「MTU 残留也要还原」（veth 上真的设 9000 再验回 1500）与「abort trap 必须在位」两条）。**一个自己踩的坑**：新加的那条 grep 直接写了 `"$LIB/sim-day.sh"`，而测试脚本有 `set -u` 且 `LIB` 只在别处以 `${LIB:-…}` 形式用过 ⇒ 未绑定变量让整个测试**静默中止**，输出停在 veth 那三条之后；改成 `${LIB:-/usr/local/lib/kebab-modem}` 后正常。这已经是同形状的第四次（`set -u` + 未初始化变量），已记在案。

### 4.33.5 对插卡当天的意义

- `--hold N` 现在是**真的**把呼叫按 N 秒，soak/证据包才有意义；默认 120 s，长稳测试直接 `--hold 600`。
- 任何异常退出（Ctrl-C、脚本 bug、systemd 超时）**都不会留下活着的承载**——插卡当天崩溃一次不会顺带烧掉流量配额，也不会污染下一次的基线。
- `probes_at_start` 与结束探针的对比，是「先通后 wedge」最直接的证据；配合 `samples.tsv` 与 `wedge_dump`，三种失效形态（从头不通 / 通一阵后停 / 接口消失）各有自己的字段。
## 4.34 证据包要能自证：`SHA256SUMS` 从「写在最后」变成「封完自己读一遍」

`kebab-sim-day` 每轮都把 `preflight.txt / status-*.txt / dataup.log / probes.txt / samples.tsv / dns.json / tcp.json / nas.txt / dmesg-tail.txt / summary.txt / run.log` 打进一个目录，末尾生成 `SHA256SUMS`。但**没有任何东西验证过这份清单**，而它要防的正是最要命的一种失效：**采集过程把证据写残了**（磁盘满、进程被杀、文件被截断）—— 一份被截断的 `probes.txt` 读起来和「真的没有数据」**一模一样**，而整台机器的结论就建立在它上面。

### 4.34.1 `seal_pack()`：封 + 验 + 留证

```
find "$OUT" -type f ! -name SHA256SUMS | sort | xargs sha256sum > SHA256SUMS   # 第一次封
sha256sum -c --quiet SHA256SUMS                                                # 读回来验
manifest-check.txt  <- "manifest check: ok (N files)" + 验证时间             # 结果留证
find ... > SHA256SUMS                                                          # 再封一次，把上面这份也盖进去
```

dry-run 与正常结束两个出口都走它；失败时 `MANIFEST_BAD=1` 参与退出码（证据不可信就不该返回 0）。

**一个自己踩的顺序 bug（第一次跑就暴露，值得记）**：`seal_pack` 里原本把 `log "manifest: ok"` 放在**最后一次 `find` 之后**——而 `run.log` **本身也在包里**，于是每次运行的结果都是 `run.log: FAILED`，`sha256sum -c` rc=1。也就是说：**封装动作本身不能被封装进去**。修法是把 `log` 移到最终 `find` 之前；结尾那句告警也从 `log`（写 run.log）改成 `printf`（只上屏），因为封完之后**任何**写 run.log 的动作都会让包失效。这件事改完才让「包能自证」成立，第一次的运行是**验证失败**的，正好说明这道检查有用。

### 4.34.2 顺带堵住磁盘这一路

起跑时读一次 `df -Pm "$OUT"`：正常 `disk: 7823 MB free under $OUT`；低于 20 MB 时打 `WARNING only N MB free ... the evidence pack may be truncated`；读不到就 `could not read free space`。这不是错误处理，是**让「证据为什么不全」有账可查**。

### 4.34.3 验证

| 项目 | 结果 |
| --- | --- |
| dry-run 包 | `manifest check: ok (4 files sealed before this note)`，外部 `sha256sum -c SHA256SUMS` **ok** |
| 完整假承载包 | `ok (13 files)`，外部复核 **ok** |
| 清单完整性 | 目录里每个文件都在 `SHA256SUMS` 里（逐个 awk 反查，0 缺失） |
| 顺序 bug | 修复前 `run.log: FAILED`（rc=1）；修复后全 OK |
| 分支测试 | **63 passed, 0 failed + 2 项链路名回读 = 65 项 PASS**（新增 dry-run 包自验 + `manifest-check.txt` 两条，都会真的跑一次 dry-run） |

**边界**：自验只能证明「文件与清单一致」，**不能**证明内容正确或采集完整（比如 dmesg 只取末 40 行）。它挡的是**传输/落盘损坏**这一类，不是判断错误。
## 4.35 「先通后坏」要有自己的建议：`bearer_lost` 与 `next_steps`

上一轮让证据包能说出「起来时通、按住期间坏了」（`probes_at_start` + `bearer_lost` + `reason:` 里的 `BEARER LOST`）。但**建议**那一行还是从旧的分支里挑的：

- 承载死掉后接口上往往**还留着地址**（`data-up` 已退出、地址是它装的），于是 `NEVER` 走不到「没有地址」那几条；
- `diag` 这时多半是 `idle`，落到最后那条兜底分支 ——「先看路由 / 看 RX」，而**路由根本不是问题**。

于是写了一个纯函数把这两种失败分开：

```
bearer_lost <start_ok yes|no> <end_ok yes|no>   # 只有「开始 yes + 结束 not yes」才是 yes
```

`derive_next` 增加第 10 个参数 `_lost`，并且这条分支**排在 APN/路由那几条之前**，但**要求 `_ip` 非空**（「我们确实拿到过承载地址」才用这条；连地址都没有时，未注册的读数更有解释力）：

| 场景 | `next_steps` 现在说什么 |
| --- | --- |
| 有地址 + 先通后坏 | 「承载起来时是通的（probes_at_start），按住期间坏掉了 —— 这是「先通后 wedge」，不是 APN/路由问题：先看 samples.tsv 与 wedge_dump，再按 §4.15 切 QMAP。别去改 APN。」 |
| 无地址 + 未注册 | 仍然先说「还没注册，先等到 registered」（这条压过 lost，见上） |
| 有地址 + 从未通 | 原来的路由/ICMP 排查 |

summary 同时多一行 `bearer_lost: yes|no`，让「这个判断做过没有」可查。

**验证**：`bearer_lost` 四种组合各一条（(no,no)/(yes,no)/(yes,yes)/(no,yes)）；`derive_next` 两条 —— 一条要求「先通后 wedge」与 `samples.tsv` 同时出现，一条要求 `not-registered` **压过** lost（带地址才走 lost，无地址时注册读数优先）。分支测试增至 **71 passed 0 failed + 2 项链路名回读 = 73 项 PASS**。

## 4.36 MTU 基线不能取「现在是多少」：一次残留会把 1500 永久固化

第 50 轮给 `residue_cleanup` 加了「没有地址也把 MTU 写回运行前记录的值」。本轮按「上一轮崩溃留下的垃圾」做真机演练时发现它还漏了一环：**那个「运行前记录的值」本身可能就是残留**。

实测：手工作出 old-run 的样子（`ip addr add 10.99.13.2/30` + `mtu 1500`），然后正常运行一次 —— 收尾后 `addr=[]` 但 **`mtu=1500`**：`data-up` 读到 1500 就「恢复」成 1500，而 `sim-day` 的守卫记录的也是 1500，两边都不报错。**一次崩溃 → 之后的每一次运行都把 1500 当基线 → 永久固化，而「零残留」的断言看起来还是对的。**

改法：目标值用**驱动默认值**而不是当前值。`mhi_net` 起接口时是 `0x4000`（16384），所以：

```
BASE_MTU=${KEBAB_BEARER_MTU:-16384}
ORIG_MTU=$(cat /sys/class/net/$IFACE/mtu)
[ "$ORIG_MTU" != "$BASE_MTU" ] && log "baseline: mtu=$ORIG_MTU but the driver default is $BASE_MTU - looks like residue …" && ORIG_MTU=$BASE_MTU
```

summary 多一行 `baseline_mtu:`（守卫会还原到哪个值），异常时日志里明写一句「看着像残留」。`KEBAB_BEARER_MTU` 可覆盖（换驱动/换口时的逃生门）。**一个自己踩的落地顺序坑**：这段最初插在 `log()` 定义**之前**，于是 `log` 报 `command not found`（`set -u` 不致命），功能看似正常、日志却永远缺那行 —— 挪到 `log "out: $OUT"` 之后就对了。

**验证**（全部真机）：

| 场景 | 结果 |
| --- | --- |
| 残留基线（addr + mtu 1500） | 日志 `baseline: mtu=1500 but the driver default is 16384 …`、summary `baseline_mtu: 16384`、收尾 `residue_after_stop: mtu 1500 -> 16384`、`addr=[] mtu=16384` |
| 干净基线 | 日志里没有 baseline 行，`residue_after_stop: none` |
| 覆盖 | `KEBAB_BEARER_MTU=9000 --dry-run` 会打 baseline 警告，且 dry-run 仍然 `verdict: DRY-RUN`（不碰接口） |
| 分支测试 | **73 项 PASS**（新增上面两条 dry-run 断言） |

**边界**：这只修「我们自己留下的 MTU 残留」。若有人**故意**把 bearer 口设成非默认 MTU，下一次运行会被改回 16384；要保留就设 `KEBAB_BEARER_MTU`。
## 4.37 一次只能跑一个：`sim-day` 的单实例锁，以及它暴露的两个「顺序」bug

### 4.37.1 为什么必须锁

`sim-day` 会独占 `mhi_hwip0`（地址、MTU、链路状态）和那条 WDS 呼叫。从两个终端各跑一次是很自然的操作（「刚才那个是不是卡住了，我再跑一个」），结果是两次 `data-up` 抢同一个端点、两个守卫互相「还原」对方刚写下的地址/MTU，**产出的两份证据都解释不了自己**。

实现用 `flock`，锁文件 `/run/kebab-sim-day.lock`：

```
exec 9>>"$LOCK"                     # 注意是 >> 不是 >
if flock -n 9; then
  LOCK_ACQUIRED=1; : >"$LOCK"; printf 'pid %s since %s\n' "$$" "$(date -u …)" >"$LOCK"
else
  log "refusing to start: another kebab-sim-day is already running (pid N since T)"
  exit 3
fi
```

拿不到锁就**不开始采集**（退出码 3），并把持有者的 pid 与启动时间打出来；退出时（正常、`--dry-run`、异常 trap）都把 pid 清掉，避免「文件里有 pid 但其实没人跑」把下一次运行吓住。

### 4.37.2 两个 bug：都是「在函数定义之前用了它」

**① `>` 截断了持有者的 pid。** 第一版用 `exec 9>"$LOCK"` —— 第二个实例一打开就**把文件截断**，于是它报的「谁在占用」永远是空的，而且第一个实例记下的 pid 被抹掉。改成 `>>`（追加打开）+ 拿到锁之后再显式截断。实测现在是 `refusing to start: another kebab-sim-day is already running (pid 101842 since 2026-11-11T23:44:51Z)`。

**② 更严重的一个：`log()` 之前就有代码在 `log`。** 第 51 轮加的磁盘检查块落在 `log()` 定义**之前**，于是每次运行的 `log "disk: … MB free"` 都是 `log: not found` —— 不致命（只有 `set -u`），**磁盘行从来没进过 `run.log`**，而当时的验收只看了 `manifest:` 那一行，没注意少了一行。第 52 轮的 MTU 基线块犯了同一个错（已修），这轮是第三次。

根治办法不是再挪一次：把 `log()` 的定义**提到 `OUT` 建好之后、任何会打日志的代码之前**，并在注释里写明原因。现在 `log()` 在第 504 行，第一条 `log` 调用在第 511 行，代码里再新增早期日志块也不会踩到。

**能防住这一类的东西**：新的断言不检查「disk 行存在」这么具体的事，而是检查 **dry-run 的 stderr 必须为空**——任何「在定义前调用」的写法都会在这里冒出来；再补一条「disk 行确实进了 run.log」。

### 4.37.3 验证

| 项目 | 结果 |
| --- | --- |
| 第二实例 | `rc=3`，输出 `refusing to start: another kebab-sim-day is already running (pid … since …)`，并提示「如果那是僵尸：`sudo rm -f /run/kebab-sim-day.lock`」 |
| 锁文件 | 运行中 `pid 98709 since …`；退出后 **size=0**（dry-run 与完整运行都验过） |
| 释放 | 持有者结束后第三次运行正常开始 |
| 早期日志 | dry-run `stderr` 为空、`run.log` 有 `disk: 5804 MB free under /tmp/…` |
| 分支测试 | **76 passed, 0 failed + 2 项链路名回读 = 78 项 PASS** |
| 残留 | 结束时 `addr=[] mtu=16384`、data-up 孤儿 0 |

**边界**：锁只防同一台机器上的并发 `sim-day`；它不管 ModemManager（MM 自己起承载是另一回事，summary 里的 `mm:` 行就是给这种情况留的）。
## 4.38 默认路由必须带 metric：插卡当天最容易把「服务器搞下线」的一步

### 4.38.1 问题

`data-up --route` 原本执行的是 `ip route replace default via <gw> dev <iface>` —— **没有 metric**。这台机器是服务器：

```
default via 192.168.8.2 dev usb0  proto static metric 100
default via 192.168.1.1 dev wlan0 proto dhcp   metric 600
```

两条上联的 metric 都非零，而 modem 那条会是 **metric 0** —— **最低 metric 直接获胜**。插卡当天一条 `--route` 就能把整台机器的出口切到一条刚起来的蜂窝链路上：SSH 可能断、`kebab-dsh`/`kebab-sillytavern` 等常驻服务对外全断，而现象看起来像「modem 一插就不稳定」。反面也一样危险：metric 相同（都写 0）时内核按 FIB 顺序选，行为随插入顺序变化，**不可预测**。

### 4.38.2 改法

把路由命令抽成纯函数、并强制带 metric：

```
route_cmds(iface, gw, metric, v6)      -> [["ip","route","replace","default","via",gw,"dev",iface,"metric",str(metric)], …]
route_del_cmds(iface, gw, v6)          -> 与安装一一对应的删除命令
```

- `--route-metric`，**默认 1000**（比两条上联都大 ⇒ 只当备份，永远不抢出口）；
- `--route-metric 0` **被 argparse 直接拒绝**（`--route-metric must be >= 1`），因为 0 正是要避免的那个值；
- 安装时把真正执行的命令打出来：`route   : ip route replace default via 10.99.13.1 dev mhi_hwip0 metric 1000`；
- `--dry-run` 里也带上 metric：`+ default route metric=1000`。

### 4.38.3 顺带修掉一个「假 dry-run」

验证时发现 `--fake --dry-run --route` **真的去配了接口**（打了 `configured: …` 并执行 `ip route replace …`）：`fake_bearer()` 从来没看过 `args.dry_run`。这是最坏的一类 bug —— 一个**声称不碰任何东西**的开关，在服务器上装了条默认路由。现在 `--dry-run` 在 `fake_bearer()` 的第一行就返回：

```
route   : would run ip route replace default via 10.99.13.1 dev mhi_hwip0 metric 1000
dry-run : would configure mhi_hwip0 10.99.13.2/30 gw=10.99.13.1 mtu=1500  + default route metric=1000
dry-run : nothing was touched (no QMI, no interface change)
```

同时给 fake/real 两条路都加了 `SIGPIPE` 忽略：输出被 `| head` 截断时得到的是异常（`finally` 里的清理照跑），而不是进程被信号打死、把地址和 1500 的 MTU 留在接口上。

### 4.38.4 证据包里也要能看出来

`sim-day` 收尾写 `routes.txt`（`ip route show default` 原文），并在 summary 加两行：

```
default_routes: usb0/100 wlan0/600
bearer_default_route: no
```

解析是纯函数 `default_routes_from()`（`dev/metric` 成对压缩，无 metric 记 0）与 `bearer_default_route()`（数一下有多少条默认路由指向承载口）。**健康的一轮永远是 `bearer_default_route: no`** —— `sim-day` 本身从不装默认路由；若哪天这里变成 `yes`，就说明有别人（多半是自己手跑的 `--route`，或者被启用的 MM multiplex）动了出口。

### 4.38.5 验证（全部真机）

| 项目 | 结果 |
| --- | --- |
| 路线命令 | `ip route replace default via 10.99.1.1 dev mhi_hwip0 metric 1000`；v6 同；删除命令与安装一一对应（selftest 5 条断言） |
| `--route-metric 0` | `error: --route-metric must be >= 1 (0 would tie with the server uplink)` |
| dry-run | 路由条数 2→2、MTU 16384→16384、无地址 ⇒ **零副作用** |
| fake 实跑 | 期间 `default via 10.99.13.1 dev mhi_hwip0 metric 1000` 在表内；`--hold-seconds 4` 结束后**route 0 条、MTU 16384、addr 空、DOWN**，两条上联原样 |
| sim-day | `default_routes: usb0/100 wlan0/600` / `bearer_default_route: no` / `residue_after_stop: none` |
| 分支测试 | **79 passed, 0 failed + 2 项链路名回读 = 81 项 PASS** |

**给插卡当天的建议**：先用 `--dry-run` 看那条命令，再加 `--route`；加完立刻 `ip route get 223.5.5.5` 确认走的还是 **usb0/wlan0**（`metric 1000` 的效果），只有在**确实想**让蜂窝当出口时才 `--route-metric 50` 之类地手动压过。
## 5. ModemManager 已装并实测：控制面完全可用（2026-11-13）## 5. ModemManager 已装并实测：控制面完全可用（2026-11-13）

§5 原来是「为什么本轮不装」。这一轮装了 —— 因为**无卡也能验证控制面**，而控制面是否可用决定插卡当天要不要走 MM。

### 装了什么

```
sudo dpkg -i modemmanager_1.24.0-1+deb13u1_arm64.deb polkitd_126-2_arm64.deb
# polkitd 又依赖 xml-core(>=0.19)，xml-core 依赖 sgml-base：
sudo dpkg -i sgml-base_1.31_all.deb xml-core_0.19_all.deb
sudo dpkg --configure polkitd modemmanager
```

**§1 的依赖清单漏了两项**：`dpkg --no-act -i` 不会递归解析，`polkitd` 要 `xml-core`、`xml-core` 要 `sgml-base`。两个都是 `_all` 小包，用 `curl --interface wlan0 --resolve deb.debian.org:443:151.101.2.132` 直接拉（各 15–20 KB），实测 200。

### 结果：MM 完整识别了这个 modem

```
$ mmcli -L
    /org/freedesktop/ModemManager1/Modem/0 [QUALCOMM INCORPORATED] 0
$ mmcli -m 0
  Hardware |      manufacturer: QUALCOMM INCORPORATED
           |      firmware revision: Q_V1_P14
           |           current: cdma-evdo, gsm-umts, lte, 5gnr
  System   |            device: qcom-soc
           |           drivers: qrtr, mhi_net
           |            plugin: qcom-soc
           |      primary port: qrtr3
           |             ports: mhi_hwip0 (net), mhi_swip0 (net), qrtr3 (qmi)
  Status   |             state: failed
           |     failed reason: sim-missing
```

`sim-missing` 正是无卡应有的结论。**两条 udev 规则生效**：`wwan0qmi0` 带 `ID_MM_PORT_IGNORE`（那个 10 s 超时的死端口没被浪费时间），`mhi_hwip0`/`mhi_swip0` 带 `ID_MM_QCOM_SOC=1` + `ID_MM_PHYSDEV_UID=qcom-soc`，于是 QRTR 控制面与 mhi_net 数据面被合并成**同一个** MM 设备。

### 三个此前不知道的事实

1. **MM 的 QMI 走 QRTR 是通的**。`qmicli -d qrtr://3`、`qmicli -p -d qrtr://3`（走 qmi-proxy）都成功拿到 manufacturer；MM 也成功为客户 DMS/NAS/WDS/WMS/UIM/LOC/PDC/DSD/SAR/VOICE 分配了 client。之前那句「MM 只能靠 `wwan0qmi0`」是错的。
2. **`/dev/wwan0qmi0` 确认是死的**：`qmicli -d /dev/wwan0qmi0` 报 `CID allocation failed in the CTL client: Transaction timed out`，而同一台机器的 QRTR 路径正常 —— 说明不是 CTL 缺失，是这个端点本身不响应。继续 ignore 它是对的。
3. **MM 自己重启后要 ~30 s 才能重新发现 modem**：重启后 20 s 时 `mmcli -L` 是空的，30 s 就有了；`sudo mmcli --scan-modems` 可立刻催一次。这是**运维细节**，不是失败。

### 数据面仍然缺补丁（这条没变）

`mmcli -m 0` 把 `mhi_hwip0` 认成数据端口了，但**建承载**时 qcom-soc 插件会在 `peek_port_qmi_for_data` 里按驱动分派：只有 `ipa`、`bam-dmux` 有分支，`mhi_net` 落到「unsupported data port driver」。Apollo 的 `0001-qcom-soc-mhi-net.patch` 正是加一个 `mhi_net` 分支（复用 IPA 那条），`0002-bearer-bind-pcie.patch` 让 PCIe 端点在未复用的情况下也先 bind（**这一条我们自己独立验证过**：不 bind 就是 `error 70 invalid-operation`，bind 之后才走到 `error 14`）。

另外确认**multiplex 机制本身是上游就有的**（不需要打补丁）：`--test-multiplex-requested` 选项、`qmi_device_add_link_with_flags`、`MM_PORT_QMI_SETUP_DATA_FORMAT_ACTION_SET_MULTIPLEX` 都在 Debian 1.24 的二进制里。也就是说，只要 **① 加载 `rmnet.ko`（已验证可外部构建，MM 文档 §4.10）② 打好 0001/0002**，MM 的 QMAP 承载路径在理论上就是完整的。

### 现在的状态与运维

- `ModemManager.service` **enabled + active**；`/etc/systemd/system/ModemManager.service.d/zz-kebab-mm.conf` 让它 `After=kebab-sdx55-modem.service`（`systemctl show -p After` 已确认生效）。
- **没有 NetworkManager**（`dpkg -q network-manager` = not-installed），所以 MM 不会顺带接管系统网络，不会和现有路由/DNS 打架。
- 健康检查：`dmesg` 里 `errfatal|device died` **0 次**；`wwan0efs0` 的 ignore 规则生效，**没有复现 11/10 的 ERRFATAL**；GNSS 不受影响（仍在 25–28 颗）；`kebab-qmi-wds.py selftest` 在 MM 运行期间 **PASS**（两套客户端可共存）；三个支撑单元 + MM 全部 active。
- `systemctl restart kebab-sdx55-modem` 之后 MM 仍能看到 modem（实测），所以插卡当天重启我们的单元不会把 MM 弄丢。

**注意**：`mmcli -m 0` 会打印 `imei:`，那是真实 IMEI，**不要把它贴进任何文档或日志**。


### 5.5 已构建并运行补丁版 `ModemManager`（2026-11-13）

补丁已真正编进二进制并在跑，不再只是「补丁可用」。做法（`scripts/linux/build-modemmanager-kebab.sh --build`）：

1. Debian 源码放到 `/srv/modem-20261112/src/modemmanager-1.24.0`（`orig` + `debian.tar.xz`，走 wlan0 拉取）；
2. 打 `patches/mm/` 两条补丁（0001 上下文精确，0002 offset −1 行，均 rc=0）；
3. `meson setup build-kebab -Dintrospection=false -Dman=false -Dtests=false -Dexamples=false -Dbash_completion=false -Dbuiltin_plugins=true -Dqmi=true -Dmbim=true -Dqrtr=true -Dudevdir=/usr/lib/udev`；
4. `ninja` 489 个目标全过，`src/ModemManager` **3,680,200 B**、SHA-256 `61c7911031907a5c565a5c3a6822f0fee283da86199aae58819fa5823679b394`；
5. 装到 `/usr/local/lib/kebab-mm/ModemManager-patched`，用 drop-in `zz-kebab-mm-patched.conf` 覆盖 `ExecStart`，**dpkg 的文件一个没动**（`dpkg -V modemmanager` 干净）；
6. `systemctl restart ModemManager` 后 35 s，`mmcli -L` 仍出 `Modem/0 [QUALCOMM INCORPORATED]`，状态 `failed / sim-missing`（无卡正确）。

**补丁确实在二进制里的证据**（不是「编译没报错所以应该在」）：qcom-soc 的分派函数 `peek_port_qmi_for_data`（符号 `0x193700`）里有**三次** `g_strcmp0`，分别引用 `0x220380`、`0x227160`、`0x242890`；用 `dd` 读这三个地址得到 `ipa`、`mhi_net`、`bam-dmux`。原版只有 `ipa` 与 `bam-dmux` 两次比较。

**回退已验证**：`--revert` 删 drop-in 并重启 → `ExecStart` 回到 `/usr/sbin/ModemManager`；再写回 drop-in → 又回到补丁版。

### 5.6 一个比补丁更有意思的发现：上游其实已经支持 mhi_net，缺的只是 qcom-soc 插件

在源码里 `mhi_net` 出现在 `mm-broadband-modem-qmi.c:356`（有专用 `peek_port_qmi_for_data_mhi()`）、`mm-port-qmi.c`（默认 MTU、raw-ip、link 管理多处）、`mm-modem-helpers-qmi.c`、`mm-bearer-mbim.c`。也就是说**通用 QMI 类早就会处理 mhi_net**，只有 `src/plugins/qcom-soc/` 的分派漏了它。我们的设备因为 QRTR 被打了 `ID_MM_QCOM_SOC` 才走 qcom-soc 插件，于是正好踩在这个缺口上。Apollo 的 0001 让 qcom-soc 复用 IPA 分支，信息等价（endpoint 来自 QMI 端口本身），但更「正统」的写法是调用上游那个 `_mhi` 变体——只是它是 static，插件里拿不到。

### 5.7 QMAP 端到端演练（含 WDA 切换，已跑合并恢复）

此前只分别验证过「WDA 能切 QMAP」和「rmnet 能建」，没有把整条链一次跑完。本轮按 SIM 日真实顺序跑了一遍：

```
format-set --ul qmap --dl qmap --apply   # set result=0，读回 ul=5/dl=5（link-layer 仍 raw-ip）
kebab-modem-qmap load && up --mux 1      # rmnet0 over mhi_hwip0, mux_id 1, mtu 1500
kebab-qmi-wds.py data-up --mux 1 --ifname rmnet0 --profile 3
    bind  : result=0 error=0
    start : result=1 error=14 (call-failed) + cm/acl-failure 692
format-set --ul disabled --dl disabled --apply   # 恢复
```

**结论**：QMAP 打开 + mux 绑定后，呼叫仍然走到同一个「无卡 → CM/ACL 失败」，没有出现 `invalid-argument` 或与 mux 相关的新错误 ⇒ 数据面组合在控制层是自洽的。恢复后复核：`format` = raw-ip/disabled、无 `rmnet0`、无 `rmnet` 模块、`mhi_hwip0` DOWN 无地址、`SELFTEST PASS`。

### 5.8 没有 apt 也能装依赖：`fetch-debs-kebab.py`

这台机器 DNS 不通，`apt` 用不了。新工具 `scripts/linux/fetch-debs-kebab.py` 直接把 Debian 的 `Packages.xz` 拉下来、按 `Depends` 递归解析（忽略 Recommends）、打印计划（默认 `--list`），`--apply` 才下载并一次 `dpkg -i`。**实装验证**：用它装齐了 `meson ninja-build libdbus-1-dev gettext gettext-base xsltproc` 和 7 个 `-dev` 包（约 40 个依赖包），解析耗时 1.3 s。

两个坑值得记：

* `dpkg -i` **一次传全部包**仍可能因顺序报「depends … is not configured yet」，补一次 `dpkg --configure -a` 即可；
* 本机安全更新版本比 pool 里的新（`libpcre2-*` 是 `~deb13u3`，pool 是 `~deb13u2`），`-dev` 包要求**精确版本**，必须去 `security.debian.org` 拉 `~deb13u3` 那一套（同一 IP 151.101.2.132 可直连）。

`libgudev-1.0-dev` 与 `libpolkit-gobject-1-dev` 至今仍是「unpacked but not configured」：它们依赖的 `gir1.2-gobject-2.0-dev` **在 trixie main 里不存在**（只有别家依赖它，没有这个包）。因为我们关掉了 introspection，构建只需要头文件和 `.pc`，`pkg-config` 全部正常，所以不影响构建；这一点写进了工具的 `--check` 提示里。
### 5.9 自建 MM 与 modem/GNSS 共存长稳（2026-11-13，21 样本 / 10 分钟）

自建二进制最需要回答的一个问题是「它会不会把 modem 或 GNSS 带坏」。为此跑了 10 分钟、每 30 s 一次的共存采样（`/srv/modem-20261112/mm-soak-20261111T195738Z/samples.csv`，CSV 同时归档到 `local/modem-20261112/`）：

| 指标 | 21 个样本的结果 |
| --- | --- |
| `mm_state` / `mm_reason` | `failed` + `sim-missing`，**21/21 恒定**（无卡的正确结论，没有在 `failed` 与其它状态间抖动） |
| `opmode`（独立 `qmicli` 走 QRTR 查 DMS） | `online`，**21/21** |
| GNSS 卫星数 | 24–25，均值 25.0，无掉零 |
| `/run/kebab-gps.json` 新鲜度 | 0–1 s，**从未变旧** ⇒ `kebab-gps` 没被 MM 抢 LOC 客户端搞停 |
| QMI 可达性（`--dms-get-manufacturer`） | 成功 21/21（MM 持有自己的 QRTR 客户端时仍可并发） |
| `dmesg` 的 `errfatal|device died` | **0**（累计计数，21 次采样都没涨） |
| `/sys/bus/mhi/devices/mhi1` | 在位 21/21 |

结论：**打补丁的自建 MM 在本机是安静的邻居**——它自己维持一个 `failed/sim-missing` 的 modem 对象，不干扰 QRTR、不干扰 GNSS 的 LOC 会话、不引发 MHI 故障。这条只覆盖无卡状态；插卡后 MM 会真的去建承载，那一段仍要在 SIM 日观察（也是 `--qmap` 之外的唯一未知）。
### 5.10 两个网口里只能有一个当数据口（2026-11-13，已钉死）

这台机器从 `mhi_net` 拿到**两个** net 设备：

| ifindex | 接口 | MHI 通道 | 用途 |
| --- | --- | --- | --- |
| 5 | `mhi_swip0` | IP_SW0 46/47 | 闲置 |
| 6 | `mhi_hwip0` | IP_HW0 100/101 | **真正的数据通路** |

读代码发现 ModemManager 不是按名字挑的：`mm_base_modem_peek_best_data_port()` 返回“类型匹配且未连接的第一个”，而 `mm-base-modem.c` 又把这个**第一个**端口当成 QMI 端口的 `net_sysfs_path`/`net_driver`，endpoint 信息也从它算。两个候选时这就是凭列表顺序掷骰子——**选到 `mhi_swip0` 的承载会 bind 到一个不承载流量的端点上**，插卡当天表现为“呼叫起来了但没包”。顺带一提：`mmcli` 打印的端口列表是**按名字排序**的（`port_info_cmp`），所以旧输出里 `mhi_hwip0` 在前纯属字母序，不能当内部顺序的证据。

修法是一条 udev 规则（`77-mm-sdx55-swip-ignore.rules`）：把 SW 口标成 `ID_MM_PORT_IGNORE`。已验证：

```
# 用 --log-level=DEBUG 单独起一次 MM 观察分类结果
[mhi_swip0/probe] port is ignored via udev tag
[modem0] net/mhi_hwip0: net (data)
```

`mmcli -m 0` 现在是 `mhi_hwip0 (net), mhi_swip0 (ignored), qrtr3 (qmi)`；加规则之前两个都是 `(net)`。

### 5.11 QMAP 的最后一个开关：MM 的 multiplex（已备好，默认不装）

社区把 `zz-apollo-multiplex.conf` 列为 kebab 上“缺的一件”——没有 QMAP multiplexing，下行大约 10 MB 后永久 wedge。我们补丁版 MM 现在认得 `mhi_net`，只差这个 flag：

```
[Service]
ExecStart=
ExecStart=/usr/local/lib/kebab-mm/ModemManager-patched --log-journal --test-multiplex-requested
```

文件放在 `/usr/local/lib/kebab-mm/dropins/zzz-kebab-mm-multiplex.conf`（在 `/etc` 之外，所以**默认不起作用**）。启用前必须先 `kebab-modem-qmap load`（multiplex 走 `qmi_device_add_link_with_flags` 建 rmnet 链路，没模块会直接失败），启用后**不要再同时跑 `data-up` 且不带 `--mux`**。

踩到的坑值得记：文件名 `zz-kebab-mm-multiplex.conf` 会**排在** `zz-kebab-mm-patched.conf` **前面**，被后者的 `ExecStart` 静默覆盖——flag 根本没到进程，而 `systemctl show -p ExecStart` 看着一切正常。改成 `zzz-…` 才对。两种状态都验过：装了 → `argv[]=… --log-journal --test-multiplex-requested`；删了 → 回到 `--log-journal`，modem 两种情况都正常识别（`failed/sim-missing`）。

## 6. 回退（如果将来装了）

`
sudo systemctl disable --now ModemManager
sudo rm -f /etc/udev/rules.d/77-mm-ignore-sdx55-efs.rules /etc/udev/rules.d/77-mm-sdx55-fusion.rules
sudo rm -rf /etc/systemd/system/ModemManager.service.d
sudo udevadm control --reload-rules
sudo apt purge modemmanager libmm-glib0     # 或 dpkg -r
`
回退后 modem 仍由 `kebab-sdx55-modem.service` 管理，GNSS/EFS 不受影响。

## 7. Evidence → Finding → Path

| Evidence | Finding | Path |
| --- | --- | --- |
| 两条 patch 对 1.24.0 `apply rc=0`（§1） | 不必从 git 重编，Debian 源码即可打补丁 | 插卡后按 §3 构建补丁版 |
| CRLF 导致 apply 失败（§1） | 跨平台搬 patch 必须先归一 LF | 入库副本已是 LF |
| `wwan0qmi0` 10 s 超时（§1） | 控制面只能 QRTR；MM 必须 ignore 该端口 | fusion 规则保留 `*qmi*` ignore |
| 内核无 RMNET、mhi_net 无 QMAP（§1） | Apollo 的 multiplex 方案在 kebab 不可用 | 不开 `--test-multiplex-requested`；raw-IP 先行 |
| 内核有 MHI_WWAN_MBIM，但固件无 IP_HW0_MBIM 且 MBIM_OPEN 不回应 | MBIM 不是可用数据路径 | 只在 raw-IP 上规划，wedge 时考虑 QMAP 内核工作 |
| 设备无外网（§1） | 装包要绕 PC | 记录了 .deb 依赖清单 |
