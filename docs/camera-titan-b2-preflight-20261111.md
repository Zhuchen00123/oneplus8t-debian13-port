# Titan ISP PoC B2 预研：AP 侧窗口、时钟与设备基线（2026-11-11）

> 状态：**B2b（寄存器映射）离线解出**；B2a（加载固件）/B2c（HFI 握手）**尚未上机**。
> 本轮对手机只做了**只读**侦察，没有任何写操作、没有改内核、没有刷任何分区。
> 分支 `codex/camera-titan-poc-20261111`；前置阶段 [B1](camera-titan-hfi-b1-20261111.md) / [B0](camera-titan-hfi-b0-20261111.md)

## 0. 结论先行

B0 §9 把 B2 的**唯一真阻塞**列为「AP 侧寄存器映射」。这一条现在**离线解决了**，来源不是猜，是我们自己手机的原厂 DTB：

1. **三窗口定死**：`/soc/qcom,a5@ac00000`（compatible `qcom,cam-a5`）的
   `reg` 是 **0x0ac00000+0x6000 / 0x0ac10000+0x8000 / 0x0ac18000+0x3000**，
   `reg-names` = `a5_qgic / a5_sierra / a5_csr`，`reg-cam-base` = **0x0 / 0x10000 / 0x18000**。
2. **A5 视图与 AP 窗口严格对齐**：固件里所有 `0xe0010xxx` 寄存器，
   正是 A5 把 `0xe0010000` 当成块基址 + 偏移；AP 侧 = **0x0ac10000 + 偏移**。
   三个窗口的相对偏移（0x0 / 0x10000 / 0x18000）与三段 size 完全自洽。
3. **时钟/电源/中断全部可从主线现有代码满足**：原厂 DT 的四个时钟
   `<&camcc 0x21/0x26/0x28/0x27>` 就是主线的
   `CAM_CC_FAST_AHB_CLK_SRC(33) / ICP_AHB(38) / ICP_CLK_SRC(40) / ICP_CLK(39)`；
   供电 `camss-vdd-supply` 指向 **`titan_top_gdsc`**（`ad0c144`）；
   中断是 **GIC SPI 463 上升沿**。主线的 `camcc-sm8250.c` 已经把这三路时钟、
   `titan_top_gdsc` 和 `CAM_CC_ICP_BCR`（`0xc070`）都注册了。
4. **设备侧基线**：ICP 现在**完全没上电**——`titan_top_gdsc` 为 `off-0`，
   三路 `cam_cc_icp_*` 频率全 0、无人认领 `0x0ac00000`。
   这正是"能安全开始"的状态，但也意味着**必须先上电才能读寄存器**。

**所以 B2 的下一步不是"读一下就知道了"，而是"先把它按框架正常上电"。**
这一步需要设备树节点 + 一个只读探针模块，也就是 B2a 的前置动作，见 §6。

## 1. 原厂 cam-a5 节点（证据原文）

`./tools/titan-icp/fdt-dump.py stock-000.dtb --path /soc/qcom,a5@ac00000` 的输出：

| 属性 | 值 | 用途 |
| --- | --- | --- |
| compatible | `qcom,cam-a5` | 下游 `cam_a5` 驱动匹配名 |
| reg | `0xac00000 0x6000` / `0xac10000 0x8000` / `0xac18000 0x3000` | 三段 MMIO |
| reg-names | `a5_qgic` / `a5_sierra` / `a5_csr` | A5 内部 QGIC / 外设 / 控制 |
| reg-cam-base | `0x0 0x10000 0x18000` | **相对 cam base 的偏移，关键** |
| interrupts | `<0 0x1cf 1>` | GIC SPI 463，边沿上升 |
| clocks | `<&camcc 0x21> <&camcc 0x26> <&camcc 0x28> <&camcc 0x27>` | 见 §3 |
| clock-names | `soc_fast_ahb / icp_ahb_clk / icp_clk_src / icp_clk` | |
| camss-vdd-supply | `0x250` → `/soc/qcom,gdsc@ad0c144`（`titan_top_gdsc`） | 电源域 |
| fw_name | `CAMERA_ICP.elf` | 与我们 B0/B1 用的是同一个文件 |
| ubwc-ipe-fetch-cfg | `0x707b 0x7083` | 属性 2/9 要写的值 |
| ubwc-ipe-write-cfg | `0x161ef 0x1620f` | |
| ubwc-bps-fetch-cfg | `0x707b 0x7083` | |
| ubwc-bps-write-cfg | `0x161ef 0x1620f` | |

顺带：`/soc/qcom,cam-icp`（`qcom,cam-icp`）`compat-hw-name = qcom,a5 / qcom,ipe0 / qcom,bps`，
`num-a5/num-ipe/num-bps = 1`；`/soc/qcom,cam-cpas@ac40000` 是 CPAS（`cam_cpas_top` 0xac40000+0x1000、
`cam_camnoc` 0xac42000+0x8000）——它属于 camera NoC，不是 HFI 数据面。

## 2. A5 视图 ↔ AP 窗口的对齐推导

固件（A5 侧）访问的地址全部落在 `0xe0010008 … 0xe0010078`。
把 A5 的块基址取 `0xe0010000`，则 AP 侧 = `0x0ac10000 + (A5 - 0xe0010000)`。

支持这条映射的三条独立证据：

1. **偏移自洽**：原厂 `reg-cam-base` 是 0x0 / 0x10000 / 0x18000；A5 侧对应
   0xe0000000 / 0xe0010000 / 0xe0018000。两套偏移一一对应，不是巧合。
2. **容量自洽**：0x6000 / 0x8000 / 0x3000 与三窗口 size 完全相同；
   固件访问到的最远寄存器 0xe0010078 距 0xe0010000 仅 0x78，远在 0x8000 之内。
3. **语义自洽**：`0xe0010058` 读出的就是队列表指针（`HFI_QueueGetQHdr` 直接
   `ldr r6,[r0]` 当指针用），而 `a5_sierra`（0xac10000）正是使能了
   `icp_clk`（A5 外设侧）的那一块；`a5_csr`（0xac18000）留给 A5 自身的
   时钟/复位/启动控制，固件侧那一小段代码（`0xe0018xxx`）本轮**没有**命中。

> 注意：固件还有 `0xe0000080`、`0xe000b000`、`0xe0040000` 等访问，
> 那些落在窗口外或别的块（CDM/NoC），**不影响**本节的结论；
> 它们说明 A5 看到的地址空间比 AP 驱动映射的三段更大，不能反过来推翻窗口对齐。

## 3. 固件侧寄存器清单（12 个 `movt #0xe001` 站点，全部实读反汇编）

| A5 地址 | AP 地址 | 访问 | 所在函数 | 语义（按用法推断） |
| --- | --- | --- | --- | --- |
| 0xe0010008 | 0xac10008 | 读/写 | `ICPIdleTask` | 空闲/心跳控制 |
| 0xe0010010 | 0xac10010 | 基址 | `ICPDomainCmdHandler` | +0x60 = 0xe0010070 一组 |
| **0xe0010014** | **0xac10014** | **写 1** | `IcpDiagLogV/F`、`ICPHostInterface_postMessage`、`driverTask` | **抬主机中断**（B0 已见） |
| 0xe0010044 | 0xac10044 | 写 | `fwEntryFunction` 附近 | 启动握手请求 |
| **0xe0010048** | **0xac10048** | 轮询直到 ==1 | `fwEntryFunction` | **启动握手应答** |
| 0xe001004c | 0xac1004c | 写 1；另有 `+0x1c` 读 | 初始化路径 / `SendSfrMessage` | 第二个事件/门铃 |
| **0xe0010058** | **0xac10058** | 读 | `HFI_QueueGetQHdr` | **队列表指针**（B0 已见） |
| 0xe0010060 | 0xac10060 | 写位掩码 | `ICPDomainCmdHandler` | 接口清除（B0 已见 0x10/0x20/0x120） |
| 0xe0010068 | 0xac10068 | 读 | `SendSfrMessage` | SFR 相关 |
| 0xe0010070 | 0xac10070 | 写 | `ICPDomainCmdHandler` | 0xe0010010+0x60 组 |
| 0xe0010074/78 | 0xac10074/78 | 读 | `MemMapUmdCdmRegions` | CDM/UMD 区域 base/size |

**上游 `camss-icp-hfi.h` 的 `HFI_REG_*` 不能照搬**：它把 GPn 定义为
`CSR + 0x20 + 4n`，QTBL_PTR 在 `+0x38`（GP6）、FW_VERSION 在 `+0x24`（GP1）；
而固件把队列表指针放在 **+0x58**（按上游编号是 GP14，上游那里是 IO_REGION_1_SIZE）。
两者不是同一套编号。**以本文表格为准**，上游常量只作命名参考。

---

## 4. 主线内核已有的能力（不需要自己写时钟驱动）

| 需要的东西 | 主线现状 | 出处 |
| --- | --- | --- |
| `cam_cc_icp_ahb_clk` | 已注册（id 38） | `drivers/clk/qcom/camcc-sm8250.c` |
| `cam_cc_icp_clk` / `cam_cc_icp_clk_src` | 已注册（id 39 / 40，RCG 有 19.2 MHz 父） | 同上 |
| `CAM_CC_FAST_AHB_CLK_SRC` | 已注册（id 33） | 同上 |
| `titan_top_gdsc` | 已注册（`gem_pd`），当前 `off-0` | 同上 + `pm_genpd_summary` |
| `CAM_CC_ICP_BCR`（块复位） | 已注册（`0xc070`） | 同上 |
| cam_a5 / cam_icp 设备树节点 | **没有**（`sm8250.dtsi` 只有 camss/cci/csiphy/vfe） | 需新增 |
| ICP 驱动 | **没有**（`camss/` 下无 icp 文件） | 需新增 |

**时钟 ID 完全对上**：原厂 DT 写 `<&camcc 0x21> <&camcc 0x26> <&camcc 0x28> <&camcc 0x27>`，
主线 binding 是 `FAST_AHB_CLK_SRC=33(0x21) / ICP_AHB=38(0x26) / ICP_CLK=39(0x27) / ICP_CLK_SRC=40(0x28)`。
两组独立来源给出同一套编号，说明 CamCC 布局在主线与厂方一致，**不需要逆向时钟树**。

供电也直接对上：`camss-vdd-supply = <&titan_top_gdsc>`，也就是
「ICP 的电源不是外部稳压器，是 Camera 子系统顶层的 GDSC」。

## 5. 设备只读基线（2026-11-11 21:0x，未做任何写操作）

| 项 | 值 |
| --- | --- |
| 主机 / 内核 | `kebab-debian13` / `7.2.0-kebab-debian13-r4-venus+ #4`（2026-11-05 构建） |
| boot id | `ed3ae39d-1076-4eee-9c0b-a58a3412edc4` |
| `titan_top_gdsc` | **`off-0`**；子域 bps/ipe_0/sbi/ife_0/ife_1 全 off |
| `cam_cc_icp_ahb_clk / icp_clk / icp_clk_src` | 全 **0 Hz**，父 19.2 MHz，`deviceless` |
| `/proc/iomem` | **没有 0x0ac00000**（注意：`00ac0000` 是 geniqup，11 MB 那个，别混） |
| `/dev/mem` | 在；`CONFIG_DEVMEM=y`、`CONFIG_STRICT_DEVMEM=y`、`IO_STRICT_DEVMEM` 未开 |
| SCM PAS | `qcom_scm_pas_init_image / mem_setup / auth_and_reset / shutdown` 等 8 个符号在 |
| remoteproc | slpi / cdsp / adsp **三核 running**（说明 TZ 的 PAS 通路本身可用） |
| 固件文件 | `/lib/firmware` 下**没有** `CAMERA_ICP.elf`（需推上去） |
| 模块构建 | 设备上**没有** `/lib/modules/$(uname -r)/build`（模块必须离线交叉编译） |
| debugfs | 未挂载（探针要用的 `clk_summary` / `pm_genpd` 得先 mount） |

结论：**ICP 现在处于「上电即可安全探测」的冷态**；但也说明没有框架上电就无法读寄存器。

## 6. B2 分阶段计划（按风险从低到高，逐段单独批准）

### Stage A —— 只读探针（建议下一步）

做什么：

1. 在 `sm8250-kebab.dtsi` 增加 `cam_a5` 节点（照原厂 DT 抄 reg/reg-names/interrupts/clocks，
   `status = "disabled"`，由 overlay 或模块参数开启），
2. 写一个**只读**模块 `titan-icp-probe`：`devm_clk_bulk_get` 四路时钟 +
   `devm_pm_runtime_enable` 挂在 `titan_top_gdsc` 上，ioremap 三个窗口，
   读每窗口前 0x80 字节 → debugfs 文本输出，**一个字节都不写**。

判据（三种可能都有信息量）：

- 若 `0x0ac10058` 读出**非零且像 IOVA/SMMU 地址** → §2 的映射成立，B2b 收尾；
- 若三个窗口全 0 / 全 0xff → 映射到错窗口，或块没真正上电；
- 若读出可识别的版本/常量（如固件版本）→ 直接坐实。

风险与回滚：上电 `titan_top_gdsc` 是正常驱动行为，但会拉起整个 camera 顶层域；
当前 CSIPHY/VFE 都没在用，冲突面很小。**最坏情况是读未上电块导致 NoC 卡死→整机重启**，
所以仍按"维护窗口 + 可重启"对待。回滚 = 卸载模块 + 重启。

### Stage B —— 写队列表指针（需要 Stage A 成功）

在 Stage A 的窗口基础上写 `0x0ac10058`（队列表地址）与 `0x0ac10044/0x0ac10048` 握手，
**但这一步必须以 Stage C 已经把固件跑起来为前提**，否则写进去也没人应答。

### Stage C —— 让 A5 真正跑起来（最高风险，可能不可行）

- 需要 **PAS ID**（原厂 `cam_icp` 驱动里是常量，镜像里没有字符串，离线查不到）；
- 需要确认 TZ 是否接受我们手里这份 `CAMERA_ICP.elf`：

      $ readelf -l CAMERA_ICP.elf
      LOAD 0x100000 vaddr 0x00000000 filesz 0x108da0   # 代码从文件 1 MiB 处开始
      LOAD 0x208da0 vaddr 0x00150000 filesz 0x0
      LOAD 0x208da0 vaddr 0x00350000 filesz 0x44000

  这是**带调试符号的原始 REX ELF**（23 个 section，含 `.debug_info`/`.symtab`），
  **文件里没有签名/哈希表**；而本机 `secboot=enabled`。ADSP/SLPI 能用 8 KB 的桩 mbn 起，
  说明 PAS 通路本身不校验业务内容，但 ICP 是否同样宽松**未知**。

## 7. 仍然未知（不假装已解决）

1. **PAS ID**：离线无来源；只能从 OnePlusOSS 的 `cam_icp` 驱动源码或实测（逐个 ID 试
   `qcom_scm_pas_auth_and_reset`，错 ID 返回 -EINVAL，不致命但要谨慎）。
2. **A5 的启动方式**：`a5_csr`（0xac18000）里的启动地址/复位寄存器偏移还没解出来；
   固件的 `fwEntryFunction` 假设自己已经被搬到正确位置。
3. **TZ 是否接受未签名镜像**（见 Stage C）。
4. **上游 `HFI_REG_*` 的 GP 编号与 Kona 不一致**（§3），必须以实读为准。
5. IQ 参数（B0 遗留）。

## 8. 复现

    # 1. 从原厂 DTB 取 cam-a5 节点（不需要设备、不需要 dtc）
    ./tools/titan-icp/fdt-dump.py \
        ../../local/sensors-20261107/stock-dtb-split/stock-000.dtb \
        --path /soc/qcom,a5@ac00000 --phandle 0x250

    # 2. 与固件侧寄存器表对照（B0 的反汇编）
    grep -n 'movt\s*r[0-9]*, #0xe001' ../../local/titan-b0/disasm-thumb.txt

    # 3. 设备只读基线
    ssh debian@192.168.8.1 'sudo cat /sys/kernel/debug/pm_genpd/pm_genpd_summary | grep -A3 titan_top'

> 注意 `/proc/iomem` 里 `00ac0000` 与 `0x0ac00000` 不是一个东西，前者是 geniqup，
> 读错了会以为 ICP 已经被认领。

## 9. 相关文档

- [B1 HFI 契约固化](camera-titan-hfi-b1-20261111.md)
- [B0 HFI 协议提取](camera-titan-hfi-b0-20261111.md)
- [范围与判定标准](camera-titan-poc-scope-20261111.md)
