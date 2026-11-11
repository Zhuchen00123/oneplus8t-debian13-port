# tools/titan-hfi — Titan (Kona CAMSS ICP/BPS/IPE) HFI 契约

B1 阶段的产物：把 B0 从 `CAMERA_ICP.elf` 里离线提取出来的 HFI 线格式固化成
可编译、可测试的契约。**纯离线工具，不参与设备运行，也不上机。**

固件：`CICP.FW.1.0.82-4-ICA_REG` / `AAAAAA` / `HARV-PSOMANCH`
（`titan.icp.1.0.release82`），3,922,232 B，
SHA-256 `cb98cec20d1073d4b2872342b3db2185b57422c73e9e7f327887f1b039a1ceb9`。

| 文件 | 作用 |
| --- | --- |
| `titan_hfi.h` | **权威契约**：队列层 24/956 B、包类型、全部包结构与 payload ABI、opcode→负载尺寸表；161 处 `static_assert` 把每个尺寸与偏移钉死 |
| `titan_hfi_selftest.c` | 打印全部尺寸/偏移，并把 opcode 表与 payload 结构大小交叉核对；编译失败或打印 FAIL 即契约被改坏 |
| `titan_hfi.py` | 纯标准库编解码器：`encode_async` / `decode` / `queue_header_bytes`，并复刻固件 `CheckCommandSize` 的两条长度规则 |
| `extract_opcodes.py` | 从 `llvm-objdump` 反汇编 + ELF 里**自动重抽** opcode 跳表与负载尺寸，再与 `titan_hfi.h` 逐条比对 |
| `make_config_io.py` | 生成"最小可跑"的 `_BpsConfigIo`(840 B) / `_IpeConfigIo`(1600 B) 负载与完整 async 包 |
| `test_titan_hfi.py` | 37 项单测，含 async payload 偏移 32+4n 的 n=1/2/3 三组用例 |

## 用法

    # C 契约：跑一遍断言并打印全部尺寸
    gcc -std=c11 -Wall -Wextra -Werror -pedantic -O2 \
        -o titan_hfi_selftest titan_hfi_selftest.c && ./titan_hfi_selftest

    # Python 契约
    python3 test_titan_hfi.py -v
    python3 make_config_io.py --dump

    # 从固件重新抽取 opcode 表（需要 B0 的反汇编与 ELF，都在 local/ 里）
    llvm-objdump -d --triple=thumbv7a-none-eabi --no-show-raw-insn \
        CAMERA_ICP.elf > disasm-thumb.txt
    python3 extract_opcodes.py --disasm disasm-thumb.txt --elf CAMERA_ICP.elf

    # 生成 B2 用的最小报文（写 local/，不要 git add）
    python3 make_config_io.py --out ../../local/titan-b1/template

## 三条不要踩的线

1. **不要照上游 `hfi_cmd_async` 写命令包**：它在 `+12` 多插一个 `fw_handle`，
   把 `nNumHandles` 推到 `+32`、payload 推到 `+40`。固件是 `nUserArg1@12`、
   `nNumHandles@28`、`hHandles@32`、`payload@32+4n`。
2. **INDIRECT 只校验 `nSize >= 40`**，负载在别处；DIRECT 才要求
   `nSize >= 负载 + 32 + 4n`。`IPE_FRAME_PROCESS`(14328 B) 只能走 INDIRECT。
3. **整条 HFI 只认 32 位 SMMU 地址**，且单包上限 6019 B。

契约细节、证据链与与上游的差异清单见
[`docs/camera-titan-hfi-b1-20261111.md`](../../docs/camera-titan-hfi-b1-20261111.md)。
