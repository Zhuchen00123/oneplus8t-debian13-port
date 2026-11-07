# 传感器接入第二轮：状态核对、证据回收、代码审计与离线候选 v4（2026-11-07）

## 结论

**仍然没有在 Linux 侧读到真实传感器数据，本轮没有做任何实机 DSP 试验。**

本轮完成的是：设备与产物状态的只读核对、上阶段探测日志的证据回收（含一处关键事实修正）、候选服务的代码/权限/厂商 RPC 审计、原厂平台字段与电源/总线归属的证据核对，以及一份**未部署**的离线诊断候选 v4（x86_64 与 aarch64 均编译并通过全部四项离线测试）。

关键结论有四条：

1. 上阶段 20:11 的实机探测**跑的是 Debian 包里的原版 hexagonrpcd**（`f0442014…`），不是 v3 候选（`75f039e4…`）。所以"listener 退出"是上游 0.3.2 的既定行为，v3 从未上过机，不能说 v3 失败。
2. 真正缺的厂商 RPC 行为已经被日志直接点名：DSP 请求本地接口 **`sns_registry`**，daemon 只实现了 remotectl 与 apps_std，于是返回 -5；随后到达 handle=`0xFFFFFFFF` 的请求，上游在这一步直接终止整个 listener。
3. 原厂 ODM 配置 `kona_bmi26x_0.json` 用 `hw_platform` 与 `soc_id` **门控**是否加载，本机实测值（`hardware_platform=8 → "MTP"`、`soc_id=356`）都在这份配置的白名单里；而 20:11 那次没有映射 `/odm`，这份配置根本没被提供。
4. **现有 7.2 内核与设备树不需要改。**原厂 DT 也没有让 Linux 去使能 `ldoc8`（无消费者、无 always-on），SLPI 固件自带 `ldoc8/ldoc10` 供电表；总线 se3 在原厂与主线都保持 disabled，无 AP 争用。`r4-ldoc8-hypothesis-NOT-FOR-BOOT.dtb` 继续标为不可启动，不进入本轮。

## 一、只读状态核对（2026-11-07 20:55 CST）

| 项目 | 实测值 |
| --- | --- |
| 内核 | `Linux 7.2.0-kebab-debian13-r4-venus+ aarch64` |
| boot ID | `7065f22c-00ca-472e-b8eb-937d11c8b5b8`（与上阶段记录相同，期间未重启） |
| 运行时间 | 169,392 s（约 1 天 23 小时） |
| 内存 | 11,613 MiB 总，可用 9,544 MiB；无 swap |
| 根分区 | `/dev/sda15` 6.9G，已用 87%，**剩 911 MiB** |
| /srv | `/dev/sda28` 220G，已用 10% |
| 电池 | `bq27541-0` 88%，`Not charging`，8.336 V，0 µA，35.1 °C |
| USB 输入 | `tcpm-source-psy` online=1，5.000 V |
| 温度 | 最高 41 °C（pm8150l/pm8150/cpu 各点） |
| 失败单元 | 无；关键单元 `kebab-dsh`、`kebab-venus`、`qrtr-ns` 均 active、NRestarts=0 |
| IIO | 仅 3 个 PMIC ADC（`iio:device0..2`），没有运动/光线设备 |
| remoteproc | `slpi`/`cdsp`/`adsp` 均 running；`/dev/fastrpc-sdsp` 存在 |
| pstore | 空 |

电源两个关键稳压器与上阶段一致：`vreg_l8c_1p8`（ldoc8）为 `state=unknown / num_users=0 / 1.8 V`，`vreg_l10c_3p0`（ldoc10）为 `state=enabled / num_users=1 / 3.0 V`。**这仍只是 Linux 框架视角，不是物理电压测量。**

分区与备份（只读读数，均与既有记录一致）：

| 对象 | SHA-256 |
| --- | --- |
| `boot_a`（现用，r4） | `4838d489afb25402c64c439b35d58701ec4dacecb879825ce3187790b7cc2ac5` |
| `boot_b` | `36c58b409e4602fc29481a01eef3d929109669f97dc1753288a2038303b535b3` |
| `dtbo_a` | `99af1264aa5f1a2bdcff12b4e4d5ec5ff0a04c86b88a207bdcd28429b79b21b0` |
| `dtbo_b` | `af59ee907f5e2c0d3546f87c3e56b0383c9d3c4b4e81c986e86598ae1a107519` |
| r3 完整 boot 备份 `/srv/kebab-r4-deploy-20261105/backup/boot_a.img` | `4fab2d9152aa78b41b884e913afb53c9605b5bb5e2ecb821f7e72d20d2603dfa` |
| `dsp_a` 备份（本机私有） | `307648ccc47f2960d7958a74eb92ad0167cf90589f46affc1cf0cf0e9344c881` |

`dtbo_a` 现值为社区空 DTBO，不是 2026-10-03 原厂清单里的 `3e4a4aad…`：这是更早轮次按既有授权做的 DTBO 替换，r4 的 `deployment.json` 明确记录本次部署 `dtbo_written=false`、部署前后均为 `99af1264…`。原厂 DTBO 恢复副本仍在备份中。

上阶段产物哈希在本机与设备两侧复算，全部一致：源码 `f5b90834…`、ARM64 包 `f1425ea8…`、私有载荷 v2 `96b95092…`、设备树对照 `69eb7b72…`、候选二进制 `75f039e4…`。

## 二、证据回收与一处事实修正

上阶段的实机日志此前没有归档。本轮从设备 journal 里把 `kebab-ssc-probe-20261107-a` 单元完整取回，存为私有 `local/sensors-20261107/evidence/probe-a-unit-20261107.log`（**3,921 行 / 468,470 B / SHA-256 `e24b1abc9e4af3c4ef2ed0206e1600a0156d1d1d3053270ac2f7ae4fafe1aa1e`**）。第一次归档误用了 PowerShell 默认的 UTF-16 输出，已作废重取，只保留 UTF-8 这一份。

从这份日志可以直接读出的事实：

- **实际运行的是原版程序**：`/tmp/kebab-ssc-root-20261107-a/tool/usr/libexec/hexagonrpc/hexagonrpcd` = `f04420144febb3372328e7a7e22dce3d83bae420dab15711d60d480290a155d9`（2025-02-10 的 Debian 包内文件），与 v3 候选 `75f039e4…` 不同。
- **载荷是 v1 加现场 socinfo**：`sensor-payload.tar` 解包后 `sensors/` 里没有 `sns_reg_version`，另外现场从 `/sys/devices/soc0/` 复制了 `family/machine/revision/soc_id` 四个文件。
- 统计（逐条 grep）：`hw_platform` 失败 10 次、`platform_subtype` 10 次、`platform_subtype_id` 10 次、`platform_version` 10 次；`sns_reg_version` 先 stat 失败 10 次、再以写方式打开 10 次；`Could not open` 合计 52 条；读过 740 个不同的 registry 路径。
- **`Could not find local interface sns_registry` 出现 1 次**（日志第 3830 行，与缓冲刷出的 stdout 交错在同一行，逐字节核对为 `... interface sns_registry`）。
- `Unsupported handle: 4294967295` 出现 1 次，随后单元 `Deactivated successfully`。

**日志可读性警告**：该程序把 `HEXAGONRPC_VERBOSE` 的文件操作轨迹写到 stdout（管道→全缓冲），错误信息写 stderr（无缓冲）。stdout 的整块刷出会让同一行里出现交错文本（如 `ak0991x_0_platform.ori` + `Could not find local interface sns_registry` + `ent`），且刷出的行时间戳完全相同。**因此跨 stdout/stderr 的先后顺序不可靠**，只能按单条 stderr 消息的语义判断。

## 三、候选服务审计（v3）

v3 相对上游 `b7eff23`（hexagonrpc 0.3.2）的语义改动只有三处文件加两个新测试：`hexagonrpcd/listener.c`、`hexagonrpcd/rpcd_builder.c`、`tests/meson.build`，其余源文件逐字节相同。注意工作区的上游副本是 CRLF 检出，v3 归档沿用了这些字节，新加的测试是 LF；不一致只影响 diff 可读性，不影响编译。

已经做对的：

- 写操作一律拒绝且不伪造成功：`apps_std_fopen_with_env()` 对 `"w"`/`"a"` 直接返回 `AEE_EUNSUPPORTED`，日志里的 10 次 `Tried to open ... for writing` 就是这条路径。
- 新增的 `/odm/etc/sensor/config` 映射与 `persist/sensors/sns_reg_version` 映射结构正确，`hfs_mkdir` 的子节点计数与实参一致（根 7 个、registry 2 个）。
- 未知 handle 的新分支里 `*returned = NULL` 后再让 reply 用零 scalar，避免了用已释放指针序列化输出。

审计发现的问题（本轮已在 v4 处理，见第五节）：

1. **只救了一条路径**。上游对 `sc & 0xff`（请求自带 handle）、`method` 越界、`impl == NULL`、缓冲区个数不符、输入尺寸不符仍是 `return 1` 直接退出。任何一条都可能像 20:11 那样把 listener 打死。
2. **退出状态被丢弃**。`start_reverse_tunnel()` 忽略 `run_fastrpc_listener()` 的返回值，`main` 恒返回 0，所以 systemd 的 `Deactivated successfully` 不含信息量（上阶段文档已指出，但没有修）。
3. **没有协议侧观察能力**。未知请求只打印一个 handle 数字，方法号、buffer 数、缓冲区内容全部丢失——这正是这次要命的地方：日志差点只留下"某个 handle 不认识"，而 `sns_registry` 这个名字是靠 stdout 交错才侥幸保住的。
4. 只读映射是硬约束：`sns_registry` 本地接口、以及对 `sns_reg_version` 的写入需求，当前代码结构上都无法满足。

顺带记录两条**上游既有**缺陷，不是本轮引入、也不在本轮修：`apps_std_stat()` 把 `ctime` 取成 `st_ctim.tv_nsec`；`iobuffer.c:215` 的 32 位写入在非对齐地址上属于 UB（上游自己的 misaligned 测试在 x86 UBSan 下会报）。aarch64 普通内存允许非对齐访问，本轮不据此改动上游代码。

## 四、平台字段、电源与总线归属

**平台字段（全部现场复读，不硬编码）**：`/sys/devices/soc0` 在主线只有 `family/machine/revision/soc_id`；`/sys/kernel/debug/qcom_socinfo/` 给出 `hardware_platform=8`、`hardware_platform_subtype=0`、`platform_version=65536`。按[一加公开的同系列 Android 14 内核 socinfo 实现](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/846e71f228ad605c75b53064c4c165a1361a0bfe/drivers/soc/qcom/socinfo.c)，`HW_PLATFORM_MTP = 8 → "MTP"`，`hw_platform_subtype[0] = "Unknown"`。本轮重新核对了这两张表，与载荷 v2 里的推导值一致。

**控制点**：原厂 ODM 配置 `kona_bmi26x_0.json` 顶部有
```json
"config": { "hw_platform": ["QRD","MTP","Dragon","Surf","HDK","IDP"], "soc_id": ["356"] }
```
本机 `soc_id=356`、`hw_platform=MTP`，都在白名单内。同一文件下半部分才是真正的硬件参数：`bus_type=1`、`bus_instance=3`、`slave_config=0`、`max_bus_speed_khz=9600`、`dri_irq_num=123`、`irq_trigger_type=3`、`num_rail=2`、`vdd_rail="/pmic/client/sensor_vdd"`、`vddio_rail="/pmic/client/sensor_vddio"`。20:11 那次没有 `/odm` 映射，这份文件根本没被服务到。

**电源**：原厂 DTS 里 `rpmh-regulator-ldoc8` / `ldoc10` 只有 `qcom,set=3`、`init-voltage`，**没有 `regulator-always-on`，也没有任何设备把它们当 `supply` 引用**；主线 DTS 同样是"无消费者"，唯一的例外是 `ldoc10` 被三段键（`oneplus,kebab-alert-slider`）占用。也就是说"给 ldoc8 加 always-on"并不是恢复原厂行为，而是新增一条原厂没有的约束；SLPI ELF 自带 `sensor_vdd→/pm/ldoc10`、`sensor_vddio→/pm/ldoc8` 的供电表，更像是 DSP 自己投票。**结论：不改内核、不改设备树，`ldoc8` 对照组继续不具备启动价值。**

**总线**：ODM 配置里的 `bus_instance=3` 对应 `i2c@98c000`（se3），该节点在原厂 DTS 与主线 DTS 里都是 `status="disabled"`，AP 侧没有争用，无需改动。

**仍然缺的东西**：DSP 还请求过 `/mnt/vendor/persist/engineermode/cali_coe`（失败 1 次）。该路径在原厂 persist 分区确实存在（`/engineermode/` 下还有 `red/green/blue/white_max_lux`、`als_bias`、`sensor/` 等光线校准项），但当前虚拟文件系统只暴露 `persist/sensors/`，所以这些校准数据一律不可见。这是后续要单独处理的一项（需要新的映射 + 私有副本），本轮**没有**把它塞进候选件。

## 五、离线修正与验证（v4，未部署）

v4 只做"让诊断跑得下去、并且留下证据"，不实现任何厂商协议：

1. 所有"服务不了"的路径（自带 handle、未知 handle、方法越界、空实现、缓冲区个数/尺寸不符）改成**回一个错误并继续**，并统一把 `*returned` 置空。
2. 新增有界诊断日志：前 16 条未服务请求打印 `handle / sc / method / in / out` 和前两个输入缓冲区的 48 字节可打印预览；累计 64 条后**先答完最后一条再停**，避免无限循环。
3. 把 listener 的退出状态传回 `main`：`3` = 触到诊断上限，`1` = FastRPC 传输/初始化失败，`0` = 正常结束。systemd 里再也不会一律显示成功。

验证：

| 项目 | 结果 |
| --- | --- |
| 源码归档 `hexagonrpc-kebab-source-v4-NOT-FOR-DEPLOY.tar` | 205,312 B，SHA-256 `0ee4ae02bf0127297323fefd08f049b63c2aa0492ffc43545b4b94d0e32c8c48` |
| x86_64 meson 构建 | 4/4 测试通过 |
| x86_64 ASan+UBSan | `kebab_listener`、`kebab_paths` 通过（`iobuffer` 的上游非对齐用例仍报 UB，属既有问题） |
| aarch64 本机编译（`nice 15`，普通用户，/tmp） | 通过，耗时约 4 秒，构建前后 load 0.13→0.17 |
| aarch64 四项测试 | `iobuffer`/`hexagonfs`/`kebab_listener`/`kebab_paths` 全部 PASS |
| ARM64 包 `hexagonrpc-kebab-arm64-v4-NOT-FOR-DEPLOY.tar` | 593,920 B，SHA-256 `12e8c3cbee16c45671fd54f13a3691dc533d36ed32135e67926c28131e9cc5bf` |
| 二进制 `hexagonrpcd` | 174,816 B，SHA-256 `94fc29ef43b2ce965b56ee56ffce23961618aa5ef6a85824f1acb68bbd94591d` |

私有载荷补成 v3：在 v2 基础上补 `socinfo/machine`（`f3ef3e83…`）与 `socinfo/family`（`8c537061…`），两者都由现场 `/sys/devices/soc0/` 原字节生成，与实机哈希一致；`sensor-payload-v3-NOT-FOR-DEPLOY.tar` = 2,918,400 B / 859 文件 / SHA-256 `df99dc2cc5ff8051ae07efbd29484fc9a294e9b995cc7eec62c410a953d89cd5`。

**v4 仍然是诊断件，不是部署包**：它没有实现 `sns_registry`，也没有实现可写映射；"回一个错误能不能让 DSP 满意"完全未验证。

## 六、风险与未验证项

- 即便加了超时，错误的 DSP 状态仍可能触发协处理器故障或整机重启，DSH 等服务会中断；社区已有其它机型因此重启的记录。
- 未验证：DSP 侧 stub 能否接受"0 个输出缓冲 + EUNSUPPORTED"的回复；`sns_registry` 接口的方法号与数据结构（当前未知）；`platform_subtype/platform_version` 的文本格式是否被正确解析（值有证据，格式是推断）；`sns_reg_version` 只读是否足以让服务跳过迁移。
- 只读映射无法满足写入需求，若服务强依赖写入，探针仍会失败——这需要新的可写私有副本设计，不能碰原厂 persist。

## 七、下一步（需要明确批准）

1. **本轮请求**：批准一次范围最小、限时、可监控的用户态试验——v4 诊断件 + 载荷 v3，仍在 root 专用隔离目录、`RuntimeMaxSec`、`PrivateNetwork`、设备白名单的 transient unit 里跑，出问题立即停止并保存证据。目标是拿到 `sns_registry` 的请求细节（handle/method/缓冲区），而不是拿到传感器数据。
2. 若日志确认 `sns_registry` 协议形状，再离线实现只读版本；写入需求要单独设计私有可写副本。
3. `engineermode` 校准项（`cali_coe`、`*_max_lux`、`als_bias`）作为第二项独立改动，单独评审。
4. 只有实机证据显示供电不足时，才重新讨论 `ldoc8` 设备树候选；在此之前不制作、不启动。

## 八、本轮没有做的事

未重启、未切内核、未启动任何候选镜像、未刷写任何分区、未修改原厂 persist/校准数据、未重启 remoteproc、未改动网络与充电策略、未安装常驻服务。唯一在设备上执行的写操作是在 `/tmp` 下解包与编译 v4（普通用户、`nice 15`、约 4 秒），构建后五业务单元仍 active、无 failed。
