# r4-camera 任务：前摄 + 后主摄（IMX586）

- 状态：待审核，未执行。
- 日期：2026-11-10。
- 候选名：r4-camera（r4 的后续版本；不是 r5，不含 binderfs）。
- 范围：前摄 IMX471 host 出图 + 后主摄 IMX586 驱动/DTS/候选内核。
- 非范围：r5/binderfs、后摄其他三颗（IMX481/GC02M1B/GC5035）、Waydroid、Android 相机 HAL。

本文件可直接作为 worker prompt 使用：从“## 0. 角色与目标”开始全文粘贴即可。

## 0. 角色与目标

你是本项目的相机与内核候选 worker，从空白上下文开始。目标：在 r4 基础上做出 r4-camera 候选内核，打通前摄（IMX471）和后主摄（IMX586）。r5/binderfs 暂时搁置，本任务不要引入 binderfs；不改 r4 分区/服务，不刷写。

## 1. 已核实事实

- 设备：OnePlus 8T KB2000（kebab / SM8250），Debian 13 arm64，现役 r4：7.2.0-kebab-debian13-r4-venus+。
- r4 源码树：/home/zhuchen/oneplus8t-venus-20261105/（linux HEAD 88903750a310、config SHA e2543ac0、LOCALVERSION -kebab-debian13-r4-venus）。只读使用，复制到新目录再改。
- 前摄：imx471 已绑定（24-0010，subdev /dev/v4l-subdev24），DTS 完整（cci1 0x10、csiphy4 4-lane、MCLK4 19.2MHz、reset GPIO144）；media graph 中 imx471→csiphy4 已 ENABLED，csiphy4→csid0..3 未使能。media-ctl/v4l2-ctl/cam/qcam/ffmpeg 已装。
- 后主摄：r4 DTS 注释已给映射——IMX586 wide/main，CCI0 master0，CSIPHY0。树内没有 imx586 驱动，mainline 7.2 也没有。
- 内核配置：CONFIG_VIDEO_IMX471=m、CONFIG_VIDEO_QCOM_CAMSS=m；新增 IMX586 驱动作为 built-in（=y），避免依赖 rootfs 模块；LOCALVERSION 保持不变，便于 fastboot boot 时加载 r4 rootfs 模块。
- 本任务只做前摄 + 后主摄；IMX481 超广角、GC02M1B 黑白、GC5035 微距不做。

## 2. 同类项目可参考源（按优先级）

### 2.1 Sr-0w/hotdog-linux-bringup（最接近，必读）

- 仓库：[hotdog-linux-bringup](https://github.com/Sr-0w/hotdog-linux-bringup)，GPL-2.0。
- 设备：OnePlus 7T Pro（hotdog / SM8150），主摄同为 IMX586。
- 状态：[status.md](https://github.com/Sr-0w/hotdog-linux-bringup/blob/main/docs/status.md) 写明 Cameras Partial——四颗物理传感器都能通过 libcamera 出图，后摄对焦和 IMX471 弹出生命周期可用；生产级 3A/色彩/视频/OIS/闪光灯同步未完成。
- 关键补丁（kernel-checkpoints/clearstaff-403b56c-r181/patches/）：
  - [0090-media-i2c-add-Sony-IMX586-main-camera.patch](https://github.com/Sr-0w/hotdog-linux-bringup/blob/main/kernel-checkpoints/clearstaff-403b56c-r181/patches/0090-media-i2c-add-Sony-IMX586-main-camera.patch)：新增 drivers/media/i2c/imx586.c（1419 行）+ Kconfig/Makefile + hotdog DTS。
  - 0089-media-qcom-camss-add-SM8150-C-PHY-support.patch：CAMSS 的 SM8150 C-PHY 支持。
  - 0084~0088：IMX586 上电时序、VDIG/VANA 供电、GPIO 时序。
  - 0091：IMX586 userspace 元数据。
  - libcamera 补丁：aports/temp/libcamera/0004-libcamera-add-Sony-IMX586-sensor-data.patch。
- 驱动工作模式（务必先读代码确认，不要只看摘要）：
  - 4000x3000 binned RAW10，link freq 698395833，C-PHY 3 trio。
  - 走 CSIPHY1 → CSID0 → VFE0 RDI0；传感器地址 0x1a，CCI0 i2c1，MCLK1。
  - 驱动里硬编码 IMX586_NUM_DATA_LANES=3、V4L2_MBUS_CSI2_CPHY，并检查 bus_type != CPHY 就报错。
- 必读文档：
  - [camera-port-plan.md](https://github.com/Sr-0w/hotdog-linux-bringup/blob/main/docs/camera-port-plan.md)（SM8150 相机硬件图、CAMSS 计划）
  - [status.md](https://github.com/Sr-0w/hotdog-linux-bringup/blob/main/docs/status.md)（当前支持矩阵）
  - [IMX586 evidence](https://github.com/Sr-0w/hotdog-linux-bringup/blob/main/docs/evidence/2026-08-09-mainline616-camera-imx586.md)（黑帧/VFE write master 调试记录，避免重复踩坑）
  - [IMX586 focus evidence](https://github.com/Sr-0w/hotdog-linux-bringup/blob/main/docs/evidence/2026-08-10-mainline616-camera-imx586-focus.md)（对焦/EEPROM 变体）
- 复用限制：
  - hotdog 是 SM8150 + C-PHY 3-lane；kebab 是 SM8250，且 PHY 类型/lane 数尚未确认（vendor DTS 只看到 CSIPHY0，需从原厂 DTBO/寄存器证据确认 D-PHY 还是 C-PHY）。
  - hotdog VANA 3.3V；kebab vendor 用 L7P 2.9V。电源时序不能照抄。
  - 不要直接把 hotdog DTS 搬过来；只复用 imx586.c 的 sensor 逻辑、模式表、控制项、Kconfig/Makefile 结构。

### 2.2 kebab vendor 相机（DTS/电源/GPIO 事实源）

- LineageOS 内核：[android_kernel_oneplus_sm8250](https://github.com/LineageOS/android_kernel_oneplus_sm8250)
  - [kona-camera-sensor.dtsi](https://github.com/LineageOS/android_kernel_oneplus_sm8250/blob/lineage-24.0/arch/arm64/boot/dts/vendor/oplus/kebab/kona-camera-sensor.dtsi)
  - techpack/camera-oplus/...（vendor cam_sensor 代码，仅作参数参考，不要搬进 mainline）
- OnePlusOSS 内核：[android_kernel_oneplus_sm8250](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250)（分支 oneplus/sm8250_u_14.0.0_op8t）
- 从 kebab vendor DTS 可读到的主摄事实（需与原厂 DTBO 交叉核对）：
  - qcom,cam-sensor@0，csiphy-sd-index = <0>，CCI0 master0。
  - 供电：L4P（VIO）、pm8150a_s8（VDIG 1.35V）、L7P（VANA 2.9V）、pm8150_s5（CUSTOM1 2.0V）、L3P（VAF 2.8V）。
  - GPIO：tlmm 94（MCLK0）、78（RESET0）、139（CUSTOM1）、117（VDIG）。
  - 注意：vendor DTS 里 MCLK 属性有重复/疑似复制错误，必须以原厂 DTBO 和实际寄存器为准，不要盲抄。
- 这些参数用于写 mainline DTS 的 supplies/reset-gpios/clock/lanes，不用于复制 vendor cam_sensor 驱动。

### 2.3 通用 V4L2 IMX586 驱动（寄存器/模式参考）

- [youjunl/imx586-v4l2-driver](https://github.com/youjunl/imx586-v4l2-driver)（MIT，通用 V4L2）
- [Thomas-Li-AVSS/IMX586_RPI5_MIPI_DRIVER](https://github.com/Thomas-Li-AVSS/IMX586_RPI5_MIPI_DRIVER)
- [Thomas-Li-AVSS/IMX586_RK35xx_MIPI_DRIVER](https://github.com/Thomas-Li-AVSS/IMX586_RK35xx_MIPI_DRIVER)
- [Thomas-Li-AVSS/IMX586_IMX8x_MIPI_DRIVER](https://github.com/Thomas-Li-AVSS/IMX586_IMX8x_MIPI_DRIVER)
- [Basicname/IMX586-Rock5B](https://github.com/Basicname/IMX586-Rock5B)
- 用途：交叉验证寄存器序列、模式表、曝光/增益控制；平台不同，不能直接当 CAMSS 驱动。

### 2.4 明确没有的

- kebab 主线相机：Nura kebab bring-up [MR !32](https://gitlab.nura.eco/soc/qualcomm-sm8250/linux/-/merge_requests/32) 的功能列表不含相机；[postmarketOS kebab wiki](https://wiki.postmarketos.org/wiki/OnePlus_8T_(oneplus-kebab)) 的 Camera 仍是 Broken；GitHub 上没有 kebab 的 imx586/camss 集成。不要把“hotdog 已通”当成“kebab 已通”。
- mainline 7.2 没有 imx586.c；不要等上游驱动。

## 3. 阶段 0：参考源审计（先做，不写内核代码）

1. 用 gh/curl 拉取或阅读 hotdog 的 0090/0089/0084-0088/0091 补丁、status.md、camera-port-plan.md、IMX586 evidence；记录仓库 URL、commit/tag、许可证、文件路径。
2. 拉取 kebab vendor DTS（LineageOS/OnePlusOSS），提取 IMX586 的 CCI/CSIPHY/MCLK/GPIO/regulators/上电顺序；与原厂 DTBO 交叉核对。
3. 确认 kebab 的 PHY 类型（D-PHY/C-PHY）、data lane 数、link frequency；证据来自原厂 DTBO、vendor 寄存器配置或实测，不接受猜测。若无法确认，停止并报告。
4. 输出“复用/改造矩阵”：
   - hotdog 可直接复用：imx586.c 的 probe/ID/模式表/控制项/Kconfig 结构。
   - 必须改：C-PHY→D-PHY（若 kebab 是 D-PHY）、3-lane→4-lane、link freq、VANA/VDIG/VIO 电压、GPIO/reset/MCLK、CCI/CSIPHY 实例。
   - kebab 独有：DTS 节点、供电轨、CSI lane 映射、CAMSS port。
5. 不要在这一阶段改内核或设备。阶段 0 报告是第一个停点：PHY/lane 结论确认后再进入阶段 2。

## 4. 阶段 1：前摄 host 出图（当前 r4，不改内核）

1. 用 media-ctl 映射 imx471→csiphy4→csidN→vfeX_rdiY→videoN 候选路径，选一条使能。
2. 枚举 sensor 模式（mbus code/分辨率/帧率），选最小模式，设置各 pad 格式。
3. 抓 10 帧 RAW10，转成可查看 PNG（灰度可接受），保存 /srv/camera-lab/（0600）。
4. 记录完整命令到 /srv/camera-lab/front-camera-setup.sh；报告格式/帧统计/控制项/libcamera 支持情况。

## 5. 阶段 2：IMX586 移植 + DTS + 构建

1. 以 hotdog 的 drivers/media/i2c/imx586.c 为起点，保留 GPL 头和作者署名；先尝试在 7.2 上编译，按需做最小 API 适配，不要重写 1419 行。
2. 根据阶段 0 的结论适配：
   - 若 kebab 是 D-PHY：增加 D-PHY/4-lane 支持，去掉“必须 C-PHY/3-lane”的硬检查；保留 C-PHY 路径作为可选。
   - 若 kebab 是 C-PHY：确认 trio/lane 数与 link freq 后复用。
   - 电源序列按 kebab vendor DTS/DTBO 重写（VANA 2.9V、VDIG 1.35V、VIO、CUSTOM1、VAF）。
3. 新增 Kconfig/Makefile 项（CONFIG_VIDEO_IMX586），作为 built-in；不要改 CAMSS 核心，除非确有必要（若必须改，全部相机驱动 built-in 并说明）。
4. DTS：在 cci0 对应总线加 imx586 节点（地址/供电/reset/pwdn/MCLK/lane/link-freq），在 camss 对应 CSIPHY port 加 endpoint，链路与 front 同样式；patch 放 patches/local/ 或 patches/camera/ 并在候选树提交。
5. 配置片段：config/debian13-r4-camera.config，只加 IMX586 所需项；与 r4 config 的 diff 必须可解释（列出每一项）。
6. 候选构建：复制 r4 树到 /home/zhuchen/oneplus8t-r4-camera-20261110，提交 patch，更新 build-info（build_profile "r4-camera"、parent_build、config_sha、kernel_commit），用 scripts/linux/build-kernel.sh 构建 Image.gz/modules/dtb；核对 LOCALVERSION 不变、IMX586=y、r4 其它配置不变。
7. 打包 boot.img：只用 r4 已验证的 ramdisk/cmdline（项目 artifacts 或 r4 部署记录），kernel/ramdisk/header_version 按 config/boot-kb2000.json，但 **dtb_offset 必须显式改为 `0x03000000`**（config 里的 0x01f00000 是 stock 镜像测量值，不能用于 fastboot boot）；unpack_bootimg 独立校验，并确认 ramdisk_end（ramdisk_addr+ramdisk_size）≤ dtb_addr；输出 boot-r4-camera.img + SHA。原 r4 镜像的 `0x1f00000` 与 32 MB ramdisk 重叠，`fastboot boot` 会报 `Failed to load/authenticate boot image: Load Error`；不要用该布局。

## 6. 阶段 3：启动验证（需用户明确批准/维护窗口；不刷分区）

1. fastboot boot 候选；USB 网络起后 SSH；确认 uname 仍是 r4-venus、imx586 已 probe、/dev/media0 出现新 subdev。主机循环会先检查 boot.img 的 ramdisk/dtb 不重叠，并检查 boot_a `successful=1`、`tries>=3`；不满足直接拒绝。
2. 按前摄同样方法配置 IMX586 链路，抓 RAW10 并转 PNG；前摄也复测一次。
3. 正常 reboot 回 r4，核对 boot_id、服务、r4 原状。
4. 若启动失败/无帧：报告 dmesg/日志，不要反复重启或改 DTS 猜；恢复 r4 后停止。

## 7. 禁止

- 不刷分区、不写 boot_a、不改 r4 分区/服务；只 fastboot boot（且需批准）。
- 不引入 binderfs/r5 改动；不改 LOCALVERSION；不启用无关配置。
- 不把 hotdog 的 C-PHY/3-lane 配置直接当 kebab；不把 vendor cam_sensor 代码直接搬进 mainline；不猜 DTS/寄存器。
- 不复制非 GPL 代码；vendor 驱动只作参数参考。
- 相机图片仅本地 /srv/camera-lab，0600，不上传；画面全黑先报告遮挡。
- 不掩盖失败：PHY 类型确认不了、驱动源不可用、ID 读不到、0 帧、启动失败，都停下报告。

## 8. 输出与验收

输出：

1. 结论（成功/部分/失败）。
2. 参考源审计（URL/commit/许可证；hotdog 复用/改造矩阵）。
3. kebab PHY/lane/link-freq 结论与证据。
4. 前摄结果（命令/格式/帧/图片）。
5. 后摄 DTS/驱动/配置 diff。
6. 候选产物 SHA（Image/Image.gz/dtb/boot.img/build-info）。
7. 启动验证结果（若执行）。
8. 未做/待批准/风险。

验收：

- 前摄：当前 r4 上至少一张非全黑 RAW 帧 + 可查看 PNG，命令可重复。
- 后主摄：候选内核上 imx586 成功 probe，至少一张非全黑 RAW 帧 + 可查看 PNG；前摄复测不回归。
- 候选产物完整且可校验；r4 运行系统、分区、服务未被修改。
- 未执行 fastboot boot 时，明确停在“候选产物 + 阶段 0/2 报告”。

## 9. 停点

1. 阶段 0 结束：提交参考源审计 + PHY/lane 结论；确认后再写驱动/DTS。
2. 阶段 2 结束：提交候选产物与 SHA；是否 fastboot boot 需用户明确批准。
3. 阶段 3 若失败：恢复 r4 后停止，不带病重试。
