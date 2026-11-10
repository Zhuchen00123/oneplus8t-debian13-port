# 主线 7.2（r4 / kebab）启用 SDX55 GNSS：可行性评估与分步计划

状态：**已执行并打通（2026-11-10 晚，见 §21）**。早期 §0/§20.4 的“零测量 / 缺 SIM”结论已被实测推翻；以 §21 为准。
日期：2026-11-10。工作区：F:\codexprojects\oneplus8t。手机 SSH：ssh -F local/ssh/kebab-server.conf -o BatchMode=yes kebab。

## 0. 结论摘要（先给答案）

**整条启动链（PCIe → mhi_pci_generic 绑定 → Sahara 固件链 → mission mode → QMI LOC/GNSS）在现役 r4 上「内核与固件前提都已具备、只是没有启用」；主机侧拿到 NMEA 的可行性为「中高」，但「能否真的出定位」被一个与蜂窝共用的 modem 侧策略/配置门卡住，且该门是否也阻塞 GNSS 未被证伪——这是本计划唯一真正的未知数。**

可行性分层：
- **A. 让 SDX55 起来并进入 mission mode、出现 wwan/qrtr/LOC 节点**：高。内核补丁 0010–0013、固件、模块、DTS 全部就位；参考 kb2003 已实测稳定。主要工作是"装本机私有固件 + 加载模块 + 起 EFS 守护 + 装用户态"，都是可回退的设备写入。
- **B. 让 QMI LOC 引擎启动、能要 NMEA**：中高。MODEM.md §4.6 实测 --loc-start 成功、XTRA 注入成功、MM 报 gps-raw/gps-nmea；同平台 hotdog 也实测 LOC/NMEA types 可用。
- **C. 真的拿到卫星测量/定位/NMEA 句子**：**未验证、风险最高**。MODEM.md 的结论自相矛盾且已过时（见 §3），不能复用"doesn't work"。

与现有路线图的关系：P0 计划（docs/android-lab-p0-plan-20261110.md）第 89–106 行把"D 主线启用 SDX55 GNSS"判为"极高、不建议、不排期"，建议走外接 GPS（方案 C）。本任务按用户明确要求**重新评估 D**；评估后 D 的内核/固件门槛其实比 11/10 写计划时认为的低（补丁已在 r4、固件已在本机），但"定位能否出数"仍取决于 §3 的未知数。Waydroid GNSS HAL / gpsd 属更后续，不在本会话执行范围。

硬约束遵守：服务器优先；现役 r4 与现有服务不动；不迁 7.3；不刷分区、不写 persist/NV（除非单独批准）；固件/EFS/NV 每台设备私有、OEM 签名，只放忽略目录（.gitignore 第 5/6/9/10 行 /local/、/firmware/、*.bin、*.mbn 已忽略），禁止提交/上传。

---

## 1. 已核实的现状（本会话只读取证）

### 1.1 设备实况（boot 811708cb-9ebd-4c92-aaef-3e8c9c01a493，7.2.0-kebab-debian13-r4-venus+）

只读盘点命令经 SSH 执行，关键回执：

| 项 | 结果 | 证据 |
|---|---|---|
| 内核 | 7.2.0-kebab-debian13-r4-venus+ | uname -r（设备） |
| /proc/config.gz | MHI_BUS=m、MHI_BUS_PCI_GENERIC=m、MHI_BUS_SAHARA_MODEM=m、MHI_WWAN_CTRL=m、MHI_WWAN_MBIM=m、MHI_NET=m、QRTR_MHI=m、QCOM_RMTFS_MEM=m、WWAN=m | 设备 zcat /proc/config.gz 过滤 |
| 模块文件 | mhi_pci_generic.ko.zst、mhi_sahara_modem.ko.zst、mhi.ko.zst、mhi_wwan_ctrl.ko.zst、qrtr-mhi.ko.zst 全在 | 设备 find /lib/modules/$(uname -r) |
| 已加载 | 仅 mhi（被 ath11k_pci/qrtr_mhi 用）、qrtr_mhi；mhi_pci_generic 未加载 | 设备 lsmod |
| /sys/bus/mhi/devices | 只有 mhi0（Wi-Fi，挂 0000:01:00.0）+ mhi0_IPCR，driver 只有 qcom_mhi_qrtr；**无 mhi1/SDX55** | 设备 ls |
| PCI 0002:01:00.0 | vendor 0x17cb、device 0x0306、subsystem 0x17cb:0x010c、class 0xff0000、driver 链接**空**、driver_override=(null)、link 8.0 GT/s x2 | 设备 sysfs + dmesg |
| 0002:00:00.0 | pcieport（PME: Signaling with IRQ 200） | dmesg |
| 固件 | /lib/firmware/sdx55m/ **不存在**、无 sbl1.mbn | 设备 ls |
| /dev/wwan*、/dev/mhi* | 全无 | 设备 ls |
| qrtr | qrtr-lookup 在（qrtr-tools 1.1-2+b1），只有 node 1/5/9/10（ADSP/SLPI/CDSP/SSC 400），**无 node 3、无 LOC 服务** | 设备 qrtr-lookup |
| 用户态 | qmicli/mmcli/tqftpserv/pd-mapper/rmtfs/mhi_efs_sync/gpsd/qmi-proxy 全 MISSING；仅 libqmi-glib5 1.36.0-1、libqmi-proxy、libqrtr-glib0、qrtr-tools 已装 | 设备 command -v / dpkg -l |
| apt 候选（只读） | libqmi-utils 1.36.0-1、modemmanager 1.24.0-1+deb13u1、gpsd 3.25-5+deb13u2 均可从 trixie main 装 | 设备 apt-cache policy（未安装） |
| 分区（by-partlabel） | modem_a/b、mdm1m9kefs1/2/3、mdm1m9kefsc、mdm_oem_stanvbk、mdm_oem_dycnvbk、mdm1oemnvbktmp、modemst1/2、fsg、fsc、spunvm 全在；sudo -n dd ... of=/dev/null 全部 READABLE | 设备 ls /dev/disk/by-partlabel + 只读 dd 探测 |
| 磁盘 | / 仅 846M 空闲（88%）、/srv 199G 空闲 | 设备 df -h |
| 服务 | 无 mhi/efs/tqftpserv/mm/diag 相关 unit/rules | 设备 systemctl list-unit-files、ls /etc/udev/rules.d |

dmesg 中 mhi0 的 "Power on / Wait for device to enter SBL or Mission mode" 全部对应 Wi-Fi（之后是 ath11k RDDM），**没有一条 SDX55/Sahara 记录**——端点从未被 probe，符合"无驱动绑定"。这正是 MODEM.md §1 第 41–44 行警告的"先重读 live dmesg"，本会话已重读。

### 1.2 r4 内核确实含 0010–0013，且 0091/0092 已等价包含

固定 bringup 副本 sources/bringup/kernel/（pinned 2e5e065d，见 sources.lock.json 第 132–138 行、docs/community-status.md 第 13 行）：

| 补丁 | 文件 | 作用 | 对应 MODEM.md |
|---|---|---|---|
| 0010 | 0010-...Sahara-v2-firmware-loader...patch | 新增 drivers/bus/mhi/host/sahara.c + CONFIG_MHI_BUS_SAHARA_MODEM，SBL 经 MHI ch2/3 逐镜像 HELLO/READ_DATA/END_OF_IMAGE | §2.1 固件链 |
| 0011 | 0011-...SDX55-Fusion...patch | mhi_qcom_sdx55_fusion_info（fw=sdx55m/sbl1.mbn、amss_fw=sdx55m/multi_image.mbn、no_m3=true）、SAHARA/EFS/DIAG/MBIM/QMI 通道、PCI 17cb:0306/17cb:010c ID 覆盖 | §2 / §4.2 |
| 0012 | 0012-...don-t-re-enable-runtime-PM-on-no_m3...patch | mhi_pci_status_cb() 里 if (!mhi_pdev->no_m3) pm_runtime_allow() | **= 0091**（MODEM.md §3.2 / MODEM-summary.md 第 18–28 行，逻辑逐字一致） |
| 0013 | 0013-...remote-EFS-MHI-channel...patch | 新增 WWAN_PORT_EFS、把 MHI "EFS" 通道绑到 /dev/wwanXefsY | **= 0092**（MODEM.md §4.0 / MODEM-summary.md 第 30–41 行） |

r4 venus 构建树 /home/zhuchen/oneplus8t-venus-20261105/linux（WSL）的 git log 明确列出四个提交：f5d70fc05(0010)、3e04fe3fb(0011)、ce19ab4da(0012)、77244ff3d(0013)；out/.config 有 MHI_BUS_SAHARA_MODEM=m、LOCALVERSION="-kebab-debian13-r4-venus"；构建产物 out/drivers/bus/mhi/host/mhi_pci_generic.ko 内含字符串 qcom-sdx55m-fusion、mhi_qcom_sdx55_fusion_info、no_m3、amss_fw、sdx55m/multi_image.mbn、alias v000017CBd00000306sv000017CBsd0000010C。设备上 modinfo mhi_pci_generic 同样返回该精确 alias，mhi_sahara_modem 描述 "MHI Sahara firmware loader for Qualcomm modems"。**即 0010–0013 与 0091/0092 全部已编入现役 r4 并安装到设备，无需重编内核、无需换 boot.img、无需新补丁。**

（注：构建树工作区的 pci_generic.c 当前是回退到基线的未提交状态，但 HEAD 与已安装 .ko 都含补丁；以已安装模块与 /proc/config.gz 为准，不要被工作区文件误导。）

### 1.3 r4 DTS 已含 SDX55 所需板级描述

sm8250-oneplus-kebab.dts 第 1788–1862 行（WSL r4 树）：
- &pcie2 { msi-parent = <&intc>; status = "okay"; }（第 1799–1814 行），&intc { msi-controller; mbi-ranges = <864 32>; mbi-alias = <0x0 0x17a10000>; }——GICv3 MBI 路由，避开 SMMU 0x64100000 上下文错误；
- &pcie2_phy { ... status = "okay"; }（第 1817 行）；
- &tlmm 下 ap2mdm-status-hog(gpio56 high) / ap2mdm-errfatal-hog(gpio57 low)（第 1849–1861 行），与 MODEM.md §2.3 第 106–118 行的 stock f13 DT 一致。设备 /proc/device-tree/soc@0/pcie@1c10000 存在。**DTS 无需改动。**

### 1.4 固件来源（本机私有，已确认）

firmware/kb2000-14.0.0.602-20261003-154007/firmware.tar（467,082,752 B、SHA db7d97ec…、manifest.json 第 1–12 行，从本机 KB2000 原厂只读抽取，archive-entries.txt 2601 行）中 vendor/firmware_mnt/image/sdx55m/ 含 MODEM.md §2.1 第 66–92 行要求的**全部 15 个镜像且尺寸逐一吻合**：

```
sbl1.mbn 547904   multi_image.mbn 13232   multi_image_qti.mbn 12616
apdp.mbn 13508    devcfg.mbn 42515        tz.mbn 913408
hyp.mbn 84416     sec.elf 12368           aop.mbn 154672
acdb.mbn 131112   qdsp6sw.mbn 84390028    apps.mbn 2632704
efs1/2/3.bin 512 each（占位空镜像，见下）
```

另有 modem_pr/mcfg/... 配置树、mdmddr.mbn、xbl_cfg.elf 等。路径应为 /lib/firmware/sdx55m/（**无 qcom/ 前缀**），MODEM.md §2.1 第 91–92 行与上游 10/02 提交 b1134ff9 均确认。

**EFS/NV 注意**：tar 里的 efs{1,2,3}.bin 是 512 B 占位（MODEM.md §2.2 第 94–104 行：本机的 2 MiB 真实 NV 在 mdm1m9kefs* 分区 / mdm_oem_stanvbk 容器，且实测内容为空白或需 EFS 守护导出后才填充）。本会话已确认设备上这些分区在 sudo -n 下只读可读（见 §1.1）。固件链可先用 tar 的 15 个镜像起步；2 MiB EFS/NV 是否替换、如何替换属后续单独批准项（见 §4 步骤 2/3）。

**隐私**：/firmware/、/local/、*.bin、*.mbn 已在 .gitignore 第 5/6/9/10 行忽略；sources.lock.json 第 304 行 privacy 也规定设备序列号/专有固件/分区镜像/EFS/NV 只留忽略的本地目录。本会话未复制、未上传任何固件/NV 内容。

### 1.5 现成用户态配方（bringup 仓库，GPL/本地）

- sources/bringup/packages/kebab-modem-tools/mhi_efs_sync.c（GPL-2.0，注释第 1–34 行说明 Sahara memory-debug 协议、Android /vendor/bin/ks 等价物）、mhi-efs-sync.service（第 18 行 exec /usr/bin/mhi_efs_sync -v -o /var/lib/mhi-efs，**默认安全模式，不写分区**）。
- sources/bringup/device/oneplus-kebab/77-mm-ignore-sdx55-efs.rules（第 4 行 SUBSYSTEM=="wwan", KERNEL=="wwan0efs0", ENV{ID_MM_PORT_IGNORE}="1"）——MODEM.md §4.0 第 189–195 行强调**加 EFS 端口必须同批加此规则**，否则 ModemManager AT 探测会在 mission mode 后 ~2 s 触发 ERRFATAL。
- 77-mm-sdx55-fusion.rules（QRTR 控制面 + mhi_net 数据面融合）、kebab-uim-provision*（本设备自配置 SIM，MODEM.md §4.4.4 第 489–490 行判为"不需要"）。
- MODEM.md §5 第 1114–1132 行还列 tqftpserv-sdx55（QRTR 服务 4096 发布到 instance 3）、pm_service_native、pd-mapper、rmtfs、MM 补丁等——这些是**蜂窝数据**栈所需；对纯 GNSS 而言，核心只需 EFS 守护（保命）+ 一条能发 QMI LOC 的通道。

---

## 2. 启动链路还缺什么（相对"能跑 GNSS"）

按用户目标链逐段对照，缺的都不是内核/固件，而是**设备侧启用与用户态**：

| 链路段 | r4 现状 | 还缺 | 性质 |
|---|---|---|---|
| PCIe 枚举 0002:01:00.0 | 已枚举、无驱动 | 无 | — |
| mhi_pci_generic 绑定 | 模块在、alias 精确匹配、未加载 | 一次 modprobe mhi_pci_generic（或 udev 自动） | 设备写（模块加载） |
| Sahara 固件链 15 镜像 | 本机 tar 齐全 | 把 15 镜像落到 /lib/firmware/sdx55m/（/ 仅 846M，建议先核对空间/软链到 /srv） | 设备写（固件安装） |
| mission mode | 补丁/DTS 齐 | 绑定后自动进入（BHI→SBL→Sahara→AMSS，参考 ~14–17 s） | 自动 |
| EFS 守护（防 ~706 s ERRFATAL） | 无 | 编译/安装 mhi_efs_sync + mhi-efs-sync.service + 77-mm-ignore-sdx55-efs.rules（**三者同批**） | 设备写（用户态/服务） |
| /dev/wwan*、QRTR node 3 | 无 | 随 mhi1 + 0013 + qrtr_mhi 自动出现 | 自动 |
| QMI LOC / GNSS 用户态 | qmicli/mmcli 缺 | apt install libqmi-utils（+可选 ModemManager、gpsd）；用 qmicli -p -d qrtr://3 起 LOC 会话 | 设备写（装包） |
| 主机拿位置/NMEA | 无 | LOC 会话 + --loc-follow-nmea / MM location / gpsd 馈入（后续） | 测试/开发 |
| **卫星测量/定位真的出数** | **未知** | 见 §3 | **真正风险** |

补充：MODEM.md §5 第 1121–1129 行的 tqftpserv-sdx55、pm_service_native、pd-mapper、rmtfs、MM 融合规则/QMAP 是**蜂窝数据**与"ModemManager 完整建 modem"所需。若走"直接 QMI LOC（不经 ModemManager）"路线，可先不装这些，降低改动面；hotdog 证据（§3.3）说明这条短路可行。

---

## 3. 重新评估 GPS 阻塞：不复用旧结论（本任务重点）

用户明确要求"不要复用旧的 doesn't work 结论；MODEM.md 说过要先重读 live dmesg"。本会话重读了 live dmesg（§1.1）与整份 MODEM.md，发现**MODEM.md 对 GPS 的结论自相矛盾、且 §4.6 已被后续 §4.4.4 的工作证伪/架空**：

### 3.1 MODEM.md 三层互相冲突的说法

1. **文件头（第 6–14 行，标注 2026-09-21，最新状态）**："厂方 NV **已经恢复**——容器格式破解、经 DIAG 写入、逐字节校验、跨重启持久，含 ~250 KB RF 校准（§4.4.4），IMEI 字段已出现。**但射频仍不上线**，Bands: 'none' 不变，所以容器之外还有东西门控 band capability，首要嫌疑是 RF card 配置（/nv/item_store/rfnv/rfc.bl，5 字节，疑似空）。**GPS 仍被同一件事阻塞**（§4.6）。"
2. **§4.6（第 998–1095 行，较早分析）**："GNSS 引擎未被门控，--loc-start 成功、XTRA 注入成功、gps-raw/gps-nmea 能力在；但 20–25 分钟天空视角下**零 CN0/零测量**，判定是缺 XO/TCXO 校准 → **GPS 与蜂窝同 bug，解决 NV 恢复（§4.4.4）两者都好**（第 1063–1068 行）。"
3. **§4.4.4 尾部（第 750–941 行，实际后来做的工作）**：
   - 第 753–778 行：nvrestore.py 把 **28 个 RFNV 项、~250 KB、含 ft_table_gps(768 B)、xo_factory_cal、nr_band_pref** 全部写入并跨重启持久；第 775 行明列 ft_table_gps (0, 768)。
   - 第 827–849 行：**DIAG 抓包确认 RF 栈已起来、校准被成功读取、没有任何校准读错误**（"§4.4.4 的恢复在关键层面成功了"）。
   - 第 851–860 行：真正空的是 Policy Manager 的 Num_Techs 0, State Baseline，"**这比校准缺失更能解释 Bands:'none' 和 DeviceNotReady**"；第 878 行："**下一个要追的是 policyman，不是 RF**"；第 891–892 行："**RF card 配置（rfc.bl）已降优先级，基本可认为没问题**"。
   - 第 796–804 行：即便如此 Bands:'none' 不变，"**容器之外还有东西门控**"。

### 3.2 因此，旧结论逐条重判

- ❌ **"GPS 被 NV/XO 校准阻塞、恢复 NV 即修 GPS"（§4.6.3.1 第 1063–1068 行）已被证伪**：校准（含 GPS 专用 ft_table_gps 768 B、xo_*）**确实恢复了**，RF 栈也在读，但 MODEM.md 里**没有任何"NV 恢复之后重测 GNSS"的记录**。§4.6 的 GNSS 测试时间线在 §4.4.4 大规模 NV 写入之前。这是最关键的证据缺口。
- ❌ **"rfc.bl 空导致 GPS"（文件头第 10–14 行）只是当时猜测，且被 §4.4.4 自己（第 891–892 行）降优先级**；不能当结论。
- ✅ **仍然成立的部分**：--loc-start 能起会话、LOC 服务在 QRTR、XTRA 能注入、gps-raw/gps-nmea 能力在（§4.6 第 1000–1009 行）——GNSS 引擎与主机侧 QMI 通路是通的。
- ⚠️ **新的、更可能的门**：Policy Manager / MCFG apply / device_config.xml / /mdb/policyman/*.mdb / Num_Techs 0（§4.4.4 第 851–889 行、MODEM-summary.md 第 103–113 行 "Remaining blocker: MCFG apply"、上游 REGRESSIONS.md L206 "still blocked on the MCFG apply failure"）。但**这是蜂窝 radio 的门，不是已被证明的 GNSS 门**。

### 3.3 QMI LOC 是否依赖 RF card / NV / Bands:'none'？——社区与上游证据

- **QMI LOC 是独立服务，不要求蜂窝射频在线**：libqmi 有 QMI_LOC_OPERATION_MODE_STANDALONE（freedesktop libqmi 文档）；MODEM.md §4.6 第 1000–1009 行在 Bands:'none'、offline 状态下 --loc-start 仍返回成功、MM 仍列出 gps-raw/gps-nmea。同平台 **hotdog（SM8150+SDX55）docs/evidence/2026-08-10-gnss-qmi-loc.md** 实测：qmicli -d qrtr://0 --loc-get-nmea-types 返回 gga/rmc/gsv/gsa/vtg、--loc-start/--loc-stop 干净、--dms-* 都答；并指出 qmicli -p（qmi-proxy）可让 LOC 会话跨进程保持，"GNSS bridge 不必经过 ModemManager"。**即"拿 NMEA 通路"不依赖 band/射频在线**。
- **但"出卫星测量"在两处都未证实**：MODEM.md §4.6.3 是"零测量"（NV 恢复前、无天空视角变量控制）；hotdog 是"indoors on desk，100 s 超时无 SV，冷启动室内本就如此，**既未证实也未排除接收机**"。两者都**没有在 NV 恢复后、有天空视角、用长驻 qmi-proxy 会话重测**。
- **GNSS 是否受 policyman 门控，无公开证据**：上游 kebab 仓库 2026-10-02→10-05 的提交全是 DP/上游化与固件 README 修正，**无新 GNSS/modem/NV 修复**；live README 仍写 "Cellular data, GPS: **not working**"；live MODEM.md 头与本机 pinned 副本一致（NV 已恢复、rfc.bl 嫌疑），但 live 版仅 1141 行，比本机 pinned 1292 行**更旧/更短**（本机副本含 §4.4.4 尾部的 policyman 结论）。gnss-share issue #10（已归档）只讨论 A-GPS/XTRA 经 ModemManager 注入，未涉及零测量根因。
- **结论**：§4.6 "GPS 与蜂窝同一 NV bug、不可独立修" **不再成立为定论**；在 Bands:'none' 下让 GNSS 出 NMEA **在协议层是可能的**（QMI LOC standalone 独立），但是否真能出测量/定位，必须由一次**受控重测**回答：NV/校准状态明确（已恢复）→ 起 LOC 会话（qmi-proxy 长驻）→ 有天空视角 → 看 --loc-get-gnss-sv-info 有无 CN0/SV、--loc-follow-nmea 有无句子。这次重测本身就是本计划的"GNSS 测试"步骤，且是**判定整个 D 路线是否值得继续的闸门**。

### 3.4 与 P0 计划的关系

P0 计划第 104 行把 D 标"极高、不建议、不排期"，是基于 11/10 时"主线未启用 SDX55、无固件/MHI"的判断。本评估把**内核/固件前提**降级为"已具备、只需启用"；但把**定位出数**保留为未知。因此建议把 D 从"不排期"调整为"分两阶段：先做启用+QMI LOC 通路（确定可得），再用一次受控重测决定是否继续投入 GNSS/蜂窝策略门"，且始终与外接 GPS（方案 C）并行评估、不堵死 C。

---

## 4. 分步计划（每步：是否写设备 / 需何批准 / 回退）

> 总原则：服务器优先；现役 r4 与现有服务不动；每一步单独批准；先只读基线，再最小写入；每步有验证与可回退；固件/EFS/NV 只在忽略目录、绝不进 Git。以下**全部尚未执行**，等批准。

### 步骤 0（只读，已完成／可复核）— 基线与取证
- 本会话已做：§1.1–1.5 全部只读盘点、live dmesg 重读、r4 构建树 0010–0013 核验、固件 tar 清单、DTS 核验、上游检索。
- 写设备：**否**。批准：**无需**。

### 步骤 1（设备写·小）— 安装固件 + 加载模块，先拿到 mission mode
- 内容：
  1. 从 firmware/.../firmware.tar 仅解出 vendor/firmware_mnt/image/sdx55m/ 下 15 个 .mbn/.elf/.bin 到 /lib/firmware/sdx55m/（无 qcom/ 前缀，mode 600）。/ 仅 846M，先 du 估算（qdsp6sw ~84 MB，15 镜像合计 ~90 MB，放得下；若保守可落 /srv/sdx55m 再符号链接，但固件加载器按名查 /lib/firmware，建议直接放并核对空间）。
  2. **在同一步**部署 EFS 守护三件套（否则 ~706 s ERRFATAL，且 EFS 端口暴露后 MM 的 AT 探测会 ~2 s 内搞死 modem）：编译 mhi_efs_sync.c → /usr/bin/mhi_efs_sync；装 mhi-efs-sync.service（安全模式 -o /var/lib/mhi-efs，**不写分区**）；装 77-mm-ignore-sdx55-efs.rules 并 udevadm control --reload。
  3. sudo -n modprobe mhi_pci_generic mhi_sahara_modem mhi_wwan_ctrl（或让 udev 自动）。先不碰 Wi-Fi 用的 mhi0。
- 验证（只读）：dmesg 出现 BHI→Sahara 15×END_OF_IMAGE→MISSION MODE；lspci -s 0002:01:00.0 -k 有 mhi_pci_generic；ls /sys/bus/mhi/devices 出现 mhi1 + mhi1_EFS/QMI/DIAG/MBIM；ls /dev/wwan* 有 wwan0qcdm0/mbim0/qmi0/efs0；qrtr-lookup 出现 **node 3** 与 LOC 服务；systemctl status mhi-efs-sync active；journalctl -u mhi-efs-sync | grep wrote 在 ~640 s 看到导出 m9kefs1。
- 写设备：**是**（固件文件、模块加载、服务/规则）。批准：**需用户逐项批准这一批**（固件安装 + modprobe + 服务/规则）。
- 回退：modprobe -r mhi_pci_generic mhi_sahara_modem mhi_wwan_ctrl（先停 EFS 服务）；删 /lib/firmware/sdx55m/、删服务/规则；Wi-Fi 的 mhi0 不受影响（不同 PCI 设备）；不重启也能回到"端点无驱动"原状。固件文件本身可删可重装。
- 风险：Sahara 链任何镜像缺失/尺寸错会卡在 SBL（不损坏设备，热回退即可）；EFS 端口与 ignore 规则必须同批，否则 ERRFATAL（MODEM.md §4.0/§6 第 1191–1196 行）；no_m3(0012) 已避免 M3 恢复失败。

### 步骤 2（设备写·小）— 用户态 QMI 工具，起 QMI LOC 会话
- 内容：apt install -y libqmi-utils（trixie main，1.36.0-1；libqmi-glib5 已装）。可选 qmi-proxy 随 libqmi-utils 提供（设备当前 qmi-proxy MISSING，hotdog 证据表明长驻会话需要它）。**先不装 ModemManager**，降低"AT 探测 EFS 端口/需 net port"风险；如后续要 mmcli --location-* 再单独批准装 ModemManager + 77-mm-sdx55-fusion.rules。
- 验证：qmicli -d qrtr://3 --loc-get-nmea-types；qmicli -p -d qrtr://3 --loc-session-id=1 --loc-start --client-no-release-cid；--loc-get-gnss-sv-info；--loc-follow-nmea。
- 写设备：**是**（apt 装包）。批准：**需批准**。
- 回退：apt remove libqmi-utils（不动其它包）；LOC 会话随进程退出。

### 步骤 3（设备写·中，条件性）— EFS/NV 持久化与"私有数据落地"决策点
- 这是**每台设备私有、OEM 签名**数据处理的关键停点：
  - 选项 a（最保守，推荐先做）：什么都不写分区，只让 mhi_efs_sync 把导出的 m9kefs1 存 /var/lib/mhi-efs/（运行时数据，可备份到忽略目录）。**不把导出的 NV 回灌 /lib/firmware/sdx55m/efs1.bin，除非单独批准**（回灌会改变下次启动喂给 modem 的 EFS）。
  - 选项 b（需单独批准）：把本机 mdm1m9kefs1/2/3（sudo -n 只读可读）或 mdm_oem_stanvbk 解析结果用于恢复 NV/校准——**这正是 MODEM.md §4.4.4 已做且证明"校准恢复后 radio 仍不上线"的重活**，且写 RF 校准风险高（MODEM.md 第 937–941 行自述"错值比没值更糟"）。本计划**不默认执行**，仅在步骤 4 的 GNSS 重测证明"就差校准"且用户单独批准后才考虑。
  - **绝不**写 persist、绝不刷分区、绝不上传这些镜像。
- 写设备：a 否（仅运行时文件）／b 是（且高风险）。批准：a 随步骤 1 批；**b 需单独、明确批准**。
- 回退：a 删 /var/lib/mhi-efs/*；b 按 MODEM.md 第 918–920 行用 /root/efs-blank-backup 思路回灌空白 EFS 并核对。

### 步骤 4（测试·只读为主，可能改服务开关）— GNSS 受控重测（判定闸门）
- 前提：步骤 1–2 成功，QRTR node 3 的 LOC 服务在。
- 内容（**都在有天空视角、用户知情的时间窗内**）：
  1. 用 qmi-proxy 长驻 LOC 会话（--client-no-release-cid），避免 qmicli 退出即断会话（hotdog 第 91–110 行）。
  2. --loc-start → --loc-get-gnss-sv-info（看有无 CN0/SV）、--loc-follow-nmea（看有无 GGA/RMC 句子）、mmcli --location-get（若装了 MM）。
  3. 可选注入 XTRA（MODEM.md §4.6.1 已证可注入）。
  4. 判定：**出 CN0/SV 或 NMEA 句子** → D 路线可行，继续步骤 5；**仍零测量**（且已排除室内/会话寿命）→ 说明 §4.4.4 的 policyman/MCFG 门**也**压住 GNSS，或 GNSS 另有独立门；此时**停止投入**，回到 P0 方案 C（外接 GPS），并把证据写回 docs。
- 写设备：基本只读（起/停 LOC 会话、开关服务）；装 gpsd 或 MM 属另批。批准：**测试窗口需用户确认在场/知情**；不改内核、不刷写。
- 回退：停 LOC 会话、停 qmi-proxy；不影响 modem 稳定性（EFS 守护继续保命）。

### 步骤 5（后续，不在本会话执行）— 主机侧位置消费
- 若步骤 4 通过：apt install gpsd（或自写小 NMEA feeder）→ gpsd → 后续再议 Waydroid GNSS HAL / geoclue。属 P1，单独计划与批准。

### 步骤 6（回退总纲）
- 任意时刻可回到"现役 r4、SDX55 未绑定、现有服务不动"：停 mhi-efs-sync → modprobe -r mhi_pci_generic mhi_sahara_modem mhi_wwan_ctrl → 删 /lib/firmware/sdx55m、服务、规则、apt 装的 libqmi-utils → 核对 Wi-Fi mhi0 / 五业务 / boot id / 服务 NRestarts0 与基线一致。
- **不需要重刷 boot、不需要重启、不需要换内核**（0010–0013 已在 r4，回退只是卸载模块/删文件）。
- 若出现 modem ERRFATAL 连带 PCIe 链路掉（冷启动才恢复），按 MODEM.md §3.1 用一次正常重启恢复，并保留证据；不自行反复重试。

---

## 5. 风险登记

| 风险 | 来源 | 缓解 |
|---|---|---|
| ~706 s ERRFATAL（EFS 通道无人服务） | MODEM.md §4.0 第 172–195 行 | 步骤 1 同批部署 mhi_efs_sync+service+ignore 规则；守护用安全模式 |
| MM 把 wwan0efs0 当控制口 AT 探测 → ~2 s ERRFATAL | MODEM.md §4.0.2 / §6 第 1191–1196 行 | 77-mm-ignore-sdx55-efs.rules 与端口同批；先不装 ModemManager |
| M3 自动挂起恢复失败、链路不重训 | MODEM.md §3 | 0012(no_m3) 已在 r4，无需用户态 pin |
| Sahara 镜像缺失/路径带 qcom/ 前缀 | MODEM.md §2.1 第 86–92 行 | 用本机 tar 精确 15 镜像、无 qcom/ 前缀、mode 600 |
| / 仅 846M 空闲 | 设备 df | 先估算 ~90 MB，必要时落 /srv+软链（须验证固件加载器按名解析） |
| 写 RF 校准/NV 错值比没值更糟 | MODEM.md 第 937–941 行 | **默认不写**；步骤 3b 单独批准前只读、先备份 |
| 固件/EFS/NV 私有、OEM 签名外泄 | sources.lock.json 第 304 行、.gitignore | 只放 /firmware/、/local/、设备 /srv 忽略目录；不提交/不上传/不写 docs 原文 |
| GNSS 零测量（真正未知数） | §3 | 用步骤 4 受控重测做 go/no-go；不复用旧结论；失败即回方案 C |
| 影响现役服务器/Wi-Fi/五业务 | 全局约束 | 只动 0002:01:00.0 新端点；Wi-Fi 是 0000:01:00.0 的 mhi0，独立；每步后核对服务 PID/NRestarts0 |

---

## 6. 证据索引（本会话新增/复核）

- 设备只读盘点：ssh -F local/ssh/kebab-server.conf -o BatchMode=yes kebab，/proc/config.gz、ls /sys/bus/mhi、/sys/bus/pci/devices/0002:01:00.0、qrtr-lookup、sudo -n dmesg | grep -iE mhi|pcie|sdx55、lsmod、dpkg -l、df -h、ls /dev/disk/by-partlabel、apt-cache policy、sudo -n dd ... of=/dev/null（只读）。
- 本地源码：sources/bringup/docs/research/MODEM.md（§2/§4.0/§4.4.4/§4.6 行号见正文）、MODEM-summary.md、sources/bringup/kernel/0010-0013*.patch、sources/bringup/packages/kebab-modem-tools/*、sources/bringup/device/oneplus-kebab/77-mm-*rules。
- 固定版本：sources.lock.json 第 132–138/304 行、docs/community-status.md 第 13/27/54 行。
- r4 构建树（WSL）：/home/zhuchen/oneplus8t-venus-20261105/linux 的 git log（四个提交了 0010–0013）、out/.config、mhi_pci_generic.ko 字符串、sm8250-oneplus-kebab.dts 第 1788–1862 行；docs/venus-video-decode-20261105.md（r4 部署/build-id a4c5c07b…/config e2543ac0…）。
- 固件归档：firmware/kb2000-14.0.0.602-20261003-154007/{firmware.tar,archive-entries.txt,manifest.json}（私有、忽略）。
- 上游/社区：jfbobier/Oneplus-8T-kebab-pmos-bringup live README/MODEM.md/REGRESSIONS.md（2026-10-02→10-05 commit，无新 GNSS 修复，README 仍 "Cellular data, GPS: not working"）；torvalds/linux master drivers/bus/mhi/host/pci_generic.c（no_m3 通用 flag、mhi_qcom_sdx55_info、NMEA 通道，但无 flashless Fusion profile）；Sr-0w/hotdog-linux-bringup docs/evidence/2026-08-10-gnss-qmi-loc.md（同平台 QMI LOC/NMEA types/qmi-proxy 长驻会话证据）；postmarketOS/gnss-share issue #10（A-GPS/XTRA，已归档）；freedesktop libqmi Qmi.LocOperationMode（STANDALONE）。
- 本会话临时盘点脚本（local/sdx55-*.sh）用完即删，未留设备侧文件。

---

## 7. 待用户批准项清单（执行前逐项确认）

- [ ] 步骤 1：安装 15 个 sdx55m 固件到 /lib/firmware/sdx55m/ + 编译/安装 mhi_efs_sync + 装 mhi-efs-sync.service + 装 77-mm-ignore-sdx55-efs.rules + modprobe 三个模块（**一批，EFS 端口与 ignore 规则必须同批**）。
- [ ] 步骤 2：apt install libqmi-utils（含 qmi-proxy），起 QMI LOC 会话。
- [ ] 步骤 3b（条件性、单独批准）：是否把本机 mdm1m9kefs* / mdm_oem_stanvbk 私有 NV/校准用于回灌（默认**不做**）。
- [ ] 步骤 4：GNSS 受控重测窗口（需用户在场/知情，有天空视角；含是否临时装 ModemManager/gpsd）。
- [ ] 是否接受"若步骤 4 仍零测量则停止 D、回方案 C（外接 GPS）"这一 go/no-go 判定。

**本文件为评估与计划，未执行任何上机变更。**

---

## 8. 执行记录（步骤 1，2026-11-10，用户回复"开始继续"后执行）

> 按"逐项批准"原则，本轮只推进**步骤 1**（固件 + EFS 三件套 + modprobe 同批），**未装包、未起 LOC 会话、未做 GNSS 重测、未写分区/NV/persist**。步骤 2/4 仍待批准。

### 8.1 已执行的设备写入（全部可回退）

| 动作 | 内容 | 回执 |
|---|---|---|
| 本机暂存 | 从 `firmware/.../firmware.tar` 解出 15 个 `sdx55m` 镜像到 `local/sdx55m-stage-20261110/`（忽略目录），尺寸与 MODEM.md §2.1 逐一吻合，SHA 记录；打包 `sdx55m-firmware.tar` SHA `01d34693…` | WSL `tar -xf`/sha256sum |
| 传设备 | scp 4 个文件到 `/tmp/sdx55-stage/`（firmware.tar / mhi_efs_sync.c / mhi-efs-sync.service / 77-mm-ignore-sdx55-efs.rules），设备端 SHA 复核 `firmware.tar=01d34693…` 一致 | scp + sha256sum |
| 装固件 | `/lib/firmware/sdx55m/` 15 文件、mode 600、共 85 MiB（`/lib/firmware` 所在 `/` 从 846M→762M 空闲） | 设备 `ls -la` |
| 编译工具 | `gcc -O2 -Wall` 编 `mhi_efs_sync.c` → 72,200 B，装 `/usr/bin/mhi_efs_sync` | 设备 gcc + install |
| 装服务 | `mhi-efs-sync.service` → `/etc/systemd/system/`，`daemon-reload`，**未 enable**（仅手动 start，避免开机自启在未批准前生效） | 设备 systemctl |
| 装规则（先于端口暴露） | `77-mm-ignore-sdx55-efs.rules` → `/etc/udev/rules.d/`、`udevadm control --reload`；`udevadm info /dev/wwan0efs0` 回 `ID_MM_PORT_IGNORE=1` | 设备 udevadm |
| modprobe | `mhi → mhi_sahara_modem → mhi_wwan_ctrl → mhi_pci_generic`（`sudo -n /sbin/modprobe`，RC=0） | 设备 lsmod |
| 起 EFS 守护 | `systemctl start mhi-efs-sync.service` → active/running，PID 249712，`mhi_efs_sync -v -o /var/lib/mhi-efs`，日志 "files only"，**无 -W、-g，不写分区** | 设备 journal |

### 8.2 实测结果（启动链打通）

- `dmesg`：`mhi-pci-generic 0002:01:00.0: MHI PCI device found: qcom-sdx55m-fusion` → `mhi1` → **Sahara 15 镜像全部 session complete**（13232×2、13508、12616、42515、913408、84416、12368、154672、512×3、131112、84390028、2632704），尺寸与 §2.1 完全一致。
- `wwan wwan0: port wwan0qcdm0 / wwan0efs0 / wwan0mbim0 / wwan0qmi0 attached` → **mission mode**。
- `/sys/bus/mhi/devices/`：`mhi1_DIAG/EFS/IPCR/IP_HW0/IP_SW0/MBIM/QMI` 全套；Wi-Fi `mhi0` 完好未受影响。
- `/dev/wwan0efs0` 等 4 端口存在；**EFS 端口名确为 `wwan0efs0`，与 77-mm-ignore 规则 `KERNEL=="wwan0efs0"` 精确匹配**。
- `qrtr-lookup`：**node 3 上线，41 个服务**，含 `Location service (~ PDS v2)` service 16 instance 0 node 3 port 69（QMI LOC）——GNSS 的 QRTR 通路已就绪。
- `dmesg` 无 `errfatal / Device died / mhi1 fail`；EFS 守护已先于 ~706 s 起好，机制性防 ERRFATAL 已就位。
- 服务器优先复核：boot `811708cb…` 不变、r4 不变、`kebab-dsh/690`、`sillytavern/709`、`three-mode/851`、`charge-policy/566` 全部 active、NRestarts=0 与基线一致；Wi-Fi mhi0 在。

### 8.3 未做 / 待批准

- **未装** `libqmi-utils` / ModemManager / gpsd，**未起** QMI LOC 会话，**未做** GNSS 重测（步骤 2/4，需批准）。
- **未**写任何分区、persist、NV、EFS 回灌；`mhi-efs-sync` 用安全模式，导出数据只到 `/var/lib/mhi-efs`（首次 `wrote` 预计 ~640 s 后出现，本轮未等到，不影响机制）。
- `mhi-efs-sync.service` 当前 `disabled; active(running)`——**未设开机自启**，下次重启不会自动拉起；如需长期保留要单独批准 enable。

### 8.4 回退就绪（一步回到步骤 0 状态）

```sh
sudo -n systemctl stop mhi-efs-sync.service
sudo -n /sbin/modprobe -r mhi_pci_generic mhi_wwan_ctrl mhi_sahara_modem
sudo -n rm -rf /lib/firmware/sdx55m /etc/systemd/system/mhi-efs-sync.service /etc/udev/rules.d/77-mm-ignore-sdx55-efs.rules /usr/bin/mhi_efs_sync /var/lib/mhi-efs /tmp/sdx55-stage
sudo -n systemctl daemon-reload; sudo -n udevadm control --reload
# 核对：boot id 不变、五业务 PID/NRestarts0 不变、mhi0 在、0002:01:00.0 driver 链接空、无 /dev/wwan*
```
不需要重刷 boot、不需要重启、不需要换内核。若出现 ERRFATAL 连带链路掉，按 MODEM.md §3.1 一次正常重启恢复并留证。


---

## 9. 执行记录（步骤 2，2026-11-10，用户回复"请继续"后执行）

> 本轮只推进**步骤 2**（用户态 QMI 工具 + 只读探测）。**未装 ModemManager、未 enable EFS 服务、未写分区/NV/persist、未做天空视角重测**。步骤 4 仍需用户物理配合（把手机放到有天空视角处）后单独确认。

### 9.1 安装 libqmi-utils（绕过设备 DNS 故障）

- 设备 `apt-get install libqmi-utils` 失败：`Temporary failure resolving 'deb.debian.org'`（设备 DNS 服务器 192.168.8.2 / 192.168.1.1 均不应答，`getent`、curl 超时；systemd-resolved active、NRestarts0；**属外部网络问题，非本任务引入**）。apt 仿真确认只会新增 1 包、0 升级、0 删除。
- 规避方式：PC 侧 `curl.exe --socks5-hostname 127.0.0.1:12080` 从 Debian 官方镜像下载 `libqmi-utils_1.36.0-1_arm64.deb`（184,596 B，SHA `f5ee234c…`），WSL `dpkg-deb -I` 核验为真 Debian 包（含 `qmicli` 670,704 B、`qmi-network`、`qmi-firmware-update`，依赖 `libqmi-glib5>=1.36.0` 等均已装），scp 到设备 `/tmp/sdx55-stage/`，设备端 SHA 复核一致后 `sudo -n dpkg -i` 本地安装。`dpkg --audit` 无缺失依赖。
- `qmi-proxy` 二进制在 `/usr/libexec/qmi-proxy`（`libqmi-proxy 1.36.0-1` 已装），不在 PATH；且手动运行回 `error: Not enough privileges`，`qmicli -p` 需 root + `PATH=/usr/libexec:...`。

### 9.2 只读 QMI 探测（`qmicli -d qrtr://3`，全部秒回、不含标识符）

| 命令 | 结果 | 与 MODEM.md 对照 |
|---|---|---|
| `--dms-get-manufacturer` | QUALCOMM INCORPORATED | §5 一致 |
| `--dms-get-revision` | Q_V1_P14 | §5 一致 |
| `--dms-get-operating-mode` | `offline`, HW restricted no | §4.4.4 第 477 行一致 |
| **`--dms-get-band-capabilities`** | **`Bands: 'none'`, `LTE bands: 'none'`** | **§4.4.4 第 474/796 行一致 → 旧"radio 仍不上线"仍成立** |
| **`--loc-get-nmea-types`** | **gga, rmc, gsv, gsa, vtg, pqxfi** | **§4.6 / hotdog 一致 → QMI LOC 通路可用** |
| **`--loc-get-operation-mode`** | **standalone** | GNSS 独立模式可用 |
| `--nas-get-signal-strength` / `--nas-get-serving-system` | -128 dBm / not-registered、radio off | 与 offline 一致 |

**决定性结论（实机证实 §3.3 的推断）**：在 `Bands:'none'` / `offline` 状态下，QMI LOC 服务（node 3、service 16、PDS v2）仍注册、NMEA types 可读、operation mode = standalone、`--loc-start` 返回 `Successfully started location tracking`。**GNSS 的 QMI 通路不依赖蜂窝 band/射频在线**；MODEM.md §4.6.3.1"GPS 与蜂窝同一 NV bug、不可独立修"**不再成立为定论**。但 `Bands:'none'` 本身也**没有**因本机启用 SDX55 而改善（与 §4.4.4 的 policyman/MCFG 门一致）——蜂窝 radio 仍不上线，这不是本任务目标。

### 9.3 EFS 守护机制实测通过（防 ~706 s ERRFATAL）

- `journalctl -u mhi-efs-sync` 在 uptime ~51066（modprobe 后约 850 s，MODEM.md 说 ~640 s 量级）出现：
  ```
  [51066.035]   wrote 2097152 bytes to /var/lib/mhi-efs/m9kefs1
  [51066.043] session complete
  ```
- 与 MODEM.md §4.0.1 第 199–210 行完全同构（导出区名 `m9kefs1`、2 MiB、RESET 完成）。`sudo -n ls /var/lib/mhi-efs/` 只有 `m9kefs1`（2,097,152 B，0600）。**守护只用安全模式 `-o /var/lib/mhi-efs`，未用 `-g/-W`，未写任何 NV 分区**。
- 期间 `dmesg` 无 `errfatal / Device died / mhi1 fail`；服务 PID 249712、NRestarts0、active；wwan 4 端口、qrtr LOC 服务全程在。

### 9.4 LOC 会话寿命测试（有界、室内；含一处诚实的失败）

- 直连 `qmicli -d qrtr://3`（不带 `-p`）：所有只读/启停命令**秒回**，`--loc-start --loc-stop` 均成功，NMEA types/operation mode 随时可读。
- `qmicli -p`（qmi-proxy 长驻，hotdog 建议用于跨进程保持会话）：需 `sudo -n env PATH=/usr/libexec:/usr/bin:/bin`。此条件下 `--loc-start --client-no-release-cid` 成功（`session id 1`、CID 1 保留），但后续 `--loc-get-nmea-types` / `--loc-get-gnss-sv-info` / `--loc-stop` 经 proxy 全部 `Transaction timed out`。**保留此失败，不掩饰**：它可能反映 qmi-proxy 在本机 QRTR 传输上的兼容问题，也可能只是室内冷启动无测量；**不能据此判 GNSS 失败，也不能据此判成功**。
- 收尾已用直连 `--loc-stop` 停掉 session 0 与 1，确认 `--loc-get-nmea-types` 仍秒回、无残留会话、无 errfatal。`qmi-proxy` 有一个空闲 root 进程（254645），会随 `--empty-timeout` 自行退出，未主动 kill。
- **"能否真出卫星测量/定位/NMEA 句子"仍未回答**：室内冷启动 SV info 超时是预期结果（hotdog 第 117–123 行），必须由**步骤 4 天空视角受控重测**判定，需用户把手机放到能看到天空的位置并确认时间窗。

### 9.5 服务器优先复核（每轮）

boot `811708cb…` 不变、`7.2.0-kebab-debian13-r4-venus+` 不变；`kebab-dsh/690`、`sillytavern/709`、`three-mode/851`、`charge-policy/566` 全 active、NRestarts0 与基线一致；Wi-Fi `mhi0` 完好；`mhi1` 全套通道、wwan 4 端口、qrtr node3 LOC 在；无 errfatal/device died。

### 9.6 当前改动面（全部可回退，与 §8.4 合并）

步骤 1 的固件/模块/EFS 三件套 + 步骤 2 的 `libqmi-utils`（本地 `dpkg -i`，可用 `sudo -n dpkg -r libqmi-utils` 单独卸载）。**`mhi-efs-sync.service` 仍为 `disabled; active(running)`——未设开机自启**；重启后 SDX55 需重新 modprobe 才会起来，不会自动影响现役服务。固件/EFS/NV 私有数据仍在忽略目录：设备 `/lib/firmware/sdx55m/`、`/var/lib/mhi-efs/m9kefs1`、`/tmp/sdx55-stage/`；本机 `firmware/`、`local/sdx55m-stage-20261110/`。**均未提交/上传**。

### 9.7 下一步（仍需批准 / 物理配合）

- **步骤 4（GNSS 受控重测）**：需用户把手机放到有天空视角处并确认时间窗；用直连 `qmicli -d qrtr://3 --loc-start` 后另起 `--loc-get-gnss-sv-info` / `--loc-follow-nmea` 长驻观察（或修好 qmi-proxy 长驻路径）。判定：出 CN0/SV 或 NMEA 句子 → D 可行；仍零测量且排除室内/会话寿命 → 停 D、回 P0 方案 C（外接 GPS）。
- 可选（单独批准）：装 ModemManager + `77-mm-sdx55-fusion.rules` 走 `mmcli --location-*`；enable `mhi-efs-sync.service` 开机自启；gpsd / Waydroid GNSS HAL（P1）。


---

## 10. 步骤 4 进行中记录 + 整机失联事件（2026-11-10，需用户知情/决策）

> **重要：本轮发生一次设备整机失联，尚未恢复、尚未查明原因。以下按时间线如实记录，未做任何盲目重试或恢复动作。** 用户回复"好的，继续"后按批准步骤 4 推进。

### 10.1 GNSS 受控重测（有界、可逆、未出结论）

- `qmicli --help-all` 确认 1.36.0 有 `--loc-follow-nmea` / `--loc-follow-gnss-sv-info` / `--loc-follow-position-report`（无限跟随）与 `--loc-timeout=[SECS]`（默认 30s，供 `--loc-get-gnss-sv-info` / `--loc-get-position-report` 用）。
- 设备侧 detached 脚本（`nohup bash /tmp/sdx55-stage/gnss-capture.sh`，PID 257370，**先 scp 成文件再执行**，避免 `bash -s` 经 SSH stdin 管道 EOF 静默退出——第一次 `nohup bash -s` 确实没跑起来，launcher.log 0 字节、无 capture 目录，已改正），90 秒窗口：3 个 `--loc-follow-*` 并行 + `--loc-session-id=7 --loc-start --client-no-release-cid` 保持会话，全部输出写设备私有 `/tmp/sdx55-stage/gnss-capture-20261110T074050Z/`，无标识符。
- 窗口内（~36s 时只读查看）：`--loc-start` 成功（`session id 7`、CID 1 保留），3 个 follow 进程在跑，但 **nmea.log / svinfo.log / pos.log 全部 0 行**。与 MODEM.md §4.6"引擎跑但无测量"一致，也与"室内冷启动无 SV"一致；**90 秒对 GPS 冷启动 TTFF 偏短，且我无法从设备侧确认天空视角，故本轮不构成 go/no-go 判定**。
- 窗口结束前，客户端在收数据 + 单次 `--loc-get-gnss-sv-info --loc-timeout=30` 时 **SSH 连接 `client_loop: send disconnect: Connection reset`**。

### 10.2 整机失联（非 GNSS 结论，需用户处理）

时间线（UTC / 设备本地）：
- 07:39–07:41 设备正常（boot `811708cb…`、r4、五业务 active、mhi1/wwan/qrtr LOC 在、EFS 守护 PID 249712/NRestarts0、无 errfatal）。GNSS 采集脚本运行中。
- 07:41:45 客户端收数据时 SSH 连接 reset。
- 之后复查：
  - SSH 经 USB 配置报 `bind 192.168.8.2: Unknown error`（PC 侧 USB 网卡绑定地址消失）。
  - PC `Get-NetIPAddress`：`192.168.8.2` 不在了，只剩以太网 `192.168.1.195`；手机 USB gadget 网卡 `192.168.8.1` 也不通。
  - 经 wlan IP `192.168.1.122` 直连 SSH 也 `Connection timed out`。
  - `ping 192.168.8.1` / `ping 192.168.1.122` 均 100% 丢包；`ping 192.168.1.1`（路由器）正常 → **PC 网络出口没问题，是手机整机失联**。
- **未查明原因**，可能与 MODEM.md §7.2 第 1281–1285 行记录的"一次启动挂起需强制断电"、或 SDX55/USB gadget 重枚举、或整机重启有关；也可能是手机休眠/低电/用户物理移动。**无证据表明是 GNSS 采集或本任务写操作直接导致**（采集只是 QMI LOC 跟随 + 只读查询；期间未写内核/分区/NV/服务）。
- **本代理未做任何恢复动作**（未重启、未断电、未重插 USB、未反复重试），按"危险/影响范围不明先确认"约束停下等待用户。

### 10.3 待用户决策

1. 请检查手机物理状态（屏幕、是否开机、USB 线、电量），必要时手动恢复（这与项目历史一致：曾出现需强制断电/正常重启恢复的情况）。
2. 恢复后请告诉我，我会先做**只读基线复核**：boot id 是否变化（判断是否整机重启）、五业务/充电策略 PID/NRestarts、`dmesg` 是否有 errfatal/pstore/整机复位痕迹、`mhi1`/wwan/qrtr LOC/EFS 守护/m9kefs1 是否还在、GNSS 采集目录是否残留、`libqmi-utils` 与固件是否完好。
3. 在确认整机状态前**不再执行步骤 4 后续或任何设备写入**；若确认为整机重启且 SDX55 未自动恢复（`mhi-efs-sync` 仍 disabled、模块未自动加载），这正好说明"未 enable 自启"的设计生效、现役服务不受影响。
4. go/no-go 判定继续挂起：需要"有天空视角 + 足够冷启动时间窗 + 设备在线"三者齐备才能做。

### 10.4 已落盘的私有数据（设备侧，恢复后需核对/清理）

`/tmp/sdx55-stage/gnss-capture-20261110T074050Z/`（nmea/svinfo/pos 日志、meta）、`/tmp/sdx55-stage/gnss-capture.sh`、`gnss-launcher.log`。均为临时、无标识符、设备私有；如整机重启 `/tmp` 会被清空，无需特意删除。本机侧 `local/sdx55m-stage-20261110/`（固件+deb，忽略）与 `docs/` 文档不变。


---

## 11. 失联复盘与方案修正（2026-11-10）

### 11.1 我的方案错误（责任在本代理）

用户按步骤 4 要求把手机移到**有天空视角处**，而本代理设计的采集依赖 **USB 有线 SSH**（`ssh -F local/ssh/kebab-server.conf` 固定 `HostName 192.168.8.1` + `BindAddress 192.168.8.2`）。移动带 USB 线的手机直接扯断/松开了 USB 网络：
- PC 侧 `192.168.8.2` 网卡消失（`bind 192.168.8.2: Unknown error`），USB gadget `192.168.8.1` 不通；
- 手机 wlan `192.168.1.122` 的 ARP 条目仍在（MAC `00-03-7f-12-75-75`，即 wlan0），但 TCP/22 超时 → **手机链路层在线、传输层不可达**（可能整机重启后 sshd 未起 / 室外 Wi-Fi 弱 / 挂起）；
- 90 秒 GNSS 采集窗口因此被 SSH reset 打断，数据不完整，**不得作为 go/no-go 结论**。

**这是本代理的方案缺陷**：把"需要用户移动设备"和"依赖有线连接做实时 SSH"同时放进一个步骤，且未预先设计断线容错。不是用户操作问题。

### 11.2 方案修正（待设备恢复后执行）

1. **不再依赖实时 SSH 长连接做户外 GNSS 采集**。改为「设备侧 detached 自主采集 + 事后回收」：
   - 采集脚本写到设备 `/srv/sdx55-gnss-20261110/`（`/` 之外的持久分区，199G 空闲；重启不丢；私有、忽略、不进 Git），而非 `/tmp`（重启清空）。
   - 设备侧用 `nohup` 自跑 **10–15 分钟**（GPS 冷启动 TTFF 常需数分钟，90 秒偏短），并行 `--loc-follow-nmea` / `--loc-follow-gnss-sv-info` / `--loc-follow-position-report` + 长驻 `--loc-start --client-no-release-cid`，脚本自停自清会话，结果落盘。
   - 我**事后**在设备可达时回收并解析，避免 SSH 断线毁证据。
2. **USB 恢复后先做只读基线复核**（强制）：boot id 是否变化（判断整机是否重启）、五业务/充电策略 PID/NRestarts、`dmesg` 有无 errfatal/pstore/复位痕迹、`mhi1`/wwan/qrtr LOC/EFS 守护/`m9kefs1` 是否还在、`libqmi-utils` 与 15 固件是否完好、GNSS 旧采集目录残留。
3. 若确认为整机重启：`mhi-efs-sync.service` 仍 `disabled` → SDX55 不会自动重新加载，现役服务器服务不受影响（护栏生效）；需重新按步骤 1 的 modprobe + start EFS 守护才能继续（**需用户重新批准该写入**）。
4. **若 USB 不便恢复**：只要 wlan SSH 可用，可临时用 `-o BindAddress=192.168.1.195` 经以太网+路由器连 `192.168.1.122`，无需改 SSH 配置文件。

### 11.3 当前挂起状态

- 设备**不可达**（USB/wlan SSH 均失败，ARP 在线）；本代理**未做任何恢复动作**（未重启/断电/重插/反复重试）。
- 待用户：把 USB 线重新插好，或确认手机屏幕亮、Wi-Fi 连、sshd 起来后告知"再试"。
- 设备恢复前**不再执行任何设备写入或步骤 4 后续**。
- 之前的 90 秒采集（§10.1）因断线不完整，结论作废；go/no-go 判定继续挂起。


---

## 12. USB 恢复复核 + 户外采集启动（2026-11-10 07:52–07:55 UTC）

### 12.1 失联真相：纯网络断开，手机从未重启

用户重插 USB 线后：
- PC `192.168.8.2` 恢复（`以太网 4`）、`ping 192.168.8.1` 通、SSH 恢复。
- **boot id 仍 `811708cb-9ebd-4c92-aaef-3e8c9c01a493`、uptime 连续 14:33 → 手机没有重启/硬复位**。§10.2 的"整机失联"实际是 USB 网卡被扯掉导致的纯网络失联；ARP 里手机 wlan MAC 一直在也印证。
- 完整只读复核（§12 回执）：五业务 `690/709/851/566` 全 active/NRestarts0；`mhi1` 全套通道、wwan 4 端口、qrtr node3 `Location service` 在；EFS 守护 PID 249712/NRestarts0，期间**又完成一次 EFS-Sync**（15:40:52 导出 `m9kefs1`，另有 `m9kefs2`）；15 固件 85 MiB、`libqmi-utils 1.36.0` 完好；`dmesg` 无 errfatal/device died/Linkdown/retrain/panic；pstore 空；无残留 qmicli 进程；旧 GNSS 采集目录 `gnss-capture-20261110T074050Z` 仍在（`/tmp`，0 行日志）。
- **结论：零回归，无需重新批准步骤 1，无需重新 modprobe，状态全部延续。**

### 12.2 户外 GNSS 采集已启动（detached、不依赖实时 SSH）

按 §11.2 修正方案：
- 脚本 `/tmp/sdx55-stage/gnss-outdoor.sh`（本机副本 `local/sdx55-gnss-outdoor.sh`），`nohup bash <file>` 启动（PID 261765，07:53:59 UTC）。
- 输出目录 `/srv/sdx55-gnss-20261110/capture-20261110T075359Z/`（持久 `/srv` 分区，199G 空闲；私有、忽略、不进 Git；重启不丢，避免 `/tmp` 清空）。
- 窗口 780 s（13 min），足够 GPS 冷启动 TTFF；用户无需守电脑。
- 组成：`qmicli -p`（root + `PATH=/usr/libexec:...`）长驻 `--loc-session-id=9 --loc-start --client-no-release-cid` 保持会话；`--loc-follow-nmea` / `--loc-follow-gnss-sv-info` / `--loc-follow-position-report` 三路并行；每 60 s 一次 `--loc-get-gnss-sv-info --loc-timeout=40` 快照（12 次，给出结构化"有/无 SV"证据）；结束自停会话、自清进程、写 `meta.txt` 与 `done`。
- 启动回执：`Successfully started location tracking (session id 9)`、CID 1 保留；`pgrep` 确认 3 个 follow + 快照进程在跑；`snapshot 0 = timeout`（冷启动预期，不判失败）。
- 客户端那个卡住的 SSH launcher 作业（`nohup` 继承 stdout 所致）已 `job_kill`，**不影响设备侧 detached 采集**。

### 12.3 判定标准（采集结束后回收解析）

- **出数（D 可行）**：`nmea.log` 出现 `$G` 系列句子（GGA/RMC/GSV…）、或 `svinfo.log`/`sv-snapshots.log` 出现 SV 列表与 CN0/SNR、或 `pos.log` 出现经纬度 → 与 MODEM.md §4.6 "零测量"旧结论冲突，D 路线成立。
- **零测量（停 D、回方案 C）**：13 分钟、有天空视角、长驻会话下三路全空且 12 个快照全为 timeout/0 SV → 说明 modem 侧策略/配置门也压住 GNSS，按约定停止投入，证据写回 docs，回 P0 方案 C（外接 GPS）。
- **不确定**：若手机其实未真正处于开阔天空视角、或采集中途 USB/网络再断导致数据不全 → 如实标注局限，不硬下结论，必要时约下一次。

### 12.4 当前状态

- 采集运行中（预计 08:07 UTC 前后结束），本代理**未做其它设备写入**；`mhi-efs-sync.service` 仍 `disabled; active(running)`，未 enable 自启。
- 待采集结束 → 回收 `/srv/sdx55-gnss-20261110/capture-.../` 并解析 → 给 go/no-go 结论 → 写入 docs §13。


---

## 13. 用户问"现在有卫星信号了吗"时的状态（2026-11-10 08:05 UTC）


---

## 14. 最终结果：go/no-go 判定（2026-11-10 08:09 UTC 回收）

> 说明：§14–§19 的正文未写入；§20/§21 是后续实际执行记录。§20.4 的结论已被 §21 推翻。

### 14.1 13 分钟户外采集完整结果

`/srv/sdx55-gnss-20261110/capture-20261110T075359Z/`：

---


---


---


---


---


---

## 20. 第 7 轮：LOC 引擎锁修复、GNSS 终局判定、小区位置服务落地（2026-11-10 09:56–10:29 UTC）

> 目标「GPS 可用」。本轮**修正了第 6 轮的一个误判**（`--loc-start` 46 不是无 SIM 硬限制，是可修的引擎锁残留），并把 GNSS 卫星前端不可用的结论做实；同时交付一个**真实可用**的降级位置源（小区定位→经纬度），如实标注非 GNSS。全程可逆、有界，未写 NV/persist/分区、未刷机、未重启。

### 20.1 修正：`--loc-start` 46 GeneralError 是引擎锁残留，非无 SIM 硬限制

- `--loc-get-engine-lock` 一直返回 `missing`（该服务版本不回读锁状态）。
- 执行 `--loc-set-engine-lock=none` 后，`--loc-start` **恢复成功**（session 0/61/1 全部 `Successfully started`）。第 6 轮切 `wwan` 模式导致引擎锁残留，是 46 的成因，已修复。
- 完整重置（lock all→mi→mt→none、opmode msb→msa→default→standalone、删辅助数据）后，175 秒 + 90 秒单/双流 `--loc-follow-nmea` / `--loc-follow-gnss-sv-info` / `--loc-get-position-report` **仍全部 0 行/timeout**。
- 结论：LOC 引擎**可正常启动、可配、可跟随**，但 GNSS 卫星前端不出任何测量。与 MODEM.md §4.6.1「GNSS engine runs but zero measurements，NV 修复结论已证伪」一致。

### 20.2 QMI LOC 底层消息注入（手工构造，验证 modem 接受）

- 从 libqmi 上游 `data/qmi-service-loc.json` 取得消息定义：`Inject UTC Time` = **0x0038**（TLV 0x01 u64 UTC ms、0x02 u32 uncertainty、0x10 i32 source）、`Inject Position` = **0x0039**（TLV 0x10 gdouble lat、0x11 gdouble lon、0x12 f32 accuracy）。
- 写 `gps-r7-inject.py`，用 Python `socket.AF_QIPCRTR(42)/SOCK_DGRAM` 直接构造 QMUX+QMI 帧发到 `(node=3, port=84, service=16 LOC)`，避开 qmicli 1.36 未暴露的命令。
- 两条消息**均被 modem 接受并返回 14B 结构化响应**（非 BAD_CMD/timeout）。但注入后 `--dms-get-time` 仍 `2017-05-27 / source=device`，GNSS 仍零测量 → 注入未改变引擎所需时间源，或返回的是拒绝（响应解析未最终确认 SUCCESS）。
- 相关发现：`--dms-get-time` 报 modem 时间 **2017-05-27**（device 源）、`--nas-get-network-time` 报 74 `InformationUnavailable`（无 SIM 无 NITZ）—— GNSS 冷启动缺有效 UTC 时间是客观事实，但注入未绕过。

### 20.3 真实可用的降级位置源（本轮主要交付，如实标注非 GNSS）

- `--nas-get-cell-location-info` 返回**真实射频测量**：主小区 `Cell ID 293 / PLMN 46000 / LAC 9441 / ARFCN 52 / RX -98dBm`，外加 4–6 个邻区（各带 CID/LAC/ARFCN/RX），实时变化。
- 设备侧 `kebab-cell-loc.py` 解析并原子发布 `/run/kebab-cell.json`（`source: cellular-network; NOT GNSS`）。
- PC 侧 `gps-cell-lookup.ps1`（走既有 `127.0.0.1:12080` 代理）把小区 POST 到 **beaconDB**（MLS 兼容接口），得到 **`lat 23.043629, lon 113.38371, accuracy 148 m`**（广州，与环境一致）。
- 结果写入本机 `local/gps-fix-20261110/cell-position.json` 并推送设备 `/run/kebab-cell-position.json`，字段含 `source: "cellular-network (beaconDB MLS-compatible); NOT GNSS satellites"`、`timestampUtc`、`cells: 4`。双端复读一致。
- **这不是 GPS/GNSS 卫星定位，不冒充完成目标**；但它是主机侧真实可消费的位置能力（基于本机 modem 实测小区），可喂 gpsd/应用。

### 20.4 终局判定（**已于 2026-11-10 晚被 §21 推翻，保留原文位置以记录误判**）

> **更正**：GNSS 零测量不是“缺有效 SIM / 时间源”的硬限制。真实原因是采集侧不是 LOC 会话的 owner（`qmicli --loc-follow-*` 另起进程收不到 indications），加上引擎锁枚举错误与默认单次 fix。改用单客户端监听、`lock=none`、`periodic=1` 后，室外已稳定出 1 Hz fix（§21）。

原文（错误结论）：GNSS 卫星测量所需条件逐项核对：固件链 ✓、真实 EFS/NV ✓、RFS/MCFG ✓、PDC/online ✓、真实小区注册 ✓、QRTR LOC 服务 ✓、LOC 引擎可启动/可配 ✓、NMEA 全开 ✓、时间/位置注入已尝试 ✗（未生效）、有效 SIM ✗（物理缺席，两槽 `absent / no-atr-received`）、NITZ 网络时间 ✗。原文推断“剩余缺口是有效 SIM / 有效时间源”已被 §21 的实测定性推翻。

### 20.5 状态、备份、回退

- boot `08ac9d41…` 不变、8 unit 全 active、3 个 SDX55 服务 `disabled`、`online`、无 errfatal、磁力计三轴出数。
- 主备份 19 项 SHA OK、`gps-rollback.sh` + `gps-round4-rollback.sh` 语法 OK。
- 本轮新增（均私有/忽略目录）：设备 `/srv/gps-fix-20261110/`（`gps-r7-*.sh`、`gps-r7-inject.py`、`kebab-cell-loc.py`、`cell-position.json`、`qmi-service-loc.json` 副本）、`/run/kebab-cell*.json`；本机 `local/gps-r7-*`、`local/gps-cell-lookup.ps1`、`local/gps-fix-20261110/{qmi-service-loc.json,cell-position.json,cell-req.json}`、`local/gps-kebab-cell-loc.py`。
- 回退：`sudo -n bash /srv/gps-fix-20261110/gps-rollback.sh`（RFS/PM）+ `gps-round4-rollback.sh`（EFS 占位）+ 删除本轮 `/run/kebab-cell*.json`。无持久系统单元、无开机自启被改动。
- 私有数据未提交/上传、未写 NV/persist、未刷分区、未重启、未迁 7.3。

---

## 21. 2026-11-10 晚：GNSS 真正打通、常驻服务与 GeoClue 接入（最终结果）

> 本节推翻 §20.4。结论先行：**SDX55 的 GNSS 卫星定位已经可用**，室外稳定 1 Hz 出 fix，水平精度 HDOP 1.5；不是小区/网络定位。

### 21.1 误判更正：为什么之前一直“零测量”

三个采集侧原因，全部可复现：

1. **不是 LOC 会话 owner**：LOC 的 NMEA/SV/position indications 只发给拥有 session 的客户端。`qmicli --loc-follow-*` 另起进程不是 owner，只打印 `cancelling the operation...`；因此早先所有 “0 行 / timeout” 都是采集方法错误，不是引擎不出数。
2. **引擎锁枚举写错**：把 lock 设为 `0`（无效）会得到 `ENGINE_LOCK (1, 48)`，引擎停在 state 2。正确是 `none = 1`（`Set Engine Lock` TLV 0x01 u32）。
3. **默认单次 fix**：不显式设置 recurrence 时引擎按 single fix（2）跑，出第一个 fix 后会话停止。正确是 periodic=1 + intermediate=1 + interval=1000 ms（`LOC_START` TLV 0x10/0x12/0x13）。
4. （附带）XTRA/A-GPS 注入仍被拒：`0x00A7`（Inject Xtra Data）与 `0x0035`（Inject Predicted Orbits）在本固件返回 `result=(1,1)`（general-error / invalid-parameter），所以冷启动仍需约 2 分钟，暂无 A-GPS 加速。

### 21.2 实测结果（outdoor4，2026-11-10T12:46:44Z–12:56:44Z，600 s）

| 指标 | 结果 |
|---|---|
| NMEA 行数 | 3715 |
| 有效 GGA | 460（1 Hz 连续） |
| 有效 RMC | 444 |
| `POS_REPORT status=0` | 433 / 562 |
| 可见卫星 | 峰值 42（`SV_COUNT` 8→42） |
| 星历解出（`nav_hi` 累计） | 25,945 |
| 首个 fix | 开始后约 2 分钟 |
| 最后 GGA | `$GPGGA,125644.00,2302.751466,N,11322.785459,E,1,05,1.5,5.5,M,-4.0,M,,*71` |
| 坐标 | 23.04585776 N, 113.37975765 E |
| 水平精度 | HDOP 1.5（参与解算 5 颗；可见 42 颗） |
| 高度 | 5.5 m（海拔字段；单点，未做长时统计） |

对照：outdoor2（11:59:52Z）首次证明能出 fix（501 行 NMEA，1 条 GGA）；outdoor3 证明单次 fix 后会停（`RECURRENCE 2`），outdoor4 用 periodic 模式跑满 10 分钟。

### 21.3 常驻位置源（已部署）

- 守护进程：`/usr/local/lib/kebab-gps/kebab-gpsd.py`（Python 3，单客户端 QMI LOC；AF_QIPCRTR=42，node 3 port 84）。
- 单元：`kebab-gps.service`，已 `enable`，当前 `active`。
- 行为：注册 LOC events；`engine lock=none`；`opmode=standalone`；`LOC_START` session 0x4b、periodic=1、intermediate=1、interval=1000 ms；每 10 s 注入 UTC 时间；首次 fix 前每 60 s 注入小区位置（`/run/kebab-cell-position.json`，beaconDB，±148 m）；NMEA 断流 >30 s 自动重启 session。
- 发布：
  - `/run/kebab-gps.json`（原子写，0644）：`ts,fix,lat,lon,alt_m,hdop,sats_used,sats_visible,speed_mps,track_deg,source,engine_state,session,restarts,nmea_ok,error`。
  - `/run/kebab-gps.sock`（Unix stream，0666）：原始 NMEA 0183，每行 CRLF；支持多客户端。
- 验证命令：
  - `cat /run/kebab-gps.json`
  - `systemctl status kebab-gps`
  - `timeout 3 python3 -c 'import socket; s=socket.socket(socket.AF_UNIX); s.connect("/run/kebab-gps.sock"); print(s.recv(200))'`
- 室内（无 fix）行为已验证：`fix:false`，`sats_visible` 约 24–26，`engine_state:1`，`nmea_ok:true`，socket 持续有 `$GPGSV` 与空 `$GPGGA`。

### 21.4 GeoClue 接入（已打通，应用层可用）

- 离线安装（设备 DNS 故障，未改系统 DNS；.deb 从 Debian pool 下载并校验 SHA256）：
  - `geoclue-2.0 2.7.2-2`、`libgeoclue-2-0 2.7.2-2`、`geoclue-2-demo 2.7.2-2`、`libavahi-glib1 0.8-16`、`avahi-daemon 0.8-16`（+ `libavahi-core7`、`libdaemon0`）。
  - **没有**安装 ModemManager / polkitd / NetworkManager / iio-sensor-proxy。
- `/etc/geoclue/geoclue.conf`（原文件备份为 `geoclue.conf.pre-kebab-gps`）：
  - `[network-nmea] enable=true`、`nmea-socket=/run/kebab-gps.sock`；
  - `[3g]`、`[cdma]`、`[modem-gps]`、`[wifi]`、`[compass]`、`[static-source]` 全部 `enable=false`（无 MM/NM/DNS 环境，避免噪声源）。
- 代理：`kebab-geoclue-agent.service` 以 `User=debian` 运行 `/usr/libexec/geoclue-2.0/demos/agent`，已 `enable`。**必需**：GeoClue 在 `GetClient` 时会等该 UID 的 agent；没有 agent 时客户端会卡在 `Client ... waiting for agent for user ID '1000'`。
- **精度门槛（关键）**：GeoClue 按客户端请求的精度决定是否唤醒 GPS。客户端必须把 `org.freedesktop.GeoClue2.Client.RequestedAccuracyLevel` 设为 **8（exact）**；设为 4（street）时日志明确 `Not starting GClueNMEASource (accuracy level: 8). Requested accuracy level: 4.`，不会启动 GPS。
- 端到端验证（用 `--simulate` 合成 NMEA，避免等天空）：
  - GeoClue 调试日志：`GClueNMEASource now active` → `Trying to connect to NMEA socket service /run/kebab-gps.sock:0.` → `NMEA service connected.` → `New location available from GClueNMEASource`。
  - 客户端读到：`Latitude 23.0458578`、`Longitude 113.3797577`、`Altitude 5.0`、`Description "GPS GGA+RMC"`。
  - 真实守护进程（室内）验证：GeoClue 同样连上（`CLIENT+`），无 fix 时客户端正确返回“无位置”。
- 客户端示例：`local/kebab-gps-geoclue-test.py`（D-Bus/Gio；GetClient → DesktopId=firefox → RequestedAccuracyLevel=8 → Start → 读 Location）。普通应用要拿到 GPS，同样需要请求 exact 精度。

### 21.5 与 Android 的差距（更新）

已经追平/接近的：
- 卫星解算、RF、校准、MCFG、EFS 全是原厂 SDX55 固件；AP 侧用主线 Linux + 自写 QMI 客户端，不依赖 Android `libloc`/GNSS HAL。
- 1 Hz 连续 fix、NMEA、可见星/参与解算星、HDOP/高度、GeoClue D-Bus 位置 API。

仍缺的：
- **A-GPS/XTRA/SUPL**：注入被本固件拒绝；冷启动约 2 分钟，室内/弱信号更慢。
- **Wi-Fi/基站融合**：GeoClue 的 WiFi 源被禁用，GeoIP 源因设备 DNS 不可用而无法工作；目前只有手动注入的小区位置（±148 m）。
- **应用覆盖面**：只有请求 exact 精度的客户端才会触发 GPS；Firefox/Phosh 等实际行为未逐一验证。
- **长时/动态/功耗**：本次是静止 10 分钟；步行/驾车、高度准确性、连续功耗与发热未验收。
- **多频/双频、SBAS、RTK**：未验证。

### 21.6 文件与回退

- 本机（工作区）：`local/kebab-gpsd.py`、`local/kebab-gps.service`、`local/kebab-geoclue-agent.service`、`local/geoclue.conf.kebab-gps`、`local/kebab-gps-geoclue-test.py`、`local/geoclue-debs/`。
- 设备：`/usr/local/lib/kebab-gps/kebab-gpsd.py`、`/etc/systemd/system/kebab-gps.service`、`/etc/systemd/system/kebab-geoclue-agent.service`、`/etc/geoclue/geoclue.conf`。
- 回退：
  - `sudo systemctl disable --now kebab-gps kebab-geoclue-agent`
  - 恢复 `/etc/geoclue/geoclue.conf.pre-kebab-gps`
  - 如不需要 GeoClue：`sudo apt purge geoclue-2.0 geoclue-2-demo libgeoclue-2-0 libavahi-glib1 avahi-daemon libavahi-core7 libdaemon0`（注意 `avahi-daemon` 可能被其它包依赖，purge 前先 `apt-get -s` 检查）。
  - 未写 NV/persist/分区、未刷机、未重启；QMI LOC 会话停止即释放。

### 21.7 当前状态

- `kebab-gps`、`kebab-geoclue-agent` 均已 `enable` 且 `active`；`geoclue` 由 D-Bus 激活，空闲 60 s 自动退出；`avahi-daemon` active。
- 既有服务未动；唯一 failed 是历史遗留 `kebab-cnb-domain.service`。


