# camera 补丁集（r4-camera / IMX586 + C-PHY）

## 0001 不要重复应用（重要）

`0001-media-i2c-add-imx586-kebab.patch` 是**历史补丁**：它是从 r4 基线到 `65305469b`（"r4 + IMX586 驱动"）的那一段 diff，而 `65305469b` **已经包含**这些改动。所以：

- `git apply 0001` 会失败在 `drivers/media/i2c/imx586.c: already exists`——**这是正常的，不是坏补丁**；
- `verify-patches.sh` 因此从 **0002** 开始应用；
- 驱动源码的可复现性用另一条判据保证：`git log 65305469b..HEAD -- drivers/media/i2c/imx586.c` 为**空**，即工作树里的 `imx586.c` 与基线提交里的一模一样（实测两边都是 `7ba35a09d5b60342…`）。

## 验证状态（先读这段）

| 范围 | 状态 |
| --- | --- |
| 0001–0005 能否干净重放 | **已验证**：在 r4 树的干净副本上按序应用，`cmp` 全部 dirty 文件与原树一致 |
| DTB 与源码树是否一致 | **已验证**：候选镜像里内嵌的 DTB 与 `make dtbs` 产物逐字节相同（`c42f8555…`） |
| boot header id | **已验证**：五张候选的 SHA1 都按 dtb_size/dtb_addr 重算过 |
| 相机链路是否真的能出图 | **完全未验证**——没有任何一条补丁在设备上跑通过 |

00:22 试机的 `boot-r4-camera.img` 用的是**已弃用的第一版**（`imx586=y` + 钉死 L7 + 一个没有出处的 `regulator-cam-rear-vana`），它失联了，不能用来判断本补丁集的好坏。当前候选与执行顺序见 `local/camera-r4-20261110/STATUS.md`。

按顺序应用在 r4 内核树上（base `71f4068cb254730bcf334cde9bdd6ca183e4fcd0`，我们实际是从 `65305469b` 这个"r4 + IMX586 驱动"提交继续的）：

| 文件 | 内容 | SHA-256 |
| --- | --- | --- |
| `0001-media-i2c-add-imx586-kebab.patch` | 新增 `drivers/media/i2c/imx586.c`（源自 Sr-0w/hotdog-linux-bringup 的 GPL-2.0 驱动，保留原作者署名；改为 endpoint 决定 C-PHY/D-PHY、kebab 的供电与 GPIO、4000×3000 RAW10 单模式） | `0be736a86012d4c1861acf8b67fe0f9d25a53373d6c334e7e569d831bae39a3a` |
| `0002-media-camss-cphy-sm8250.patch` | CAMSS 的 C-PHY 支持：上游 v8 系列 9 个补丁 + Qualcomm C-PHY 系列 6 个补丁 + 本机补的 SM8250 三相数据（3 个提交） | `e272b6b41a65a43def907616b200dba448033d560e8ff033dbe434d6cfa8e40e` |
| `0003-arm64-dts-kebab-rear-camera.patch` | `sm8250-oneplus-kebab.dts` 的后摄节点：cci0/i2c0 400 kHz、csiphy0 port@0、供电/复位/MCLK、C-PHY 3 trio 端点 | `6c11847f2b4544f4f50be29dc6c4f6e28fda6002feb64d39d83513a981ee2241` |
| `0004-arm64-dts-kebab-rear-camera-pinctrl.patch` | 给后摄三根控制引脚（78 复位、139 CAM_CUSTOM1、117 CAM_VDIG）补 pinctrl 状态，与厂商 overlay 和 hotdog 社区端口一致 | `5337b53b04a51e69c1fed8303ab0dceaf70fce1134920e9335f7a6168a993af1` |
| `0005-arm64-dts-kebab-cam-vdig-comment.patch` | 只改注释：`cam_vdig` 的 1,100,000 µV / pm8008_l1 属于 cam1，后摄在厂商 overlay 里是 `pm8150a_s8` @ 1,350,000 µV | `9220278d86413fdd2eeab2841eb841525ce6d84fc70676ac3666b57c03cb3f40` |
| `0007-arm64-dts-kebab-camera-comment-fixes.patch` | 只改注释：后摄节点头部原本还写着 D-PHY 四 lane 与 tlmm145/tlmm26 那套旧读数，改成 C-PHY 三 trio + tlmm78/139/117，并注明 trio 数仍待真机抓帧确认 | `361a53bbd2fd842b699f821d4cddf853f3de3c374dafe4493433a9ad013dc3de` |

## 0002 的来源

**上游 v8 系列（David Heidelberg，2026-06-10，9 个补丁）**，全部保留原 commit message 与作者：

```
cdf4a71d2 media: qcom: camss: csiphy: Introduce PHY configuration
f8b8969f4 media: qcom: camss: csiphy-3ph: Fix lane mask calculation
db74a8118 media: qcom: camss: csiphy-3ph: Use odd bits for configuring C-PHY lanes
dd0554b18 media: qcom: camss: Prepare CSID for C-PHY support
671889847 media: qcom: camss: Initialize lanes after lane configuration is available
08f807e54 media: qcom: camss: csiphy-3ph: Add Gen2 v1.1 MIPI CSI-2 C-PHY init
948b5b599 media: qcom: camss: csiphy-3ph: Update Gen2 v1.1 MIPI CSI-2 C-PHY init
e524dd371 media: qcom: camss: Account for C-PHY when calculating link frequency
c15f42e68 media: qcom: camss: Enable C-PHY where available
```

**Qualcomm C-PHY 系列（Anusha Arun Nandi，2026-07-17，6 个补丁）**：

```
612ad46d7 media: qcom: camss: Program CSIPHY common control registers
ee055ea11 media: qcom: camss: Prepare CSID for C-PHY support in gen3
78ba57143 media: qcom: camss: Add sa8775p C-PHY 3ph lane config
3f950ceb4 media: qcom: camss: Add sa8300 C-PHY 3ph lane config
7ea780cb2 media: qcom: camss: Dynamic data-rate specific C-PHY register settings
8595a52cd media: qcom: camss: validate local/remote endpoint bus-type
```

**本机补的 SM8250 部分（3 个提交）**：

```
0db7616bc media: qcom: camss: add the SM8250 three-trio C-PHY lane table
cae0d72e6 media: qcom: camss: program the SM8250 CSIPHY v1.2.1 3-phase common registers
acfeabecb media: qcom: camss: match the vendor 3ph data-rate delay flags
```

前两个按厂商 `cam_csiphy_1_2_1_hwreg.h` 逐条照抄：99 行 `csiphy_3ph_v1_2_1_reg` 三 trio 基表、`csiphy_common_reg_1_2_1[]` 里的三相公共值（CTRL7=0x52、CTRL0=0x03→0x0e）；第三个把 3.5/4.5 Gsps 两组的 `delay_us` 对齐厂商（只有最后一笔置 1）。三者都不含推测值。

## 0003 的关键点

- 传感器端点：`bus-type = <MEDIA_BUS_TYPE_CSI2_CPHY>`、`data-lanes = <0 1 2>`、`link-frequencies = <0 698395833>`；
- CSIPHY 端点（camss port@0）：同样 `bus-type` C-PHY、`data-lanes = <0 1 2>`，**不写 clock-lanes**（C-PHY 时钟内嵌）；
- 控制引脚按本机原厂 DTBO：`reset-gpios = <&tlmm 78>`、`vana-enable-gpios = <&tlmm 139>`、`custom1-gpios = <&tlmm 117>`；MCLK0 = tlmm 94、19.2 MHz；
- 供电：`vana = <&pm8008_l7>`、`vdig = <&pm8008_l1>`、`vio = <&pm8008_l4>`；
- `&cci0` 里补了和 CCI1 相同的 37.5 MHz `CAM_CC_CCI_0_CLK` 赋值。

D-PHY 变体不在这份补丁里（它把端点换成 `bus-type = <MEDIA_BUS_TYPE_CSI2_DPHY>`、`data-lanes = <0 1 2 3>` + `clock-lanes = <7>`，属临时试机用的另一个 DTB）。

## 0004 的关键点

0003 里后摄节点只声明了 `pinctrl-0 = <&cam_mclk0_default>`，复位 / CUSTOM1 / VDIG 三根引脚**没有任何 pinctrl 状态**：pinctrl-msm 的 `gpio_set_direction` 只改 OE 位、不动功能复用位，所以这些引脚能不能真的被驱动，取决于 bootloader 留下的复用值。

- hotdog（同一颗 IMX586 模组、社区验证可用）对 mclk / vana / custom1 / custom2 / reset **每根都声明了状态**；
- 本机厂商 overlay 对这三根的定义完全一致：`function = "gpio"; bias-disable; drive-strength = <2>;`（active 与 suspend 两组都一样）；
- 0004 按同样参数补了三个状态并挂到 `pinctrl-0`，不引入任何推测值。

这条改动只影响 DTB（内核 `Image` 不变），对应候选镜像 `boot-r4-camera-cphy3.img`。

## 0005 的关键点

纯注释修正，不影响 DTB（改完重编，`sm8250-oneplus-kebab.dtb` 仍是 `c42f8555…`）。

厂商 overlay 里 `qcom,cam-sensor@0`（后摄 IMX586）的实际映射是 `cam_vio = pm8008_l4`、`cam_vana = pm8008_l7`、`cam_vdig = pm8150a_s8`（rgltr 1,350,000 µV）；`pm8008_l1` + 1,100,000 µV 属于 `qcom,cam-sensor@1`，是**另一颗相机**。原注释把两者混在一起了。

主line 没有 `pm8150a` 这颗 PMIC 的模型，它的 SMPS8 类轨道（`vreg_s8c_1p3` 等）本来就给别的消费者常开，而 CAM_VDIG（tlmm 117）门控的是这条轨道到模组的通路，所以我们这里写 pm8008_l1 只起到"给驱动一个可 enable 的 supply 句柄"的作用。真实电压必须由诊断脚本在设备上读出来。

## 编号说明：0006 空缺，0007 是注释修正

0006 是**被撤回**的那条 VANA 钉死改动（见下节），文件已删。为了不让后来的人误以为 0006 又回来了，这个编号**不再复用**；后来的注释修正用 0007。

0007 只改注释，**DTB 逐字节不变**（重编后仍是 `c42f8555…`），所以已经做好的候选镜像不需要重新打包。

## 已撤回：0006（把 cam_vana 钉成 2,896,000 µV）

这条改动试过又撤回了，文件已删除，**不要在后续重建中使用**。原因：

- `imx586_power_on()` 第一步就是 `regulator_set_voltage(vana, 2900000, 2900000)`；
- 把 DT 约束钉成 `min == max == 2896000` 后，核心的 `regulator_check_voltage()` 会发现请求的 `min_uV (2900000) > constraints->max_uV (2896000)`，直接返回 `-EINVAL`；
- 于是 `power_on` 失败、根本进不了 streaming——**这是自己给自己制造的"没有帧"**。

原来的 1.5–3.4 V 区间反而能让这个请求通过（映射到 `2904000`），所以撤回后回到区间是正确状态。

## 0008-media-camss-3ph-clamp-rate-index.patch

`csiphy_cphy_data_rate_config()` 选速率表时，如果链路比表里所有条目都快，循环退出时 `idx == num_settings`，随后 `settings[idx]` **越界读**。原注释写的是"否则取最高一档"，代码没做这件事。补一行 clamp 到最高档。

- 影响面：只有 `link_freq` > 表内最高（SM8250 C-PHY 表最高 4.5 Gsps）才会走到；本机 imx586 是 0.698 Gsps，**永不触发**，所以它与候选镜像的行为无关，属于把移植代码修到正确；
- 已验证：`camss-csiphy-3ph-1-0.o` 用本机树 + venus 配置实际编译通过（`CC ... camss-csiphy-3ph-1-0.o`），补丁能干净地 apply 到上一提交；
- 未验证：没有跑过真机（本机永远走不到这条路径）。
