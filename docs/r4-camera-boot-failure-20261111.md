# 2026-11-11 00:22 候选内核启动失败与设备失联记录

状态：**设备自 00:22:45 起无 USB 枚举、无 fastboot、无 SSH**，需要人工上电/按键恢复；候选镜像 `boot-r4-camera.img` 已定位为唯一变量。

## 怎么读这份文档

- **只想把手机救回来** → 第五节「恢复步骤」；无人值守状态与命令在 [`STATUS.md`](../local/camera-r4-20261110/STATUS.md)。
- **想知道这一夜到底改了什么** → STATUS 开头那张 14 条缺陷表，再往下看 §18.84 起的逐轮取证（越靠后越新）。
- **拿到结果想知道怎么看** → STATUS 的「判读表」「`verdict` 一览」两节，配合 §18.116 的 `irq:` 行。
- **想复核设备证据** → 第二、十节（设备现状、C-PHY 证据链），以及 §18.115（PHY 类型在本机已穷尽取证）。
- 全文按时间顺序写，**越往下越新**；`§18.x` 是逐轮追加的取证小节，编号即顺序。

## 一、时间线（主机侧日志 `local/camera-r4-20261110/boot-run.log`，UTF-16）

| 时间 | 事件 |
| --- | --- |
| 00:22:21 | `fastboot-camera-loop.py --boot-image boot-r4-camera.img --entry pon --camera both` 启动，run 目录 `local/camera-runs/20261111-002222-1d193a` |
| 00:22:22 | SSH 正常，boot_id `2a2a5127-…`，内核 `7.2.0-kebab-debian13-r4-venus+` |
| 00:22:23 | 槽位检查通过：boot_a active=1 successful=1 tries=6 unbootable=0 |
| 00:22:23 | PON helper 写 volatile PON → 重启进 fastboot |
| 00:22:44 | `fastboot -s b5fcd385 getvar product` → `kona`；`fastboot boot boot-r4-camera.img` 返回 0 |
| 00:22:45→00:26:48 | 持续 SSH 探测（每 3 秒），全部失败 |
| 00:26:48 | 脚本判定 "candidate did not reach SSH"，执行 `fastboot reboot`（此时设备已不在 fastboot，命令无效） |
| 00:27:44 | 脚本结束，ok=false |

## 二、设备现状（00:27–00:37 多次复核）

- `fastboot devices`：空（且 `fastboot boot` 成功过，说明不是 fastboot 驱动问题）
- `adb devices`：空
- Windows PnP：无 VID_2A70 / VID_05C6 / VID_18D1 设备，无新 USB 网卡；USB 复合设备只剩键鼠类
- `ping 192.168.8.1`：超时；主机 `192.168.8.2` 接口消失（该接口原为手机 RNDIS 对端）
- `ping 192.168.1.122`（10-20 记录里的手机 wlan0 静态地址）：超时，ARP 无该条目
- 结论：手机既没有进 fastboot，也没有把 USB gadget 枚举出来 ⇒ 卡在启动早期或 USB gadget 未配置

## 三、候选镜像（唯一变量）

| 项 | 值 |
| --- | --- |
| 镜像 | `local/camera-r4-20261110/boot-r4-camera.img`，47,869,952 B |
| SHA-256 | `8b632fa1fbe569b4e17c6f8f551bde91d17af7a663d350c7e4bd11d137fe50fb` |
| 内核 | r4-camera 65305469（r4 88903750 + imx586 驱动 + DTS + 配置） |
| config | `3f1b3aad…`（相对 r4：+CONFIG_VIDEO_IMX586=y，V4L2_CCI/V4L2_CCI_I2C 由 m 升 y） |
| DTB | `5e2bf04c…`，132,897 B（r4 + 后摄节点） |
| 布局 | header v2、page 4096、dtb_offset 0x03000000、ramdisk_end 0x02eea827 |

同一脚本在 **23:24 / 23:27** 用 `boot-r4-dtb48.img`（SHA c50ccdeb，r4 内核 + r4 DTB，只改 dtb_offset）**成功启动并回到 baseline**。⇒ 布局参数 0x03000000 与 `fastboot boot` 流程本身已验证，失败来自这次的内核/DTB 差异。

自检结果（事后离线）：

- `Image.gz` gzip 校验通过，解压 46,598,656 B 与 `Image` 逐字节相等 ⇒ 不是压缩包损坏
- `Image` 与 r4 Image 尺寸相同、16,756,106 字节不同 ⇒ 正常重链接
- DTB `dtc` 可反编译，`camera@1a`、`csiphy0_rear_ep` 节点结构正确
- 内核 System.map 含 25 个 imx586 符号 ⇒ 驱动确实编入

## 四、待验证的假设（按可能性排序）

1. **早期内核挂起**（最可能）：`CONFIG_VIDEO_IMX586=y` 使驱动内建，若其在 init 阶段被探测并卡在不可中断等待，USB 永不初始化；`panic=15` 对非 panic 的挂起无效。
   - 反常点：CCI（`CONFIG_I2C_QCOM_CCI=m`）与 CAMSS（`=m`）都是模块，驱动本应在模块加载后才 probe。该假设需要 dmesg 才能证实。
2. **USB gadget 未配置**（次可能）：内核已起到用户态，但 gadget/网络服务失败，导致无 USB 网卡。
3. **线材/接触**（11-02 记录里出现过同类：换线后才枚举）：若如此，手机可能正常跑到候选内核，只是 USB 不通。

三种情况都需人工接触设备；远程无法区分。

## 五、恢复步骤（给用户）

1. 长按电源键 10–15 秒（`logind.conf.d/90-kebab-server.conf` 已设 `HandlePowerKeyLongPress=poweroff`）→ 关机后再开机。
2. 若长按无效：音量上 + 电源 12 秒进 fastboot（屏幕出现 fastboot 字样），主机执行：
   ```
   fastboot devices
   fastboot reboot
   ```
3. 开机后确认内核为 `7.2.0-kebab-debian13-r4-venus+`、boot_id 变化；`boot_a` 应仍为 successful=1/tries=6（本次 fastboot boot 不写分区，不会消耗重试）。
4. 恢复后先跑 `py scripts/linux/fastboot-camera-loop.py --no-reboot --camera front` 确认前摄与网络正常。

## 六、已准备的安全候选（v2，隔离变量）

- 构建：`r4-camera-isolation`，commit `19ff8d36…`，config `668cdb82…`
- 相对 r4 仅一处：`CONFIG_VIDEO_IMX586=m`（驱动改模块，不内建；`V4L2_CCI` 自动回落 m）
- DTS 恢复为 r4 原样（**不带**后摄节点），DTB 与 r4 逐字节相同
- 用途：先证明"内核侧改动 + 我们的构建流程"能启动；通过后再单独验证 DTS 改动
- 产物：`boot-r4-camera-v2.img`（dtb_offset 仍为 0x03000000）

## 七、后续原则

1. 每次只改一个变量：v2（模块、无 DTS）→ v3（+DTS）→ v4（手动 insmod 模块）
2. 下一次试机前先确认 v2 镜像的 DTB 与 r4 完全一致（SHA 对比）
3. 驱动改为模块后，probe 失败/挂起只影响模块加载，不阻塞系统启动
4. 若 v2 也失联，则问题在构建/配置基线，需用 r4 内核镜像做对照（同一 `fastboot boot` 流程）


## 八、已准备好的三个镜像（均在 local/camera-r4-20261110/）

| 镜像 | SHA-256 | 内容 | 用途 |
| --- | --- | --- | --- |
| `boot-r4-camera.img` | `8b632fa1…` | r4 内核 + imx586=y + C-PHY 3-trio DTS | **本次失联的镜像**，保留作证 |
| `boot-r4-camera-v2.img` | `86b41681…` | r4 内核 + imx586=m，DTB 与 r4 **逐字节相同**（`216c2fa1…`），config 仅多一行 `CONFIG_VIDEO_IMX586=m` | 隔离验证：内核/构建基线是否安全 |
| `boot-r4-camera-v3.img` | `7beedbe0…` | v2 内核 + 后摄 DTS（**D-PHY 4 lane**，`sm8250-oneplus-kebab-v3.dtb` `6dfe58fb…`） | 验证 DTS 是否安全；跑通后手动 insmod 验芯片 |
| `imx586.ko` | `b51c07a2…` | 29,641 B，vermagic 与 r4 一致 | 手动加载，probe 挂起不阻塞启动 |

三镜像布局一致：47,869,952 B、header v2、page 4096、dtb_addr 0x03000000、ramdisk_end 0x02eea827（≤dtb_addr）。

### 关键修正：CSIPHY 侧 lane 必须 0 基

r4 的 DTS 注释（`&camss` 与 `csiphy4_front_ep`）写明：`csiphy_lanes_enable()` 用 `BIT(pos*2)` 组 CTRL5，若把 sensor 侧的 1 基 `<1 2 3 4>` 抄到 CSIPHY 侧，会写 `0x154` 而不是 `0x55`，物理 lane0 关、不存在的 lane 开，PHY 永不锁定且**没有任何报错**。
正确写法：CSIPHY 侧 `data-lanes = <0 1 2 3>` + `clock-lanes = <7>`；sensor 侧才用 `<1 2 3 4>`。
v3 的第一版写反了，已修正（commit `3961a6db…`，DTB 重新编译）。
另补了 CCI0 时钟：`&cci0` 原来只有 `status = "okay"`，没像 `&cci1` 那样把 `CAM_CC_CCI_0_CLK` 指到 37.5 MHz（CCI v2 的 SCL 时序表按 37.5 MHz 算，19.2 MHz 会让请求的 400 kHz 实际跑一半），已按 cci1 的同一修法补上（commit `f39a97a0…`）。

## 九、手机恢复后的测试顺序（每次只改一个变量）

1. 只读确认：`fastboot-camera-loop.py --no-reboot --camera front`，并读 `boot_a` 槽位
2. `boot-r4-camera-v2.img` → 能起、能回 baseline ⇒ 构建/配置基线安全
3. `boot-r4-camera-v3.img` → 能起 ⇒ DTS 安全（D-PHY 端点不会挂 camss）
4. v3 起来后手动 `insmod /tmp/imx586.ko`（配合 `camera-v3-diag.py`）：
   - `dmesg` 看 chip id（0x0586）→ 证明上电时序与 CCI 通
   - `media-ctl` 看 imx586 entity 是否出现
   - `camera-capture-test.py --camera rear` 抓帧 → 有帧即证明 D-PHY 4-lane 假设成立
5. 若 D-PHY 无帧：换 lane 顺序/时钟，再不行才考虑 C-PHY（需要给 CSID 加 `PHY_TYPE_SEL`，mainline 7.2 没有）


## 十、C-PHY 证据链与备选内核（本轮新查）

### 1. mainline 7.2 的 CAMSS 只支持 D-PHY（源码实证）

`drivers/media/platform/qcom/camss/camss.c` 的 `camss_parse_endpoint_node()` 明确拒绝非 D-PHY 端点：

```c
if (vep.bus_type != V4L2_MBUS_CSI2_DPHY) {
    dev_err(dev, "Unsupported bus type %d\n", vep.bus_type);
    return -EINVAL;
}
```

`camss-csid-gen2.c` 写 `CSI2_RX_CFG0`（0x100）时只填 `NUM_ACTIVE_LANES`、`DL0..3_INPUT_SEL`、`PHY_NUM_SEL`，**从不写 `PHY_TYPE_SEL`（bit 24）**。所以 7.2 原版内核即使 DT 写 C-PHY，camss probe 也会直接失败（打印 "Unsupported bus type 1"），不会出帧。

### 2. 社区证据：同款 IMX586 模组用的是三 trio C-PHY

hotdog（一加 7T Pro，SM8150）社区移植仓库 `camera-refs-20261110/hotdog` 的证据文档写明：

- `docs/camera-port-plan.md` §"Main IMX586 identification and C-PHY requirement"：**"The recovered Qualcomm IMX586 mode descriptions specify three C-PHY trios for every mode."**
- `docs/evidence/2026-08-09-mainline616-camera-power-sequence.md` §"SM8150 C-PHY support"：IMX586 用 **3 个 C-PHY trio**，不是 4 条 D-PHY lane；其 r98 版本从高通 GPL 下游内核搬来 SM8150 三相寄存器表，并在 CSID 里选 C-PHY、校验三 trio 端点映射。
- 实测通过：15,024,000 B RAW10 帧 @30 fps 经 CSIPHY1→CSID0→VFE0 RDI0。
- 其 DTS 写法（可直接借鉴）：sensor 端点 `bus-type = <MEDIA_BUS_TYPE_CSI2_CPHY>; data-lanes = <0 1 2>; link-frequencies = <698395833>;`，CSIPHY 端点同样 `bus-type = CPHY; data-lanes = <0 1 2>;`（无 clock-lanes）。**这和我们最初给后摄写的 CPHY 参数完全一致。**

kebab 的 CSIPHY 版本是 **v1.2.1**（`kebab-vendor/kona-camera.dtsi`：`compatible = "qcom,csiphy-v1.2.1"`），比 hotdog 的 v1.1 新。

### 3. 上游 C-PHY 系列可直接落到本树

David Heidelberg 的 `[PATCH v8 0/9] media: camss: Add support for C-PHY configuration on Qualcomm platforms`（2026-06-10）**9 个补丁全部干净应用到本 7.2 树**，分支 `cphy-v8`（HEAD `82e780d1…`），改动 8 个文件、+229/−61 行：

- 引入 `phy_cfg` 把端点 bus-type 传进 CSIPHY/CSID；
- `csiphy-3ph` 增加 C-PHY lane 表（SM8150 `lane_regs_sdm845_3ph`）与奇地址 lane 编程；
- CSID 在 `CSI2_RX_CFG0` 里写 `PHY_TYPE_SEL`；
- 按 C-PHY 修正 link-frequency 计算；
- 放开 `camss_parse_endpoint_node` 的 bus-type 检查。

**限制**：该系列对 `CAMSS_8250` 的 C-PHY 分支是 `lane_regs = NULL; lane_array_size = 0` —— 也就是**没有 SM8250 的 C-PHY 寄存器表**，只有 SM8150 有。所以这一版能在 SM8250 上枚举/连线，但真正出流还需要把厂商 v1.2.1 的 `csiphy_3ph_v1_2_1_reg` 表移植进来（厂商 `cam_csiphy_soc.c` 里按 compatible 选表，表本身在未取到的文件里）。

### 4. 新增产物

| 产物 | SHA-256 | 说明 |
| --- | --- | --- |
| `boot-r4-camera-cphy.img` | `2319d3ce…` | 与 v3 同一 Image.gz（camss 是模块，内核镜像不变），DTB 为三 trio C-PHY（`bus-type = <1>`、`data-lanes = <0 1 2>`、link 698395833） |
| `sm8250-oneplus-kebab-cphy.dtb` | `696af20c…` | 上述 DTB |
| `qcom-camss.ko`（cphy 构建） | `489dd46e…` | 带上游 C-PHY 补丁的 camss 模块，需手动替换设备上的 r4 模块 |
| `imx586.ko` | `b51c07a2…` | 两个分支共用，vermagic 一致 |

分支：`v3-dphy` = `f39a97a0…`（D-PHY 版），`cphy-v8` = `82e780d1…`（C-PHY 版，当前工作树）。

### 5. 试机顺序（修订）

1. `boot-r4-camera-v2.img` → 证明构建基线安全；
2. `boot-r4-camera-v3.img`（D-PHY，用设备自带 r4 camss）→ 看芯片 ID 与是否出帧；camss 是 r4 原版，这条路径最干净；
3. 若 v3 无帧：换 `boot-r4-camera-cphy.img`，起来后先 `rmmod qcom-camss` 再 `insmod /tmp/qcom-camss.ko`（C-PHY 版）+ `insmod /tmp/imx586.ko`；此时应能看到 csiphy0 连线成功，但**预计仍无帧**，因为缺 SM8250 的 v1.2.1 C-PHY 表；
4. 若确认 C-PHY：从厂商内核取 `csiphy_3ph_v1_2_1_reg` 表移植进 `camss-csiphy-3ph-1-0.c` 的 `CAMSS_8250` 分支。


### 6. 追加：高通自家 SM8250 C-PHY 表系列（已应用，仍有缺口）

又找到并干净应用了第二个系列：

`[PATCH 0/6] media: qcom: camss: Add C-PHY support for sa8775p, sa8300 and sm8250`（Anusha Arun Nandi，2026-07-17，patchew `20260717231331.1229693`），6 个补丁全部干净应用，分支 `cphy-v8` 现 HEAD `8595a52c…`：

- `Program CSIPHY common control registers`
- `Prepare CSID for C-PHY support in gen3`
- `Add sa8775p / sa8300 C-PHY 3ph lane config`
- `Dynamic data-rate specific C-PHY register settings` —— 新增按符号率的覆盖表：
  - `datarate_sm8250_3ph_2p5Gsps/3p5Gsps/4p5Gsps`
  - `data_rate_settings_sm8250_3ph`
- `validate local/remote endpoint bus-type`

**缺口（重要）**：这两个系列的作者在 commit message 里说会"为两个平台选择 data-rate 表"，但实际代码里：
1. `csiphy_lanes_enable()` 的 `case CAMSS_8250` 在 C-PHY 分支仍写 `lane_regs = NULL`，只会在流开始时触发 `WARN_ONCE("Missing lane_regs definition!")`；
2. `data_rate_settings = data_rate_settings_sa8775p_3ph` 只出现在 `CAMSS_8300/8775P` 分支，**SM8250 的表定义了却没被选中**；
3. 没有 `lane_regs_sm8250_3ph` 基表（data-rate 表只是覆盖项）。

因此要让 SM8250 C-PHY 真正出流，还差两件事：
- 从厂商内核（`cam_csiphy` 的 v1.2.1 表，`csiphy_3ph_v1_2_1_reg`）移植基表；
- 在 `CAMSS_8250` 的 C-PHY 分支接上基表 + `data_rate_settings_sm8250_3ph`。

这两步是纯离线工作，等手机可用时可直接用 `cphy-v8` 分支构建验证。


## 十一、后摄控制引脚纠正（本轮关键修正）

v3／C-PHY 候选里的 `reset-gpios = <&tlmm 145 …>`、`vana-enable-gpios = <&tlmm 26 …>` 来自与本机无关的板级描述。本机自己的 DTBO overlay（`stock-dtbo/entry-3.dts` 的 `qcom,cam-sensor@0`）与厂商 sensor dtsi 两处独立给出同一组：

| 信号 | GPIO | 厂商 pinctrl 名 | 说明 |
| --- | --- | --- | --- |
| CAM_MCLK0 | 94 | `cam_sensor_rear_0_clock` | MCLK0 |
| CAM_RESET0 | **78** | `cam_sensor_rear_0_reset` | 复位，ACTIVE_LOW |
| CAM_CUSTOM1 | **139** | `cam_sensor_rear_0_avdd` | 模拟电源开关 |
| CAM_VDIG | **117** | `cam_sensor_rear_0_dvdd` | 数字电源开关 |

stock overlay 原文：`gpios = <0xffffffff 0x5e 0x00>, <0xffffffff 0x4e 0x00>, <0xffffffff 0x8b 0x00>, <0xffffffff 0x75 0x00>`，即 94／78／139／117；LOS 厂商 dtsi 的 `gpio-reset = <1>`、`gpio-custom1 = <2>`、`gpio-vdig = <3>` 指向同一组，且 `cam_sensor_rear_0_reset_active` 的 `pins = "gpio78"` 再次印证。

旧的 145／26 会把复位信号打到别的引脚上，传感器不会退出复位，CCI0 上不可能 ACK——即使 PHY 配置全对也拿不到芯片 ID。

已改为：

```dts
reset-gpios = <&tlmm 78 GPIO_ACTIVE_LOW>;
vana-enable-gpios = <&tlmm 139 GPIO_ACTIVE_HIGH>;
custom1-gpios = <&tlmm 117 GPIO_ACTIVE_HIGH>;
```

（`custom1-gpios` 对应驱动里的 CAAM_CUSTOM1/VDIG 开关，power-on 时在 VANA 之后、VDIG 之前拉高，与厂商 `avdd_active`／`dvdd_active` 的顺序一致。）

同时核对过电源：厂商 rear 节点 `cam_vio = L4P`（pm8008_l4，1.8 V）、`cam_vana = L7P`（pm8008_l7，2.9 V）、`cam_vdig = pm8150a_s8`（1.35 V，stock overlay 里该 supply 为 `<0xffffffff>` 即不接管，实际常开），mainline 侧继续用 pm8008_l1 作 vdig（与前摄 imx471 一致），不改变供电路径。

## 十二、SM8250 C-PHY 基表移植（完成）

上游两个系列都没有给 SM8250 一张 C-PHY 基表（`lane_regs = NULL`）。本轮从厂商 SM8250 相机驱动直接移植：

- 源文件：`techpack/camera/drivers/cam_sensor_module/cam_csiphy/include/cam_csiphy_1_2_1_hwreg.h`（LineageOS `android_kernel_oneplus_sm8250`，commit `078365e5…`，SHA-256 `60795796…`），
- 表：`csiphy_3ph_v1_2_1_reg[3][33]`（三个 trio 块，每块 33 条，含 3 条 SETTLE_CNT 低字节/高字节条目），
- 落地：`camss-csiphy-3ph-1-0.c` 新增 `lane_regs_sm8250_3ph[]`（99 条），并把 `case CAMSS_8250` 的 C-PHY 分支接上基表 + `data_rate_settings_sm8250_3ph`。

许可证沿用厂商文件 GPL-2.0；寄存器值逐条照抄，未做推测。

## 十三、本轮产物

| 产物 | SHA-256 | 说明 |
| --- | --- | --- |
| `boot-r4-camera-v3b.img` | `c7886eeb…` | D-PHY 4 lane + 纠正后的 78/139/117 引脚；用设备自带 r4 camss |
| `sm8250-oneplus-kebab-v3b.dtb` | `a7ba5704…` | 上述 DTB |
| `sm8250-oneplus-kebab-cphy2.dtb` | `2cb29975…` | C-PHY DTB（引脚已纠正） |
| `boot-r4-camera-cphy2.img` | `5e4e013f…` | C-PHY 三 trio + 纠正引脚；配 `qcom-camss.ko` 使用 |
| `qcom-camss.ko`（C-PHY 版） | `6c66311c…`（839,817 B） | 含两个上游 C-PHY 系列 + SM8250 基表 |
| `imx586.ko` | `b51c07a2…` | 两个分支共用 |

分支：`v3-dphy` = `3892dec4b`（含引脚修正），`cphy-v8` = `3e3bf2ee`（含引脚修正 + C-PHY 基表）。


## 十四、C-PHY 公共寄存器的第二处缺口（已补）

上游 v8 系列只给 CAMSS_8300/8775P 写了 C-PHY 的公共控制寄存器值（CTRL7=0x5a、CTRL0=0x0e），SM8250 落进 default 分支，仍然写两相值（CTRL7=0x02、CTRL0=0x00）。厂商 `csiphy_common_reg_1_2_1[]` 明确给出：

| 寄存器 | 2PH | 3PH |
| --- | --- | --- |
| 0x0814 CTRL5 | lane_enable | lane_enable（三条 trio = 0x2a） |
| 0x0818 CTRL6 | 0x01 | 0x01 |
| 0x081c CTRL7 | 0x02 | **0x52** |
| 0x0800 CTRL0 | 0x02 | **0x0e**（先写 0x03，延时 1 ms，再写 0x0e） |

并且厂商的 lane 位定义是 `CPHY_LANE_0..2 = BIT(1), BIT(3), BIT(5)`（`cam_csiphy_dev.h`），与上游补丁的 `BIT(pos*2+1)` 完全一致，说明上游的 lane mask 逻辑对 SM8250 是对的，只有公共寄存器这组值缺了。

已在 `camss-csiphy-3ph-1-0.c` 里为 `case CAMSS_8250` 单独分支补上 3PH 值（含 0x03 → 1 ms → 0x0e 的过渡），D-PHY 仍走原来的 0x02/0x00，避免动到已经工作的前摄通路。

新模块 `qcom-camss.ko` SHA-256 `149c075c…`（839,8xx B）；`Image.gz`（`f3391b29…`）与 DTB（`2cb29975…`）未变，所以 `boot-r4-camera-cphy2.img` 仍然有效，只需替换模块。

分支 `cphy-v8` 现在到 `cae0d72e6`。

### 14.1 反汇编核对

`llvm-objdump -d qcom-camss.ko` 在 `csiphy_lanes_enable` 里能直接看到新分支：

```
6fa8: mov  w9, #0x52        ; CTRL7 = 0x52
6fac: str  w9, [x8]
6fcc: mov  w9, #0x3         ; CTRL0 = 0x03
6fd0: str  w9, [x8]
6fd4: bl   usleep_range(1000, 1200)
6fe8: mov  w9, #0xe         ; CTRL0 = 0x0e
6fec: str  w9, [x8]
```

另外在 `csiphy_gen2_config_lanes` 里确认写的是 `base + r->reg_addr`（绝对地址，不再叠加 lane 偏移），所以 3×33 的平表移植语义正确；`CSIPHY_SETTLE_CNT_HIGHER_BYTE` 走 default 写 `reg_data`（该表里是 0），settle≈20 时高字节本来就是 0，不影响。

### 14.2 诊断脚本增强

设备侧 `camera-full-diag.py` 现在会：

1. 先看 r4 自带 camss + `imx586.ko`：dump DT 端点 `bus-type`、CCI 总线列表、对 `/dev/i2c-*` 上 0x08–0x3f 逐个做 ACK 探测（能区分“传感器根本没上电/没退出复位”和“上电了但 PHY 不对”），再看驱动绑定、dmesg、出帧；
2. 再 `rmmod qcom_camss` 换 `qcom-camss.ko`（C-PHY 版）重复一遍，末尾顺带跑一次前摄，确认打了补丁的 camss 不会把前摄弄坏。

### 14.3 传感器寄存器表与社区版逐条一致

把 hotdog 社区仓库（`clearstaff-403b56c-r181/cumulative.patch`，同一颗 SEMCO IMX586 已验证出图）里的驱动抽出来与本轮 `imx586.c` 逐条比对：

| 表 | 条数 | 结果 |
| --- | --- | --- |
| `imx586_init_regs` | 171 vs 171 | **完全一致** |
| `mode_4000x3000_regs` | 94 vs 94 | **完全一致** |

也就是说传感器初始化与 4000×3000 模式的寄存器序列没有移植误差；剩下的差异只有板级部分（供电、GPIO、lane 数）和 `0x0114`（我们按 DT bus-type 在 3 lane C-PHY / 4 lane D-PHY 之间切换，社区版固定 3 lane 且只接受 C-PHY 端点）。

### 14.4 社区侧复查（2026-11-11 01:35）

- C-PHY 系列已出到 **v9**（2026-06-17，David Heidelberg）：相对 v8 只调整了一个 commit 的顺序、去掉了 "Fix lane mask" 的 Cc/Fixes 标签，**没有任何 SM8250 相关的表或公共寄存器值**；系列自述"成功的是 SDM845（一加 6/6T + IMX519），其它 SoC 需要各自补 csiphy 初始化数据"。我们的 SM8250 移植正好是它说的"后续补数据"。
- 主线 OnePlus 8T（kebab）DTS 系列（Frieder Hannenheim，v4 RESEND 2026-11-05）只做 framebuffer/UFS/电池/音量键/UART，没有相机节点。
- 没有找到 kebab/lemonade（SM8250 + IMX586）的主线相机先例；唯一的同类先例仍是 hotdog（SM8150 + 同款 SEMCO IMX586，C-PHY 3 trio 已出图）。

结论：SM8250 的 C-PHY 只能按厂商 v1.2.1 数据自己补，已补的两处（基表 + 公共寄存器）都是照抄厂商值，没有推测成分；剩下靠实测。

### 14.5 无人值守链路（已挂，窗口几经调整，现为 14 小时）

`camera-autowatch.ps1`（当前 14 小时窗口，02:20 起）→ `auto-camera-run.ps1`：

1. watcher 每 45 秒查一次 fastboot / SSH，发现手机即调用 runner；
2. runner 按顺序遍历 **v2 → v3b → cphy2**（v2 命名的来历见第六节，实际是 r4 内核 + 未改 DTB）；
3. 每张候选：若在 r4 就调 `kebab-reboot-fastboot` 进 fastboot（若已在 fastboot 则跳过）→ `fastboot boot` → 等 SSH（最多 15 分钟）→ 打印身份指纹 → 推 4 个文件到 /tmp → 跑 `camera-full-diag.py`（I2C ACK 扫描、设备自带 camss 与新 camss 两步、前摄回归、中断差分、拓扑）→ 结果存 `diag-<时间>-<tag>.json.txt`；
4. 指纹里没有 `CONFIG_VIDEO_IMX586` 就判定"panic 后回退到 r4"，跳过该候选（不拿 r4 的结果冒充）；
5. 任意一步出现 `REAR_RESULT a=1` 或 `b=1` 即停止；若某张候选始终没回到 SSH，直接 break（不连续硬试）。

诊断脚本最后一行固定打印 `REAR_RESULT a=<0|1> b=<0|1>`；JSON 完整输出。两个 PowerShell 脚本都过解析检查，Python 过语法检查；远程命令另过了 `bash -n`。

两个脚本都过了 PowerShell 解析检查，Python 过了语法检查。


## 十五、后摄 DTS 链路逐项核对（2026-11-11 01:35）

把后摄节点涉及的每一条硬件事实都对回厂商/本机 DTBO 后，确认没有遗留的"猜"：

| 项目 | 值 | 依据 |
| --- | --- | --- |
| MCLK0 引脚 | gpio94，function cam_mclk，4 mA | `sm8250.dtsi` 的 `cam_mclk0_default`；厂商 dtsi 同 |
| MCLK0 频率 | 19.2 MHz | 厂商 `clock-rates = <19200000>` |
| 复位 | tlmm 78，ACTIVE_LOW | 本机 DTBO + 厂商 dtsi（见第十一节） |
| VANA 开关 | tlmm 139 高有效 | 同上（厂商 pinctrl 名 avdd） |
| VDIG 开关 | tlmm 117 高有效 | 同上（厂商 pinctrl 名 dvdd） |
| VANA | pm8008_l7，2.9 V | 厂商 `cam_vana = L7P`、`rgltr 2900000` |
| VDIG | pm8008_l1 | 与前摄 imx471 同（厂商 rear 写 pm8150a_s8，但 stock overlay 里该 supply 为 -1、实际常开） |
| VIO | pm8008_l4，1.8 V | 厂商 `cam_vio = L4P` |
| CCI | cci0 / i2c-bus@0 / 400 kHz，37.5 MHz 时钟 | 厂商 `cci-master = <0>`；pinctrl 由 sm8250.dtsi 提供 |
| 地址 | 0x1a | 厂商 `cam-sensor@0` |
| CSIPHY | csiphy0 port@0 | 厂商 `csiphy-sd-index = <0>` |

驱动侧同样核对过：

- `imx586_parse_hw_config()` 要求 bus-type 为 CSI-2、lane 数必须等于 3（C-PHY）或 4（D-PHY）、link-frequencies 必须包含 698395833 —— 我们的两套 DT 分别满足；
- 它先用 `GPIOD_OUT_HIGH` 取得 reset（ACTIVE_LOW ⇒ 物理拉低=复位有效），`power_on()` 末尾再置逻辑 0（物理拉高=释放复位），顺序正确；
- `probe()` 是 `parse_hw_config → power_on → detect(读 0x0586)`，不存在断电读 ID 的问题。

诊断脚本的 I2C 探测范围也从 0x08–0x3f 扩到 **0x08–0x77**，这样模组 EEPROM（厂商节点里的 `eeprom-rear-0`，通常在 0x54/0x5c）也会被扫到——只要模组上电，就一定能看到 ACK，可以立刻把"供电/复位"和"PHY 配置"两类问题分开。

### 15.1 诊断再加两项

- **中断计数差分**：每次抓帧前后各读一次 `/proc/interrupts`，把 `csiphy/csid/vfe/camss/cci` 相关行的计数差记进 JSON（`a_irq`/`b_irq`）。这样即使没出帧，也能判断"PHY 有没有收到数据、CSID/VFE 有没有中断"——中断有涨但没帧，说明链路通了但格式/路由不对；中断完全不涨，说明 PHY 没锁或没数据。
- **media 拓扑**：两步各 dump 一次 `media-ctl -p -d /dev/media0` 的前 45 行，确认 imx586 实体、CSIPHY/CSID/VFE 链路是否按预期建立。

脚本 151 行，已过 `ast.parse` 并同步到 `deploy-cphy/`（`7e412a27…`）。

### 15.2 两个候选在设备上的执行顺序（已核对）

- cphy2 镜像启动时，**设备自带的旧 camss 会拒绝 C-PHY 端点**（没打 v8 补丁），camss 整体 probe 失败、没有 /dev/media0 ——这是预期行为，诊断第一步会返回错误，第二步 `rmmod` 旧模块、`insmod` 新模块后才建图、抓帧。
- v3b 镜像上则是旧 camss 先跑 D-PHY（步骤 A），再换新模块重跑（步骤 B），顺带验证新模块不会破坏 D-PHY 通路。

### 15.3 数据速率表与厂商逐条比对（发现一处小差异，已修）

链路频率的取值路径已核实：`imx586.c` 实现了 `V4L2_CID_LINK_FREQ`（int menu，唯一值 698395833），并在选模式时把它写进控制；`v4l2_get_link_freq()` 在传感器没有 `get_mbus_config` 时回退到该控制，因此 **csiphy 拿到的 link_freq 就是 698395833**，不是按 pixel_rate 推算的 1.82 GHz。于是：

- `csiphy_set_clock_rates()` 的 `min_rate = 698395833/4 ≈ 174.6 MHz`（+15% 余量 ≈ 200.8 MHz），不会触发 "Pixel clock is too high"；
- 数据速率选择器按 `bandwidth >= link_freq` 挑第一项 ⇒ 命中 **2.5 Gsps** 那一组，正好是厂商 `data_rate_delta_table_1_2_1` 的第一组。

比对发现我们的 3.5/4.5 Gsps 两组把 `delay_us` 都写成 0x01，而厂商只在最后一笔（0xBB4）置 0x01。已改成与厂商完全一致，提交 `acfeabecbe58` 并重编：

| 产物 | 哈希 | 说明 |
| --- | --- | --- |
| `qcom-camss.ko` | `17a444fe4b3c46e1c3f408f87d3ecc804178113aa872b80427b2620c8784169c` | 新，已放进 `deploy-cphy/` |
| `imx586.ko` | `b51c07a2…` | 未变 |
| `Image.gz` | `f3391b29…` | 未变（源码只动了一个模块文件） |
| DTB | `2cb29975…` | 未变，`boot-r4-camera-cphy2.img` 继续有效 |

反汇编核对新模块：`mov w9, #0x52` 1 处、`mov w9, #0xe` 2 处（8250 与 8775P 各一），C-PHY 分支完整。另外 **99 条三 trio lane 表与厂商 `csiphy_3ph_v1_2_1_reg` 逐条、同序完全一致**（脚本比对通过）。

### 15.4 启动镜像完整性（DTB 与产物逐字节对齐）

按 boot header v2 的真实布局解析（`dtb_size` 在 0x670、`dtb_addr` 在 0x674，不是常被误记的 0x410）：

| 镜像 | header | dtb_size | dtb_addr | DTB 文件偏移 | DTB SHA-256 | 与产物对比 |
| --- | --- | --- | --- | --- | --- | --- |
| `boot-r4-camera-v3b.img` | v2 / 1660 | 132,845 | 0x03000000 | 0x02d86000 | `a7ba5704…` | D-PHY 产物，一致 |
| `boot-r4-camera-cphy2.img` | v2 / 1660 | 132,821 | 0x03000000 | 0x02d86000 | `2cb29975…` | C-PHY 产物，一致 |

两镜像 DTB 区起始都是 FDT magic `d0 0d fe ed`，`totalsize` 与 `dtb_size` 相符；ramdisk 结束于 0x02d85f3f 附近，DTB 从 0x02d86000 开始，与 `dtb_addr 0x03000000` 不重叠（这正是当初必须用 0x03000000 而非 0x01f00000 的原因）。

也就是说：**待测镜像里的设备树，就是前面逐项分析过的那两个 DTB**，不存在"打包时用了旧 DTB"的可能。

### 15.5 供电序列与稳压器约束（离线核对，排掉一个潜在 probe 失败点）

`imx586_power_on()` 里有一句从社区驱动继承来的 `regulator_set_load(imx586->vdig, IMX586_VDIG_LOAD_UA)`，其中常量是 **1100000**（μA，等于把电压值抄进了负载字段）。查了内核实现，它不会导致 probe 失败：

- `regulator_set_load()` 只有在该 regulator 已经 enable（`enable_count != 0`）时才走 `drms_uA_update()`；这里是 enable 之前调用，直接返回 0；
- 即便走了，PM8008 LDO 没有 `REGULATOR_CHANGE_DRMS` 权限、也没有 `set_load/get_optimum_mode`，`drms_uA_update()` 在第一个判断就返回 0；
- `power_off()` 里的 `regulator_set_load(vdig, 0)` 在 `regulator_disable(vdig)` 之后，同样不触发。

稳压器约束也都对得上：PM8008 L7 范围 1.5–3.4 V，请求 2.9 V ✓；L1（vdig）在 kebab 上被固定为 1.1 V 单点（驱动只设负载、不设电压，所以不会像当年 L1 那样踩 "unsupportable voltage constraints"）；L4（vio）1.8 V 单点 ✓。

上电顺序也与厂商一致：VANA 电压+使能 → VANA 开关(139) 高 → 1–2 ms → VDIG 开关(117) 高 → VDIG 使能 → 1–2 ms → VIO 使能 → 1–2 ms → MCLK 19.2 MHz → 1 ms → 释放复位 → 1–2 ms。复位在 `power_off` 里最先拉低，顺序正确。

### 15.6 I2C 探测改成"先写后读"

原探测是 `I2C_SLAVE` 之后读 1 字节，但有些器件地址 ACK 了、首个寄存器却不可读（返回 EIO），会被误判成 NACK。改成：

- 先 `write(fd, b"\x00")`（写寄存器下标一个字节）——只要器件 ACK 地址就返回 1，记 `ack-w(1)`；
- 写失败再回退读 1 字节，记 `ack-r(n)`；两者都失败记 `nack:<write错误>/<read错误>`。

这样"后摄模组是否真的在总线上"这个判断不会被器件的读语义干扰。脚本以模块方式导入做过冒烟测试：不存在的设备路径返回 `open:[Errno 2]`、`irq_snapshot()` 返回空表、`irq_delta()` 只对已知键求差，均不抛异常。

另外确认了无人值守脚本会把四个文件都推到 /tmp：`qcom-camss.ko`、`imx586.ko`、`camera-full-diag.py`、`camera-capture-test.py`，诊断里调用的 `/tmp/camera-capture-test.py` 不会缺。

### 15.7 4K 模式的时钟链路（VFE/CSID 不会被"像素时钟太高"挡住）

- 传感器 `V4L2_CID_PIXEL_RATE` 固定为 478,900,000（4000×3000@30 的模式值），驱动里是可读控制；
- VFE（`vfe_set_clock_rates`）对 RDI 线取 `pixel_clock * bpp / 64 = 478.9M × 10 / 64 ≈ 74.8 MHz`，加 15% 余量约 86 MHz，SM8250 的 VFE 时钟表足够，不会触发 "Pixel clock is too high for VFE"；PIX 线未连接时 pixel_clock 取 0，不参与取大；
- CSID 与 CSIPHY 用的是同一个 `link_freq = 698,395,833`（传感器 LINK_FREQ 控制），`min_rate = link_freq / 4 ≈ 174.6 MHz`，同样在表内；
- 前摄 1928×1088 的路径完全不受影响（同一套公式、更低的时钟）。

## 十六、可达性复查（2026-11-11 01:50）

手机仍不可达，但这轮把「从电脑还能怎么够到它」查清楚了，并留下一条备用通道。

| 尝试 | 结果 |
| --- | --- |
| fastboot devices | 空 |
| USB PnP（VID_05C6 / VID_18D1） | 0 个 |
| ping 192.168.8.1 / TCP 22 | 超时（PC 当前没有 192.168.8.x 网卡，也没有到该网段的路由） |
| 局域网 192.168.1.0/24 扫 22 端口 | 只有网关 192.168.1.1 |
| kebab.local / kebab-debian13.local 解析 | 返回 198.18.15.3 / 198.18.15.4，是路由器 Clash 的 fake-IP（HINFO 回 RFC8482 也说明是上游 DNS），**不是手机在线的证据**；对这两个地址与 fc00::e1b/e1c 的 TCP 22 都是「连上立刻关」（代理接住 SYN 后无上游），不能当 sshd |

结论：手机既不在电脑 USB 上，也不在这个局域网里。

### 16.1 备用通道：手机插在路由器 USB 口时

11/02 的记录里有一条实测路径——手机插路由器的 USB、路由器持有 192.168.8.2 那一端时，电脑用 -o BindAddress=0.0.0.0 覆盖源地址就能 ssh 到 192.168.8.1：

```
ssh -F .\local\ssh\kebab-server.conf -o BindAddress=0.0.0.0 kebab
```

为了下次不用临时拼参数，已在 local/ssh/kebab-server.conf 里加了 Host kebab-router（HostName 192.168.8.1、User debian、BindAddress 0.0.0.0、同一把私钥与严格主机校验），ssh -G kebab-router 已核对解析结果。

**重要约束**：如果手机是通过路由器接的，只能用它做只读查询（hostname/uname/boot_id/dmesg），**不要**从这条路调 kebab-reboot-fastboot——一旦进了 fastboot，fastboot 口在路由器那侧，电脑看不到，手机会一直停在 fastboot 里等人工处理。

### 16.2 无人值守改成一串三候选（变量隔离优先）

原先的脚本是「v3b，若没帧再 cphy2」。考虑到 11/11 00:22 那次失联至今没有定位，先把**风险最低**的一步排在前面，改成按顺序试三张镜像、拿到后摄帧就停：

| 顺序 | 镜像 | 目的 |
| --- | --- | --- |
| 1 | `boot-r4-camera-v2.img` | r4 内核 + **DTB 逐字节不变**，只多 `CONFIG_VIDEO_IMX586=m`；只证明"候选内核本身能正常启动、SSH 能回来"，不期待后摄 |
| 2 | `boot-r4-camera-v3b.img` | 加后摄 DTS（D-PHY 4 lane）；只多一个变量 |
| 3 | `boot-r4-camera-cphy2.img` | 换成 C-PHY 3 trio DTB；用于验证另一假设 |

每张镜像跑同一套诊断（含设备自带 camss 与新 camss 两步、I2C 扫描、中断差分、拓扑），结果按 `diag-<时间>-<tag>.json.txt` 落盘；只要任意一步出现 `REAR_RESULT a=1` 或 `b=1` 就停止。

脚本同时加了 `-NoPon`（只看不动）与 `-Image <path>`（手工指定单张镜像）两个开关，方便人工复核。两个脚本都过了 PowerShell 解析检查，旧的 8 小时 watcher 已终止并换成新的（确认只有一个 watcher 进程）。

### 16.3 把改动落成可复现的补丁集

此前所有改动只存在于构建机的 git 分支里。现在导出到 `patches/camera/`，并做了可复现性验证：

| 文件 | 大小 | SHA-256 |
| --- | --- | --- |
| `0001-media-i2c-add-imx586-kebab.patch` | 42,902 B | `0be736a8…` |
| `0002-media-camss-cphy-sm8250.patch` | 48,468 B | `e272b6b41a65a43def907616b200dba448033d560e8ff033dbe434d6cfa8e40e` |
| `0003-arm64-dts-kebab-rear-camera.patch` | 5,445 B | `6c11847f2b4544f4f50be29dc6c4f6e28fda6002feb64d39d83513a981ee2241` |

`0002` 是 CAMSS 部分（上游 v8 系列 9 个补丁 + Qualcomm C-PHY 系列 6 个补丁 + 本机补的 SM8250 三相数据 3 个提交）的累积 diff，只动 `drivers/media/platform/qcom/camss/`；`0003` 只动 `sm8250-oneplus-kebab.dts`。`patches/camera/README.md` 里按顺序列了全部 18 个提交的标题与作者分组。

**验证**：在 `65305469b`（r4 + IMX586 驱动）上开一个临时 worktree，`git apply --check` 与 `git apply` 两个补丁都通过，应用后的 `camss-csiphy-3ph-1-0.c` 与 `sm8250-oneplus-kebab.dts` 与 `cphy-v8` HEAD 逐字节相同；worktree 已清理。

补丁内容抽检（应用之后再看一眼，防止导出时漏东西）：

| 检查项 | 0002 | 0003 |
| --- | --- | --- |
| CTRL7 = 0x52 / CTRL0 = 0x0e | ✓ | — |
| `lane_regs_sm8250_3ph` / `data_rate_settings_sm8250_3ph` | ✓ | — |
| `CSID_PHY_SEL_CPHY` / `PHY_TYPE_SEL` / `CAMSS_CPHY_DIVISOR` | ✓ | — |
| C-PHY lane mask offset=1 | ✓ | — |
| `MEDIA_BUS_TYPE_CSI2_CPHY` | — | ✓ |
| 控制引脚 78 / 139 / 117 | — | ✓ |

`0002` 共 28 个 hunk、9 个文件（csid/3ph/csiphy/camss + 头文件），`0003` 只有 kebab 的 DTS。

## 十七、抓帧脚本的后摄路径有三个会让"能出图也判失败"的 bug（已修）

后摄走的是自动发现（前摄是硬编码名字），这条路径从没在真机上跑过，重新审时发现两处错误：

**① 一 pad 传感器的链接被丢掉。** camss 的 sensor 子设备只有一个 SINK pad，`media-ctl -p` 把它的出链打印在 `pad0: SINK` 下面（`-> "msm_csiphy0":0 [ENABLED]`）。原来的解析只在 `pad_dir == "SOURCE"` 时记录链接，于是 `find_link(sensor, "msm_csiphy")` 永远是 None，后摄直接报 `imx586: no csiphy link`。

**② 源 pad 取成了目的 pad。** 修好①之后，代码用 `l1["dst_pad"]`（csiphy 的 SINK pad = 0）去建 csiphy→csid 的链接，但 camss 的 csiphy/csid 都是两 pad，出链必须用 SOURCE pad **1**；`media-ctl -l` 会直接报错，整个抓帧流程仍然失败。

改法：链接记录不再看 pad 方向；源 pad 改从**出链本身**取（`l2["pad"]` 是 csiphy 的出 pad、`l3["pad"]` 是 csid 的出 pad）。

用一个仿真的 `media-ctl -p` 拓扑（sensor 1 pad → csiphy 2 pad → csid 2 pad → vfe0_rdi0 → video0）跑 `discover_rear()`，结果 `csiphy pad 1 / csid pad 1 / video /dev/video0`，断言通过。

脚本三处副本已同步：`scripts/linux/camera-capture-test.py`、`local/camera-r4-20261110/camera-capture-test.py`、`deploy-cphy/camera-capture-test.py`，SHA-256 均为 `6db6c844341e76f4571bee6d3a14b3d8fa30d8cc3bb14e19cbce1ec0ca071c57`。

影响：不修这两处，即使硬件完全正常，后摄也只会得到 `result: fail`，而我们会误以为是 PHY 或模组的问题——正是这次最需要避免的误判。
**③ 用实体名去调 v4l2-ctl。** 发现逻辑返回的是 `media-ctl` 的实体名（`imx586 0-001a`），但 `subdev_mbus()` / `subdev_size()` 把它当成 `-d` 的设备路径传给 `v4l2-ctl`，会直接打不开设备。已改为从拓扑里取该实体的 `device node name`（`/dev/v4l-subdevN`）用于 v4l2-ctl，实体名仍用于 `media-ctl` 的 `-l`/`--set-v4l2`。

修正后脚本 SHA-256 `4dba8bd0f29be07014593baa73766b2815b4be260c45e46a3908078fe3bbb8fe`，仿真拓扑测试输出：`sensor imx586 0-001a /dev/v4l-subdev0 | csiphy pad 1 | csid pad 1 | video /dev/video0`，断言通过。

### 17.1 给抓帧脚本补了离线测试

新增 `scripts/linux/test-camera-capture-test.py`，用一段仿真的 `media-ctl -p` 输出（sensor 1 pad → csiphy → csid → vfe0_rdi0 → video0）把这三个坑钉住：

- `test_discovery`：实体名、`/dev` 节点、csiphy/csid 必须是 **pad 1**、video 是 `/dev/video0`；
- `test_one_pad_sensor_link_is_seen`：sensor 出链即使打印在 SINK pad 下也必须被解析到；
- `test_raw10_decode`：MIPI RAW10 解包（5 字节 4 像素）在 0 与满幅两种输入下都正确；
- `test_pixfmt_map`：`SRGGB10_1X10 → pRAA`、`SBGGR8_1X8 → BA81`。

实跑：`4/4 passed`。以后改这个脚本不用上真机也能验证发现逻辑。

## 十八、结果判读表（手机回来后照着看，避免误判）

诊断产物里同时有 I2C 扫描、dmesg、中断差分、绑定状态，四者互相独立。判读顺序：

| 现象 | 含义 | 下一步 |
| --- | --- | --- |
| I2C 扫不到 0x1a（`nack`） | 模组没上电/没出复位/CCI 不通 | 先查供电与 139/117/78 三个引脚，不要动 PHY |
| 0x1a 有 `ack-w`，但 `/sys/bus/i2c/devices/0-001a/driver` 为空 | 传感器在线，驱动没绑上（probe 失败） | 看 dmesg 里 `chip id mismatch` 或 `failed to power-on` |
| `chip id mismatch: 586!=xxxx` | 通电了但不是 IMX586（或地址上别的器件） | 先确认模组型号，再谈驱动 |
| 绑定成功、`media-ctl -p` 有 `imx586` 实体 | 传感器与驱动都 OK | 问题在 PHY/链路层 |
| 抓到帧但 `max < 32`（`partial`） | 链路通了，画面黑 | 先报"遮挡/无光"，再查曝光与镜头盖（前摄上次就是这个） |
| `max >= 32`、`nonblack_ratio > 0.01` | 后摄真正出图 | 保存 PNG + raw 作为证据 |
| csiphy 中断计数为 0、csid/vfe 也为 0 | 没有 PHY 锁定（lane/时序不对） | 换 C-PHY ↔ D-PHY 候选，或查 lane 数 |
| csiphy 有中断、csid/vfe 无 | PHY 锁上了但路由/格式不对 | 查 `data-lanes`、CSID 的 PHY_TYPE_SEL、mbus code |

补充：驱动在 probe 成功时**不打印任何日志**（`imx586_detect()` 只在失败时 `dev_err`），所以"dmesg 里没有 imx586"不等于失败——以 I2C 应答和 `driver` 软链为准；抓帧成功后才有 `streaming 4000x3000 RAW10 over ...` 这一行。

### 18.1 lane mask 在最终树里复核（一边错就整条 lane 静默关掉）

`camss-csiphy-3ph-1-0.c:1630` 的最终实现：

```c
switch (lane_cfg->phy_cfg) {
case V4L2_MBUS_CSI2_CPHY: offset = 1; break;
case V4L2_MBUS_CSI2_DPHY: lane_mask = CSIPHY_3PH_CMN_CSI_COMMON_CTRL5_CLK_ENABLE; break;
}
for (int i = 0; i < lane_cfg->num_data; i++)
        lane_mask |= BIT((lane_cfg->data[i].pos * 2) + offset);
```

代入本机 DTS：C-PHY `data-lanes = <0 1 2>` → `BIT(1)|BIT(3)|BIT(5)` = **0x2a**，与厂商 `CPHY_LANE_0..2 = BIT(1)/BIT(3)/BIT(5)` 完全一致；D-PHY `data-lanes = <0 1 2 3>` 另加时钟位 → `0x01|0x04|0x10|0x40|0x80` = **0xd5**，与厂商 `DPHY_LANE_0..3 + clk BIT(7)` 一致。

`camss-csiphy.c:270` 在 stream_on 时用 `cfg->csi2->lane_cfg` 取 mask，`num_data` 与 `phy_cfg` 都来自该 endpoint，没有第二处覆盖——即 DTS 写什么这里就配什么，不存在"配了 C-PHY 但按 D-PHY 算"的隐患。

### 18.2 `0x0114` 的 lane mode 语义核对

驱动在开流前写 `IMX586_REG_CSI2_LANE_MODE`，注释里写明语义：**0 = 1 lane，1 = 2 lanes，2 = 3 lanes，3 = 4 lanes**。

- 共享模式表里 `mode_4000x3000_regs[]` 带的是 `{ 0x0114, 0x02 }`（三 lane）；
- hotdog 的原补丁里同样是 `{ 0x0114, 0x02 }`，且从不在运行时改；
- 我们的驱动在写完整张模式表后，按 endpoint 的 `bus-type` 复写一次：C-PHY → `0x02`（与 hotdog 一致），D-PHY → `0x03`（这是为 D-PHY 候选加的实验分支，不是厂商数据）。

开流成功时驱动会打印 `streaming 4000x3000 RAW10 over C-PHY (3 trios)` / `over D-PHY (4 lanes)`，dmesg 里能直接看出走了哪条分支。

### 18.3 直接从 boot.img 里解 DTB 复核，并理清 `bus-type` 的两套枚举

把三张候选镜像的 DTB 段（偏移 0x02d86000，长度 132,845 / 132,821）dd 出来，FDT magic `d00dfeed` 正确，再用 dtc 反编译：

| 镜像 | reset-gpios | vana-enable | custom1 | 后摄端点 bus-type | data-lanes |
| --- | --- | --- | --- | --- | --- |
| v2 | 无 camera@1a（DTB 与 r4 相同） | — | — | — | — |
| v3b | `0x4e`=78 ACTIVE_LOW | `0x8b`=139 ACTIVE_HIGH | `0x75`=117 ACTIVE_HIGH | `0x04` | `<0 1 2 3>` + clock-lanes 7 |
| cphy2 | 同上 | 同上 | 同上 | `0x01` | `<0 1 2>` |

引脚与极性、lane 数都与设计一致——**镜像里的 DTB 就是预期的那份**，不是编译树里的另一个版本。

### 18.4 为什么 DTB 里 C-PHY 写成 `<1>`、D-PHY 写成 `<4>`

这里有两套枚举，容易看错：

| DT 里的 `bus-type`（dt-bindings） | 内核内部的 `enum v4l2_mbus_type` |
| --- | --- |
| `MEDIA_BUS_TYPE_CSI2_CPHY` = **1** | `V4L2_MBUS_CSI2_CPHY` = **6** |
| `MEDIA_BUS_TYPE_CSI2_DPHY` = **4** | `V4L2_MBUS_CSI2_DPHY` = **5** |

`v4l2_fwnode_endpoint_parse()` 用一张转换表把 DT 值映射成内核枚举，所以 DTB 里看到 `<1>` 不是"parallel"，而是 C-PHY；驱动里 `case V4L2_MBUS_CSI2_CPHY:`（值 6）也就能正确命中。`camss.c` 把 `vep.bus_type` 直接存进 `lane_cfg.phy_cfg`，`csiphy_get_lane_mask()` 与 `imx586.c` 用的都是同一个枚举，整条链条只有一次转换、没有第二处比较。

### 18.5 csiphy0 不是"没被用过的空节点"

担心点：这块板子上前摄只走 csiphy4，后摄要用 csiphy0，如果 sm8250 的设备树里 csiphy0 的资源（时钟/寄存器/中断）不全，probe 会失败。查下来不是问题：

- `sm8250.dtsi` 里没有独立的 `csiphy@` 节点，五个 PHY 都在**同一个 camss 节点**下，用 `reg-names = "csiphy0" … "csiphy5"`、`interrupt-names` 和时钟表统一声明；
- `camss.c` 的资源表里 `csiphy0` 的 `.clock = { "top_ahb", "ispif_ahb", "ahb", "csiphy0_timer" }`、`.reg = { "csiphy0", "csiphy0_clk_mux" }`、`.interrupt = { "csiphy0" }` 齐全；
- 端口号直接当 PHY 号用（`csd->interface.csiphy_id = vep.base.port`），`port@0` 就是 csiphy0，不需要额外的设备树节点。

### 18.6 内核镜像比对：三张候选共用同一个 Image，且与 r4 的差异只在配置

把 `artifacts/` 下每次构建的 `Image` / `Image.gz` 全部算了一遍：

| 构建 | Image | Image.gz |
| --- | --- | --- |
| r4（`88903750a310`，现在跑着的那个） | 46,598,656 B `650f34dfc06efd8e…` | 15,308,718 B `8c8f4bcd…` |
| v2 / v3 / v3b / cphy / cphy2 全部 | 46,598,656 B `42e1a3a4c7fdef35…` | 15,308,744 B `f3391b29…` |

两点结论：

1. **三张候选的内核完全相同**——DTS 改动在 DTB 里，camss/imx586 改动在模块里，都不影响 `Image`，所以候选之间只差 DTB（正是"每次只改一个变量"要的）；
2. 与 r4 的 `Image` 不同，但差异只来自配置（`CONFIG_VIDEO_IMX586=m`，以及被它 select 提升的 `V4L2_CCI_I2C`），不是源码改动。

### 18.7 关于 00:22 那次失联的一个可检验假设

挂掉的那张 `boot-r4-camera.img` 用的是 **`CONFIG_VIDEO_IMX586=y`（编进内核）**，而现在的三张候选都是 **`=m`**，只由诊断脚本在系统起来之后 `insmod`。

差别在于：编进内核时，传感器驱动会在启动过程中就 probe（读芯片 ID、上电、开时钟）；作为模块时，这些动作发生在系统完全起来之后。也就是说，**即使后摄这条路径里有会让内核挂死的动作，现在的候选也不会在启动阶段触发它**，最坏情况只是 insmod 之后系统无响应——而那时 SSH 已经在跑，能留下日志。

这只是相关性推断，没有拿到上一次的串口/panic 证据，不能当结论；但它是把三张候选排成"从最保守开始"的实际理由。

### 18.8 逐行比对配置与逐字节比对 Image（修正 18.6 里一处说法）

**配置**：把 r4 的 `kernel.config`（9121 行）与候选构建的（9122 行）逐行比，差异**只有一行**：

```
+CONFIG_VIDEO_IMX586=m
```

也就是说 `V4L2_CCI=m`、`V4L2_CCI_I2C=m`、`CONFIG_VIDEO_QCOM_CAMSS=m` 在 r4 里本来就是这个值——**18.6 里写的"CCI 被提升为内建"属于上一张（00:22 挂掉的那张）配置，不是现在的候选**。那张挂掉的镜像是 `V4L2_CCI=y`、`V4L2_CCI_I2C=y`、`VIDEO_IMX586=y` 三个内建，与 r4 的差异比现在大得多。

**Image**：两个 `Image` 大小完全相同（46,598,656 B），逐字节比共有 65,208 字节不同、分布在 817 段；其中 **63,350 字节（97%）落在 18–19 MB 这一段**，正是内核内嵌的 `/proc/config.gz`（`CONFIG_IKCONFIG=y`，blob 在 19,496,072 处）——配置差一行导致压缩后的 blob 几乎整段变化。其余约 1.8 KB 散布在 rodata 类区域（1–5 MB、16–24 MB、33–44 MB），未逐一归因，最可能是 BTF/字符串表层面的差异；**没有任何与执行路径相关的配置项不同**（`MODVERSIONS`、`TRIM_UNUSED_KSYMS`、`RANDSTRUCT` 均为关）。

结论：v2 与"现在跑着的 r4 内核"在可执行内容上是同一份，只多了一个模块的配置项；这也让"v2 起不来就说明引导流程有问题、而不是内核"这个判断成立。

### 18.9 boot.img 头逐字段对比（和"能启动的那张"比）

拿 `local/kexec-test/boot-r4-known-good.img`（SHA `8d409dfd…`，公认能启动）当基准：

| 字段 | r4 known-good | v2 | v3b | cphy2 |
| --- | --- | --- | --- | --- |
| magic / hdr / page | ANDROID! / v2 / 4096 | 同 | 同 | 同 |
| kernel_size | 15,308,718 | 15,308,744 | 同 v2 | 同 v2 |
| kernel_addr | 0x8000 | 0x8000 | 0x8000 | 0x8000 |
| ramdisk_size | 32,417,831 | 同 | 同 | 同 |
| ramdisk_addr | 0x01000000 | 同 | 同 | 同 |
| tags_addr | 0x100 | 同 | 同 | 同 |
| os_version | 402653576 | 同 | 同 | 同 |
| dtb_offset（文件内） | 0x2d86000 | 0x2d86000 | 0x2d86000 | 0x2d86000 |
| dtb_size | 132,213 | 132,213 | 132,845 | 132,821 |
| **dtb_addr** | **0x1F00000** | 0x3000000 | 0x3000000 | 0x3000000 |
| 文件大小 | 47,869,952 | 同 | 同 | 同 |

四张镜像在 0x2d86000 处都是 `d0 0d fe ed`，FDT totalsize 与头部 `dtb_size` 一致。

### 18.10 更正：`dtb_addr` 在 `fastboot boot` 这条路上**确实是硬约束**

（这一节推翻本节早前版本里"ABL 不看这些字面地址、dtb_addr 无所谓"的说法——那个结论只对"从分区正常启动"成立，对我们实际用的 `fastboot boot` 不成立。）

把内存区间算清楚：ramdisk 载入地址 0x01000000、大小 32,417,831 → 占 **0x01000000–0x02EEA827**；r4 自己那张镜像的 `dtb_addr = 0x1F00000` 落在区间内部。

- **从分区正常启动**：r4 镜像用的就是这个值，照样能开机——说明这条路径上 ABL 不用 `dtb_addr` 摆放 DTB；
- **用 `fastboot boot` 临时启动**：这条路径上 ABL 会做重叠校验，`dtb_addr` 落在 ramdisk 区间内就报 **Load Error**。这是 2026-11-10 23:24/23:27 实测过的（见 [camera-fastboot-loop-20261110.md](camera-fastboot-loop-20261110.md) 第 26 行："原 r4 镜像 dtb 地址 0x1f00000 与 32 MB ramdisk 重叠，`fastboot boot` 会报 Load Error；必须用 0x03000000"），同文档第 56 行也写明打包必须显式 `--dtb_offset 0x03000000`。

对照本机三张候选：`dtb_addr = 0x03000000`（50,331,648）> ramdisk_end `0x02EEA827`（49,210,407），**满足约束**，`dtb_addr + dtb_size` 也仍在文件尾部之前；头部其余字段与 known-good 逐项相同。因此镜像封装本身**不是** 00:22 那次失联的原因。

教训记录：上一版本节把"从分区启动时地址被忽略"错误地外推到了 `fastboot boot`，差点让"重新打包成 0x1f00000"变成一个看似更"贴近原厂"的改动。这类改动一旦做下去，会直接变成 Load Error。
### 18.11 让脚本自己分辨"跑的是我们的镜像"还是"panic 后回退到 r4"

`fastboot boot` 之后如果候选内核 panic（cmdline 里有 `panic=15`），手机会自动重启进 r4，SSH 照样会起来——脚本分不清就会把我们镜像的诊断结果写成 r4 的，反过来也会把 r4 的失败算到候选头上。

利用候选与 r4 配置的唯一差异做指纹：r4 的内核里**没有** `CONFIG_VIDEO_IMX586`，候选的是 `=m`，而内核把自己配置内嵌在 `/proc/config.gz`（`CONFIG_IKCONFIG_PROC=y`）。于是每次启动后先查：

```sh
cat /proc/sys/kernel/random/boot_id; uname -r
{ zcat /proc/config.gz 2>/dev/null | grep CONFIG_VIDEO_IMX586 || echo imx586_cfg=absent; }
printf dtb_camera=; ls -d /proc/device-tree/soc@0/*/i2c@*/camera@1a 2>/dev/null | wc -l
printf lsmod_imx586=; lsmod | grep -c imx586; true
```

候选上打印 `imx586_cfg=CONFIG_VIDEO_IMX586=m`，r4 上打印 `imx586_cfg=absent`；两种情况下命令都以 0 退出（末尾 `true`，避免 `grep -c` 无匹配时把整条命令变成非零退出）。这条命令在 WSL 里实跑验证过输出格式与退出码。

三项一起打印进日志；若 `CONFIG_VIDEO_IMX586` 缺失，脚本判定"回退到了 r4"，**跳过这张候选**继续下一张（不会拿 r4 的结果冒充候选结果）。

### 18.12 `fastboot boot` 失败时立刻停，而不是等 15 分钟 SSH

原先脚本在 `fastboot boot` 之后无条件 `WaitSsh 90`（15 分钟）。如果 ABL 直接拒绝（例如 DTB 地址重叠导致 `FAILED (remote: 'Load Error')`），手机其实还停在 fastboot，等 SSH 纯属浪费——而且这段时间里 8/14 小时窗口在被消耗。

现在 boot 之后先看两件事：`$LASTEXITCODE` 与输出里是否出现 `FAILED` 或独立的 `error`（`\berror\b`，避免误伤正常输出）。命中就记一行日志并 break。三种真实输出的判定已核对：

| 输出 | 判定 |
| --- | --- |
| `Booting OKAY / Finished. Total time` | 继续等 SSH |
| `Booting FAILED (remote: 'Load Error')` | 立即停 |
| `fastboot: error: cannot load ...` | 立即停 |

### 18.13 给无人值守的 ssh 加存活检测，避免内核中途挂死把 watcher 拖住

风险场景：诊断脚本跑到一半内核挂死。内核卡住时 sshd 也发不出数据，客户端会**无限等待**——watcher 的 14 小时窗口就全耗在这一条命令上，而且日志里看不出卡在哪。

做法：给三条长时间会话的 ssh 加 `-o ServerAliveInterval=15 -o ServerAliveCountMax=4`。15 秒一次保活、连续 4 次无响应即断开（约 60 秒），脚本就能继续往下写日志、判定失败，而不是整体僵住。`ConnectTimeout=6` 管的是"连不上"，保活管的是"连上了但对面不再说话"，两者互补。

已重新生成 `SHA256SUMS-camera`（02:26）并 `sha256sum -c` 复核。

### 18.14 厂商 GPIO 表逐项对照：139 其实是 CUSTOM1、117 是 VDIG（我们名字取反了，但电平没错）

厂商 overlay（`entry-3.dts` 里后摄模组的 eeprom@0 节点）写得很明确：

```
gpios = <0xffffffff 0x5e 0x00   /* CAM_MCLK0 : 0x5e = 94  */
         0xffffffff 0x4e 0x00   /* CAM_RESET0: 0x4e = 78  */
         0xffffffff 0x8b 0x00   /* CAM_CUSTOM1: 0x8b = 139 */
         0xffffffff 0x75 0x00>  /* CAM_VDIG  : 0x75 = 117 */
gpio-reset = <1>; gpio-custom1 = <2>; gpio-vdig = <3>;
gpio-req-tbl-label = "CAM_MCLK0", "CAM_RESET0", "CAM_CUSTOM1", "CAM_VDIG";
```

而我们的 `imx586` 节点写的是 `vana-enable-gpios = <139>`、`custom1-gpios = <117>`——**两个名字与厂商相反**（139 是 CUSTOM1、117 是 VDIG），MCLK 94 与复位 78 一致。

为什么这不影响功能：驱动 `imx586_power_on()` 依次把 `vana_enable_gpio`（139）和 `custom1_gpio`（117）都拉高，两个引脚最终都是高电平，与厂商的 `enable_active_high` 语义一致；差别只在属性命名的可读性。只要不去"按名字"单独关闭其中一路，就不会少一根使能。

顺带记下电压口径：厂商该节点的 `rgltr-min-voltage` 里 `cam_vdig` 是 **1,350,000 µV**，但同一个节点 `cam_vdig-supply = <0xffffffff>`（未接），这组数值更可能是从别的模组抄来的模板；IMX586 的 DVDD 规格是 1.1 V 典型（0.99–1.21），所以我们用 pm8008 L1 的 1.1 V 是物理上合理的选择。**这一点仍未实测**，见下条。

### 18.15 诊断脚本新增稳压器快照，直接验证 VDIG 那条假设

`camera-full-diag.py` 增加 `regulators()`，从 `/sys/class/regulator/*` 读 `name`/`microvolts`/`state`/`num_users`/`max_microamps`，只保留与相机相关的（l1p/l4p/l7p/cam/vreg_l），并在两个时点采样：

- `regulators_boot`：系统刚起来、驱动还没上电时的基线；
- `regulators_after_power`：`imx586` 已 probe / 已上电之后——如果 L1 显示 1.1 V 且 `state=enabled`，说明 VDIG 这条路按设计走通；如果是 disabled 或电压不对，说明供电假设错了，要改的是 DTS 而不是 PHY。

另外还抓一份 `/sys/kernel/debug/regulator/regulator_summary` 里含 l1p/l4p/l7p/cam 的行（debugfs 没挂时退化成一行说明）。诊断脚本已 `py_compile` 通过，本地与 `deploy-cphy/` 两份逐字节相同（`7db5c8ff…`），清单 16 项 `ok=16 bad=0`。

### 18.16 与"能用的那个"逐项比对，发现控制引脚没有 pinctrl 状态（已补，第 4 张候选）

把 hotdog 的后摄节点（同款 IMX586 模组、社区验证可用）和本机的逐项对照，发现一处此前没注意到的差别：

```dts
/* hotdog */
vana-enable-gpios = <&tlmm 11 GPIO_ACTIVE_HIGH>;
custom1-gpios     = <&tlmm 29 GPIO_ACTIVE_HIGH>;
custom2-gpios     = <&pm8150l_gpios 1 GPIO_ACTIVE_HIGH>;
pinctrl-0 = <&cam_mclk1_default>, <&cam0_vana_default>,
            <&cam0_custom1_default>, <&cam0_custom2_default>, <&cam0_reset_default>;

/* 本机（0003 时） */
pinctrl-0 = <&cam_mclk0_default>;   /* 只有 MCLK！ */
```

也就是说 78（复位）、139（CAM_CUSTOM1）、117（CAM_VDIG）三根**没有任何 pinctrl 状态**。pinctrl-msm 的 `gpio_set_direction` 只改 OE 位、不动功能复用位，所以这三根能不能真被驱动，取决于 bootloader 留下的复用值——而厂商和 hotdog 都显式把它们设成 `function = "gpio"`。

厂商 overlay 对这组的定义（active 与 suspend 两组一致）：

```
mux    { pins = "gpio78";  function = "gpio"; };
config { pins = "gpio78";  bias-disable; drive-strength = <2>; };
mux    { pins = "gpio139"; function = "gpio"; };
config { pins = "gpio139"; bias-disable; drive-strength = <2>; };
mux    { pins = "gpio117"; function = "gpio"; };
config { pins = "gpio117"; bias-disable; drive-strength = <2>; };
```

按同样参数补了三个状态（提交 `2b18f6c92`，补丁 `0004-arm64-dts-kebab-rear-camera-pinctrl.patch`，SHA `5337b53b…`），挂到 `pinctrl-0`。只影响 DTB，内核 `Image` 不变。

**新的第 4 张候选 `boot-r4-camera-cphy3.img`**（`835b0d45…`）：与 `cphy2` 相同的内核与端点，只多这三个 pinctrl 状态。构造方式是直接把新 DTB 写进 `cphy2` 的 DTB 偏移 0x02d86000、同步改头部 `dtb_size`（132,821 → 133,189），`dtb_addr` 仍是 0x03000000、仍满足“大于 ramdisk 结束 0x02eea827”。校验：镜像内 DTB 段与构建产物**逐字节相同**（`c42f8555…`）、FDT magic 与 totalsize 一致。

候选顺序保持“每次只加一个变量”：`v2`（基线）→ `v3b`（+DTS，D-PHY）→ `cphy2`（+C-PHY）→ `cphy3`（+pinctrl）。

### 18.17 候选补齐成 2×2：两种 PHY × 有无 pinctrl

第 4 张只补了 C-PHY 一侧。为了不依赖人工在两种 PHY 之间来回试，把 D-PHY 一侧也补齐，形成完整组合：

| # | 镜像 | 内核 | DTB 差异 | 端点 | pinctrl |
| --- | --- | --- | --- | --- | --- |
| 1 | `boot-r4-camera-v2.img` `86b41681…` | 候选内核 | 与 r4 相同 | 无后摄节点 | — |
| 2 | `boot-r4-camera-v3b.img` `c7886eeb…` | 同 1 | + 后摄节点 | D-PHY 4 lane | 旧（只有 MCLK） |
| 3 | `boot-r4-camera-cphy2.img` `5e4e013f…` | 同 1 | + 后摄节点 | C-PHY 3 trio | 旧 |
| 4 | `boot-r4-camera-cphy3.img` `835b0d45…` | 同 1 | + 三根控制引脚状态 | C-PHY 3 trio | **新** |
| 5 | `boot-r4-camera-v3c.img` `c7489e64…` | 同 1 | + 三根控制引脚状态 | D-PHY 4 lane | **新** |

4、5 两张都是从 `cphy2`/`v3b` 的镜像里替换 DTB 段做出来的（内核 `Image` 逐字节不变），做法与校验同 §18.16：镜像内 DTB 与构建产物逐字节相同、FDT magic/totalsize 一致、`dtb_size` 头部已同步、`dtb_addr` 0x03000000 仍大于 ramdisk 结束地址。

`v3c` 的 DTB 反编译核对：`bus-type = <0x04>`、`clock-lanes = <0x07>`、`data-lanes = <0 1 2 3>`（传感器与 CSIPHY 两侧一致），三个新 pinctrl 状态齐全。

**这张组合表的价值**：只要有电、只要 PHY 是两者之一，就一定会有一张出帧；如果五张全都不出帧，那结论就收敛到“电气/模组级问题（供电、复位、I2C 上拉、模组本身）”，而不是配置问题——诊断脚本里的 I2C ACK 扫描与稳压器快照正是为这条结论准备的。

### 18.18 补丁集端到端复现验证（0002+0003+0004 → 与构建树逐文件相同）

在 `65305469b` 上开一个临时 worktree，按顺序 `git apply` 三个补丁，再和实际构建用的树逐文件比对：

```
applied: 0002-media-camss-cphy-sm8250.patch
applied: 0003-arm64-dts-kebab-rear-camera.patch
applied: 0004-arm64-dts-kebab-rear-camera-pinctrl.patch
same: arch/arm64/boot/dts/qcom/sm8250-oneplus-kebab.dts
same: camss-csid-gen2.c / camss-csid-gen3.c / camss-csid.c / camss-csid.h
same: camss-csiphy-3ph-1-0.c / camss-csiphy.c / camss-csiphy.h
same: camss.c / camss.h
rc=0
```

十个被改动的文件全部逐字节一致，`rc=0`——即**候选镜像的内核与 DTB 确实能由这三个补丁在基线上重建出来**，不存在"构建树里有、补丁里没有"的隐藏改动。

（过程里先出现过一个假警报：比对脚本列了 `camss-csiphy-3ph-1-0.h`，而这一版内核根本没有这个头文件，`diff` 两边都报 not found，被我误读成"文件不一致"。改成只比对 worktree 里 `git status` 实际列出的文件后结果干净。）

### 18.19 产物来源登记：两个 DTB、一个 Image

做 4、5 两张候选时出现过一个状态不一致：`out/` 里的 DTB 最后停在 **D-PHY** 版本（为了做 `v3c` 临时改端点编译的），而源码已经 `git checkout` 回 C-PHY。如果此刻有人重编 DTB，会得到和 `out/` 不同的文件而不自知。

处理：

1. 用当前源码（C-PHY）重编一次，得到 `c42f8555…`——与 `cphy3` 镜像里那份**完全相同**，说明 dtc 输出是确定的；
2. 在 `build-info.json` 里把来源登记清楚，不再只写一个 `kernel_commit`：

```json
"kernel_commit":     "acfeabecbe58…",   // Image 与全部 .ko 的构建提交
"dtb_commit":        "2b18f6c9238e…",   // pinctrl 那次 DTS 提交
"dtb_sha256":        "c42f8555…",       // 源码原样编译的 C-PHY DTB（cphy3 携带）
"dtb_dphy_sha256":   "bf097cc8…",       // 同一棵树、端点临时改 D-PHY 编出的（v3c 携带）
```

三条信息合起来回答一个容易被忽略的问题：**"镜像里那段 DTB 到底是哪来的"**——是源码原样编的，还是某次临时改端点编的。

### 18.20 步骤 B 原来可能根本没换上新 camss（假阴性风险）

诊断脚本的步骤 B 顺序是"卸载 qcom_camss → insmod 新模块 → 抓帧"。问题在于：此时 `imx586` 还加载着并且绑定在旧 camss 的 media 图上，`rmmod qcom_camss` 有可能被拒（或部分成功），随后 `insmod` 报 "File exists" 失败——于是**步骤 B 实际用的还是设备自带的老模块**，而老模块正是拒绝 C-PHY 端点的那个。结果就是：C-PHY 这条路根本没被测过，却会显示"没出帧"。

改法：

```python
out['b_rmmod_imx'] = run('sudo -n modprobe -r imx586 ... || sudo -n rmmod imx586 ... || true')
out['b_rmmod']     = run('sudo -n modprobe -r qcom_camss ... || sudo -n rmmod qcom_camss ... || true')
out['b_still_loaded'] = run('lsmod | grep -E "^qcom_camss|^imx586" || echo none')  # 新模块装载前先确认旧的真的没了
out['b_insmod']    = run('sudo -n insmod /tmp/qcom-camss.ko')
out['b_insmod_imx'] = run('sudo -n insmod /tmp/imx586.ko')
time.sleep(2)  # 等 udev 把重新注册的 subdev 节点建出来
```

先卸传感器、再卸 camss、装之前把"还剩谁"记进日志、装完等 udev 两秒。这样步骤 B 的结果才能代表新模块。

诊断脚本改动后 `py_compile` 通过，本地与 `deploy-cphy/` 两份仍逐字节相同（`0912d100…`），清单 20 项复核通过。

### 18.21 前摄也要有"重载前"的基线

步骤 B 现在会先 `rmmod imx586`、`rmmod qcom_camss` 再装新模块，前摄（imx471）的 subdev 要跟着 re-bind 一次。如果它没重新绑上，`b_front` 会显示失败——看起来像"新 camss 把前摄弄坏了"，其实是卸载/重载的副作用。

原来只有 `b_front`（重载之后测一次前摄），没有对照。现在在步骤 A 里加一次 `a_front`（**任何模块重载之前**）：

| 组合 | 结论 |
| --- | --- |
| `a_front` 成功 + `b_front` 成功 | 前摄没被重载影响 |
| `a_front` 成功 + `b_front` 失败 | 是 rmmod/insmod 之后前端没重绑，不是回归 |
| `a_front` 失败 | 前摄本来就有问题，与后摄无关 |

抓帧子进程退出时会关掉全部 fd，不会把 media 图占住而挡下步骤 B 的 `rmmod`。脚本已 `py_compile` 通过，两份仍逐字节相同（`bf075e40…`），清单 20 项复核通过。

### 18.22 I2C 扫描原来会往**所有**总线上乱写一个字节（有风险），已收到 CCI 总线

原来的 `cci_scan()` 遍历 `/sys/class/i2c-dev/*` 下**每一个**适配器，对 0x08–0x77 每个地址都做"写 1 字节"探测。问题是这条路径会往 PMIC、充电器、触摸、NFC、电量计等设备的任意地址写一个 `0x00`——一次误写就可能关掉某路电源或改掉某个寄存器。这属于"影响范围不明确的操作"，不该在无人值守的夜里做。

改成：

1. 先 `modprobe i2c-dev`——否则 `/sys/class/i2c-dev` 是空的，扫描会**静默返回"什么都没 ACK"**，直接被误读成"模组没上电"；
2. 只探测名字里带 `cci` 的适配器（CCI 总线上只有相机模组，写一个地址字节是安全的），其余记一行 `skipped (not a CCI adapter)`；
3. 如果一个 CCI 适配器都没找到，输出 `warning: no CCI adapter found; nothing was scanned`，不让"空结果"冒充"无应答"。

顺带把 `a_driver` 从写死的 `/sys/bus/i2c/devices/0-001a` 改成 glob `*-001a`——i2c 适配器编号是动态分配的，写死 0 号会把"驱动没绑定"和"设备不在 0 号总线"混为一谈。

脚本已 `py_compile` 通过，两份仍逐字节相同（`126acd11…`），清单 20 项复核通过。

### 18.23 给诊断脚本补离线测试——第一次跑就抓到"稳压器过滤条件写错"

新增 `test-camera-full-diag.py`（unittest，9 项，全部 mock 掉 `/sys`、`/proc`、`subprocess`）：

| 用例 | 验证点 |
| --- | --- |
| `test_probes_only_cci_adapters` | 只探测名字带 cci 的适配器，其余记 skipped |
| `test_warns_when_no_cci_adapter` | 没有 CCI 适配器时给 warning 而不是空结果 |
| `test_reports_when_i2c_dev_missing` | `/sys/class/i2c-dev` 不存在时报 error |
| `test_missing_device` | 打开 `/dev/i2c-N` 失败时返回 `open:...` |
| `test_delta` / `test_snapshot_parses_counters` | 中断差分与 `/proc/interrupts` 解析 |
| `test_filters_and_reads` | 稳压器名字过滤与字段读取 |
| `test_parses_json` / `test_garbage_stdout` | 抓帧输出解析与异常输出兜底 |

**第一次运行就有 1 项失败，暴露出真 bug**：`regulators()` 的过滤条件是 `l1p/l4p/l7p/cam/vreg_l`，但这块板的稳压器名字是 `pm8008_l1`、`pm8008_l4`、`pm8008_l7`——一个都匹配不上，**在设备上会得到一个空的稳压器快照**，然后被误读成"读不到"。已加入 `pm8008` 关键字，9 项全过。

一个纯粹离线的过滤条件，靠人眼很难发现，因为两种写法在不同板子上都是"看起来合理"的。

### 18.24 同一类错误的第二个实例：CCI 总线节点叫 `i2c-bus@N`，不是 `i2c@N`

诊断脚本用 shell 通配找设备树节点：

```sh
ls /proc/device-tree/soc@0/*/i2c@*/camera@1a
```

而反编译出来的真实层级是 `/soc@0/cci@ac4f000/i2c-bus@0/camera@1a`——**总线节点名是 `i2c-bus@N`**，`i2c@*` 一个都匹配不到。后果：`dt_rear` 永远返回空、runner 身份检查里 `dtb_camera=` 永远是 0，看起来像"设备树里没有这个节点"。

这类"名字差一个后缀"的错误和上一节的 `pm8008_l1` 是同一类：在别的板子上都"看起来合理"，只有在本机数据上才暴露。

两处都改了：

- 诊断脚本不再用 shell 通配，改成 Python 里遍历 `/proc/device-tree` 找 `camera@1a`（新增 `dt_rear_path()`，可离线测试）；
- runner 的身份检查同时匹配 `i2c-bus@*` 与 `i2c@*` 两种命名。

新增 2 个用例（`test_finds_node_under_i2c_bus`、`test_missing_root`），现在共 **11 项全过**。

### 18.25 推进到设备上的两个 .ko 与镜像同源（逐字节核对）

之前只登记了"模块来自哪次构建"，没做逐字节核对。这一轮把构建输出的模块解压出来和 `deploy-cphy/` 里实际要推送的两份比了一次：

```
构建输出 out/modules-stage/lib/modules/7.2.0-kebab-debian13-r4-venus+/…
  imx586.ko.zst       -> imx586.ko      b51c07a2…
  qcom-camss.ko.zst   -> qcom-camss.ko  17a444fe…
deploy-cphy/
  imx586.ko                             b51c07a2…   identical
  qcom-camss.ko                         17a444fe…   identical
```

同一模块目录名 `7.2.0-kebab-debian13-r4-venus+` 就是候选镜像的内核版本，所以"镜像里的内核 ↔ 推进去的模块"这一环也对上了。至此完整链条都有逐字节证据：

| 环节 | 证据 |
| --- | --- |
| Image | `42e1a3a4…`，`build-info.json` 记 `kernel_commit acfeabecb` |
| 模块 | 与构建输出逐字节相同（本节） |
| DTB | C-PHY `c42f8555…`（cphy3）／D-PHY `bf097cc8…`（v3c），`dtb_commit 2b18f6c9` |
| 补丁复现 | 0002+0003+0004 应用后 10 个文件逐字节相同（§18.18） |
| 脚本 | 本地与 `deploy-cphy/` 副本逐字节相同，11 项离线测试通过 |

另外确认 runner 的推送列表取的是 `$base/camera-full-diag.py`（工作目录那份）与 `deploy-cphy/*.ko`，两份 diag 副本内容一致，所以推送的确实是本轮改过的版本。

### 18.26 runner 加固：推送失败不再拿旧文件跑诊断、等待缩短、结尾有汇总

逐行读 runner 时发现三处会在无人值守时产生误导或浪费时间的地方：

1. **`PushFiles` 失败后仍然继续跑诊断**——若 scp 失败（网络抖动、`/tmp` 满），诊断用的还是上一次推上去的模块，结果却是"这一轮的"。改成 `PushFiles` 返回布尔值，任一文件推送失败就跳过本轮诊断，并记 `push-failed`。
2. **`WaitSsh 90` 等于 15 分钟**——实测 Debian 从 `fastboot boot` 到 SSH 只要 35–60 秒，15 分钟等于 25 倍余量，五个候选最坏要耗一小时以上。改成 45 次（7.5 分钟，约 8 倍余量）。
3. **结尾没有汇总**——五次尝试的结果散在日志里。现在每个候选都会追加一条 `tag:结果`，结束时打一行：

```
summary: v2:no-frame  v3b:no-frame  cphy3:a=1 b=1
```

顺带把"看起来不像我们的镜像"（`/proc/config.gz` 里没有 `CONFIG_VIDEO_IMX586`）和"起来了但 SSH 不通（疑似挂死）"也计入汇总，分别为 `wrong-kernel` 与 `no-ssh`，避免事后翻日志才能判断到底卡在哪一步。

PowerShell 语法检查通过。

### 18.27 跑完不要留手机在 fastboot 界面；STATUS 按实际状态重写

`fastboot boot` 失败时手机会停在 fastboot，而这个流程是**无人值守**的——早上醒来看到的是一个停在 fastboot 的手机，既没有相机也没有系统。已在 runner 结尾加一步：若 `fastboot devices` 仍有设备，就 `fastboot reboot`（不写任何分区，只是让手机回到 r4 正常启动）。

同时把工作目录的 `STATUS.md` 按实际状态重写了一遍。原稿有三处与事实不符：

- 写"按顺序试三张镜像"，表里其实已经有五张；
- 写"等 SSH 最多 15 分钟"，实际已改成 7.5 分钟；
- 写"最长连续两轮 fastboot boot 失败就停手"，实际是一次 pass、遇到"起来了但没 SSH"就 break；校验项数也还停在 16。

新稿补了一张**判读表**（看到什么 ⇒ 下一步），把"没 ACK / 有 ACK 没绑定 / 绑定没中断 / 有中断全黑 / 只有 b 成功"这些分叉与对应动作写在一起，早上按表走即可。

### 18.28 一处 DTS 注释写错了：`cam_vdig` 的 1.1 V 属于 cam1，不是后摄

把厂商 overlay 里七颗 PM8008 LDO 的 phandle 全部解出来后，相机节点的映射关系才完全对上：

| 厂商节点 | cam_vio | cam_vana | cam_vdig |
| --- | --- | --- | --- |
| `qcom,cam-sensor@0`（后摄 IMX586） | `pm8008_l4`（0x92，1.8 V） | `pm8008_l7`（0x95，2.9 V） | **`pm8150a_s8`，1,350,000 µV** |
| `qcom,cam-sensor@1`（另一颗） | `pm8008_l4` | — | `pm8008_l1`（0x9e），1,100,000 µV |

也就是说：`pm8008_l1` + 1,100,000 µV 这组值属于 **cam1**，我们在 DTS 注释里把它当成了后摄的 VDIG 依据——写错了。后摄在厂商侧走的是 SMPS8（1.35 V）。

**这会不会就是"没有 ACK"的原因？** 大概率不是，但必须实测才能下结论：

- 模组的 VDIG 由 `CAM_VDIG`（tlmm 117）门控，我们照样把它拉高了；
- 但主line 根本没有 `pm8150a` 这颗 PMIC 的模型，`vreg_s8c_1p3` 这类 SMPS8 轨道是给 UFS/PCIe/WiFi 等消费者常开的，模组接的就是它；
- 所以我们写在 `vdig-supply` 里的 pm8008_l1 只起到"给驱动一个可 enable 的句柄"的作用，真正电压取决于 PMIC 当前状态。

因此这一轮做了两件事，都不改硬件行为：

1. **诊断脚本的稳压器快照加宽**：原来只抓 `pm8008/l1p/l4p/l7p/cam/vreg_l`，现在把名字里带 `s8` 的轨道也抓进来（`vreg_s8c_1p3` 等），这样下次开机就能直接看到 SMPS8 到底使能没有、电压多少；
2. **把 DTS 注释改成事实**（提交 `96c786c59`，补丁 `0005`）：写清楚后摄走 SMPS8、1.1 V 属于 cam1、主line 没有那颗 PMIC 的模型。

改完重编 DTB：`c42f8555…` **与改之前完全一致**（注释被 dtc 丢掉），所以已有的 5 张候选不受影响，不需要重做。补丁集重新验证：0002+0003+0004+0005 应用后 10 个文件逐字节相同，`rc=0`。

### 18.29 后摄 VANA 的电压从来没被设过（本轮最像"真凶"的一条）

厂商 overlay 对后摄给的是**单点**：`rgltr-min == rgltr-max == 2900000 µV`（cam_vana → `pm8008_l7`）。我们的板级 DTS 把它写成了 **1.5–3.4 V 的区间**：

```dts
pm8008_l7: ldo7 {
    regulator-name = "pm8008_l7";
    regulator-min-microvolt = <1500000>;
    regulator-max-microvolt = <3400000>;   /* 区间 = 核心不编程 */
};
```

关键在于两件事叠加：

1. **区间不会被编程**。`machine_constraints_voltage()` 只在 `min == max` 时真正调用 `set_voltage()`；给区间时这条轨道保持 bootloader 留下的值；
2. **驱动从不设压**。`imx586_power_on()` 只调 `regulator_set_load()`（设负载），没有任何 `regulator_set_voltage()`。

于是这条轨道从头到尾没有被人设定过。而**前摄不走这条路**：它的 VANA 是 `vreg_cam_front_vana`（fixed，2.856 V，vin = vreg_bob，gpio156 门控），永远是对的——这正好对上"前摄能出帧、后摄没反应"的不对称。

**修法**：钉成单点。但 `2900000` 在 pm8008 上不可表示——LDO 是 8 mV 步进、从 1504000 起：

```
1504000 + 174*8000 = 2896000
1504000 + 175*8000 = 2904000   （2900000 落在两档之间）
```

硬写 2900000 会重演 18.x 里 L1 那个坑：min 向上取整成 2904000、max 向下取整成 2896000，`min > max` → `-EINVAL` → ldo7 注册失败 → **整个 pm8008 驱动 probe 失败** → L1/L4/L7 全部消失，相机总线彻底断电。所以取 **2896000**（比厂商要求低 4 mV，在 IMX586 VANA 规格内）。

反编译新 DTB 确认：`regulator-min-microvolt = <0x2c3080>; regulator-max-microvolt = <0x2c3080>;` 即 2,896,000 ✓。

**影响 DTB**（与 0004/0005 那两次纯静态改动不同），对应两张新候选：

| 镜像 | SHA-256 | DTB | 内容 |
| --- | --- | --- | --- |
| `boot-r4-camera-cphy4.img` | `5ec3e13e…` | `b4893ce1…` | C-PHY + pinctrl + VANA 钉位 |
| `boot-r4-camera-v3d.img` | `0f761f62…` | `926848f3…` | D-PHY + pinctrl + VANA 钉位 |

两张的 DTB 段都逐字节核对过（FDT magic、totalsize、`dtb_addr` 0x03000000 仍大于 ramdisk 结束地址）。

**尝试顺序改为**：`v2`（基线）→ `cphy4`（首选）→ `v3d`（D-PHY 兜底）→ `cphy3`（不带 VANA 钉位的兜底）。从 5 张减到 4 张，把最可能正确的配置排在最前。

补丁集扩到 0002–0006，重新验证：应用后 10 个文件逐字节相同，`rc=0`；清单 25 项 `ok=25 bad=0`。

### 18.30 睡前预检：把"武装好的东西"逐项验一遍

新增 `preflight-camera.ps1`，一条命令检查无人值守流程依赖的全部前提，结果 **PREFLIGHT OK**（17 项全过）：

```
PASS  boot-r4-camera-v3d.img    len=47869952 dtb_size=133229 dtb_addr=0x3000000 ramdisk_end=0x2eea827 magic=D0-0D-FE-ED
PASS  boot-r4-camera-cphy3.img   len=47869952 dtb_size=133189 ...
PASS  boot-r4-camera-v2.img      len=47869952 dtb_size=132213 ...
PASS  boot-r4-camera-cphy4.img   len=47869952 dtb_size=133189 ...
PASS  push qcom-camss.ko  bytes=839969
PASS  push imx586.ko      bytes=29641
PASS  push camera-full-diag.py     bytes=9220
PASS  push camera-capture-test.py  bytes=11575
PASS  runner parses        errors=0
PASS  runner lists <四张镜像>
PASS  watcher running      processes=2
PASS  fastboot / ssh on PATH, ssh config present
```

检查项不是"文件存在"就算过：镜像要验长度、FDT magic、`dtb_size`、`dtb_addr > ramdisk 结束地址`；runner 要能被 PowerShell 解析且列出的镜像名与实际文件一致；watcher 要确实有进程在跑。

这样"睡前状态"不是靠回忆，而是有一份可重复的检查记录。

### 18.31 让诊断自己下结论：新增 `VERDICT` 一行

原来早上要读完整份 JSON 才能判断走哪条路。现在诊断在结尾多打一行分类结论，同时写进 JSON 的 `verdict` 字段，runner 也会把它记进汇总：

```
REAR_RESULT a=0 b=0
VERDICT ack-at-0x1a-but-no-driver
```

分类规则（按优先级）：

| verdict | 判据 | 对应动作 |
| --- | --- | --- |
| `rear-frame` | `a_capture` 或 `b_capture` 的 `ok` 为真 | 成功 |
| `driver-bound-no-frame` | `a_driver`/`b_driver` 非空 | 驱动绑上了但无帧 → 查链路/PHY |
| `ack-at-0x1a-but-no-driver` | CCI 扫描里 0x1a 有 ACK | 上电正常、驱动没绑 → 查 chip id / probe |
| `no-cci-adapter` | 扫描里出现 `warning` | i2c-dev 或 CCI 适配器没出来 |
| `no-ack-at-0x1a` | 以上都不是 | 供电/复位/CCI0 → 看稳压器快照 |

runner 的汇总因此变成 `cphy4:no-frame/no-ack-at-0x1a` 这种形式，一眼能看出卡在哪一层。新增 5 个离线用例（`VerdictTests`），现在 **16 项全过**。

### 18.32 内容级检查：把要启动的那几个 DTB 反编译出来逐项断言

哈希只能证明"文件没变"，不能证明"里面写的是我们想的那套配置"。新增 `check-candidate-dtb.py`：从每张候选镜像里按 `dtb_size` 取出 DTB，反编译后逐项断言，结果 **3/3 candidates ok**：

```
boot-r4-camera-cphy4.img (cphy, vana_pinned=True)
  ok  rear node present / compatible sony,imx586 / reg 0x1a
  ok  bus-type C-PHY / data-lanes 0 1 2 / no clock-lanes
  ok  pinctrl-0 has 4 states
  ok  reset gpio 78 active low / vana-enable gpio 139 / custom1 gpio 117
  ok  link-frequencies 698395833
  ok  pm8008_l7 pinned 2896000
```

（`v3d` 断言 D-PHY 的 `clock-lanes 7` + 4 条 lane，`cphy3` 断言 l7 仍是区间、没有 VANA 钉位。）

这一项已经并进 `preflight-camera.ps1`，现在预检 18 项，输出 `PREFLIGHT OK`。

**过程里自己犯了两次错，都被这个检查当场抓住**：

1. 先把 698,395,833 的十六进制写成 `0x29a0b1f9`（实际是 `0x29a0acb9`）；
2. 改对数值后又没注意 dtc 把 `/bits/ 64` 打印成两个 32 位单元 `<0x00 0x29a0acb9>`，正则匹配不到，仍然报 FAIL。

两次都不是镜像的问题，而是**检查器写错了**——如果没有这层内容级断言，我大概会以为镜像有问题而去重做，白白浪费一夜。

### 18.33 watcher 允许重试一轮，并换了新的 14 小时窗口

原来的 watcher 只跑一遍就退出：一旦那一遍因为瞬时原因失败（scp 没落地、手机回退到 r4 内核、SSH 抖动），后面十几个小时就什么都不做了。

改成最多 **2 轮**：一轮没拿到帧就等 **10 分钟**再守一次（手机可能刚好重启完），第二轮仍无帧才退出；任意一轮出帧立刻停止。窗口仍是 14 小时。

同时换了新窗口：**02:59:58 起，到 16:59**（旧窗口 02:20–16:20 已随旧进程结束）。进程核实：PID 41092，父进程退出后仍存活，日志已写 `pass 1/2 - waiting for the phone`。

顺带确认系统里**只有一个** watcher 进程——按命令行前缀筛出的另外两个 PID 是我自己的查询 shell（命令行里含同样的字符串），不是第二个 watcher。这一点值得确认，因为两个 watcher 同时抢手机做 `fastboot boot` 是很危险的。

### 18.34 `a_driver` 会把"没绑定"误判成"绑定了"

诊断里判断"驱动有没有绑上"用的是：

```sh
readlink -f /sys/bus/i2c/devices/*-001a/driver
```

问题在于 GNU `readlink -f` **只要父目录存在就会把路径原样打出来**，哪怕最后一段（`driver` 这个符号链接）根本不存在。于是"设备在、驱动没绑"会输出一个非空路径，被我的 `verdict()` 判成 `driver-bound-no-frame`——正好把"没绑上"和"绑上了但没帧"这两条完全不同的分支搞反。

改成先判存在再解析：

```sh
for d in /sys/bus/i2c/devices/*-001a; do [ -e "$d/driver" ] && \
    printf "%s -> %s\n" "$d" "$(readlink -f "$d/driver")"; done 2>/dev/null
```

顺带补上步骤 B 的 `b_driver`（原来只有步骤 A 有）——重载 camss 之后驱动有没有重新绑上，本身就是一条有用信息，也让 `verdict()` 里那个从来没被满足过的 `b_driver` 分支真正生效。

16 项离线测试仍全过，两份 diag 副本仍逐字节相同（`b10f5890…`）。

### 18.35 更正 §18.29：VANA 的"钉位"是个错误改动，已撤回

§18.29 说"驱动只调 `regulator_set_load()`，从不 `set_voltage()`"，据此把 `pm8008_l7` 的约束从 1.5–3.4 V 区间钉成 2,896,000 µV。**这条前提是错的**——重读驱动第一行就看到：

```c
#define IMX586_VANA_MIN_UV	2900000
#define IMX586_VANA_MAX_UV	2900000
...
	ret = regulator_set_voltage(imx586->vana, IMX586_VANA_MIN_UV,
				    IMX586_VANA_MAX_UV);   /* power_on 的第一步 */
```

驱动确实会设 VANA 电压，`set_load` 只用在 VDIG 上。

后果更严重：把约束钉成 `min == max == 2896000` 之后，核心的 `regulator_check_voltage()` 会拿驱动的请求 `[2900000, 2900000]` 去比约束，`2900000 > constraints->max_uV (2896000)` → **`-EINVAL`** → `power_on` 直接失败 → 永远进不了 streaming。也就是说这张候选会**自己制造"没有帧"**，而且是那种看起来"供电都正常、就是不出图"的假象。

而原来的 1.5–3.4 V 区间反而能让这个请求通过（映射到 `2904000`，2.904 V，比厂商要求高 4 mV，仍远在 IMX586 VANA 规格内）。**所以撤回后回到区间才是对的。**

已做：

- `git revert` 掉那次提交（`5e0791d89`），重编 DTB 回到 `c42f8555…`——与 `cphy3` 里那份**完全一致**；
- 删除 `boot-r4-camera-cphy4.img`（`5ec3e13e…`）与 `boot-r4-camera-v3d.img`（`0f761f62…`），它们是带着这个错误的候选；
- 删除补丁 `0006`，并在 `patches/camera/README.md` 里写明撤回原因；
- 候选顺序回到 `v2 → cphy3 → v3c → cphy2`；补丁集（0002–0005）重新验证，10 个文件逐字节相同，`rc=0`。

**教训**：我在同一晚第 18.28 节刚因为"把 cam1 的 1.1 V 当成后摄的"纠正过一次，转头又在 18.29 用同样"没读全代码"的方式推出了另一个错误结论。两次都是先有结论、再去找支持它的半截证据。

### 18.36 逐行核对驱动对设备树的全部要求（上次就是漏了这一步）

§18.35 的错误根因是"只对厂商表、不对驱动代码"。这次把 `imx586.c` 里**每一个**对 DT 的请求列出来，逐项确认我们的 DT 能不能满足：

| 驱动里的调用 | 需要的 DT 属性 | 我们的值 | 能否成功 |
| --- | --- | --- | --- |
| `devm_gpiod_get(dev, "reset", GPIOD_OUT_HIGH)`（必需） | `reset-gpios` | `&tlmm 78 GPIO_ACTIVE_LOW` | ✓ 物理低 = 复位有效；power_on 里 `set 0` = 释放 |
| `devm_gpiod_get_optional("vana-enable", OUT_LOW)` | `vana-enable-gpios` | `&tlmm 139 ACTIVE_HIGH` | ✓ |
| `devm_gpiod_get_optional("custom1", OUT_LOW)` | `custom1-gpios` | `&tlmm 117 ACTIVE_HIGH` | ✓ |
| `devm_gpiod_get_optional("custom2", OUT_LOW)` | `custom2-gpios` | 未提供 | ✓ 辅助函数判空后跳过 |
| `regulator_set_voltage(vana, 2900000, 2900000)` | `vana-supply` 的约束必须**包含** 2.9 V | `pm8008_l7` 1.5–3.4 V | ✓ 映射到 2,904,000 µV |
| `regulator_enable(vana)` | 同上 | 同上 | ✓ |
| `regulator_set_load(vdig, 1100000)` | `vdig-supply` | `pm8008_l1` [1,104,000…] | ✓ 只记负载，不设压 |
| `regulator_enable(vdig)` | 同上 | 同上 | ✓ |
| `regulator_enable(vio)` | `vio-supply` | `pm8008_l4` [1,800,000] | ✓ |
| `clk_set_rate(inclk, 19200000)` + 回读比对 | `clocks` | `&camcc CAM_CC_MCLK0_CLK`，`assigned-clock-rates = <19200000>` | ✓ |
| `bus_cfg.bus_type == V4L2_MBUS_CSI2_CPHY → num_data_lanes = 3` | `data-lanes` 个数必须等于 **3** | `<0 1 2>` | ✓ |
| （D-PHY 分支）`num_data_lanes = 4` | `data-lanes` 个数必须等于 **4** | `<0 1 2 3>` + `clock-lanes = <7>` | ✓ |
| `link_frequencies[i] == 698395833` | `link-frequencies` 必须含该值 | `/bits/ 64 <698395833>` | ✓ |

三处硬约束值得单独记：

- **复位极性**：`GPIOD_OUT_HIGH` + `ACTIVE_LOW` = 物理低 = 复位有效；`power_on` 末尾 `set 0` = 物理高 = 释放。符号对不上就是"永远在复位里"，而 I2C 根本不会 ACK；
- **lane 数必须精确相等**（C-PHY 3 / D-PHY 4），少一条直接 `-EINVAL`，probe 失败；
- **link frequency 必须在列表里**，否则同样 `-EINVAL`。

这三条都是"驱动会明确报错"的类型，比"悄悄不出图"好排查——前提是 `dmesg` 真的被读。诊断脚本的 `a_dmesg`/`b_dmesg` 就是为此准备的。

### 18.37 用假 fastboot/ssh 空跑一遍 runner，抓出三个真 bug

`auto-camera-run.ps1` 是唯一没有离线测试的部分。这次在 `sim/` 里放了三个 batch 桩（`fastboot.cmd`/`ssh.cmd`/`scp.cmd`，只回答文本、`boot` 只创建一个 flag 文件），把 `sim` 加到 PATH 前面，就能在不碰手机的情况下把整条流程跑通。第一次空跑的输出立刻不对劲：

```
candidate v2 gave no rear frame (/);
summary: v2:/  cphy3:/  v3c:/  cphy2:/
```

结果解析全丢。查下去发现三个**同源**的 bug：

1. **`Log()` 用 `Write-Output`**。它被函数内部调用时，那行日志会混进函数的返回值。`RunDiag` 于是返回一个数组（`["… diag saved: …", <诊断正文>]`）；数组上做 `-match` 只返回匹配元素、**不填充 `$Matches`**，所以 `$res = $Matches[1]` 拿到空串——就是那个 `/`。
2. **`PushFiles` 的返回值同样被污染**。`if (-not (PushFiles))` 对数组求值，非空数组恒为真，**推送失败永远不会被发现**，会拿旧文件跑诊断。这条最危险。
3. **`SshOk` 把 ssh 的输出也一起返回**，`if (SshOk)` 在"命令失败但有输出"时也会为真。

改法：`Log` 改 `Write-Host`（只写控制台/文件，不进管道），`SshOk` 加 `| Out-Null`，`PushFiles` 明确 `return ($ok -eq $true)`。

第二次空跑，整条链路符合预期：

```
candidate cphy3 gave no rear frame (a=0 b=0/no-ack-at-0x1a); moving on
still in fastboot - rebooting into the normal system
summary: v2:a=0 b=0/no-ack-at-0x1a  cphy3:a=0 b=0/no-ack-at-0x1a  v3c:…  cphy2:…
```

顺带验证了：fastboot 回执解析、`CONFIG_VIDEO_IMX586` 身份判断、四个文件推送回执、诊断落盘、`VERDICT` 合并进汇总、结束时若仍在 fastboot 就 `reboot`。空跑产生的假 `diag-*.json.txt` 与假日志已删除、`camera-test.log` 已从备份还原（169 B，仍是 02:07 那次冒烟测试的内容）。

### 18.38 早上拿到结果后的判读树（按 dmesg 原文分流）

`imx586_probe()` 的三个失败点各有**一句独立的 dmesg**，所以不用猜，按原文分流即可：

| dmesg 原文 | 失败点 | 含义 | 下一步 |
| --- | --- | --- | --- |
| `HW configuration is not supported` | `imx586_parse_hw_config()` | endpoint 不匹配：`data-lanes` 条数（C-PHY 必须 3 / D-PHY 必须 4）或 `link-frequencies` 里没有 698395833 | 换 PHY 候选（`cphy3` ↔ `v3c`）；两种都报同一句说明 DT 与驱动常量不一致，读 §18.36 表重新核对 |
| `failed to power-on the sensor` | `imx586_power_on()` | 稳压器/GPIO/时钟某一步返回错误；同一行前后会有具体那条（VANA 设压、VIO/VANA/VDIG enable、inclk） | 看 `regulators_after_power`：哪条轨 `state` 不对；同时看 `dmesg` 里更靠前的那条具体错误 |
| `chip id mismatch: 586!=xxxx` | `imx586_detect()` | **I2C 通了**，但 0x0016 读回的值不是 0x0586 | 说明地址对了、总线通了，但器件不是 IMX586 或读到了别的从机（0x1a 上还有别的设备？）。按实际值判断 |
| `failed to find sensor: -121` / `-5` / `-6` | `imx586_detect()` 的读寄存器失败 | I2C 没有 ACK（-EREMOTEIO/-EIO） | 供电/复位/CCI0 问题，不是 PHY 问题 |
| 完全没有 `imx586` 相关行 | 驱动没被 probe | 要么模块没插、要么 DT 里没有 `camera@1a` | 看 `lsmod_boot`、`a_driver`、`dtb_camera=` 计数 |

配合 `VERDICT` 一起看最省事：

| VERDICT | 通常对应的 dmesg | 含义 |
| --- | --- | --- |
| `ack-at-0x1a-but-no-driver` | `chip id mismatch` 或 `failed to find sensor` | I2C 通了，驱动绑不上 |
| `no-ack-at-0x1a` | 无 imx586 行，或 `failed to power-on` | 上电/复位/总线没通 |
| `driver-bound-no-frame` | 无错误 | 驱动绑上了，问题在 CSIPHY/CSID/VFE 或流配置 |
| `rear-frame` | — | 成功 |
| `no-cci-adapter` | — | `i2c-dev` 或 CCI 适配器没出来（检查 `cci_buses`） |

另外两条已知的"不是故障"：`camss` 的 `supply vdda-phy not found, using dummy regulator` 若再次出现，说明 `&camss` 的 `vdda-phy-supply`/`vdda-pll-supply` 没生效（我们已加，正常不该出现）；`imx471`（前摄）的报错与后摄无关，可以忽略。

**如果四个候选全是 `no-ack-at-0x1a` 且 dmesg 里没有任何 `imx586` 行**，那说明驱动根本没 probe——这时该看的是 `dtb_camera=` 计数（DT 里有没有 `camera@1a`）和 `lsmod_boot`（模块有没有加载），而不是继续换 PHY 候选。

### 18.39 0x1a 这个地址到底有多少证据

查了一遍来源，结论要如实说：**厂商 DTBO 里根本没有传感器的 I2C 地址**。`qcom,cam-sensor@0` 的 `reg = <0x00>` 是 `cell-index`，`qcom,eeprom@0`、`qcom,actuator@0` 同理，整份 overlay 里搜不到 `0x1a`。

所以 0x1a 的依据是**社区先例**：hotdog（一加 8 Pro，同厂同代、同为 IMX586）的主线 DTS 里写着

```dts
camera@1a {
    compatible = "sony,imx586";
    reg = <0x1a>;
    clocks = <&camcc CAM_CC_MCLK1_CLK>;
    assigned-clock-rates = <19200000>;
    vio-supply = <&vreg_l1c_1p8>;
    vana-supply = <&vreg_bob>;
    vdig-supply = <&vreg_l1f_1p1>;
    reset-gpios = <&tlmm 30 GPIO_ACTIVE_LOW>;
    vana-enable-gpios = <&tlmm 11 GPIO_ACTIVE_HIGH>;
    custom1-gpios = <&tlmm 29 GPIO_ACTIVE_HIGH>;
    custom2-gpios = <&pm8150l_gpios 1 GPIO_ACTIVE_HIGH>;
```

IMX586 的 I2C 地址由模块上的 SID 引脚决定，常见就是 `0x1a` 与 `0x10` 两种。hotdog 用 0x1a，我们跟它一致。

**但这仍然是"先例"而不是"实测"**。所以诊断里的 CCI ACK 扫描（0x08–0x77）不能只看 0x1a：

- 若 0x1a 有 ACK → 地址正确，问题在别处；
- 若**别的地址**有 ACK 而 0x1a 没有 → 那才是真实地址，加一个对应地址的 `camera@N` 节点即可（不必改驱动）。

这条已经加进 §18.38 的判读树。也提醒一句：`imx471`（前摄）在我们的 DTS 里就是 0x10（`24-0010`），说明这个板上确实有传感器用 0x10，不能想当然。

### 18.40 新增 C-PHY 会不会把已经能用的前摄弄坏

前摄（IMX471，D-PHY）是这台机器上**唯一验证过能出帧**的相机，而所有候选都加载同一个打过 C-PHY 补丁的 `qcom-camss.ko`。所以必须确认补丁没有动 D-PHY 路径。逐项查了：

1. **ops 是按 SoC 静态绑定的**，不是按 bus type。`camss.c` 里 SM8250 的每个 CSIPHY 都是 `csiphy_ops_3ph_1_0`（上游本来就这样，因为硬件是 3-phase CSR 块，D-PHY 也走它）；
2. **D-PHY 分支完整**：`csiphy_3ph_config_lanes()` 里 `switch (lane_cfg->phy_cfg)` 有 `V4L2_MBUS_CSI2_DPHY` 分支（第 1639 行），另有 10 处 `if (DPHY) … else if (CPHY) …` 的逐 lane 处理，D-PHY 一路都在；
3. **lane mask 对 D-PHY 没变**：

```c
switch (lane_cfg->phy_cfg) {
case V4L2_MBUS_CSI2_CPHY:  offset = 1; break;              /* 新增：C-PHY */
case V4L2_MBUS_CSI2_DPHY:  lane_mask = CLK_ENABLE; break;   /* 与上游一致 */
}
for (i = 0; i < num_data; i++) lane_mask |= BIT(data[i].pos * 2 + offset);
```

D-PHY 的 `offset` 是 0，即位宽、位置、时钟位都与打补丁前完全一致；C-PHY 只是多一个 `offset = 1`。

4. **camss.c 的 endpoint 解析**只是从"只接受 D-PHY"放宽成"两种都接受，并要求本地与远端一致"，没有削弱 D-PHY 校验。

结论：补丁对 D-PHY 是**加法**，不是改写。前摄若在这次运行里出问题，更可能是别的原因（比如模块重载顺序），而不是 C-PHY 支持的副作用。诊断里保留了 `a_front` 基线，跑完可以直接对比。

### 18.41 DTB 的加载地址还要再挪一次：0x3000000 正好压在"补齐后的 ramdisk 末尾"

之前把 `dtb_addr` 从 `0x1f00000` 改到 `0x3000000`，依据是"ramdisk 原始结束地址是 `0x2eea827`"。但换个算法：

```
ramdisk 起 0x01000000，大小 32,417,831 (0x1EEA827)
原始结束 0x02EEA827
按 32 MB 向上补齐：0x01000000 + ceil(0x1EEA827 / 0x2000000) * 0x2000000 = 0x03000000
```

**`0x3000000` 正好是补齐后的边界**。如果加载器是按补齐后的块来安置 ramdisk 的，那么把 DTB 放在这个地址就是在赌"边界那一字节不算越界"。

四张候选的 `dtb_addr` 全部改成 **`0x08000000`**（128 MB），远在 kernel（`0x8000 + 15.3 MB = 0xea17c8`）与 ramdisk（补齐后 `0x3000000`）之上，不再有任何重叠可能。DTB 内容逐字节未变（`cphy3` 仍是 `c42f8555…`、`v3c` 仍是 `bf097cc8…`），只改了头里的 4 字节。

顺便确认了 **`v2` 是一张真正的对照**：它的 DTB 与**已知能启动的 r4 镜像里的 DTB 逐字节相同**（`216c2fa1…`，132,213 B），kernel 也只差那个 `CONFIG_VIDEO_IMX586=m`（体现在内嵌 config 的 26 字节）。所以：

- `v2` 能起、后面几张不能 → 是 DTS/驱动改动的问题；
- `v2` 也起不来 → 那是 `fastboot boot` 这条路或设备状态本身的问题，跟相机代码无关。

### 18.42 预检脚本自己也有一个"假通过"

改 `dtb_addr` 检查时，`[Math]::Ceiling()` 返回 double，`.ToString("x")` 直接抛异常——而异常发生在 `Check` 之前，于是**四张镜像的检查被整体跳过**，脚本末尾照样打印 `PREFLIGHT OK`。

两个修法：

1. 用 `[int64]` 明确类型（`$ramdiskPadded = [int64]$rdAddr + [int64]([Math]::Ceiling([double]$rdSize / [double]0x2000000)) * [int64]0x2000000`）；
2. 加了 `CheckSafe` 包装：**异常一律计为 FAIL**，不再有"跳过即通过"。

修完重跑：18 项全过，且镜像那一行现在会打印 `ramdisk_padded_end=0x3000000`，把这次改动的原因直接写在输出里。

### 18.43 两张主候选的 boot header id 是错的（重算后已修正）

Android boot header v2 里有一个 32 字节的 `id`，是 `sha1(kernel, kernel_size, ramdisk, ramdisk_size, second, second_size, dtbo, dtbo_size, dtb, dtb_size)`。**更换 DTB 之后必须重算**，否则这个字段就是陈旧的。

查了四张候选（`v2` 与已知能启动的 r4 镜像做对照）：

| 镜像 | 修正前 | 结论 |
| --- | --- | --- |
| `boot-r4-known-good.img`（r4，实际刷过并启动成功） | 与内容一致 | 对照正常 |
| `boot-r4-camera-v2.img` | 与内容一致 | 正常（DTB 没换过，`dtb_addr` 不在 id 覆盖范围内） |
| `boot-r4-camera-cphy2.img` | 与内容一致 | 正常 |
| `boot-r4-camera-cphy3.img` | `ffdef77f…`，应为 `84dfb18f…` | **陈旧** |
| `boot-r4-camera-v3c.img` | `a99d3b31…`，应为 `73403c45…` | **陈旧** |

原因很直白：`cphy3`/`v3c` 是把新编的 DTB 塞进已经构建好的镜像里，id 没跟着重算。**两张主候选恰好都中招**。

已用脚本重算四张的 id 并写回 `0x240`，再逐张验证与内容一致；这一步也并进了 `check-candidate-dtb.py`，以后每次内容检查都会顺带验证 id。

**这能不能解释 00:22 那次挂起？不确定，但它是真实缺陷。** 若 ABL 对 id 有校验，陈旧 id 足以让 `fastboot boot` 在很早期失败；若没有校验，改了也无害。两种情况下都该修。

### 18.44 我们的镜像 vs 已知能启动的 r4 镜像：只有 4 个字段不同

逐字段对比 boot header（`boot-r4-known-good.img` 是实际刷进 boot_a 并成功启动过的镜像）：

| 字段 | r4 已知可启动 | `cphy3` 候选 | 说明 |
| --- | --- | --- | --- |
| `kernel_size` | 15,308,718 | 15,308,744 | 差 26 B，就是内嵌 config 多了 `CONFIG_VIDEO_IMX586=m` |
| `dtb_size` | 132,213 | 133,189 | 差 976 B，后摄节点 + 三个 pinctrl 状态 |
| `dtb_addr` | `0x1f00000` | `0x08000000` | 为 `fastboot boot` 主动改的（见下） |
| `id` | `51d45cc6…` | `84dfb18f…` | 内容变了，已重算（§18.43） |

其余全部相同：magic、`kernel_addr`、`ramdisk_addr/size`、`tags_addr`、`page_size`、`header_version`=2、`os_version`、`name`、**`cmdline`**、`extra_cmdline`、`dtbo_size`=0、`header_size`=0、总长度 47,869,952 B。

也就是说：**这张候选在结构上与一张确实能启动的镜像几乎一模一样**，差别只在"多了一个后摄节点 + 多了一条模块配置 + DTB 放在哪儿"。

### 关于 00:22 那次挂起的一个新解释（假说）

`dtb_addr` 原来改成 `0x3000000`，理由是"在 ramdisk 原始结束地址 `0x2eea827` 之后"。但 ramdisk 按 32 MB 补齐后，结束地址**正好是 `0x3000000`**。

如果加载顺序是"先放 DTB，再写 ramdisk"，而 ramdisk 的写入范围按补齐后的块来算，那么 **DTB 的头几个字节会被 ramdisk 覆盖**——内核拿到的是一份被踩坏的 DTB，表现就是"没有任何输出地挂住"，与 00:22 的现象一致。

这条只是假说，无法在设备缺席时验证。但把它当真的代价很低：`dtb_addr` 已经挪到 `0x08000000`，与 kernel（`0xea17c8`）、ramdisk（补齐后 `0x3000000`）都不再有任何交集。

另外，`id` 陈旧（§18.43）也可能单独导致早期失败——两张主候选当时都带着陈旧 id。这两个缺陷叠加，足以解释"fastboot 回执 OK 但手机就是不起来"。

### 18.45 更正 §18.41：`0x3000000` 是**实测两次成功过**的地址，撤回改成 0x08000000

翻旧文档 [相机 fastboot 循环](camera-fastboot-loop-20261110.md) 时看到一句被我漏掉的关键记录：

> boot 模式也已实测（2026-11-10 23:24 / 23:27）：`boot-r4-dtb48.img`（SHA c50ccdeb…）经 PON 进 fastboot，`fastboot boot` 成功，抓帧后正常回 baseline；`returned_to_baseline=true`。原 r4 镜像 dtb 地址 0x1f00000 与 32 MB ramdisk 重叠，`fastboot boot` 会报 Load Error；**必须用 0x03000000**。

也就是说：

- `0x1f00000` → Load Error（**实测**）；
- `0x3000000` → `fastboot boot` **成功过两次**（23:24、23:27，同一晚、同一台机）；
- `0x08000000` → 我按"32 MB 补齐后恰好压在 0x3000000"的推理改的，**从未实测**。

§18.41 的理由是推理，而 0x3000000 是实测。按"实测优先于推理"的原则，四张候选的 `dtb_addr` 已全部改回 **`0x3000000`**，保留 §18.43 的 id 修正（那是确定性的缺陷，与地址无关——`dtb_addr` 本来也不在 id 的哈希范围内，改回后四张 id 仍然正确）。

§18.44 里"DTB 被 ramdisk 补齐块覆盖"的假说因此**降级**：如果补齐块真会踩到 0x3000000，23:24/23:27 那两次就该失败。它现在只能算"未排除的次要可能"，不能当作主因。

这也意味着 00:22 那次挂起**更可能是设备状态或别的因素**，而不是镜像布局——毕竟同一布局当晚成功过两次。watchdog 的判读表里"v2 也挂 → 引导/设备状态问题"这条分支因此更有参考价值。

### 18.46 第一张候选改成"对照镜像"：它在这台机上真的 `fastboot boot` 成功过

`local/kexec-test/boot-r4-dtb48.img`（SHA `c50ccdeb8d97…`，47,869,952 B）就是 §18.45 引用的那张镜像：**2026-11-10 23:24 / 23:27 两次 `fastboot boot` 成功、进入 Debian、抓帧后正常回 baseline**。

逐项验证它确实"干净"：

| 项 | 值 |
| --- | --- |
| kernel | 15,308,718（r4 内核） |
| dtb_size | 132,213（r4 DTB） |
| dtb_addr | `0x3000000` |
| header id | **与内容一致** |

把它加到候选列表**最前面**（`r4ctl`），并且让 runner 对它跳过相机身份检查与诊断：

```
r4ctl  -> 只验证"能不能启动到 SSH"，不做相机测试
v2     -> C-PHY/D-PHY 都无关的安全基线
cphy3  -> C-PHY + pinctrl（主候选）
v3c    -> D-PHY + pinctrl
cphy2  -> C-PHY，无 pinctrl（对照）
```

> 顺序后来调整过一次（见 **18.110**）：`v3c` 与 `cphy3` 对调，D-PHY 排前面。上面这张表是当时刚组好列表的状态。

这样**今晚第一次启动**就会回答一个到今天为止一直悬着的问题：`fastboot boot` 这条路在当前设备状态下到底还通不通。

- `r4ctl` 到 SSH → 临时启动路径正常，后面几张起不来就只可能是相机相关改动；
- `r4ctl` 也到不了 SSH → 是设备状态/临时启动路径本身，与相机代码无关，早上该做的是给手机断电重来或换启动方式，而不是继续调 DTS。

预检也加上了这张镜像的校验（SHA 前缀 `c50ccdeb` + `dtb_addr=0x3000000`），dry-run 已确认 `r4ctl:ssh-up` 分支走得通。

### 18.47 补上 ABL 槽位护栏：不允许在槽位不健康时自动重启

[fastboot 循环说明](camera-fastboot-loop-20261110.md) §5 里有一条硬规则：

> boot_a 的 successful=0 或 tries<3 时，禁止任何自动重启/PON 循环。

那是因为 11/10 出过一次真实的"系统文件被损坏，无法开机"——实际上是 ABL 的 A 槽记账耗尽（successful=no、retry=0、unbootable=yes），起因就是连续 PON 重启却没人置成功位。

我们的 `auto-camera-run.ps1` 会连续做 5 次 `fastboot boot` + `fastboot reboot`，**原来完全没有这道检查**。这相当于把那次事故的条件又摆了一遍。

已补上 `SlotHealthy()`：进入 fastboot 后先读四个标准变量——

```
fastboot getvar slot-successful:a
fastboot getvar slot-retry-count:a
fastboot getvar slot-unbootable:a
```

- `slot-successful:a: no` → 中止整轮；
- `slot-unbootable:a: yes` → 中止整轮；
- `slot-retry-count:a` < 3 → 中止整轮；
- 读不到（例如设备刚起来还没应答）→ 记录后继续，保持原有行为。

所有判断都会打印到日志（`slot: slot-successful:a: yes  slot-retry-count:a: 6  slot-unbootable:a: no`）。

另外把"结束时若仍在 fastboot 就 `fastboot reboot`"这一步也加了条件：**槽位不健康时故意留在 fastboot**，不自动重启——因为这种重启会再烧一次 retry，正是事故的触发方式。

用两套桩各跑了一次：

```
健康：slot: slot-successful:a: yes  slot-retry-count:a: 6  slot-unbootable:a: no
      -> 继续启动 r4ctl，summary: r4ctl:ssh-up
不健康：slot: slot-successful:a: no  slot-retry-count:a: 1  slot-unbootable:a: no
      -> boot_a is not marked successful - refusing to reboot into it
      -> 中止，并打印 "leaving the phone in fastboot on purpose"
```

### 18.48 一条命令看完全部结果：`triage-camera-results.py`

早上要读的东西原来分散在 `camera-test.log`、若干份 `diag-*.json.txt`、以及各自的 `VERDICT` 里。新写了一个小工具，把每份诊断压成一行：

```
tag    verdict                    acks  driver                              capture  rails
r4ctl  no-cci-adapter             -     -                                   -        
cphy3  ack-at-0x1a-but-no-driver  0x1a  -                                   -        l1=enabled/1104000 l4=enabled/1800000 l7=enabled/2904000
v3c    rear-frame                 0x1a  /sys/bus/i2c/devices/0-001a/driver  rear-a
```

用法（在 WSL 里）：

```
cd /mnt/f/codexprojects/oneplus8t/local/camera-r4-20261110
python3 triage-camera-results.py diag-*.txt        # 省略参数则自动找本目录
```

实现上注意了三点：

1. 诊断文件里 `REAR_RESULT` / `VERDICT` 两行在 JSON 之前，所以从**第一行以 `{` 开头**的位置开始解析；
2. 解析失败**不抛异常**，那一行标 `unreadable` 并附原因——早上不该因为一份坏文件看不到其余的；
3. `acks` 列出**所有**应答过的地址（不只 0x1a），因为 §18.39 说明了地址本身还没有实测依据，如果出现 `0x10` 那才是真相。

8 项离线测试（含 log 行在前的解析、坏 JSON、非对象 JSON、`warning` 键不算 ACK、稳压器过滤、抓帧与驱动字段）。

### 18.49 一个会把人带偏的坑：步骤 B 可能还在跑**根文件系统里的旧 camss**

先把模块形态查清楚（用实际构建出的 `.config`，不是仓库里的片段）：

```
CONFIG_VIDEO_QCOM_CAMSS=m      CONFIG_I2C_QCOM_CCI=m
CONFIG_VIDEO_IMX586=m          CONFIG_V4L2_CCI=m
```

四个都是模块，所以流程本身成立。但也正因为是模块，存在这个风险：

- 开机时 udev 会从 `/lib/modules/7.2.0-…` 按需加载 **根文件系统里的 `qcom-camss`**——那是 r4 的构建，**没有 C-PHY 支持**；
- 步骤 B 的做法是 `rmmod imx586` → `rmmod qcom_camss` → `insmod /tmp/qcom-camss.ko`；
- 如果 `rmmod` 被拒（还有引用、sensor 还绑着），`insmod` 就会以 `File exists` 失败，**步骤 B 会继续用 r4 的模块**跑，然后报"没有帧"。

这种情况下早上的结论会是"C-PHY 不行"，而真相是"我们的模块根本没加载"——方向直接错掉。

**改法**：步骤 B 之后读两边的 `srcversion` 比对（模块内容变了，srcversion 一定不同）：

```sh
echo -n "file=";  modinfo -F srcversion /tmp/qcom-camss.ko
echo -n "loaded="; cat /sys/module/qcom_camss/srcversion
```

新增判据 `stock-camss-still-loaded`：两边不一致且**没有任何帧**时给出这个结论。

特意做成"帧优先"：D-PHY 的情况下 r4 的模块本来就能用，只要抓到了帧就算成功，不能因为 srcversion 不同就把成功报成失败。19 项离线测试（新增 3 项：能识别、同 srcversion 不误报、有帧时不误报）。

### 18.50 模块依赖：`qcom-camss` 需要 videobuf2，`imx586` 不需要 v4l2-cci

用 `llvm-nm -u` 与 `modinfo -F depends` 查了两边的真实依赖：

| 模块 | depends | 说明 |
| --- | --- | --- |
| `qcom-camss.ko` | `videobuf2-common,videobuf2-dma-sg,videobuf2-v4l2` | 三个 videobuf2 |
| `imx586.ko` | *（空）* | **不依赖 v4l2-cci** |

`imx586.ko` 的 56 个未定义符号里没有一个 `v4l2_cci_*`，也没有 `regmap_*`——它直接用 i2c 传输。所以之前往 `deploy-cphy/` 里放 `v4l2-cci.ko` 是多余的，已删除。

但 `qcom-camss` 依赖 videobuf2：如果开机时 camss 没被自动加载，那三个 videobuf2 也不会在，`insmod /tmp/qcom-camss.ko` 就会以 `Unknown symbol` 失败。步骤 B 因此增加一步：

```sh
sudo -n modprobe -a videobuf2-common videobuf2-dma-sg videobuf2-v4l2
```

另外新增判据 **`camss-load-failed`**：步骤 B 结束后 `b_still_loaded` 是 `none`（模块根本没加载上）时直接给出这个结论，而不是含糊地报"没有帧"。

20 项离线测试（新增 1 项）。

### 18.51 端到端桩测抓到一个真问题：`b_scan` 从来没被生成过

给整个诊断流程写了一个"把所有 shell 调用都打桩"的冒烟测试（`test-camera-full-diag-flow.py`）：替换 `run`/`cci_scan`/`capture`/`regulators`/`irq_*`/`dt_rear_path`，然后真的跑一遍 `main()`，解析它打印的 JSON。

第一次跑就抓到：**`main()` 只做了一次 I2C 扫描（步骤 A），步骤 B 之后没有再扫**。而 `verdict()`、triage 工具、以及文档里的判读表都引用 `b_scan`——也就是说这个字段一直是空的。

这不是纯形式问题：步骤 B 换上打过补丁的 camss 之后，CSIPHY 的 endpoint 才是第一次被解析；如果 CCI 适配器是这时候才出现的（或者反过来），**只有步骤 B 的扫描能看到**。已在 `b_insmod_imx` + `sleep 2` 之后补上第二次 `cci_scan()`。

现在的测试分布：

| 测试 | 数量 | 覆盖 |
| --- | --- | --- |
| `test-camera-full-diag.py` | 20 | `verdict()` 各分支、地址扫描、抓帧判定、srcversion、模块加载失败 |
| `test-camera-full-diag-flow.py` | 4 | 整条 `main()`：JSON 字段齐全、无 CCI 适配器、抓帧成功、camss 未加载 |
| `test-triage-camera-results.py` | 8 | 早上的汇总表 |

桩测还有两次是**桩不真实**导致的假失败（先让 `lsmod` 一律返回 `none`，于是每轮都被判成 `camss-load-failed`；`readlink` 不许返回非空，否则会被当成"驱动已绑定"）。这两次都改的是桩，不是产品——但也顺手确认了 `verdict()` 的这两个假设在真实输出下成立：未绑定就是空输出、未加载就是 `none`。

### 18.52 通读 runner 时发现一个会让整夜白跑的 bug：`SshOk -and $NoPon`

把 `auto-camera-run.ps1` 从头到尾重读了一遍，"每步流程"里有一行：

```powershell
if (SshOk -and $NoPon) { Log "NoPon set: leaving the phone alone"; return $false }
```

在 PowerShell 里，**裸写的 `SshOk -and $NoPon` 不是"函数返回值 与 变量"**：`SshOk` 被当成命令，`-and` 和 `$NoPon` 被当成它的参数，整个条件等于**只取 `SshOk` 的真值**，`$NoPon` 完全不参与。

实测对照（`paramtest` / `parsetest` 两个一次性脚本，跑完即删）：

| 写法 | SshOk | `$NoPon` | 期望 | 实际 |
| --- | --- | --- | --- | --- |
| `if ((SshOk) -and $NoPon)` | false | false | 不进 | 不进 ✓ |
| `if ((SshOk) -and $NoPon)` | true | false | 不进 | 不进 ✓ |
| `if ((SshOk) -and $NoPon)` | true | true | 进 | 进 ✓ |
| `if (SshOk -and $NoPon)` | **true** | **false** | 不进 | **进 ✗** |

后果非常具体：**只要手机 SSH 能连上**（这是最常见的情形——手机正常起 r4、SSH 通、watcher 发现它就开跑），`ToFastboot()` 就会打印 `NoPon set` 并返回 false，整轮立刻中断，一行诊断都跑不到。

之前几次 dry-run 之所以看起来正常，是因为起跑瞬间 `booted.flag` 不在、`SshOk` 返回 false，恰好绕过了这个分支——**桩的时序把 bug 藏住了**。是"五个候选跑满一轮"的 dry-run 才把它逼出来：第二次 `ToFastboot`（手机已在 SSH 里）立刻暴露。

已改为 `if ((SshOk) -and $NoPon)`，并加注释说明为什么不能省括号。修完用同一套桩跑满五张候选：

```
summary: r4ctl:ssh-up  v2:a=0 b=0/no-ack-at-0x1a  cphy3:a=0 b=0/no-ack-at-0x1a
         v3c:a=0 b=0/no-ack-at-0x1a  cphy2:a=0 b=0/no-ack-at-0x1a
```

每张都走完了：槽位检查 → `fastboot boot` → 等 SSH → 身份核对 → 推 4 个文件 → 诊断（12 分钟硬超时）→ 保存 `diag-*.txt` → 解析 verdict → 下一张。最后用 triage 工具读这四份产物，表格正常输出。

### 另外两处顺手加固

1. **诊断有硬超时了**：`ssh` 在远端命令卡住时不会自己超时，现在 `RunDiag` 用 `Start-Job` + `Wait-Job -Timeout 720`，超时就写一条 `VERDICT diag-timeout` 继续走，不会把整夜挂死在一个 capture 上；
2. **推的脚本就是预检检查的那一份**：原来 runner 推 `$base/camera-full-diag.py`、预检查 `deploy-cphy/` 那一份，两份虽然当下相同但没有约束。现在都指向 `deploy-cphy/`，并由测试脚本 `cmp` 保证两份一致。

### 18.53 watcher 也端到端跑了一遍，顺手补两处"该停就停"

wacher（`camera-autowatch.ps1`）之前只做过"进程在、日志能写"的静态检查。这一轮用同一套桩把它整条跑通：

1. `-Hours 1 -MaxPasses 2 -RetryMinutes 0`：发现手机 → 调 runner → 五张候选跑满 → 无帧 → 立即重试 → 第二轮同样跑满 → `autowatch exit`；两轮的完整输出分别落在 `autowatch-run-<时间>-p1.txt` / `-p2.txt`；
2. 用"槽位不健康"的桩再跑一次：runner 打印 `boot_a is not marked successful` 并故意留在 fastboot，watcher 打出 `boot slot is unhealthy - stopping instead of retrying` 后退出，**没有**做第二轮。

两处改动：

- **成功判据双保险**：原来只看 runner 的输出里有没有 "rear camera produced a frame"。runner 那行是 `Write-Host` 写的，虽然子进程重定向后能捕获，但没必要赌——现在同时查 `camera-test.log` 的最后 25 行。
- **槽位不健康就停**：看到 `not marked successful` / `flagged unbootable` / `refusing to reboot into it` 时**不重试**。重试就是再烧一次 retry，正是 11/10 那次事故的成因。

### 18.54 把 runner 的三条失败分支也各跑了一遍

之前只测过"一切正常"和"槽位不健康"。今晚最可能遇到的其实是另外三种，于是各做一套桩，实跑：

| 桩 | 模拟的现场 | 实测结果 |
| --- | --- | --- |
| `sim-wrongkernel` | 手机跑的还是 r4（`/proc/config.gz` 里没有 `CONFIG_VIDEO_IMX586`） | 每张候选打 `this does not look like our image`，`summary: r4ctl:ssh-up v2:wrong-kernel cphy3:wrong-kernel …` |
| `sim-pushfail` | `scp` 被拒（四个文件全 rc=1） | 不拿旧文件硬跑诊断，打 `push failed - not running the diag against stale files`，`summary: … v2:push-failed` |
| `sim-bootfail` | `fastboot boot` 回 `FAILED (remote: Load Error)` | **立刻停**，不等那 7.5 分钟 SSH，`summary: r4ctl:boot-failed` |

第三条尤其重要：Load Error 是 11/10 就实测出现过的失败方式（`dtb_addr` 与 ramdisk 重叠时）。原来这条分支只打日志、`summary` 是空的——早上看到空 summary 不好判读。现在各种中止都会在 summary 里留一个明确的词：`:wrong-kernel` / `:push-failed` / `:boot-failed` / `:no-fastboot` / `:no-ssh`。

这五套桩（`sim`、`sim-unhealthy`、`sim-wrongkernel`、`sim-pushfail`、`sim-bootfail`）都留在工作目录里，SHA 清单一并覆盖，以后改脚本可直接回归。

### 18.55 诊断卡住的那条路也验了：5 秒超时 + 慢应答桩

`RunDiag` 用了 `Start-Job` + `Wait-Job -Timeout 720`，但"超时真的会发生什么"从没跑过。把 runner 复制一份、把 720 改成 5，配一个"诊断调用睡 20 秒才回"的桩（`sim-slowdiag`），用 `-Image` 只跑一张：

```
03:38:49 fastboot boot: Booting OKAY
03:38:49 promoted 4 files rc=0
03:39:02 diag saved: diag-…-custom.json.txt
03:39:02 candidate custom gave no rear frame (a=0 b=0/diag-timeout); moving on
03:39:02 summary: custom:a=0 b=0/diag-timeout
```

从 boot 到结束 14 秒（5 秒超时 + 开销），`Stop-Job` 把挂住的 job 清掉、`Remove-Job` 不留残余，写进文件的是一条真正的 `VERDICT diag-timeout`，summary 里也能看到——**不会因为一个卡住的 capture 把整夜挂死**。

顺带验了 `-Image` 单张模式（日志开头 `Image=[…cphy3.img]`，候选只有一个 `custom`）。

测完把那份"改过超时的 runner 副本"删掉了——留着它就是一份会和真身分叉的重复脚本，是最容易误用的东西。要复现就临时 `sed` 一下 720 即可，不留在盘上。

### 18.56 整理目录之后 preflight 立刻报错——是它依赖了一个我挪走的文件

把 417 个一次性排查脚本挪进 `probes/` 之后重跑预检，`candidate DTB content` 直接 FAIL。

原因不是 DTB 有问题（单独跑 `check-candidate-dtb.py` 是 `RESULT: 2/2 candidates ok`）：预检第 6 项是通过一个**小包装脚本** `run-candidate-check.sh` 调 python 的（因为 powershell → wsl → bash 的三层引号很容易被吃掉，这是这个项目早就踩过的坑），而那个包装脚本不在"保留清单"里，被一起挪走了。

两处修正：

1. 把 `run-candidate-check.sh` 放回顶层并列进清单；
2. 预检里加一条存在性判断——**helper 不在就明确报 "helper missing"**，而不是拿空输出去解析然后只说一句 FAIL。

修完重跑：19 项全过，`PREFLIGHT OK`。

教训很小但值得记：**"整理目录"这类看起来无害的动作，会把隐式依赖打断**。所以整理之后必须把全部自检重跑一遍——这次正是这么发现的。

### 18.57 把"改完必须重跑全部自检"做成一条命令

§18.56 的教训是"整理目录打断了隐式依赖"。要防这类问题，靠记性不行，所以加了一个 `check-all.ps1`：

```
[PASS] preflight                        （19 项）
[PASS] diagnostic unit + flow tests     （22 + 4 项）
[PASS] triage tests                    （8 项）
[PASS] manifest (44 entries)           （ok=44 bad=0）
[PASS] patch reproducibility (slow)    （-Slow 时才有，0002–0005 在干净树上重放并 cmp）

phone in fastboot: no
ALL CHECKS PASS
```

每个 Stage 只在**看到预期字符串**时算过（`PREFLIGHT OK` / `copies identical` / `OK` / `bad=0` / `rc=0`），失败时打印最后 6 行原始输出——不靠"退出码为 0"这种弱判据。

两种模式都实跑过：不带 `-Slow` 4 项全过（约 10 秒），带 `-Slow` 5 项全过（约 2 分钟）。

### 18.58 两个"能不能跑起来"的前提，用项目自己的成功记录核过了

今晚整条链都依赖两个前提，之前只是"应该是这样"，这一轮去翻 11/10 的实际运行记录确认。

**前提一：`sudo -n` 在这台机上可用。**

`scripts/linux/fastboot-camera-loop.py`（11/10 用它跑通过）里有：

```python
ssh_cmd(a, "sudo -n python3 /tmp/boot-slot-state.py", timeout=30)
ssh_cmd(a, "sudo -n /usr/local/sbin/kebab-reboot-fastboot", timeout=20)
```

而且 `local/camera-runs/20261110-233051-614b3c/run.json` 记着 `ok: true`——**成功过**。我的诊断用 `sudo -n python3 /tmp/camera-full-diag.py`，形状与 `sudo -n python3 /tmp/boot-slot-state.py` 完全一致（同为 `sudo -n python3 <路径>`），所以 sudoers 那条规则能覆盖到。

**前提二：抓帧脚本的命令行契约。**

过去跑通的调用是 `python3 /tmp/camera-capture-test.py --camera <front|rear> --output-dir /tmp/camera-lab`；诊断里的调用逐字一致（`sys.executable` + 同样的两个参数），脚本本身 `--camera` 的 choices 就是 `["front", "rear"]` ✓。子进程另加 240 秒上限，脚本内部每次抓帧 25 秒超时。

顺带确认了 00:22 那次失败的记录长什么样——`local/camera-runs/20261111-002222-1d193a/run.json`：

```json
"boot_image": ".../boot-r4-camera.img",  "entry": "pon",
"ok": false,
"error": "Command ['fastboot', '-s', 'b5fcd385', 'reboot'] timed out after 60 seconds"
```

即：PON 进 fastboot、`fastboot boot` 之后手机再也不应答 `fastboot reboot`——这就是"失联"的原始记录，也是为什么今晚要加对照镜像和槽位护栏。

### 18.59 把 00:22 那张镜像的 DTB 拆出来，看它到底做了什么

那张镜像是 `boot-r4-camera.img`（现在在 `history/`）。把它的 DTB 和"已知能启动的 r4 DTB"都反编译，逐行对比：

| 项 | 00:22 那张 | 今晚的 `cphy3`/`v3c` |
| --- | --- | --- |
| `pm8008_l7` | **钉死 2,896,000 µV**（min=max） | 保持 1.5–3.4 V 范围 ✓ |
| `regulator-cam-rear-vana` | **有**：`gpio = <&tlmm 0x1a>`（第 26 脚，**没有出处**） | 不存在 ✓ |
| `vana-enable-gpios` / `custom1-gpios` | **没有** | 有（139 / 117，按厂商标签） ✓ |
| 内核 | 15,310,410 B（更早的一次构建） | 15,308,744 B（r4 内核 + 一行配置） |
| `dtb_addr` | 0x3000000 | 0x3000000 |

也就是说那张镜像同时带着 §18.35 里被我撤回的"钉死 VANA"，以及一个**凭空造出来的、挂在 tlmm 26 上的 fixed regulator**。

### 与 00:22 日志对得上的部分

`local/camera-runs/20261111-002222-1d193a/run.json` 里记的错是 `fastboot reboot` 超时，而 `fastboot-camera-loop.py` 里那条 `fastboot reboot` 只出现在**"候选没能在超时内到达 SSH"**的分支（第 276–281 行）。由此可还原：

```
fastboot boot      -> 返回 0（被接受，不是 Load Error）
wait_ssh 240 秒    -> 超时，内核没进用户态
fastboot reboot    -> 60 秒无应答，手机从此不响应 USB
```

所以 00:22 不是"镜像被拒"，而是**内核起来后极早期就死了、随后连 fastboot 都不应答**。具体死在哪一步仍然没有证据（没有串口、没有 pstore），但**今晚的四张候选已经把这三点全部去掉了**：L7 是范围、没有自造 regulator、内核就是 r4 那个。

这也是为什么候选顺序从 `v2` 开始有意义：`v2` 的 DTB 与那张**实际刷进 boot_a 并启动成功过**的 r4 镜像逐字节相同。**如果 v2 也到不了 SSH，那问题就不在相机改动上**。

### 18.60 身份判据成立：`/proc/config.gz` 确实存在，而 `uname -r` 不足以区分

runner 判断"跑的是不是我们的镜像"，靠的是 `/proc/config.gz` 里有没有 `CONFIG_VIDEO_IMX586`。这个判据要成立，得有两个条件，都去核了：

| 条件 | 实测 |
| --- | --- |
| 内核带 `CONFIG_IKCONFIG=y` + `CONFIG_IKCONFIG_PROC=y` | **都有**（r4 的构建配置里本来就有，不是我们加的） |
| `uname -r` 能不能区分 | **不能**：两边都是 `7.2.0-kebab-debian13-r4-venus+`（`CONFIG_LOCALVERSION` 相同） |

所以用 `/proc/config.gz` 是**必要且正确**的做法——仅看 `uname -r` 会把 r4 内核误认成候选内核。

顺带记录：想直接从 Image 里抠出内嵌 config 做离线对比没成功（按 gzip 魔数扫描没有命中完整成员），但不影响上述结论——`/proc/config.gz` 是运行期生成的，只要 IKCONFIG 编进去就在。

### 18.61 两件事：窗口改成 24 小时；`check-all.ps1` 自己也有一处"假通过"

**一、把 watcher 窗口从 14 小时换成 24 小时。**

目标写的是"一直到摄像机真正能用为止"，那就不该被一个夜间窗口卡住。原进程（PID 41092）停在等待循环里，直接停掉重开：

```
2026-11-11 03:51:08 autowatch start, hours=24 maxpasses=2
2026-11-11 03:51:08 pass 1/2 - waiting for the phone
```

新窗口到 **11/12 03:51**，覆盖今天一整天。

**二、`check-all.ps1` 里 `$fail++` 是局部变量，永远不会累加。**

改成 24 小时窗口后先跑了一次自检，输出是这样——

```
[FAIL] manifest (44 entries)
       ok=44 bad=2
       STATUS.md: FAILED

ALL CHECKS PASS        <-- 自相矛盾
```

原因是 PowerShell 函数有独立作用域：`Stage()` 里的 `$fail++` 只是在函数内新建了一个局部 `$fail`，外层的 `$fail` 一直是 0。**判据本身错了，于是"有失败"也报"全部通过"**——和我这几轮一直在防的"假通过"是同一类错误，只不过这次出现在我自己写的检查器里。

改成 `$script:fail` 之后复测：同样故意让 `check-all.ps1` 自己比清单新一版，输出变成

```
[FAIL] manifest (44 entries)
       check-all.ps1: FAILED
FAILURES: 1
```

——终于是对的（而且这次失败是**真**的：我确实刚改过这个文件，清单该更新）。重生成清单后 4 项全过。

### 18.62 最后一个检查器也查了：预检的计数是对的，但它的异常护栏是死代码

连着三次踩到"检查器假通过"（`SshOk -and $NoPon`、预检里 `.ToString("x")` 抛异常、`check-all.ps1` 的 `$fail++`），所以把**最后一个没查过的检查器** `preflight-camera.ps1` 也审了一遍：

- `Check()` 里用的是 `$script:fail++`，**计数是对的**（这也解释了第 18.56 轮它确实报出过 `PREFLIGHT FAILURES: 1`）；
- 但为"异常不该被跳过"而写的 `CheckSafe()` **定义了却从未被调用**——纯死代码。镜像检查那段仍然会读字节（`[BitConverter]::ToUInt32($bytes, 0x670)`），一旦镜像被截断就会抛异常，而异常发生在 `Check` 之前，于是那一项被**整体跳过**。

改法：删掉没用的 `CheckSafe`，把镜像检查的循环体包进 `try/catch`，捕获里调用 `Check $k $false "exception: ..."`。

**用一个截断的镜像实测**（把 `cphy2` 临时换成 100 字节的桩，跑完立刻还原并核对 SHA）：

```
FAIL  boot-r4-camera-cphy2.img  exception: Exception calling "ToUInt32" with "2" argument(s):
      "Index was out of range. Must be non-negative and less than the size of the collection."
PREFLIGHT FAILURES: 1
```

还原后 `PREFLIGHT OK`，镜像 SHA 与替换前一致（没动过真文件）。

### 18.63 把入口补到文档索引里

`docs/README.md` 的"当前入口"里原来只有 r4-camera 的**任务书**和 11/10 的 fastboot 循环说明，没有今晚这两份：

- `../local/camera-r4-20261110/STATUS.md`（早上第一眼看的那份，等手机、候选表、判读表、恢复步骤、一条自检命令）；
- `r4-camera-boot-failure-20261111.md`（本文，§18.1–18.63 的取证）；
- `r4-camera-20261110.md`（主报告：C-PHY 移植与 DTS/驱动最终形态）。

已补进索引顶部。理由很简单：**如果入口找不到，前面所有工作对早上那个人来说就等于不存在**。

### 18.64 确认"手机不在场"这个否定结论本身是可信的

"`fastboot devices` 为空"只有在**工具本身正常**时才算证据。逐个核过：

| 检查 | 结果 |
| --- | --- |
| `fastboot --version` | `37.0.0-14910828`，`E:\adroid\platform-tools\fastboot.exe` ✓ |
| `adb` 是否装上并能跑 | 装上，`adb devices` 正常输出空表 ✓ |
| 当前插着的 USB 设备 | 只有 USB 集线器 / `USB Composite Device`（键鼠一类），**没有任何 Qualcomm/Android 设备** |
| 串口（EDL 的 9008 常以串口出现） | **无任何 COM 口** |

所以这不是"工具坏了看不到设备"，而是**真的没有任何设备**。否证成立。

### 18.65 "没有后摄节点"不会把诊断拖死——失败是快的

担心的场景：后摄没能 probe 时，抓帧脚本会不会在等节点上耗掉几分钟，把 5 张候选 × 每张 4 次抓帧拖成不可接受的时长。读代码确认**不会**：

- `--camera rear` 先调 `discover_rear()`，找不到 `imx586` 实体就 `raise RuntimeError("no imx586 sensor entity in media graph")` —— **立刻失败**，不轮询、不 sleep；
- 该异常在 `main()` 的 `try` 里被接住，输出 `ok: false` + 原因，退出码正常；
- 只有"找到了节点但收不到帧"才会等，而且被 `timeout 25 v4l2-ctl` 卡住上限。

据此估算每张候选的诊断耗时上界：前摄两次 + 后摄两次抓帧 ≈ 2 分钟以内（最坏情况），加上两次 CCI 扫描与寄存器快照，**远小于 runner 给的 720 秒**。也就是说 `diag-timeout` 那条分支只是保险，正常情况下不会触发。

### 18.66 自检这份文档本身：编号无重无缺、交叉引用无悬空、本地链接有效

80 KB 的取证文档里最容易出的低级问题就是"引用了一个不存在的章节"。用脚本过了一遍：

| 检查 | 结果 |
| --- | --- |
| `###` 一级小节编号 | **65 个，1–65 连续，无重复、无缺号** |
| 正文里的 `§18.N` 交叉引用 | 13 处，**全部命中真实小节** |
| 本地文件链接 | 4 个，全部存在 |

（脚本第一次跑时报了一个"坏链接"，实际是文档里那段 PowerShell 代码 `[Math]::Ceiling(...)` 被正则误认成 markdown 链接——不是真链接，记录在此以免下次误判。）

另外两份文档（`r4-camera-20261110.md`、`STATUS.md`）的本地链接也都有效。

### 18.67 通读 runner 的收尾：五种结局下手机分别停在哪

之前只验了"每条失败分支会不会被触发"，没细看**跑完之后手机处于什么状态**。逐个走了一遍：

| 结局 | 手机停在哪 | 代码依据 |
| --- | --- | --- |
| 出帧 | 那张候选上（临时内核） | 第 170–172 行 `break` |
| 五张都无帧 | 最后一张候选（`cphy2`）上 | 循环自然结束，`$fbNow` 为空，不触发收尾重启 |
| `no-ssh` | 卡死状态 | 第 142–145 行：记 `needs a physical power cycle` 并 `break`，**不再动后面的候选**（对：设备不应答时继续只会白烧） |
| `boot-failed` / 进不了 fastboot | 留在 fastboot | 第 179–186 行显式 `fastboot reboot` |
| A 槽不健康 | **故意留在 fastboot** | 第 180–181 行，理由是自动重启会烧掉最后的 retry |

关键点：**全部是 `fastboot boot`，没有任何分区被写**。所以"停在临时内核"和"停在 fastboot"都只是运行态，重启即回到 r4，不存在变砖风险。这一点已经写进 `STATUS.md` 的"跑完之后手机停在什么状态"一节。

### 18.68 watcher 改成脱离会话运行（原来会话一结束就没人守了）

原来的 watcher 是用后台作业起的，属于**本会话的子进程**。也就是说：会话进程一退出（或 DSH 重启），watch 就没了，而窗口本该覆盖到 72 小时后。

改成 `Start-Process -WindowStyle Hidden` 独立进程之后，实测到了一个干净的证据：

```
watcher  pid=45628  ppid=26580   (26580 已退出)
parent 26580 alive: False        <- 父进程没了，watcher 还活着
```

**父进程已经不存在，watcher 仍在运行**——这才是真的脱离，而不是"看起来还在跑"。

顺带把停法写进 `STATUS.md`：现在它不属于任何作业，只能用进程名停，命令已给出。

### 18.69 回头挑自己最核心的那个假设，重新用源码验一遍

整个 C-PHY 方案建立在两句话上：**SM8250 的 CSIPHY 走的是三相（3ph）那套 ops**，以及**那套 ops 同时支持 D-PHY**。这两句如果错了，五张候选的设计前提就全错。重新在源码里逐条确认：

**一、`csiphy_res_8250` 里 6 个 CSIPHY 全部用 `csiphy_ops_3ph_1_0`**

```
.hw_ops = &csiphy_ops_3ph_1_0   x6   （CSIPHY0..5，无一例外）
.hw_ops = &csid_ops_gen2      x2   （那是紧邻的 CSID 数组，不是 CSIPHY）
```

`sm8250_resources` 里也确认指向的是 `csiphy_res_8250`、`csiphy_num = ARRAY_SIZE(csiphy_res_8250)`。

**二、那套 ops 里 D-PHY 分支是完整的**（`camss-csiphy-3ph-1-0.c`）：

```c
switch (c->phy_cfg) {          // csiphy_get_lane_mask()
case V4L2_MBUS_CSI2_CPHY: ...
case V4L2_MBUS_CSI2_DPHY:
        lane_mask = CSIPHY_3PH_CMN_CSI_COMMON_CTRL5_CLK_ENABLE;   // 只有时钟使能位
```

后面的寄存器编程也是成对分支：`if (phy_cfg == DPHY) {...} else if (phy_cfg == CPHY) {...}` 出现 9 次；C-PHY 专属的逻辑（数据率相关设置）另外用 `if (phy_cfg == CPHY && data_rate_settings)` 兜住。C-PHY 的 lane 位是 `BIT(pos * 2 + 1)`——与我们算出的 `0x2a`（3 trio）一致。

结论：这两条前提成立，`v3c`（D-PHY 4 lane）走的是同一套 ops 里的 D-PHY 分支，不会被拒绝。

### 18.70 第二个核心假设：驱动请求的 2.9 V 到底能不能落到硬件上

`imx586_power_on()` 第一步是 `regulator_set_voltage(vana, 2900000, 2900000)`——**min 和 max 都是 2,900,000**。如果这条请求失败，`power_on` 直接返回错误，后面全都不用谈。而 pm8008 的 LDO 是 8 mV 步进，2.9 V 根本不是可表示的值。所以这条到底成不成立，必须查源码：

**驱动侧的电压表**（`drivers/regulator/qcom-pm8008-regulator.c`）：

```c
static const struct linear_range pldo_ranges[] = {
        REGULATOR_LINEAR_RANGE(1504000, 0, 237, 8000),   // 1.504 V 起，238 档，8 mV 步进 → 最高 3.400 V
};
```

**映射行为**：`regulator_map_voltage_linear_range()` 只看请求的 **min**，向上取整选档：

```
(2900000 - 1504000) / 8000 = 174.5  ->  取整到 175
1504000 + 175 * 8000        = 2904000   (即 2.904 V，比请求高 0.14%)
```

**约束检查**走的是 DT 里的 `regulator-min/max-microvolt`（现在是 1.5–3.4 V），`[2900000, 2900000]` 完全落在区间内 → 通过。

所以：**请求成功，rail 实际是 2.904 V**。这也正好解释了被撤回的 0006 为什么会失败——把 DT 钉成 `min == max == 2896000` 后，失败点不是映射（映射本来就会给 2.904 V），而是 `regulator_check_voltage()`：请求的 2,900,000 超过了约束上限 2,896,000，直接 `-EINVAL`。

**结论**：`pm8008_l7` 保持 1.5–3.4 V 区间是正确状态，2.9 V 由驱动自己请求、最终落到 2.904 V，偏差 0.14%（3.6 mV/1000），对 IMX586 的 AVDD 完全无害。

### 18.71 DTS 里有一段彻底过时的注释，改它的时候差点把补丁链弄断

后摄节点上方那段注释还写着**旧结论**：

```
 * CAM_MCLK0=tlmm94, CAM_RESET0=tlmm145, CAM_VANA=tlmm26).
 * The link is described as D-PHY with four lanes because that is the only
 * signalling mode the 7.2 CAMSS drivers can currently program (the CSID
 * driver never sets its C-PHY PHY_TYPE_SEL bit).
 * Lane order <1 2 3 4> mirrors the working front IMX471 endpoint ...
```

三处全错：引脚是 78/139/117；链路早就是 C-PHY；lane 是 `<0 1 2>`。节点本身是对的（`check-candidate-dtb.py` 逐项断言过），但**注释会骗人**——比 DTB 更容易被后来的人当依据。

改注释本身很安全：重编后 DTB 仍是 `c42f8555…`（逐字节相同），所以四张候选镜像**不用重打**。

### 但差点把补丁链弄断

第一次我的做法是"重新生成 0003"：`git diff BASE..HEAD`。结果 `verify-patches.sh` 立刻 `rc=1`：

```
applied: 0003 ...
FAILED:  0004-arm64-dts-kebab-rear-camera-pinctrl.patch
FAILED:  0005-arm64-dts-kebab-cam-vdig-comment.patch
```

原因：0003 是**累积式**补丁（`BASE..某个提交`），而 0004/0005 是**单提交**的 `format-patch`。用当前 HEAD 重生成 0003，就把 0004/0005 的内容一起吞进去了，后面自然冲突。

第二次我又猜错了界点（取 `82e780d10`），0004 仍然失败——因为 0004 的父提交是 `3e3bf2eef`，中间还差一个"控制引脚改成 78/139/117"的提交。

正确解：

- 0003 恢复成 `git diff BASE..3e3bf2eef -- dts`，**SHA 回到了原来的 `6c11847f…`**（这本身就是界点选对了的证据）；
- 我的注释改动单独导出成 **0007**（`git format-patch -1 f4e07f889`，SHA `361a53bb…`）；
- `verify-patches.sh` 的列表加上 0007，重跑：**五个补丁全部 applied、11 个文件全部 same、`rc=0`**。

顺带说明编号：0006 是被撤回的 VANA 钉死改动，那个编号**不再复用**，注释修正用 0007，README 里已写清。

### 18.72 重建的人需要的准确坐标

```
源码树   /home/zhuchen/oneplus8t-r4-camera-20261110/linux   (分支 cphy-v8)
HEAD     f4e07f889  arm64: dts: qcom: kebab: correct the rear camera comment block
构建输出 /home/zhuchen/oneplus8t-r4-camera-20261110/out

DTB      c42f8555764b230db40f43a42e54bfd2f0512455a3a335cdf8afa605c90d4227
         （与 boot-r4-camera-cphy3.img 内嵌的那份逐字节相同）
模块     qcom-camss.ko 17a444fe4b3c46e1…  839,969 B（= modules-stage 里那份，非 debug 版）
         imx586.ko     b51c07a2144902b2…   29,641 B
```

关键提交序列（从 r4 基线 `65305469b` 起）：

```
19ff8d364  imx586 先做成模块，板级 DTS 不动（隔离变量）
305e7c78d  D-PHY 后摄节点（第一版）
3961a6db6  csiphy0 侧 lane 改成 0 基
f39a97a00  &cci0 补 37.5 MHz
82e780d10  端点改成 C-PHY 三 trio
3e3bf2eef  控制引脚改用本机 DTBO 的 78/139/117
2b18f6c92  给这三根脚补 pinctrl 状态
96c786c59  修正 cam_vdig 注释
fe902e4ae  把 cam_vana 钉成 2.896 V  <- 已被下一提交撤回，不要再用
5e0791d89  Revert 上面那条
f4e07f889  注释修正（DTB 不变）
```

补丁集与这些提交的对应关系见 `patches/camera/README.md`；`verify-patches.sh` 会把 0002/0003/0004/0005/0007 依次应用到 `65305469b` 的干净 worktree 并 `cmp` 全部产物文件（当前 `rc=0`，11 个文件全 same）。

### 18.73 最后一个环节：推上去的那两个 `.ko` 里到底有没有我们改的代码

前面的链条一直是"源码 → 补丁 → 构建 → modules-stage → deploy-cphy"，但**没有直接看过 deploy 里的二进制本身**。用 `strings` / `llvm-nm` 查了一遍，证据很直接：

**`imx586.ko`（29,641 B）**

```
C-PHY
D-PHY
streaming 4000x3000 RAW10 over %s (%u %s)
trios
```

—— 就是驱动里那句按 `is_cphy` 选词的打印，两种词都在；说明这份模块确实是"按端点决定 C-PHY/D-PHY"的那版，不是上游原样。

**`qcom-camss.ko`（839,969 B）**

```
0000000000072168 R csiphy_ops_3ph_1_0
0000000000073bb0 r lane_regs_sm8250_3ph          <- 我们补的 SM8250 三 trio 表
0000000000000630 D datarate_sm8250_3ph_2p5Gsps    <- 三档数据率表
00000000000006c0 D datarate_sm8250_3ph_3p5Gsps
0000000000000780 D datarate_sm8250_3ph_4p5Gsps
0000000000073570 r lane_regs_sm8250             <- 上游的 D-PHY 表（没被我们动）
```

还有一条字符串 `drivers/media/platform/qcom/camss/camss-csiphy-3ph-1-0.c` —— 正是被 0002 改过的那个文件。

结论：**推给手机的二进制确实包含 SM8250 三相表和三档数据率表**，与源码/补丁一致，不是某次旧构建的残留。整条链的每一环现在都有独立证据。

### 18.74 推上去的两个脚本，每一份副本都逐字节比对

`camera-full-diag.py` 之前有 `cmp`，`camera-capture-test.py` 没有——而它有**三份**（工作目录、`deploy-cphy/`、仓库 `scripts/linux/`），任何一份不同都会让"测过的"和"推上去的"不是同一个东西。

| 文件 | 位置 | SHA-256（前 16） |
| --- | --- | --- |
| `camera-capture-test.py` | 工作目录 / `deploy-cphy/` / `scripts/linux/` | **`4dba8bd0f29be070`** ×3 |
| `camera-full-diag.py` | 工作目录 / `deploy-cphy/` | **`8a693e4dc1ca4f62`** ×2 |

三份/两份全部逐字节相同。并且把这个比对**加进了 `run-all-diag-tests.sh`**（`cmp ... || exit 1` 三行），以后每次自检都会验，不靠人记。

### 18.75 0001 为什么不在 `verify-patches.sh` 里——以及驱动源码靠什么保证可复现

注意到 `verify-patches.sh` 只应用 0002/0003/0004/0005/0007，**没有 0001**。查清原因（顺手验证它确实不该在里面）：

```
git apply --check 0001  →  drivers/media/i2c/imx586.c: already exists in working directory
```

因为 0001 是**历史补丁**：它是"r4 基线 → `65305469b`（r4 + IMX586 驱动）"那一段，而 `65305469b` 本身就包含这些改动。所以它应用失败是**正常现象**，不是坏补丁——把它塞进 `verify-patches.sh` 只会制造假失败。

那驱动源码的可复现性怎么保证？用另一条判据：

```
git log 65305469b..HEAD -- drivers/media/i2c/imx586.c   ->  (空)
工作树 sha   7ba35a09d5b60342ef4feb84
基线提交 sha 7ba35a09d5b60342ef4feb84   (git show 65305469b:... | sha256sum)
```

即**基线之后没有任何提交动过它**，工作树里的驱动就是基线提交里的那一份。再加上第 18.73 节对 `imx586.ko` 二进制的字符串检查（`C-PHY`/`D-PHY`/`trios`），驱动这条链闭环：基线 → 源码 → 构建 → 推上去的模块。

### 18.76 把"早上那条命令"真的跑一遍（在没有任何结果的情况下）

STATUS 让早上的人执行 `python3 triage-camera-results.py diag-*.txt`。但**如果一个文件都没有**（手机整夜没出现，正是现在的情形），bash 会把 `diag-*.txt` 原样传给 python。原来的输出是一行 `* unreadable  note: [Errno 2] No such file or directory`——不崩，但对还没睡醒的人等于没说。

改成：脚本自己展开通配符；展开后为空就打印

```
no diag files found
  -> no pass ever ran, so the phone was not on USB; check autowatch.log
```

三种情况都验了：

| 情况 | 行为 |
| --- | --- |
| 通配符没匹配 | 友好提示 + 指向 `autowatch.log`（不再打印 `unreadable`） |
| 通配符有匹配 | 正常展开成文件列表 |
| 普通路径参数 | 原样保留 |

测试从 8 项加到 **11 项，全过**。

### 18.77 打开 CAMSS 的动态调试：否则链路失败在 dmesg 里是一片安静

查 camss 的报错字符串时发现一个问题：**流水线里的诊断几乎全是 `dev_dbg()`**，默认不打印。真正的 `dev_err` 只有寥寥几条（`Failed to link %s->%s entities`、`vfe power off on power_count == 0`）。也就是说，如果 CSIPHY/CSID/VFE 哪一步没锁上，dmesg 里**什么都不会有**——只有"没帧"这个结果。

而这份内核的配置支持动态调试：

```
CONFIG_DYNAMIC_DEBUG=y
CONFIG_DYNAMIC_DEBUG_CORE=y
CONFIG_DEBUG_FS=y
CONFIG_DEBUG_FS_ALLOW_ALL=y
```

所以诊断脚本在步骤 B 里加了三个动作：

```
insmod qcom-camss.ko → 打开 dynamic_debug（camss/* 与 imx586.c +p）→ 抓帧
   → dmesg | tail -120（b_dmesg_dbg，未过滤）→ 关掉（-p）
```

用 shell 片段写成，带三层降级：

1. 控制文件不存在就先 `mount -t debugfs`；
2. 还是不可写就打印 `no dynamic_debug control`；
3. 无论哪种情况都 `exit 0`——**开不了动态调试只是少了诊断信息，不能让整轮诊断失败**。

在本机 WSL（没有可写 debugfs）实测走的是第 2 条分支，输出 `no dynamic_debug control`、`exit=0` ✓。

### 18.78 用 `CSIPHY 3PH HW Version` 当"三相驱动真绑上了"的证据钉

动态调试打开之后，`camss-csiphy-3ph-1-0.c` 的 probe 会打印 `CSIPHY 3PH HW Version = 0x%08x`。这行**只可能来自三相信道驱动**（两相那份打印的是 `CSIPHY HW Version`），所以它是一颗干净的身份钉：

- 出现 → 我们的 `csiphy_ops_3ph_1_0`（带 SM8250 三相 lane 表）真的绑在 csiphy0 上；
- 不出现 → 要么动态调试没开成，要么绑的是别的 ops。

已写进 JSON 作 `b_3ph_active`（`yes`/`no`），并在 STATUS 里说明它的含义。这比"insmod 没报错"更进一步——`insmod` 成功只说明模块加载了，这一行说明**具体的 ops 表生效了**。

### 18.79 更正：接路由器那条路救不回来自动流程

上一节我在 STATUS 里写过"USB 口直连，或经那台路由器再进电脑——两条路 watcher 都认"。**这句是错的**，自己复查时发现的：

- `kebab-router` 那份 SSH 配置确实存在（`BindAddress 0.0.0.0`，针对路由器 NAT 后面），SSH 能通；
- 但整轮流程的第一步是 **`fastboot boot`**，fastboot 只能走**本机 USB**；
- 手机接在路由器上时 `fastboot devices` 是空的 → watcher 认为手机不在 → 后续什么都不会发生。

所以正确说法是：**必须直连这台电脑的 USB**。已经改掉 STATUS 里那句话，并写明"路由器那条路即使 SSH 通了也救不回来"，免得早上照着一个救不了场的方案折腾。

这也是"检查一条结论"的价值所在：不是发现自己错了，而是**发现自己写的操作指引会把人带错方向**。

### 18.80 watcher 的"pass"到底是怎么算的（对着代码核过）

STATUS 说"最多两轮、每轮跑完 5 张候选、失败每隔 10 分钟再试"，这轮把 `camera-autowatch.ps1` 逐行读了一遍，确认语义如下（`-Hours 72 -MaxPasses 2 -RetryMinutes 10`）：

| 阶段 | 行为 |
| --- | --- |
| 等手机 | 每 45 秒轮询：`fastboot devices` 非空 → 触发；`ssh kebab true` 返回 0 → 触发。任一命中就进入下一步 |
| 跑一轮 | 调 `auto-camera-run.ps1`，整轮输出存 `autowatch-run-HHMMSS-pN.txt`，日志里留最后 600 字符 |
| 判成功 | 输出里有 `produced a frame`，或 `camera-test.log` 最后 25 行里有 → 停 |
| 判槽位坏 | 输出里有 `not marked successful` / `flagged unbootable` / `refusing to reboot into it` → **停，不重试** |
| 都不是 | 等 10 分钟，进下一轮（最多 2 轮） |
| 到点 | 72 小时窗口用完 → 记 `autowatch timeout` 退出 |

两个细节值得写下来：

1. **`fastboot` 优先于 SSH**：手机停在 fastboot 时也会被接手，不需要先能 SSH。
2. **槽位坏是硬停**：这是唯一一个"明明有重试额度却主动不重试"的分支——因为每重启进坏槽位会消耗一次 `slot-retry-count`，烧光了连 fastboot 都进不去（这与 STATUS 里"故意留在 fastboot"是一致的设计）。

所以最可能的早上情形（手机一直没上电）最终日志会是：`pass 1/2 - waiting for the phone` → …… → `autowatch timeout`。看到这个就说明"整夜没来电"，不是哪里坏了。

### 18.81 槽位健康检查会误杀：`successful=yes` 但 `retry-count=0` 时整夜什么都不做

`SlotHealthy()` 原本有三条拒绝条件：`slot-successful:a` 是 `no`、`slot-unbootable:a` 是 `yes`、**`slot-retry-count:a` < 3**。前两条对，第三条会误杀。

造了个新桩 `sim-retry0/`（`successful=yes`、`retry-count=0`、`unbootable=no`）跑了一遍，**旧代码立刻停手**：

```
slot: slot-successful:a: yes  slot-retry-count:a: 0  slot-unbootable:a: no
boot_a retry count 0 < 3 - refusing to reboot into it
cannot reach fastboot; aborting run
leaving the phone in fastboot on purpose: the boot slot is unhealthy ...
summary: r4ctl:no-fastboot
```

而 **`slot-successful=yes` 而 `retry-count` 是 0 完全正常**：ABL 对已标记成功的槽位不会消耗重试次数，刚标记成功的槽位经常就报 0。也就是说这份"安全"检查会让整夜在第一张候选之前就结束——比不做检查更糟。

**修法**：`successful=yes` 是权威信号，直接放行；`retry-count` 只在槽位状态**未知**（既不是 yes 也不是 no）时才作为补充判据。

三个桩重跑：

| 桩 | 槽位 | 结果 |
| --- | --- | --- |
| `sim-retry0`（新） | yes / 0 / no | **放行**，`fastboot boot` 执行、控制镜像到达 SSH ✓ |
| `sim-unhealthy` | no / 1 / no | 仍然拒绝：`boot_a is not marked successful - refusing` ✓ |
| `sim` | yes / 6 / no | 照常放行 ✓ |

也就是说：该拦的还拦得住，不该拦的现在不会拦。真正的 `camera-test.log` 跑完已还原成 163 字节原样。

### 18.82 用真实脚本把六条失败路径全跑一遍（不只是"设计上应该这样"）

这一轮把 `auto-camera-run.ps1` 原样跑在离线桩上，记录**真实输出的 summary 行**作为证据：

| 桩 | 场景 | 实际 summary | 判读 |
| --- | --- | --- | --- |
| `sim` | 一切正常（控制镜像） | `r4ctl:ssh-up  v2:no-fastboot` | 控制镜像到达 SSH ✓（v2 因 NoPon 不复位，符合预期） |
| `sim-retry0` | 槽位成功但 retry=0 | `r4ctl:ssh-up  v2:no-fastboot` | **修复后放行** ✓（修复前是 `r4ctl:no-fastboot`） |
| `sim-unhealthy` | `successful=no` | `r4ctl:no-fastboot` + `boot_a is not marked successful` | 硬停、不烧 retry ✓ |
| `sim-bootfail` | `fastboot boot` 报 Load Error | `r4ctl:boot-failed` | 立刻停、不等 SSH，并 `fastboot reboot` 退出 fastboot ✓ |
| `sim-pushfail` | scp 全部 rc=1 | `v2:push-failed cphy3:push-failed v3c:push-failed cphy2:push-failed` | **不会拿旧文件跑诊断**，逐个候选拒绝 ✓ |
| `sim-wrongkernel` | 启动后 `/proc/config.gz` 无 IMX586 | `v2:wrong-kernel … cphy2:wrong-kernel` | 逐张认定为"不是我们的内核"并跳过 ✓ |

这一点比"代码审查通过"强：这些 summary 行是脚本**真的跑出来的**，而且每一行都对应一个明确的失败语义。

顺带纠正我自己的一次误测：前两条桩最初用 `-NoPon` 跑，`ToFastboot()` 在"已能 SSH + NoPon"时会直接返回 false，于是看起来像"到不了 fastboot"。这不是脚本的问题——`sim-pushfail`/`sim-wrongkernel` 里预先放着 `booted.flag`（模拟手机已经起着），必须按设计**不带** `-NoPon` 跑，让脚本自己走 PON→fastboot→boot 这条链。

### 18.83 前摄的节点名是写死的——而 camss 重载后节点号会变

`camera-capture-test.py` 的**后摄**是从 media graph 里顺着 `imx586 → msiphy → csid → vfe → /dev/videoN` 走出来的，但**前摄是写死的**：

```python
sensor = "imx471 24-0010"; csiphy = "msm_csiphy4"; csid = "msm_csid0"
vfe = "msm_vfe0_rdi0"; video = "/dev/video0"
```

这在 11/10 验证前摄时是对的（那时真抓到过帧）。但**今晚的诊断流程恰恰要先 `rmmod`/`insmod` 一次 camss**——节点号按 probe 顺序分配，重载后 `/dev/video0` 未必还是前摄那个。于是步骤 B 的前摄抓帧可能莫名其妙失败，而我这一夜刚把"前摄当对照"写进判读表（见 18.x）：**对照本身失真，比没有对照更糟**。

**改法**：把 `discover_rear()` 泛化成 `discover_pipeline(media, sensor_prefix)`，前摄也按名字 `imx471` 走图找到真实节点；**只在名字与节点号上做发现，格式（SRGGB10_1X10 / 1928×1088 / pRAA）保持 11/10 验证过的组合不动**，以免把一个已知能用的路径改坏。发现失败就退回原来那套写死值（并记 `front_discovered: "fallback: ..."`），不会让整轮失败。

测试从 4 项加到 6 项，全过：

| 新增测试 | 证明什么 |
| --- | --- |
| `test_front_pipeline_discovery` | 图里前摄在 `/dev/video3` 时，发现逻辑拿到的是 **video3** 而不是写死的 video0 |
| `test_front_discovery_falls_back_to_defaults` | 图里没有 imx471 时抛明确错误（`main` 据此退回默认值），不会静默用错节点 |

三份副本（工作目录 / `deploy-cphy/` / 仓库 `scripts/linux/`）已同步为同一份。

### 18.84 把 `3ph` 放进早间那张表

`b_3ph_active` 之前只在 JSON 里，早间那张表看不到。现在它是表格的独立一列，配上真实样例：

```
tag    verdict                acks  driver                capture  3ph  rails
cphy3  driver-bound-no-frame  0x1a  /sys/x/driver/imx586  front    yes  l7=enabled/2904000
```

这样一行就同时回答了四个问题：**传感器在不在总线上**（acks）、**驱动绑没绑**（driver）、**camss 整体坏没坏**（capture，前摄是内置对照）、**我们的三相 ops 生效没**（3ph）。

测试 11 → **13 项**（新增 `3ph` 缺省为 `-`、有值取原值两条），全过。

### 18.85 索引里的两处过期说法（24 小时 / §18.62）

`docs/README.md` 的"当前入口"是早上第一个点开的地方，里面两处已经不准：

| 原文 | 实际 |
| --- | --- |
| "watcher 24 小时窗口" | 起的时候用的是 `-Hours 72`，窗口到 11/14 04:02 |
| "§18.1–18.62" | 本文件已经到 **§18.84 / 18.85** |

两处都已改，并在描述里点出这一夜新增的东西（补丁重放自检、dmesg 病因表、三相 ops 证据钉、槽位误杀与前摄节点写死这两个假通过）。索引 131 条相对链接全部可解析，0 断链。

### 18.86 三相链路的最后两环：CSID 的 PHY_TYPE_SEL 位、以及链路频率够不够

CSIPHY 那一半前面已经验过（`csiphy_res_8250` 六路全是 3ph ops、lane mask 0x2a、二进制里有 `lane_regs_sm8250_3ph`）。剩下两环这轮查了：

**一、CSID 的 C-PHY 位**

```c
#define CSID_CSI2_RX_CFG0          0x100
#define   CSI2_RX_CFG0_PHY_TYPE_SEL   24   /* gen2 与 gen3 都有，位置相同 */
```

关键在于：**这个位不是我们发明的**——`camss-csid-gen2.c` 上游本身就定义了它（只是从来没写过）。我们的补丁做的只是把端点总线类型换算成 0/1 写进去：

```c
phy_sel = (bus_type == V4L2_MBUS_CSI2_CPHY) ? CSID_PHY_SEL_CPHY : CSID_PHY_SEL_DPHY;   /* 1 : 0 */
val |= phy_sel << CSI2_RX_CFG0_PHY_TYPE_SEL;
```

SM8250 用的是 `csid_ops_gen2`（`camss.c` 里逐条确认），所以真正生效的就是 gen2 那一条。**寄存器位来自上游定义 ⇒ 位置这一层没有猜测成分。**

**二、链路频率够不够**

```c
#define CAMSS_COMMON_PHY_DIVIDENT 16
#define CAMSS_CPHY_DIVISOR         7     /* C-PHY 每符号 7 bit */
#define CAMSS_DPHY_DIVISOR        16
div = lanes * 2 * (cphy ? 7 : 16);
link = v4l2_get_link_freq(pad, 16 * bpp, div);
```

我们的数值（`IMX586_LINK_FREQ = 698395833`，3 trio，RAW10）：

```
div = 3 * 2 * 7 = 42
698,395,833 * 160 / 42 ≈ 2.666 Gbit/s  （CSID 侧用的链路值）
```

对照前摄（`200,000,000`，4 lane，D-PHY）：`200e6 * 160 / 128 = 250 Mbit/s`。两者量级关系与像素率关系一致（后摄 12 Mpixel × 30 fps 对前摄 2.1 Mpixel × 30 fps，约 5.7 倍，链路值约 10.7 倍——因为后摄是 3 组差分对跑 C-PHY，每对比 D-PHY 少 9/16 的编码效率）。

**这条链路会不会"饿死"**：IMX586 4000×3000 RAW10@30fps 需要约 3.6 Gbit/s 总带宽；3 trio × 698.4 MSps × 2.285 bit/symbol ≈ **4.79 Gbit/s**，余量约 33%。所以即使驱动按 30 fps 声明，供数也足够；反过来 698.4 MSps 对应的实际上限约 40 fps。

**离线能查到这儿为止**：位定义、公式、常数、带宽都自洽且有出处。剩下唯一无法离线判定的是"硅片是否接受这套相位配置"，那只能靠真机。

### 18.87 这一夜能跑下去的物理前提：电脑不会睡

所有自动化都依赖一个前提——**主机整夜不睡**。这一轮查了电源方案（`powercfg /q SCHEME_CURRENT`）：

| 设置 | 交流（插电） | 含义 |
| --- | --- | --- |
| `STANDBYIDLE`（睡眠） | `0x00000000` | **从不睡眠** |
| `VIDEOIDLE`（关屏） | `0x00000000` | 从不关屏 |

所以不会出现"watcher 被休眠掐掉"这种情况。仍然存在的一个真实缺口是**主机重启**（系统更新等）——重启后 watcher 就没了，手机再出现也不会自动跑。已经把这一点写进 STATUS，并配了重新起 watcher 的完整命令（含 `-Hours 72` 与 `Start-Process` 脱离会话）。

顺带说明为什么这条值得单独查：前面所有"六条失败路径都跑过"的结论，前提是进程还活着。**如果一个全局前提没被检查，"局部都验过"是没意义的。**

### 18.88 后台进程能不能找到 `fastboot` / `ssh` / `scp`（在隐藏会话里实测）

watcher 是以 `Start-Process -WindowStyle Hidden` 起的**独立会话**，它的 PATH 未必和我这个交互 shell 一样。所以用一个同样隐藏的会话跑了一遍：

```
fastboot -> E:\adroid\platform-tools\fastboot.exe
ssh      -> C:\Windows\System32\OpenSSH\ssh.exe
scp      -> C:\Windows\System32\OpenSSH\scp.exe
```

三个都在 ✓。磁盘余量 C 19 GB / E 220 GB / F 37 GB ✓（一份诊断 JSON 约 50 KB，整夜最多几十份）。

一个看起来可疑但**实际不影响**的项：Windows 侧的 `python` 解析到 `WindowsApps\python.exe`（商店占位符，运行会弹安装）。整条流程里所有 Python 都跑在 WSL 里（`python3 triage-camera-results.py`、`run-candidate-check.sh` 内部），Windows 侧从不调用 python ✓。

### 18.89 改了槽位判据之后，watcher 的"硬停"还认得出来吗

18.81 改掉了 `retry-count<3` 那条误杀，但 watcher 是靠**匹配 runner 的日志文本**决定"槽位坏了就别再试"的。改文案有可能把这条安全链改断，所以对了一遍：

```
runner 打出来的（仅两种，都是真坏槽位）:
  boot_a is not marked successful - refusing to reboot into it
  boot_a is flagged unbootable - refusing to reboot into it

watcher 认的（硬停）:
  not marked successful        -> OK 命中第一条
  flagged unbootable           -> OK 命中第二条
  refusing to reboot into it    -> OK 两条都命中
```

三条全部仍能命中；而且新增的"槽位状态未知"分支用的也是同一句 `refusing to reboot into it`，所以那种情况同样会硬停 ✓。**改判据没有把安全链改断。**

### 18.90 早间表里"前摄对照"看的是错的那一次

判读表里写"前摄出图 ⇒ camss 没被换坏"，可**表里显示的 `front` 取的是 `a_front`**——那是**换模块之前**（stock camss）抓的。真正要回答"我们的模块有没有把 camss 弄坏"的是 **`b_front`**（换完之后抓的）。

也就是说：即使我们的 camss 把前摄也弄坏了，表里仍然会显示 `front`，读者会得出"camss 没问题"的**相反结论**。这是这一夜最好笑也最危险的一个 bug：判据写对了，取数取错了。

**改法**：两路都显示，标签区分 `front-a` / `front-b`；顺带 `rear-a`/`rear-b` 语义一致（换模块前/后）。

测试 13 → **16 项**（`b_front` 单独显示、两者并列、`b_front` 失败时只剩 `front-a`），全过。

### 18.91 产物清单：数字本身错了，而且一个缺失会被数成三个

故意把 `deploy-cphy/imx586.ko` 改名藏起来，跑一遍自检，发现两个问题：

**一、清单条目数一直是错的**

`check-all.ps1` 里这个 Stage 的标签是**写死的字符串** `"manifest (44 entries)"`，而实际清单有 **53** 条。标签不会自己更新，看日志的人会以为"只有 44 个产物"——这正是"数字写死在文案里"的典型腐坏。改成不带数字的 `"manifest"`，真实条数由 `verify-sums.sh` 输出。

**二、缺一个文件被数成 bad=3**

`sha256sum -c` 除了每个文件的 `FAILED` 行，还会打两行 `WARNING:` 汇总；原来的统计是"所有不以 `: OK` 结尾的行"，于是 1 个缺失 + 2 行警告 = `bad=3`。改成只数 `: FAILED`：

| 场景 | 修改前 | 修改后 |
| --- | --- | --- |
| 全部正常 | `ok=51 bad=3`（本来就错） | `entries=53 ok=53 bad=0` |
| 藏掉一个文件 | `ok=51 bad=3` | `entries=53 ok=52 bad=1` ← 准确 |

两个问题都不会让检查"漏报"（都还是 FAIL），但会让**数字不可信**。报告里的数字一旦不可信，人就学会忽略它——这比漏报更慢性。

### 18.92 五个 Stage 都试过"让它失败"——自检不是装饰

`check-all.ps1` 每次都说 `ALL CHECKS PASS`，那它到底会不会 FAIL？逐个人为制造失败验证了一遍：

| Stage | 怎么让它失败 | 结果 |
| --- | --- | --- |
| preflight | 藏掉 `deploy-cphy/imx586.ko` | `[FAIL] preflight`，`PREFLIGHT FAILURES: 1` |
| 诊断单元 + 流程测试 | 给 `deploy-cphy/camera-full-diag.py` 追加一行（与工作副本不再一致） | `[FAIL]`，`cmp` 拒绝通过 |
| 汇总工具测试 | 把 `triage-camera-results.py` 改名 | `[FAIL]`，`FileNotFoundError` 直接抛出 |
| 产物清单 | 藏掉同一个模块 | `[FAIL] manifest`，`entries=53 ok=52 bad=1` |
| 补丁可重放（`-Slow`） | 用当前 HEAD 重生成 0003（把 0004/0005 吞进去） | `[FAIL]`，`rc=1`、两个补丁 `FAILED` |

每一次失败后都恢复原状并重跑，确认回到 `ALL CHECKS PASS`。

这一步的意义：**一个从不失败的检查等于没有检查**。这一夜已经在三个地方发现过"看起来在检查、实际什么都没检查"的假通过（条件没执行、异常被跳过、计数器不累加），所以对自检本身也要做同样的怀疑。

### 18.93 DTB 内容检查只覆盖了两张，而且第三张的描述是错的

`check-candidate-dtb.py` 原本只查 `cphy3` 与 `v3c` 两张。**`v2` 与 `cphy2` 从来没被内容检查过**——`v2` 是"安全基线"、`cphy2` 是"对照组"，恰恰是两张最需要确认"它真的和声称的一样"的镜像。

扩到四张后立刻抓到一个错：

```
boot-r4-camera-cphy2.img (phy=cphy, vana_pinned=False, pinctrl=one)
  FAIL no pinctrl-0 (control candidate)
```

它被描述成"**无** pinctrl"，实际反编译出来是：

```dts
pinctrl-names = "default";
pinctrl-0 = <0xdf>;      /* 只有一个 phandle */
```

也就是说它**有** pinctrl，只是只有 **MCLK 一个状态**——它对应的是"给后摄控制脚补 pinctrl"那次提交之前的形态，不是"没有 pinctrl"。

这个差别会影响判读：如果 `cphy2` 出图而 `cphy3` 不出，原因不是"pinctrl 开或关"，而是"**四个引脚的状态（reset/custom1/vdig 的电平与驱动能力）被一起配上了**"。方向从"要不要 pinctrl"变成"多配的那三个状态是不是有问题"——后者更尖锐，也更可能真。

修法：四张各自断言，措辞改准：

| 镜像 | 断言 |
| --- | --- |
| `v2` | **没有** `camera@1a` 节点、没有 `vana-enable-gpios`（这才是"r4 DTB 原样"） |
| `cphy3` | C-PHY 三 trio + `pinctrl-0` 有 4 个状态 |
| `v3c` | D-PHY 4 lane + `clock-lanes = <7>` + 4 个状态 |
| `cphy2` | C-PHY 三 trio + `pinctrl-0` 只有 **1** 个状态 |

`RESULT: 4/4 candidates ok`。

### 18.94 四张候选"只差 DTB"这件事，现在由检查器自己证明

STATUS 的"关键事实"里一直写着"四张候选的内核完全相同，彼此只差 DTB"——那是当初手工核的。现在写进检查器，每次自检都会重算：

```
image parts:
  boot-r4-camera-v2.img      kernel=f3391b293976 ramdisk=2c54c5270ba4 second=e3b0c44298fc dtb=216c2fa1f677
  boot-r4-camera-cphy3.img   kernel=f3391b293976 ramdisk=2c54c5270ba4 second=e3b0c44298fc dtb=c42f8555764b
  boot-r4-camera-v3c.img     kernel=f3391b293976 ramdisk=2c54c5270ba4 second=e3b0c44298fc dtb=bf097cc81018
  boot-r4-camera-cphy2.img   kernel=f3391b293976 ramdisk=2c54c5270ba4 second=e3b0c44298fc dtb=2cb29975cfe4
  ok   camera candidates share kernel / ramdisk / second / cmdline
```

三件事一眼可见：

1. **内核 sha 四张完全一致**（`f3391b293976`）——所以任何一张起不来都不是内核不同引起的；
2. **ramdisk 也一致**（`2c54c5270ba4`）——排除"某张镜像塞了不同 initramfs"这种混淆；
3. `second` 与 `dtbo` 都是 `e3b0c44298fc`，那是**空字符串的 SHA-256**，即这两段长度都是 0，与"dtb 不按页对齐"的布局一致。

命令行的比对也加上了，四张相同。

### 18.95 PowerShell 5.1 不认没有 BOM 的 UTF-8：新写的"早间一条命令"整段乱码

写了 `status-camera.ps1`，跑出来是这样：

```
==================== 涓€鍙ヨ瘽 ====================
  鏁村亱娌¤窇  -> 鎵嬫満娌′笂鐢?
```

原因不是终端，是**解析阶段就已经坏了**：Windows PowerShell 5.1 打开 `.ps1` 时，**没有 UTF-8 BOM 就按 ANSI（本机 GBK）解码**。所以文件里的中文在**读进内存时**就变成了乱码字符串，后面无论输出编码怎么设都救不回来。

修法是加 BOM（`EF BB BF`）。加上之后立刻正常：

```
==================== 一句话 ====================
==================== 结果表 ====================
  整夜没跑  -> 手机没上电；插上这台电脑的 USB 口即可，watcher 45 秒内接手
```

顺着把四个脚本都查了一遍：只有 `auto-camera-run.ps1` 里还有 7 个非 ASCII 字符，而且它们**只在一个注释里**（引用厂商的"系统文件被损坏"），不影响运行——但注释乱码同样会误导后来人，已改成 ASCII 表述。其余三个脚本全 ASCII，没有问题。

**这条经验对中文用户特别容易踩**：文件在编辑器里看着完全正常，只有 PowerShell 跑起来才乱码，而"乱码"又很容易被当成终端字体问题而忽略。

### 18.96 判读表**只在成功那一种情况下崩溃**

给新的"早间一条命令"造了一份**成功**的假数据（`b_3ph_active: true`、后摄出帧），结果表直接抛异常：

```
Traceback (most recent call last):
  File "triage-camera-results.py", line 121, in main
    widths = {k: max([len(k)] + [len(r[k]) for r in rows])
TypeError: object of type 'bool' has no len()
```

NaN

**为什么它能活到现在**：

- `False` 会走 `or "-"` 变成字符串，所以失败情况正常；
- `None`（字段缺失）也是字符串；
- 只有 `true` —— 也就是**三相 ops 真的绑上、最想看到的那个结果** —— 会崩。

换句话说：这一夜的判读表在"全失败"的世界里测了 16 次全过，唯独**成功会让它崩**。要是早上真跑出 C-PHY 结果，人看到的会是一个 traceback。

修法：所有进表格的值都过一层 `cell()`（`None`/空 → `-`，其余 `str()`），三态字段单独用 `tri_state()` 映射 `true→yes / false→no`。

测试 16 → **19 项**（`true→yes`、`false→no`、以及一个直接调用 `main()` 断言"布尔不再崩且输出含 yes"的端到端用例）。修复后同样的假数据：

```
tag    verdict     acks  driver                                     capture                 3ph  rails
cphy3  rear-frame  0x1a  /sys/bus/i2c/devices/0-001a/driver/imx586  rear-b,front-a,front-b  yes  l7=enabled/2904000
```

**教训**：测试数据必须覆盖"成功"分支。这一夜之前的假数据清一色是失败态（无 ACK、无驱动、无帧），因为它们描述的是当时的现实——而缺陷恰好住在没人去过的成功路径上。

### 18.97 **最严重的一个缺陷：出帧会被判成没出帧**

为了补上"成功路径从来没被测试过"这个洞，做了一套新的离线桩 `sim-frame`，让诊断输出 `REAR_RESULT a=0 b=1`（也就是 C-PHY 真跑通时会看到的那一行），然后原样跑 `auto-camera-run.ps1`：

```
candidate custom gave no rear frame (a=0 b=1/rear-frame); moving on
```

**它没认出帧。** 原因在判据的写法：

```powershell
if ($text -match "REAR_RESULT a=1" -or $text -match "REAR_RESULT b=1") { ... }
```

诊断把两个计数打在**同一行**（`REAR_RESULT a=0 b=1`），所以字符串 `"REAR_RESULT b=1"` 永远不可能匹配——它前面还隔着 `a=0 `。只有当 `b=1` 是第一个字段（即输出恰好是 `REAR_RESULT b=1`）时才会命中，而诊断从不那么打。

**后果**：A 步用原厂 camss（不懂 C-PHY）必然拿不到帧，B 步用我们的模块才可能出帧——所以 `a=0 b=1` 正是**这条线路成功时唯一可能的形状**。修复前，这个结果会被判成失败，脚本继续换下一张候选，最后整轮报"没有出帧"。

换句话说：**这一夜所有的准备，都会在真正成功的那一刻把它当成失败。** 而且它不会报错、不会崩，只会安静地走开。

**修法**：解析两个计数，再判：

```powershell
if ($text -match "REAR_RESULT a=(\d) b=(\d)") { $a = $Matches[1]; $b = $Matches[2] }
if ($a -eq "1" -or $b -eq "1") { Log "rear camera produced a frame with candidate ..."; break }
```

同一份桩数据，修复后：

```
2026-11-11 05:12:09 rear camera produced a frame with candidate custom (a=0 b=1/rear-frame)
2026-11-11 05:12:09 summary: custom:a=0 b=1/rear-frame
```

顺便确认 watcher 那侧没有同类问题：它认的是 `produced a frame`（由 runner 打），以及三个"槽位坏了"的短语，都不涉及 `REAR_RESULT` 的字段解析 ✓。

**为什么会漏到今天**：前面七套桩全部在模拟失败（无 ACK、无驱动、无帧、推送失败、启动失败、超时、槽位异常）——因为那正是当时的现实。而**成功路径只在"真的成功"时才会被执行**。这与 18.96 是同一类错误，只是后果大得多：一个是判读表崩，一个是把成功当成失败。

### 18.98 watcher 那一侧的"出帧即停"也实跑了一遍

18.97 修好的是 runner 认不认帧。watcher 还有自己的判断：跑完之后看 `produced a frame`，命中就 `break`，**不再跑第二轮**。这条路径此前也没被执行过。用 `sim-frame` + `-MaxPasses 2` 实跑：

```
summary: r4ctl:ssh-up  v2:a=0 b=1/rear-frame
frame produced - stopping
autowatch exit
```

三件事同时成立：

1. 控制镜像走完 → v2 出帧 → runner 立刻停（`a=0 b=1` 被认出来了）；
2. watcher 认到 `produced a frame`，写 `frame produced - stopping`；
3. **没有进入第二轮** —— 虽然 `-MaxPasses 2`。

跑完已把 `camera-test.log`（163 B）与 `autowatch.log`（12 行）原样还原，并删掉本次产生的 `autowatch-run-*.txt` 与 `diag-*.txt`。

至此"成功"这条路上的**三个环节**都验过了：runner 认帧（18.97）、runner 停并打 summary、watcher 停并退出循环。

### 18.99 内核起得来但 SSH 不上来（手机挂住）时会不会卡死

这是最接近"事故"的一种失败：`fastboot boot` 报成功，内核却在中途挂了，SSH 永远不来。如果没有超时，脚本会在这里等一整夜。

代码里本来就有这层保护（`WaitSsh 45` = 45×10 秒 = 7.5 分钟），但**从来没被执行过**。为了让这条分支可测，把等待次数提成参数（默认仍 45，行为不变）：

```powershell
param([switch]$NoPon, [string]$Image = "", [int]$SshTries = 45)
if (-not (WaitSsh $SshTries)) { ... }
```

配一套新桩 `sim-nossh`：`fastboot boot` 成功，然后手机既不在 fastboot、也不回应 ssh（真实挂住的形状）。跑 `-SshTries 2`：

```
candidate custom never reached ssh - phone likely hung; needs a physical power cycle
summary: custom:no-ssh
=== run end ===
```

三个判据都对：**7.5 分钟封顶**（测试用 2 次=20 秒）、**不再试下一张**（手机已经挂了，换镜像没有意义）、**明确写出"需要人工断电重启"**——而不是含糊地继续。

之后 watcher 会照常进第二轮等手机回来：如果人断电重启后它重新出现在 fastboot 或 ssh 上，第二轮会接着跑；如果一直不出现，就等到窗口结束。这是刻意的——挂住的手机只有人能救，但救回来之后不该还要人再点一次。

### 18.100 两份报告和补丁集都不在 git 里

查了一下工作树：

| 路径 | 状态 |
| --- | --- |
| `docs/README.md` | 已改（`M`） |
| `docs/r4-camera-20261110.md` | **未跟踪**（`??`） |
| `docs/r4-camera-boot-failure-20261111.md` | **未跟踪**（`??`，就是这份） |
| `patches/camera/` | **未跟踪**（`??`，7 个补丁 + README） |
| `local/camera-r4-20261110/` | 被 `.gitignore:5 /local/` 排除（按设计） |

也就是说：这一夜的主要交付物**只存在于磁盘上**。镜像和私有证据放 `local/` 是对的，但报告与补丁本该入库。

**没有替你提交**：分支上还有别的未提交改动，提交范围该由你划；一个"顺手"的 `git add -A` 会把不相干的东西一起卷进去。做法是把命令写进 STATUS，你醒了看一眼就能决定。

### 18.101 想替你提交，结果这台机器连 git 身份都没有

18.100 发现两份报告和补丁集都不在 git 里。于是试着**只提交这四个路径**（`git commit` 带路径，不动索引里别的东西）：

```
Author identity unknown
fatal: unable to auto-detect email address (got '15185@…(none)')
```

这台机器上 `user.name` / `user.email` 都没配。三种做法里我选了最保守的：

| 做法 | 为什么不做 |
| --- | --- |
| 编一个作者名提交 | 历史里会出现一个不存在的作者，且以后要改得 rebase |
| 用 `--author` 猜你的信息 | 猜错比不提交更麻烦 |
| **重置暂存区，把命令写给你** | 工作树回到我来之前的样子，`HEAD` 不动，决定权在你 |

已经 `git reset`：暂存区空、`HEAD` 仍是 `2a3fb5d`、四个路径仍是"1 改 + 3 未跟踪"。

顺带记一条环境事实：**这个仓库里 `git commit` 现在直接就会失败**，任何后续自动化（包括我下一轮）都不能假定"add 完就能 commit"。

### 18.102 夜里的修复对已经在跑的 watcher 生效吗

这是必须确认的一点：修复是在 watcher 已经跑起来之后做的，如果它持有旧代码，早上那一轮还是错的。

查下来是安全的，因为 watcher **每一轮都重新从磁盘读 runner**：

```powershell
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$base/auto-camera-run.ps1" 2>&1
```

而 watcher 自己的文件在这一夜**没有被改过**（`camera-autowatch.ps1` 的 mtime 早于它的启动时间 04:03:36），进程里的那份与磁盘一致。所以：

| 文件 | 进程里的 | 磁盘上的 | 一致？ |
| --- | --- | --- | --- |
| `camera-autowatch.ps1` | 04:03:36 载入 | 未被修改 | ✓ |
| `auto-camera-run.ps1` | 每轮重新读 | 已含修复 | ✓ 生效 |

结论：**不需要重启 watcher**，修复会用在早上那一轮。

### 18.103 七个 `verdict` 里有四个在文档里查不到

诊断只会给出有限几种结论，但 STATUS 只解释了其中三个。早上真跑出 `ack-at-0x1a-but-no-driver` 这种结论，人得自己猜。补了一张完整的表：

| verdict | 含义 | 下一步 |
| --- | --- | --- |
| `rear-frame` | 出帧 | 停在出帧那张候选 |
| `driver-bound-no-frame` | 绑上了但没帧 | 链路没锁上，比中断计数 |
| `ack-at-0x1a-but-no-driver` | **传感器在总线上**、驱动没绑 | 看 probe 错误（端点配置/器件 ID/上电） |
| `no-ack-at-0x1a` | 不应答 | 供电/复位/MCLK，或地址本就不对 |
| `no-cci-adapter` | 找不到 CCI 适配器 | camss 没起来 |
| `camss-load-failed` | 换模块后 `lsmod` 两个都没有 | `insmod` 没成功，**结论不可信** |
| `stock-camss-still-loaded` | 旧 camss 还在跑 | 重新 `rmmod`，**结论不可信** |

外加 runner 层的 `no-ssh` 与 `diag-timeout`。

顺带把判读表里三处笼统的 `front` 改成精确的 `front-b`（换模块**之后**才抓的那次）——表里的列名变了，行文也必须跟着变，否则读者对不上号。

### 18.104 文档里让人"去看某个字段"，那字段根本不存在

把两份文档里出现的所有 `a_*` / `b_*` / `regulators*` 标识符抽出来，跟诊断脚本真正写入的 42 个键对一遍：

```
文档提到但诊断从不写出的键: a_i2c_modules, a_lsmod, regulators
```

前两个是**过期的名字**：诊断实际写的是 `lsmod_boot` 与 `cci_buses`。文档里三处"看 `a_lsmod`"、"检查 `a_i2c_modules`"会让读者在 JSON 里找一个不存在的字段。已改成真名。

第三个 `regulators` 稍微不同：它是**汇总工具里的死分支**——`out.get("regulators_after_power") or out.get("regulators")`，而诊断从不写 `regulators`，后半个条件永远不会成立。它不会算错，只是让人以为存在这么个键。已删掉。

现在两边一致：文档提到的**键名**全部能在诊断输出里找到（复跑检查后只剩 `regulators` 一项，那是 Python 函数名 `regulators()`、以及这段叙述本身，不是 JSON 键——自动检查区分不了，所以在这里说清）。

### 18.105 顺着"文档提到的文件"查下去，发现一个没被推送的依赖

把文档里的文件名逐个解析，发现 `v4l2-cci.ko` 在**任何地方都不存在**——可诊断脚本的两处都在用它：

```python
out['a_insmod_cci'] = run('sudo -n insmod /tmp/v4l2-cci.ko 2>&1 || true')       # 步骤 A
out['b_modprobe_deps'] = run('sudo -n modprobe -a videobuf2-… 2>&1 || true')     # 步骤 B
```

而 runner 只推四个文件（`qcom-camss.ko`、`imx586.ko`、两个 py），**从来没有推过 `v4l2-cci.ko`**。也就是说步骤 A 那句 `insmod` 依赖的是"以前某次会话恰好推过"——如果没有，它静默失败（`|| true`），然后：

- 若 CCI 没起来 → `cci_scan` 找不到适配器 → 结论 `no-cci-adapter`；
- 而判读表把 `no-cci-adapter` 解释为"camss 没起来或 CCI 没 probe"，**读者会去查 camss，而不是"我们忘了推依赖"**。

这台内核里 `v4l2_cci` 是**独立模块**（`CONFIG_V4L2_CCI=m`，因为 `CONFIG_VIDEO_IMX586=m` 不再提升它），而 `qcom_camss` 链接它。所以正确做法是**按模块名 modprobe**（模块就在设备的 `/lib/modules` 里），而不是指望 `/tmp` 里有个来路不明的 `.ko`：

```python
out['b_modprobe_deps'] = run('sudo -n modprobe -a videobuf2-common videobuf2-dma-sg videobuf2-v4l2 v4l2-cci 2>&1 || true')
out['a_insmod_cci'] = run('sudo -n modprobe v4l2-cci 2>&1 || sudo -n insmod /tmp/v4l2-cci.ko 2>&1 || true')
```

这里保留 `/tmp` 那条作为兜底（静态编译进内核或恰好已加载时，两条都会无害地失败）。改完 23 项单元测试 + 4 项流程测试全过，`deploy-cphy/` 副本已同步（校验收敛）。

**这类问题的共同形状**：一个 `|| true` 让缺失的依赖永远不报错，错误被推迟到下游，最后表现为一个**语义完全不同的 verdict**。

### 18.106 「扫描没做成」和「没人应答」被归成了同一个结论

`cci_scan()` 有两种失败形态：

```python
return {'error': 'no i2c-dev (modprobe i2c-dev failed?)'}        # 扫描没做成
out['warning'] = 'no CCI adapter found; nothing was scanned'      # 扫描做了，没有 CCI
```

而 `verdict()` 只认 `warning`，不认 `error`。于是「没有 `i2c-dev`」这种情况会一路落到 **`no-ack-at-0x1a`**——读者会去查后摄的供电、复位、MCLK，而真实原因可能是**内核没编 `CONFIG_I2C_CHARDEV`**，跟传感器毫无关系。

判据里加一条，把两种「没扫成」分开：

```python
if (out.get("b_scan") or {}).get("error") and not ok("b_capture"):
    return "no-i2c-dev"
```

`not ok("b_capture")` 是必要的：真出帧了就别管扫描，帧是最高优先级证据。测试里专门有一条：`b_scan` 带 error + `b_capture` 成功 → 仍然是 `rear-frame`。

仍是 **23 个测试**（两条新断言加进已有的 `VerdictTests` 里，没有新增测试方法——这里如实记，不把断言数说成测试数），全过；`deploy-cphy/` 副本同步。

### 18.107 后摄抓帧依赖一个可能不存在的设备节点

前摄那条路是**写死格式**的（11/10 验证过的组合），后摄则是**问子设备**：

```python
sensor_dev = d.get("sensor_dev") or ""            # 来自 media graph，形如 /dev/v4l-subdev7
if not sensor_dev:
    raise RuntimeError("sensor entity has no /dev node: " + sensor)
code, name = subdev_mbus(sensor_dev)
width, height = subdev_size(sensor_dev, code)
```

问题在于 `/dev/v4l-subdevN` 是 **udev 发布的**。如果它没出现（没有 udev 规则、或 camss 重载后节点没重建），后摄这条路会直接抛异常 → 诊断记 `capture failed` → 最终 verdict 是 `driver-bound-no-frame`。**读者会去查 CSI 链路**，而真实原因是一个设备节点没建出来。

这跟前面两条是同一个形状：一个"环境不完整"被翻译成了"硬件不对"。

改法：把这段判据提成 `rear_format()`，失败时退回**驱动声明的组合**（`SRGGB10_1X10` / 4000×3000，与 `imx586.c` 里那个 mode 一致），并把原因记进 `rear_from_subdev`；成功时照旧记下真实值。

```json
"rear_from_subdev": {"dev": "/dev/v4l-subdev7", "mbus": "SRGGB10_1X10", "size": [4000, 3000]}   // 正常
"rear_from_subdev": "fallback: sensor entity has no /dev node: imx586 24-001a"                  // 退路
```

测试 6 → **8 项**（退路取值、正常取值各一）。

**过程中我自己踩的坑也记一下**：第一次把测试函数插进了 `main()` 体内（缩进看着对，位置错），于是 `main()` 里出现同名局部变量、报 `UnboundLocalError`；第二次用了这个文件里不存在的别名 `mod`（文件里叫 `cap`）。两步都是靠"测试数量没变 6/6"发现的——**如果只看"全过"，插进去的测试根本没跑也会显示全过**。

### 18.108 一个没有做的改动：media 设备自动发现

顺着设备侧假设往下想，还有一条：抓帧脚本与诊断都固定用 `/dev/media0`。camss 正常注册在 media0，但介质设备索引只是最小空闲号——如果别的驱动先占了 media0，我们要的图就在 media1 上，而读错图的表现是：

```
no imx586 sensor entity in media graph
```

看起来像传感器没了，实际是读错了图。我为此写了一版 `pick_media()`（遍历 `/dev/media*`，找图里真有该 sensor 的那个），写到一半停手并整体回退了：

| 顾虑 | 说明 |
| --- | --- |
| 动的是已验证能用的那条路 | 前摄 11/10 跑通时用的就是 media0；把默认改成自动挑，等于在别人验证过的路径上加分支 |
| 现在没有设备可以验证 | 回退后至少与 11/10 的行为完全一致 |
| 收益不确定 | 需要 media1 的前提是别的驱动抢了 media0，本机没有证据 |
| 代价明确 | 一旦挑错，错误会以找不到 sensor 的形式出现，和真故障混在一起 |

回退后三份副本一致、8 项测试全过、`--media` 仍是 `/dev/media0`。改为把假设写进文档：STATUS 的判读表加了一行——报 `no ... sensor entity in media graph` 时，先确认哪个 mdev 里有该 sensor，不要直接当成传感器故障。

记这条的理由：一个改动想法上更健壮，并不等于现在就该做。在没有真机可验的夜里，把已验证的路径保持原样，比加一层没人验证过的智能更安全。

### 18.109 C-PHY 的速率表里没有 698 Msps 这一档——这是一个值得记住的线索

三相（C-PHY）驱动在配置通道前，会按 `link_freq` 从一张表里挑一档"带宽"，再把那一档的寄存器覆盖上去。我们加进去的表：

```
sm8250  : 2.5, 3.5, 4.5 Gsps        （来自厂商 techpack cam_csiphy_1_2_1_hwreg.h）
sa8775p : 1.5, 1.7, 2.5, 3.5, 4.5 Gsps（上游自带的对照）
```

而我们的 `link_freq` 是 **698,395,833**（0.7 Gsps）。选择规则是"第一档带宽 >= 请求值"，所以会落到 **2.5 Gsps** 那一档——用一档**快 3.6 倍**的时序寄存器去跑 0.7 Gsps 的链路。

这件事本身不一定致命（时序寄存器大多是延时/裕量常数，跑慢一点常常能容忍），但它指向一个更值得注意的推论：

**如果这块板的 IMX586 真是 3-trio C-PHY、跑 0.7 Gsps，厂商驱动不可能把最低档做成 2.5 Gsps——那是给自己产品的时序去配一档快 3.6 倍的表。** 反过来说，SM8250 厂商表从 2.5 Gsps 起，更像是为"快链路"准备的，而 0.7 Gsps 这种速率在本板上更可能走 **D-PHY 4 lane**（D-PHY 路径根本不需要这张表，只要 lane 使能位）。

这条线索和另外两条一致：前摄在这块板上是 D-PHY；社区 hotdog（8 Pro，同为 IMX586）的驱动也是 4-lane D-PHY。

**当时没有据此改候选顺序**，理由：cphy3 的排位来自更早的取证，不能因为一条间接推论就翻掉；而且一轮本来就会把四张都跑一遍，顺序只在"手机可用时间很短"时才有影响。不过接着又查了两件事（原厂 DTBO 里完全没有 CSI-2 信息、任务文档自己写着 kebab 的 PHY 类型从未确认），合起来足够把顺序调一次——见 **18.110**。

**但如果真跑到 `cphy3` 且出现这组特征**：

```
acks=0x1a   driver=imx586  3ph=yes  capture 只有 front  →  driver-bound-no-frame
```

那就说明"传感器在、驱动绑了、三相 ops 也生效了，但链路锁不上"——这时第一个该怀疑的就是这张表。可做的实验（按侵入性排序）：

1. 把 `link_freq` 改成 `2500000000` 试（纯 DT 改动，让选择逻辑与 2.5 Gsps 表自洽）；
2. 或者让 `csiphy_cphy_data_rate_config()` 在 `link_freq` 低于最低档时**不覆盖**（保留基础寄存器组）；
3. 录一版 0.7 Gsps 的表（需要从厂商对应档位取，不能凭猜）。

三条都写在这里，是为了让早上的人**不必重新发现这个问题**。

### 18.110 把 D-PHY 那张候选提到前面：一次基于证据的排序改动

翻回任务文档，`r4-camera-task-20261110.md` 里写得很清楚：

```
hotdog 是 SM8150 + C-PHY 3-lane；kebab 是 SM8250，且 PHY 类型/lane 数尚未确认
```

也就是说 **C-PHY 从来只是一个"沿用驱动原生形态"的假设**，主报告里也如实写着"无法用 DT/vendor 源码确证"。这一轮又补了两条证据：

| 证据 | 指向 |
| --- | --- |
| 原厂 DTBO 的 19 个 entry 全解了一遍 | **完全没有** CSI-2 相关信息：只有 `camera-flash`、`camera_flash_therm`、充电的 `input_current_camera_ma`。没有 `imx586`、没有 `csiphy`、没有 `bus-type`/`data-lanes` |
| SM8250 的 C-PHY 速率表 | 只有 2.5 / 3.5 / 4.5 Gsps；我们的链路是 0.7 Gsps。厂商不会给自己产品配一档它表里没有的速率 |
| 同板前摄 | D-PHY 4 lane |

三条都不是铁证（尤其第一条只是"没有反证"），但它们**一起把 D-PHY 推到至少不弱于 C-PHY**，而之前 C-PHY 排第一的唯一理由就是"驱动原生形态"。

于是把顺序改成：

```
r4ctl(对照) → v2(基线) → v3c(D-PHY 4 lane) → cphy3(C-PHY 3 trio) → cphy2(C-PHY, pinctrl 只含 MCLK)
```

改动本身只有两行（`$images` 里 v3c 与 cphy3 对调），一轮仍然会把两张都跑；**意义只在"手机可用时间有限时先跑更可能的那张"**。改完 PowerShell 语法检查通过、顺序如上。

**同时明确记下没有做的事**：没有删掉 cphy3、没有改任何 DTS/驱动——PHY 类型仍未确证，两张都必须留下。如果 v3c 先出图，那正好回答了任务文档里那个悬了一整天的问题。

### 18.111 用"早上的人"的眼睛看一遍状态页

`status-camera.ps1` 是早上第一眼要看的东西，所以直接把它跑出来读：

```
==================== 一句话 ====================
phone is ABSENT (no fastboot, no ssh)
==================== watcher ====================
  running pid=45628 since 11/11/2026 04:03:36
==================== camera-test.log (last 5) ====================
  2026-11-11 02:07:03 === run start ===
  2026-11-11 02:07:03 --- candidate v2 (safety) ---
  2026-11-11 02:07:03 NoPon and not in ssh; waiting for fastboot without PON
```

一眼就看出三个问题：

1. **`camera-test.log` 里那三行是离线桩跑出来的**，不是真机。可它长得跟真实的运行开头一模一样（`=== run start ===`），早上很容易被当成"夜里跑过一轮"。→ 已归档到 `history/camera-test-sims-20261111.log`，当前日志只留一行说明。
2. **看不出"接下来会跑哪几张、什么顺序"**。这个顺序刚在一小时前调整过，正是最该显示的东西。→ 从 `auto-camera-run.ps1` 里直接解析 tag 打印（单一来源，改了 runner 状态页自动跟着变，不会再次不同步）。
3. **只显示一个 watcher**，但日志里有三次 `autowatch start`（我手工重启过）。如果同时跑着两个 watcher，它们会抢同一台手机。→ 加了"多于一个就报警"。

改完的样子：

```
==================== watcher ====================
  running pid=45628 since 11/11/2026 04:03:36
  next order: r4ctl -> v2 -> v3c -> cphy3 -> cphy2
==================== camera-test.log (last 5) ====================
  (no real run yet - offline test output archived in history/camera-test-sims-20261111.log)
```

顺手修掉一个自己的小错：`next order` 第一版把 `custom` 也列进去了——那是 `-Image` 手动覆盖时的伪条目，自动流程根本不会跑它。已过滤。

**这一类改动的价值不在代码**：状态页是唯一一个"人会在半睡半醒时读"的东西，它写错不如不写。

### 18.112 我把时间读错了三个小时，而且一直没发现

这一轮为了核对"watcher 到底跑了多久"，直接问了一次时钟：

```
local : 2026-11-11 05:46:22
utc   : 2026-11-10 21:46:22
offset: 08:00:00
```

而我在前面若干轮的报告里一直写成 **08:xx**。原因是：早前用 Python 读文件时间时拿到的是 UTC（`toISOString`），我据此认定"本地比文件时间快 8 小时"；后来又把这条规则用反了——把本来就是本地时间的 `Get-Date`／日志时间当成了 UTC，再往上加 8 小时。于是 04:51 被我说成了 08:56。

**影响范围（逐项核过）**：

| 对象 | 是否受影响 |
| --- | --- |
| 代码、补丁、镜像、测试结果 | 不受影响——都与时间无关 |
| `autowatch.log` / `camera-test.log` | 不受影响——脚本用的是 `Get-Date`，一直是对的 |
| watcher 的 72 小时窗口 | 不受影响——由脚本自己算，结束时间是 11/14 04:03（本地） |
| `STATUS.md` 的标题时间 | 原来写 04:51，是对的；但已过期，改为当前时间并在标题里注明取自 `Get-Date` |
| **我在对话里的报时** | **错的**，应为 05:xx 而不是 08:xx |

处置：

1. `status-camera.ps1` 现在**第一行就打印当前本地时间**（含时区偏移），状态页自带时间戳，不再需要谁去推断；
2. STATUS 标题注明时间来源；
3. 这条错误写在这里，而不是悄悄改掉——它是"我基于一个未经复核的换算规则连续推断"的典型样本，和前面那些"静默失败被翻译成错误结论"是同一类问题，只不过这次错的人是我。

顺带一个更实际的提醒：**现在才 05:5x**，用户睡下约五个半小时。watcher 还有将近三天窗口，不必着急。

### 18.113 改完顺序之后，真跑一遍（离线桩）确认它真的按新顺序走

顺序改了不能只看"文件里那两行换了位置"。用离线桩把 runner **整条流程**跑了一遍，这次不加 `-NoPon`：

```
--- candidate r4ctl (control: proven fastboot-boot image) ---
fastboot: b5fcd385	fastboot
slot: slot-successful:a: yes  slot-retry-count:a: 6  slot-unbootable:a: no
control image booted and reached ssh - the temporary boot path works tonight
--- candidate v2 (safety) ---
candidate v2 gave no rear frame (a=0 b=0/no-ack-at-0x1a); moving on
--- candidate v3c (dphy+pinctrl) ---
candidate v3c gave no rear frame (a=0 b=0/no-ack-at-0x1a); moving on
--- candidate cphy3 (cphy+pinctrl) ---
candidate cphy3 gave no rear frame (a=0 b=0/no-ack-at-0x1a); moving on
--- candidate cphy2 (cphy, pinctrl=mclk only) ---
candidate cphy2 gave no rear frame (a=0 b=0/no-ack-at-0x1a); moving on
still in fastboot - rebooting into the normal system
summary: r4ctl:ssh-up  v2:a=0 b=0/no-ack-at-0x1a  v3c:a=0 b=0/no-ack-at-0x1a  cphy3:…  cphy2:…
```

**v3c 确实排在 cphy3 前面**，每张都走完"启动 → 核对内核身份 → 推四个文件 → 诊断 → 存 `diag-*.json.txt` → 无帧则下一张"，最后 `fastboot reboot` 回正常系统并打汇总。

顺带排掉一个假警报：第一次跑（带 `-NoPon`）只打印 `r4ctl:no-fastboot` 就退了，我一度以为是 bug。读代码才发现 `ToFastboot()` 第一行就是：

```
if ((SshOk) -and $NoPon) { Log "NoPon set: leaving the phone alone"; return $false }
```

而离线桩的 `ssh` 是**成功**的，于是"别动手机"这条规则正确生效——是我用错了参数，不是程序错了。这种"先怀疑代码、结果是自己调用方式不对"的情况，靠的是把那行日志找出来读，而不是靠猜。

### 18.114 手机在系统里、SSH 通、但 `pon` 助手不见了

runner 的正常开局是：SSH 通 → 跑 `sudo -n /usr/local/sbin/kebab-reboot-fastboot` 让手机重启进 fastboot → 等 7 分钟 → `fastboot boot`。

如果那个助手**不存在**（换了根文件系统、升级过、或者被清理掉），现象是：日志里 `pon:` 后面跟着一行 `No such file or directory`，然后就是**七分钟静默**，最后 `cannot reach fastboot; aborting run`。早上的人看到的只是"fastboot 一直没出现"，很容易去怀疑线、怀疑 USB、怀疑手机坏了。

加了一行判断：

```
if ($ponText -match "not found|No such file|Permission denied|sudo:") {
    Log "pon helper did not run - put the phone into fastboot by hand (power + volume down); the runner keeps looking for 7 minutes"
}
```

并且**造了一个会失败的离线桩来证明它真的会触发**（`sim-ponfail/`：ssh 一切正常，只让 `kebab-reboot-fastboot` 这一条返回 127）：

```
pon: bash: /usr/local/sbin/kebab-reboot-fastboot: No such file or directory
pon helper did not run - put the phone into fastboot by hand (power + volume down); the runner keeps looking for 7 minutes
```

顺带修正一个对桩的误解：`sim/booted.flag` 不是"预置文件"，而是 `sim/fastboot.cmd` 在 `boot` 时创建、`diag` 桩在结束时删除的——它模拟"启动过才连得上 ssh"。所以我那次 `Copy-Item` 失败是**正常的**，不是桩坏了。

离线桩 9 → **10 套**。

### 18.115 把本机所有可能写着 PHY 类型的地方翻完了：没有

C-PHY 还是 D-PHY，是这一整天悬着的核心问题。这一轮把本机**每一个**可能记录它的地方都查了：

| 来源 | 结果 |
| --- | --- |
| 原厂 DTBO（19 个 entry，全解） | 只有 `camera-flash`、`camera_flash_therm`、充电的 `input_current_camera_ma`。**没有 csiphy / bus-type / data-lanes** |
| `vendor_a.img` 的 `/lib/modules`（45 个模块） | 全是 `audio_*`、`qca_cld3_qca6390/6490`、`gspca_main`、`rmnet_*` 之类，**没有任何 cam 模块**（相机驱动是编进厂商内核的） |
| `vendor_a.img` 的 `/etc/camera` | 只有 AI 模型（`segment.dlc`、`depth.dlc`、`*.model`），没有 CSI 配置 |
| `vendor_a.img` 的 `/lib/camera` | `com.qti.eeprom.truly_cmb433.so`、`com.qti.tuned.default.bin`——EEPROM 与调校数据，不是 PHY 配置 |
| OTA payload 的分区表 | 没有 `vendor_dlkm`；`vendor` 就是那个 968 MB 的镜像，已解 |
| 厂商相机源码 / techpack | 本机没有 |

所以主报告里那句"**无法用 DT/vendor 源码确证**"不是偷懒，是**核实过的结论**：本机没有任何文件记录这件事。

这意味着：

1. PHY 类型只能由**设备上的运行结果**回答——哪张候选出图，就是哪一种；
2. 在那之前，任何"应该是 C-PHY/D-PHY"都只是推论，我的排序改动（把 D-PHY 提前）也是推论，文档里已经这么写了；
3. 反过来说，**不需要**再花时间在本机翻文件了——这条已经穷尽。

顺带记一个边界：`/etc` 里那两个 `OPlus_QC_LTM_Commercial_SM8250_*.pfm` 确认了平台是 SM8250（与已知一致），但对 PHY 类型没有帮助。

### 18.116 中断计数早就有，但没人看得出"停在哪一级"

诊断一直在按名字收集 `/proc/interrupts` 的增量（`csiphy`/`csid`/`vfe`/`cci`/`camss`），也确实存进了 JSON。问题是：**早上拿到 `{"csiphy0": 9}` 这种数字，没人知道它意味着什么。**

而这恰恰是后摄链路最关键的分级信息：

| 现象 | 说明 |
| --- | --- |
| 只有 `csiphy` 涨 | PHY 配了、数据没出来 → **lane / PHY 模式**的问题（最可能是 C-PHY vs D-PHY 选错） |
| `csiphy` + `csid` 涨 | 数据到 CSID 了 → **路由/settle** 的问题 |
| 连 `vfe` 都涨 | 链路是通的 → **格式/队列**的问题，别再动链路 |

于是加了一个 `irq_story()`，把增量翻译成一句话，并存成 `a_irq_story` / `b_irq_story`；汇总工具把它打成表下的一行 `irq:`：

```
tag  verdict                acks  driver                                     capture  3ph  rails
v3c  driver-bound-no-frame  0x1a  /sys/bus/i2c/devices/0-001a/driver/imx586  -        yes  -
    irq:  csiphy fired but csid saw nothing - PHY/lane side (csiphy +9)
```

单元测试 23 → **28 项**（五种情形：都没涨、只 PHY 涨、到 CSID、到 VFE、只有无关中断）。STATUS 的两处"比中断计数"也改成"先看这行"。

**为什么这条值得做**：它不增加任何设备端采集成本（数据早就在），只是把已经拿到的数字翻译成人能用的结论——这正是前面那些缺陷的共同反面。

### 18.117 四张候选内核完全一样，唯一区别是 DTB——但之前没人验证"跑的是哪一张"

四张候选镜像的内核是**同一份**（sha `f3391b293976`，四张全同），ramdisk 也相同。它们的区别**只在 DTB**。而 runner 的身份检查是 `grep CONFIG_VIDEO_IMX586`——四张都有，**等于什么都没验证**。

也就是说：如果刷错、或者 boot header 的 id/地址算错导致 ABL 用了别的树，runner 会毫无察觉地继续，然后我们会拿着一张 D-PHY 的图去解释 C-PHY 的失败。

补两处：

1. **runner 每次启动后读回设备树的端点**并记一行：

```
dt: bus=4 | lanes=0 1 2 3
```

   并且和这张候选"应该是什么"比对（`expect` 里写着 dphy/cphy），不一致就明写警告。DT 里 `MEDIA_BUS_TYPE_CSI2_CPHY=1`、`_DPHY=4`（注意不是 v4l2 的 6/5）。

2. **汇总表多一列 `bus`**：`4(dphy)` / `1(cphy)`；遇到没见过的数值**原样显示**而不是映射成 `-`（宁可让人看见怪数字，也不要悄悄吞掉）。诊断里也补了 `dt_lanes`。

然后**两个方向都证明它真的会响**（`sim/ssh.cmd` 现在会按 `sim/dt.bus` 回答）：

```
--- dt.bus=4 (D-PHY) ---
  v3c   dt: bus=4 | lanes=0 1 2 3          ← 相符，不响
  cphy3 dt: bus=4 | lanes=0 1 2 3  -> WARNED
  cphy2 dt: bus=4 | lanes=0 1 2 3  -> WARNED
--- dt.bus=1 (C-PHY) ---
  v3c   dt: bus=1 | lanes=0 1 2 3  -> WARNED
  cphy3 dt: bus=1 | lanes=0 1 2 3          ← 相符，不响
```

`r4ctl` 与 `v2` 没有 PHY 期望，两个方向都不响——**不能因为"想让它报错"就对没有期望的候选也报警**。

测试：诊断 28 项、汇总 19 → **23 项**（含"未知数值要原样显示"）。

### 18.118 `scp` 返回 0，但设备上未必是我们哈希过的那份

推送那一步的判据是 `scp` 的退出码。它只证明"传输没报错"，不证明 **`/tmp` 里现在那份文件的字节**就是我们哈希、我们测过的那份。`/tmp` 满、同名旧文件被保留、文件系统异常——这些都可能让 rc=0 而内容不对，然后我们拿着**另一份代码**的结论去解释硬件。

补一步：推送完在设备上 `sha256sum` 读回四个文件，和本地逐个比。

两条路径都验证过：

```
--- 设备没有可用输出（必须继续，不能卡死整轮） ---
could not read back the pushed hashes (no parsable sha256sum output); carrying on
summary: r4ctl:ssh-up  v2:a=0 b=0/no-ack-at-0x1a …          ← 正常跑完
--- 设备上的 hash 是错的（每张都必须停下） ---
HASH MISMATCH on /tmp/qcom-camss.ko: device=000000000000 ours=17a444fe4b3c
HASH MISMATCH on /tmp/imx586.ko: device=000000000000 ours=b51c07a21449
HASH MISMATCH on /tmp/camera-full-diag.py: device=000000000000 ours=3e386a19cfd6
the device is not running the files we hashed - stopping this candidate
summary: r4ctl:ssh-up  v2:stale-files  v3c:stale-files  cphy3:stale-files  cphy2:stale-files
```

设计上的两个取舍：

1. **读不到就继续**（记一行 "could not read back"）。因为设备缺 `sha256sum` 或 `/tmp` 权限异常时，把它当成硬失败会让整轮白跑——而"读不到"本身已经写进日志，不会被藏起来；
2. **读到了但不等，必须停**。这种情况下继续跑，等于用未知代码得到结论，比不跑更糟。

顺带一个交叉印证：日志里我们本地的 `qcom-camss.ko` 前缀是 `17a444fe4b3c`，与 `SHA256SUMS-camera` 里那条完全一致——说明读回值确实来自被哈希过的那份文件，而不是又一次"看起来对"。

### 18.119 十套离线桩终于有了一条命令，而它第一次跑就抓到两个问题

之前每套桩都是我在会话里手工跑：设 PATH、跑 runner、读日志、还原日志。改一次 runner 就要重复五到十次，很容易漏，而且**没有任何东西保证"改了 A 不会弄坏 B"**。

写了 `run-sim-tests.ps1`：十套桩一次跑完，每套只断言"它该说的话出现过"，跑完自动还原日志、清掉 diag 文件，失败时退出码非零。

**第一次跑：3 套失败。** 而且其中一套是我刚刚弄坏的：

| 失败 | 根因 |
| --- | --- |
| `rear-frame` | 我这轮加的 `VerifyPushed` 命令里含 `/tmp/camera-full-diag.py` 这个**文件名**，而桩是用 `findstr /C:"camera-full-diag"` 认"这是诊断调用"的——于是哈希检查被当成了诊断，桩把 `booted.flag` 删掉，真正的诊断再跑时已经没有"已启动"标记，**每张候选都 `no-result`** |
| `diag-timeout` | 期望值写成 `:diag-timeout`，实际是 `/diag-timeout`；而且 12 分钟上限写死在代码里，桩等不了那么久 |
| `no-ssh` | 桩目录里残留 `booted.flag` 让"fastboot 设备"直接消失，走成 `no-fastboot`；且 45×10 秒的重试让这一套要跑 7.5 分钟 |

三处都是**真的**问题（前两条会直接影响真机结论的解读，第三条是测试工具本身不可靠）。修法：

- 桩改用 `findstr /C:"python3 /tmp/camera-full-diag.py"` 精确匹配诊断调用，并给哈希检查单独一个分支；
- 诊断上限改成可覆盖（`KEBAB_DIAG_CAP`，默认仍是 720 秒），桩用 8 秒就能证明超时路径；
- 每套桩开跑前清掉 `booted.flag`，`no-ssh` 那套用 `-SshTries 3`（30 秒）；
- 顺手加 `-Only <名字>` 单跑一套，改桩时不用等四分钟。

**最终 10/10 全过。** 但更重要的是：它在**没有手机**的情况下，抓到了一个"改了验证代码、却让桩静默换了行为"的连锁反应。真机上这个具体的碰撞不会发生（`sha256sum` 会正常输出哈希），可它证明了一件事——**runner 里每一条 ssh 命令的文本，都是桩的输入**；一旦改命令，桩就可能认错分支，而这个错误只会表现为"结果不对"。

这条也补上了前面反复出现的模式：我一次一次手工重跑、手工还原日志，靠的是"记得"；现在靠的是一条命令和它自己的断言。

### 18.120 出帧之后：把那张图拉回来

以前"出帧"这件事的终点是一行日志 `rear camera produced a frame`。可这张图是抓帧脚本写在**手机上**的 `/tmp/camera-lab/`，而 runner 一旦 break 就再没人碰它。等到早上，手机可能已经被重启、`/tmp` 已经清空，**唯一的证据只剩日志里那个数字**。

补一步：确认出帧后立刻 `scp` 把 `/tmp/camera-lab/*` 拉回本地 `frames/`，并按文件记录大小与 sha256：

```
rear camera produced a frame with candidate v2 (a=0 b=1/rear-frame)
keeping the phone on this candidate; pulling the frame back now
frame fetch rc=0 files=1 -> .../camera-r4-20261110/frames
  rear-sim.pgm 21 B sha256:5c2ddbac0be7b627
```

拉不到时明写原因，而不是静默留白：

```
no frame files came back - the phone may have rebooted, or nothing was written to /tmp/camera-lab
```

这一条也是用桩证明的：`sim-frame/scp.cmd` 现在只在**拉取**（命令行里出现 `camera-lab`）时才伪造一个文件，推文件时照旧什么都不做——早先我把它写成无条件的 `echo > %3`，会让 PushFiles 也去写文件（`%3` 其实是 `-o`），属于"把测试工具写成了会制造噪声的东西"。改成"取最后一个参数做目标"才稳。

**为什么值得做**：这一夜所有工作都指向"证据要能被复核"。出帧是唯一一次真正的成功信号，把它留在手机上等着被清空，是这一夜最不该有的收尾方式。

### 18.121 `stock-camss-still-loaded` 把两种情况混成了一个名字

判据原本是"`b_insmod` 和 `b_insmod_imx` 里任何一个出现 `File exists`"就报 `stock-camss-still-loaded`。可这两件事指向**完全不同的文件**：

- camss 是**旧的那份不含 C-PHY 支持**的模块还在跑 → 边界配置全白费；
- imx586 是**传感器驱动**被根文件系统那份抢先绑定（udev 在 `rmmod` 后立刻按 compatible 重新 probe，我们的 `insmod` 才报 `File exists`）→ 这一轮跑的根本不是我们推的那份驱动。

两者都让结论不可信，但要查的地方不一样。名字报错会把早上的人送去翻 camss，而真正的问题在传感器驱动。

现在分开：

| 判据 | 结论 |
| --- | --- |
| camss 的 insmod `File exists` | `stock-camss-still-loaded` |
| 只有 imx586 的 insmod `File exists` | `stock-imx586-loaded` |
| 两者都 `File exists` | `stock-camss-still-loaded`（camss 先修，否则 imx 也挂不上） |
| 真抓到帧 | `rear-frame`（帧永远优先于"模块可疑"） |

单元测试 28 → **32 项**，四条各一。这条与 §18.104（文档里引用了不存在的 JSON 键）、§18.107（`/dev/v4l-subdevN` 缺失被判成"驱动绑了没出帧"）是同一类：**名字比事实说得更具体时，人就会照名字去查。**

### 18.122 前摄是这轮唯一的对照组，但没人读它

前摄（IMX471）在 r4 上用**原厂 camss** 本来就能出图（11/10 已验证）。诊断里也一直在做两组前摄抓帧：`a_front`（原厂 camss）和 `b_front`（换上我们的 camss 之后）。这两组是**整夜唯一的对照实验**：

- `a_front` 出图 = 这台机器、这盏灯、这个抓帧脚本都正常；
- `b_front` 不出图 = 我们的 camss 把一件原本能用的东西弄坏了。

可这两个值只躺在 JSON 里，runner 只解析 `REAR_RESULT`，而 summary 一行又是早上唯一会看的东西。**等于做了对照实验，却不读结果。**

补一条 `FRONT_RESULT a=%d b=%d`，以及 runner 的判断：

```
FRONT_RESULT a=1 b=0
front camera regressed with our camss (stock ok, ours not) - this candidate is not safe to keep
candidate v2 gave no rear frame (a=0 b=0/front-regression/no-ack-at-0x1a); moving on
```

为什么这句重要：**如果前摄在我们的模块下也死了，那么"后摄没出帧"就不再是后摄的问题**，而是 camss 本身在某条公共路径上坏了（供电、CCI、VFE、时钟）。反过来说，只要 `b_front` 还出图，就说明 camss 主体是健康的、"只有后摄这条路"不通——这两种情况的下一步完全不同。

`sim-frontreg` 是第 11 套桩，专门证明这句话会响；同时复核了普通桩**不会**误报（前摄两组都成功时保持安静）。

### 18.123 45 个字段里，汇总表只读 15 个——包括内核自己那句抱怨

把诊断输出的全部 JSON 键列出来对了一遍：**45 个键，汇总工具只读 15 个**。缺的那些里，最不该缺的是 `b_dmesg` / `b_dmesg_final`——驱动自己写下的错误串（"failed to set the CSI-2 lane mode"、"inclk frequency mismatch"、"failed to get VANA"）全在那里，而汇总表那一行只印 acks、bus、3ph、rails。

也就是说：**最直接的一句证词，要靠人去打开 JSON 才看得到。**

现在汇总表下面会直接打出来（最多 4 条，只取尾部，按"上电→探测→链路→取帧"的顺序匹配已知错误串）：

```
tag  verdict                acks  capture  bus      3ph  rails
v3c  driver-bound-no-frame  0x1a  -        4(dphy)  yes  -
    irq:   csiphy fired but csid saw nothing - PHY/lane side (csiphy +9)
    dmesg: qcom-camss: failed to start streaming
    dmesg: imx586 0-001a: failed to set the CSI-2 lane mode
```

匹配表就是这一夜整理的 dmesg→病因表（15 条驱动错误串 + camss/v4l2 层的 Unsupported/timeout/error），所以它和 STATUS 里那张表**是同一份知识**，不会各说各话。

测试 23 → **27 项**（认得出、干净输出时安静、也读 step A、只保留尾部）。

剩下没被读的键我逐个看过：`a_topology`/`b_topology`（media-ctl 拓扑）、`b_dyndbg`（动态调试图开关回执）、`b_modprobe_deps`、`b_rmmod` 这些属于**事后追查**用，塞进一行汇总只会把它变成噪音，继续留在 JSON 里。判断标准是"早上第一眼需不需要它"，不是"能不能塞进去"。

### 18.124 看门狗从来没人演过：一演就发现它会"凭空报成功"

`camera-autowatch.ps1` 是早上真正会动手的那个东西——它等手机出现、调用 runner、再决定"要继续重试还是收工"。可它从来没被**端到端**跑过一次：之前只验证过 runner（用桩），看门狗本身只是"启动着、在轮询"。

第一次演戏（`-Hours 1 -MaxPasses 1` + 桩）就抓到一个真 bug：

```
--- unhealthy slot -> watcher must not retry ---
summary: r4ctl:no-fastboot          ← 什么都没发生
frame produced - stopping           ← 它却说"出帧了"
```

原因：判断"是否出帧"有两路，一路看 runner 的 stdout，另一路**回看 `camera-test.log` 的最后 25 行**。而 `camera-test.log` 在两次运行之间**不会被清空**——上一场演戏（`sim-frame`）写下的 `produced a frame` 还躺在里面，于是这一场什么都没做，也被判成成功。

改成**只认本次运行新追加的字节**（运行前记下文件长度，运行后从那个偏移往后读）。三种情形现在都对：

```
PASS sim-frame (15 s)      → frame produced - stopping
PASS sim (19 s)            → 不提"frame produced"，安静退出
PASS sim-unhealthy (1 s)   → boot slot is unhealthy - stopping instead of retrying
```

**为什么这条最要紧**：看门狗的唯一职责就是判断"成功还是再试一次"。它误报成功，就等于**手机明明没出图，整夜却不再尝试**；而且在旧逻辑下，只要历史上任何一次运行出过帧，之后每次失败都会被读成成功。

两个操作教训也记下来：

1. **改脚本对已经在跑的进程不生效。** PowerShell 启动时就把脚本读进内存了。所以修完必须重启看门狗——旧的那个（pid 45628）跑的仍是旧逻辑。这次重启顺带发现当时其实有**两个**看门狗进程（45628 与 37360），同时出现会两个 runner 抢同一台手机。
2. **`Start-Process` 在这里活不下来**，进程随调用方的 shell 一起被回收。改用 `Win32_Process::Create`（父进程是 WMI 提供者，脱离调用树），现在的 pid 28304、窗口 68 小时（到 11/14 07:26）。

演戏脚本固化为 `run-watch-rehearsal.ps1`（三例，自动备份/还原 `autowatch.log`、清掉 `autowatch-run-*.txt`）。

### 18.125 顺手读到我自己移植代码里的越界读

给"如果 C-PHY 那档速率不对怎么办"备料时，重读 `csiphy_cphy_data_rate_config()`，发现选表那段有问题：

```
for (idx = 0; idx < num_settings; idx++) {
    if (settings[idx].bandwidth >= link_freq)
        break;
}
```

如果链路频率比表里**所有**条目都高，循环正常结束后 `idx == num_settings`，紧接着的 `settings[idx].bandwidth` 就是**越界读**（三档表，读第 4 个元素）。注释写着"否则取最高一档"，代码并没有做这件事。

补一行 clamp：

```
if (idx >= num_settings)
    idx = num_settings - 1;
```

**诚实的范围**：本机 imx586 的 `link_freq` 是 0.698 Gsps，表里最低一档就是 2.5 Gsps，所以这段**永远走不到**，它与今晚四张候选的行为无关；这是把移植代码修到"符合它自己的注释"。提交 `2097e6406`，导出 `0008-media-camss-3ph-clamp-rate-index.patch`。

验证到什么程度：

| 项 | 结果 |
| --- | --- |
| 补丁能干净 apply 到上一提交 | 是（`git apply --check` 在临时 clone 里通过） |
| 改动真能编译 | 是：借 venus 那棵树的 `.config`，`make ... camss-csiphy-3ph-1-0.o` 实际产出 339,680 B 的 `.o` |
| 补丁重放与工作树一致 | 是：`verify-patches.sh` 加入 0008 后 `check-all -Slow` 全过 |
| 真机跑过这条路径 | **没有**（也跑不到） |

顺带记一个环境细节：r4-camera 的源码树**没有 `.config`**（构建产物早被清掉），所以编译验证要临时借配置；验证完 `git clean -xdf` 清掉 219 项构建产物，树恢复到"只有已提交改动"的状态。**验证过程本身不应该给下一轮留下垃圾。**

> 补记：查"到底有几个看门狗"时，匹配串 `camera-autowatch.ps1 -Hours` **匹配不到真进程**——`Start-Process`/WMI 传参后命令行里是 `camera-autowatch.ps1" -Hours`（路径带引号）。于是每次查询只匹配到**查询自己的那个 powershell**，读出来的 pid 每次都变（28304 → 30044 → 36104），看起来像"看门狗反复重启"。改成 `camera-autowatch\.ps1"? -Hours` 后稳定读到唯一的 pid 28304。`status-camera.ps1` 的匹配已同步收紧。

### 18.126 D-PHY 那条路用的是上游原表——这决定了 v3c 失败时该怀疑谁

把"换到 D-PHY 之后，PHY 到底写哪张表"读清楚了（`csiphy_3ph_init()` 里按 SoC + `phy_cfg` 分派）：

```
case CAMSS_7280:
case CAMSS_8250:
    if (phy_cfg == V4L2_MBUS_CSI2_DPHY) {
        lane_regs = &lane_regs_sm8250[0];          /* 上游原表，我们没动过 */
    } else if (phy_cfg == V4L2_MBUS_CSI2_CPHY) {
        lane_regs = &lane_regs_sm8250_3ph[0];      /* 我加的表 */
        data_rate_settings = data_rate_settings_sm8250_3ph;
    }
```

两点结论：

1. **v3c（D-PHY）走的是完全上游的代码路径**——lane 表、settle、gen2 配置都没被我改过。所以如果 v3c 也不出帧，嫌疑基本可以排除"驱动寄存器写错"，应该转向板级：引脚复用、供电时序、lane 映射、模组本身。这比"两种 PHY 都有嫌疑"有用得多。
2. **SM8250 是少数几个 C-PHY 真正有表可写的平台**（对比：6150/6350/8280XP/X1E80100/8550 的 C-PHY 分支直接给 `lane_regs = NULL`，即不支持）。也就是说这条路上游本来就没怎么被验证过，我们等于在走一条冷门分支——这也是为什么把 D-PHY 放在前面更合理。

顺带确认 `csiphy_get_lane_mask()` 两种模式都对：C-PHY 从 bit1 起按 trio 位置置位（`BIT(pos*2+1)`），D-PHY 先置时钟位再按 lane 位置置位。我们 DT 里 C-PHY `<0 1 2>` → `0x2a`、D-PHY `<0 1 2 3>` → `0xd5`，与代码一致。

### 18.127 一个 `bus-type` 同时配置两端：CSID 从 CSIPHY 的配置里抄

一直以为"PHY 类型"只是 CSIPHY 的事，CSID 另有自己的端点配置。读 `camss-csid.c` 的 `csid_link_setup()` 才发现不是：

```
lane_cfg = &csiphy->cfg.csi2->lane_cfg;
csid->phy.lane_cnt = lane_cfg->num_data;
if (lane_cfg->phy_cfg == V4L2_MBUS_CSI2_CPHY)
        csid->phy.phy_sel = CSID_PHY_SEL_CPHY;
else
        csid->phy.phy_sel = CSID_PHY_SEL_DPHY;
csid->phy.lane_assign = csid_get_lane_assign(lane_cfg, lane_cfg->num_data);
```

也就是说 **DT 里传感器端点那一个 `bus-type` 同时决定 CSIPHY 和 CSID 的模式和 lane 数**。这条事实有两个直接后果：

1. **"`csiphy` 有中断、`csid` 没中断"几乎是 PHY 类型选错的同义词**——CSID 被设成 C-PHY 却在收 D-PHY 的差分信号，解不出帧边界，自然一个中断都不来。所以那一行诊断不是"含糊的提示"，它直接指向 `bus-type`。诊断里那句话已据此改写：

```
csiphy fired but csid saw nothing - PHY/lane side, check bus-type first (csid copies the csiphy lane config) (csiphy +9)
```

2. 反过来，**只要出帧，就说明 `bus-type` 至少对这一端是对的**（CSID 和 CSIPHY 同时被它配置）。这也解释了为什么"出帧"值得作为唯一成功判据。

另外确认 `lane_cnt` 对 C-PHY 取 `num_data`（3），不是乘 2——与 `csiphy_get_lane_mask()` 的 `BIT(pos*2+1)` 是两套编号（一个是 CSID 的 lane 编号，一个是 CSIPHY 的使能位），这里没有错位。

### 18.128 一句 "streaming" 把嫌疑分成两半

驱动的 `__imx586_start_stream()` 成功时会打：

```
imx586 0-001a: streaming 4000x3000 RAW10 over D-PHY (4 lanes)
```

这句话走完了**上电时序、chip id、模式寄存器表、lane 模式**，也就是说：**只要它出现，传感器驱动这一侧就是跑通的**，问题必定在它之后——链路、PHY 类型、板级。反过来，它没出现，就说明驱动连 start_stream 都没走到，原因就在上面的 dmesg 里（`failed to power-on`、`chip id mismatch`、`failed to set the CSI-2 lane mode`…）。

以前这条线只在 JSON 的 `b_dmesg` 里，汇总表不显示。现在会直接打出来，和 `irq:`、`dmesg:` 并列——于是**一行汇总表就能把"驱动侧"和"链路侧"分开**：

```
tag  verdict                acks  capture  bus      3ph  rails
v3c  driver-bound-no-frame  0x1a  -        4(dphy)  yes  -
    irq:    csiphy fired but csid saw nothing - PHY/lane side, check bus-type first
    stream: streaming 4000x3000 RAW10 over D-PHY (4 lanes)
```

这个组合的读法很干净：**驱动在出数据、PHY 有中断、CSID 一个都没收到** → 不用再翻驱动，直接怀疑 `bus-type` 或 lane 物理连接。

测试 27 → **29 项**（能抽出该行、没有时保持空）。

### 18.129 "抓不到帧"有十种原因，而汇总表只印了一个 `-`

抓帧脚本的失败会带一句自己的话，比如：`no frames captured`、`rear: no csid link`、`no video node`、`no mbus code on ...`、`short raw: 12000000 < 48000000`。这些话指向**完全不同的修法**：

| 说法 | 含义 |
| --- | --- |
| `no frames captured` | 流水线组装好、也在流，但缓冲区一直没满 → **链路/PHY 侧** |
| `rear: no csid link` | media graph 里 csiphy→csid 的链路根本没连上 → **流水线组装**的问题 |
| `no video node` | 找不到 `/dev/videoN` → 绑定/拓扑问题 |
| `no mbus code on ...` | 子设备没上报格式 → 驱动/探测问题 |

而汇总表的 `capture` 列对所有这些都只印 `-`。现在补一行：

```
v3c    ...  capture  bus      3ph  rails
v3c    ...  -        4(dphy)  yes  -
    irq:     csiphy fired but csid saw nothing - PHY/lane side, check bus-type first …
    stream:  streaming 4000x3000 RAW10 over D-PHY (4 lanes)
    capture: B: no frames captured
cphy3  ...  -        1(cphy)  yes  -
    capture: B: rear: no csid link
```

这两行放在一起就是一份诊断：**v3c 是"链路建好了但收不到数据"，cphy3 是"链路根本没建起来"**——两者的下一步完全相反（一个查 bus-type/走线，一个查 media graph 为什么没连）。以前这两张候选看起来一模一样。

测试 29 → **33 项**（B/A 前缀、成功时不留、helper 没吐 JSON 时说清楚）。

### 18.130 手机回来了：四张候选跑完，后摄卡在"设不上电压"这一行

07:54:56 手机以 `USB\VID_18D1&PID_D00D\B5FCD385`（fastboot）出现，看门狗 07:55:23 接手，08:28:32 跑完四张候选。整轮 `fastboot boot`，没有写任何分区。

**结果一览**

| 候选 | 前摄 | 后摄 | 说明 |
| --- | --- | --- | --- |
| `r4ctl`（对照） | — | — | 控制镜像只是证明"临时启动 + ssh"这条链路通 |
| `v2`（原始 r4 DTB） | **a=1 b=1** | a=0 b=0 | 前摄在**原厂 camss 和我们的 camss 上都真出图** |
| `v3c`（D-PHY 4 lane） | a=0 b=0 | a=0 b=0 | 后摄 probe 失败，前摄跟着没链上 |
| `cphy3`（C-PHY 3 trio） | a=0 b=0 | a=0 b=0 | 同上 |
| `cphy2`（C-PHY 备选） | a=0 b=0 | a=0 b=0 | 同上 |

前摄那张图是真数据：`1928x1088`、`raw_bytes=2628608`、`mean=16.24`、`nonblack_ratio=1`。**这是我们第一次证明自己编的 camss 能把一台相机跑到出图**——之前所有结论都缺这块地基。

**后摄为什么没起来**（dmesg 原话）

```
imx586 21-001a: error -EINVAL: failed to set VANA voltage
imx586 21-001a: failed to power-on the sensor
imx586 21-001a: probe with driver imx586 failed with error -22
```

不是找不到器件，也不是链路锁不住——是**驱动在 probe 阶段就退了**，连一次 I2C 都没发。根因很具体：

- 驱动要精确 **2.900 V**：`#define IMX586_VANA_MIN_UV 2900000` / `MAX 2900000`；
- PM8008 LDO7 的步进是 **8 mV、从 1.504 V 起**（`pldo_ranges[] = REGULATOR_LINEAR_RANGE(1504000, 0, 237, 8000)`），格点上是 **2.896 V** 和 **2.904 V**，没有 2.900；
- `regulator_set_voltage(2900000, 2900000)` 因此返回 `-EINVAL`。

这正是 0006 那次"把 DT 钉成 2896000 反而 -EINVAL"的同一件事的另一半：**问题在请求值，不在 DT**。改成请求区间（2.800–2.904 V，两个端点都在格点上）即可。

**连带影响：前摄也被拖下水。** 三张相机 DTB 上，`imx471` 驱动其实**绑上了**（`/sys/bus/i2c/devices/24-0010/driver -> imx471`，dmesg 有 `rails up (vana,vdig,vio), img_clk = 19200000 Hz`），但它在媒体图里是 `1 pad, 0 link`——链路没建。同一棵树上另一个 sensor（imx586）probe 失败会拖住 async notifier，于是两台相机一起"没链上"。所以**修好后摄 probe 是同时恢复两台相机的关键**，不是两件事。

**跑的过程中修掉的三个问题**

1. **runner 的 ssh 会挂死**：`ConnectTimeout` 只管 TCP 连接，不管握手。手机正在重启时 sshd 可能接了连接却不发 banner，于是 `ssh` 永远等下去——07:55:45 和 08:08:46 各挂死一次（每挂一次整轮就停在那儿）。已给 `SshOk` 和 PON 调用加上 `-o ServerAliveInterval=5 -o ServerAliveCountMax=2`，另写了一个只杀 `kebab true` 探测 ssh（不碰跑几分钟的 diag 会话）的看护脚本。
2. **判据写反了**：`b_still_loaded` 是 `lsmod | grep camss/imx586 || echo none`，`none` 表示两个模块都卸载干净了——这是**想要的状态**，而代码写的是 `if "none" == b_still_loaded: return "camss-load-failed"`。于是四张候选的结论全被标成"camss 加载失败"。已改成"仍有残留才算失败"，并补了 4 项测试（全套 32 → 35）。
3. **设备上跑的是旧脚本**：runner 从 `deploy-cphy/` 推文件，我只改了工作副本没同步，所以设备上仍是改之前的判据。已同步，并在下一轮校验里加进清单。

**编译模块踩的三个坑（记下来免得重踩）**

1. `make` 不构建模块，要 `make modules`；
2. 不传 `ARCH=arm64`，`modpost` 仍会写 `aarch64` 的 vermagic，但 `.o` 是 x86_64 的——insmod 报 `Invalid architecture in ELF header: 62`（62 = EM_X86_64）；
3. 用别的树的 `.config` 编出来的模块 BTF 校验失败、`struct module` 大小也对不上（`.gnu.linkonce.this_module section size must match`）。**唯一可靠的办法是把设备的 `/proc/config.gz` 拉下来当 `.config`**——已保存为 `device-kernel.config`（各 9122 行，含 `CONFIG_VIDEO_IMX586=m`）。

### 18.131 后摄出帧了——而且答案就是 C-PHY 3 trio

`18.130` 把 probe 卡在 `-EINVAL: failed to set VANA voltage`。把驱动改成请求 **2.800–2.904 V** 区间、用**设备自己的 `/proc/config.gz`** 重编 `imx586.ko` 之后，设备上直接看到：

```
media-ctl -p | grep imx586
- entity 1: msm_csiphy0 (2 pads, 5 links)
        <- "imx586 21-001a":0 [ENABLED,IMMUTABLE]
```

**传感器绑上了**，而且**前摄的链路同时回来了**（`<- "imx471 24-0010":0 [ENABLED,IMMUTABLE]`）——18.130 里"一个 sensor probe 失败会拖住另一个"的推断得到证实。

然后手工把三级链路打开并直接抓流：

```
media-ctl -d /dev/media0 -l '"msm_csiphy0":1 -> "msm_csid0":0[1]'
media-ctl -d /dev/media0 -l '"msm_csid0":1 -> "msm_vfe0_rdi0":0[1]'
media-ctl -d /dev/media0 -l '"msm_vfe0_rdi0":1 -> "msm_vfe0_video0":0[1]'
v4l2-ctl -d /dev/video1 --set-fmt-video=width=4000,height=3000,pixelformat=pRAA \
  --stream-mmap=3 --stream-count=5 --stream-to=/tmp/rear.raw
```

结果：

```
<<<<<                      # v4l2-ctl 的五个 "一帧到达" 标记
-rw-r--r-- 1 root root 75120000 /tmp/rear.raw     # 5 帧 × 15,024,000 B
```

**这是这块板子第一次从 IMX586 真的收到数据。** 每帧 15,024,000 B = 5008 字节行宽 × 3000 行（4000×3000 RAW10，行尾 8 字节对齐填充），与 4000×3000@RAW10 完全吻合。

**链路类型：C-PHY，3 个 trio。** 运行中的设备树直接给出答案：

```
bus-type:     1        （MEDIA_BUS_TYPE_CSI2_CPHY）
data-lanes:   0 1 2    （三个 trio）
link-freq:    698395833
```

也就是说 ``cphy2`` 那张候选（C-PHY 3 trio）是对的，D-PHY 那条路（`v3c`）从一开始就选错了。整夜纠结的"本机到底是哪种 PHY"由实机回答了。

**画面是黑的，但黑得有道理。** 对抓到的一帧做完整解码（4 像素/5 字节解包，4000×3000 全量 1200 万像素）：

```
all pixels: mean 15.84  min 15  max 18  n=3000000（抽样）
most common values: 16 → 2,526,033 个；15 → 473,964 个
```

也就是几乎整幅都是 RAW10 的**黑电平**（64/1024 ≈ 16/255）。为了区分"传感器没在曝光"和"环境真的没光"，扫了一遍曝光/增益：

| 设置 | 均值 | min/max |
| --- | --- | --- |
| exposure=6（最小） | 15.842 | 15 / 16 |
| exposure=3018（最大） | 15.847 | 15 / 16 |
| gain=992（最大） | 15.643 | **10 / 25** |

曝光推到最大几乎没变化、增益把**噪声**放大了（min/max 从 15/16 变成 10/25）——这正是"传感器模拟通路在工作、但一个光子都没进来"的样子。前摄在同一台机器上此前也是 mean≈16.2。结论：**两台相机都在正常工作，手机正躺在没有光的地方（镜头朝下或暗处）**。

**还没做/待验证**：①有光环境下的真实画面（需要把手机翻过来或开灯）；②自动流程还没用上这条通路——`camera-capture-test.py` 的后摄路径卡在 `media-ctl --set-v4l2` 的 `EINVAL`（链路未使能时格式传播失败），而真正可用的是"先 `-l` 使能三级链路，再用 `v4l2-ctl --set-fmt-video` 设格式并抓流"；③C-PHY 数据率表那档 2.5 Gsps 与 0.7 Gsps 的偏差对成像质量的影响未评估。

### 18.132 自动流程也通了：`camera-capture-test.py` 的后摄路径修好

手抓通之后，把同样的顺序写进脚本——原来的实现用 `media-ctl --set-v4l2` 给每个实体设格式，那条命令在链路未使能时返回 `EINVAL`（实测：四个实体全报同一个错，但每个 subdev 单独 `VIDIOC_SUBDEV_S_FMT` 都成功）。改成"先 `media-ctl -l` 使能三级链路，再用 `v4l2-ctl --set-subdev-fmt` 逐个设格式，最后 `v4l2-ctl --stream-mmap`"之后：

```
"ok": true,
"sensor": "imx586 21-001a",  "csiphy": "msm_csiphy0",  "csid": "msm_csid0",
"vfe": "msm_vfe0_rdi0",      "video": "/dev/video1",
"width": 4000, "height": 3000, "pixfmt": "pRAA",
"raw_bytes": 45072000, "stride": 5008, "capture_rc": 0,
"mean": 15.647, "min": 10, "max": 23, "nonblack_ratio": 1.0,
"result": "partial",
"note": "capture path works but frame is dark; check lens cover or scene"
```

3 帧 = 45,072,000 B，行宽 5008 与 4000×3000 RAW10 对齐后的行宽一致。

**"黑"到什么程度**：把这一帧按 1%–99% 分位拉伸（14→18 映射到 0→255）后，画面是**纯散粒噪声**，没有任何大尺度结构——这是"有增益、没光子"的典型样子，与 18.131 里"曝光无效、增益只放大噪声"的结论互相印证。

**所以现在的状态是**：链路、PHY、CSID、VFE、DMA、格式协商全部打通，**只差环境光**。要看到真实画面只需要把手机翻过来对着亮处，然后：

```
ssh -F local/ssh/kebab-server.conf kebab \
  "sudo python3 /tmp/camera-capture-test.py --camera rear --output-dir /tmp/camera-lab --frames 3"
```

**被推翻/修正的旧认知**：①"本机可能是 D-PHY"——实机是 **C-PHY 3 trio**；②"camss 加载失败"——是判据写反（18.130）；③"格式设置失败说明驱动不支持"——是 `media-ctl` 的传播行为，不是驱动问题；④"前摄坏了"——是被后摄 probe 失败拖住的 async 链路，后摄一修好前摄同时恢复。

### 18.133 收工：手机已回到之前的镜像，工作进度全部保留

按用户要求收尾。**没有写任何分区**——整轮所有候选都只走 `fastboot boot`，所以"恢复"就是一次正常重启：内置 `boot_a` 上的 r4 镜像本来就是原样。

恢复前后对照（同一台手机）：

| 项 | 恢复前（候选运行时） | 恢复后 |
| --- | --- | --- |
| boot_id | `ca742e59-6ffc-4208-a0c9-4c3ddb8e33ad` | `d11f99f1-5dd9-40e7-b284-4d0b62e2bec5` |
| 设备树相机节点 | `cci@ac4f000/i2c-bus@0/camera@1a` 存在 | **不存在**（r4 原始 DTB） |
| `imx586` | 已加载并绑定 `21-001a` | 未加载 |
| `qcom_camss` | 自编模块 688,128 B | rootfs 原版 679,936 B |
| `imx471` | 绑定 | 绑定（正常） |
| `/tmp` 临时件 | 模块、75 MB raw、抓帧目录 | 已随重启清空 |
| 服务 | — | `kebab-venus.service` active；唯一 failed 为历史遗留 `kebab-cnb-domain.service` |

看门狗（autowatch）在重启前已停止，不会再自动把手机拉进 fastboot。

**保留的工作进度**（都在 `local/camera-r4-20261110/`，该目录被 `.gitignore` 排除，不入库）：四张候选镜像、修好的 `imx586-vana-range.ko`、设备 `/proc/config.gz` 存档 `device-kernel.config`、抓到的一帧（`frames/rear-20261111-090328.png` 与 16 倍拉伸版）、`SHA256SUMS-frames`、以及重写过的 `STATUS.md`。

**入库的**：`docs/` 四份相机文档、`patches/camera/0001…0008`、`scripts/linux/` 三个相机脚本、`config/debian13-r4-camera.config`。
