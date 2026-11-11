# 继续适配摄像头：续作 prompt（2026-11-11）

- 用途：**可直接作为 worker / 新会话 prompt**，从"## 0. 角色与目标"起全文粘贴。本文自包含，不依赖上一轮对话历史。
- 前序：[r4-camera 任务书（已执行完毕，转为历史）](r4-camera-task-20261110.md)、[主报告](r4-camera-20261110.md)、[候选启动与出帧取证 §18.1–18.133](r4-camera-boot-failure-20261111.md)、[fastboot 循环执行说明](camera-fastboot-loop-20261110.md)。
- **权威当前状态**：[local/camera-r4-20261110/STATUS.md](../local/camera-r4-20261110/STATUS.md)（gitignore）。本文与它冲突时以它为准，并回来更正本文。

## 0. 角色与目标

你是本项目的相机续作 worker，从空白上下文开始。**后摄 IMX586 已经在 2026-11-11 凌晨首次真出帧，链路已通**——本轮不是"从零打通相机"，而是四件事，按优先级：

1. **补齐可复现性**（离线，先做）：把真正出帧的那套模块归档、校验、接进自动流程。当前工作盘的模块与实机跑通的不是同一套，见第 2 节。
2. **拿到有光环境下的真实画面**（需人在场 + 维护窗口）：这是唯一挡住"看到照片"的一步，也是最便宜的一步。
3. **前摄在相机 DTB 上复测 + 画质/速率表评估**（一次试机内合并做完）。
4. **持久化决策**（需用户明确批准，本轮默认不做）。

3A/对焦/VAF、IMX481 超广角、GC02M1B 黑白、GC5035 微距、Android 相机 HAL、Waydroid **均不在本轮范围**，除非用户另行要求。

## 1. 已核实事实（不要重新推导，也不要再猜）

### 1.0 本轮起点：设备与仓库的当前状态（2026-11-11 12:53 CST 复核）

| 项 | 值 |
| --- | --- |
| 手机 | 正常 Debian，内核 `7.2.0-kebab-debian13-r4-venus+`，boot_id `d11f99f1-5dd9-40e7-b284-4d0b62e2bec5`，已运行约 3.8 小时 |
| 相机节点 | **不存在**（r4 原始 DTB，相机能力**未持久部署**） |
| 相机模块 | `imx586` 未加载；`imx471` 正常绑定；`qcom_camss` 是 rootfs 原版（679,936） |
| 看门狗 | **已停止**，不会再自动把手机拉进 fastboot；要重跑需显式启动 |
| 目标 | 会话目标已 pause（用户叫停），恢复由用户发起 |
| 仓库 | 分支 `codex/sensors-20261107`，HEAD `e508293`（`eec3310` 相机入库 16 文件 + `e508293` 索引） |

已归档的可复现材料（都在 `local/camera-r4-20261110/`，gitignore，**不入库**）：

- 候选镜像 5 张 + `SHA256SUMS-camera`（18 项全 OK，`verify-sums.sh` 可复核）
- 出帧用的模块：`deploy-cphy/imx586-vana-range.ko`（452,864 B）+ `deploy-cphy/qcom-camss.ko`（839,969 B，已核实**就是**实机跑通那份）
- 设备配置 `device-kernel.config.utf8`（9,121 行，含 `CONFIG_VIDEO_IMX586=m`）+ r4 自带 camss 解压副本 `probes/stock-camss-r4.ko`
- 出帧证据 `frames/rear-20261111-090328.png`（+16 倍拉伸 `rear-x16.png`、`.pgm`）与 `SHA256SUMS-frames`

### 1.1 设备与入口

- OnePlus 8T 中国版 KB2000，代号 kebab，SM8250 / Adreno 650，Debian 13 arm64。
- 现役内核 `7.2.0-kebab-debian13-r4-venus+`，装在内置 `boot_a`。当前 boot_id `d11f99f1-5dd9-40e7-b284-4d0b62e2bec5`（每次重启都会变，不要拿它当"没重启"的判据）。
- SSH：`ssh -F local/ssh/kebab-server.conf kebab`。**所有 ssh 调用必须带 `-o ServerAliveInterval=5 -o ServerAliveCountMax=2`**——`ConnectTimeout` 只管 TCP 连接不管握手，手机重启中 sshd 可能接了连接却不发 banner，ssh 会永久挂死（上一轮挂死两次，每次整轮停摆）。
- fastboot：`E:\adroid\platform-tools\fastboot.exe`。
- 槽位（只读复核，不要写）：`boot_a` active / successful=1 / tries=6 / unbootable=0；`boot_b` inactive / unbootable=1。
- 设备证据目录 `/srv/camera-lab/`（0700，文件 0600）。主机侧帧与原始记录只放 `local/`，**不上传、不入 Git**。

### 1.2 相机链路（实机确认）

| 项 | 后摄 | 前摄 |
| --- | --- | --- |
| sensor | `imx586 21-001a`（CCI0 master0，地址 0x1a） | `imx471 24-0010`（CCI1，地址 0x10） |
| PHY | **C-PHY，3 trio**，`bus-type=1`、`data-lanes=<0 1 2>`、`link-freq=698395833` | D-PHY 4 lane，csiphy4 |
| 路径 | `msm_csiphy0 → msm_csid0 → msm_vfe0_rdi0 → /dev/video1` | `msm_csiphy4 → msm_csid0 → msm_vfe0_rdi0 → /dev/video0` |
| 格式 | 4000×3000 RAW10 `pRAA`，stride **5008**，15,024,000 B/帧 | 1928×1088 `SRGGB10_1X10`，stride **2416**，2,628,608 B/帧 |
| 出帧 | **已实证**（5 帧 75,120,000 B；自动流程 3 帧 45,072,000 B） | **已实证**（在 v2 候选上，mean 16.24） |

- **D-PHY 假设已被实机否定**，候选 `v3c`（D-PHY 4 lane）全零。不要再试 D-PHY 分支。
- **一个 sensor probe 失败会拖住同树另一个 sensor 的媒体链路**（async notifier）。后摄修好后前摄链路自动恢复。所以看到"前摄 1 pad 0 link"先查后摄 probe，不要判前摄坏。
- GPIO（按本机原厂 DTBO 核实，**不是**别的板子）：MCLK0=**tlmm94**、RESET=**tlmm78**、CAM_CUSTOM1=**tlmm139**（模拟域使能）、CAM_VDIG=**tlmm117**（数字域使能）。早期误读为 145/26/156，已更正。
- 供电：`vana = <&pm8008_l7>`、`vdig = <&pm8008_l1>`、`vio = <&pm8008_l4>`。`&cci0_i2c0` 400 kHz，且**必须有 37.5 MHz 时钟赋值**（曾漏）。
- **CSIPHY 侧 lane 必须 0 基**：`csiphy_lanes_enable()` 用 `BIT(pos*2)`，抄 sensor 侧的 1 基会静默关掉物理 lane0 且不报错。

### 1.3 两个决定性修复（已在候选里，不要回退）

1. **VANA 电压请求区间**：驱动原本请求精确 2.900 V（`IMX586_VANA_MIN_UV == MAX == 2900000`），而 PM8008 LDO7 是 `REGULATOR_LINEAR_RANGE(1504000, 0, 237, 8000)`，格点只有 **2.896 / 2.904 V**，取不到 2.9 ⇒ `regulator_set_voltage` 返回 `-EINVAL`，probe 在发第一条 I2C 之前就退：
   ```
   imx586 21-001a: error -EINVAL: failed to set VANA voltage
   imx586 21-001a: failed to power-on the sensor
   imx586 21-001a: probe with driver imx586 failed with error -22
   ```
   改成请求 **2.800–2.904 V 区间**（两端都在格点上）即通过。**问题在请求值，不在 DT**——把 DT 钉成 2896000 同样 `-EINVAL`（那是已撤回的 patch 0006）。
2. **抓帧顺序**：`media-ctl --set-v4l2` 在链路未使能时对每个实体都返回 `EINVAL`（**不是驱动不支持**；每个 subdev 单独 `VIDIOC_SUBDEV_S_FMT` 都成功）。正确顺序：
   ```
   media-ctl -d /dev/media0 -l '"msm_csiphy0":1 -> "msm_csid0":0[1]'
   media-ctl -d /dev/media0 -l '"msm_csid0":1 -> "msm_vfe0_rdi0":0[1]'
   media-ctl -d /dev/media0 -l '"msm_vfe0_rdi0":1 -> "msm_vfe0_video0":0[1]'
   v4l2-ctl -d /dev/video1 --set-fmt-video=width=4000,height=3000,pixelformat=pRAA \
     --stream-mmap=3 --stream-count=5 --stream-to=/tmp/rear.raw
   ```
   `scripts/linux/camera-capture-test.py` 已按此修好（先 `-l` 使能三级链路 → 逐个 `v4l2-ctl --set-subdev-fmt` → `--stream-mmap`）。

### 1.4 黑帧判据（**不要把黑帧当链路失败，也不要把出帧当画质验收**）

上一轮抓到的帧全黑，但已证明是"有增益、没光子"：

| 实验 | 均值 | min/max | 含义 |
| --- | --- | --- | --- |
| exposure=6（最小） | 15.842 | 15/16 | RAW10 黑电平（64/1024 ≈ 16/255） |
| exposure=3018（最大） | 15.847 | 15/16 | **推满无变化 ⇒ 没有光子进来** |
| gain=992（最大） | 15.643 | **10/25** | **噪声被放大 ⇒ 模拟通路在工作** |

拉伸 16 倍后是纯散粒噪声、无大尺度结构。判定"链路通但缺光"用这三条，**不要只看 mean**。本轮要做的就是把手机对着亮处再抓一次，看 mean/min/max 是否离开黑电平、是否出现大尺度结构。

### 1.5 构建与产物

- 候选树（构建机）：`/home/zhuchen/oneplus8t-r4-camera-20261110`，提交 `65305469bd3ba4d22931ff5660845c74adc4bc41`。r4 基线树 `/home/zhuchen/oneplus8t-venus-20261105/`（HEAD `88903750a310`、config SHA `e2543ac0`），**只读，复制到新目录再改**。
- 配置：`config/debian13-r4-camera.config`，相对 r4 只加 `CONFIG_VIDEO_IMX586=m`、`VIDEO_QCOM_CAMSS=m`、`I2C_QCOM_CCI=m`、`V4L2_CCI=m`。**最终形态是模块，不是 built-in**；早期 `=y` 的镜像已弃用。LOCALVERSION 保持 `-kebab-debian13-r4-venus` 不变，以便 `fastboot boot` 时用 r4 rootfs 的模块。
- 补丁：`patches/camera/0001…0005 + 0007 + 0008`。**没有 0006**（把 cam_vana 钉成 2,896,000 µV 的那条，已撤回、编号不复用）。0001 是历史补丁（基线提交已含），不重复应用。0008 是 `csiphy_cphy_data_rate_config()` 选表越界读的钳位修复。
- **可用候选镜像**：`local/camera-r4-20261110/boot-r4-camera-cphy2.img`，SHA-256 `5e4e013f50995c536db57c6f9d518cf782d7e6e586a82506d85eb5a1d10c2e8f`。其余 `v2`（前摄对照）/`v3c`（D-PHY，已否定）/`cphy3`/`v3b` 见 `SHA256SUMS-camera`。
- **打包硬约束**：`dtb_offset` 必须显式 `0x03000000`。r4 原值 `0x1f00000` 与 32 MB ramdisk 重叠（ramdisk_end `0x02eea827`），`fastboot boot` 会报 `Failed to load/authenticate boot image: Load Error`。boot header `id` 必须重算。打包后用 `unpack_bootimg` 独立校验。
- **模块编译三坑**（上一轮全踩过）：① `make` 不构建模块，要 `make modules`；② 不传 `ARCH=arm64` 会得到 x86_64 的 `.o`，insmod 报 `Invalid architecture in ELF header: 62`；③ 用别的树的 `.config` 会 BTF 校验失败、`struct module` 大小不匹配（`.gnu.linkonce.this_module section size must match`）。**唯一可靠办法：把设备 `/proc/config.gz` 拉下来当 `.config`**，已存档 `local/camera-r4-20261110/device-kernel.config.utf8`（9122 行，含 `CONFIG_VIDEO_IMX586=m`）。

## 2. 阶段 0：先补可复现性（离线，不碰手机）

**这是本轮第一件事。自动流程里装的 imx586 还是 VANA 修复之前的版本，直接重跑会原样重现旧故障。**

已核实的两处真实缺口（第三处经复核**不是**缺口，见下）：

| # | 缺口 | 证据 | 后果 |
| --- | --- | --- | --- |
| 1 | **自动流程仍在装 VANA 修复前的 imx586** | `deploy-cphy/imx586.ko` 是 **29,641 B**（修复前构建）；`deploy-cphy/camera-full-diag.py` 里是 `insmod /tmp/imx586.ko` | **自动流程会原样重现 `-EINVAL: failed to set VANA voltage`** |
| 2 | **修复版模块虽已进校验，但没有任何 runner/diag 消费它** | `deploy-cphy/imx586-vana-range.ko`（**452,864 B**，用设备 config 编的）已在 `SHA256SUMS-camera`；但全仓只有临时脚本把它拷出来，`camera-full-diag.py` 仍指 `imx586.ko` | `verify-sums.sh` 全绿 ≠ 在用模块是修好的那份 |

**已排除的疑点（不要再查一遍）**：上一版本文说"真正出帧的 camss 没归档（688,128 B vs 839,969 B）"——**这是误判**。已用两个独立方法核对：① 时间线：整轮只推送过一个 camss（08:08，839,969 B），diag 只 `insmod /tmp/qcom-camss.ko`，出帧前没有任何第二次加载；② 尺寸：把设备自带的 `qcom-camss.ko.zst` 解压后比 `SHF_ALLOC` 段和——stock **601,899**、自编 **610,164**，差 **8,265**，与 `lsmod` 报的 **688,128 vs 679,936 = 8,192** 一致（差的 73 B 是段对齐取整）。⇒ **`deploy-cphy/qcom-camss.ko` 就是实机跑通的那一份**，无需重建。复核脚本：`local/camera-r4-20261110/_cmp.sh`，stock 副本已存 `probes/stock-camss-r4.ko`。

要做的：

1. 定一个明确的装载契约：要么把修复版覆盖成 `deploy-cphy/imx586.ko`，要么改 `camera-full-diag.py` 指向 `imx586-vana-range.ko`。**二者选一，不要两条路并存**——上一轮"设备上跑的是旧脚本"就是因为只改工作副本没同步 `deploy-cphy/`。
2. 同步改 `camera-capture-test.py` 与 diag 的模块清单；`SHA256SUMS-camera` 已重算（18 项全 OK），改完重算。
3. 跑一遍离线桩：`run-sim-tests.ps1`、`run-flow-tests.sh`、`run-all-diag-tests.sh`、`run-triage-tests.sh`、`test-camera-capture-test.py`（8 项）、`test-camera-full-diag.py`（35 项）、`test-triage-camera-results.py`（33 项）。**判据改动必须配测试**——上一轮 16 条缺陷里多数是判据写反/写错。

**停点 A**：阶段 0 结束提交"在用模块清单 + SHA + 校验结果 + 装载契约"。此时不碰手机。

## 3. 阶段 1：有光环境下的真实画面（需人在场 + 维护窗口）

**前置条件，缺一不可**：

- 用户明确批准本次试机，且**在维护窗口内**——候选启动期间 DSH / 酒馆 / 三档会下线。
- 用户在场，能把手机镜头对着亮处（翻过来或开灯）。**上一轮就是因为手机躺在暗处，全部帧都是黑电平。**
- 只读复核 `boot_a`：`successful=1` 且 `tries>=3` 且 `unbootable=0`。不满足**直接拒绝**，不要跑 PON。用 `scripts/linux/boot-slot-state.py` 或设备上的 `/usr/local/sbin/kebab-boot-slot-state`。

步骤（全程 `fastboot boot`，**不写任何分区**）：

1. `powershell -NoProfile -File local/camera-r4-20261110/status-camera.ps1` 看手机在不在。
2. 进 fastboot（PON 入口，helper `/usr/local/sbin/kebab-reboot-fastboot`），然后
   `E:\adroid\platform-tools\fastboot.exe boot local/camera-r4-20261110/boot-r4-camera-cphy2.img`
3. 起来后按阶段 0 定下的装载契约装模块（先 `modprobe -r imx586 qcom_camss`，`modprobe -a videobuf2-common videobuf2-dma-sg videobuf2-v4l2 v4l2-cci`，再 insmod 自编 camss 与修复版 imx586）。**用 `modinfo -F srcversion` 核对装上的确实是自编那份**，不要只看 insmod 返回 0——`File exists` 意味着 rmmod 没释放、随后静默跑的是 rootfs 原版。
4. **请用户把镜头对着亮处**，然后抓帧：
   ```
   ssh -F local/ssh/kebab-server.conf kebab \
     "sudo python3 /tmp/camera-capture-test.py --camera rear --output-dir /tmp/camera-lab --frames 3"
   ```
5. 同一轮里**前摄也复测一次**（`--camera front`），确认相机 DTB 上前摄不回归。
6. 取回帧与 JSON 到 `local/camera-r4-20261110/frames/`，更新 `SHA256SUMS-frames`。
7. `sudo systemctl reboot` 回内置 r4，核对 boot_id 改变、内核仍是 r4 release、相机节点消失、`imx586` 未加载、五业务 active、无新增 failed（既有 `kebab-cnb-domain.service` 是历史遗留，不要归因本轮）。

验收（**按 1.4 的判据，不要只看 ok=true**）：

- 后摄：mean 明显离开 15–16 黑电平、min/max 张开、画面出现大尺度结构，`result` 从 `partial` 变 `pass`，并给出可查看 PNG。
- 前摄：1928×1088、stride 2416、非全黑，不回归。
- 若仍是黑帧：先确认用户真的把镜头对着光了，再复跑曝光/增益三组对照。**不要据此判链路失败**，也不要反复重启猜 DTS。

**停点 B**：拿到受光画面（或明确报告"仍黑 + 三组对照数据"）后停下，报告帧统计与 PNG 路径。

## 4. 阶段 2：画质与速率表（离线分析 + 最多一次试机）

1. **C-PHY 数据率表偏差**：实际 link freq 0.698 Gsps，而 `csiphy_cphy_data_rate_config()` 的表最低档是 2.5 Gsps（patch 0008 已钳位防越界读）。**钳位只防了越界，没解决"选了不匹配的档"**。读源码确认钳位后实际写进硬件的是哪一档、对采样时序有何影响，再用受光帧评估画质（噪声、行/列条纹、色彩）。**不要凭"能出帧"就说速率表没问题。**
2. **前摄 `test_pattern` 无效**：`test_pattern=2`（彩条）设置成功但画面仍是黑电平。查是驱动没写寄存器、还是该模式下 test pattern 不生效。**这是已记录的缺口，不要当链路证据用。**
3. **VDIG 供电归属未确证**：vendor 给主摄 `cam_vdig = pm8150a_s8`，本机原厂 DTBO 是 **1,104,000 µV**（注意旧任务书写的 1.35 V 是错的），mainline sm8250.dtsi 无 PM8150A 描述，暂用同电压的 `pm8008_l1`（前摄在用的那条）。**是否真的共享未证**，chip-id 能读到就说明当前接法可用，但不等于归属已确证。
4. libcamera 0.4.0 能枚举前摄（"Internal front camera"），但告警 imx471 未提供 crop rectangle、无静态 sensor properties、缺 Rotation 控制。后摄的 libcamera 支持未验。

**停点 C**：提交速率表实际选档 + 画质评估 + test_pattern 结论。需要试机时合并进一次窗口，不要为每个问题单独重启。

## 5. 阶段 3：持久化（**默认不做，需用户明确批准**）

当前相机能力**完全没有持久部署**：手机跑的是内置 r4 原始 DTB，无相机节点，`imx586` 未加载，`qcom_camss` 是 rootfs 原版。要常驻必须走一次正式部署（新候选 + 写 `boot_a` + 模块进 rootfs + 开机单元），这属于**新的授权范围**。

若用户批准，硬规则：

- 先备完整 96 MiB 分区镜像，再只写 `boot_a`；不写尾部 / `boot_b` / 两 DTBO / GPT。
- **每次 `fastboot flash boot` 后，首次成功启动必须置成功位**。11/10 的事故就是 r4 从不置成功位，ABL 每次启动递减 retry，约 7 次归零后报"系统文件被损坏，无法开机"——**那不是文件系统损坏，是 A 槽记账耗尽**。恢复用完整分区镜像 `local/kexec-test/boot_a-r4.img`（100,663,296 B，SHA `4838d489afb25402c64c439b35d58701ec4dacecb8790b7cc2ac5`）+ `kebab-mark-boot-success`。
- 置成功位命令默认只读预览，`--apply` 才写，且必须带 `--expected-boot-sha256` / `--expected-boot-bytes` / `--expected-rootfs-uuid bc52fbbe-777b-47c8-84fc-ba56db7a8813`。
- 不 fsck、不 wipe、不重建分区表；先读 GPT 槽位再决定恢复。

## 6. 禁止

- **不刷分区、不写 boot/GPT/super/vbmeta/misc**，除非是明确批准的恢复或部署步骤。自动流程里不得出现 `fastboot flash/erase/format/-w/set_active`。
- **不把 100 MB 的分区镜像当 boot.img 去 `fastboot boot`**。
- 槽位不健康（`successful=0` 或 `tries<3`）时**禁止任何自动重启 / PON 循环**。
- **连续两次启动失败或无 SSH 就熔断**，保留 fastboot 入口，停下报告，不无限重试。
- 不在维护窗口之外跑 boot 模式。
- 不猜 DTS / 寄存器；**一次只改一个变量**；不反复重启试错。
- 不引入 binderfs / r5 改动，不改 LOCALVERSION，不启用无关配置。
- 不复制非 GPL 代码；vendor `cam_sensor` 驱动只作参数参考，不搬进 mainline。hotdog 是 SM8150，电源时序与 C-PHY 配置不能照抄。
- 相机图片仅存设备 `/srv/camera-lab`（0600）与主机 `local/`，**不上传、不入 Git**。
- **不掩盖失败**：0 帧、probe 失败、启动失败、判据不确定，都停下报告。不把 `partial`（画面暗）当链路失败，也不把 `pass` 当画质验收。
- 不改充电 / 温控 / 电源键 / USB SSH / 网络 / 既有基础监控。

## 7. 输出与验收

每轮输出：

1. 结论（成功 / 部分 / 失败），以及**本轮推翻或修正了哪条旧认知**。
2. 阶段 0：在用模块清单 + SHA + 校验结果 + 装载契约。
3. 阶段 1：run 目录、boot_id 前后、槽位状态、前后摄各自的 `raw_bytes`/`stride`/`mean`/`min`/`max`/`result`、PNG 路径、曝光/增益对照（若仍黑）。
4. 阶段 2：速率表实际选档、画质评估、test_pattern 与 VDIG 结论。
5. **失误保留**：本轮自己犯的错、被什么挡下、怎么发现的。上一轮 16 条缺陷的共同形状是"**一个静默失败被翻译成了一个语义完全不同的结论**"，续作要主动找这类形状。
6. 未做 / 待批准 / 风险。

验收：

- 阶段 0：工作盘模块与实机在用的**逐字节一致**，全部进 SHA 清单，离线测试全绿。
- 阶段 1：受光环境下后摄 `result=pass` + 可查看 PNG，前摄不回归，`returned_to_baseline=true`，r4 分区/服务未被修改。
- 阶段 2：速率表与 test_pattern 各有明确结论（含"证不了"的诚实边界）。
- 未获批准时，明确停在"阶段 0 报告"或"候选产物 + 离线分析"，不自行试机。

## 8. 停点汇总

| 停点 | 位置 | 继续的条件 |
| --- | --- | --- |
| A | 阶段 0 结束 | 提交模块清单与校验结果；此时不碰手机 |
| B | 阶段 1 结束 | 需用户批准 + 维护窗口 + 用户在场打光 |
| C | 阶段 2 结束 | 需试机时合并进一次窗口 |
| D | 阶段 3 | **需用户明确批准写分区**，默认不做 |
| 熔断 | 任意阶段 | 连续两次启动失败/无 SSH ⇒ 恢复 r4 后停止，不带病重试 |