# Titan ISP PoC B0 —— HFI 协议离线提取与上游比对（2026-11-11）

> 状态：**B0 完成**（纯离线，未接触设备）
> 分支：`codex/camera-titan-poc-20261111`（worktree `.agents/worktrees/camera-titan-poc`）
> 上游：[范围与判定标准](camera-titan-poc-scope-20261111.md) §2 B0
> 固件：`local/camera-r4-20261110/analysis/CAMERA_ICP.elf`，3,922,232 B，SHA-256 `cb98cec20d1073d4b2872342b3db2185b57422c73e9e7f327887f1b039a1ceb9`（与 scope §4 一致）
> 中间产物：`local/titan-b0/`（已 gitignore，不入库）

## 0. 结论先行

一句话：**线格式几乎完全一致，实现几乎为零。**

1. **传输层逐字节相同。** 队列头 `956 B`、队列表头 `24 B`、字段顺序、`type` 在 `+188`、运行时索引以 4 字节为单位、三个队列（cmd/msg/dbg）——固件与上游 `camss-icp-hfi.h` 的定义**完全一致**；这不是巧合，双方都源自 CAMX 的 `hfidefs.h`。
2. **包类型编号（含 8/24/25 位分组）、系统命令 0x10001–0x10006、系统消息 0x20001–、IPE/BPS 命令 0x1010008/0x101000a/0x101000e、属性号 1/2/5** 全部**逐值相同**，是本次最强的可用资产。
3. **唯一的线格式分歧在 async 命令包**：固件 `HFI_CMD_IPEBPS_ASYNC_COMMAND_TYPE` 是 **40 字节定长**、`nUserArg1` 在 `+12`、`nNumHandles` 在 `+28`、**payload 起点 = `32 + 4×nNumHandles`**；上游 `hfi_cmd_async` 在 `+12` 多插了一个 `fw_handle`，把 `num_handles`/`handle[]`/`payload` 整体后移 4 字节。**照上游写必错。**（ACK 包反而完全对齐。）
4. **固件把整条 ISP 的调校 ABI 都带在 debug_info 里**：`_BpsConfigIo` 840 B、`_BpsFrameProcess` 2360 B、`_IpeConfigIo` 1600 B、`_IpeFrameProcess` 14328 B，以及 `BpsIQSettings`（1036 B）/ `IpeIQSettings`（3676 B）里的逐模块参数。**上游 RFC 完全没有任何 payload 定义**。这一块是额外赚到的，但也是必须自己写的。
5. **上游 RFC 的 HFI 引擎根本不存在**：`camss-icp-hfi.h` 声明了 `icp_hfi_init_queues()` / `icp_hfi_core_init()` / `icp_hfi_process_msg_queue()` 等，但分支里**没有 `camss-icp-hfi.c`**，`camss-bps.c` 引用的 `camss-icp-hfi-frame.h` 也不存在。也就是说：能借的是**头文件里的线格式常量**，不能借的是**任何可运行的 HFI 代码**。

**可复用比例判断：传输层定义 = 高（可直接抄常量与小结构，约 100–120 行头文件）；实现层 = 低（HFI 引擎、payload 编解码、固件加载全部要自写）；整体 = 中偏低。**
详见 §8。**建议进入 B1（结构自洽固化），但把 B2 的判据从"借上游"改成"自证 + 一次最小启动"。**

---

## 1. 方法、工具链与环境坑（可复现）

### 1.1 实际的工具链

| 用途 | 工具 | 备注 |
| --- | --- | --- |
| ELF 头/节/符号 | `readelf` (GNU binutils 2.44) | 可用 |
| **反汇编** | **`llvm-objdump` 19** | **GNU `objdump` 2.44 在这台机器上是 x86-only 构建，对 ELF32 ARM 直接报 `can't disassemble for architecture UNKNOWN`** |
| DWARF 解析 | `readelf --debug-dump=info` + 自写解析器 | 系统无 pyelftools、无 pip3 |

`objdump` 不可用这条是本次新踩的坑，记在这里以免下次重踩。`llvm-objdump` 需要显式指定 Thumb：

    llvm-objdump -d --triple=thumbv7a-none-eabi --no-show-raw-insn CAMERA_ICP.elf

**本镜像是 ARM/Thumb 混合**：symbol value 为奇数 = Thumb 函数（如 `ICPHostInterface_driverTask @ 0x1edfd`、`IpeBpsDomainCmdHandler @ 0x1f829`），偶数 = ARM（如 `kernel_init_warm @ 0x0`）。驱动/HFI 相关代码**全是 Thumb**，所以整段用 thumbv7a 反汇编即可；只有启动早期（`kernel_init_warm` 等）要换回 ARM。

### 1.2 本次写的小工具（放在 `local/titan-b0/`，未入库）

| 文件 | 作用 |
| --- | --- |
| `dwarf2c.py` | 把 `readelf --debug-dump=info` 的文本 DIE 树还原成 C 结构体定义（含 `byte_size`、逐成员偏移、匿名 union 展开、typedef/数组/指针解析、enum 值）。`--list` / `--show` / `--grep` / `--uses` |
| `refs.py` | 在反汇编文本里重建 `movw/movt` 拼出的 32 位常量，反查"哪条指令引用了某个字符串/某个地址" |
| `resolve.py` | VA→节内文件偏移→字符串，把日志格式串落回代码位置 |

**关键技巧**：ARM 的 32 位常量是用 `movw`+`movt` 两条指令拼出来的，所以在二进制里**搜不到** `0x01010008` 这种 4 字节小端模式。第一次直接扫 `.text` 的 4 字节字，全部 0 命中，差点误判成"固件编号不同"；改成解析 `movw/movt` 对之后立刻命中。

### 1.3 交叉验证方法（本次最重要的方法论）

固件对每个 opcode 都会先做一次长度校验：

    mov  r0, r7                  ; pkt
    mov.w r1, #<expected>        ; payload 期望字节数
    mov  r2, r5                  ; direct(1)/indirect(0)
    bl   CheckCommandSize

于是"opcode → payload 字节数"可以从**代码里的立即数**独立读出，再和 **DWARF 里同名结构的 `byte_size`** 对照。15 个 opcode 全部一对一命中（§4），这直接证明：**debug_info 里的结构体定义就是线上格式**，不是内部表示。

---

## 2. 固件身份（自报）

| 项 | 值 | 出处 |
| --- | --- | --- |
| 版本串 | `QC_IMAGE_VERSION_STRING=CICP.FW.1.0.82-4-ICA_REG` | .rodata `0x2cc40` |
| 镜像变体 | `IMAGE_VARIANT_STRING=AAAAAA` | .rodata `0x2cc71` |
| OEM 版本 | `OEM_IMAGE_VERSION_STRING=HARV-PSOMANCH` | .rodata `0x2cc8d` |
| 构建路径 | `...\flat_branch\components\dev\titan.icp\1.0\jiesong.titan.icp.1.0.release82\fw_core\...` | 多处 |
| 架构 | ELF32 ARM EABI5，`cortex-a5`，REX 内核（Qualcomm RTOS） | ELF 头 / `.ARM.attributes` |
| 段 | `REX_CORE_RO`(0x0, 226,928 B, AX) / `REX_CORE_RW`(0x100000, 36,256 B, WA) / `REX_CORE_ZI` / `AMSS_HEAP_SEG`(2 MiB) / `PAGE_TABLE` | 节表 |
| 固件格式 | **单一 ELF**（入口 0x0，3 个 program header） | ELF 头 |
| Titan 代号枚举 | `TITAN_480_V1 = SPECTRA_480 = 7`（Kona）；同镜像还含 `TITAN_580_V1/Lahaina = 10`、`SPECTRA_680 = 15` 等 | `enum` DIE |
| 字符串对照 | `TITAN_480_V1 (Kona)`、`TITAN_580_V1 (Lahaina)`、`TITAN_150_V1 (Talos)` … | .rodata |

**推论（用于 B2 的预期，尚未验证）**：这是一份**跨平台族固件**（同一份镜像里带 Kona/Lahaina/Talos 等分支），靠 `chipinforeader480.c` / `chipinforeader580.c` 在运行时选支。所以"Titan 1.0 vs x1e80100 那代"的差异，**不在协议层而在 payload 与硬件编程序列**。

其余线索（未逐一追）：`ELF variant: %s , API version: 0x%x` —— 固件会在某个报文里回 `apiVersion`；`HFI_SYSTEM_SUPPORTED_TYPE.apiVersion` 就是它（见 §6）。

---

## 3. HFI 传输层

### 3.1 队列与队列表

固件的 `HFI_QueueGetQHdr(idx, &out)`（`0x1dbd6`）逻辑：读 `[0xe0010058]` 得到队列表指针 → `tryMemMapping(ptr, 0x1000)` → 用 **`qhdr0_offset` 定位第一个队列头，步长固定 `0x3bc = 956`**，逐个读 `qhdr_type`（`+188`，按字节）匹配 `idx`。`ICPHostInterface_driverTask` 依次取 idx = 0/1/2 三次，正好三个队列。

| 项 | 固件 | 上游 `camss-icp-hfi.h` | 判定 |
| --- | --- | --- | --- |
| 队列表头类型 | `HFI_QUEUE_TABLE_HEADER_TYPE` **24 B** | `struct hfi_q_tbl_hdr` 24 B | **相同** |
| 表头字段 | `qtbl_version, qtbl_size, qtbl_qhdr0_offset, qtbl_qhdr_size, qtbl_num_q, qtbl_num_active_q` | `version, size, q_hdr0_offset, q_hdr_size, num_queues, num_active_queues` | **同序同宽** |
| 队列头类型 | `HFI_QUEUE_HEADER_TYPE` **956 B** | `struct hfi_q_hdr` 956 B | **相同** |
| 队列头字段 | 14 个字段，每个前面 `uint32_t padding[15]`（共 15 段 padding）；`status@60, start_addr@124, type@188, q_size@252, pkt_size@316, pkt_drop_cnt@380, rx_wm@444, tx_wm@508, rx_req@572, tx_req@636, rx_irq_status@700, tx_irq_status@764, read_idx@828, write_idx@892` | 同 14 字段、同 15 段 `dummy[15]`、同顺序 | **逐字节相同** |
| 实际步长 | 运行时用 `0x3bc`（=956）硬编码遍历 | `sizeof(struct hfi_q_hdr)` = 956 | **相同** |
| 队列数/序 | 3 个：0=命令、1=消息、2=调试（`HFI_QueueGetQHdr(0/1/2)`） | `HFI_Q_CMD_TYPE=0, HFI_Q_MSG_TYPE=1, HFI_Q_DBG_TYPE=2` | **相同** |
| 索引单位 | **4 字节字**：`addr = start_addr + idx*4`，`q_size` 也是字数（`HFI_queueTryWrite` 用 `pkt->nSize>>2` 与 `q_size` 比较、绕回时 `idx -= q_size`） | 头部注释未写单位；`hfi_queue_free()` 的算法一致 | **语义相同，单位需自证** |
| 队列满处理 | 置 `qhdr_tx_req`(636) 并返回 0x3E9 | 无（引擎缺） | 固件多 |
| 表版本号 | **不做校验**（全程没有比较 `qtbl_version`）；`qtbl_qhdr_size` 也**被忽略**（用固定 956） | `HFI_QTBL_VERSION 0xFFFFFFFF` | 上游常量约束，固件不强制 |
| 队列表映射窗口 | `tryMemMapping(qtbl, 0x1000)` —— 4 KiB 足以容纳 `24 + 3×956 = 2892` | 无 | 固件约束，**小但重要** |

> 结论：**队列层可以原样复用上游定义**（956/24 + 字段名），只需补三条固件侧事实：索引以字为单位、队列表必须落在单一 4 KiB 窗口内、版本号不校验。

### 3.2 包类型编号与 8/24/25 位分组

固件按 `(ePacketType >> 24) & 7` 选域（`ICPHostInterface_driverTask` @ `0x1f292`：`ldr r2,[r1,#4]` 取包类型，`ubfx r0, r2, #0x18, #0x3`），再查一张 3 项的函数表。域枚举在 DWARF 里是现成的：

    enum TitanDomain { TITAN_DOMAIN_ICP = 0, TITAN_DOMAIN_IPE_BPS = 1,
                       TITAN_DOMAIN_CDM = 2, TITAN_DOMAIN_DBG = 3 };

与上游 `HFI_CMD_GRP_ICP=0 / HFI_CMD_GRP_IPE_BPS=BIT(24) / HFI_CMD_GRP_CDM=BIT(25) / HFI_CMD_GRP_DBG=BIT(24)|BIT(25)` **完全对应**。

三处独立取证：

| 证据 | 代码 | 印证 |
| --- | --- | --- |
| ICP 域 | `ICPDomainCmdHandler` @ `0x1dcb8`：`sub.w r0, r6, #0x10001; cmp r0, #0x5; bhi → 不支持; tbh` | 0x10001–0x10006 六条系统命令 |
| IPE/BPS 域 | `IpeBpsDomainCmdHandler` @ `0x1f828`：`movs r0,#0x8/#0xa/#0xe; movt r0,#0x101; cmp r6,r0` | 0x01010008 / 0x0101000a / 0x0101000e |
| 调试域 | `DbgDomainCmdHandler` @ `0x1da48`：`movs r3,#0x8/#0x9; movt r3,#0x301` | 0x03010008 / 0x03010009 |

### 3.3 包头与包类型编号表

所有包共享 8 字节头 `{u32 nSize; u32 ePacketType}`（固件全部 16 个包结构都以 `nSize`/`ePacketType` 开头，偏移 0/4）。

| 常量 | 值 | 固件取证 | 上游 | 判定 |
| --- | --- | --- | --- | --- |
| `HFI_CMD_SYS_INIT` | 0x10001 | 范围检查 `0x10001..0x10006` + 跳表 idx0 | 同值 | 相同 |
| `HFI_CMD_SYS_PC_PREP` | 0x10002 | 同上（跳表 idx1 → 打印 "Received HFI_CMD_SYS_PC_PREP command" @0x1ddde） | 同值 | 相同 |
| `HFI_CMD_SYS_SET_PROPERTY` | 0x10003 | 跳表 idx2 → 属性分发 | 同值 | 相同 |
| `HFI_CMD_SYS_GET_PROPERTY` | 0x10004 | 跳表 idx3 → **不支持**（走 bhi 错误路径） | 有定义 | 固件未实现 |
| `HFI_CMD_SYS_PING` | 0x10005 | 打印 "Received HFI_CMD_SYS_PING command"，回 16 B ACK | 同值 | 相同 |
| `HFI_CMD_SYS_RESET` | 0x10006 | 打印 "Received HFI_CMD_SYS_RESET command" | 同值 | 相同 |
| `HFI_MSG_SYS_INIT_DONE` | 0x20001 | SYS_INIT 分支组包回发 | 同值 | 相同 |
| `HFI_MSG_SYS_PC_PREP_DONE` | 0x20002 | PC_PREP 分支 | 同值 | 相同 |
| `HFI_MSG_SYS_DEBUG` | 0x20003 | `HFI_MSG_SYS_DEBUG_PACKET_TYPE` | 同值 | 相同 |
| `HFI_MSG_SYS_IDLE` | 0x20004 | 未找到显式引用 | 同值 | 未证 |
| `HFI_MSG_SYS_PROPERTY_INFO` | 0x20005 | 未找到显式引用 | 同值 | 未证 |
| `HFI_MSG_SYS_PING_ACK` | 0x20006 | PING 分支组 16 B 包 | 同值 | 相同 |
| `HFI_MSG_SYS_RESET_ACK` | 0x20007 | RESET 分支 | 同值 | 相同 |
| `HFI_MSG_EVENT_NOTIFY` | **0x20008** | `postHFIEventNotify` @0x1ed78：`vSYS_AllocMem(0x1c)` 后 `str 0x00020008,[pkt+4]` | 同值 | **直接命中** |
| `HFI_CMD_IPEBPS_CREATE_HANDLE` | **0x01010008** | `IpeBpsDomainCmdHandler` 三次比较之一 | 同值 | **直接命中** |
| `HFI_CMD_IPEBPS_ASYNC_COMMAND_DIRECT` | **0x0101000A** | 同上 → `ProcessAsyncCommand(..., direct=1)` | 同值 | **直接命中** |
| `HFI_CMD_IPEBPS_ASYNC_COMMAND_INDIRECT` | **0x0101000E** | 同上 → `ProcessAsyncCommand(..., direct=0)` | 同值 | **直接命中** |
| `HFI_MSG_IPEBPS_CREATE_HANDLE_ACK` | 0x1020008 | 组 32 B ACK（`memset(sp,0,0x20)`） | 同值 | 相同 |
| `HFI_MSG_IPEBPS_ASYNC_DIRECT_ACK` | 0x102000A | 未逐一取证 | 同值 | 未证 |
| `HFI_MSG_IPEBPS_ASYNC_INDIRECT_ACK` | 0x102000E | 未逐一取证 | 同值 | 未证 |
| 调试命令 | 0x03010008 / 0x03010009 | `DbgDomainCmdHandler` | 上游无定义 | 固件额外 |

### 3.4 包结构表（固件 DWARF vs 上游 C）

| 包 | 固件结构（字段 @ 偏移） | 大小 | 上游结构 | 判定 |
| --- | --- | --- | --- | --- |
| 系统初始化 | `nSize@0, ePacketType@4` | 8 | `hfi_cmd_sys_init{hdr}` 8 | **相同** |
| PING / PING_ACK / RESET / RESET_ACK | `nSize@0, ePacketType@4, nClientData(u64)@8` | 16 | `hfi_cmd_ping{hdr;u64 user_data}` 16 | **相同** |
| 设置属性 | `nSize@0, ePacketType@4, nNumProperties@8, rgPropertyData[]@12` | 16+ | `hfi_cmd_set_property{hdr;u32 num_prop;u32 prop_data[]}` | **相同** |
| 初始化完成 | `nSize, ePacketType, eErrorType@8, nNumProperties@12, rgPropertyData[]@16` | 20+ | `hfi_msg_init_done{hdr;u32 error;u32 prop_num;u32 prop_data[]}` | **相同** |
| PC_PREP 完成 | `nSize, ePacketType, eErrorType@8` | 12 | 上游结构未定义，值对得上 | 相同 |
| 调试消息 | `nSize, ePacketType, eMsgType@8, nMsgSize@12, nTimeStampHi@16, nTimeStampLo@20, rgMsgData[]@24` | 25+ | `hfi_msg_debug{hdr;msg_type;msg_size;timestamp_hi;timestamp_lo;u8 msg_data[]}` | **相同** |
| 事件通知 | `nSize, ePacketType, nSessionID@8, nEventID@12, eventData1@16, eventData2@20, rgExtEventData[]@24` | 28+ | `hfi_msg_event{hdr;session_id;event_id;data1;data2}` | **相同** |
| 建句柄 | `nSize, ePacketType, eHandleType@8, nUserArg1(u64)@12, nUserArg2(u64)@20` | **28** | `hfi_cmd_create_handle{hdr;u32 handle_type;u64 ud0;u64 ud1}` 28 | **相同（packed）** |
| 建句柄 ACK | `nSize, ePacketType, eErrorType@8, hHandle@12, nUserArg1@16, nUserArg2@24` | **32** | `hfi_msg_create_handle_ack{hdr;error;fw_handle;ud0;ud1}` 32 | **相同** |
| **异步命令** | `nSize@0, ePacketType@4, eCmdOpcode@8, **nUserArg1(u64)@12**, nUserArg2(u64)@20, **nNumHandles@28**, hHandles[]@32, payload@32+4×nNumHandles` | **40 定长（payload 在外）** | `hfi_cmd_async{hdr;opcode@8; **fw_handle@12**; ud0@16; ud1@24; **num_handles@32**; handle[]@36; payload[]@40}` | **不同（+4 位移）** |
| 异步命令 ACK | `nSize, ePacketType, eCmdOpcode@8, nUserArg1@12, nUserArg2@20, eErrorType@28, cmdPayload[]@32` | **36+** | `hfi_msg_async_ack{hdr;opcode;ud0@12;ud1@20;error@28;msg[]@32}` 160 | **相同（同偏移）** |
| 调试 dump 命令 | `nSize, ePacketType, nPtr@8` | 12 | 上游无 | 固件额外 |

### 3.5 负载放置规则（固件实测，必须照抄）

`ProcessAsyncCommand` @ `0x1fc30`：

    ldr  r0, [r7, #0x1c]          ; pkt->nNumHandles （+28）
    add.w r1, r7, r0, lsl #2      ; pkt + 4*nNumHandles
    add.w r10, r1, #0x20          ; + 32  → payload 位置
    cmp  r5, #0                   ; r5 = direct 标志
    bne  1f        ; DIRECT：r10 就是包内负载首地址
    ldr.w r10, [r10]              ; INDIRECT：该处是 u32 SMMU 地址，解引用

配合 `CheckCommandSize`（`0x22560`）：

- **DIRECT**：要求 `nSize >= 期望负载字节数 + 32 + 4×nNumHandles`（负载**内联**在同一包里）
- **INDIRECT**：只要求 `nSize >= 40`（负载在别处，包内只放 32 位地址）

两条推论：

1. **`IPE_FRAME_PROCESS`（负载 14328 B）在 DIRECT 下永远不可能成立** —— `14328+32+4 = 14364 B` 远超接收上限（见 §3.6）。它**只能走 INDIRECT**。BPS 的 `FRAME_PROCESS`（2360 B）可以 DIRECT。
2. 负载地址是 **32 位 SMMU 地址**（`typedef UINTPTR32 SMMU_ADDR`），不是 64 位 IOVA。**整个 Titan 的 HFI 只认 32 位地址空间。**

### 3.6 尺寸与上限

| 项 | 固件实测值 | 上游 | 说明 |
| --- | --- | --- | --- |
| 接收包上限 | **6019 B**（`0x1783`，`driverTask` @0x1f1a8 `movw r5,#0x1783; cmp r3,r5; bhi → 丢弃`） | `HFI_CMD_Q_DATA_SIZE = SZ_4K`(4096)、`HFI_MSG_Q_DATA_SIZE = SZ_4K` | **上游偏小** |
| 包长合法性 | `nSize>>2 != 0` 且 `nSize & 3 == 0`（4 字节对齐） | — | 固件强校验 |
| 非 DIRECT 最小包 | 40 B | — | |
| 队列表映射窗口 | 4 KiB | `HFI_QTBL_SIZE = SZ_1M` | 固件只需要 4 KiB |
| 队列表项数 | 3 | `HFI_Q_MAX = 3` | 相同 |

---

## 4. opcode 表（`HFI_IPEBPS_CMD_OPCODE_*`，方向：host → ICP）

编号从 `ProcessAsyncCommand` 的 `tbh` 跳表（`0x1fc4c`）直接解出：`r2 = eCmdOpcode - 1`，`cmp r2, #0xe`（即 1..15 合法），跳表基址 `0x1fc50`。每个分支的 `CheckCommandSize` 立即数即负载期望字节数。

| opcode | 名称（固件字符串 / 结构） | 负载结构 | 负载 B | 负载字段（首层） | 上游同名常量 | 判定 |
| --- | --- | --- | --- | --- | --- | --- |
| 0x1 | `BPS_CONFIG_IO` | `_BpsConfigIo` | **840** | `{BpsConfigIoData cmdData; u64 userArg}`；`BpsConfigIoData{ImageDescriptor images[9]; u32 secureMode}` 见 §5 | 0x1 | **相同** |
| 0x2 | `BPS_FRAME_PROCESS` | `_BpsFrameProcess` | **2360** | `{BpsFrameProcessData cmdData; u64 userArg}`；含 `maxNumOfCoresToUse, targetCompletionTimeInNs, ubwcStatsBufferAddress, cdmBufferAddress, iqSettingsAddr, stripingLibOutAddr, cdmProgramArrayAddr, requestId, numFrameSetsInBatch, FrameSetBps[16] frameSets` | 0x2 | **相同** |
| 0x3 | `BPS_ABORT` | `_BpsAbort` | **16** | `{BpsAbortData{u32 numRequestIds; u32 *requestIds}; u64 userArg}` | 0x3 | **相同** |
| 0x4 | `BPS_DESTROY` | `_BpsDestroy` | **8** | `{u64 userArg}` | 0x4 | **相同** |
| 0x5 | `IPE_CONFIG_IO` | `_IpeConfigIo` | **1600** | `IpeConfigIoData{ImageDescriptor images[17]; ImageDimensions stabilizationMargins; u32 secureMode; u32 icaMaxUpscalingQ16; u32 maxBatchSize; u32 muxMode; IpeTopologyType topologyType}; u64 userArg` | 0x5 | **相同** |
| 0x6 | `IPE_FRAME_PROCESS` | `_IpeFrameProcess` | **14328** | `IpeFrameProcessData`：串流/UBWC/CDM 地址 + `cdmProgramArray*` 七路分块地址 + `requestId, frameOpMode, numFrameSetsInBatch, FrameSet[16] frameSets` | 0x6 | **相同** |
| 0x7 | `IPE_ABORT` | `_IpeAbort` | **16** | `{IpeAbortData cmdData; u64 userArg}` | 0x7 | **相同** |
| 0x8 | `IPE_DESTROY` | `_IpeDestroy` | **8** | `{u64 userArg}`（分支里还做 16 项句柄表查找） | 0x8 | **相同** |
| 0x9 | `BPS_WAIT_FOR_IPE` | `_BpsWaitForIpe` | **16** | `{IpeHandle waitFor; u64 userArg}` | 0x9 | **相同** |
| 0xa | `BPS_WAIT_FOR_BPS` | `_BpsWaitForBps` | **16** | 同上 | 0xa | **相同** |
| 0xb | `IPE_WAIT_FOR_BPS` | `_IpeWaitForBps` | **16** | 同上 | 0xb | **相同** |
| 0xc | `IPE_WAIT_FOR_IPE` | `_IpeWaitForIpe` | **16** | 同上 | 0xc | **相同** |
| **0xd** | **未识别** | 未知 | **16** | 分支只做 `CreateCallbackData(16)` + 立即 `ReleaseCallbackData`，不碰硬件 | 上游**无此编号** | **固件多一个** |
| 0xe | `MEM_MAP` | `_IpeBpsMemMap` | **20** | `{u64 userArg; u32 memMapRequestNum; MemMapRegionSet[1] {SMMU_ADDR swBufferBaseAddr; u32 swBufferSize}}` | 0xe | **相同** |
| 0xf | `MEM_UNMAP` | `_IpeBpsMemMap` | **20** | 同上 | 0xf | **相同** |

**15/15 的"opcode → 负载字节数"与 DWARF `byte_size` 一对一吻合**（840 / 2360 / 16 / 8 / 1600 / 14328 / 16 / 8 / 16 / 16 / 16 / 16 / 16 / 20 / 20）。

关于 opcode 0xd：镜像里确实存在字符串 `HFI_IPEBPS_CMD_OPCODE_PERF_COUNT`（VA `0x348f9`），但**没有任何指令引用它**（`movw/movt` 反查全表 0 命中），而 0xd 分支本身只登记回调再立即释放、不下发硬件。所以 0xd 是**有名无实**的 16 字节空转命令 —— **归为未识别，不猜**。

### 4.1 注意区分：`CMD_OPCODE_*` 不是 HFI opcode

固件里还有一套 **0..9** 的 `CMD_OPCODE_*` 枚举（`CONFIG_IO / FRAME_PROCESS / ABORT / DESTROY / *_DONE / EVENT_WAIT / EVENT_WAIT_DONE / MAX`），配 `Handle_CMD_OPCODE_*`（BPS 与 IPE 各一套）与 `cmdqueue.c` / `_BpsCommand`(188 B) / `_IpeCommand`(196 B)。这是 **ICP 固件内部发给 BPS/IPE 硬件的命令流**，与 AP↔ICP 的 HFI 完全无关，**不要混进驱动**。

---

## 5. 负载 ABI（上游完全没有、必须自写的一层）

### 5.1 公共外壳

所有 async 负载的最后 8 字节是 `FWUSERARG userArg`（`typedef uint64_t`，固件原样回传，相当于 cookie）；其余是 `cmdData`。

### 5.2 图像描述符（配置 IO 的元语）

    typedef struct _ImageDescriptor {            // 92 B
        ImageInfo      info;                     // 60 B
        BufferLayout   bufferLayout[2];          // 16 B
        BufferLayout   metadataBufferLayout[2];  // 16 B
    } ImageDescriptor;

- `_BpsConfigIoData`：`{ImageDescriptor images[9]; u32 secureMode;}` = 9×92+4 = **832** ✔
- `_IpeConfigIoData`：`{ImageDescriptor images[17]; ImageDimensions stabilizationMargins; u32 secureMode; u32 icaMaxUpscalingQ16; u32 maxBatchSize; u32 muxMode; IpeTopologyType topologyType;}` = 17×92+8+4+4+4+4+4 = **1592** ✔

> 9 张（BPS）与 17 张（IPE）输入/输出槽位，是"一次配置多少路图像"的硬上限。

### 5.3 IQ 参数（真正的 ISP 调校面）

| 结构 | 大小 | 内含模块 |
| --- | --- | --- |
| `_BpsIQSettings` | **1036 B** | pedestal, linearization, pdpc(+480/+680), hdrRecon, hdrMac, hdrBinCorrect_480, gic(+680), abf(+680), lensRollOff(+480), demosaic, bayerGrid, bayerHistogram, colorCorrect, gtm, glut, colorXform, chromaSubsample, hnr, registration1/2Scaler, quarter/sixteenth/sixtyFourthDownscaler, 6×RoundAndClamp, fetchCropEN/fetchCropWindow, minDownscaleRatio, uvGamma, compdecomp … |
| `_IpeIQSettings` | **3676 B** | NPS/PPS 侧（去噪、锐化、色度、缩放、LTMF、MFHDR 等） |

> 这一层意味着：**BPS/IPE 的调校参数不是驱动算出来的，而是用户态填好后作为 SMMU 地址交给固件**（`iqSettingsAddr`）。上游 RFC 对此**零定义**；而 `/lib64/camera/com.qti.tuned.default.bin` + `CFR_*.bin` 正好是这套参数的来源。因此 B0 之后真正的工作量在**参数从哪来**，而不是协议怎么解。

### 5.4 完成 / 结果结构（就是 ACK 的 `cmdPayload[]`）

| 结构 | 大小 | 布局 |
| --- | --- | --- |
| `_BpsConfigIoDone` | 16 | `{FWSTATUS rc; BpsConfigIoResult cmdRes; u64 userArg}` |
| `_BpsFrameProcessDone` | 24 | 同类 |
| `_IpeConfigIoDone` | 152 | `{rc; IpeConfigIoResult(136); userArg}` |
| `_IpeFrameProcessDone` | 152 | 同类 |
| `_BpsDestroyDone` / `_IpeDestroyDone` | 16 | |
| `_IpeBpsMemMapDone` | 12 | `{rc; userArg}` |

---

## 6. 属性表（SET_PROPERTY 的 TLV）

固件 `ICPDomainCmdHandler` 把 `ePacketType == 0x10003` 交给 `0x1e3fc`：取首属性 id，`id-1 > 8` 即拒绝 —— **属性 id 1..9**，跳表 `0x1e40a`。每个属性项 **12 字节**（`id + 2×u32`），处理完指针 +0xC。

| id | 固件分支 | 含义 | 上游 |
| --- | --- | --- | --- |
| 1 | 0x1e420 | 调试配置 + STM trace（`STMTrace_NewPort`） | `HFI_PROP_SYS_DEBUG_CFG = 1` ✔ |
| 2 | 0x1e55a | `Rxed HFI_PROPERTY_SYS_UBWC_CONFIG` → `HFI_UBWC_CONFIG_TYPE{UbwcFetchModeCfg; UbwcWriteModeCfg}`（8 B） | `HFI_PROP_SYS_UBWC_CFG = 2` ✔ |
| 3 | 0x1e61e | **不支持**（进错误路径） | `HFI_PROP_SYS_IMAGE_VER = 3`（只读，合理） |
| 4 | 0x1e61e | **不支持** | `HFI_PROP_SYS_SUPPORTED = 4`（只读，合理） |
| 5 | 0x1e642 | `Received HFI_PROPERTY_SYSTEM_IPEBPS_PC command` | `HFI_PROP_SYS_IPEBPS_PC = 5` ✔ |
| 6 | 0x1e6fe | 写一个标量到内部状态 | 上游无 |
| 7 | 0x1e70c | 同上 | 上游无 |
| 8 | 0x1e71a | `Received HFI_PROPERTY_SYSTEM_HANG_DUMP_MODE command` → `HFI_PROPERTY_CONFIG_HANG_DUMP_MODE{HangDumpMode}` | 上游无 |
| 9 | 0x1e746 | `Rxed HFI_PROPERTY_SYS_UBWC_CONFIG_EX` → `HFI_UBWC_CONFIG_TYPE_EX{HFI_UBWC_CONFIG_TYPE bps; HFI_UBWC_CONFIG_TYPE ipe}`（16 B） | 上游无 |

固件还提供了 `HFI_SYSTEM_SUPPORTED_TYPE`（36 B，只读属性 4 的负载）：

    struct HFI_SYSTEM_SUPPORTED_TYPE {
        u32 apiVersion;           // +0
        u32 deviceTitanVersion;   // +4   （Kona = 7）
        u32 numberICPHW;          // +8
        u32 deviceHWVersion[4];   // +12
        u32 bForce8BppIpe;        // +28
        u32 reservedPlaceHolder;  // +32
    };

> **优先级提示**：属性 2/9 是 UBWC 配置，是让 BPS/IPE 正确读写压缩 buffer 的前提，B2 之前必须先把这两条通路弄清楚。

---

## 7. 与 x1e80100 上游驱动逐项比对

上游对象：`drivers/media/platform/qcom/camss/camss-icp.c`（17,437 B）、`camss-icp.h`、`camss-icp-hfi.h`（13,120 B）、`camss-bps.c/.h`、`camss-ipe.c/.h`，分支 `qcom-laptops-v6.18-rc4-camss-icp-bps-ipe-icp-boots+stats-b4`。

### 7.1 逐项相同（可直接复用）

| 项 | 证据 |
| --- | --- |
| 队列头 956 B / 表头 24 B / 字段顺序 / `type@+188` | 固件运行时步长 `0x3bc` + DWARF；上游注释 `q_hdr=956 q_tbl_hdr=24` |
| 队列 0/1/2 = cmd/msg/dbg | `HFI_QueueGetQHdr(0/1/2)` 三次 |
| 队列表 6 个字段及其含义 | 逐字段同名同序 |
| 域分组 = 位 24/25 | `enum TitanDomain` + `ubfx #0x18,#3` |
| 系统命令 0x10001–0x10006 | 范围比较 + 跳表 |
| 事件通知 0x20008 / 建句柄 0x01010008 / 异步 DIRECT 0x0101000A / INDIRECT 0x0101000E | 立即数直接命中 |
| 系统包结构（init / ping / reset / set_property / init_done / debug / event） | §3.4 逐字段 |
| 建句柄包与 ACK 包（28 B / 32 B） | 逐偏移一致（固件是 packed） |
| **异步 ACK 包**（opcode@8、ud0@12、ud1@20、error@28、payload@32） | 逐偏移一致 |
| opcode 1..0xC、0xE、0xF 的编号与语义 | 字符串 + 跳表 + 负载尺寸 |
| 属性 1/2/5 | 分支行为与日志串 |
| 句柄类型取值域 | 固件 `eHandleType ∈ 1..6`；1/5/6→BPS 流、2/3/4→IPE 流；上游 BPS 传 `HFI_DEV_TYPE_BPS=1`、IPE 传 `HFI_DEV_TYPE_IPE=3`，落在正确分支 |

### 7.2 部分相同 / 可用但需改

| 项 | 差异 | 影响 |
| --- | --- | --- |
| **async 命令包** | 固件 `nUserArg1@12`、`nNumHandles@28`、`hHandles@32`、payload@`32+4n`；上游插了 `fw_handle@12`，其余后移 4，payload@40（n=1） | **必须按固件改**，否则 `num_handles` 会被当成 `nUserArg2` 的高半字，长度校验直接失败 |
| 队列索引单位 | 固件以 4 字节字为单位（`q_size` 也是字）；上游结构体没写单位 | 写引擎时必须乘/除 4，否则绕回判据全错 |
| `q_size` 建议值 | 固件单包上限 6019 B，上游把数据区设成 4 KiB | 上游值会拒收大包；建议数据区 ≥ 8 KiB |
| 句柄类型宏 | 上游另有 `HFI_HANDLE_TYPE_BPS=1 / HFI_HANDLE_TYPE_IPE=2`，若用 2 会被固件当成 `IPE_RT` | 只用 `HFI_DEV_TYPE_*` 就安全；两套宏并存是隐患 |
| `HFI_CMD_SYS_GET_PROPERTY` | 固件不实现 | 不要在初始化序列里用 |
| `qtbl_version` | 固件不校验（上游写 `0xFFFFFFFF`） | 无害，照写即可 |

### 7.3 完全不同 / 上游完全没有

| 项 | 固件 | 上游 |
| --- | --- | --- |
| **固件封装** | 单一 ELF（`CAMERA_ICP.elf`，入口 0x0，REX 镜像） | `.mdt` 分片 + `qcom_mdt_load()` + PAS/SCM 认证 |
| **HFI 引擎实现** | 有实现：`hfiqueue.c`（队列读写）+ `ipebpsdomain.c`（命令分发，`ProcessAsyncCommand` 10.5 KB）；`hfihal.c` 在本镜像里只贡献类型头 | **文件不存在**：`camss-icp-hfi.h` 声明、无 `camss-icp-hfi.c`；`camss-bps.c` 还 include 了不存在的 `camss-icp-hfi-frame.h` |
| **payload ABI** | 全套结构（§5） | **零定义** |
| **属性 6/7/8/9** | 实现 | 未定义 |
| **调试域命令 0x03010008/9** | 实现 | 未定义 |
| **寄存器 / GP 映射** | DSP 视角：QTBL 指针 `0xe0010058`、上行中断 `0xe0010014`（写 1）、接口清除 `0xe0010060`（写位掩码）、`0xe0010074` | `HFI_REG_*` 在 CSR+`0x24..0x68`，中断走独立 `cirq_base`（`CIRQ_HOST2ICPINT` / `CIRQ_OB_STATUS/CLEAR/MASK`） | **无法对齐**，见 §10 |

### 7.4 无法比对（信息不足，不作结论）

1. **CSR/GP 寄存器偏移**。固件里看到的是 **A5 自己的地址视图**（`0xe0000000` 段：`0xe0010000` 接口块、`0xe0040000` CDM、`0xe0070000`、`0xe0090000`）。这些**不是** AP 侧物理地址，两者之间隔着 CPAS/NoC 的窗口映射。上游 `HFI_REG_*` 是作者从 x1e80100 侧推的，我们**没有 kona 侧的对应证据**（`kona-camera.dtsi` 只给 `cam_a5@ac00000` 一个 base，看不出 GP 偏移）。
2. **固件如何向 host 报警 / host 如何唤醒固件**。固件侧写 `0xe0010014 = 1` 抬中断、写 `0xe0010060 = 0x10/0x20/0x120` 清状态；AP 侧具体是哪个 offset，需要 Titan 寄存器手册或一次实测。
3. **SCM 认证是否接受这份 ELF**（scope §7 风险 1 仍然成立，B0 无法证伪）。
4. **`HFI_MSG_SYS_IDLE` / `PROPERTY_INFO` / `ASYNC_*_ACK` 的具体取值**：只按上游常量推定，固件侧未逐一取证。
5. **opcode 0xD 的语义**。

---

## 8. 可复用比例判断

### 8.1 分项打分

| 层 | 可复用度 | 理由 |
| --- | --- | --- |
| 队列 / 队列表结构 | **高（≈100%）** | 956 / 24 与字段逐字节相同，连 padding 模式都一样 |
| 包类型编号 + 域分组 | **高（≈100%）** | 三处独立取证全部命中 |
| 系统命令 / 消息结构 | **高（≈95%）** | 仅 `GET_PROPERTY` 固件不支持 |
| 建句柄 / ACK / 事件包 | **高（≈100%）** | 逐偏移一致 |
| 异步命令包 | **低（需重写）** | 4 字节位移 + payload 放置规则不同 |
| 异步 ACK 包 | **高** | 偏移一致 |
| payload ABI | **0（上游空白）** | 只能从固件 DWARF 反向重建（本次已给出） |
| HFI 引擎代码 | **0** | 上游没有实现 |
| 寄存器编程 / 固件加载 | **低** | 固件格式不同 + 无寄存器证据 |
| 硬件块模型（BPS/IPE 电源、时钟、PD、ICC） | **中** | `camss-bps.c` / `camss-ipe.c` 的 `power_on` / `set_clock` 骨架可借，但 kona 的时钟名 / PD 名要重写 |

### 8.2 总判断

> **能借骨架，不能借肉。**
>
> - 协议**定义**这一层几乎可以整段搬（约 **100–120 行头文件**：队列结构 + 包类型常量 + 系统包结构 + opcode 常量）。
> - 协议**引擎**（队列读写、门铃、消息分发、payload 编解码、超时/中止）必须自写，量级 **1,500–2,500 行**。
> - 上游 RFC 从"可运行参考"降级为"**常量字典 + 硬件块骨架**"。原 scope 里"存在可借的骨架（HFI 层 + BPS/IPE m2m）"这句话，前半部分要修正：**HFI 层只是头文件，不是骨架**。

---

## 9. 下一步建议

### 9.1 建议进入 B1（成本低、能立刻证伪）

B1 原定义是"写解析器/断言工具，用固件里的结构尺寸打印/符号交叉验证"。本次已经**完成了最关键的交叉验证**（15/15 opcode ↔ `CheckCommandSize` ↔ DWARF `byte_size`），所以 B1 应该改成更有价值的形式：

**B1'（建议，1–2 天，纯离线）**

1. 把 §3/§4/§5 的常量与结构落成 `tools/titan-hfi/`（C 头 + Python 编解码器）；
2. 写 `static_assert` / 单测，把 956 / 24 / 28 / 32 / 40 / 840 / 2360 / 1600 / 14328 … 全部钉死；
3. 用固件里 `ProcessAsyncCommand` 的跳表**自动**重抽一遍表（把本次的手工反汇编固化成脚本，防止以后记错）；
4. 用 `_BpsConfigIo` / `_IpeConfigIo` 反推一份"最小可跑 CONFIG_IO 负载"模板（全零 + 一个 9/17 槽的 `ImageDescriptor`）。

**这一阶段仍然零设备风险，且直接决定 B2 要写多少代码。**

### 9.2 B2 的判据要改

原判据"起得来 = 路通；连续两次失败 = 归档"。基于 B0 的新信息，建议拆成可分级的三步，每步都能单独失败：

| 级别 | 目标 | 需要的未知量 |
| --- | --- | --- |
| **B2a** | 把 `CAMERA_ICP.elf` 原样搬进 `cam_a5@ac00000` 的 reserved memory，放 core，看 **A5 是否执行到写 GP 寄存器** | 固件加载方式（单 ELF vs PAS+SCM）、入口约定 |
| **B2b** | 拿到 AP 侧真正的 GP / QTBL / 中断 offset（用 §10 的候选做实测二分） | §7.4 第 1、2 条 |
| **B2c** | HFI 握手：host 填队列表 → 发 `SYS_INIT` → 收 `INIT_DONE` + `HFI_SYSTEM_SUPPORTED_TYPE`（`deviceTitanVersion` 应为 **7**） | B2b |

**只要 B2b 拿到寄存器映射，B2c 大概率一次就通** —— 因为协议本身已经没有未知量了。反之，如果 B2b 拿不到，后面全是空谈。

### 9.3 不建议做的事

- 不要为了"少写代码"去迁 x1e80100 的 `camss-icp.c` 主体：它的 HFI 引擎是空的，固件加载方式不适用，寄存器映射未证。
- 不要在 B1 之前上机（没有 payload 定义，握手成功也没法下发任何有用命令）。
- 不要按上游 `hfi_cmd_async` 写命令包。

---

## 10. 未解问题与风险（按优先级）

1. **【阻塞 B2】AP 侧寄存器映射**。这是唯一真正卡住整条线的东西。候选取证途径（按成本排序）：
   a. 原厂 `kona-camera.dtsi` 里 `cam_a5@ac00000` 的 reg 窗口大小 / reg-names → 能看出 CSR 与 CIRQ 是否分窗（`local/sensors-20261107` 的 vendor 分区里有现成副本）；
   b. 下游 `camera-kernel` 驱动里 `cam_a5` 平台的 `icp_hw` 表（**只读**，cherry 出 offset 即可，比反汇编固件便宜）；
   c. B2a 实测二分。
2. **【阻塞 B3】IQ 参数来源**。`iqSettingsAddr` 指向的 `BpsIQSettings` / `IpeIQSettings` 是用户态生成物；`com.qti.tuned.default.bin` 与 `CFR_*.bin` 的格式未解。若不能解，即使 B2 通过也只能跑"全默认/全零"参数。
3. **固件加载与认证**：单 ELF 能否被 `qcom_scm` / PAS 接受，未知。
4. **32 位 SMMU 地址空间**：整个 HFI 只认 32 位地址，kona 的 CAMSS SMMU 是否给 ICP 准备了 < 4 GiB 的 IOVA 窗口，需要确认。
5. **6019 B 接收上限的出处**：这个数字不是 2 的幂，疑为某个推导值；实际影响有限（BPS `FRAME_PROCESS` 2360 B、ACK 152 B 都在内），仅记在这里。
6. **opcode 0xD 语义未定**。

---

## 11. 本轮的失误与方法保留

1. **第一次常量扫描方法错了**：直接扫 `.text` 的 4 字节小端字找 `0x01010008`，全部 0 命中，差点得出"固件编号与上游不同"的错误结论。根因是 ARM 用 `movw`+`movt` 拼常量，完整值从不以连续 4 字节出现。改成解析指令对之后立刻命中。**教训：ARM 上找常量必须走反汇编，不能搜字节。**
2. **第一次反汇编用 GNU `objdump`**，报 `can't disassemble for architecture UNKNOWN`（这台机器的 binutils 是 x86-only 构建），且即使能跑也会因本镜像是 ARM/Thumb 混合而错译。改用 `llvm-objdump --triple=thumbv7a-none-eabi`，并按"符号值奇偶 = Thumb/ARM"区分。第一版把 Thumb 当 ARM 反汇编出来的东西完全是垃圾（`<unknown>` 连片），**当时就应该按符号奇偶停下来**。
3. **`dwarf2c.py` 第一版解析成员偏移取错**：`DW_AT_data_member_location` 的形态是 `2 byte block: 23 3c (DW_OP_plus_uconst: 60)`，第一版正则在字符串里抓第一个数字，抓到的是长度字节 `2`，于是所有成员偏移都变成 0/2。修法是优先匹配 `DW_OP_plus_uconst: N`。**这类错会安静地毁掉整张表，必须拿已知尺寸（956）反查一遍才发现。**
4. **`dwarf2c.py` 第二版把 typedef 与同名 struct 混成一条**：`HFI_QUEUE_HEADER_TYPE` 同时是 typedef 名与 struct tag，第一版 `--show` 输出 `typedef HFI_QUEUE_HEADER_TYPE HFI_QUEUE_HEADER_TYPE;`。改成同名冲突时优先取 struct/union/enum。
5. **`refs.py` 第一版有 bug**：在每条指令后清空了 `movw` 暂存，导致 `movw/movt` 对永远拼不起来，字符串反查全空。删除该清空逻辑后恢复正常。
6. **没有做的事**：没有对 `CAMERA_ICP.elf` 做任何写操作；没有 `git add` 任何固件/中间产物；没有连设备。中间产物全部在 `local/titan-b0/`。

---

## 12. 复现步骤

    # 1. 资产校验（必须与 scope §4 一致）
    sha256sum local/camera-r4-20261110/analysis/CAMERA_ICP.elf
    # cb98cec20d1073d4b2872342b3db2185b57422c73e9e7f327887f1b039a1ceb9

    # 2. 建工作目录并复制（local/ 已 gitignore）
    mkdir -p local/titan-b0 && cp local/camera-r4-20261110/analysis/CAMERA_ICP.elf local/titan-b0/

    # 3. DWARF 转 C
    readelf --debug-dump=info local/titan-b0/CAMERA_ICP.elf > local/titan-b0/debug_info.txt
    python3 local/titan-b0/dwarf2c.py local/titan-b0/debug_info.txt --show \
        HFI_QUEUE_HEADER_TYPE HFI_QUEUE_TABLE_HEADER_TYPE HFI_CMD_IPEBPS_ASYNC_COMMAND_TYPE \
        HFI_MSG_IPEBPS_ASYNC_COMMAND_ACK_TYPE
    python3 local/titan-b0/dwarf2c.py local/titan-b0/debug_info.txt --list --grep 'hfi|ipe|bps'

    # 4. 反汇编（必须用 llvm-objdump + Thumb）
    llvm-objdump -d --triple=thumbv7a-none-eabi --no-show-raw-insn \
        local/titan-b0/CAMERA_ICP.elf > local/titan-b0/disasm-thumb.txt

    # 5. 只从可加载段取字符串（否则会把 .debug_str 里的类型名当成代码字符串）
    dd if=local/titan-b0/CAMERA_ICP.elf of=local/titan-b0/loadable.bin bs=1 skip=1048576 count=226928
    dd if=local/titan-b0/CAMERA_ICP.elf of=local/titan-b0/loadable.bin bs=1 seek=226928 skip=2097152 count=36256 conv=notrunc
    strings -a -n 6 local/titan-b0/loadable.bin | grep -E 'HFI|IPEBPS|CICP'

    # 6. 关键函数地址
    #   IpeBpsDomainCmdHandler      0x1f828  （建句柄 / async direct / indirect 三选一）
    #   ProcessAsyncCommand         0x1fc08  （opcode 跳表 0x1fc4c，负载尺寸）
    #   ICPDomainCmdHandler         0x1dcb8  （系统命令跳表 0x1dcf0，属性跳表 0x1e40a）
    #   DbgDomainCmdHandler         0x1da48
    #   ICPHostInterface_driverTask 0x1edfc
    #   HFI_queueTryWrite           0x1dacc
    #   HFI_QueueGetQHdr            0x1dbd6
    #   CheckCommandSize            0x22560

> 注：`loadable.bin` 的两段拼接只为了让 `strings` 不扫到 `.debug_*`；不影响地址换算（`refs.py` 里 `SECS` 表已写死两段）。

---

## 13. 上游参考的准确定位

| 文件 | 大小 | 对我们有用的部分 |
| --- | --- | --- |
| `camss-icp-hfi.h` | 13,120 B | **全部线格式常量 + `hfi_q_hdr` / `hfi_q_tbl_hdr` / `hfi_pkt_hdr` / 系统包结构**；`HFI_REG_*` 一组存疑；`hfi_cmd_async` **有错** |
| `camss-icp.c` | 17,437 B | `icp_boot()` 的 CSR 序列、IRQ 两段式、`icp_hfi_latch_regs()` 的 GP 赋值顺序（可作 B2 的检查清单，但 offset 要换） |
| `camss-icp.h` | 1,045 B | `icp_context` / `icp_frame_request` 抽象 |
| `camss-bps.c/.h` | 17,081 / 1,397 B | 电源/时钟/PD/ICC 骨架、V4L2 m2m 框架；`icp_hfi_create_handle(hfi, HFI_DEV_TYPE_BPS, …)` 的调用点 |
| `camss-ipe.c/.h` | 13,957 / 1,377 B | 同上，`icp_ctx_create(ipe->icp, HFI_DEV_TYPE_IPE, …)` |
| **缺失** | — | `camss-icp-hfi.c`（引擎）、`camss-icp-hfi-frame.h`（payload 定义）—— **这两个文件上游根本不存在** |

获取方式（可直连，本机实测 HTTP 200）：

    B='qcom-laptops-v6.18-rc4-camss-icp-bps-ipe-icp-boots%2Bstats-b4'
    curl -sS -o camss-icp-hfi.h \
      "https://git.kernel.org/pub/scm/linux/kernel/git/bod/linux.git/plain/drivers/media/platform/qcom/camss/camss-icp-hfi.h?h=$B"

---

## 14. 相关文档

- [范围与判定标准](camera-titan-poc-scope-20261111.md)
- [续作 prompt](camera-titan-poc-resume-prompt-20261111.md)
- [相机续作 prompt](camera-resume-prompt-20261111.md)
- [r4 相机首次受光](r4-camera-first-light-20261111.md)
