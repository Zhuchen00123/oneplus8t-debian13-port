# OnePlus 8T 传感器接入：首次探测、候选件与安全边界（2026-11-07）

## 结论

**尚未驱动出运动、方向、光线或距离传感器的实时数据。** 本轮查明了原厂传感器协处理器的访问链、原厂数据和一处用户态兼容故障，完成了一个未部署的 ARM64 用户态候选件和一个仅用于离线研究的设备树对照件。它们不是生产部署包，也没有通过实机传感器验收。

当前 r4 内核已经能启动 SLPI、创建 `/dev/fastrpc-sdsp`，并通过 QRTR 发现 SSC；不能仅凭结果断言必须升级整个 Linux 内核。若最终确需在启动时改变电源约束，可以研究用现有 7.2 内核配新的设备树及配套启动包，而不是必然重编所有内核代码。这一必要性和安全性均尚待验证。

## 本机实测与保留边界

- 设备为 KB2000，内核 `7.2.0-kebab-debian13-r4-venus+`。本轮最后一次检查时间 20:42:33，boot ID 仍为 `7065f22c-00ca-472e-b8eb-937d11c8b5b8`；SLPI、CDSP、ADSP 均显示 `running`。DSH、现有 VNC 桌面和 `qrtr-ns` 为 active、重启计数均为 0，没有 failed unit。**这只说明检查时的状态，不证明所有外围设备功能恢复或长期稳定。**
- 当前 IIO 设备只有三个 PMIC ADC 条目；它们不等于加速度计、陀螺仪或光线传感器已可用。首次 `ssccli --sensor=accelerometer --timeout=3` 未获得传感器数据，报告 registry 发现超时；之后一次查询报 `UNKNOWN`。
- 使用 Debian trixie `hexagonrpcd` 0.3.2 的解包副本，在 root 专用临时目录、只读原厂数据副本、隔离网络、设备白名单和有限资源/时间的 transient unit 中执行了一次实际 SLPI 连接。约 20:11:10 启动，20:11:13 退出；日志有 738 条针对副本中 registry 路径的 `openat` 记录，包括成功打开 `bmi26x_0_platform.config`。这表明链路到达原厂固件和部分文件，**不表明已产生或验证传感器数据**。
- 原服务请求不存在的 `sns_registry` 本地接口后出现 `Unsupported handle: 4294967295`，listener 退出。systemd 日志里的 `Deactivated successfully` 只反映该版进程返回状态；代码中 listener 故障不会正确传播为 main 的失败状态，不能当成功验收。
- 日志还显示原服务找不到原厂期待的 `sns_reg_version`、`hw_platform`、`platform_subtype`、`platform_subtype_id`、`platform_version`，并尝试以写方式打开版本文件；缺失文件和 unsupported handle 都是事实，但现阶段不能把某一项单独断定为全部根因。
- 20:31 内核日志出现两条已有类型的触摸 `-71` 和一条触摸报告 `-22`。本轮没有证据把它们归因于传感器探测或编译，也不能忽略它们。
- 本轮没有安装软件包、建立常驻服务、更改原厂 persist、刷写分区、修改正在使用的内核/设备树、重启手机、重启 remoteproc 或修改网络/充电策略。实际 FastRPC 探测曾进入 SLPI 并可能改变协处理器的瞬时内存状态；不能把它描述成纯只读检查。临时文件仍留在手机 `/tmp`，机器重启后可能消失。

## 取证：原厂资料与平台字段

所有带原厂固件、配置及校准数据的产物均在 Git 忽略的 `local/sensors-20261107/` 中，仅限本机私有使用，不应上传公开仓库。

1. 原厂 `persist.img` 的 SHA256 与既有备份清单一致；只读提取了 739 个 registry 文件。`devinfo.gsensor`/`devinfo.gyro` 指向 `bmi26x` 的校准项，但 registry 也含其他型号配置，**仅凭文件名不能确认实际安装了哪颗芯片**。版本文件的原字节 SHA256 为 `470b294d4a6c3198e486da76f8b45d4262b4b862183f980238cfa09fe8b2161e`，内容为 `version=9` 加 NUL，不能照搬其他设备上假报 `version=3` 的方案。
2. 原厂 `super.img` 的 SHA256 与既有清单一致；离线提取 vendor 传感器配置和 ODM 传感器配置，分别保留，避免同名文件混合。原配置确实引用 `/odm/etc/sensor/config`；首轮 Debian 服务没有映射它。
3. `dsp_a` 无既有备份，因此只读备份了该分区到本机。设备备份前后及电脑副本 SHA256 均为 `307648ccc47f2960d7958a74eb92ad0167cf90589f46affc1cf0cf0e9344c881`。提取出的 `fastrpc_shell_2` 与设备原分区中对应文件字节哈希一致；没有向该分区写入。
4. 原厂固件备份中的 SLPI 23 个分片/元数据与现用手机磁盘上的对应文件 SHA256 一致；这是**磁盘文件**比较，不是运行中内存镜像度量。
5. 主线 `/sys/kernel/debug/qcom_socinfo/` 实测硬件平台编号 `8`、平台版本 `65536`、子类型 `0`；`soc_id=356`、revision `2.1`。按[一加公开的同系列 Android 14 内核 socinfo 实现](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/846e71f228ad605c75b53064c4c165a1361a0bfe/drivers/soc/qcom/socinfo.c)，可推导旧路径对应 `hw_platform=MTP`、`platform_subtype=Unknown`、`platform_subtype_id=0`、`platform_version=65536`。已生成有证据的离线快照；未来使用前必须重新读取并比对，不应盲目硬编码。

## 原厂固件中的电源线索

原厂 SLPI ELF 的 `.b04/.b05` 中，离线脚本 `local/sensors-20261107/verify_slpi_rail_tables.py` 复核了以下引用链：

| 原厂资源标签 | `.b04` 描述符偏移 | `.b05` 依赖表偏移 | 表内的 mode/mV/en 资源 |
| --- | --- | --- | --- |
| `sensor_vdd` | `0x13a200` | `0x9028` | `/pm/ldoc10/*` |
| `sensor_vddio` | `0x13a248` | `0x90b8` | `/pm/ldoc8/*` |

原厂 boot 备份中的设备树也有 `ldoc8`、`ldoc10` RPMh 资源；当前主线 DTS 对应 `vreg_l8c_1p8` 和 `vreg_l10c_3p0`。观察时前者的 Linux regulator 状态为 `unknown`、配置电压 1.8V、Linux 使用计数为 0；后者显示 enabled、3.0V、一个 Linux 使用者。**这些是框架视角和设置值，不是外部测量的物理电压，也不能排除协处理器或其他主体的投票。** 固件指针表是静态线索，不证明该板当前实装器件、电源时序或故障原因。

社区另有设备报告过传感器电源处理不当造成 DSP 故障或整机重启，例如 [Galaxy A52 项目调试记录](https://github.com/miromraz/a52q-linux/blob/59eaa173feda657d6c33db282b02e9439d3aae5c/DEBUG.md)。不同手机不能直接套用，但足以说明 systemd 的超时不能保障系统不会重启。

## 离线候选件：禁止直接部署

### FastRPC 文件服务

基于 [hexagonrpc v0.3.2 的精确源码](https://github.com/linux-msm/hexagonrpc/tree/b7eff23db1b11541d11175bd3f42bd4c85214baf)，参考社区对[未支持请求的处理](https://github.com/royka1/diting-ubuntu/blob/c68ff47db94792fb5fd608e98d1ae489c2e592b1/ubuntu-diting/packages/hexagonrpc/patches/0004-hexagonrpcd-listener-Answer-unsupported-requests-wit.patch)，在隔离副本中：

- 为已观察到的 unsupported handle 返回错误而不立即停止 listener，并避免序列化不存在的输出；
- 分别映射 ODM 配置目录和原厂 registry 版本文件；仍保持文件服务只读，不伪造写入成功；
- 加入原厂实际 `registry/../sns_reg_version` 路径及 vendor/ODM 同名文件区分的离线测试。

源码归档 `local/sensors-20261107/hexagonrpc-kebab-source-v3.tar` SHA256：`f5b90834e0863689a268726de361be876ce6ab9ff65f7be54dfb27e191d567ef`。手机上以普通用户、`nice 15`、两任务并行原生编译，ARM64 的两项上游测试和两项新增测试均通过；x86_64 WSL 上对应新增测试在 ASan/UBSan 下通过。由于上游 root builder 没有析构实现，ASan 测试关闭了泄漏检查，故**不代表通过了泄漏审计或完整安全审计**。原版 listener 的符号链接问题和首次 `--no-rebuild` 缺测试样本导致的失败均已定位，修正后重建重跑。

候选程序和测试日志归档 `local/sensors-20261107/hexagonrpc-kebab-arm64-v3-NOT-FOR-DEPLOY.tar`，手机和电脑 SHA256 均为 `f1425ea82672133aee1471459f10f4d155304184f1fadec7d2354d70c07719f4`。程序本体 SHA256 `75f039e40efba3cf444a23d1baae776a60cf63c4bdfdff2d9e212336cf4b9dfd`。它尚未连接实际 DSP、未覆盖所有厂商 RPC 方法或写需求，不能声称软件绝对无害，也不能作为正式服务启动。

含原厂私有文件及经过证据推导的平台字段的离线副本 `sensor-payload-v2-NOT-FOR-DEPLOY.tar` SHA256 `96b95092334f69dd1f39f30d2d0d2e2f8c6e0b7f8efafb3b2766d3dace5fe66f`，仅存本机；这不是自动部署包。早期探测使用的 `sensor-payload.tar` 没有版本文件或完整平台字段，不能把它当作 v2。

### 设备树假设

从既有 r4 封存启动包解出的设备树 SHA256 `216c2fa1f6771ae4541a091390dca9ec2a255960c9d56d66f85cbd4c52c7dd7d`，与 r4 artifact 及其清单一致。在其离线副本中加入 `regulator-always-on` 到 `ldo8`；反编译文本对照只有这一条新增属性。

候选 `local/sensors-20261107/r4-ldoc8-hypothesis-NOT-FOR-BOOT.dtb` SHA256 `69eb7b7210bc9dfa1ad9adb8c1280cac5e71eef83fa935933506bb4a1ce93fb4`。**它不是已验证修复，也不是启动镜像；不能直接启动、刷写或在运行系统热加载。** 始终供电对功耗、供电时序、共享资源和 DSP 投票的影响均未实测。

## 下一步验收门槛

1. 需要单独约定可中断窗口、现场物理接触/恢复方式和生产服务容忍度；先复核 boot 分区备份、供电、存储和保护服务。即便只运行限时用户态服务，错误的 DSP/电源状态也可能导致整机重启。
2. 在不修改原厂校准数据的前提下，复核候选服务 RPC 协议、平台字段、权限/路径和电源竞争；确认是否还需要 ODM、版本文件以外的原厂接口，避免伪造成功的写操作。当前候选程序尚不能解决所有已知请求。
3. 按从低风险到高风险的顺序，先考虑现有内核上的有界、受监测用户态试验，再决定是否真的需要新的启动设备树。任何内核启动或分区写入需另行明确批准；不得用自动恢复脚本擅自重启关键生产服务。
4. 成功标准是先枚举真实传感器，再连续读取合理数值并与人工移动/遮挡对照，同时观察温度、电源、协处理器与生产服务；失败则保留证据，不将空值或虚拟数值当成功。
5. Linux 侧可用后再研究 DSH agent 的权限隔离、`libssc`/D-Bus 接口；Android 容器还需要单独的传感器 HAL/桥接与应用内验证，**Linux 读到数值不自动意味着 Waydroid 或原厂 Android 已获得真实传感器**。

## 后续（2026-11-07 第二轮）

第二轮只读复核、日志证据回收、代码审计与离线诊断候选 v4 见[第二轮记录](sensor-bringup-r2-20261107.md)。要点：20:11 那次实机探测跑的是**包内原版 0.3.2**（`f0442014…`）而不是 v3 候选；DSP 请求的本地接口名被日志直接点名（`sns_registry`）；原厂 ODM 配置以 `hw_platform`/`soc_id` 门控，本机实测值在其白名单内；原厂 DT 同样没有让 Linux 使能 `ldoc8`，**现有 7.2 内核与设备树暂不需要改动**。v4 仍为诊断件，未部署、未上机。
