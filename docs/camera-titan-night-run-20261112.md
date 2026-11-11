# Titan ISP 夜间攻坚任务书 v2（2026-11-11 夜 → 11-12 晨）

> **v2 变更：允许刷机，但必须走 §4 的安全协议。** 用户已授权全自动、含刷写。
> 用法：新会话只读本文件即可开工。开工第一件事：建 `local/titan-b2/night/STATE.md`，之后每推进一步就更新它。
> **一句话纪律：先证明能救，再允许弄坏；先用 RAM 启动验，再持久化。**

---

## 0. 目标与概率

协议层已经全解（B0/B1），硬件映射已离线定位（B2）。现在卡的是**固件装载链路**：ICP 用「DDR 保留内存 + 独立 SMMU 上下文 + A5 复位」起固件，不是 PAS、也不是普通 request_firmware。

| 级别 | 目标 | 验收 | 概率 |
| --- | --- | --- | --- |
| **P0** | 解 vendor 镜像里的相机模块，产出权威装载蓝图（固件怎么进内存 / A5 怎么放复位 / SMMU 怎么配 / 三窗口职责 / 顺序） | 带文件偏移出处的 md | ≥95% |
| **P1** | **恢复通道演练 + known-good 基线**（零写入） | 进城/出城/RAM 启动三段实测记录 | ≥95% |
| **P2** | 「零改动」内核重建 + **RAM 启动**通过（证明构建→打包→启动链路） | 启动后 ssh 回来 + dmesg 干净 | 85% |
| **P3** | DT 加 `cam-a5` + 固件 carveout，RAM 启动通过后**持久化**，再用框架上电只读 dump | 断电/上电对照 hex + 映射判定 | 70% |
| **P4** | 机会主义：A5 真的跑起来（读到固件写过的寄存器） | 证据 + 判定 | 20% |
| — | 出图 / V4L2 / IQ 参数 | — | 今夜不做 |

**允许刷机后最大的变化**：P3 从"明天再做"变成"今夜可做"，而且走正规框架上电（比寄存器直写稳）。但 P4 依旧难，因为它要写 SMMU 上下文驱动。

---

## 1. 环境（全部实测可用）

| 项 | 值 |
| --- | --- |
| 工作树 | `/mnt/f/codexprojects/oneplus8t/.agents/worktrees/camera-titan-poc`，分支 `codex/camera-titan-poc-20261111` |
| 手机 | `ssh -F /home/zhuchen/.ssh/kebab.conf -o BatchMode=yes kebab`（sudo 免密） |
| 手机内核 | `7.2.0-kebab-debian13-r4-venus+`，boot id `ed3ae39d`，**current-slot: a** |
| **进城（进 fastboot）** | 手机侧 `sudo kebab-reboot-fastboot`（PON helper，含 vermagic 自检）→ 约 20 秒进 fastboot |
| **fastboot 在哪** | **Windows 侧**：`E:\adroid\platform-tools\fastboot.exe`（v37.0.0）。WSL 无 USB、无 fastboot，但可经 interop 调 `/mnt/e/adroid/platform-tools/fastboot.exe`（已实测能跑） |
| fastboot 能力 | `unlocked: yes`，`max-download-size: 805306368`（768 MB，boot 包约 47 MB） |
| 内核源码 | `/mnt/f/codexprojects/oneplus8t/sources/linux` |
| 固件 | `local/camera-r4-20261110/analysis/CAMERA_ICP.elf`（SHA `cb98cec2…`） |
| 原厂 DTB | `/mnt/f/codexprojects/oneplus8t/local/sensors-20261107/stock-dtb-split/stock-000.dtb` |
| vendor 镜像 | `…/local/sensors-20261107/stock-vendor/vendor_a.img`（967 MB，EROFS） |
| erofs 工具 | `local/sensors-20261107/erofs-tool/usr/bin/{fsck,dump}.erofs` + `LD_LIBRARY_PATH=…/erofs-tool/usr/lib/x86_64-linux-gnu` |
| dtc | WSL 自带 `/usr/bin/dtc` |
| git 身份 | `git -c user.name='Codex' -c user.email='codex@localhost'` |

**坑**：`fastboot devices` 在无设备时会**阻塞**，所有 fastboot 命令一律加 `timeout 10`（或 `timeout 120`，给闪写留时间）。`/tmp` 不跨调用保留，中间产物写 `local/`。

---

## 2. 今夜已验证的环境能力

| 能力 | 状态 | 意义 |
| --- | --- | --- |
| `CONFIG_OF_OVERLAY` | **未开** | 运行时 DT overlay 不可用 → 要 DT 节点只能重编内核刷 boot_a |
| `CONFIG_DEVMEM=y` + `STRICT_DEVMEM=y` | `/dev/mem` 可读写 device 区 | 零刷机的兜底路径 |
| `MODULE_SIG` 有但 `MODULE_SIG_FORCE` 未开 | 可加载未签名 .ko | 探针模块能直接 load |
| camcc | 已绑定 `cam_cc-sm8250`，四个 ICP 时钟在 debugfs | 框架可用 |
| `titan_top_gdsc` | `off-0` 冷态 | 必须自己上电 |
| `0ac00000-0ac1afff` | `/proc/iomem` 中无人认领 | 与 camss（0xac6a000+）不冲突 |
| `/proc/config.gz` | `CONFIG_IKCONFIG_PROC=y` | **可直接取到运行内核的完整配置** ← 重建内核的关键 |

---

## 3. 权限分级（按这个执行）

| 级别 | 动作 | 是否需要闸门 |
| --- | --- | --- |
| **T0** | 只读侦察、离线分析、加载只读探针模块 | 随时 |
| **T1** | `fastboot boot <img>`（RAM 启动，**不写任何分区**） | 随时，可反复 |
| **T2** | `fastboot flash boot_a <img>` | **必须过 §4 全部闸门** |
| **T3** | 刷 boot_b / dtbo / super / misc / erase / `--set-active` | **禁止** |

T3 为什么禁：boot_b 是你最后的兜底；dtbo 与 super 与本次无关；`--set-active`/`erase` 会毁掉可恢复性。

---

## 4. 刷机安全协议（S1–S8，缺一不可）

### S1 恢复通道演练（零写入，**不做完不许 T2**）

1. 记录 `boot_id` + 时间；
2. `ssh kebab 'sudo kebab-reboot-fastboot'`（连接会断，正常）；
3. 轮询：`until timeout 5 fastboot.exe devices | grep -qi fastboot; do sleep 3; done`，上限 120 秒；
4. `fastboot.exe getvar current-slot` → 必须是 `a`；
5. `fastboot.exe boot <known-good.img>` ← **RAM 启动，零写入**；
6. 等 ssh 回来（上限 8 分钟，每 15 秒探一次）；
7. 把「进城耗时 / 出城耗时 / 镜像 SHA」写进 STATE.md。

**任何一步失败 → 今夜不许 T2**，改为只做 P0、P2 的离线部分，并在报告里写明原因。

### S2 known-good 基线（必须从设备取，不要信仓库里的旧镜像）

    ssh kebab 'sudo dd if=/dev/disk/by-partlabel/boot_a of=/tmp/boot_a.img bs=4M status=none; sha256sum /tmp/boot_a.img'
    scp -F … kebab:/tmp/boot_a.img local/titan-b2/night/known-good/boot_a-<bootid>.img
    本地再算一次 sha256，与设备侧一致才收

以后每次持久化成功，**把新镜像登记为新的 known-good**（回滚目标是「最近的能启动的那个」，不是最老的）。

### S3 候选镜像离线校验（每条都要过，写进 STATE.md）

1. **配置从设备取**：`ssh kebab 'sudo zcat /proc/config.gz'` → 作为 `.config`；
2. **LOCALVERSION 必须一致**：新内核 `uname -r` 必须还是 `7.2.0-kebab-debian13-r4-venus+`。
   **这条是硬要求**：手机靠 USB gadget 提供 192.168.8.1 的 ssh 通道，vermagic 一变，`/lib/modules` 里的模块（含 venus/camss）就加载失败，严重时**网络都回不来**。
3. 反编译新 DTB 复核：`scripts/dtc/dtc -I dtb -O dts`，确认 a5 节点、carveout、无语法错；
4. 镜像大小 < 768 MB；
5. 记录候选 SHA-256。

### S4 先 RAM 启动，再持久化

`fastboot.exe boot candidate.img` → 起来后必须逐项确认：

- ssh 回来；
- `dmesg | grep -iE 'oops|panic|BUG:'` 空；
- `lsmod` 与刷前一致（模块没掉）；
- `modinfo` 的 vermagic == `uname -r`；
- 若加了 a5 节点：`/sys/firmware/devicetree/base/soc/qcom,a5@ac00000` 存在。

**全部通过才允许 flash。** 若 `fastboot boot` 报 `Failed to load/authenticate`（项目里出现过）：这是镜像头/AVB 问题，**不要**改成 flash 绕过，先修镜像。

### S5 持久化：一次一个 generation

`fastboot.exe flash boot_a candidate.img` → `fastboot.exe reboot`。
只写 boot_a；**绝不写 boot_b**；不 `--set-active`；不 `erase`；不刷 dtbo/super/misc。
同一份改动最多刷 **2 次**；整夜 flash 总数 ≤ **6**。

### S6 自动回滚看门狗（flash 前就要启好，别刷完才想起）

    # 伪代码：先启动看门狗，再执行 flash
    flash boot_a candidate
    if wait_ssh 8min:            # 每 15 s 探一次
        mark_good candidate; 更新 known-good; continue
    log "boot failed -> rollback step 1"
    fastboot flash boot_a known-good      # 回滚 1
    if wait_ssh 5min: mark candidate_bad; 本 generation 停止 T2; continue
    log "rollback 1 failed -> rollback step 2 (RAM boot)"
    fastboot boot known-good              # 回滚 2，不写
    if wait_ssh 5min: 标记 flash 通路异常; **今夜停止所有 T2**; 转 RAM-boot only
    write WAKE-ME.md; stop everything

`WAKE-ME.md` 必须包含：当前状态、已刷/未刷什么、回滚命令原文、known-good 路径与 SHA、需要人手做什么（通常是「长按电源 + 音量上 进 fastboot」）。

### S7 熔断

- 同一镜像连续 2 次启动失败 → 标记 bad，回滚，**不再试**；
- 手机 ssh 失联 10 分钟 → 只做只读诊断；30 分钟 → 收工写报告；
- 任何「必须改 boot_b / 必须 erase」的念头 → 立即停止，写报告。

### S8 记录

每次 T1/T2 之前，先把「要做什么 / 回滚目标 / 候选 SHA / boot_id」写进 STATE.md，再动手。任何一次进入 fastboot 都要记时间戳（进城/出城耗时是后续排障的关键数据）。

---

## 5. 执行顺序

### G0 开工自检（10 分钟）
分支/HEAD 正确；ssh 通；建 STATE.md。

### G1 恢复通道演练 + known-good（S1 + S2，约 30–45 分钟）
**这是今晚一切写入的前提。** 演练通过后把结果写进 STATE.md，并在报告里注明「T2 已解锁」。

### G2（P0）离线装载蓝图（2–3 小时）

解 `vendor_a.img` 找相机模块，只回答五个问题：

1. **固件怎么进内存**：搜 `CAMERA_ICP` / `request_firmware` / `elf`；确认是按 ELF program header 拷到 carveout，还是别的。**carveout 已定位：物理 0x8dc00000，5 MiB**（`pil_camera_region@8dc00000`，`no-map`，compatible 为 removed-dma-pool），ICP SMMU 把它映射成 IOVA 0。请核实驱动是按物理地址直写还是经 IOMMU 映射后写。
2. **A5 怎么放复位**：哪个寄存器 / 有没有 SCM 调用 / 有没有 firmware base 寄存器。
3. **三窗口职责**：qgic（中断）/ sierra（**HFI 寄存器在这**）/ csr（控制）。
4. **SMMU 怎么配**：14 个 SID、iova 各区的 start/len、谁编程 CPAS。
5. **顺序**：上电 → 装固件 → 写队列表指针 → 放复位（**队列表指针必须在放复位之前**）。

产出 `docs/camera-titan-b2-load-blueprint-20261112.md`，每条结论带「文件 + 偏移 + 原文」。

### G3（P2）零改动内核的构建链路验证（1–1.5 小时）

1. 取设备 config；
2. 只改**无功能**的一处（或干脆不改），完整构建 + 打包；
3. `fastboot boot` 起来 → 通过 → 说明「构建→打包→启动」全链路可用；
4. **这一步先不要 flash**，省一次写入预算。

若构建不出来（缺工具链/缺 config），立刻记录并转向「只做 P0 + 只读 dump」，不要硬撑。

### G4（P3a）DT 加节点 → RAM 启动 → 持久化（1.5–2 小时）

DT 改动（照抄原厂，值已核对）：

- `reserved-memory`：`no-map` 0x8dc00000 + 0x500000（**先检查与现有 reserved-memory、memory@ 节点无重叠**）；
- `qcom,a5@ac00000`：三窗口 reg、四路 clocks、`power-domains = <&camcc TITAN_TOP_GDSC>`、`interrupts <0 0x1cf 1>`；
- 可选：ICP SMMU client（`iommus`，SID 0x20e2…）；
- `status = "okay"`，且**不要**给它绑一个会抢资源的驱动（今晚用自写探针）。

流程：构建 → `fastboot boot` → 全部检查通过 → **才** `flash boot_a` + 看门狗。

### G5（P3b）上电 + 只读 dump（1–1.5 小时）

有了 DT 节点，走框架最干净：模块里 `devm_clk_bulk_get` + `devm_pm_domain_attach`，按 §10.1 的频率表 set_rate → enable → ioremap 三窗口读。
**顺序**：qgic → csr → sierra；每窗先读 0x00，异常 bail；**做 GDSC off/on 对照**（差值是「块活了」的唯一证据）。
重点地址：`0xac10008/0x10/0x14/0x44/0x58/0x60/0x74`。
**Stage A 绝不写 0xac1xxxx。**

### G6（P4，机会主义）

仅当 G2 给出明确可行路径时才做（例如 A5 有直写口、或固件装载可以不依赖 SMMU）。否则跳过。

### G7 收尾（**07:00 前必须开始**）

1. 手机回干净状态：模块卸载、时钟关、GDSC 关；`dmesg` 无 oops；必要时重启并确认 ssh + 常驻服务回来。
2. 若新内核有不稳定迹象 → **主动回滚到 known-good**，不要留给用户一个半坏的机器。
3. 写 `docs/camera-titan-night-report-20261112.md`：做了什么 / 证了什么 / 没证什么 / 明天第一步。
4. 提交（只 `docs/` 与 `tools/`，固件/dump/镜像留 `local/`）。

---

## 6. 硬边界

1. **只写 boot_a**；boot_b / dtbo / super / misc / persist / EFS 一概不碰；不 `erase`、不 `--set-active`。
2. 不写 persist / 校准数据；不动 `/srv`；不改网络、SSH、密码、限充、温控、看门狗配置。
3. 不装常驻服务/unit/timer；模块用完 `rmmod`。
4. Stage A **绝不写 0xac1xxxx**；不碰 camss 的 `0xac6a000+`。
5. 同一子步骤连续 2 次失败 → 停手、记录、换路线或收工。
6. **每次 T2 之前必须确认看门狗已就绪**；没有看门狗不许 flash。

---

## 7. 证据纪律

- 每个结论带「命令 + 原始输出 + 文件/偏移」，没有证据写「未证」。
- 原始输出落 `local/titan-b2/night/`，带时间戳；每次进/出 fastboot 都记。
- `insmod` 前后各抓一次 `dmesg`。
- 每次改动手机前记录 `boot_id` + 时间 + 目的，收尾对照。
- **刷机相关的一切（镜像 SHA、命令原文、返回码）必须原样落盘**，这是明早复核的唯一依据。

---

## 8. 交付物

| 文件 | 内容 |
| --- | --- |
| `docs/camera-titan-b2-load-blueprint-20261112.md` | P0 装载蓝图（五问 + 证据 + 不确定项） |
| `docs/camera-titan-night-report-20261112.md` | 总报告（含刷机台账：每次 flash 的时间、镜像、SHA、结果、回滚） |
| `local/titan-b2/night/STATE.md` | 断点续作状态 |
| `local/titan-b2/night/known-good/` | 从设备取的 boot_a 镜像 + SHA |
| `local/titan-b2/night/dumps/` | 寄存器 dump |
| `tools/titan-icp/` | 可复用离线脚本（可选） |

---

## 9. 明天交接（写在报告末尾）

1. 若 G5 证实映射：写真正的 ICP 驱动第一步（上电 → 装固件到 0x8dc00000 → 配 SMMU → 写 QTBL 指针 → 放复位）。
2. 若 G5 证伪：重新审视窗口映射（可能 a5_sierra 不是 HFI 所在），先从固件侧的 CPAS 访问点反推。
3. 无论结果如何：B3（IQ 参数来源）仍是出图的硬前置，`com.qti.tuned.default.bin` 格式未解。

---

## 10. 已确证事实（直接抄）

### 10.1 硬件（原厂 DT 实证，dtc 反编译）

    qcom,a5@ac00000 {
        reg = <0xac00000 0x6000  0xac10000 0x8000  0xac18000 0x3000>;
        reg-names = "a5_qgic","a5_sierra","a5_csr";
        reg-cam-base = <0x0 0x10000 0x18000>;   // 就是 reg-0xac00000，非独立证据
        interrupts = <0x0 0x1cf 0x1>;           // GIC SPI 463
        camss-vdd-supply = <&titan_top_gdsc>;   // GDSCR @0xad0c144
        clocks = <&camcc 0x21 0x26 0x28 0x27>;  // 33/38/40/39
        fw_name = "CAMERA_ICP.elf";
        ubwc-ipe-fetch-cfg = <0x707b 0x7083>;  ubwc-ipe-write-cfg = <0x161ef 0x1620f>;
        ubwc-bps-fetch-cfg = <0x707b 0x7083>;  ubwc-bps-write-cfg = <0x161ef 0x1620f>;
    };
    pil_camera_region@8dc00000 { no-map; reg = <0x0 0x8dc00000 0x0 0x500000>; };

| level | soc_fast_ahb | icp_clk_src |
| --- | --- | --- |
| lowsvs | 100 MHz | 400 MHz |
| svs | 200 MHz | 480 MHz |
| svs_l1 | 300 MHz | 600 MHz |
| nominal / turbo | 400 MHz | 600 MHz |

- camcc：`FAST_AHB_CLK_SRC=33, ICP_AHB_CLK=38, ICP_CLK=39, ICP_CLK_SRC=40`
- `titan_top_gdsc`：`.gdscr = 0xc144`，`PWRSTS_OFF_ON`，`POLL_CFG_GDSCR`
- `CAM_CC_ICP_BCR = 0xc070` 是 **reset**（bit0=assert，正常态 0）
- ICP SMMU：`iova-mem-region-firmware` = IOVA 0x0 / 0x500000（正好装下 ELF 的 0x0、0x100000 两段 + 0x150000 堆 + 0x350000 页表）；14 个 SID 见 §5 G2

### 10.2 固件侧 HFI 寄存器（推断，G5 要证）

| A5 地址 | AP（推断） | 用途 |
| --- | --- | --- |
| 0xe0010058 | 0xac10058 | 队列表指针 |
| 0xe0010014 | 0xac10014 | 抬 host 中断（写 1） |
| 0xe0010060 | 0xac10060 | 状态清除（0x10/0x20/0x120） |
| 0xe0010044 | 0xac10044 | fwEntryFunction 读 |
| 0xe0010074 | 0xac10074 | MemMapUmdCdmRegions |
| 0xe0040000 / 0x3c | 0xac40000 / 0xac4003c | **CPAS**（B0 曾误标 CDM） |

**不能外推**：固件还摸 0xe0070000 / 0xe0090000 / 0xe00bd000，按同规则会落到 csiphy3(0xac70000) / 0xac90000 / 0xacbd000，显然不是 CDM —— 那几段是 CPAS 内部视图。

### 10.3 HFI 协议（B1 已固化，直接用 tools/titan-hfi/）

- 队列头 956 B / 表头 24 B / 3 队列（cmd, msg, dbg）/ 索引以 4 字节字计 / 单包上限 6019 B
- 包类型：SYS 0x10001–0x10006；MSG 0x20001+（EVENT 0x20008）；IPE/BPS 0x01010008 / 0x0101000A / 0x0101000E
- **async 命令包与上游差 4 字节**：nUserArg1@12、nUserArg2@20、nNumHandles@28、hHandles@32、payload@32+4n
- opcode 1–0xF 负载：840 / 2360 / 16 / 8 / 1600 / 14328 / 16 / 8 / 16 / 16 / 16 / 16 / 16 / 20 / 20





