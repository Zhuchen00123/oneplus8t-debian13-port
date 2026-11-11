# Titan ISP PoC B1 —— HFI 线格式固化成契约（2026-11-11）

> 状态：**B1 完成**（纯离线，未接触设备、未上机、未写任何分区）
> 分支：`codex/camera-titan-poc-20261111` ／ worktree `.agents/worktrees/camera-titan-poc`
> 上游阶段：[B0 结果](camera-titan-hfi-b0-20261111.md) ／ [范围与判定标准](camera-titan-poc-scope-20261111.md)
> 固件：`local/camera-r4-20261110/analysis/CAMERA_ICP.elf`，3,922,232 B，
> SHA-256 `cb98cec20d1073d4b2872342b3db2185b57422c73e9e7f327887f1b039a1ceb9`
> （脚本每次运行都校验这个哈希，不一致即报警）

## 0. 结论先行

一句话：**B0 手工读出来的表，现在是一条能自己重算的流水线。**

1. **契约可编译、可自检**：`tools/titan-hfi/titan_hfi.h` 用 161 处
   `static_assert` 把 24 / 956 / 8 / 16 / 28 / 32 / 40 / 36 / 12 / 20 / 840 / 1600 /
   14328(表值) / 92 / 1036 / 3676 / 152 / 16 / 8 / 20 B 全部钉死；
   gcc 14 与 clang 19 在 C11 下、clang++ 19 在 C++17 下**零警告通过**，
   selftest 52 项运行时检查全 ok。
2. **opcode 表不再依赖记忆**：`extract_opcodes.py` 从反汇编 + ELF 里自己解
   `ProcessAsyncCommand` 的 TBH 跳表、自走 15 个分支、从 `CheckCommandSize` 调用点
   读立即数，**15/15 与 B0 逐条一致**，且与头文件逐条比对通过（退出码 0）。
3. **最小可跑报文已经能生成**：`_BpsConfigIo` 840 B → 876 B DIRECT 包；
   `_IpeConfigIo` 1600 B → 1636 B DIRECT 包；两者都过"固件长度规则"模拟。
4. **本轮新增 4 条 B0 没有的事实**（详见 §7）：哪些 opcode 强制"恰好 1 个 handle"、
   `SET_PROPERTY` 的**线上**长度、"done" 结构里 4 B 对齐空洞、以及 abort 负载里的
   指针在 ARM32 上就是 32 位 SMMU 地址。

**没有解决的仍然是 B0 那几条**（AP 侧寄存器映射、固件加载方式、IQ 参数来源、
opcode 0xD 语义），本轮一条都没有推进，也不该在本轮推进。

---

## 1. 交付物

| 文件 | 行数 | SHA-256 | 作用 |
| --- | --- | --- | --- |
| `tools/titan-hfi/titan_hfi.h` | 926 | `904bd8c288c98edcd9a7ccbf8eca64f6cfc53f3543f3ffa27686cd48d74a2dba` | 权威契约 |
| `tools/titan-hfi/titan_hfi_selftest.c` | 216 | `e884582ff9fafccf8e1bc802247fb68d343c8dd58129cb6ce3417891aed1baab` | 尺寸/偏移打印与交叉核对 |
| `tools/titan-hfi/titan_hfi.py` | 617 | `f0b3cb2ef477ebfcc380151ed1d3578b2d1210d834826cfcb1d178a2ef66ca60` | Python 编解码器 |
| `tools/titan-hfi/extract_opcodes.py` | 390 | `86ed1919707d0ccb76ddfc02a67ee86ec589323271491f1b56d41ac1f13924de` | 从固件重抽 opcode 表 |
| `tools/titan-hfi/make_config_io.py` | 226 | `f6f7b2faf636edb154be13654d5f496b10b3dbaf0a4bbe8eff1421a39d7904ec` | 最小 CONFIG_IO 模板 |
| `tools/titan-hfi/test_titan_hfi.py` | 427 | `7b3735837df9709c2ca195516367179a25faa515598d12d3146a0777d90f17cb` | 37 项单测 |
| `tools/titan-hfi/README.md` | 47 | `a3b5795169f21b2cf0ffe3fab0333ff12e4164c00a597227524e7fca0d2ffce3` | 用法与三条红线 |

中间产物（**已 gitignore，不入库**）在 `local/titan-b1/`：
`evidence-c.txt`（编译与 selftest 全量日志）、`selftest.txt`、
`template/{bps,ipe}_config_io_{payload,packet}.bin`。

---

## 2. C 契约：`titan_hfi.h`

### 2.1 组织方式

- **第 0 节** 基类型：`FWUSERARG`(u64)、`SMMU_ADDR`(u32)、`FWSTATUS`(u32)，
  直接照抄固件 typedef（固件是 ARM32，所有跨界指针都是 32 位）。
- **第 1 节** 队列层：`titan_hfi_q_tbl_hdr`(24 B) 与 `titan_hfi_q_hdr`(956 B)，
  14 个字段夹在 15 段 `padding[15]` 之间，逐字段注释了偏移。
- **第 2 节** 包类型：系统命令/消息、IPE_BPS 三件套、调试域两条，外加
  `TITAN_HFI_DOMAIN(type) = (type >> 24) & 7`。
- **第 3 节** 包结构：每个都标了实测字节数与关键字段偏移。
- **第 4 节** **负载放置规则**：`TITAN_HFI_ASYNC_PAYLOAD_OFF(n) = 32 + 4n`、
  `titan_hfi_async_size_ok()` —— 就是固件 `CheckCommandSize` 的 C 版。
- **第 5 节** payload ABI：`ImageDescriptor`(92) / `_BpsConfigIo`(840) /
  `_IpeConfigIo`(1600) / IQ 块 / 完成结构。
- **第 6 节** 属性表 1..9 与 `HFI_SYSTEM_SUPPORTED_TYPE`(36)。
- **第 7 节** `TITAN_HFI_OPCODE_TABLE(X)` —— **唯一的一份 opcode→尺寸表**，
  C 端用宏展开、Python 与 `extract_opcodes.py` 都读同一张表的文本形式。
- **第 8 节** 161 处断言。

对齐策略：**统一 `packed` + 显式填补**。理由见 §7.3：固件里包结构是 packed
（`u64` 真的落在 `+12`），而 done 系列是自然对齐（`u64` 落在 `+8/+16`）。
只在结构里显式写出 `uint32_t reserved` 或 `uint32_t padN[15]`，比依赖编译器默认
对齐安全。

### 2.2 钉死的尺寸

| 类别 | 尺寸（B） |
| --- | --- |
| 队列 | 表头 **24**、队列头 **956**（3 条共 2892 ≤ 4096 映射窗口） |
| 包 | 头 **8**、SYS_INIT **8**、PING/RESET **16**、属性项 **12**、SET_PROPERTY 声明 **16**、INIT_DONE 声明 **20**、PC_PREP_DONE **12**、EVENT **28**、CREATE_HANDLE **28**、CREATE_HANDLE_ACK **32**、**ASYNC 40**、ASYNC_ACK **36** |
| async 关键偏移 | `nUserArg1@12`、`nUserArg2@20`、`nNumHandles@28`、`hHandles@32`、`payload@36`（n=1） |
| payload | `ImageDescriptor@92`、`ImageInfo@60`、`BufferLayout@8`、`ImageDimensions@8`、`BpsConfigIo@840`、`IpeConfigIo@1600`、`BpsIQ@1036`、`IpeIQ@3676`、`MemMap@20`、`Abort@16`、`Destroy@8`、`Wait@16` |
| 完成 | `BpsConfigIoDone@16`、`BpsFrameProcessDone@24`、`IpeConfigIoDone@152`、`IpeFrameProcessDone@152`、`MemMapDone@12`、`*DestroyDone/AbortDone/WaitDone@16` |
| 上限 | 单包 **6019**、INDIRECT 最小 **40**、SMMU 地址 **32 位** |

### 2.3 断言通过记录

四个编译器、同一头文件（全部 `-Wall -Wextra -Werror -pedantic`）：

      gcc -std=c11        OK
      clang -std=c11      OK
      clang++ -std=c++17  OK
      (g++ 本机未安装，仅 clang++ 覆盖 C++ 模式)

> 断言数是 `grep -c TITAN_HFI_ASSERT` = **161**（含 `_SIZE`/`_OFF` 两种派生宏）。
> C++ 模式走 `static_assert`，C 模式走 `_Static_assert`，由 `TITAN_HFI_SA` 分派；
> 这样 `extern "C"` 的头在 C++ 里也不触发 `-Wc11-extensions`（第一次就是这样失败的，见 §9）。

selftest（`local/titan-b1/selftest.txt`，**52 项运行时检查，0 failure**）节选：

      queue layer
        struct titan_hfi_q_tbl_hdr                   24 B  expected     24  ok
        struct titan_hfi_q_hdr                      956 B  expected    956  ok
        qhdr.status                              +60             expected +60     ok
        qhdr.type                                +188            expected +188    ok
        qhdr.read_idx                            +828            expected +828    ok
        qhdr.write_idx                           +892            expected +892    ok
        queue table for 3 queues: 2892 B (limit 4096 B)

      packets
        struct titan_hfi_cmd_async                   40 B  expected     40  ok
        async.nUserArg1                          +12             expected +12     ok
        async.nNumHandles                        +28             expected +28     ok
        async.hHandles                           +32             expected +32     ok
        async.payload                            +36             expected +36     ok
        struct titan_hfi_msg_async_ack               36 B  expected     36  ok
        ack.cmdPayload                           +32             expected +32     ok

      async payload placement (firmware CheckCommandSize)
        n=1 -> payload offset 36; direct packet for an 8 B payload = 44 B
        n=2 -> payload offset 40; direct packet for an 8 B payload = 48 B
        n=3 -> payload offset 44; direct packet for an 8 B payload = 52 B

      opcode -> payload size, cross-checked against struct sizes
        0x01 BPS_CONFIG_IO        table   840 B  sizeof(titan_hfi_bps_config_io)   840 B  ok
        0x05 IPE_CONFIG_IO        table  1600 B  sizeof(titan_hfi_ipe_config_io)  1600 B  ok
        0x0e MEM_MAP              table    20 B  sizeof(titan_hfi_ipe_bps_mem_map)    20 B  ok
        ...

      PASS (0 failures)

opcode 0x02 / 0x06 / 0x0D 的负载在固件里是**不透明**的（没有可复用的小结构），
所以 selftest 对它们只核对表值，不冒充 sizeof(struct) 相等——这一点在输出里
显式写成 `(no C struct: opaque)`，没有拿空壳结构去"凑"一个 2360/14328。

### 2.4 断言当场抓到的三处错

第一次编译（done 结构已按 DWARF 填了 `rc / cmdRes / userArg`）**直接编译失败 3 处**：

      error: static assertion failed: "wrong size: titan_hfi_bps_frame_process_done"
      error: static assertion failed: "wrong offset: titan_hfi_bps_frame_process_done.userArg"
      error: static assertion failed: "wrong size: titan_hfi_ipe_config_io_done"
      ... (3 个结构、共 6 条)

根因：`{ u32 rc; <8 B 或 136 B 结果>; u64 userArg; }` 在**自然对齐**下
`userArg` 会被推到 `+16` / `+144`，而我把整个结构标成 `packed`，于是它落在
`+12` / `+140`，尺寸少了 4。修法是保留 `packed`（保证其它地方确定）并显式
补 `uint32_t reserved`。

**这 3 处错误正是"照 DWARF 抄字段、但不抄对齐"会犯的错**，靠断言在第一次编译就
暴露了，而不是留到 B2 上机时表现为"完成包解析不对"。

---

## 3. Python 编解码器：`titan_hfi.py`

### 3.1 API

| 函数 | 说明 |
| --- | --- |
| `encode_async(opcode, handles, payload, direct, user_arg1=0, user_arg2=0, indirect_addr=None)` | 生成 async 命令包；DIRECT 内联负载，INDIRECT 在 `32+4n` 处写 32 位 SMMU 地址；长度/句柄数/上限不合格直接抛 ValueError |
| `decode(pkt)` | 双向解析：async / async_ack / create_handle(_ack) / event / init_done / set_property / debug / system_echo；未知类型返回 `kind="unknown"` 而不抛（与固件"记录并丢弃"一致） |
| `queue_header_bytes(queues, ...)` | 序列化整张队列表（24 + n×956），字段按固件偏移写入 |
| `decode_queue_table(blob)` | 反向解析，用于 B2 现场比对 |
| `check_command_size(nsize, payload_bytes, num_handles, direct)` | 固件 `CheckCommandSize` 的镜像 |
| `firmware_accepts(pkt)` | 把字节流喂进上面两条规则，回答"固件会不会收" |
| `async_payload_off(n)` / `async_direct_min_size(...)` | 几何计算 |
| `encode_create_handle` / `encode_sys_command` / `encode_set_property` | 其余包 |

### 3.2 单测：37 项全通过

      $ python3 test_titan_hfi.py
      .............................................................
      ----------------------------------------------------------------------
      Ran 37 tests in 0.012s

      OK

覆盖：头文件↔模块常量逐项相等（opcode 表、几何常量、关键 static_assert 值）、
async payload 偏移 n=1/2/3、DIRECT/INDIRECT 两态、长度校验边界与拒绝用例、
"句柄数必须为 1"的 10 个 opcode、队列表往返、CONFIG_IO 模板偏移、其余包长度。

### 3.3 验收要求的"async payload 偏移 32+4n"三组用例

三组用例都**同时**断言"字节落在哪"与"decode 解出来的偏移是多少"，避免自证：

    async_payload_off(1) = 36   pkt[0x24:0x24+20] == payload   nSize = 20+32+4*1 = 56
    async_payload_off(2) = 40   pkt[0x28:0x28+20] == payload   nSize = 20+32+4*2 = 60
    async_payload_off(3) = 44   pkt[0x2C:0x2C+20] == payload   nSize = 20+32+4*3 = 64

外加一条反向断言：`hHandles[0]` 在 `+32` 处，且 `+32` 的读数**不等于**句柄数
（上游布局会把 `nNumHandles` 放在 `+32`，这条断言专门防"照着上游把计数放 32、
却把句柄也读成 32"的混淆）。

### 3.4 复刻的两条固件规则（反汇编原文）

`CheckCommandSize` @ `0x22560`，DIRECT 分支（`r2 != 0`）：

      ldr  r3, [r0, #0x1c]        ; r3 = pkt->nNumHandles
      ldr  r2, [r0]               ; r2 = pkt->nSize
      add.w r3, r0, r3, lsl #2    ; pkt + 4*n
      add  r1, r3                 ; expected + pkt + 4*n
      adds r1, #0x20              ; + 32
      subs r0, r1, r0             ; 期望包长 = expected + 32 + 4*n
      cmp  r0, r2
      bls  ok

INDIRECT 分支（`r2 == 0`）：

      ldr  r0, [r0]               ; pkt->nSize
      cmp  r0, #0x27              ; 39
      bhi  ok                     ; nSize >= 40 即可，负载不在包里

Python 侧 `check_command_size()` 逐字节复刻，并额外加上接收循环的两条限制
（`nSize <= 6019`、`nSize % 4 == 0`、非 0），后两条来自
`ICPHostInterface_driverTask`（`movw r5,#0x1783` 比较）。

---

## 4. opcode 表自动重抽（与 B0 逐条一致）

    $ python3 extract_opcodes.py --disasm .../disasm-thumb.txt --elf .../CAMERA_ICP.elf
    firmware  .../CAMERA_ICP.elf
    sha256    cb98cec20d1073d4b2872342b3db2185b57422c73e9e7f327887f1b039a1ceb9
    function  ProcessAsyncCommand @ 0x1fc08 .. 0x22560
    tbh       @ 0x1fc4c, jump table @ 0x1fc50 (15 entries, halfwords relative to 0x1fc50)
    nNumHandles read from pkt+0x1c into r0

    opcode  target     payload  cache  handles  header name                  bytes  verdict
    0x01    0x01fc6e      840    840     == 1  BPS_CONFIG_IO                 840  match
    0x02    0x0201bc     2360   2360     == 1  BPS_FRAME_PROCESS            2360  match
    0x03    0x02027e       16     16        -  BPS_ABORT                      16  match
    0x04    0x0203ee        8      8     == 1  BPS_DESTROY                     8  match
    0x05    0x01fdca     1600   1600     == 1  IPE_CONFIG_IO                1600  match
    0x06    0x0204c8    14328  14328     == 1  IPE_FRAME_PROCESS           14328  match
    0x07    0x02058e       16     16        -  IPE_ABORT                      16  match
    0x08    0x0206fe        8      8     == 1  IPE_DESTROY                     8  match
    0x09    0x02075a       16     16     == 1  BPS_WAIT_FOR_IPE               16  match
    0x0a    0x020808       16     16     == 1  BPS_WAIT_FOR_BPS               16  match
    0x0b    0x0208b6       16     16     == 1  IPE_WAIT_FOR_BPS               16  match
    0x0c    0x01ff2a       16     16     == 1  IPE_WAIT_FOR_IPE               16  match
    0x0d    0x020968       16     16        -  UNKNOWN_0D                     16  match
    0x0e    0x0209c6       20      -        -  MEM_MAP                        20  match
    0x0f    0x01ffda       20      -        -  MEM_UNMAP                      20  match

    all 15 opcodes match titan_hfi.h
    [exit 0]

方法（全部可复现，不依赖任何手工抄写）：

1. 从反汇编里找 `ProcessAsyncCommand` 符号，函数范围 = 到下一个符号（`0x22560`，
   正好是 `CheckCommandSize`）为止；
2. 找函数里的 `tbh [pc, r2, lsl #1]`，**跳表基址 = tbh 地址 + 4**（`0x1fc4c + 4 = 0x1fc50`），
   15 个半字偏移；
3. 逐个目标自走分支：跟踪 `r1`（`movs` / `mov.w` / `movw`+`movt`），
   在 `bl CheckCommandSize` 处取值；
4. **第二路独立证据**：同一分支里 `vSYS_MaintainCache` 调用前会把同一尺寸写进
   `[sp, #72]`，脚本把它也读出来（cache 列）。13 个 opcode 两路一致；
   `0x0e` / `0x0f` 分支里没有这次 cache 维护调用，所以该列为 `-` ——
   **不编造，如实留空**；
5. 与 `titan_hfi.h` 的 `TITAN_HFI_OPCODE_TABLE` 逐条比对，不一致则退出码 1。

脚本还会在开头校验固件 SHA-256，并用自带的极简 ELF32 读取器（只认 PT_LOAD）
直接从文件读跳表字节，**不依赖 objdump 的数据行格式**。

---

## 5. 最小 CONFIG_IO 模板

### 5.1 尺寸

    $ python3 make_config_io.py
    minimal CONFIG_IO template
      user_arg           0x1122334455667788
      handle             1
      bps payload         840 B  (_BpsConfigIo, opcode 0x01)
      ipe payload        1600 B  (_IpeConfigIo, opcode 0x05)
      bps async packet    876 B  direct (limit 6019 B)
      ipe async packet   1636 B  direct (limit 6019 B)
      bps_config_io    non-zero bytes 17 / 840
      ipe_config_io    non-zero bytes 19 / 1600
      bps_config_io    firmware check: accept
      bps_config_io    decoded payload offset 36, nNumHandles 1, opcode 0x01
      ipe_config_io    firmware check: accept
      ipe_config_io    decoded payload offset 36, nNumHandles 1, opcode 0x05

两个 CONFIG_IO 都能 DIRECT（876 / 1636 B 都远小于 6019 B）。
**只有 `IPE_FRAME_PROCESS`(14328 B) 不能 DIRECT**，必须 INDIRECT；BPS 的
`FRAME_PROCESS`(2360 B) 可以 DIRECT。

### 5.2 负载十六进制（前 96 B；其余全零）

`_BpsConfigIo`（840 B）：槽 0 一个 4000×3000 / BAYER_10 / UBWC v4 / lossless 描述符。

    00000000  06 00 00 00 a0 0f 00 00 b8 0b 00 00 00 00 00 00  ................
    00000010  00 00 00 00 00 00 00 00 00 00 00 00 02 00 00 00  ................
    00000020  00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00  ................
    00000030  00 00 00 00 00 00 00 00 00 00 00 00 00 10 00 00  ................
    00000040  b8 0b 00 00 00 00 00 00 00 00 00 00 00 00 00 00  ................

字段解释：`+0` format=6(BAYER_10)、`+4` width=4000(0xFA0)、`+8` height=3000(0xBB8)、
`+28` ubwcVersion=2(v4)、`+32` ubwcLossyMode=0(lossless)、`+60` bufferStride=4096(0x1000)、
`+64` bufferHeight=3000；`+828` secureMode=0；`+832` userArg。

`_IpeConfigIo`（1600 B）：同槽 0 描述符，外加 `+1564` stabilizationMargins、
`+1572` secureMode=0、`+1576` icaMaxUpscalingQ16=0x10000（**不填 0**，避免固件除零）、
`+1580` maxBatchSize=1、`+1584` muxMode=0、`+1588` topologyType=0(DEFAULT)。

### 5.3 完整报文头（前 40 B）

BPS（876 B）：

    00000000  6c 03 00 00 0a 00 01 01 01 00 00 00 88 77 66 55  l............wfU
    00000010  44 33 22 11 00 00 00 00 00 00 00 00 01 00 00 00  D3".............
    00000020  01 00 00 00 06 00 00 00 a0 0f 00 00 b8 0b 00 00  ................

IPE（1636 B，头相同，只有 opcode 与 nSize 不同）：

    00000000  64 06 00 00 0a 00 01 01 05 00 00 00 88 77 66 55  d............wfU
    00000010  44 33 22 11 00 00 00 00 00 00 00 00 01 00 00 00  D3".............
    00000020  01 00 00 00 06 00 00 00 a0 0f 00 00 b8 0b 00 00  ................

逐字段：`+0` nSize(0x36c=876 / 0x664=1636)、`+4` ePacketType=`0x0101000A`(DIRECT)、
`+8` eCmdOpcode(1 / 5)、`+12` nUserArg1=`0x1122334455667788`、`+20` nUserArg2=0、
`+28` nNumHandles=1、`+32` hHandles[0]=1、`+36` 负载起点（描述符 format=6）。

> **这只是"敢发出去"的模板，不是能出图的配置。** 没有 IQ 参数块
> （`BpsIQSettings` 1036 B / `IpeIQSettings` 3676 B 的内容仍是未知），
> 也没有 PD 元数据、stats、scratch 缓冲。B2 用它只证明"传输 + 命令分发 + 完成包"。

---

## 6. 与上游差异清单（只列差异）

相同项一句话：**队列层（24/956、字段顺序、type@188）、域分组（位 24/25）、系统命令
0x10001–0x10006 与消息 0x20001–0x20008、建句柄 28 B 与 ACK 32 B、async ACK 36 B、
opcode 1..0xC/0xE/0xF 的编号、属性 1/2/5** —— 与上游 `camss-icp-hfi.h` 逐值相同。

| # | 项 | 固件（以本文为准） | 上游 | 后果 |
| --- | --- | --- | --- | --- |
| 1 | **async 命令包** | `nUserArg1@12`(u64)、`nUserArg2@20`、`nNumHandles@28`、`hHandles@32`、payload `32+4n` | 在 `+12` 放 `fw_handle(u32)`，其余整体后移 4：`num_handles@32`、`handle[]@36`、`payload@40` | **照上游写必错**：`nNumHandles` 会读到 `nUserArg2` 的高半字，长度校验直接失败 |
| 2 | 队列索引单位 | **4 字节字**（`addr = start_addr + idx*4`，`q_size` 也是字数） | 结构里没写单位 | 引擎里乘/除 4 写错就绕回判据全错 |
| 3 | 单包上限 / 数据区 | 接收上限 **6019 B**，队列表只需 **4 KiB** 映射窗口 | 数据区 `SZ_4K`、队列表 `SZ_1M` | 上游的 4 KiB 数据区会拒收 1636 B 以上的合法包 |
| 4 | `qtbl_version` / `qtbl_qhdr_size` | **都不校验**（步长硬编码 0x3bc） | 写 `0xFFFFFFFF`、按结构体算 | 无害，照写即可 |
| 5 | `HFI_CMD_SYS_GET_PROPERTY` | **未实现**（跳表 idx3 直接拒绝） | 有定义 | 初始化序列里不能用 |
| 6 | 句柄类型宏 | 只有一套：`eHandleType ∈ 1..6`，`0x62`(1/5/6)→BPS、`0x1C`(2/3/4)→IPE | 另有一套 `HFI_HANDLE_TYPE_BPS=1`、`HFI_HANDLE_TYPE_IPE=2` | 用 2 会被固件当 IPE 流；两个宏集并存是隐患 |
| 7 | 属性 6/7/8/9 | 有实现（8=`HANG_DUMP_MODE`、9=`UBWC_CONFIG_EX` 16 B） | 无定义 | B2 要先弄明白 2/9 的 UBWC 配置，否则压缩 buffer 读写不对 |
| 8 | `HFI_SYSTEM_SUPPORTED_TYPE`(36 B) | 固件给了完整定义（`deviceTitanVersion` Kona=**7**） | 无 | B2c 的验收指标之一 |
| 9 | 调试域命令 | `0x03010008` / `0x03010009` 有实现 | 无定义 | 可作额外的"固件活着"探针 |
| 10 | opcode 0xD | 存在，16 B，只登记回调不下发硬件，名字疑似 `PERF_COUNT` 但全镜像无引用 | 无此编号 | 当成"未识别"，不要用 |
| 11 | HFI 引擎实现 | 有（`hfiqueue.c` + `ipebpsdomain.c`，`ProcessAsyncCommand` 10.5 KB） | **文件不存在**：`camss-icp-hfi.h` 只有声明 | 不能"抄实现"，只能抄常量 |
| 12 | payload ABI | 全套（§2） | **零定义** | 这一层只能从 DWARF 反向重建 |
| 13 | 打包方式 | 单一 ELF（入口 0x0，REX） | `.mdt` 分片 + `qcom_mdt_load()` + PAS/SCM | B2a 的最大未知 |
| 14 | 寄存器视图 | A5 视角（`0xe0010058` 队列表指针、`0xe0010014` 抬中断、`0xe0010060` 清状态） | `HFI_REG_*` 在 CSR+0x24..0x68，中断走独立 `cirq_base` | **无法对齐**，B2b 的真正阻塞 |
| 15 | `_BpsAbortData.requestIds` | DWARF 写 `uint32_t *`，ARM32 ⇒ 线上就是 **4 B SMMU 地址** | 无 | C 头里按 `SMMU_ADDR` 建模（x86_64 上按指针建模会变成 8 B，断言尺寸就错） |

---

## 7. 本轮新确认 / 修正的事实（相对 B0）

### 7.1 哪些 opcode 强制"恰好 1 个 handle"

B0 只写"async 命令 payload 在 `32+4n`"。本轮把**每个分支开头的句柄数检查**也读了：

    0x01 0x02 0x04 0x05 0x06 0x08 0x09 0x0A 0x0B 0x0C   ->  nNumHandles == 1，否则 bne 共享错误出口
    0x03 0x07 0x0D 0x0E 0x0F                            ->  不检查

证据：函数入口 `ldr r0,[pkt,#0x1c]`（`r0 = nNumHandles`），随后每个受限分支的前两条
指令就是 `cmp r0, #0x1 ; bne.w 0x1fc26`。Python 的
`OPCODES_REQUIRING_ONE_HANDLE` 就是这张表；给 `0x04` 传 2 个句柄会直接 ValueError。

### 7.2 SET_PROPERTY 的"线上长度"不是 16 B

DWARF 的 `HFI_CMD_SYS_SET_PROPERTY_PACKET_TYPE` 是 **16 B**，但那是
`rgPropertyData[1]` 只占一个 u32 的声明大小。线上布局是
`nSize@0, ePacketType@4, nNumProperties@8, 属性项@12`（每项 12 B），所以
**一个属性的包是 24 B**，两个属性是 36 B。同类问题也在 `SYS_INIT_DONE`
（声明 20 B，线上 `16 + 12n`）。

这条是我在单测里先按"16+12n"写、被自己的断言 `len(one)==16` 抓到才发现的
（见 §9 失误 2），已同步修正 Python 与头文件注释。

### 7.3 结构对齐是**混合**的，不能一刀切

- **包结构是 packed**：`CREATE_HANDLE` 的 `u64` 真的落在 `+12`，
  `ASYNC` 的 `nUserArg1` 也落在 `+12`；
- **done 结构是自然对齐**：`_BpsFrameProcessDone` 的 `userArg@16`、
  `_IpeConfigIoDone` / `_IpeFrameProcessDone` 的 `userArg@144`，都在结果结构
  （8 B / 136 B）之后留了 4 B 空洞；
- `_IpeBpsMemMapDone` 反过来又是 `rc@0, userArg@4`（12 B），`u64` 未对齐，
  **必须** packed。

结论：C 头统一 `packed` + 显式 `reserved` 填补，比"依赖默认对齐"或"全 packed"
都安全。这条如果不记下来，B2 解析完成包时会莫名少 4 字节。

### 7.4 abort 负载里的"指针"

`_BpsAbortData` / `_IpeAbortData` 的第二个字段在 DWARF 里是
`uint32_t *requestIds`。固件是 ARM32，所以它 4 B；**在 x86_64 上按指针建模会变成
8 B，整个 abort 负载就从 16 B 变成 22 B 而断言失败**。契约里按 `SMMU_ADDR`(u32)
建模，并在头文件里注明"线上是 32 位 SMMU 地址"。

### 7.5 顺带确认

- 15 个分支里 13 个还有**第二路独立证据**（`vSYS_MaintainCache` 之前的尺寸立即数），
  与新抽出的表值一致；0x0E/0x0F 分支没有这次调用，如实留空而不是"补一个"。
- 跳表基址规律定死：`target = (tbh_addr + 4) + 2 * halfword`，15 项，与
  `cmp r2, #0xe` 的范围检查自洽。

---

## 8. 与 B0 的一致性核对表

| B0 的说法 | B1 的复核 |
| --- | --- |
| 队列头 956 B / 表头 24 B / 步长 0x3bc | 一致：断言 + selftest + Python 三处 |
| 包类型编号（含 0x20008 / 0x1010008 / A / E） | 一致：常量写进头，Python 用它编码解码 |
| 系统命令 0x10001–0x10006，GET_PROPERTY 未实现 | 一致：`encode_sys_command` 显式拒绝它 |
| async 40 B、`nUserArg1@12`、`nNumHandles@28`、payload `32+4n` | 一致，且新增 §7.1 的句柄数约束 |
| DIRECT/INDIRECT 两条长度规则 | 一致，且给出反汇编原文（§3.4）并复刻进代码 |
| 单包 6019 B、4 字节对齐、32 位 SMMU | 一致 |
| 15 条 opcode 与负载尺寸 | **15/15 自动重抽复现**（§4） |
| 负载 840/2360/1600/14328 B | 一致；840/1600 还额外与 sizeof(struct) 对上 |
| IQ 1036 / 3676 B | 一致：只固化尺寸，内容留空 |
| 完成结构 16/24/152/152/12 B | 一致，但**修正了对齐**（§7.3） |

---

## 9. 本轮的失误与保留

1. **第一版把头文件当成纯 C11 写**：`TITAN_HFI_ASSERT` 只展开成 `_Static_assert`，
   于是 `clang++ -std=c++17 -pedantic` 报
   `'-Static_assert' is a C11 extension [-Werror,-Wc11-extensions]`。
   修法是加一层 `TITAN_HFI_SA`，C++ 走 `static_assert`。
   *教训：头文件写了 `extern "C"` 就必须真的用 C++ 编一遍，不能只测 C。*
2. **SET_PROPERTY 长度按"声明大小"算错**：先写成 `nsize = 16 + 12*(n-1)`，
   单测 `len(one) == 16` 立刻失败（实际是 24）。根因是把 DWARF 的
   `byte_size=16` 当成了"一个属性时的线上长度"，而它其实只是
   `rgPropertyData[1]` 的声明。已修正代码 + 头文件注释 + 本文档（§7.2）。
3. **反汇编文本切分第一次写错**：用 `split('\t')[-1]` 取指令，结果取到的是**操作数**
   而不是助记符（objdump 行是 `地址: 助记符 操作数`，中间是制表符），导致"找不到 tbh"。
   改成"折叠空白后取整行"并单独过滤 `.word/.short/.byte` 数据行才对。
4. **handle 数检查集合第一次写窄了**：只按 B0 文字记了 0x01/0x02/0x05/0x06/0x0C，
   自动抽取跑出来多了 0x04/0x08/0x09/0x0A/0x0B。逐条回看反汇编（分支前两条就是
   `cmp r0,#0x1 ; bne.w`）确认后改正 —— **这正是"手工表会记漏"的实例，
   也是把表交给脚本的理由。**
5. **没有做的事**：没有对 `CAMERA_ICP.elf` 做任何写操作；没有 `git add` 任何固件、
   反汇编或中间产物（全在 `local/`）；没有连设备、没有碰 `/srv`、没有改常驻服务、
   没有动充电/电源键/USB-SSH/网络；没有跑 `fastboot`。

---

## 10. 留给 B2 的未知（本轮**没有**解决，也不要当已解决）

1. **AP 侧寄存器/GP 映射** —— 唯一真阻塞。固件里看到的 `0xe00100xx` 是 A5 自己的
   地址视图。可查：原厂 `kona-camera.dtsi` 的 `cam_a5@ac00000` reg 窗口、
   下游 `camera-kernel` 的 `icp_hw` 表（只读）。
2. **固件加载方式**：kona 是单 ELF，上游是 `.mdt` + `qcom_scm`，TZ 是否接受未知。
3. **IQ 参数从哪来**：`com.qti.tuned.default.bin` / `CFR_*.bin` 格式未解；
   不解就只能全零参数跑。
4. **opcode 0xD 的语义**。
5. 本轮额外暴露的：**BPS/IPE 的 UBWC 配置（属性 2 / 9）** 必须先弄清楚，否则
   BPS/IPE 读写压缩 buffer 会错——但这是 B2 之后的活。

---

## 11. 复现步骤

    W=.agents/worktrees/camera-titan-poc
    cd "$W/tools/titan-hfi"

    # 1. C 契约（任选编译器）
    gcc -std=c11 -Wall -Wextra -Werror -pedantic -O2 \
        -o /tmp/titan_hfi_selftest titan_hfi_selftest.c && /tmp/titan_hfi_selftest
    gcc -std=c11 -Wall -Wextra -Werror -pedantic -x c -c titan_hfi.h -o /tmp/hdr.o
    clang++ -std=c++17 -Wall -Wextra -Werror -pedantic -x c++ -c titan_hfi.h -o /tmp/hdr.opp

    # 2. Python 契约
    python3 test_titan_hfi.py -v
    python3 make_config_io.py --dump --out ../../local/titan-b1/template

    # 3. 从固件重抽 opcode（需要 B0 的中间产物；local/ 已 gitignore）
    FW=../../local/titan-b0/CAMERA_ICP.elf
    test "$(sha256sum "$FW" | cut -d' ' -f1)" = \
        cb98cec20d1073d4b2872342b3db2185b57422c73e9e7f327887f1b039a1ceb9
    llvm-objdump -d --triple=thumbv7a-none-eabi --no-show-raw-insn \
        "$FW" > /tmp/disasm-thumb.txt
    python3 extract_opcodes.py --disasm /tmp/disasm-thumb.txt --elf "$FW"

> 反汇编**必须**用 `llvm-objdump` 并显式 `--triple=thumbv7a-none-eabi`：
> 本机 GNU `objdump` 2.44 是 x86-only 构建，对 ELF32 ARM 直接报
> `can't disassemble for architecture UNKNOWN`；本镜像 ARM/Thumb 混合，
> 驱动与 HFI 代码全是 Thumb（符号值奇数）。

---

## 12. 相关文档

- [B0 结果（HFI 协议提取与上游比对）](camera-titan-hfi-b0-20261111.md)
- [范围与判定标准](camera-titan-poc-scope-20261111.md)
- [B1 续作 prompt（历史）](camera-titan-b1-resume-prompt-20261111.md)
- [tools/titan-hfi/README.md](../../tools/titan-hfi/README.md)
