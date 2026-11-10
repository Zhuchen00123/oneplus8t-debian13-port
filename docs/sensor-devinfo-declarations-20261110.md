# SSC 传感器申报（SMEM130）：从 19 项到 62 项

日期：2026-11-10。适用范围：OnePlus 8T（kebab）主线 Debian 13，内核 7.2，原厂 r31 listener。

## 一、结论

| 传感器 | 状态 |
|---|---|
| 加速度计 BMI260 | **可用**（bmi26x，±8 g/0.244 mg） |
| 磁力计 MMC5603 | **可用**，带倾斜无关的绝对方位；**附近有铁磁物时航向不可信**（第七节） |
| 前摄光感 STK33502 | **可用**，5 Hz 输出 lux（第五节） |
| 接近光 STK33502 | **可用**：消息号 769，f1=1 遮挡 / 0 无遮挡，f2 是原始计数（第六节） |
| 后摄 TCS3408 三路、SX9324 SAR 两路 | 原厂态就可用，本轮未改动 |
| 气压计 / 霍尔 | **本机没有**（原厂 DTBO 的 `oplus_sensor` 下无对应节点） |
| 相机 / 指纹 / 霍尔 IST8801 | **有硬件、主线无驱动**，不在 SSC 范围内 |

SSC 实例：原厂 **19** → 加速度计+磁力计 **55** → +光感+接近光 **60** → +算法层 **62**。

## 二、机制：为什么原厂之外一个传感器都没有

Qualcomm Sensor Core 不自己发现传感器。它读 **SMEM item 130** 里的 `struct sensor_info`，
由 AP 内核驱动在开机时按设备树填好，DSP 起来后照这张表创建实例：

```
struct sensor_info      { int magic_num; sensor_vector s_vector[11]; sensor_algorithm a_vector[8]; }  6792 B
struct sensor_vector    { int sensor_id; sensor_hw hw[3]; }                                          556 B
struct sensor_hw        { u8 sensor_name, bus_number, direction, irq_number; sensor_feature feature; } 184 B
struct sensor_feature   { int reg[10]; int parameter[25]; int feature[10]; }                          180 B
struct sensor_algorithm { int sensor_id; int parameter[15]; int feature[5]; }                          84 B
```

一加的原厂驱动是 `oplus_sensor_devinfo.c`（公开源码）：按 `sensor-type` 选槽位、按 `sensor-index` 选 hw，
物理源写 `s_vector`、算法写 `a_vector`。主线内核没有这个驱动，SMEM130 在这台机上是**全零**，
所以除了 DSP 固件里能靠默认参数自启的少数实例，什么都没注册。把这 6792 字节按原厂 DTBO 的值填回去就能找回来。

## 三、申报了什么

**物理源（s_vector）**

| vector | sensor_id | 型号 | bus | irq | 附带字段 |
|---|---|---|---|---|---|
| 0 | OPLUS_ACCEL=0 | BMI260 0x08 | 0xff | 0xff | — |
| 2 | OPLUS_MAG=2 | MMC5603 0x02 | 0xff | 0xff | parameter[0..17] 软磁矩阵 |
| 3 | OPLUS_LIGHT=3 | STK3A5X 0x01 | 5 | 136 | feature[0..5]（als-type 等） |
| 4 | OPLUS_PROX=4 | STK3A5X 0x01 | 5 | 136 | reg[0..4] + feature[0..2] |

**算法（a_vector）**，照原厂 DTBO 的虚拟传感器节点：

| 槽位 | 算法 | 字段 |
|---|---|---|
| 0 | pickup_detect | parameter[0]=prox-type 0；feature[0]=is-need-prox 1 |
| 1 | lux_aod | parameter[0..2]=thrd-low 12 / thrd-high 50 / als-type 1 |
| 3 | fp_display | parameter[0]=prox-type 0 |
| 4 | free_fall | 只有 id |

bus/irq 写 0xff 表示『由 registry 决定』，与原厂驱动的 `DEFAULT_CONFIG` 一致；
光感/接近光给 DTBO 实值（bus 5、irq 136），因为 registry 里没有它们的条目。
参数只认 DTBO 的 **entry-0**：同一套 DTBO 里 entry-1 是 TCS3701+irq 122、entry-2 是 irq 122+als-type 3，选错会静默失败。

## 四、逐实例实测：60 项里谁真出数

每个实例单独 enable、抓 1.5 秒、disable，排除 128/130/514/768 控制事件后按消息号统计：

| 结果 | 数量 |
|---|---|
| 出数 | **18** |
| 0 帧 | 33 |
| 主动跳过 | 9（OIS/相机标定/da_test/框架类） |

出数的 18 个：accel 42 帧、gyro 79、mag 78、rotv 6、game_rv 7、geomag_rv 9、fmv 7、gravity 8、
gyro_rot_matrix 8、sensor_temperature 8、wise_light 9、proximity 1、ambient_light_rear 8、rgb_rear 8、
flicker_rear 3、sars 1、op_sar 1、motion_detect 1。

33 个 0 帧分三类，都不是坏了：

- **要人动**：pedometer、pedometer_minute、pick_up_motion、bring_to_ear、facing、st_double_tap、multishake、
  basic_gestures、motion_recognition、tilt、device_orient、elevator_detect、distance_bound、oca_detect、
  sig_motion、rmd、psmd、aont；
- **要相机流**：ois、ois_system、cmc、cmc_oplus、ccd_ttw、ccd_walk、amd、amd_oplus、dpc；
- **校准/框架**：mag_cal、gyro_cal、context_aware、ext_svc、data_acquisition_engine、oplus_monitor。

## 五、前摄光感的字段：不是『首项即 lux』

实测两段：暗环境 `(105.3, 0, 5.0, 0, 0)`，亮环境 `(4040, 2, 416.0, 0, 0)`。
第一项是**原始计数**（随增益档变化），第二项是**增益档**，第三项才是 **lux**。
读数器据此输出 `lux`（第三项）与 `light_raw`（计数与增益）。

## 六、接近光：另一套事件，且不能和光感同时开

`proximity` 不走 1025 测量事件，而是消息号 **769**，字段全是 varint：

```
f1 = 1 遮挡（近） / 0 无遮挡（远）
f2 = 原始 PS 计数（实测近 985–1804，远 0–71）
f3 = status（3 = ready）
f4 = 保留（0）
```

判定依据是一次覆盖实验：盖住屏幕左上角时 `f1` 由 0 翻到 1、f2 同时从 ~50 跳到 ~1800，松手后回到 0/50。

**重要**：光感和接近光**同时使能会互相干扰**——同一条连接里同时开两者，lux 会在 0 与 ~275 之间跳、接近光每秒翻状态；
单独开任何一个都稳定（光感 41 帧稳定 281–295 lux，接近光 15 秒 0 事件）。读数器的 `--datatype` 帮助里已注明。
合理推测是 STK33502 的 ALS/PS 共用一套 ADC，驱动在两种测量间切换时互相污染；没有固件源码，未进一步定性。

## 七、铁磁干扰：|B| 不在 40–54 µT 就说明环境不成立

实测把一块铁板放到手机旁边（手机全程平放不动）：

| 时刻 | \|B\| | 航向 | 芯片三轴读数 |
|---|---|---|---|
| 常态 | 44–46 µT | 177–178° | (−24, −2, −37) µT |
| 铁板靠近 | 92 → 305 µT | 74 → 155° | (−255, 118, −117) µT |
| 铁板移开途中 | 52 → 43 → 32 µT | 265 → 193° | 各轴乱跳 |
| 拿走后未重新校准 | 68–87 µT | 277° | (9, −70, −3) µT |

关键判据：**重力方向一直是 (0, 0, 9.8)**，说明手机没被移动，变的是磁场。铁是软铁，不产生磁场，
只把地磁线导走或聚拢；但强场也会改变 AMR 器件的工作点，所以铁板拿走后读数没有自动恢复——
需要在没有铁磁物的环境里做一次**罗盘校准**（画 8 字）把硬铁偏移重新估出来。

读数器现在发布 `field_ok` 与 `field_note`（阈值 40–54 µT，本地地磁约 46 µT），
越界时 `kebab-mag-ctl human` 直接打一行警告：航向不要采信。

## 八、限制与回退

- 申报发生在 SLPI 停机时，只写 SMEM130 且要求前像全零；任一步失败就退回『少几个传感器』，其余照常；
- 卸载时只有 6792 字节仍等于自己写的镜像才清零，被别人改过就拒绝覆盖；
- 光感/接近光的 bus/irq 取自 DTBO，换机型或换 DTBO 变体要重新取值；
- 回退到原厂态：卸载模块 + 重启 listener，SMEM130 回到全零，实例退回 19 项；
- 光感与接近光不要同时使能。

> 说明：把 SMEM130 填好的看护模块、安装与回退脚本属于本机私有部署（含原厂 DTBO 派生的参数），
> 本文只公开协议流程、结构、参数推导、实测结果与上层读数器用法。
