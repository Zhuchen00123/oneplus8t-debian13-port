# 路线 B：Titan 相机 ISP 可行性 PoC（范围与判定标准）

> 状态：**规划完成，尚未上机**
> 分支：`codex/camera-titan-poc-20261111`（worktree `.agents/worktrees/camera-titan-poc`，基线 `13513ca`）
> 用途：本文件定义 PoC 的**目标、判定标准、边界**；新会话从 [resume prompt](camera-titan-poc-resume-prompt-20261111.md) 进入。

## 0. 一句话

硬件、固件、调校数据都在手上，缺的是驱动。本 PoC 只回答一个问题：**SM8250 的 Titan（ICP + BPS/IPE）能否在主线内核上被驱动起来**。产出是**判定**，不是产品。

## 1. 为什么走这条路（背景）

主线 `qcom-camss` 只实现 RDI 原始直出，没有任何 ISP 处理。安卓的相机 ISP 能力由三部分组成：

1. 用户态 `camera.qcom.so`（camx/CHI，闭源，绑死安卓内核）——**不可复用**；
2. 内核 `camera-kernel`（下游驱动，走私有 ioctl）——**不可移植**；
3. **A5/ICP 上跑的 Titan 固件 + BPS/IPE 硬件**——**这是我们唯一可能碰到的部分**。

已有事实（2026-11-11 核实）：

| 项 | 证据 |
| --- | --- |
| 固件存在 | 原厂 vendor 分区 `/firmware/CAMERA_ICP.elf`，3,922,232 B，ELF32 ARM EXEC，**not stripped、含 debug_info** |
| 固件身份 | 构建路径 `...titan.icp.1.0.release82/fw_core/ICP/...`，`cortex-a5`，符号 `CPAS_TOP_CPAS_0_TITAN_VERSION` |
| 协议线索 | 符号表直接给出 HFI 命令：`HFI_IPEBPS_CMD_OPCODE_BPS_CONFIG_IO`、`BPS_FRAME_PROCESS`、`BPS_WAIT_FOR_IPE`、`IPE_CONFIG_IO`、`IPE_FRAME_PROCESS`、`IPE_WAIT_FOR_BPS`、`BPS_ABORT`、`IPE_ABORT`、`BPS_DESTROY`、`IPE_DESTROY` |
| 句柄类型 | `HFI_IPEBPS_HANDLE_TYPE_BPS`、`HFI_IPEBPS_HANDLE_TYPE_IPE` |
| 源码线索 | `api_imp_bps.c`、`api_imp_ipe.c`、`hfihal.c`、`hfiqueue.c` |
| 调校数据 | `/lib64/camera/com.qti.tuned.default.bin`（933,360 B）+ `CFR_*.bin` |
| 硬件节点 | 原厂 `kona-camera.dtsi`：`cam_a5@ac00000`、`qcom,ipe0`（cam-ipe）、`qcom,bps`（cam-bps）、`qcom,cam_smmu`；`"ope"` 出现 0 次 |
| 社区进展 | `[PATCH 0/7] media: qcom: camss: icp: Add HFI/ICP v4l m2m driver for x1e80100 BPS/IPE`（2026-07-03，Bryan O'Donoghue，**very loose RFC，未合并**）；`camss-icp.c` 623 行，`of_match` **只有** `qcom,x1e80100-camss-icp` |
| 无关路线 | OPE 系列（v14，2026-11-06）只支持 `qcom,qcm2290-camss-ope`，与我们这代无关 |

结论：**没有现成的 kona 版**，但存在可借的骨架（HFI 层 + BPS/IPE m2m）。PoC 的价值就是量化"能借多少"。

## 2. 分阶段目标与判定标准

| 阶段 | 输入 | 动作 | 产出 | 判定 | 风险 | 预估 |
| --- | --- | --- | --- | --- | --- | --- |
| **B0** 离线协议提取 | `CAMERA_ICP.elf`、`camss-icp.c` | 从符号/调试信息提取 HFI 队列、消息头、opcode、payload 结构；提取固件自报版本；与 x1e80100 的定义逐项比对 | `docs/camera-titan-hfi-b0-20261111.md`（opcode 表 + 结构尺寸表 + 差异结论） | **能复用比例**（高/中/低） | 零（纯离线） | 1–2 天 |
| **B1** 结构自洽验证 | B0 结果 | 写解析器/断言工具（`tools/titan-hfi/`，x86 编译），用固件里的结构尺寸打印/符号交叉验证 | 可运行工具 + 测试 | 结构定义可信 | 零（不碰设备） | 2–3 天 |
| **B2** 上机拉活 A5 | B1 通过 + **用户批准** | 临时内核（`fastboot boot`）+ 最小 ICP 节点，加载 `CAMERA_ICP.elf`，观察是否启动、HFI 是否握手 | 启动日志/失败记录 | **起得来 = 路通；连续两次失败 = 归档** | 中 | 1 个窗口 |
| **B3** 处理一帧 | B2 成功 | 让 BPS 处理一张已抓的 RAW | 输出帧 | 能出图 | 中 | 待定 |

**B0 是本次交接的唯一任务**；B1 之后是否需要新会话再评估。

## 3. 边界与停止条件

- **不写任何分区**；需要新内核时只用 `fastboot boot` 临时启动（沿用本项目既有规则）。
- **不改长期部署**：不碰 boot_a、不改常驻 systemd 单元、不动 `/srv` 既有内容。
- **不碰 persist / 校准数据**。
- 固件镜像与逆向中间产物一律放 `local/`（`/local/`、`*.elf`、`*.bin` 已在 .gitignore 中）。
- **连续两次失败即归档停止**，不无限试错。
- **B2 之前必须获得用户明确批准**，且需要维护窗口。
- 全程不改充电/温控/电源键/USB-SSH/网络配置。

## 4. 资产清单

| 资产 | 路径（不入库） | 大小 | SHA-256 |
| --- | --- | --- | --- |
| 相机 ICP 固件 | `local/camera-r4-20261110/analysis/CAMERA_ICP.elf` | 3,922,232 B | `cb98cec20d1073d4b2872342b3db2185b57422c73e9e7f327887f1b039a1ceb9` |
| Chromatix 调校 | `local/camera-r4-20261110/analysis/com.qti.tuned.default.bin` | 933,360 B | `2c72a25b3c8e66564a0a161795c742f4a51d3b1bd79440a754f1060eaa49a725` |
| 原厂 vendor 分区镜像 | `local/sensors-20261107/stock-vendor/vendor_a.img` | 约 0.9 GB | `19219fe988bc175e1fb1df25e26c2ac2e31f225bb894a1ff04ca178e181c399e` |

提取方法（可复现，两步）：

    export LD_LIBRARY_PATH=local/sensors-20261107/erofs-tool/usr/lib/x86_64-linux-gnu
    local/sensors-20261107/erofs-tool/usr/bin/dump.erofs --cat --path=/firmware/CAMERA_ICP.elf \
      local/sensors-20261107/stock-vendor/vendor_a.img > local/camera-r4-20261110/analysis/CAMERA_ICP.elf

注意：`dump.erofs` 需要项目内那份 `libdeflate.so.0`（在 `erofs-tool/usr/lib/x86_64-linux-gnu` 下），系统自带的不可用。

## 5. 明确不做的事

- 不做 IFE/VFE **inline** ISP 的寄存器逆向（工作量大一个数量级，且对静态照片收益不如离线通路）。
- 不复用 camx 用户态（闭源 + 绑死安卓内核，已论证不可行）。
- 不做 OPE（SM8250 硬件上不存在这个块）。
- **不解决对焦**：VCM 是独立的一条线，即使 ISP 跑通，照片依然糊。

## 6. 产出物

| 类型 | 位置 | 是否入库 |
| --- | --- | --- |
| 分析文档 | `docs/camera-titan-*.md` | 是 |
| 解析/校验工具 | `tools/titan-hfi/`（若 B1 做） | 是 |
| 内核补丁 | `patches/camera/`（若 B2 需要） | 是 |
| 固件、镜像、逆向中间产物 | `local/` | 否 |

## 7. 风险

1. **固件认证**：kona 是单文件 `CAMERA_ICP.elf`，x1e80100 用 `.mdt` 分片 + SCM 认证；主线通过 `qcom_scm` 加载时是否被 TZ 接受，未知。
2. **HFI 版本**：Titan 1.0（2019）与 x1e80100 那代（2023+）可能差很多；若结构尺寸/语义对不上，复用率会很低。
3. **无文档**：全部依赖符号表与反汇编，遇到优化过的代码路径可能读不通。
4. **收益有限**：即便成功，也只解决"流畅 + 色彩"，不解决"清晰"。

## 8. 时间预算

- B0：1–2 天（纯离线，本次交接的核心任务）
- B1：2–3 天
- B2：需要 1 个维护窗口 + 明确批准
- 总体上，**两周内应能给出"这条路行不行"的结论**；若超出，说明低估了难度，应重新评估。

## 9. 相关文档

- [相机续作 prompt（阶段 0/1 已完成）](camera-resume-prompt-20261111.md)
- [r4 相机首次受光](r4-camera-first-light-20261111.md)
- [可复现性](r4-camera-reproducibility-20261111.md)
- [主线移植总记录](r4-camera-20261110.md)
