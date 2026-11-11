# A5/ICP 首次从主线启用尝试（B2 夜，2026-11-12）

> 结论先行：**打通了 A5 从 AP 侧的全部“外围”条件**——寄存器可读写、固件可装入 IOVA 0、
> SMMU 域可建可附、HFI 队列可编程——但 **A5 核心始终不取指**（零 SMMU 错误、零中断），
> 卡在最后一步“核心释放”。本文给出完整证据链与可复现方法，并把下一步缩到最小。

---

## 1. 本轮达成的四件事（都是第一次）

| # | 结果 | 证据 |
| --- | --- | --- |
| 1 | **A5 寄存器可正常读写** | 写 `A5_CONTROL=0x1210` 读回 `0x1210`；写 `QTBL_PTR=0x400000` 读回 `0x400000`；`INIT_REQUEST` 置 1 后保持 |
| 2 | **主线 SMMU 接受相机 SID** | DT 加 `iommus = <&apps_smmu 0x20e2 0x400>`；`iommu_attach_device rc=0`，pgsize 4K/64K/2M |
| 3 | **固件装入 IOVA 0** | `CAMERA_ICP.elf` 3 段按 `p_vaddr` 复制；`iommu_map(iova 0, 8 MiB) rc=0`；`iova 0 -> phys 0xff800000` 回读一致 |
| 4 | **HFI 队列表可编程** | QTBL 头 + 3 个队列头写入；`QTBL_PTR` 读回 0x400000 |

## 2. 决定性实验：A5 到底有没有取指？

**方法**：域照样 attach，但**故意不映射 IOVA 0**（`nomap=1`），然后照常释放核心。
如果 A5 有任何取指，SMMU 必然报 context fault。

**结果**：

```
titan-a5-icp: nomap=1: domain attached but IOVA 0 left unmapped
titan-a5-icp: arm=1: A5_CONTROL=0x200 then NSEC_RESET=0x10
titan-a5-icp: after A5_EN: CONTROL=0x3000 STATUS=0x200
titan-a5-icp: QTBL=0x400000 readback=0x400000, INIT_REQUEST set
titan-a5-icp: INIT_RESPONSE=0x0 after 1000 ms; FW_VERSION=0x0 ...
```

**dmesg 中 arm-smmu 的 fault 计数 = 0**（`grep -aic 'arm-smmu.*fault' = 0`）。

⇒ **A5 核心没有发出任何内存访问**。问题不在固件、不在内存布局、不在 SMMU 映射，
而在“核心没有被真正放出来”。

## 3. 已经排除的原因

| 排除项 | 依据 |
| --- | --- |
| 电源（GDSC） | `titan_top_gdsc` on；读 `A5_HW_VERSION=0x10010000` 正常 |
| 时钟 | `cam_cc_icp_clk=400 MHz`、`cam_cc_icp_ahb_clk`、CAMNOC/CPAS 时钟全部 enabled |
| 相机 SID 归属 | attach rc=0，且 `iova_to_phys` 回读正确 |
| 固件格式 | ELF32/ARM、entry 0、3 段全部按 vaddr 摆放，footprint 0x394000 |
| 队列表 | QTBL/CMD/MSG/DBG/SFR/SHMEM/HEAP 全部在映射区内并写正确 |
| SFR 头 | 已按原厂补写 `size=4096` |
| ICP BCR | `camcc 0xc070 = 0`（已处于 deassert） |
| 控制位组合 | 试过 `0x200`、`0x1210`、`0x3210`、`0x3200` 四种，`A5_STATUS` 恒为 `0x200` |
| 写屏障 | 从 `writel_relaxed` 改为 `writel`（带屏障），写入落地 |
| A5 GIC | 把 `0xac00000` 的 `GICD_CTLR` 置 1 再释放核心，仍无取指 |
| 复位脉冲 | `NSEC_RESET` 先 0 后 0x10 脉冲、ICP_BCR 脉冲，均无效 |

## 4. 仍存疑的三条线索

1. **`A5_CONTROL` 默认值 `0x3000`**：bit12（`EN_CLKGATE_WFI`）与 **bit13（头文件未定义）** 同时置位。
   写 `0x200` 后立刻读回仍是 `0x3000`，写 `0x1210` 才“粘住”。bit13 可能是硬件复位态标志。
2. **`A5_STATUS` 恒为 `0x200`**：bit7（WFI）从未置位，说明核心从未进入 WFI——
   与“从未执行”一致；bit9 含义未知，可能表示“core held”。
3. **`a5_csr` 窗口 0xac18000**：`+0x00=0x00010705`、`+0x04=0x10000000`，原厂驱动从不写它。
   试着把 `0x10000000` 清零再写回，无变化。该窗口用途仍未知。

## 5. 最可能的原因（按可能性排序）

1. **核心释放需要 TZ/安全世界的配合**：OnePlus 的 Android 启动链里，TZ 应用可能在开机时
   配置 ICP 的 NS 所有权与启动地址；我们只跑 ABL→Linux，没跑这条链。
   （反证：A5_CONTROL 写能落地，说明 NS 侧并非完全无权。）
2. **缺少“启动地址”寄存器**：某些 ICP 版本有独立的 boot address 寄存器，写 0 只表示
   “从 0 取指”的前提是另一个使能位；现有 `hfi_reg.h` 里没有这一项。
3. **CPAS/CAMNOC 仲裁未投票**：AXI 通路虽有时钟但无带宽授权，取指被挂起。
   反证：被挂起的事务也应先经过 SMMU 并产生翻译（nomap 时会报错），而我们零错误。

## 6. 复现方法（全部可重复）

```bash
# 1) DT 需要三样东西（已写入 sm8250-oneplus-kebab.dts）
#    clocks + camnoc/cpas 四路、power-domains titan_top_gdsc、
#    iommus = <&apps_smmu 0x20e2 0x400>
# 2) 内核侧模块
make -C ~/oneplus8t-venus-20261105/linux O=~/oneplus8t-venus-20261105/out \
     M=~/titan-a5-icp ARCH=arm64 LLVM=1 modules
# 3) 固件放到 /lib/firmware/CAMERA_ICP.elf（SHA cb98cec2…）
# 4) RAM 启动后
sudo insmod titan_a5_icp.ko arm=0 keep=1   # 只装载+映射，验证
sudo rmmod titan_a5_icp
sudo insmod titan_a5_icp.ko arm=1 keep=1   # 真正释放核心
dmesg | grep titan-a5-icp
```

模块参数：`arm`（是否写 A5）、`nomap`（诊断：不映射 IOVA 0）、`keep`（保持状态）。

## 7. 下一步（按性价比）

1. **用 nomap 实验的变体定位事务**：把域设成 stage-1 但只映射 1 页，看是否出现对 0x0 的翻译
   请求——若仍为零，彻底排除“取指被拦截”。
2. **对比 Android 启动同一地址的值**：用一次 Android 侧只读（若可）取 `A5_CONTROL/A5_STATUS`
   在固件运行时的值，反推“运行态”应该是什么样。
3. **找 TZ 线索**：在 ABL/TZ 镜像里搜 ICP 相关字符串（`icp`/`a5`/`camera`），确认是否有
   开机配置动作。
4. **CPAS 寄存器**：从原厂 `cam_cpas` 驱动取寄存器定义，先只做“读回默认值”，确认 CAMNOC
   是否有未投票的通道。

## 8. 安全说明

全过程 T1（`fastboot boot`，零分区写入）；每次失败后 `rmmod` 即恢复（GDSC off、寄存器回 0），
最终已回 boot_a 正常系统（`a9211d85…`，0 failed）。唯一持久改动是 DT/模块/固件文件，
都在 RAM 启动里生效，未落盘到任何分区。

---

## 9. 补充实验（23:20–23:45）：为什么判定「释放权在安全世界」

### 9.1 寄存器可写位掩码（写 0xFFFFFFFF 后读回）

| 寄存器 | 写全 1 后读回 | 解释 |
| --- | --- | --- |
| `A5_CONTROL (0x08)` | **`0x0058767B`** | 只有 bit0/1/3/4/5/6/9/10/12/14/21/22 可写；**bit13 不可写**（我之前看到 0x3000 里的 bit13 是只读状态位） |
| `NSEC_RESET (0x04)` | **`0x00000000`** | 始终读 0：只写寄存器（或写被丢弃） |
| `ETM (0x0c)` | `0x00000737` | 部分可写 |
| `A5_STATUS (0x200)` | 写 0 后仍 `0x200` | 只读 |
| `a5_csr +0x00` | 写全 1 后仍 `0x00010705` | **完全只读** |
| `a5_csr +0x04` | 写全 1 后仍 `0x10000000` | **完全只读** |

### 9.2 时钟假设已被排除

设 400 MHz 后：`cam_cc_pll0` enable_count=2、`cam_cc_pll0_out_odd`=400 MHz、
`cam_cc_icp_clk_src`=400 MHz、`cam_cc_icp_clk`=400 MHz（Y），`clk_get_rate` 回读一致。
⇒ 核心时钟真实在跑，不是「PLL 没锁」。

### 9.3 `NSEC_RESET` 取值扫描（10 个值）

对 `0x1/0x2/0x4/0x8/0x10/0x20/0x40/0x100/0x7f/0xffffffff` 逐个尝试
「A5_EN → NSEC_RESET=v → 0x1210 → INIT_REQUEST=1」，每次等待后读状态：
**`A5_STATUS` 恒 0x200、`INIT_RESPONSE` 恒 0、`FW_VERSION` 恒 0、SMMU fault 恒 0。**

### 9.4 结论

所有 NS 侧可及的控制手段均已穷尽：
1. 电源（`titan_top_gdsc`）与时钟（400 MHz、PLL 已锁）都在；
2. 寄存器可读、`A5_CONTROL` 可写；
3. `NSEC_RESET` 只写、任何值都不释放核心；
4. 控制窗口 `a5_csr` 对 NS **完全只读**；
5. 核心从未产生任何 SMMU 事务（nomap 诊断 0 fault）。

⇒ **A5 核的复位释放由安全世界（TZ/XBL）掌握，Non-Secure Linux 无法完成。**
这与 OnePlus 的 Android 启动链有关：XBL/ABL → TZ 应用在开机时完成 ICP 的 NS 交接，
而我们只跑 ABL → Linux，没有这一步。

**这解释了为什么「寄存器全通、核心不动」，也标定了 P4 的真实边界：**
想在主线跑起 ICP，前提是找到并复现 TZ 那一步（或让 TZ 在开机时无条件释放 ICP）。
这不是内核驱动能补的，属于 bootloader/安全固件层面。

### 9.5 直接证据：TZ 里有 secure camera 服务

只读扫描设备分区（T0，未写入）：

| 分区 | 大小 | 格式 | camera/icp 字符串数 |
| --- | --- | --- | --- |
| `tz_a` | 4 MiB | ELF64 | **39** |
| `abl_a` | 8 MiB | ELF32 | 0 |
| `xbl_a` | 5 MiB | — | 0 |

`tz_a` 中的关键符号：

```
qsee_sec_camera_acquire_camera      qsee_sec_camera_register_read
qsee_sec_camera_get_session         qsee_sec_camera_register_write
qsee_sec_camera_register_bulk_write qsee_sec_camera_set_haven_license
qsee_sec_camera_register_callback   qsee_sec_camera_release_camera
ISPCOM_is_sp_subsystem_link_up      ISPCOM_client_is_server_connected
secure_camera                       after ISPIConfig, before ISPI_writeFd
```

⇒ **相机/ISP 在 TrustZone 里有一个“安全相机”服务（QSEE + ISPCOM 协议，带 Haven 许可）**，
A5/ICP 的所有权与寄存器访问权由它掌握。这正好解释了：

- `A5_CONTROL` 可写而 `NSEC_RESET` 写不动、`a5_csr` 完全只读；
- 任何 NS 序列都无法让核心取指；
- 原厂 Android 之所以能启动 ICP，是因为用户态通过 `qseecom` 调到了这个 TZ 服务。

我们的 Debian 启动链里 **没有 qseecom 设备、没有 TEE 驱动**（实测 `/dev/qseecom` 与
`/dev/tee*` 均不存在，platform driver 数为 0），因此**主线侧无法触达该服务**。

**P4 的真实边界由此确定：需要在 Linux 侧具备 QSEE/TEE 通道（或由 bootloader 无条件
释放 ICP），否则 A5 永远不会启动。** 这不是相机驱动能解决的。
