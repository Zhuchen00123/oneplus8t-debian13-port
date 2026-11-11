# 主线 CAMSS/VFE 首次成功出图（2026-11-12 夜）

> 结论：**kebab 上主线的相机 ISP 通路已经打通并真实出图**——前摄 imx471 经
> CSIPHY4 → CSID0 → VFE0 RDI0 输出 1928×1088 RAW10，连续三帧、帧间有真实噪声差异。
> 这条路径完全不依赖被 TZ 锁死的 A5/ICP。

## 1. 为什么这条路径重要

此前所有精力都在 A5/ICP（Titan 固件）上，而 A5 的释放权在 TrustZone（见
[A5 启用尝试](camera-titan-a5-bringup-attempt-20261112.md) §9）。但**手机里真正的 ISP
（VFE，图像前端）是 CAMSS 的一部分，主线有开源驱动 `qcom-camss`**，它不需要 ICP。
设备上其实早就绑定好了：`ac6a000.camss` + `/dev/media0` + 14 个 video 节点 + imx471 传感器。

## 2. 完整拓扑（实测）

```
imx471 24-0010  (RAW10 1928x1088)
      |  ENABLED,IMMUTABLE
      v
msm_csiphy4  (MIPI CSI-2 PHY)
      |
      v
msm_csid0  (CSI-2 解码器)
      |  pad1
      v
msm_vfe0_rdi0  (ISP 直通通道)  ->  /dev/video0
```

## 3. 可复现步骤（全部在既有系统上，无需刷机）

```bash
# 1) 传感器与逐级格式：必须用 media-ctl 的 mbus 名，不能用 fourcc！
M="media-ctl -d /dev/media0"
F=SRGGB10_1X10/1928x1088
$M -l '"msm_csiphy4":1->"msm_csid0":0[1]'
$M -l '"msm_csid0":1->"msm_vfe0_rdi0":0[1]'
$M --set-v4l2 '"imx471 24-0010":0[fmt:'$F']'
$M --set-v4l2 '"msm_csiphy4":0[fmt:'$F']'
$M --set-v4l2 '"msm_csiphy4":1[fmt:'$F']'
$M --set-v4l2 '"msm_csid0":0[fmt:'$F']'
$M --set-v4l2 '"msm_csid0":1[fmt:'$F']'
$M --set-v4l2 '"msm_vfe0_rdi0":0[fmt:'$F']'

# 2) 视频节点用 fourcc（pRAA = 10-bit packed RGGB）
v4l2-ctl -d /dev/video0 --set-fmt-video=width=1928,height=1088,pixelformat=pRAA

# 3) 抓帧
v4l2-ctl -d /dev/video0 --stream-mmap --stream-count=3 --stream-to=/tmp/f3.raw
```

## 4. 关键坑（走了近一小时弯路）

`media-ctl --set-v4l2` 的格式参数是 **media bus 名字**（`SRGGB10_1X10`），
不是 V4L2 fourcc（`pRAA`/`RG10`）。用 fourcc 会得到
`Unable to setup formats: Invalid argument (22)`，随后 `STREAMON` 报
`qcom-camss: Failed to start media pipeline: -32`（EPIPE）。
两者看起来像驱动缺陷，其实是参数用错。

## 5. 证据

| 项目 | 数值 |
| --- | --- |
| 单帧字节数 | `2,628,608` = 1088 行 × 2416 字节（1928×10bit 打包 + 行对齐） |
| 三帧总量 | `7,885,824` |
| 帧间差异 | 第0/1帧 **780,424 字节不同**，第1/2帧亦不同 ⇒ 真实传感器噪声，不是静态缓冲 |
| 统计 | min 0 / max 255，mean ≈ 19.3，stdev ≈ 27.4，唯一字节值 131–136 |
| 传感器上电 | `imx471: rails up (vana,vdig,vio), img_clk = 19200000 Hz` |
| dmesg | 成功抓帧时 **无任何 camss 报错** |

> 原始帧已删除（前摄画面），只保留统计值。

**重启后复现验证**：按上面 §3 的步骤原样重跑（干净启动、无遗留状态），
得到 2 帧 `5,257,216` B（2 × 2,628,608），本次启动 `Failed to start media pipeline` 计数 **0**。
⇒ 步骤完整、可复现，不依赖任何遗留配置。

## 5.1 控制项实测（ISP 不只是"出字节"）

传感器子设备 `/dev/v4l-subdev24` 暴露完整控制集：`exposure`(1–1290)、`analogue_gain`(0–800)、
`digital_gain`(256–4095)、`vertical_blanking`(220–64447)、`test_pattern`(0–4)、`link_frequency`、`pixel_rate`。

| 控制 | 实测结果 | 判定 |
| --- | --- | --- |
| `analogue_gain` 0 → 800 | 帧均值 **20.5 → 32.2**，标准差 **30.4 → 48.5**，字节 max 255 | **有效**（增益放大真实噪声） |
| `exposure` 1 → 1290 | 暗场下差异不明显（黑电平主导） | 未判定（暗室不可分辨，非无效证据） |
| `test_pattern` 1/2 | 读回值正确（1/2），但**画面无任何变化** | **无效**：驱动确实写了 `0x0600`，传感器未响应 |
| `horizontal_flip`/`vertical_flip` | 未测 | — |

> `test_pattern` 失效是传感器/驱动层面的细节（imx471 的 `0x0600` 在本机模组上不生效），
> 不影响主结论；也意味着**不能用彩条做自校验**，只能用增益这类可观测控制来证明通路是活的。

## 5.2 可复用脚本

`scripts/camss-frame.sh [帧数] [输出文件]`——把 §3 的步骤固化成一个命令，可直接重跑。

## 5.3 只有一个模式

imx471 驱动（`drivers/media/i2c/imx471.c`）只定义了 **一个模式**：
`1928x1088`（`imx471_modes[]` 长度为 1，link freq 单一 `200000000` Hz）。
所以 1928×1088 不是"默认值"，而是当前唯一可选分辨率；全分辨率（16MP）需要先给驱动加模式表。

## 5.4 吞吐与硬件状态（采集期间实测）

10 帧连续采集：26,286,080 字节 / 0.317 秒 ⇒ **31.5 fps**，camss 报错 **0**。

采集期间与空闲时的时钟/电源对比：

| 项 | 空闲 | 采集时 |
| --- | --- | --- |
| `cam_cc_cpas_ahb_clk` | 0 | **1（Y）** |
| `cam_cc_camnoc_axi_clk_src` | 0 | **1** |
| `cam_cc_camnoc_axi_clk` | 0 | **1（Y）** |
| `titan_top_gdsc` | on | on |

⇒ 采集时相机子系统真的被 runtime-PM 拉起（GDSC + CAMNOC/CPAS 时钟），不是空转。

**与 A5 工作的交叉印证**：camss 驱动在开流时打开的就是 `CAMNOC_AXI` 这一路——正是
[A5 探测](camera-titan-a5-probe-20261112.md)里"不开 CAMNOC 则 A5 寄存器全 0"的那一路。
两条线的硬件前提在这里对上了：正常待机时相机子系统整体 runtime-suspend，
寄存器读到 0 是正常的，不是硬件损坏。

## 6. 边界（不要夸大）

- 走的是 **RDI 直通**：ISP 把 RAW Bayer 原样写内存，**没有做去马赛克/降噪/3A**
  （那是 VFE PIX 通路与 ICP 后处理的事）。
- 分辨率是驱动当前默认的 1928×1088（约 1080p），未枚举/切换其它模式。
- 未做曝光/增益调参（V4L2 controls 未验证）。
- 未验证长时间连续采集的稳定性与功耗。

## 7. 下一步

1. 枚举传感器支持的全部模式（`--list-framesizes` 需要 subdev 枚举支持）。
2. 走 VFE **PIX 通路**（ISP 处理后的 YUV），那是"ISP 工作"的完整含义。
3. 曝光/增益/帧率 controls 验证。
4. 与 ICP 的关系：A5 负责后处理（IPE/BPS），RDI+PIX 已能覆盖基本成像，
   A5 继续留在 TZ 议题里。
