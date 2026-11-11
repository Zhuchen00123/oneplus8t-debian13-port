# A5 / ICP 寄存器首次读出真实值（B2 夜，2026-11-12）

> 一句话：**A5 三个寄存器窗口之所以一直读 0，是因为缺 `CAM_CC_CAMNOC_AXI_CLK`（以及 CPAS AHB、慢速 AHB）。
> 把 camcc 的 CAMNOC/CPAS 时钟一起打开后，A5 的 HW_VERSION、GIC、CSR 全部读出合理值。**
> 全程 T1（`fastboot boot`，零分区写入），模块只读、不写任何 A5 寄存器。

---

## 1. 怎么发现的

社区线索（用户提示「优先看社区同类项目」）→ LKML 2026-08 的
**[PATCH v2 0/3] Add Qualcomm CAMNOC ICC provider**（Atanas Filipov）：
SM8250 的 CAMNOC 是相机子系统内部 AXI 互连，多个子设备（IFE/JPEG/BPS/IPE/**ICP**）共享
`CAM_CC_CAMNOC_AXI_CLK`，该系列明确把 **ICP** 列为 CAMNOC 主端口之一。

对照原厂驱动：`cam_a5_init_hw()` 在使能时钟/GDSC 之后调用的是 `cam_cpas_start()`
（CPAS = 相机外设访问子系统，负责 CAMNOC 的 AXI 投票）。
我们此前的探针只开了 4 路 ICP 时钟 + `titan_top_gdsc`，**没有开 CAMNOC/CPAS**，
于是所有窗口读回 0——看起来像「块没上电」，其实是**互连没时钟**。

## 2. 改动

DT 的 `cam-a5` 节点增加 4 路时钟（其余不变）：

```dts
clocks = <&camcc CAM_CC_FAST_AHB_CLK_SRC>,   /* 33 */
         <&camcc CAM_CC_ICP_AHB_CLK>,        /* 38 */
         <&camcc CAM_CC_ICP_CLK_SRC>,        /* 40 */
         <&camcc CAM_CC_ICP_CLK>,            /* 39 */
         <&camcc CAM_CC_CAMNOC_AXI_CLK_SRC>, /* 6  */
         <&camcc CAM_CC_CAMNOC_AXI_CLK>,     /* 5  */
         <&camcc CAM_CC_CPAS_AHB_CLK>,       /* 13 */
         <&camcc CAM_CC_SLOW_AHB_CLK_SRC>;   /* 109 */
```

探针模块按名字取时钟并使能；速率取 lowsvs 档：fast_ahb 100 MHz、icp_src 400 MHz、
camnoc_axi_src 400 MHz、slow_ahb 80 MHz。

## 3. 实测读数（RAM 启动，keep_on=1）

### 3.1 sierra 窗口（0x0ac10000，HFI CSR）

| 偏移 | 名字 | 读数 |
| --- | --- | --- |
| 0x000 | A5_HW_VERSION | **0x10010000** |
| 0x004 | A5_CSR_NSEC_RESET | 0x00000000 |
| 0x008 | A5_CSR_A5_CONTROL | 0x00003000 |
| 0x00c | ETM 控制 | 0x00000100 |
| 0x010 | A2HOSTINTEN | 0x00000000 |
| 0x030 | HOST2ICPINT | 0x00000000 |
| 0x044 | FW_VERSION | 0x00000000（无固件运行） |
| 0x048/0x04c | INIT_REQUEST / INIT_RESPONSE | 0x00000000 / 0x00000000 |
| 0x050~0x080 | 队列/堆/区域指针 | 全 0（从未初始化） |
| 0x200 | A5_STATUS | **0x00000200** |

### 3.2 qgic 窗口（0x0ac00000）——A5 的 GIC-400，真的活着

| 偏移 | 含义 | 读数 |
| --- | --- | --- |
| 0x000 | GICD_CTLR | 0x00000000 |
| 0x004 | **GICD_TYPER** | **0x00000402**（ITLinesNumber=2 → 96 条中断线；SecurityExtn=1） |
| 0x008 | **GICD_IIDR** | **0x00001070** |

### 3.3 csr 窗口（0x0ac18000）

| 偏移 | 读数 |
| --- | --- |
| 0x000 | 0x00010705 |
| 0x004 | 0x10000000 |
| 其余 | 0 |

## 4. 因果验证（同一地址、开/关时钟对照）

| 状态 | `0xac10000` 读数 | GDSC | 时钟 |
| --- | --- | --- | --- |
| 模块加载、时钟开 | **0x10010000** | on | camnoc/icp 计数 1 |
| `rmmod` 后 | **0x00000000** | off-0 | 全部计数 0 |

同一地址、同一进程、只差电源/时钟状态 ⇒ **读 0 就是「没上电/没时钟」，不是「地址不对」**。
这同时推翻了我昨夜 G5 的结论（当时写「块本身不响应」），也解释了为什么三个窗口全 0。

## 5. 这次拿到的确定结论

1. `0x0ac00000 / 0x0ac10000 / 0x0ac18000` 三段窗口在主线 7.2 上**可以正常访问**，
   前提是 camcc 的 ICP + CAMNOC + CPAS 时钟和 `titan_top_gdsc` 都使能。
2. **A5 的 GIC-400 是活的**（TYPER/IIDR 合理），不再是「黑盒」。
3. `A5_HW_VERSION = 0x10010000` 可作为「块已上电」的判定值。
4. 固件**没有**运行：`FW_VERSION = 0`，所有队列/内存指针为 0，`INIT_REQUEST = 0`。
5. `A5_CONTROL` 默认读出 `0x3000`（bit12/13 置位）、`A5_STATUS = 0x200`，
   与「A5 处于门控/等待状态」一致；具体位义待 P4 用写路径确认。

## 6. 下一步（P4 需要写，未执行）

按原厂顺序，P4 需要：

1. 用 `dma_alloc_coherent` 分配 QTBL / shmem / heap / SFR，映射到 ICP 的 IOVA 空间
   （主线没有 cam_smmu 驱动，需要自建 IOMMU 域或用物理地址试探）。
2. 写 `A5_CONTROL = A5_EN|WAKE_UP_EN|EN_CLKGATE_WFI`、`NSEC_RESET = 0x10`。
3. 填 QTBL 头（cmd 4096 / msg 4096 / dbg 102400），写 `QTBL_PTR`、`SFR_PTR`、
   `SHARED_MEM_PTR/SIZE`、`UNCACHED_HEAP_PTR/SIZE`，再写 `INIT_REQUEST = SET`。
4. 轮询 `INIT_RESPONSE == SUCCESS`，读 `FW_VERSION`。

**风险**：写 `A5_CONTROL` 会真正启动 A5 核；没有固件与 SMMU 映射时可能触发总线异常
（最坏整机复位，但 boot_a 不受影响，因为全程 RAM 启动）。
所以这一步必须由用户明确批准后、在人能在场按按键的前提下做。

## 7. 复现命令

```bash
# 1) 构建（DT 含 camnoc 时钟 + 模块）
make -C ~/oneplus8t-venus-20261105/linux O=~/oneplus8t-venus-20261105/out ARCH=arm64 LLVM=1 -j16 \
     Image.gz qcom/sm8250-oneplus-kebab.dtb
make -C ~/oneplus8t-venus-20261105/linux O=~/oneplus8t-venus-20261105/out ARCH=arm64 LLVM=1 \
     M=~/titan-a5-probe modules
# 2) 打包 + RAM 启动（T1，零写入）
mkbootimg ... --output boot-g5.img      # 不要补零
fastboot boot boot-g5.img
# 3) 探针
sudo insmod titan_a5_probe.ko keep_on=1
sudo dmesg | grep titan-a5-probe
sudo rmmod titan_a5_probe
```

产物：`local/titan-b2/night/g5/{boot-g5.img,titan_a5_probe.c}`、日志 `local/titan-b2/night/g5-ramboot.log`。
