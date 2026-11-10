# SSC 传感器申报（SMEM130）：从 19 项到 60 项

日期：2026-11-10。适用范围：OnePlus 8T（kebab）主线 Debian 13，内核 7.2，原厂 r31 listener。

## 一、结论

| 传感器 | 状态 |
|---|---|
| 加速度计 BMI260 | **可用**（SSC 实例 bmi26x） |
| 磁力计 MMC5603 | **可用**，带倾斜无关的绝对方位（另见磁力计文档） |
| 前摄光感 STK33502 | **可用**，5.2 Hz，输出 lux |
| 接近光 STK33502 | 已申报、实例在，但 on-change 流静止时不发帧，尚未做遮挡验证 |
| 后摄 TCS3408 三路、SX9324 SAR | 原厂态就可用（与本文无关） |

SSC 实例总数：原厂 **19** → 申报加速度计+磁力计 **55** → 再加光感+接近光 **60**。

## 二、机制：为什么原厂之外一个传感器都没有

Qualcomm Sensor Core 不自己发现传感器。它读 **SMEM item 130** 里的 `struct sensor_info`，
由 AP 内核驱动在开机时按设备树填好，DSP 起来后照这张表创建实例：

```
struct sensor_info   { int magic_num; sensor_vector s_vector[11]; sensor_algorithm a_vector[8]; }  6792 B
struct sensor_vector { int sensor_id; sensor_hw hw[3]; }                                          556 B
struct sensor_hw     { u8 sensor_name, bus_number, direction, irq_number; sensor_feature feature; } 184 B
struct sensor_feature{ int reg[10]; int parameter[25]; int feature[10]; }                          180 B
```

一加的原厂驱动是 `oplus_sensor_devinfo.c`（公开源码），它按 `sensor-type` 选 vector 槽位、
按 `sensor-index` 选 hw 槽位：`hw = &chip->s_vector[sensor_type].hw[sensor_index]`。

主线内核没有这个驱动，SMEM130 在这台机上是**全零**——所以除了 DSP 固件里能靠默认参数自启的少数实例，
什么都没注册。反向也说明：把这 6792 字节按原厂 DTBO 的值填回去，就能把传感器"找回来"。

## 三、已申报的四个源

| vector | sensor_id | 型号 | bus | irq | 附带字段 | 参数来自 |
|---|---|---|---|---|---|---|
| 0 | OPLUS_ACCEL=0 | BMI260 0x08 | 0xff | 0xff | — | DT `acc@0` 只给型号 |
| 2 | OPLUS_MAG=2 | MMC5603 0x02 | 0xff | 0xff | parameter[0..17] 软磁矩阵 | DT `msensor@0` |
| 3 | OPLUS_LIGHT=3 | STK3A5X 0x01 | 5 | 136 | feature[0..5] | DT `lsensor@0` |
| 4 | OPLUS_PROX=4 | STK3A5X 0x01 | 5 | 136 | reg[0..4] + feature[0..2] | DT `psensor@0` |

bus/irq 写 0xff 表示"由 registry 决定"，与原厂驱动的 `DEFAULT_CONFIG` 一致；
光感/接近光则给出 DTBO 里的实值（bus 5、irq 136），因为 registry 里没有它们的条目。

参数只认 DTBO 的 **entry-0**：同一份 DTBO 集里 entry-1 是 TCS3701 + irq 122、
entry-2 是 irq 122 + als-type 3，取值不同，选错会静默失败。

## 四、实机结果

| 阶段 | 实例数 | 新增实例 | 磁力计 |
|---|---|---|---|
| 原厂 | 19 | — | 无 |
| + 加速度计 + 磁力计 | 55 | bmi26x ×2、rotv、geomag_rv、gravity、tilt… | 178.7° |
| + 光感 + 接近光 | **60** | proximity(stk33502)、wise_light(stk33502)、dpc、pick_up_motion、amd_oplus | 178.1° |

磁力计读数在换模块后没有退化（航向 178.1°、\|B\| 44.6 µT、三轴齐全）。

## 五、前摄光感怎么用

```
sudo kebab-mag-read.py --datatype wise_light --seconds 2 --rate 5
{"lux": 105.33, "per_sensor": {"wise_light": 11}, "rate_hz": 5.0, ...
 "values": {"wise_light": [105.33, 0.0, 5.0, 0.0, 0.0]}}
```

实例名 `stk33502`（厂商 `oplus`，协议 `sns_ambient_light.proto`），
属性 7 = 0.1 是量化步长；第一项是 lux，其余字段含义未定。

## 六、接近光为什么没有帧

`proximity` 实例存在、rate 表 [5, 10, 100]，但它是 **on-change** 流：没有遮挡变化就不发帧。
静止 20 秒 0 帧是预期行为，不是失败——真正的验收要有人把手挡到屏幕上方。

## 七、限制与回退

- 申报发生在 SLPI 停机时，只写 SMEM130 且要求前像全零；任意一步失败就退回"少几个传感器"，其余传感器照常；
- 卸载时只有在 6792 字节仍等于自己写的镜像时才清零，被别人改过就拒绝覆盖；
- 光感/接近光的 bus/irq 取自 DTBO，换机型或换 DTBO 变体要重新取值；
- 回退到原厂态：卸载模块 + 重启 listener，SMEM130 回到全零，实例退回 19 项。

> 说明：把 SMEM130 填好的看护模块、安装脚本与回退脚本属于本机私有部署（含原厂 DTBO 派生的参数），
> 本文只公开协议流程、结构与参数推导，以及上层读数器的用法。
