# SDX55 社区同类项目交叉核对（2026-11-12）

> 两个社区仓库已 clone 到本地（gitignore）：`local/community-20261112/kebab-pmos`（jfbobier/Oneplus-8T-kebab-pmos-bringup，**同为 kebab**）、`local/mm-20261112/apollo`（royka1/Xiaomi-Apollo-pmOS-packages，Mi 10T，同 SM8250+SDX55）。本轮不刷机、不改固件，只对照 + 补一块开机时序。

## 0. 结论（六条）

1. **用户态栈与社区逐字节相同**：`tqftpserv.c`、`pm_service_native.c`、`mhi_efs_sync.c`、`diag_reader.c` 四个源文件的 SHA-256 与 kebab 社区包完全一致（`1088bf5f…`/`18a2ed1b…`/`fa32a7ab…`/`cc47d7e0…`）。也就是说我们跑的 tqftpserv **已经是**他们修过的版本（`qrtr_publish(4096, instance=3)` + `send_retry` 处理 EAGAIN/ENOBUFS），不是上游裸版。社区文档里"block 8 必失败、64 s 后 ERRFATAL"那个坑，我们这边已经堵上。
2. **MM 规则也一致**：kebab 仓库的 `77-mm-sdx55-fusion.rules` 就是 Apollo 那 6 行（我们入库的 `config/mm/` 带注释，功能等价）；`77-mm-ignore-sdx55-efs.rules` 逐字相同。
3. **内核补丁我们已具备**：`sources/linux/drivers/bus/mhi/host/pci_generic.c` 的 SDX55 fusion profile 带 `no_m3 = true`（社区 patch 0091），`mhi_sahara_modem` 在（0010/0022），EFS 通道在（0092）。我们的 7.2 内核就是这条社区的树。
4. **社区卡在 "NV 未 provision"，而本机不是**：他们 `efs{1,2,3}` 是 512 B 空白占位、IMEI 全 0、MCFG apply 失败、`Bands:'none'`、GPS 无星；本机 EFS 是 2 MiB 真实镜像（10/03 Android 备份）、band 全量、GNSS 出星。**因此他们的结论（"GPS 与蜂窝是同一个 NV 问题"）不能套到 KB2000 本机**——我们早已越过了那一步。
5. **两条必须继承的时序事实**（我们此前不知道，来自他们的 `kebab-uim-provision`）：
   - **MCFG 在 mission mode 后约 15 s 和 24 s 各跑一次；期间卡若还锁着 PIN，配置就永远不激活**，之后 `DMS online` 返回 DeviceNotReady、`Networks`/band 空、无数据也无 GNSS；**MCFG 不能运行时重触发**（他们试过 `--uim-reset` 和重开会话，文件请求计数不变）。⇒ **插卡要发生在开机之前**，或插卡后重启。
   - 这块固件的**自动 UIM provisioning NV 项可能缺失**（`slot_automatic_provisioning`/`subscription_provisioning`/`sub_config`），卡"detected"但绑不上 subscription，MM 报 `GW primary session index unknown` → sim-missing；需要开机早期显式激活 primary-gw 会话。
6. **MBIM 路径本轮实测排除**（详见 §3）：固件没有 `IP_HW0_MBIM` 数据通道，MBIM_OPEN 也不回应 ⇒ 数据面只剩 raw-IP `mhi_hwip0`（或将来做 QMAP 内核工作）。

## 1. 逐项对照

| 项目 | 社区 kebab 仓库状态 | 本机（KB2000）状态 | 结论 |
| --- | --- | --- | --- |
| Sahara / mission mode | 通 | 通（15 镜像，9–14 s） | 一致 |
| `no_m3` 防 M3 挂起 | 有 patch 0091 | 内核已含 | 无需动作 |
| EFS 通道 + `mhi_efs_sync` | 有（0092 + 服务） | 有（`mhi-efs-sync` 在跑） | 一致 |
| ~706 s ERRFATAL | 已定位=EFS 未服务，已解决 | 早已解决，soak 越过（本轮 3,229 s 无 errfatal） | 一致 |
| `tqftpserv` instance 3 + 重试 | 修好 | **源文件逐字节相同** | 一致 |
| `pm_service_native` | 有 | 有（同一源文件） | 一致 |
| `diag_reader` | 有 | 有源文件；设备上未装 | 需要时再装 |
| MM udev 规则 | 有 | 已入库 `config/mm/`（未安装） | 一致，等 SIM |
| MM 补丁 | 有 | 已验证可应用（见 MM 文档） | 一致，等 SIM |
| EFS 镜像 | **512 B 空白占位** | **2 MiB 真实** | 本机更优 |
| IMEI / NV | 全 0，OEMNVBK 未恢复 | 已 provision（band 全量即证据） | 本机更优 |
| MCFG apply | 失败（回 idle） | 未验证（无卡时 MCFG 不选配置） | **插卡后第一件事** |
| GNSS | 无星（归因 NV） | 出星（26–27 颗可见） | 本机更优 |
| UIM provisioning / PIN 时序 | 已做成脚本 | **本轮已移植进 bring-up** | 已补齐 |
| MBIM | 未使用 | 实测无数据通道（§3） | 排除 |
| PCIe runtime PM | 有 `kebab-pcie2-no-runtime-pm.service` 把 PCIe2 钉成常开 | 实测 `0002:00:00.0` 与 `0002:01:00.0` 均 `power/control=on`、`runtime_status=active` | 已满足，无需该服务 |

## 2. 本轮据此做的改动（设备侧）

- 新增 `/usr/local/lib/kebab-modem/sim-provision.sh`（仓库：`scripts/linux/kebab-modem-sim-provision.sh`），改编自社区 `kebab-uim-provision`：
  - **无卡时 <1 s 退出**（社区原脚本会等 120 s，不适合放进开机链）；
  - 卡在时：先看是否已有 primary-gw 会话，没有就按卡所在物理槽激活（AID 只打印长度）；
  - PIN 只在 `/etc/kebab-sim-pin`（0600 root）存在、且 PIN1 retries == 3 时验证**一次**，其余情况拒绝（PUK 保护）；日志里 PIN 一律替换成 `<pin>`；
  - 收尾打印 Card state / Application state / PIN1 state / Primary GW 与 `Networks` 能力，不含敏感值。
- 接进 `kebab-modem-bringup.sh`：**QMI 就绪 + sleep 3 之后、设置 online 之前**跑一次（抢在 MCFG 的 15 s 窗口前），设置 online 之后再跑一次（覆盖卡初始化晚到）。
- 实测：单元重启 3.66 s（原 ~3 s），日志出现 `no SIM card present in either slot; skipping` 两次；soak 33 样本无异常（mhi1 100%、online 100%、每样本有星、errfatal 0）。
- 未做：没有安装 MM、没有改 udev、没有动 EFS/GNSS/限充/分区。

## 3. MBIM 实测排除（本轮新证据）

| 动作 | 结果 |
| --- | --- |
| 直接对 `/dev/wwan0mbim0`（`mhi_wwan_ctrl` 提供的原始端口）写 MBIM_OPEN（8 字节） | 写入成功（8 B），**5 s 内无任何回应**；DEVICE_CAPS 同样无回应 |
| `modprobe mhi_wwan_mbim`（`CONFIG_MHI_WWAN_MBIM=m`） | 驱动注册成功 |
| 把 `mhi1_MBIM` 从 `mhi_wwan_ctrl` unbind 再绑到 `mhi_wwan_mbim` | **`ENODEV`**：该驱动 `id_table` 只匹配 MHI 通道 **`IP_HW0_MBIM`**（`mhi_wwan_mbim.c:649`），本机通道表只有 `mhi1_MBIM`/`mhi1_IP_HW0`，**没有 `IP_HW0_MBIM`** |
| 恢复 | 重新绑定 `mhi_wwan_ctrl`（`wwan0mbim0` 回来、dmesg `port wwan0mbim0 attached`），`rmmod mhi_wwan_mbim` |

结论：这块固件没有暴露 MBIM 数据通道，只有控制通道且不响应 OPEN ⇒ **MBIM 不是备选数据路径**。数据面只剩 raw-IP `mhi_hwip0`（Apollo 的 `wds_test` 已证 raw-IP 可通，但有 ~10 MB wedged 风险），要更好的只能做 QMAP 内核工作。

## 4. 插卡当天的操作顺序（更新版）

1. **先插卡，再开机**（MCFG 只跑一次，运行时无法重触发）。若要验证"热插卡"，预期是无效的，需要重启。
2. （可选）`sudo install -m 600 /dev/null /etc/kebab-sim-pin`，把 PIN 写进去（本文件永不入库）；不留文件则不做 PIN 自动验证。
3. 开机后看：`journalctl -t kebab-sim`（会话是否激活、PIN 是否验证）、`qmicli -d qrtr://N --uim-get-slot-status`、`--dms-get-band-capabilities`。
4. 若 `Networks:` 或 band 为空 ⇒ MCFG 没激活：先确认卡 state=ready、PIN1 state=disabled/verified，再重启一次；仍空则退到社区 §4.4 的 NV 路线排查（本机 NV 已 provision，概率低）。
5. 之后再装 ModemManager（`config/mm/README.md`，规则先于 MM）。

## 5. 仍未闭合

- **MCFG apply 在本机是否也会失败**：无卡时 MCFG 不选配置，所以真正的检验只能在插卡后；社区失败、本机 band 正常只能说明"MCFG 之前的 NV/EFS 是好的"。
- **slot 归属**：社区说 MM 报 slot 2 活跃；本机 `--uim-get-slot-status` 显示 Physical slot 1 `Slot status: active`。插卡后按实测决定会话激活用哪个槽（脚本已按"卡所在槽"处理）。
- 社区的 `OEMNVBK` 恢复工具链（`mdm1oemnvbktmp` 记录格式、RFNV 写入）本机不需要；仅在插卡后 IMEI 读不出时才值得回头研究。
- 本轮没有冷启动验证（新脚本只在单元重启与无卡路径上验证过）；下次正常重启时确认 `journalctl -t kebab-sim` 仍只有"no card"一行且开机时间不变。

## 6. Evidence → Finding → Path

| Evidence | Finding | Path |
| --- | --- | --- |
| 四个源文件 SHA-256 与社区相同（§0.1） | 我们已在用社区修好的用户态 | 不再重造 tqftpserv/pm/efs 工具 |
| 社区 `kebab-uim-provision` 注释 + 本机无卡 | MCFG 15/24 s 一次性、PIN 必须在那之前就绪、热插卡无效 | 插卡前开机；脚本已接进 bring-up |
| 社区 NV 缺失项 + 本机 EFS 2 MiB | 本机不需要 OEMNVBK 恢复 | 插卡后先看 caps，不要先动 NV |
| MBIM_OPEN 无回应 + `IP_HW0_MBIM` 不存在（§3） | MBIM 不是数据路径 | 数据面按 raw-IP 规划 |
| Apollo 的 `CONFIG_RMNET=m` + `rmnet.conf` + `--test-multiplex-requested`（§7） | raw-IP 的 ~10 MB wedge 有同族设备的成熟解法（QMAP） | 先按 raw-IP 验证"能通"；内核 r5 加 `CONFIG_RMNET=m` 再验稳健性 |

## 7. Apollo 的数据面路线：QMAP 复用（本机内核缺 `CONFIG_RMNET`）

Apollo（小米 10T Pro，SM8250 + 外挂 SDX55，与本机同一套 MHI/PCIe 拓扑）的仓库里有三件直接证据，说明他们**已经解决**了 raw-IP 的"约 10 MB 后下行永久 wedged"：

| 文件 | 内容 | 说明 |
| --- | --- | --- |
| `linux-postmarketos-qcom-sm8250/config-postmarketos-qcom-sm8250.aarch64` | `CONFIG_RMNET=m`（全文件仅此一处 rmnet） | 内核提供 rmnet/MAP 支持 |
| `apollo-modem-support/rmnet.conf` | 7 字节：`rmnet` | 即 `/etc/modules-load.d/rmnet.conf`，开机加载 |
| `apollo-modem-support/zz-apollo-multiplex.conf` | `ExecStart=/usr/sbin/ModemManager --test-quick-suspend-resume --test-multiplex-requested` | MM 对每个 bearer 请求 QMAP 复用；注释写明"不复用则 raw-IP 下行约 10 MB 后 wedged，一次测速就废" |
| `modemmanager-patches/UPSTREAM-NOTES.md` | `wds_test --bind=mux --ep-type=pcie --ep-iface=4 --mux-id=0` → `CONNECTED (handle 0x…) address: 10.26.143.7 mtu: 1500` | 真实拿过地址；流量在 `mhi_hwip0`，`mhi_swip0` 无流量（端点接口号 4 映射硬件通道） |

**与本机的关系**：`/proc/config.gz` 没有 `CONFIG_RMNET`（只有 `CONFIG_MHI_NET=m`），`/lib/modules` 无 `rmnet*`，`ip link add … type rmnet` 被拒；即 **QMAP 不是用户态能补的，是内核 r5 的一个配置增量**。完整机制（`rmnet_newlink` 需要 `IFLA_LINK`+`IFLA_RMNET_MUX_ID`、Kconfig 写明"可挂到任意 IP 模式物理设备"、MM 通过 libqmi `qmi_device_add_link_with_flags()` 建 link）与 r5 候选清单已写进[执行记录](sdx55-modem-bringup-20261112.md) §8.1。

**一条需要小心解读的社区说法**：Apollo 的 UPSTREAM-NOTES 写"远程 EFS 理论是错的，应丢掉那个内核补丁"，但他们的包**照旧发布 `mhi-efs-sync.service`**；本机实测（无守护时首次 EFS-Sync 后 ~73 s ERRFATAL、NV 不持久）证明 kebab 需要它。按"他们指自己早先写的 EFS 端口内核补丁（主线后来已收）"理解，**不要因此删我们的 EFS 服务**。

## 8. 2026-11-13 复核：他们写的「成功判据」在本机已经通过

社区 `TECHNICAL_HANDOFF_2026-09-22.md` §9 把下面这条当成 modem 工程是否完成的**唯一成功判据**：

> **Success test:** `sudo qmicli -p -d qrtr://3 --dms-get-capabilities | grep Networks` —— anything listing RATs means solved, and data plus GPS follow.

本机实测（只读，无卡）：

```
$ qmicli -d qrtr://3 --dms-get-capabilities
	Max TX channel rate: '1800000'
	Max RX channel rate: '3100000'
	       Data Service: 'non-simultaneous-cs-ps'
	                SIM: 'supported'
	           Networks: 'cdma20001x, evdo, gsm, umts, lte, 5gnr'
```

即**他们的判据在本机是 PASS**（而且不用 `-p` proxy，QRTR 直连即可）。这与我们已经掌握的三件事互相印证：`--dms-get-band-capabilities` 全 16 个 LTE 频段 + NR 1/3/41/78/79、GNSS 稳定出星、`op_mode=online`。所以他们卡住的那一类「MCFG/NV 没 provision ⇒ 能力集为空 ⇒ 没有数据也没有 GNSS」在本机不成立（本机 EFS 是 2 MiB 真实内容、`mcfg_autoselect` 正常跑完），**剩下的只有物理卡**。

### 他们列的三件「kebab 还缺的东西」，逐项结账

| 他们的缺件 | 本机状态 |
| --- | --- |
| `77-mm-sdx55-fusion.rules` | 已有（`config/mm/`，24 条 udev 规则里的一份） |
| 两条 MM 补丁（qcom-soc 认 `mhi_net`、PCIe 端点先 bind） | 已有，且已构建运行（`ModemManager-patched`，489/489，SHA `61c79110…`） |
| `zz-apollo-multiplex.conf`（`--test-multiplex-requested`） | 已备（`/usr/local/lib/kebab-mm/dropins/zzz-kebab-mm-multiplex.conf`，默认不生效） |
| `pd-mapper` | **没有，也不需要**：本机 SLPI 传感器、GNSS、MHI 全都不走 PD 映射（`command -v pd-mapper` 空；系统里没有任何 pd/pdr 单元引用它），装它不会改变数据面 |

### 一处必须纠正的社区笔误：mhi0 是 Wi-Fi，mhi1 才是 modem

他们的 handoff §8 写「`mhi1` 的 M0/M1 churn 是 ath11k Wi-Fi……modem 是 `mhi0`」。**本机正好相反，而且有硬证据**（systemd 按 MHI 设备生成的 unit 名字自带 PCI 地址与型号）：

```
sys-devices-platform-soc@0-1c10000.pcie-pci0002:00-0002:00:00.0-0002:01:00.0-mhi1-mhi1_IP_HW0-net-mhi_hwip0.device  SDX55 [Snapdragon X55 5G]
```

`0002:01:00.0`（`0x17cb:0x0306`）挂的是 **mhi1**，它的 `IP_HW0/IP_SW0/QMI/EFS/DIAG` 通道就是 modem；`mhi0`（`0000` 域、`0x1101`）才是 QCA6390 Wi-Fi。读社区文档时不要按他们的 mhi0/mhi1 叫法去找设备。

### 他们的内核配置也把 rmnet 编进去了

`kernel/config-postmarketos-qcom-sm8250.aarch64` 里同时有 `CONFIG_MHI_NET=m` 与 `CONFIG_RMNET=m` —— 说明他们同样认为 QMAP 是数据面的正解（与 Apollo 的三件套一致）。我们的差别只是**没改内核**：用同一棵树外部构建 `rmnet.ko`（§7、MM 文档 §4.10），所以这条不需要等到 r5。

## 9. 社区复查（2026-11-12 第 53 轮，实测时间点）

每轮都会重新拉一次社区仓，因为这轮的结论可能已经过时。本轮结果：**两个仓都没有新提交**，也就是说自我们上次分析以来社区没有新证据。

| 仓库 | 我们上次分析的提交 | 本轮上游 HEAD | 结论 |
| --- | --- | --- | --- |
| `jfbobier/Oneplus-8T-kebab-pmos-bringup` | `f3dba2e`（2026-10-05 `linux_upstream: rebase against msm-next, drop a patch already fixed upstream`） | **`f3dba2e`（同一个）** | 无变化；他们仍停在「IMEI 全 0 ⇒ 不给注册」 |
| `royka1/Xiaomi-Apollo-pmOS-packages` | `4c52c8b`（QMAP multiplex 配置、两条 MM 补丁） | **`4c52c8b`（同一个）** | 无变化；raw-IP「约 10 MB 后卡住」仍是他们的公开结论 |

命令（只读）：`git ls-remote <repo> HEAD` 与 `git log --oneline 4c52c8b..HEAD`。

**顺带核了上游 `mhi_net` 有没有变**：`torvalds/linux` master 的 `drivers/net/mhi_net.c` 仍然只有那一处 MAP 标记（`skb->protocol = htons(ETH_P_MAP)`，共 3 处 `skb->protocol` 赋值：IPv4/IPv6/MAP），没有任何 QMAP/rmnet 逻辑。⇒ 我们第 30 轮得出的模型（`mhi_net` 只负责“首字节不是 4/6 就标 MAP”，封装/解封装全在 `rmnet`）与当前上游一致，升到更新内核也不会改变这部分语义。

**这意味着什么**：插卡当天的证据链只能由我们自己产出——社区没有比 Apollo 更进一步的数据面记录，而 Apollo 的「通道能起来、跑一会儿就卡」正是 `sim-day` 的 `samples.tsv` / `wedge_seen` / `bearer_lost` 现在要抓的形态。

