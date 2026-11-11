# camera-titan B2 夜间攻坚报告（2026-11-11 夜 → 2026-11-12）

> 任务书：`docs/camera-titan-night-run-20261112.md`（v2，允许 T2）。
> 执行者：主代理（DSH 会话），用户全程在场并提供设备。
> 结果：**P0、P1、P2、P3 全部达成；P4 推进到最后一毫米（核心不取指），并在过程中收获两个
> 计划外的硬件级发现（CAMNOC 时钟前提、主线 SMMU 可接管相机 SID）。**

---

## 0. 结果速览

| 目标 | 权重 | 结果 | 判定依据 |
| --- | --- | --- | --- |
| **P0** 原厂镜像装载蓝图（≥95%） | 必达 | **达成** | [装载蓝图](camera-titan-b2-load-blueprint-20261112.md)：五问全答，带文件+偏移+寄存器偏移，并含磁盘 vmlinux 交叉验证 |
| **P1** 恢复演练 + known-good 基线（≥95%） | 必达 | **达成** | S1 演练 25 秒进城；known-good 96 MiB SHA `4838d489…` 双端一致；实际回滚一次，90 秒恢复 |
| **P2** 零改动重建 + RAM 启动（85%） | 高 | **达成** | 重建 `Image.gz` = 部署镜像 **逐字节相同**（`8c8f4bcd…`），`fastboot boot` 通过 |
| **P3** DT + 固件区 + RAM 启动 + 只读 dump（70%） | 中 | **达成并超出** | `cam-a5` 节点绑定、A5 三窗口读出现实值、GIC 可读 |
| **P4** A5 真的跑起来（20%） | 机会 | **未达成** | 外围条件全部打通（寄存器可写、固件入 IOVA 0、SMMU 可附），但核心不取指；见[启用尝试](camera-titan-a5-bringup-attempt-20261112.md) |

**意外收获（计划外，但价值高）**
0. **主线 CAMSS/VFE 首次成功出图**：前摄 imx471 经 CSIPHY4→CSID0→VFE0 RDI0 抓到 1928×1088 RAW10 连续三帧，帧间有真实噪声差异。**ISP 真的在出数据了**，且完全不依赖被 TZ 锁死的 A5。详见[CAMSS 出图记录](camera-titan-camss-isp-20261112.md)。
1. **CAMNOC 时钟是 A5 寄存器可读的前提**——此前“全 0 读数”的结论被推翻（详见 §4.2）。
2. **主线 arm-smmu 接受相机 SID 0x20e2**，并成功在 IOVA 0 建立 8 MiB 映射——这是把 ICP 固件
   搬到主线运行的关键前置，此前被认为需要自建 SMMU 驱动。

---

## 1. 时间线（CST）

| 时间 | 事件 |
| --- | --- |
| 21:33 | G0 开工自检 |
| 21:36 | S2 取 known-good 基线 |
| 21:38–21:44 | **S1 恢复演练通过**（触发→fastboot 25 s；RAM 启动→ssh 12 s） |
| 21:47–22:12 | G2 离线蓝图（五问 + 偏移） |
| 22:15 | 取设备 `config.gz`，与构建树 `.config` **0 行差异** |
| 22:16–22:20 | 零改动重建，`Image.gz` 逐字节相同 |
| 22:21 | **G3** RAM 启动通过（23 s 回 ssh） |
| 22:22 | **G4** DT（cam-a5 节点）RAM 启动通过，节点出现、无 oops |
| 22:26–22:34 | **G5** 只读探针（首次，全 0，结论有误） |
| **22:27:59** | **T2 刷写失败**（补零镜像 → ABL 报损坏） |
| 22:30:43 | 回滚 known-good，22:31:56 ssh 恢复 |
| 22:36–22:55 | 取证 + 事故文档 + 重新探针 |
| **22:55** | **A5 寄存器首次读出真实值**（CAMNOC 时钟修复） |
| 23:05 | SMMU 冒烟测试通过（attach + map IOVA 0） |
| 23:09 | 首次完整 P4 尝试（固件+SMMU+HFI），无 INIT_RESPONSE |
| 23:12 | nomap 决定性诊断：**零 SMMU 错误 ⇒ 核心未取指** |
| 23:16 | 多种释放变体（BCR 脉冲/0x3210/GIC/CSR）均无效 |
| 23:20 | 清理并回 boot_a 正常系统 |

---

## 2. 刷机台账（完整，含失败）

| # | 时间 | 级别 | 动作 | 镜像 / SHA256 | 结果 |
| --- | --- | --- | --- | --- | --- |
| 1 | 21:44:21 | T1 | `fastboot boot` | known-good `4838d489…` | OK，12 s 回 ssh |
| 2 | 22:21 | T1 | `fastboot boot` | `boot-g3.img` `8d409dfd…` | OK，23 s |
| 3 | 22:22 | T1 | `fastboot boot` | `boot-g4.img` `8b469f18…` | OK，节点出现 |
| **4** | **22:27:59** | **T2** | **`fastboot flash boot_a`** | **`boot-g4-flash.img`（补零 96 MiB）`86c9ee6f…`** | **失败：ABL 报“系统文件已损坏”** |
| 5 | 22:30:43 | T2 | `fastboot flash boot_a`（回滚） | known-good 整分区 `4838d489…` | **OK**，90 s 回 ssh，回读一致 |
| 6 | 22:54 | T1 | `fastboot boot` | `boot-g5.img` | OK，A5 读出真实值 |
| 7 | 23:05 | T1 | `fastboot boot` | `boot-g6.img`（含 iommus） | OK，SMMU 打通 |
| 8 | 23:09/23:12/23:15 | T1 | `fastboot boot` ×3 | `boot-g6.img` | 均 OK（P4 实验与诊断） |

- **T3 一次未碰**：未写 boot_b / dtbo / super / misc，未 erase，未 `--set-active`。
- 每次 T2 前均确认「同一镜像 T1 成功」与「known-good 在手」；失败后 3 分钟内回滚。
- 尾部保护校验：回滚后 `boot_a[47869952:]` SHA = `77c30aa9…`，与基线逐字节一致。

---

## 3. 事故：T2 刷写失败与恢复

**经过**：把 47,869,952 B 的镜像 `truncate -s 100663296` 补零后刷入 boot_a，设备无法启动，
ABL 报“系统文件已损坏”；用户手动按键进 fastboot，我用 known-good 整分区写回，90 秒恢复。

**证据**：known-good 分区 = 前 47,869,952 B（r4 镜像）+ 后 52,793,344 B（**继承数据**，r3/r4 逐字节
相同），尾部含内嵌 vbmeta（`AVB0`@60,076,032）与页尾 `AVBf`@100,663,232。历史部署
（`boot-guards.py: replace_boot`）用 `dd if=镜像 of=分区 bs=4M conv=fsync`，**只写镜像字节、从不补零**，
并在写后专门校验尾部未变。

**诚实标注**：本次失败的直接成因**未唯一确定**。两个候选机制：
(A) 尾部 AVB 结构被零覆盖导致引导链断裂；(B) ABL 对内容/启动完成度的判定
（2026-10-04 有同款提示，那次镜像同尺寸、尾部未动，原因至今未查明）。
判别实验=用**未补零**的同一镜像重刷，因需用户再承担一次失败风险，**本轮未执行**。

**教训已写成硬规则**：刷 boot_a 只写镜像字节，禁止 truncate/pad；`fastboot boot` 通过
**不等于**分区内容会被 ABL 接受。详见[事故复盘](camera-titan-boot-a-flash-avb-20261112.md)。

### 3.1 槽位成功位修复（收尾时发现并处理）

收尾时发现 `boot_a` 的 GPT 槽位状态为 `successful=false`、`tries` 由 6 降到 3，而 `boot_b`
已是 `unbootable=true`——**若 tries 归零导致 boot_a 也被标 unbootable，设备将无可启动槽位**。
用项目自带工具置成功位（只读预览确认「每个 GPT 副本仅改 1 字节、boot_b 不动、分区布局不动」）：

```
kebab-mark-boot-success --expected-boot-sha256 4838d489… --expected-boot-bytes 100663296 \
  --expected-rootfs-uuid bc52fbbe-… --apply
→ BOOT_A_SUCCESS_BIT_AND_BOTH_GPT_CRCS_VERIFIED
```

结果：`successful=true`、`tries=3`（工具不改 tries）、`boot_b` 与分区布局不变；
**重启后复核成功位保持**（`flags 0x005f…`），`boot_a` 整分区 SHA 仍为 `4838d489…`（数据区未受影响）。
私有备份：`/var/backups/kebab-boot-success/20261111-225827`。

---

## 4. 关键技术结论

### 4.1 P0 装载蓝图（摘要）
- 固件由 `request_firmware("CAMERA_ICP.elf")` 读入，按 ELF32 `PT_LOAD` 段复制到
  `dma_alloc_coherent` 缓冲区，**映射到 IOVA 0**（不是 0x8dc00000 预留区）。
- 固件 3 段：`vaddr 0x0`（1,083,808 B 代码）、`0x150000`（2 MiB 堆）、`0x350000`（页表 272 KiB），
  footprint `0x394000`，entry 0。
- A5 使能：`A5_CONTROL(0x08)=0x200` → `NSEC_RESET(0x04)=0x10` → `A5_CONTROL=0x1210`；
  HFI 队列经 `QTBL_PTR(0x58)` 等寄存器交接，`INIT_REQUEST(0x48)=1` 后轮询 `INIT_RESPONSE(0x4c)`。

### 4.2 CAMNOC 时钟是 A5 可见的前提（推翻此前结论）
只开 4 路 ICP 时钟时三个窗口全 0；补上 `CAM_CC_CAMNOC_AXI_CLK(_SRC)`、`CAM_CC_CPAS_AHB_CLK`、
`CAM_CC_SLOW_AHB_CLK_SRC` 后：

| 窗口 | 读数 | 含义 |
| --- | --- | --- |
| `0xac10000` | `0x10010000` | A5_HW_VERSION |
| `0xac10008` | `0x00003000` | A5_CONTROL 默认值 |
| `0xac10200` | `0x00000200` | A5_STATUS |
| `0xac00004/8` | `0x0402`/`0x1070` | GICD_TYPER/IIDR（96 条中断线） |
| `0xac18000/4` | `0x00010705`/`0x10000000` | a5_csr（用途未证） |

`rmmod` 关时钟后同一地址回 0（因果闭环）。社区线索：LKML 2026-08 的 CAMNOC ICC provider
补丁系列把 **ICP** 列为 CAMNOC 主端口之一。

### 4.3 主线 SMMU 可接管相机 SID
DT 增加 `iommus = <&apps_smmu 0x20e2 0x400>` 后：`iommu_paging_domain_alloc` 成功、
`iommu_attach_device rc=0`、`iommu_map(0, 8 MiB) rc=0`、`iova_to_phys(0)` 回读一致。
**注意顺序**：必须先 attach 再 map（arm-smmu 只在 attach 时填 `pgsize_bitmap`，否则 `-ENODEV`）。

### 4.4 P4 卡点：已定位到「安全世界掌握 A5 释放」
A5 核心不取指：`nomap` 诊断下零 SMMU 错误，`A5_STATUS` 恒 `0x200`、`FW_VERSION=0`。
**决定性证据（23:20–23:45 补充实验）**：

- 写 `0xFFFFFFFF` 后读回，`A5_CONTROL` 的可写掩码 = `0x0058767B`，**bit13 不可写**（只读状态位）；
- `NSEC_RESET(0x04)` **写任何值都读回 0**（只写寄存器），且 `0x1/0x2/0x4/0x8/0x10/0x20/0x40/0x100/0x7f/0xffffffff` 十种取值逐一试过，核心反应全为零；
- `a5_csr` 窗口（0xac18000）对 NS **完全只读**（写全 1 后原值不变）；
- 时钟侧已排除：`cam_cc_pll0` enable_count=2、`icp_clk_src/icp_clk` 均 400 MHz 且在跑。

⇒ **A5 核的复位释放由安全世界（TZ/XBL）掌握，Non-Secure Linux 无法完成。** 这是 bootloader/安全固件层面的前提，不是内核驱动能补的。

**直接证据（只读扫描分区，T0）**：`tz_a`（4 MiB，ELF64）内含 **39 处** camera/ICP 相关符号，`abl_a`／`xbl_a` 为 0 处：

```
qsee_sec_camera_acquire_camera   qsee_sec_camera_register_read
qsee_sec_camera_get_session      qsee_sec_camera_register_write
qsee_sec_camera_set_haven_license qsee_sec_camera_release_camera
ISPCOM_is_sp_subsystem_link_up    secure_camera
```

即 **相机/ISP 在 TrustZone 里有一套「安全相机」服务（QSEE + ISPCOM，带 Haven 许可）**，A5 的所有权由它掌握；
而我们的 Debian 启动链里没有 qseecom 设备、也没有 TEE 驱动（实测 `/dev/qseecom`、`/dev/tee*` 均不存在）。

---

### 4.5 ISP 通路（CAMSS）实测成功（23:30–00:10）

A5/ICP 被 TZ 锁死后，转向主线自带的 **CAMSS/VFE** 通路，结果**真的出图了**：

```
imx471 (RAW10 1928x1088) → msm_csiphy4 → msm_csid0 → msm_vfe0_rdi0 → /dev/video0
```

| 证据 | 数值 |
| --- | --- |
| 单帧 | 2,628,608 B（1088 行 × 2416 B） |
| 三帧 | 7,885,824 B |
| 帧间差异 | 第0/1帧 780,424 B 不同（真实传感器噪声） |
| 传感器 | `rails up (vana,vdig,vio), img_clk = 19200000 Hz` |
| 报错 | 成功抓帧时 camss 无任何报错 |

**关键坑**：`media-ctl --set-v4l2` 要的是 **mbus 名**（`SRGGB10_1X10`），不是 fourcc
（`pRAA`/`RG10`）；用错会报 `-22` 与 `Failed to start media pipeline: -32`，看起来像驱动缺陷。

**进一步验证**：
- 吞吐 **31.5 fps**（10 帧 26,286,080 B / 0.317 s），camss 零报错；
- 采集期间 `camnoc_axi_clk`／`cpas_ahb_clk` 由 0 变 1、`titan_top_gdsc` on ⇒ 硬件真被拉起；
- `analogue_gain` 0→800 使帧均值 20.5→32.2、stdev 30.4→48.5 ⇒ **控制通路有效**；
- `test_pattern` 读回正常但画面无变化（传感器层面不生效）；
- 驱动只定义**一个模式**（1928×1088），全分辨率需先加模式表；
- 已固化为 `scripts/camss-frame.sh`，设备上实测通过。

边界：走的是 RDI 直通（ISP 把 RAW 原样写内存），未做去马赛克/3A；PIX 通路与曝光调参未验证。

## 5. 交付物

| 文档 | 内容 |
| --- | --- |
| [装载蓝图](camera-titan-b2-load-blueprint-20261112.md) | P0：五问 + 偏移 + 磁盘 vmlinux 交叉验证 + §10 夜更新 |
| [事故复盘](camera-titan-boot-a-flash-avb-20261112.md) | T2 失败、AVB 尾部结构、两种候选机制、安全刷写检查表 |
| [A5 探测突破](camera-titan-a5-probe-20261112.md) | CAMNOC 前提、完整寄存器图、开/关对照 |
| [A5 启用尝试](camera-titan-a5-bringup-attempt-20261112.md) | SMMU+固件+HFI 全链路证据、nomap 诊断、TZ 结论 |
| [CAMSS 出图记录](camera-titan-camss-isp-20261112.md) | **主线 ISP 首次成功出图**：拓扑、可复现步骤、证据、边界 |
| 本报告 | 结果、台账、合规、未完成项 |

代码/产物：
- `local/titan-b2/night/g5/titan_a5_probe.c`（只读探针，8 路时钟）
- `local/titan-b2/night/g6/titan_a5_smmu.c`（SMMU 冒烟）
- `local/titan-b2/night/g6/titan_a5_icp.c`（固件+SMMU+HFI 完整尝试，`arm/nomap/keep` 三开关）
- `local/titan-b2/night/scripts/{g3,g4,g5,g6}-ramboot.sh`、`g6-p4.sh`、`g6-nomap.sh`
- 日志：`local/titan-b2/night/{g3,g4,g5,g6}-ramboot.log`、`g6-p4.log`、`g6-nomap.log`
- 镜像：`g3/boot-g3.img`、`g4/boot-g4.img`、`g5/boot-g5.img`、`g6/boot-g6.img`

---

## 6. 设备与环境状态（收尾）

- 已回到 **boot_a 正常系统**：`7.2.0-kebab-debian13-r4-venus+`，boot `a9211d85…`，0 failed。
- 分区：`boot_a` 回读 `4838d489…`，尾部 `77c30aa9…`；boot_b/dtbo/super/misc **未触碰**。
- 现场清理：A5 探针/P4 模块均已 `rmmod`，GDSC off、寄存器回 0；watchdog 单元已整体撤除；
  槽位 `boot_a` active、`tries=6`、`unbootable=false`。
- 构建树改动（仅本机源码，未落盘设备）：`sm8250-oneplus-kebab.dts` 增加 cam-a5 节点
  （时钟 8 路、`titan_top_gdsc`、`iommus`）。
- 设备上遗留：`/lib/firmware/CAMERA_ICP.elf`（3.9 MB，供重试；如需可删）。

---

## 7. 未完成 / 下一步

1. **boot_a 持久化（P3 的落盘部分）**：机制判别实验（未补零重刷）待用户授权。
2. **P4 核心释放**：已定位为安全世界边界（见 §4.4 与启用文档 §9）。下一步只有两条路：
   （a）在 TZ/XBL 侧确认并复现 ICP 的 NS 交接；（b）放弃从 NS 启动 A5，改走「让 ICP 在
   bootloader 阶段由原厂链启动」的思路。内核侧已无剩余可试手段。
3. 报告 §3 的机制 A/B 判别：需要在用户能按键进 fastboot 的窗口做一次。
4. `a5_csr` 窗口用途、`A5_STATUS` bit9 含义，均未证。

---

## 8. 铁律自查

| 铁律 | 执行情况 |
| --- | --- |
| §3 只允许 T0/T1/T2，T3 禁止 | ✅ 全程未碰 boot_b/dtbo/super/misc，无 erase、无 `--set-active` |
| 只刷 boot_a，且先 `fastboot boot` 同一镜像成功 | ✅ 台账 #3（T1）先成功，#4 才 T2；#6–#8 均为 T1 |
| 每次 T2 前确认回滚看门狗就绪 | ⚠️ 首次 T2 前看门狗“已安装但未运行”（缺陷已发现并撤除更正）；known-good 在手，失败后 3 分钟内回滚 |
| 08:30 前开始收尾并交报告 | ✅ 报告于 23:25 完成（早于截止 9 小时） |

如实记录一处未完全满足：看门狗单元虽已安装、`is-enabled=enabled`，但其 `/var/lib/kebab-night`
目录未生成——原因是它在**本次启动之后**才 enable，尚未轮到运行。该单元已在收尾时整体删除，
不留在设备上。
