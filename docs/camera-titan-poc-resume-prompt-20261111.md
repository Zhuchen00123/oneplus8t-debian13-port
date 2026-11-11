# 相机 Titan ISP PoC —— 续作 prompt（2026-11-11）

> **状态：B0 已完成（2026-11-11），结果见 [HFI B0 提取与比对](camera-titan-hfi-b0-20261111.md)。**
> 本文件保留为当时的任务书，**下文 §3 的步骤已完成，不要重跑**。
> **要继续做 B1 请改用 [B1 续作 prompt](camera-titan-b1-resume-prompt-20261111.md)（自包含，不用先读别的文档）。**
>
> 用法：新会话先读本文件，再读它指向的 scope。**第一轮只做 B0，不要上机。**

## 0. 你的任务

在**不改设备、不碰分区、不写内核**的前提下，完成 B0：从 `CAMERA_ICP.elf` 的符号与调试信息里，把 Titan 的 **HFI 协议**（队列、消息头、opcode、payload 结构）提取出来，并与上游 x1e80100 的 `camss-icp.c` 逐项比对，给出"能复用多少"的结论。

产出：`docs/camera-titan-hfi-b0-20261111.md`。

## 1. 必读顺序

1. `AGENTS.md` —— 项目全局规则（相机、边界、网络、SSH、fastboot 都在里面）
2. `docs/camera-titan-poc-scope-20261111.md` —— **目标、判定标准、边界、资产清单、风险**
3. 本文件 —— 具体步骤与环境坑

如需了解相机现状：`docs/camera-resume-prompt-20261111.md`、`docs/r4-camera-first-light-20261111.md`。

## 2. 当前状态（交接快照）

| 项 | 值 |
| --- | --- |
| 分支 | `codex/camera-titan-poc-20261111`（worktree `.agents/worktrees/camera-titan-poc`，基线提交 `13513ca`） |
| 已完成（上一会话） | 路线论证（软件 ISP / CAMSS v3 / OPE / Titan 四条线）、社区调研、固件与调校数据提取、资产哈希 |
| 未开始 | B0（本文档的任务） |
| 设备状态 | 正常常驻；**本阶段不需要碰手机** |

已核实的关键事实（详见 scope §1）：

- 固件 `local/camera-r4-20261110/analysis/CAMERA_ICP.elf`，3,922,232 B，SHA-256 `cb98cec20d1073d4b2872342b3db2185b57422c73e9e7f327887f1b039a1ceb9`
- ELF32 ARM EXEC，**not stripped、含 debug_info**，构建路径 `...titan.icp.1.0.release82/fw_core/ICP/...`，`cortex-a5`
- 符号表已能看到 HFI 命令族：`HFI_IPEBPS_CMD_OPCODE_BPS_CONFIG_IO`、`BPS_FRAME_PROCESS`、`IPE_FRAME_PROCESS` 等
- 上游参考实现：`[PATCH 0/7] ... icp: Add HFI/ICP v4l m2m driver for x1e80100 BPS/IPE`（2026-07-03，RFC，未合并）

## 3. B0 的步骤（按顺序）

1. **校验资产**：确认固件文件存在且 SHA-256 与 scope §4 一致；不一致就停下来说明。
2. **优先看 debug_info**：先跑
       readelf --debug-dump=info CAMERA_ICP.elf | grep -A30 'DW_TAG_structure_type'
   目标是**直接拿到结构体定义**（HFI 队列头、命令头、payload）。这一步若成功，后面全都省了。
3. **补充符号表**：`readelf -sW`，按 `HFI_` / `hfi` / `ipe` / `bps` 分组，整理 opcode 与函数名映射。
4. **字符串常量**：`strings` 里的格式串（如 `Enqueued HFI_IPEBPS_CMD_OPCODE_BPS_CONFIG_IO`）常带字段顺序线索。
5. **必要时反汇编**：`objdump -d`，优先看 `hfiqueue.c` / `hfihal.c` 对应的函数。
6. **与 x1e80100 对比**：
   - 版本：`camss-icp.c` 里打印的 `HFI struct sizes: q_hdr=956 q_tbl_hdr=24`
   - opcode 编号与顺序
   - 队列/共享内存布局（SHMEM、FwUncached 等）
   给出"逐项相同 / 部分相同 / 完全不同"的结论。
7. **写文档**：opcode 表、结构尺寸表、差异结论、**可复用比例判断（高/中/低）**、下一步建议。

## 4. 可用的上游链接

- x1e80100 ICP 系列 cover（可直连）：`https://lkml.iu.edu/hypermail/linux/kernel/2607.0/07113.html`
- 作者分支（含 `camss-icp.c`/`camss-bps.c`/`camss-ipe.c`）：
  `https://git.kernel.org/pub/scm/linux/kernel/git/bod/linux.git/log/?h=qcom-laptops-v6.18-rc4-camss-icp-bps-ipe-icp-boots%2Bstats-b4`
- 单文件取法：`https://git.kernel.org/pub/scm/linux/kernel/git/bod/linux.git/plain/drivers/media/platform/qcom/camss/camss-icp.c?h=<分支名>`

## 5. 环境坑（实测过，别重踩）

- **每次 run_code 的 WSL 是独立进程**：`/tmp` 不跨调用保留。中间产物写 `local/`（已 gitignore）。
- 固件提取需要项目内的 `libdeflate.so.0`：
      export LD_LIBRARY_PATH=local/sensors-20261107/erofs-tool/usr/lib/x86_64-linux-gnu
- `lore.kernel.org` 直连返回 **403**；改用 `lists.openwall.net`、`lkml.iu.edu`、`marc.info`。
- `patchwork.kernel.org` 的 API 不可用；libcamera 的 patchwork 可用（`/patch/<id>/mbox/`）。
- PowerShell 会吃掉远端命令里的 `2>/dev/null`；远端脚本用 base64 管道给 `sudo -n bash`。
- SSH 必须带 `-o ServerAliveInterval=5 -o ServerAliveCountMax=2`。
- 仓库已忽略 `/local/`、`*.elf`、`*.bin`：**固件与逆向产物不要 `git add`**。

## 6. 边界（硬性，违反即失败）

- 不写任何分区；不用 `fastboot flash`（本阶段连 `fastboot` 都不需要）。
- 不改 `/srv`、不改常驻服务、不碰 persist/校准。
- B2（上机）**必须**用户明确批准 + 维护窗口；本阶段不涉及。
- 连续两次尝试无进展就停下来汇报，不要无限试错。

## 7. 交付验收

`docs/camera-titan-hfi-b0-20261111.md` 必须包含：

1. HFI **opcode 表**（编号 + 名称 + 方向）
2. **结构尺寸表**（我们的值 vs x1e80100 的 `q_hdr=956 / q_tbl_hdr=24`）
3. 固件自报的版本/API 信息
4. 差异结论 + **可复用比例（高/中/低）**及理由
5. 下一步建议：是否值得进入 B1/B2，或者应当归档

若某一步做不到，如实记录卡点与已排除的路径，**不要编造结论**。

## 8. 提交约定

- 只提交 `docs/`（以及必要时 `tools/`）；固件、镜像、日志一律 `local/`。
- 提交信息格式参考仓库历史：`docs(camera): ...`。
