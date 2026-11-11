# 相机 Titan ISP PoC —— B1 续作 prompt（2026-11-11，B0 已完成）

> 用法：新会话**只读本文件即可开工**（§3 自带全部硬事实）。深度细节在 [B0 结果](camera-titan-hfi-b0-20261111.md)，本文件是自包含版。
> **第一轮只做 B1，不要上机。**

## 0. 你的任务（一句话）

把 B0 提取出来的 HFI 线格式固化成**可编译、可测试的契约**：`tools/titan-hfi/`（C 头 + Python 编解码 + 断言/单测），并产出一份"最小可跑 CONFIG_IO 负载模板"。**纯离线，零设备风险。**

## 1. 当前状态（交接快照）

| 项 | 值 |
| --- | --- |
| 分支 | `codex/camera-titan-poc-20261111` |
| worktree | `.agents/worktrees/camera-titan-poc`（在 WSL 里 git 才正常，见 §5） |
| HEAD | `b2b3ba1` |
| B0 结果提交 | `db7eb81`（`docs/camera-titan-hfi-b0-20261111.md`，506 行） |
| 固件 | `local/camera-r4-20261110/analysis/CAMERA_ICP.elf`，3,922,232 B，SHA-256 `cb98cec20d1073d4b2872342b3db2185b57422c73e9e7f327887f1b039a1ceb9` |
| 固件版本 | `CICP.FW.1.0.82-4-ICA_REG` / `AAAAAA` / `HARV-PSOMANCH`，构建 `titan.icp.1.0.release82` |
| 设备状态 | 正常常驻；**本阶段不需要碰手机** |
| 已存在 | `docs/` 只有文档；**`tools/` 目录还不存在**，B1 要新建 |

B0 已完成的事（**不要重跑**）：资产校验、DWARF 结构还原、opcode 跳表解出、包类型编号取证、与上游 x1e80100 逐项比对、结论与复用度评估。

## 2. B0 的三条结论（决定 B1 怎么做）

1. **传输层几乎完全一致**：队列头 956 B、队列表头 24 B、字段顺序、三个队列、包类型编号（域分组 = 位 24/25）、系统命令 0x10001–0x10006、opcode 1–0xF —— 与上游 `camss-icp-hfi.h` 逐值相同。
2. **唯一分歧在 async 命令包差 4 字节**（见 §3.3）。**照上游 `hfi_cmd_async` 写必错。**
3. **上游 RFC 的 HFI 引擎根本不存在**：`camss-icp-hfi.h` 只有声明，分支里没有 `camss-icp-hfi.c`，`camss-bps.c` include 的 `camss-icp-hfi-frame.h` 也不存在。所以 B1 **不能"抄实现"**，只能抄常量。

## 3. 已核实的硬事实（自包含，可直接照抄）

### 3.1 队列层

    struct hfi_q_tbl_hdr {           // 24 B，与上游同序同宽
        u32 version; u32 size; u32 q_hdr0_offset; u32 q_hdr_size;
        u32 num_queues; u32 num_active_queues;
    };                               // 后面紧跟 num_queues 个 956 B 队列头

    struct hfi_q_hdr {               // 956 B（14 个字段 + 15 段 u32 padding[15]）
        u32 pad0[15];  u32 status;          // +60
        u32 pad1[15];  u32 start_addr;      // +124  ← 队列缓冲区字节地址
        u32 pad2[15];  u32 type;            // +188  ← 0=cmd 1=msg 2=dbg（按字节读）
        u32 pad3[15];  u32 q_size;          // +252  ← 字数（4 字节为单位）
        u32 pad4[15];  u32 pkt_size;        // +316
        u32 pad5[15];  u32 pkt_drop_cnt;    // +380
        u32 pad6[15];  u32 rx_wm;           // +444
        u32 pad7[15];  u32 tx_wm;           // +508
        u32 pad8[15];  u32 rx_req;          // +572
        u32 pad9[15];  u32 tx_req;          // +636
        u32 pad10[15]; u32 rx_irq_status;   // +700
        u32 pad11[15]; u32 tx_irq_status;   // +764
        u32 pad12[15]; u32 read_idx;        // +828  ← 字索引
        u32 pad13[15]; u32 write_idx;       // +892  ← 字索引
        u32 pad14[15];                      // +896..955
    };

固件侧已证：运行时用 **`0x3bc`(=956) 硬编码步长**遍历队列表；索引与 `q_size` 都以 **4 字节字**为单位（`addr = start_addr + idx*4`）；整个队列表必须在**单一 4 KiB 映射窗口**内（`tryMemMapping(qtbl, 0x1000)`，而 `24+3×956=2892` 刚好装得下）；**`version` 与 `q_hdr_size` 都不校验**。

### 3.2 包类型编号

所有包以 8 字节头 `{u32 nSize; u32 ePacketType}` 开头。域 = `(ePacketType >> 24) & 7`，与 `enum TitanDomain{ICP=0, IPE_BPS=1, CDM=2, DBG=3}` 一致。

| 常量 | 值 | 备注 |
| --- | --- | --- |
| `HFI_CMD_SYS_INIT` | 0x10001 | |
| `HFI_CMD_SYS_PC_PREP` | 0x10002 | |
| `HFI_CMD_SYS_SET_PROPERTY` | 0x10003 | 属性表见 §3.6 |
| `HFI_CMD_SYS_GET_PROPERTY` | 0x10004 | **固件未实现，别用** |
| `HFI_CMD_SYS_PING` | 0x10005 | |
| `HFI_CMD_SYS_RESET` | 0x10006 | |
| `HFI_MSG_SYS_INIT_DONE` | 0x20001 | |
| `HFI_MSG_SYS_PC_PREP_DONE` | 0x20002 | |
| `HFI_MSG_SYS_DEBUG` | 0x20003 | |
| `HFI_MSG_SYS_PING_ACK` | 0x20006 | |
| `HFI_MSG_SYS_RESET_ACK` | 0x20007 | |
| `HFI_MSG_EVENT_NOTIFY` | **0x20008** | 已直接命中 |
| `HFI_CMD_IPEBPS_CREATE_HANDLE` | **0x01010008** | 已直接命中 |
| `HFI_CMD_IPEBPS_ASYNC_COMMAND_DIRECT` | **0x0101000A** | 负载**内联** |
| `HFI_CMD_IPEBPS_ASYNC_COMMAND_INDIRECT` | **0x0101000E** | 负载**在别处**，包内只放 32 位地址 |
| `HFI_MSG_IPEBPS_CREATE_HANDLE_ACK` | 0x1020008 | 32 B |
| 调试命令 | 0x03010008 / 0x03010009 | 上游无定义 |

### 3.3 包结构（**这是最容易抄错的地方**）

| 包 | 布局 | 大小 |
| --- | --- | --- |
| 系统 init / ping / reset | 见 §3.2 头 + 可选 `u64 nClientData@8` | 8 / 16 / 16 |
| 设置属性 | `nSize, ePacketType, nNumProperties@8, rgPropertyData[]@12`（每项 12 B） | 16+ |
| 初始化完成 | `nSize, ePacketType, eErrorType@8, nNumProperties@12, rgPropertyData[]@16` | 20+ |
| 事件通知 | `…, nSessionID@8, nEventID@12, eventData1@16, eventData2@20, rgExtEventData[]@24` | 28+ |
| 建句柄 | `nSize@0, ePacketType@4, eHandleType@8, nUserArg1(u64)@12, nUserArg2(u64)@20` | **28** |
| 建句柄 ACK | `…, eErrorType@8, hHandle@12, nUserArg1@16, nUserArg2@24` | **32** |
| **异步命令** | `nSize@0, ePacketType@4, eCmdOpcode@8, **nUserArg1(u64)@12**, nUserArg2(u64)@20, **nNumHandles@28**, hHandles[]@32, payload@32+4×nNumHandles` | **40 定长（负载在外）** |
| 异步命令 ACK | `…, eCmdOpcode@8, nUserArg1@12, nUserArg2@20, eErrorType@28, cmdPayload[]@32` | 36+ |

**与上游的差异**：上游 `hfi_cmd_async` 在 `+12` 放 `fw_handle(u32)`、`num_handles@32`、`handle[]@36`、`payload@40` —— 整体比固件后移 4 字节。**以固件为准。** 反过来 **ACK 包是完全对齐的**。

负载放置规则（固件 `ProcessAsyncCommand` 实测）：

- **DIRECT**：`nSize >= 期望负载字节数 + 32 + 4×nNumHandles`，负载内联；
- **INDIRECT**：`nSize >= 40`，`32+4×nNumHandles` 处存一个 **u32 SMMU 地址**，指向负载。

推论：`IPE_FRAME_PROCESS`（负载 14328 B）**只能走 INDIRECT**；BPS `FRAME_PROCESS`（2360 B）可 DIRECT。

其它硬约束：单包上限 **6019 B**（`0x1783`）；`nSize` 必须 4 字节对齐且非 0；整条 HFI 只认 **32 位地址**（`SMMU_ADDR = UINTPTR32`）。

### 3.4 opcode 表（`HFI_IPEBPS_CMD_OPCODE_*`，方向 host → ICP）

| opcode | 名称 | 负载结构 | 负载 B |
| --- | --- | --- | --- |
| 0x1 | `BPS_CONFIG_IO` | `_BpsConfigIo` | **840** |
| 0x2 | `BPS_FRAME_PROCESS` | `_BpsFrameProcess` | **2360** |
| 0x3 | `BPS_ABORT` | `_BpsAbort` | 16 |
| 0x4 | `BPS_DESTROY` | `_BpsDestroy` | 8 |
| 0x5 | `IPE_CONFIG_IO` | `_IpeConfigIo` | **1600** |
| 0x6 | `IPE_FRAME_PROCESS` | `_IpeFrameProcess` | **14328** |
| 0x7 | `IPE_ABORT` | `_IpeAbort` | 16 |
| 0x8 | `IPE_DESTROY` | `_IpeDestroy` | 8 |
| 0x9 | `BPS_WAIT_FOR_IPE` | `_BpsWaitForIpe` | 16 |
| 0xa | `BPS_WAIT_FOR_BPS` | `_BpsWaitForBps` | 16 |
| 0xb | `IPE_WAIT_FOR_BPS` | `_IpeWaitForBps` | 16 |
| 0xc | `IPE_WAIT_FOR_IPE` | `_IpeWaitForIpe` | 16 |
| **0xd** | **未识别**（16 B，只登记回调不下发硬件） | 未知 | 16 |
| 0xe | `MEM_MAP` | `_IpeBpsMemMap` | 20 |
| 0xf | `MEM_UNMAP` | `_IpeBpsMemMap` | 20 |

**15/15 的"opcode → 负载字节数"与 DWARF `byte_size` 一对一吻合**，这是 B0 的核心交叉验证，B1 要用静态断言把它钉死。

> **不要混淆**：固件里另有 `CMD_OPCODE_*`（0..9，`CONFIG_IO/FRAME_PROCESS/ABORT/DESTROY/*_DONE/EVENT_WAIT`）与 `_BpsCommand`(188 B)/`_IpeCommand`(196 B)，那是 **ICP 内部发给 BPS/IPE 硬件的命令流**，与 HFI 无关。

### 3.5 负载关键结构

    typedef uint64_t FWUSERARG;      // 所有 async 负载最后 8 字节，固件原样回传
    typedef uint32_t SMMU_ADDR;      // 注意：32 位

    typedef struct _ImageDescriptor {            // 92 B
        ImageInfo      info;                     // 60 B
        BufferLayout   bufferLayout[2];          // 16 B
        BufferLayout   metadataBufferLayout[2];  // 16 B
    } ImageDescriptor;

- `BpsConfigIoData` = `{ImageDescriptor images[9]; u32 secureMode;}` → 832 ✔
- `IpeConfigIoData` = `{ImageDescriptor images[17]; ImageDimensions stabilizationMargins; u32 secureMode; u32 icaMaxUpscalingQ16; u32 maxBatchSize; u32 muxMode; IpeTopologyType topologyType;}` → 1592 ✔
- 外层都是 `{<Xxx>ConfigIoData cmdData; FWUSERARG userArg;}`

IQ 参数（真正的调校面，以 SMMU 地址交引用）：`_BpsIQSettings` **1036 B**、`_IpeIQSettings` **3676 B**。

完成结构（就是 ACK 的 `cmdPayload[]`）：`_BpsConfigIoDone` 16 B、`_BpsFrameProcessDone` 24 B、`_IpeConfigIoDone` 152 B、`_IpeFrameProcessDone` 152 B、`_IpeBpsMemMapDone` 12 B。

### 3.6 属性表（SET_PROPERTY，每项 12 字节）

| id | 含义 | 上游 |
| --- | --- | --- |
| 1 | 调试配置 + STM trace | `HFI_PROP_SYS_DEBUG_CFG = 1` ✔ |
| 2 | UBWC 配置（`HFI_UBWC_CONFIG_TYPE` 8 B） | `HFI_PROP_SYS_UBWC_CFG = 2` ✔ |
| 3 | **不支持** | `IMAGE_VER`（只读，合理） |
| 4 | **不支持** | `SUPPORTED`（只读，合理） |
| 5 | IPEBPS PC | `HFI_PROP_SYS_IPEBPS_PC = 5` ✔ |
| 6 / 7 | 内部标量（上游无） | — |
| 8 | HANG_DUMP_MODE（上游无） | — |
| 9 | UBWC_CONFIG_EX（`HFI_UBWC_CONFIG_TYPE_EX` 16 B，上游无） | — |

只读属性 4 的负载（固件给了定义，36 B）：

    struct HFI_SYSTEM_SUPPORTED_TYPE {
        u32 apiVersion;           // +0
        u32 deviceTitanVersion;   // +4   ← Kona = 7
        u32 numberICPHW;          // +8
        u32 deviceHWVersion[4];   // +12
        u32 bForce8BppIpe;        // +28
        u32 reservedPlaceHolder;  // +32
    };

### 3.7 关键函数地址（想自己复核时用）

| 函数 | 地址 | 用途 |
| --- | --- | --- |
| `IpeBpsDomainCmdHandler` | 0x1f828 | 建句柄 / async direct / indirect 三选一 |
| `ProcessAsyncCommand` | 0x1fc08 | opcode 跳表 0x1fc4c；负载尺寸 |
| `ICPDomainCmdHandler` | 0x1dcb8 | 系统命令跳表 0x1dcf0；属性跳表 0x1e40a |
| `DbgDomainCmdHandler` | 0x1da48 | 调试域 |
| `ICPHostInterface_driverTask` | 0x1edfc | 收包循环；6019 B 上限 |
| `HFI_queueTryWrite` | 0x1dacc | 队列写语义 |
| `HFI_QueueGetQHdr` | 0x1dbd6 | 队列表遍历（956 步长） |
| `CheckCommandSize` | 0x22560 | direct/indirect 长度校验 |

## 4. B1 的具体步骤

1. **`tools/titan-hfi/titan_hfi.h`**：把 §3 的结构与常量写成 C 头（`__attribute__((packed))` + `_Static_assert`）。
   **必须钉死的尺寸**：956 / 24 / 8 / 16 / 28 / 32 / 40 / 12 / 20 / 840 / 2360 / 1600 / 14328 / 16 / 8 / 20 / 92 / 1036 / 3676。
2. **`tools/titan-hfi/titan_hfi.py`**：纯 Python 编解码器（无第三方依赖），提供
   `encode_async(opcode, handles, payload, direct)` / `decode(pkt)` / `queue_header_bytes()`；
   单测覆盖：**async 命令 payload 偏移 = 32+4n**（n=1,2,3）、direct/indirect 两态、长度校验失败用例、opcode→尺寸表。
3. **`tools/titan-hfi/extract_opcodes.py`**：把 B0 的手工反汇编固化成脚本 —— 输入 `llvm-objdump` 输出，输出「opcode → 跳表目标 → `CheckCommandSize` 立即数」表，并与 #1 的静态断言交叉核对。**这是防止以后记错的唯一办法。**
4. **最小 CONFIG_IO 模板**：`_BpsConfigIo` / `_IpeConfigIo` 全零 + 一个 / 两个有效 `ImageDescriptor`，作为 B2 的"敢发出去"的最小报文。
5. **`docs/camera-titan-hfi-b1-20261111.md`**：契约说明 + 测试结果 + 与上游的差异清单（**只列差异，相同项一句带过**）。

## 5. 环境坑（实测过，别重踩）

- **每次 run_code 的 WSL 是独立进程**：`/tmp` 不跨调用保留。中间产物写 `local/`（已 gitignore），B0 的产物在 `local/titan-b0/`（含 `dwarf2c.py` / `refs.py` / `resolve.py`，可复用）。
- **GNU `objdump` 2.44 在这台机器上是 x86-only 构建**，对 ELF32 ARM 直接报 `can't disassemble for architecture UNKNOWN`。**必须用 `llvm-objdump` 19**：
      llvm-objdump -d --triple=thumbv7a-none-eabi --no-show-raw-insn CAMERA_ICP.elf
- **本镜像是 ARM/Thumb 混合**：symbol value 奇数 = Thumb，偶数 = ARM。驱动/HFI 代码全是 Thumb。
- **ARM 上不能用搜字节找常量**：32 位常量是 `movw`+`movt` 拼的，连续 4 字节从不出现。必须解析指令对。
- `readelf --debug-dump=info` 里成员偏移是 `2 byte block: 23 3c (DW_OP_plus_uconst: 60)`，**要取 `DW_OP_plus_uconst` 的数**，不要抓第一个数字。
- 固件提取需要项目内的 `libdeflate.so.0`（`local/sensors-20261107/erofs-tool/usr/lib/x86_64-linux-gnu`）—— **B1 不需要重新提取**，固件已在 `local/camera-r4-20261110/analysis/`。
- `lore.kernel.org` 直连 **403**；用 `git.kernel.org` 的 `/plain/...?h=<分支名>`（`+` 要写成 `%2B`）、`lkml.iu.edu`、`lists.openwall.net`。
- **WSL 里 git 只有 `.agents/worktrees/camera-titan-poc` 能用**；`dsh-*` 那些 worktree 的 `.git` 文件是 Windows 路径，WSL 下报 `not a git repository`。
- 仓库 git 身份未配置，提交时用：`git -c user.name='Codex' -c user.email='codex@localhost' commit …`（与历史一致）。

## 6. 边界（硬性，违反即失败）

- 不写任何分区；不用 `fastboot`（本阶段连 `fastboot` 都不需要）。
- **不碰手机**：不改 `/srv`、不改常驻服务、不碰 persist/校准、不动充电/温控/电源键/USB-SSH/网络。
- 固件与逆向中间产物一律放 `local/`；`/local/`、`*.elf`、`*.bin` 已 gitignore，**不要 `git add`**。
- **连续两次尝试无进展就停下来汇报**，不要无限试错。
- **B2（上机）需要用户明确批准 + 维护窗口**，本阶段不涉及。

## 7. 交付验收

`docs/camera-titan-hfi-b1-20261111.md` 必须包含：

1. 可编译的 `tools/titan-hfi/titan_hfi.h` + 全部静态断言通过记录
2. Python 编解码器 + 单测输出（含 **async payload 偏移 32+4n** 的三组用例）
3. `extract_opcodes.py` 的自动重抽结果，与 B0 的 15 条 opcode 表**逐条一致**
4. 最小 CONFIG_IO 负载模板（字节数 + 十六进制 dump）
5. 与上游的差异清单（差异项 + 相同项一句带过）

若某一步做不到，**如实记录卡点与已排除的路径，不要编造**。

## 8. 提交约定

- 只提交 `docs/` 与 `tools/`；固件、镜像、日志一律 `local/`。
- 提交信息参考仓库历史：`docs(camera): …` / `tools(camera): …`。

## 9. 留给 B2 的未知（**本轮不要解决**）

1. **AP 侧寄存器映射**——唯一的真阻塞。固件里看到的是 A5 自己的地址视图（`0xe0010058` 队列表指针、`0xe0010014` 抬中断、`0xe0010060` 清状态），**不是** AP 物理地址。可查：原厂 `kona-camera.dtsi` 的 `cam_a5@ac00000` reg 窗口、下游 `camera-kernel` 的 `icp_hw` 表（只读）。
2. **固件加载方式**：kona 是单 ELF，上游是 `.mdt` + `qcom_scm`，能否被 TZ 接受未知。
3. **IQ 参数从哪来**：`com.qti.tuned.default.bin` / `CFR_*.bin` 格式未解，不解就只能跑全零参数。
4. opcode 0xD 的语义。

## 10. 相关文档

- [B0 结果（深度版）](camera-titan-hfi-b0-20261111.md)
- [范围与判定标准](camera-titan-poc-scope-20261111.md)
- [B0 时代的续作 prompt（历史）](camera-titan-poc-resume-prompt-20261111.md)
