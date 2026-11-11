# Titan ISP 夜间攻坚任务书（2026-11-11 夜 → 11-12 晨）

> 用法：新会话**只读本文件即可开工**。用户已授权今夜连续工作、不做 token 预算。
> **今夜的唯一不可逆动作（刷机）被明确禁止**，理由见 §3。
> 开工第一件事：建 `local/titan-b2/night/STATE.md`，之后每推进一步就更新它（会话若断，靠它续命）。

---

## 0. 今夜目标（先读，避免白干）

**"把 ISP 驱动起来出图"今夜做不到，请不要朝这个方向硬撞。** 原因不是协议（协议已经全解了），而是**固件装载链路**：原厂 DT 证明 ICP 走的是"保留内存 + 独立 SMMU 上下文（SID 0x20e2 等）+ 放开 A5 复位"，不是普通的 PAS，也不是直接 request_firmware 到某个设备窗口。这条链要写一个真驱动，不是一夜的量。

今夜按优先级做四件事，**每一件都可独立验收**：

| 级别 | 目标 | 验收证据 | 概率 |
| --- | --- | --- | --- |
| **P0** | 解原厂 vendor 镜像里的相机内核模块，产出**权威装载蓝图**（固件怎么进内存、A5 怎么放复位、SMMU 怎么配、三个窗口各自干什么） | 一份带地址/寄存器/字符串出处的 md，每条结论可回溯到文件偏移 | **≥95%** |
| **P1** | 零刷机把 ICP 上电，只读 dump 三个窗口，**实测验证** `0xac10058/0xac10014/0xac10044` 这组推断 | 断电/上电对照的原始 hex + 一条明确判定（证实/证伪/不确定） | 70% |
| **P2** | 做出"明天一刷即测"的候选包：DT 片段 + 驱动骨架 + 离线编译通过 | 编译日志 + 产物 SHA + 未刷写声明 | 85% |
| **P3** | 机会主义：若 P0 发现 A5 有零刷机旁路（如 ITCM 直写口），再推进到"固件跑起来" | 能读到固件写过的寄存器证据 | <15% |

**今夜明确不做**：出图、V4L2 节点、IQ 参数（`com.qti.tuned.default.bin` 格式还没解，这是 B3 的活）。

---

## 1. 环境（全部已验证可用）

| 项 | 值 |
| --- | --- |
| 工作树（唯一） | `/mnt/f/codexprojects/oneplus8t/.agents/worktrees/camera-titan-poc` |
| 分支 | `codex/camera-titan-poc-20261111`，HEAD 至少 `fe259bd` |
| 内核源码 | `/mnt/f/codexprojects/oneplus8t/sources/linux`（含 `drivers/clk/qcom/camcc-sm8250.c`、`drivers/media/platform/qcom/camss/`） |
| 手机 | **`ssh -F /home/zhuchen/.ssh/kebab.conf -o BatchMode=yes kebab`** ← 今晚就用这条，已实测通 |
| 手机状态 | Debian 13，`7.2.0-kebab-debian13-r4-venus+`，boot id `ed3ae39d`，sudo 免密可用 |
| 固件 | `local/camera-r4-20261110/analysis/CAMERA_ICP.elf`（3,922,232 B，SHA `cb98cec2…`） |
| 原厂 DTB 拆分 | `/mnt/f/codexprojects/oneplus8t/local/sensors-20261107/stock-dtb-split/stock-000.dtb` |
| vendor 镜像 | `/mnt/f/codexprojects/oneplus8t/local/sensors-20261107/stock-vendor/vendor_a.img`（967,815,168 B，EROFS） |
| erofs 工具 | `local/sensors-20261107/erofs-tool/usr/bin/{fsck,dump}.erofs`，需 `LD_LIBRARY_PATH=local/sensors-20261107/erofs-tool/usr/lib/x86_64-linux-gnu`（缺 `libdeflate.so.0`） |
| dtc | **WSL 里有**（`/usr/bin/dtc`），别再造解析器 |
| git 身份 | `git -c user.name='Codex' -c user.email='codex@localhost'` |

`/tmp` 不跨 run_code 调用保留；中间产物一律写 `local/`（已 gitignore）。

---

## 2. 今夜已经探明的环境能力（别再猜，直接用）

我开工前跑过只读探针，结论如下：

| 能力 | 状态 | 对今晚的意义 |
| --- | --- | --- |
| `CONFIG_OF_OVERLAY` | **未开** | 运行时 DT overlay 不可用 → 想加 DT 节点必须重编内核 |
| `CONFIG_CONFIGFS_FS` + 已挂载 | 有 | 但仍受上一条限制 |
| `CONFIG_DEVMEM=y` + `CONFIG_STRICT_DEVMEM=y` | 有，`/dev/mem` 可 root 读写 | **device 区（非 RAM）可直读**，Stage A 可用它兜底 |
| `CONFIG_MODULE_SIG=y` 但 `MODULE_SIG_FORCE` 未开 | 可加载未签名模块 | 自写 .ko 能 load（项目里 taint=12800 就是证据） |
| camcc 驱动 | **已绑定**：`ad00000.clock-controller → cam_cc-sm8250` | 时钟与 GDSC 都在框架里，可被模块取用 |
| clk debugfs | 四个时钟都在：`cam_cc_fast_ahb_clk_src / cam_cc_icp_ahb_clk / cam_cc_icp_clk / cam_cc_icp_clk_src` | 可以读 rate/enable_count 做旁证 |
| `titan_top_gdsc` | `off-0`（冷态） | **必须我们自己上电**，读之前 |
| `/proc/iomem` | `0ac00000-0ac1afff` **无人认领**（camss 只占 0xac6a000 起） | 与 camss 不冲突，可安全 ioremap |
| 注意 | `/proc/iomem` 里的 `00ac0000-00ac5fff: ac0000.geniqup` 是**另一个地址**（差 16 倍，不是 20 倍） | 别混 |

---

## 3. 路线判定（必须照此执行）

| 路线 | 内容 | 判定 |
| --- | --- | --- |
| A. 运行时 DT overlay | 加 `qcom,cam-a5` 节点，让框架认领时钟/GDSC | **不可行**（OF_OVERLAY 未开），放弃 |
| **B. 零刷机模块**（今晚主路线） | 自写 .ko：用 `of_clk_get_from_provider()` 手工构造 `<&camcc 38/39/40/33>` 取到四个 clk（或用 `/dev/mem` 直写 camcc 寄存器兜底）；GDSC 直接写 `0xad0c144` 的 SW_COLLAPSE；然后 ioremap 三窗口只读 dump | **执行** |
| C. 重编内核 + 刷 boot_a | 正路，但**今夜的不可逆动作** | **禁止**，理由见下 |

**为什么今夜禁刷**：刷坏 boot_a = 手机不上电 = agent 深夜失联，你早上拿到的是一块砖；而这个仓库主工作树还有 **289 个未提交改动**、手机还跑着常驻服务。**今天不刷也完全够推进**（P0 是纯离线，P1 走路线 B），唯一需要刷的那一步（装 ICP 的 SMMU + carveout 驱动）本来就要你在场做回滚演练。**把不可逆动作留到天亮，是今晚最稳的决定。**

若某一步的结论是"不刷就做不下去"，**记录下来、停手、写报告**，不要自作主张刷机。

---

## 4. 执行顺序

### G0 开工自检（10 分钟）
1. `git -C … rev-parse --abbrev-ref HEAD` 必须是 `codex/camera-titan-poc-20261111`；`git log -1` 至少含 `fe259bd`。
2. `ssh -F /home/zhuchen/.ssh/kebab.conf -o BatchMode=yes kebab 'uname -r; uptime'`。
3. 建 `local/titan-b2/night/STATE.md`，写清当前阶段与下一步。

### G1（P0，纯离线，最高优先级，预计 2–4 小时）

解 `vendor_a.img`，找相机模块。**目标是回答五个问题，不是把镜像翻个底朝天**：

1. **固件怎么进内存**：搜 `CAMERA_ICP` / `request_firmware` / `.elf` / `mdt`；确认是"读文件→按 ELF program header 拷到某地址"，还是经 SCM PAS。**内存目标地址是关键**（是不是 `iova-mem-region-firmware` 的 IOVA 0？还是某个物理窗口？）。
2. **A5 怎么放复位**：找写 `a5_csr` / `0x18000` 偏移、或 `SCM` 调用的片段；有没有 `firmware base` / `boot_addr` 之类的寄存器。
3. **三个窗口各自干什么**：`a5_qgic`（中断控制器）/ `a5_sierra`（**HFI 寄存器就在这**）/ `a5_csr`（控制）——找 mmap 后的使用点对证。
4. **SMMU 怎么配**：SID 表（DT 里 14 个：0x20e2/0x24e2/0x2000/0x2001/0x2400/0x2401/0x2060/0x2061/0x2460/0x2461/0x2020/0x2021/0x2420/0x2421）、firmware 区 IOVA 0 + 5 MiB、shared 区、CPAS 谁来编程。
5. **顺序**：上电 → 装固件 → 写队列表指针 → 放复位，到底什么顺序（HFI 队列表指针必须在放复位**之前**写，否则固件读不到）。

**做法提示**：
- `fsck.erofs --extract=` 或 `dump.erofs` 先列出 `/lib/modules` 与 `/vendor/lib/modules` 下的 cam* 文件，**只解需要的几个**（镜像 967 MB，别全量解）。
- 模块可能不是 .ko 而是 builtin（在 `/vendor/lib/modules/modules.builtin` 里）；若如此，改从 **vendor 的 `cam_a5/cam_icp` 用户态库**（`camera.` 或 `libcam*`）与 **固件里的字符串**交叉——b0 已证明 HFI 常量都在 .text 里，装载逻辑同理可能在 A5 固件里。
- 也可以反查 **ABL / bootloader** 里有没有 ICP 初始化（`local/abl-reference-*`）。
- 每条结论必须写"出处：文件 + 偏移/符号 + 原文片段"。

**G1 交付**：`docs/camera-titan-b2-load-blueprint-20261112.md`（新文件），含上述五问的答案 + 证据 + **仍不确定的项**。

### G2（P1，上机，只读，预计 2–3 小时）

写零刷机探针模块（放 `local/titan-b2/probe/`，不入库）：

1. 上电：`titan_top_gdsc` 的 GDSCR 在 camcc `0xad0c144`，清 SW_COLLAPSE 后轮询 PWR_ON；
2. 时钟：四路（`fast_ahb 100→400MHz`、`icp_clk_src 400/480/600MHz`，按 nominal 取 400/600）；**必须先 set_rate 再 enable**；
3. ioremap 三个窗口，**按 qgic → csr → sierra 顺序**，每窗先读 `0x00`，异常立即 bail；
4. **断电/上电对照**：同一地址 GDSC 关闭时读一次、开启后读一次 —— 差值是"块真的活了"的唯一证据；
5. 重点读：`0xac10008/0xac10010/0xac10014/0xac10044/0xac10058/0xac10060/0xac10074`；
6. 退出时恢复：关时钟、GDSCR 置回 collapse、卸载干净。

**硬禁忌**：Stage A **绝不写 `0xac1xxxx`** 的任何寄存器；不写 camcc 的 `0xad0xxxx` 以外地址；不碰 camss 的 0xac6a000+。

若 `of_clk_get_from_provider` 取不到时钟，退化方案：`/dev/mem` 直写 camcc RCG/CBCR（偏移从 `camcc-sm8250.c` 里读，别猜）。

**G2 交付**：原始 hex 落到 `local/titan-b2/night/dumps/` + 一句明确判定写进 STATE.md。

### G3（P2，纯离线，预计 1–2 小时）

把 G1 的结论变成明天可刷的东西：
- DT 片段：`cam-a5` 节点（值全部照抄原厂，已在下面 §5）+ 固件 carveout reserved-memory + ICP SMMU client 节点；
- 驱动骨架：按 G1 的装载顺序写伪代码即可，**不要求编译出可跑驱动**；
- 若能编译：交叉编译一次，记录 toolchain 与产物 SHA。
- **不刷**。

### G4（P3，机会主义，剩余时间）

只有 G1 明确给出"零刷机旁路"时才做，例如 A5 有 ITCM 直写口、或 CPAS 有自举路径。否则直接跳到 G5。

### G5 收尾（**07:00 前必须开始**）
1. 手机恢复到干净状态：模块卸载、时钟关、GDSC 关、无残留 ioremap；`dmesg | tail` 无 oops；必要时重启一次并确认 ssh 与常驻服务回来。
2. 写 `docs/camera-titan-night-report-20261112.md`：今夜做了什么、证了什么、没证什么、明天的第一步。
3. 提交（只 `docs/` 与 `tools/`，固件与 dump 留 `local/`）。
4. 给用户一段 200 字内的结论 + 明天的三件事。

---

## 5. 已确证的事实（直接抄，不要重新推导）

### 5.1 ICP 硬件（原厂 DT 实证，`dtc` 反编译 `stock-000.dtb`）

    qcom,a5@ac00000 {                       // 注意：这是 cam-icp 的兄弟节点，不是子节点
        compatible = "qcom,cam-a5";
        reg = <0xac00000 0x6000  0xac10000 0x8000  0xac18000 0x3000>;
        reg-names = "a5_qgic", "a5_sierra", "a5_csr";
        reg-cam-base = <0x0 0x10000 0x18000>;   // 注意：它就是 reg-0xac00000，不是独立证据
        interrupts = <0x0 0x1cf 0x1>;           // GIC SPI 463
        camss-vdd-supply = <&titan_top_gdsc>;   // camcc GDSCR @0xad0c144
        clock-names = "soc_fast_ahb","icp_ahb_clk","icp_clk_src","icp_clk";
        clocks = <&camcc 0x21 &camcc 0x26 &camcc 0x28 &camcc 0x27>;  // 33/38/40/39
        clock-rates = <100M 0 400M 0 … 400M 0 600M 0>;              // 见下表
        fw_name = "CAMERA_ICP.elf";
        ubwc-ipe-fetch-cfg = <0x707b 0x7083>;  ubwc-ipe-write-cfg = <0x161ef 0x1620f>;
        ubwc-bps-fetch-cfg = <0x707b 0x7083>;  ubwc-bps-write-cfg = <0x161ef 0x1620f>;
    };

| level | soc_fast_ahb | icp_clk_src |
| --- | --- | --- |
| lowsvs | 100 MHz | 400 MHz |
| svs | 200 MHz | 480 MHz |
| svs_l1 | 300 MHz | 600 MHz |
| nominal / turbo | 400 MHz | 600 MHz |

- camcc 时钟编号：`FAST_AHB_CLK_SRC=33, ICP_AHB_CLK=38, ICP_CLK=39, ICP_CLK_SRC=40`（`include/dt-bindings/clock/qcom,camcc-sm8250.h`）
- `titan_top_gdsc`：`camcc-sm8250.c` 里 `.gdscr = 0xc144`、`PWRSTS_OFF_ON`、`POLL_CFG_GDSCR`
- `CAM_CC_ICP_BCR = 0xc070`（这是 **reset**，bit0=assert；正常态应写 0）
- ICP 的 SMMU：`iova-mem-region-firmware` = IOVA `0x0` 长度 `0x500000`（5 MiB，正好装下 ELF 的 0x0/0x100000 两段 + 0x150000 heap + 0x350000 页表）；SIDs 见 §4 G1 第 4 问
- 另有独立节点 `qcom,cam-icp`（compatible `qcom,cam-icp`，`compat-hw-name = "qcom,a5","qcom,ipe0","qcom,bps"`，`icp_pc_en`），是**装配节点**不是驱动

### 5.2 固件侧的 HFI 寄存器（A5 视角 → AP 侧，**推断**，正是 G2 要证的）

| A5 地址 | AP 地址（推断） | 用途（B0 反汇编实证） |
| --- | --- | --- |
| `0xe0010058` | `0xac10058` | 队列表指针（`HFI_QueueGetQHdr` 读它） |
| `0xe0010014` | `0xac10014` | 抬 host 中断（写 1） |
| `0xe0010060` | `0xac10060` | 状态清除（写 0x10/0x20/0x120） |
| `0xe0010044` | `0xac10044` | fwEntryFunction 读 |
| `0xe0010074` | `0xac10074` | MemMapUmdCdmRegions |
| `0xe0010008/0x10` | `0xac10008/0x10` | 未定 |
| `0xe0040000/0x3c` | `0xac40000/0xac4003c` | **CPAS**（不是 CDM，B0 曾标错） |

**三条必须记住的边界**（我复核 B2 预研时发现的）：
1. `reg-cam-base` 只是 `reg − 0xac00000`（ipe0 0xac9a000→0x9a000 可证），**不构成**"A5 0xe0000000 ↔ AP 0xac00000"的证据；真正支撑它的是"三窗口是 A5 块连续三段"+"0xe0040000 正好落在 cam-cpas(0xac40000)"。
2. **不能外推**：固件还摸 `0xe0070000/0xe0090000/0xe00bd000`，按同规则会落到 0xac70000（主线 camss 的 csiphy3！）/0xac90000/0xacbd000 —— 显然不是 CDM。这几段是 CPAS 内部视图，**别照搬**。
3. 所以 §5.2 的表是"高置信推断，待 G2 实测证伪"，文档里请这样写。

### 5.3 HFI 协议（B1 已固化，别重解）

- 队列头 `956 B`、队列表头 `24 B`、3 队列（0=cmd 1=msg 2=dbg）、索引以 **4 字节字** 为单位、队列表必须在单一 4 KiB 映射内、单包上限 **6019 B**
- 包类型：SYS `0x10001–0x10006`；MSG `0x20001+`（EVENT_NOTIFY `0x20008`）；IPE/BPS `0x01010008`(CREATE_HANDLE) / `0x0101000A`(ASYNC DIRECT) / `0x0101000E`(ASYNC INDIRECT)
- **async 命令包与上游差 4 字节**：`nUserArg1(u64)@12, nUserArg2(u64)@20, nNumHandles@28, hHandles[]@32, payload@32+4×nNumHandles`
- DIRECT 要求 `nSize ≥ 负载 + 32 + 4n`；INDIRECT 只要 `≥40`，负载为 32 位 SMMU 地址（**整个 HFI 只认 32 位地址**）
- opcode 1–0xF 与负载尺寸：840 / 2360 / 16 / 8 / 1600 / 14328 / 16 / 8 / 16 / 16 / 16 / 16 / 16(未识别) / 20 / 20
- 代码在 `tools/titan-hfi/`（C 头 + Python 编解码 + 单测），**直接复用**

---

## 6. 硬边界（违反即失败）

1. **今夜不刷任何分区**（尤其 boot_a / boot_b / dtbo / super / persist）。
2. 不写 `persist` / EFS / 校准数据；不动 `/srv`。
3. 不改网络、SSH、密码、限充、电源键、温控、看门狗配置。
4. 不装常驻服务/unit/timer；模块只在需要时 insmod、用完 rmmod。
5. **Stage A 绝不写 `0xac1xxxx`**；不碰 camss 的 `0xac6a000+`。
6. 任何时候**不超过 2 次连续失败**在同一子步骤上 —— 停手、记录、换路线或收工。
7. 手机 ssh **连续 10 分钟不通** → 停止所有写操作，只做只读诊断；**30 分钟不通** → 收工写报告（说明是否需要人工介入）。
8. 重启手机前先在 STATE.md 写明"要重启、当前 boot id、目的是什么"；重启后 5 分钟内确认 ssh 与常驻服务回来，否则按第 7 条处理。

---

## 7. 证据纪律（这条决定明早你是拿到结论还是拿到幻觉）

- 每个结论必须附**命令 + 原始输出片段 + 文件/行号或偏移**；没有证据的结论写"未证"。
- 所有原始输出落盘到 `local/titan-b2/night/`，文件名带时间戳。
- 不允许"应该""大概""通常"作为结论；只允许"实测到 X"或"未测"。
- 每次 `insmod` 前先 `dmesg -c` 或记录尾标，之后立刻抓 `dmesg`。
- 改动手机前记录 `boot_id` + 时间 + 目的，收尾时对照。

---

## 8. 交付物

| 文件 | 内容 |
| --- | --- |
| `docs/camera-titan-b2-load-blueprint-20261112.md` | P0 权威装载蓝图（五问 + 证据 + 不确定项） |
| `docs/camera-titan-night-report-20261112.md` | 今夜总报告（做了什么/证了什么/没证什么/明天第一步） |
| `local/titan-b2/night/STATE.md` | 随时更新的断点续作状态 |
| `local/titan-b2/night/dumps/` | G2 的原始寄存器 dump |
| `tools/titan-icp/`（可选） | 任何可复用的离线脚本 |

提交信息：`docs(camera): …` / `tools(camera): …`。

---

## 9. 明天的三件事（写在报告末尾，别自己执行）

1. 用户在场时，用 P2 的候选包做**一次**内核构建 + boot_a 刷写 + 回滚演练（DT 里加 cam-a5 + 固件 carveout + ICP SMMU client）。
2. 写真正的 ICP 驱动第一步：上电 → 装固件到 carveout（IOVA 0）→ SMMU 上下文 → 写队列表指针 → 放复位。
3. 若 A5 起来：走 HFI 握手（SYS_INIT → INIT_DONE，期望 `deviceTitanVersion = 7` = Kona）。

---

## 10. 最后：不要做的事

- 不要重新推导 B0/B1 已经证明的东西（HFI 结构、opcode、包尺寸都在 `tools/titan-hfi/` 里）。
- 不要相信任何"定死"的说法（包括本文件 §5.2 的表，它是推断）。
- 不要为了赶进度跳过 G1（离线蓝图）—— 它是今夜唯一能真正缩短明天路径的东西。
- 不要在没被 `dtc` 验证过的情况下使用任何 DT 数值。
