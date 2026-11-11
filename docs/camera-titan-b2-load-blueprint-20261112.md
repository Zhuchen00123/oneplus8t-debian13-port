# Titan ICP 装载蓝图（B2 / P0）— 2026-11-12 夜

> 任务：`docs/camera-titan-night-run-20261112.md` §5 G2 的五个问题。
> 纪律：本条**纯离线**，零设备写入。所有结论带「文件 + 偏移 + 原文」。
> 证据链：原厂 OTA `boot.img` 里的**真·原厂 kernel Image** → 用 kallsyms 恢复符号 → 反汇编；
> 再与 Qualcomm techpack 源码（CodeLinaro `camera-kernel`，分支 `LA.UM.9.15.2.c25`）逐条对上。
> 源码与二进制**互相印证**（见 §2），不是单边推断。

---

## 0. 五问五答（先看这个）

| # | 问题 | 结论（一句话） |
| --- | --- | --- |
| 1 | 固件怎么进内存 | `request_firmware("CAMERA_ICP.elf")` → 校验 ELF32/EM_ARM → 按 **ELF program header 逐段 `memcpy_toio`** 到 `fw_kva + p_vaddr`；`fw_kva` 是 `dma_alloc_coherent` 的 CPU 地址，物理页由 `iommu_map` 映射到 **IOVA 0**。**不是**直写 0x8dc00000 carveout。 |
| 2 | A5 怎么放复位 | **不走 SCM/PAS，也没有 firmware base 寄存器**。复位/使能在 SIERRA 窗口：`A5_CONTROL(0x08)` bit9(A5_EN)/bit4(WAKE_UP)/bit12(CLKGATE)，`NSEC_RESET(0x04)` 写 **0x10**。电源来自 camcc 的 `titan_top_gdsc`。 |
| 3 | 三窗口职责 | `a5_qgic`(0xac00000,24K)=A5 的 GIC；**`a5_sierra`(0xac10000,32K)=HFI CSR 全部寄存器（唯一被驱动读写的窗口）**；`a5_csr`(0xac18000,12K)=控制块，**原厂驱动零引用**。 |
| 4 | SMMU 怎么配 | apps_smmu 的 14 个 SID（0x20e2…0x2421）由 DT `iommus` 声明；ICP 域由 arm-smmu 驱动建立，`cam_smmu_api` 做 `iommu_map`。IOVA 区：firmware 0x0+0x500000、shared 0x7400000+0x9600000、io +0xee300000（discard 0xdff00000+0x300000）、secondary-heap 0x10a00000+0x100000。CPAS 0xac40000 由 `cam_cpas` 驱动编程。 |
| 5 | 顺序 | CPAS vote → SOC 资源(时钟+GDSC) → 设 IRQ 回调 → **SET_FW_BUF** → **enable_cpu(A5_CONTROL=0x200, NSEC_RESET=0x10)** → **FW_DOWNLOAD(逐段 memcpy_toio)** → **cam_hfi_init（写 QTBL/SFR/SHMEM/HEAP/INIT_REQUEST 等，轮询 INIT_RESPONSE）** → 开中断 → HFI SYS_INIT。 |

**与任务书假设的两处偏差（重要，必须知道）**：

1. **固件缓冲不是 0x8dc00000 carveout。** 原厂驱动用 `dma_alloc_coherent(0x500000)` + `iommu_map(IOVA 0)`；
   `pil_camera_region@8dc00000` 是 `removed-dma-pool`/`no-map`，**驱动源码里没有任何引用**（见 §3.3）。
   同一段 5 MiB 只是 **IOVA 区大小与之巧合相同**，不要混为一谈。
2. **队列表指针不是「放复位之前」写的。** 原厂顺序是 `enable_cpu`（写 A5_EN + NSEC_RESET=0x10）**先于**下载与 QTBL。
   QTBL/SFR/INIT_REQUEST 全部在 `cam_hfi_init` 里、在 A5 已使能之后写。见 §7。

---

## 1. 证据来源（可复现）

| 证据 | 路径 / 命令 | 说明 |
| --- | --- | --- |
| 原厂 kernel Image | `downloads/stock-kb2000-14.0.0.602/images/boot.img` → `unpack_bootimg` → `kernel`（47,511,568 B） | 未压缩 ARM64 Image，`os version 12.0.0 / patch 2024-08` |
| 符号恢复 | `vmlinux-to-elf kernel vmlinux-stock.elf` | 找到 kallsyms：**139,345 符号**、base `0xffffff8008080000` |
| 反汇编 | `llvm-objdump -d --no-show-raw-insn vmlinux-stock.elf` | 关键函数见下 |
| 驱动源码 | CodeLinaro `clo/la/platform/vendor/opensource/camera-kernel` 分支 `LA.UM.9.15.2.c25` | `drivers/cam_icp/...`、`drivers/cam_smmu/...` |
| 原厂 DTB | `local/sensors-20261107/stock-dtb-split/stock-000.dtb` | cam-a5 / cam_smmu / reserved-memory |
| 固件 | `local/titan-b2/night/vendor/CAMERA_ICP.elf`（从 vendor 镜像解出，SHA `cb98cec2…`） | 与 B0/B1 同一文件 |

源码文件落盘：`local/titan-b2/night/src/`（a5_core.c、a5_dev.c、a5_soc.c、hfi.c、hfi_reg.h、cam_icp_hw_mgr.c、cam_smmu_api.c）。
二进制产物：`local/titan-b2/night/stock-boot/{kernel,dtb,vmlinux-stock.elf}`。

**二进制↔源码对齐校验**（防止源码版本与出厂二进制不一致）：

| 源码行为 | 二进制证据（原厂 kernel Image） |
| --- | --- |
| `request_firmware("CAMERA_ICP.elf")` | `cam_a5_process_cmd` VA `0xffffff80094a68e0`：`bl request_firmware`；字符串 `CAMERA_ICP.elf` 在 Image 偏移 **0x22DFC88** |
| `memcmp(e_ident, ELFMAG, SELFMAG)` | VA `0xffffff80094a6908`：`memcmp(..., 4)`，常量 `\x7fELF` 在 VA `0xffffff800a062350` |
| `e_machine != EM_ARM` | VA `0xffffff80094a6910`：`ldrh w8,[x20,#0x12]; cmp w8,#0x28`（EM_ARM=40） |
| `e_ident[EI_CLASS] != ELFCLASS32` | VA `0xffffff80094a691c`：`ldrb w8,[x20,#4]; cmp w8,#1` |
| 逐段 `memcpy_toio(fw_kva+p_vaddr, elf+p_offset, p_filesz)` | VA `0xffffff80094a6d08`：`bl __memcpy_toio`（源 `elf+p_offset`、目标 `fw_kva+p_vaddr`、长度 `p_filesz`） |
| `cam_smmu_alloc_firmware` | `cam_icp_mgr_hw_open` VA `0xffffff80094a342c`：`bl cam_smmu_alloc_firmware` |

---

## 2. 驱动程序全景（谁调谁）

```
cam_icp_mgr_hw_open()                     cam_icp_hw_mgr.c:3738
  ├─ cam_icp_mgr_init_devs()               → a5/bps/ipe 的 hw_ops.init
  │    └─ cam_a5_init_hw()                 a5_core.c:285
  │         ├─ cam_cpas_start(cpas_handle, ahb_vote, axi_vote)   a5_core.c:329
  │         ├─ cam_a5_enable_soc_resources()                     a5_core.c:337（时钟/GDSC/regulator）
  │         └─ 可选 qos：cam_io_w_mb(qos, SIERRA+0x3C)            a5_core.c:347-349
  ├─ cam_icp_allocate_fw_mem()             cam_icp_hw_mgr.c:2754
  │    └─ cam_smmu_alloc_firmware()        cam_smmu_api.c:1269
  ├─ cam_icp_mgr_fw_download()             cam_icp_hw_mgr.c:3513
  │    ├─ SET_IRQ_CB
  │    ├─ SET_FW_BUF(kva, iova, len)       → core_info->fw_kva_addr/fw_buf/fw_buf_len
  │    ├─ cam_hfi_enable_cpu(SIERRA)       hfi.c:578
  │    └─ FW_DOWNLOAD                      a5_core.c:172 → request_firmware + memcpy_toio
  └─ cam_icp_mgr_hfi_init()                cam_icp_hw_mgr.c:3564
       └─ cam_hfi_init()                   hfi.c:684 → QTBL + 寄存器 + INIT_REQUEST
```

---

## 3. Q1：固件怎么进内存

### 3.1 分配（物理页 + IOVA 0）

`drivers/cam_smmu/cam_smmu_api.c:1269`：

```c
firmware_len   = iommu_cb_set.cb_info[idx].firmware_info.iova_len;    // DT: 0x500000
firmware_start = iommu_cb_set.cb_info[idx].firmware_info.iova_start;  // DT: 0x0
icp_fw.fw_kva = dma_alloc_coherent(icp_fw.fw_dev, firmware_len,
                                   &icp_fw.fw_dma_hdl, GFP_KERNEL);
domain = iommu_cb_set.cb_info[idx].domain;
rc = iommu_map(domain, firmware_start, icp_fw.fw_dma_hdl, firmware_len, ...);
*cpuva = (uintptr_t)icp_fw.fw_kva;   // 返回给 a5 驱动
```

- `icp_fw.fw_dev = &pdev->dev`（cam_smmu 平台设备，`cam_smmu_api.c:3983`），**没有** `of_reserved_mem_device_init`。
- 所以物理页是常规 DMA 连续内存，**IOVA 0 是 SMMU 映射出来的**，不是 0x8dc00000。

### 3.2 下载（逐段）

`drivers/cam_icp/icp_hw/a5_hw/a5_core.c:172-236`：

```c
rc = request_firmware(&core_info->fw_elf, "CAMERA_ICP.elf", &pdev->dev);   // :195
rc = cam_icp_validate_fw(fw_start);   // :208  ELFMAG / EM_ARM / ELFCLASS32
rc = cam_icp_get_fw_size(fw_start, &fw_size);   // :214
if (core_info->fw_buf_len < fw_size) { ... }     // :220  IOVA 区必须装得下
rc = cam_icp_program_fw(fw_start, core_info);    // :227
```

`cam_icp_program_fw`（:132-170）：

```c
for (i = 0; i < num_prg_hdrs; i++, prg_hdr++) {
    if (prg_hdr->p_flags == 0) continue;                 // 只搬 p_flags != 0 的段
    if (prg_hdr->p_filesz != 0) {
        src  = (u8 *)((u8 *)elf + prg_hdr->p_offset);
        dest = (u8 *)(((u8 *)core_info->fw_kva_addr) + prg_hdr->p_vaddr);
        memcpy_toio(dest, src, prg_hdr->p_filesz);       // :165
    }
}
```

`cam_icp_get_fw_size`（:81-130）算的是 **最大 end 地址**：

```c
seg_mem_size = (p_memsz + p_align - 1) & ~(p_align - 1);   // 按 p_align 向上取整
seg_mem_size += p_vaddr;
if (*fw_size < seg_mem_size) *fw_size = seg_mem_size;
```

→ 该值必须 ≤ `fw_buf_len`（= IOVA 区 0x500000）。**这就是「5 MiB 够不够」的判定依据。**

### 3.3 关于 0x8dc00000 carveout（修正任务书）

原厂 DT：

    /reserved-memory/pil_camera_region@8dc00000
        compatible  "removed-dma-pool"
        no-map      (empty)
        reg         0x0 0x8dc00000 0x0 0x500000

- `removed-dma-pool` = **DMA API 不可用**（不是 CMA、不是 `shared-dma-pool`）。
- 在下载下来的全套相机驱动源码里 grep `8dc00000` / `pil_camera` / `memory-region`：**0 命中**。
- 结论：**ICP 固件缓冲与该 carveout 无关**；同尺寸只是巧合。
  （该区域真实用途未证；候选：给固件自身/其它 remote processor，明确写「未证」。）

### 3.4 固件本身的段布局

`CAMERA_ICP.elf`（3,922,232 B，SHA `cb98cec2…`）为 **ELF32 / EM_ARM(40)**，正是 A5（Cortex-A5，ARMv7）的代码。
装载时只有 `p_flags != 0` 的段会被搬，目标地址 = `p_vaddr`（相对 IOVA 0）。

---

## 4. Q2：A5 怎么放复位 / 怎么使能

### 4.1 没有 SCM、没有 PAS

- 全套相机驱动源码里 `qcom_scm` / `scm_pas` / `subsystem_get`：**0 命中**。
- 没有 firmware base 寄存器：A5 取指地址由**固件段 p_vaddr（0x0 / 0x100000）+ SMMU IOVA 0** 决定。

### 4.2 复位/使能寄存器（`drivers/cam_icp/fw_inc/hfi_reg.h:14-25`）

| 偏移 | 名字 | 作用 |
| --- | --- | --- |
| 0x00 | `A5_HW_VERSION` | 读硬件版本 |
| **0x04** | `A5_CSR_NSEC_RESET` | 非安全复位，**写 0x10** |
| **0x08** | `A5_CSR_A5_CONTROL` | bit9=A5_EN，bit4=WAKE_UP_EN，bit12=EN_CLKGATE_WFI |
| 0x10 | `A2HOSTINTEN` | A5→Host 中断使能 |
| 0x14/0x18/0x1c/0x20 | A2HOSTINT / CLR / STATUS / SET | 中断状态 |
| 0x30 | `HOST2ICPINT` | Host→A5 中断（发命令用） |
| 0x44 | `FW_VERSION` | 固件版本（INIT 后有效） |
| 0x48 | `HOST_ICP_INIT_REQUEST` | 写 SET 触发初始化 |
| 0x4c | `ICP_HOST_INIT_RESPONSE` | 轮询 SUCCESS |
| 0x50/0x54 | SHARED_MEM_PTR/SIZE | |
| 0x58 | **QTBL_PTR** | 队列表 IOVA |
| 0x5c/0x60 | UNCACHED_HEAP_PTR/SIZE | |
| 0x68 | SFR_PTR | |
| 0x6c/0x70 | QDSS_IOVA/SIZE | |
| 0x74/0x78 | IO_REGION_IOVA/SIZE | |
| 0x7c/0x80 | IO2_REGION_IOVA/SIZE | |
| 0x200 | `A5_STATUS` | bit7=WFI |

### 4.3 使能 / 关闭（`hfi.c:578-583`、`544-576`）

```c
void cam_hfi_enable_cpu(void __iomem *icp_base)
{
    cam_io_w_mb((uint32_t)ICP_FLAG_CSR_A5_EN, icp_base + HFI_REG_A5_CSR_A5_CONTROL); // +0x08 = 0x200
    cam_io_w_mb((uint32_t)0x10,               icp_base + HFI_REG_A5_CSR_NSEC_RESET);  // +0x04 = 0x10
}
```

`cam_hfi_disable_cpu`：轮询 `A5_STATUS.WFI` → 清 EN/WAKE_UP → 直写回 NSEC_RESET →
`INIT_REQUEST=RESET` → `A2HOSTINTEN=INTR_DISABLE`。

**电源另有两条腿**（`a5_core.c:329-349`）：`cam_cpas_start()`（camnoc/NoC 投票）与
`cam_a5_enable_soc_resources()`（4 路 camcc 时钟 + `titan_top_gdsc` + `camss-vdd`）。

### 4.4 二进制证据

- `cam_a5_init_hw` VA `0xffffff80094a6370`：`bl cam_cpas_start`（+0x84）、`bl cam_a5_enable_soc_resources`（+0x94）、
  随后 `ldr w0,[x23,#0xc]` 判 QoS、`cam_io_w_mb(..., base+0x3c)`（+0xc4）——与源码 :344-349 完全对应。
- `cam_a5_irq` 用 `SIERRA + a5_host_int_status(0x1c)` 读、`+0x18` 清——对应源码 :408-413。

---

## 5. Q3：三窗口职责

| 窗口 | 物理 | 大小 | 原厂角色 | 驱动是否使用 |
| --- | --- | --- | --- | --- |
| `a5_qgic` | 0x0ac00000 | 0x6000 | A5 的 GIC-400（A5 域中断控制器） | **未用**（全文只出现 QGIC 宏定义） |
| **`a5_sierra`** | **0x0ac10000** | **0x8000** | **A5 外设/AHB 窗口 = HFI CSR 全部寄存器** | **唯一读写窗口**（`a5_core.c:348/408/412`、`cam_icp_hw_mgr.c:3039/3045/3141/3443/3548/3559/3634/3808/3811`） |
| `a5_csr` | 0x0ac18000 | 0x3000 | A5 控制块 | **未用**（`A5_CSR_BASE` 仅宏定义） |

证据（源码 grep）：

    $ grep -rn 'A5_SIERRA_BASE|A5_QGIC_BASE|A5_CSR_BASE' *.c
    a5_core.c:348/408/412 ... A5_SIERRA_BASE ...
    cam_icp_hw_mgr.c:3039/...  A5_SIERRA_BASE ...
    a5_dev.c（a5_hw_info 表）: nsec_reset=0x4, a5_control=0x8, qtbl_ptr=0x58, a5_status=0x200

**A5 侧 ↔ AP 侧对照**：A5 把 `0xe0010000` 当块基址，AP 侧 = `0x0ac10000 + (A5-0xe0010000)`，
所以 B0/§10.2 的 `0xe0010058 → 0xac10058` 是对的；而 `0xac180xx`（csr）**没有**出现在固件的访问集合里。

---

## 6. Q4：SMMU 怎么配

### 6.1 SID（原厂 DTB `/soc/qcom,cam_smmu/msm_cam_smmu_icp`）

    compatible   "qcom,msm-cam-smmu-cb"
    iommus       0x47 0x20e2 0x400   0x47 0x24e2 0x400
                 0x47 0x2000 0x400   0x47 0x2001 0x400
                 0x47 0x2400 0x400   0x47 0x2401 0x400
                 0x47 0x2060 0x400   0x47 0x2061 0x400
                 0x47 0x2460 0x400   0x47 0x2461 0x400
                 0x47 0x2020 0x400   0x47 0x2021 0x400
                 0x47 0x2420 0x400   0x47 0x2421 0x400
    iova-region-discard    0xdff00000 0x300000
    qcom,iommu-dma-addr-pool 0x10c00000 0xee300000

→ **14 个 SID**：0x20e2/0x24e2、0x2000/0x2001、0x2400/0x2401、0x2060/0x2061、0x2460/0x2461、
0x2020/0x2021、0x2420/0x2421（每对含 secure/non-secure 变体）。phandle `0x47` = apps_smmu，掩码 0x400。

### 6.2 IOVA 分区（`/soc/qcom,cam_smmu/msm_cam_smmu_icp/iova-mem-map/*`）

| 节点 | id | start | len |
| --- | --- | --- | --- |
| `iova-mem-region-firmware` | 0 | **0x0** | **0x500000** |
| `iova-mem-region-shared` | 1 | 0x7400000 | 0x9600000 |
| `iova-mem-region-io` | 3 | (见 DT) | 0xee300000，discard 0xdff00000+0x300000 |
| `iova-mem-region-secondary-heap` | 4 | 0x10a00000 | 0x100000 |
| `iova-mem-qdss-region` | — | — | — |

### 6.3 谁编程

| 部分 | 由谁 |
| --- | --- |
| SMMU 页表/域 | **apps_smmu 的 arm-smmu 驱动**（DT `iommus` + `qcom,iommu-dma-addr-pool`）；cam_smmu_api 只是调用者 |
| ICP 段映射 | `cam_smmu_map_kernel_iova / iommu_map`（cam_smmu_api.c:1326/1472/1751/2362） |
| 固件段 | `cam_smmu_alloc_firmware` → `dma_alloc_coherent` + `iommu_map(IOVA 0)` |
| CPAS / camnoc（0xac40000） | **`cam_cpas` 驱动**：`cam_cpas_start`（AHB + AXI 投票）、`cam_cpas_update_axi_vote`；ICP 通过 `cam_cpas_register_client("icp")` 注册（`a5_dev.c:80-100`） |
| A5 自己的 CPAS 视图（0xe0040000） | **固件自用**（B0 §10.2 已提示不能外推） |

---

## 7. Q5：上电顺序（源码原文，按执行次序）

`cam_icp_mgr_hw_open()`（`cam_icp_hw_mgr.c:3738` 起）实际顺序：

| # | 动作 | 证据 |
| --- | --- | --- |
| 1 | `cam_cpas_start`（AXI vote 4 项 + AHB lowsvs） | a5_core.c:311-334 |
| 2 | `cam_a5_enable_soc_resources`（4 时钟 + GDSC + regulator） | a5_core.c:337 |
| 3 | （可选）QoS 写 `SIERRA+0x3C` | a5_core.c:345-349 |
| 4 | 分配固件缓冲 `dma_alloc_coherent` + `iommu_map(0)` | cam_icp_hw_mgr.c:2754 |
| 5 | `SET_IRQ_CB`（A5 IRQ → `cam_icp_hw_mgr_cb`） | cam_icp_hw_mgr.c:3528-3533 |
| 6 | `SET_FW_BUF(kva,iova,len)` | cam_icp_hw_mgr.c:3537-3546 |
| 7 | **`cam_hfi_enable_cpu`**：`A5_CONTROL=0x200`；`NSEC_RESET=0x10` | cam_icp_hw_mgr.c:3548 / hfi.c:578 |
| 8 | **`FW_DOWNLOAD`**：request_firmware → 校验 → 逐段 `memcpy_toio` | cam_icp_hw_mgr.c:3550 / a5_core.c:172 |
| 9 | **`cam_hfi_init`**：`A5_CONTROL=0x2010`(EN|WAKE_UP|CLKGATE) | hfi.c:713-733 |
| 10 | 填 QTBL：q_tbl_hdr + 3 个 q_hdr（cmd 4096B、msg 4096B、dbg 102400B，索引以 4B 字计） | hfi.c:735-772 |
| 11 | 写 `QTBL_PTR(0x58)`、`SFR_PTR(0x68)`、`SHARED_MEM_PTR/SIZE(0x50/0x54)`、`UNCACHED_HEAP_PTR/SIZE(0x5c/0x60)`、`INIT_REQUEST=SET(0x48)`、`QDSS_IOVA/SIZE(0x6c/0x70)`、`IO_REGION(0x74/0x78)`、`IO2_REGION(0x7c/0x80)` | hfi.c:849-874 |
| 12 | 轮询 `INIT_RESPONSE(0x4c)` 直到 SUCCESS | hfi.c:882-900 |
| 13 | 读 `FW_VERSION(0x44)`；`A2HOSTINTEN=INTR_ENABLE|WD0` | hfi.c:913-922 |
| 14 | 上层再发 `HFI_CMD_SYS_INIT` 等系统命令 | a5_core.c:497 / hfi.c:652 |

**关键点**：
- **QTBL 指针在 A5 使能之后写**（不是之前）。
- `INIT_REQUEST` 是「开始握手」的闸门，必须在 QTBL/SFR/Heap 都写完之后。
- 固件下载在 `enable_cpu` 之后、`hfi_init` 之前——A5 在此窗口内应停在 boot ROM/等待 INIT_REQUEST。
- 失败回滚：`cam_hfi_disable_cpu`（清 EN、INIT_REQUEST=RESET、关中断）。

---

## 8. 给 G4/G5 的直接结论（照这个做）

1. **只碰 SIERRA 窗口（0xac10000）**即可完成上电与握手；`qgic`/`csr` 不动。
   这正好符合「Stage A 绝不写 0xac1xxxx」的纪律——**读**只读 0x00/0x04/0x08/0x44/0x48/0x4c/0x200。
2. **电源/时钟必须走框架**：camcc 4 路时钟（`FAST_AHB_CLK_SRC=33/0x21`、`ICP_AHB=38/0x26`、`ICP_CLK_SRC=40/0x28`、`ICP_CLK=39/0x27`）
   + `titan_top_gdsc`（`TITAN_TOP_GDSC=5`，camcc `#power-domain-cells=1`）。
   原厂 `clock-rates` 实测给出每档：`soc_fast_ahb` = 100/200/300/400/400 MHz，
   `icp_clk_src` = 400/480/600/600/600 MHz（见 DT 节点 `clock-rates`，10 个数一对）。
3. `camera_mem@8dc00000`（no-map 5 MiB）**已经在** `sm8250-oneplus-kebab.dts` 的 `reserved-memory` 里，
   G4 不需要再加；它与固件缓冲无关，别拿它当装载目标。
4. 读值判据（GDSC off/on 对照）：
   - on 且正确映射：`0x200(A5_STATUS)` 有 WFI 位语义、`0x00(HW_VERSION)` 非 0/非 0xff；
   - off 或错映射：全 0 / 全 0xff；读 `0x4c` 不应有副作用（只读状态）。
5. **不要**在没有时钟/GDSC 的情况下读——NoC 挂死风险（任务书 §6 已划红线）。

---

## 9. 不确定项（明确未证）

1. 源码取的是 CodeLinaro `LA.UM.9.15.2.c25`，与 OnePlus 出厂二进制的**行为已逐条对齐**（§1 表），
   但不能宣称逐字节相同（OnePlus 可能带私有补丁）。
2. `pil_camera_region@8dc00000` 的真实用途**未证**；只证了「ICP 固件装载不引用它」。
3. `a5_csr`(0xac18000) 的功能**未证**（驱动零引用，需 G5 dump 或固件侧证据）。
4. A5 在 `enable_cpu` 到 `INIT_REQUEST` 之间的实际执行状态**未证**（源码只体现顺序，未体现硬件停在哪）。
5. `qos-val` 在原厂 a5 节点里**没有**该属性 → 实际 `a5_qos_val=0`，`SIERRA+0x3C` 不会被写。
6. `cam_hfi_init` 的 `event_driven_mode=0`（INTR_MODE），若改 POLL 模式，QTBL 里 qhdr_type 位不同。

---

## 10. 2026-11-12 夜更新（推翻 §9 的第 3、4 条）

**A5 寄存器读全 0 的原因已查明：不是「块不响应」，而是 CAMNOC 互连时钟没开。**

- 原厂 `cam_a5_init_hw()` 在使能时钟/GDSC 之后会调用 `cam_cpas_start()`，CPAS 负责 CAMNOC 的 AXI 投票；
  我们最初的探针只开了 4 路 ICP 时钟 + `titan_top_gdsc`。
- DT 节点补上 `CAM_CC_CAMNOC_AXI_CLK_SRC(6)` / `CAM_CC_CAMNOC_AXI_CLK(5)` / `CAM_CC_CPAS_AHB_CLK(13)` /
  `CAM_CC_SLOW_AHB_CLK_SRC(109)` 四路时钟后，读数如下：

| 窗口 | 读数 | 含义 |
| --- | --- | --- |
| `0xac10000` | `0x10010000` | A5_HW_VERSION，块已上电 |
| `0xac10008` | `0x00003000` | A5_CONTROL 默认值（bit12/13） |
| `0xac10200` | `0x00000200` | A5_STATUS |
| `0xac00004` | `0x00000402` | GICD_TYPER（96 条中断线） |
| `0xac00008` | `0x00001070` | GICD_IIDR |
| `0xac18000` | `0x00010705` | csr 窗口，功能未证 |
| `0xac18004` | `0x10000000` | csr 窗口，功能未证 |

- `rmmod` 关闭时钟后同一地址回到 `0x00000000`，因果闭环。
- 因此 §5 的表格需要更正：**`a5_qgic` 是 A5 的 GIC-400 并且可访问**（不再是「驱动未用=无用」）；
  `a5_csr` 也真实存在并可读，只是用途仍未证。
- 完整证据与复现命令见 `docs/camera-titan-a5-probe-20261112.md`。
