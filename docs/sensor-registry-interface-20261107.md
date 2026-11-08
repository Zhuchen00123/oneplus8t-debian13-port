# 厂商 `sns_registry` 接口：从原厂镜像里还原出的真实协议（2026-11-07）

## 结论

第二轮日志里那个 `Could not find local interface sns_registry` 不是"未知的注册表协议"，而是**一个只有单个方法的属性查询接口**。协议从原厂镜像里直接还原出来了，不靠猜：

- 设备侧（SLPI 固件）在 IDL 描述表里写着 `sns_registry` / `get_property` / `prop_name` / `prop_value`；
- 应用侧（vendor 分区）的 `libsns_registry_skel.so` 只有三个导出符号，函数体就是 `property_get()` + `strlcpy()`；
- 反汇编得到的分拣逻辑把报文的字段顺序、长度校验和返回码全部定死。

据此实现了 `hexagonrpcd` 的第三个本地接口（v5，离线通过全部测试，**未部署、未上机**）。

## 一、证据来源（全部只读、离线）

| 对象 | 大小 | SHA-256 |
| --- | --- | --- |
| `stock-vendor/vendor_a.img`（EROFS，原厂 vendor 分区备份） | 922.98 MB | `19219fe988bc175e1fb1df25e26c2ac2e31f225bb894a1ff04ca178e181c399e` |
| `lib64/libsns_registry_skel.so`（arm64，从镜像解出） | 15,376 B | `70ee444d5149b26c2f7f07b1003f079228c7b6e07664d3a0f898869d3754b523` |
| `lib/libsns_registry_skel.so`（arm 32 位） | 4,444 B | `e3158bbad4c43f1d061aed8aca7286cb61d2ae73acf2389631c3ffcfa841ae9a` |
| SLPI 固件拼接镜像（`slpi.mdt` + `slpi.b00..b21`，顺序拼接） | 8,088,952 B | `5fb3cba146c9ecf9f3c98ba39c09d27c5ce4cfceb9a1b96c8521ee8b6b5555ca` |

解包用的是项目里已有的 `erofs-tool`（erofs-utils 1.9.4），缺的 `libdeflate.so.0` 从 Debian pool 取单包解到本地目录、用 `LD_LIBRARY_PATH` 临时加载，**没有改动系统**。vendor 镜像解出 1.4 GB，耗时 43 秒。

## 二、设备侧：固件里的字面证据

SLPI 镜像里的字符串（拼接镜像偏移 `0x68b7c4` 起）：

```
sns_registry_parser.c: File write error %i
get_property
prop_value
prop_name
sns_registry
```

这四个串紧挨在一起，正是 Qualcomm IDL 生成的**接口描述表**：接口名 `sns_registry`，一个方法 `get_property`，两个参数 `prop_name`（入）、`prop_value`（出）。同一镜像里 `get_property`/`prop_name`/`prop_value` **各只出现一次**，不存在第二套同名协议。调用它的模块是 `sns_registry_parser.c`——也就是负责解析传感器配置/注册表的那个解析器，它在需要 AP 侧取值时走这条路。

同一镜像里还能读到与注册表相关的其它事实（先前已知，本轮顺带确认）：`sns_registry_sensor.c`、`sns_registry_parser.c`、`SNS_REG_INIT`、`Config dir '%s' not available`、`Registry dir '%s' not available`、`/mnt/vendor/persist/engineermode/cali_coe`、`sns_reg_config`。也就是说**注册表本身的读写是 DSP 自己做的**（走 `apps_std` 文件接口），`sns_registry` 这个远程接口只负责“问 AP 要一个值”。

## 三、应用侧：`libsns_registry_skel.so` 的全部内容

导出符号只有三个：

| 符号 | 说明 |
| --- | --- |
| `sns_registry_skel_invoke` | 分拣入口（由 FastRPC 监听端调用） |
| `sns_registry_get_property` | 唯一的方法实现 |
| `sns_registry_slim` | 静态链接模式的描述表 |

导入表里只有 `property_get`（libcutils）、`strlcpy`、`memset`、`__android_log_print`。方法体（`0x1038`）翻译过来就是：

```c
int sns_registry_get_property(const char *prop_name, char *buffer, int size)
{
        char value[92];

        if (buffer == NULL) { log("invalid buffer to place requested property(%s) value", prop_name); return -1; }
        if (size <= 91)     { log("requested property(%s) cannot fitin by size:%d", prop_name, size); return -1; }

        memset(buffer, 0, size);
        property_get(prop_name, value, "");
        strlcpy(buffer, value, size);
        log("prop_name/prop_value : ('%s')/('%s')", prop_name, buffer);
        return 0;
}
```

`property_get(name, value, "")`——**默认值是空串**：Android 上取不到的属性就是空串，且函数成功返回。这一点很重要，它决定了我们这边"未知属性"的正确行为。

## 四、分拣逻辑还原出的报文格式

`sns_registry_skel_invoke`（`0x2064`）的判定顺序，逐条对应到协议：

| 指令 | 含义 |
| --- | --- |
| `tst w0,#0x1f000000; b.eq` → 否则 `mov w0,#20` | method 必须为 0，否则返回 `AEE_EUNSUPPORTED`(20) |
| 统计 `inbufs+outbufs+noIn+noOut` < 3 → `mov w0,#14` | 缓冲区总数至少 3，否则 `AEE_EBADPARM`(14) |
| `ldr x9,[x1,#8]; cmp #8; b.lo` | `in[0].s` 至少 8 字节 |
| `ldr w10,[x9]` | `name_len = in[0][0]` |
| `ldr w11,[x1,#0x18]` | `name_buf_size = in[1].s` |
| `cmp w10,w11; w10 != 0; in[1].p[w10-1]==0` | 名字必须在声明的长度内以 NUL 结尾 |
| `x8 = &bufs[inbufs]` → 第一个输出缓冲 | 出参紧跟所有入参 |
| `ldr w2,[x9,#4]` / `cmp w2,out[0].s` | `value_size = in[0][1]`，必须 ≤ `out[0].s` |
| `b sns_registry_get_property` | `(in[1].p, out[0].p, value_size)` |

于是同一份报文在 hexagonrpc 侧的约定是：

```
method            0
in[0]  u32[4]     { name_len, value_size, name_buffer_size, value_buffer_size }
in[1]  char[]     prop_name（NUL 结尾，长度 = name_len）
out[0] char[]     prop_value（>= 92 字节，NUL 结尾）
```

`in[0]` 那 16 字节不是随便定的：上游 `check_inbuf_sizes()` 要求它就是 `4 × (in_nums + in_bufs + out_bufs)` 的大小表，`in_nums=2 / in_bufs=1 / out_bufs=1` 正好 16 字节；`allocate_outbufs()` 也从同一张表取输出缓冲大小。设备侧 skel 只校验 `>= 8`，与"表里前两个 u32 是数字参数、后面是缓冲区大小"完全相容。

## 五、v5 实现（离线，未部署）

新增 `hexagonrpcd/sns_registry.c`，注册为本地接口第 2 号（`n_ifaces` 2→3）：

- 名字、方法数、缓冲区数量与上面还原的协议一致；
- 重复做设备侧同样的校验（NUL 结尾、`value_size >= 92`、输出缓冲足够），并且**额外拒绝非法属性名**（只允许 `[A-Za-z0-9_.\-@:]`）——因为名字会拼进路径，这是安全边界而不是业务判断；
- 取值顺序：`<device_dir>/properties/<name>` 有这个文件就读它（去掉行尾换行），没有就返回**空串并成功返回**，即 Android `property_get(name, value, "")` 对"未设置属性"的语义。**不编造任何值**；
- 每次查询都打一行日志：`sns-registry: get_property '<name>' -> '<value>'( unknown property, returning empty value)`——即使这次试验没拿到传感器数据，也能知道 DSP 到底问了哪些属性。

没有把它做成"能查到真值"的样子：Linux 上没有 Android 属性，空串就是正确答案；要给出具体值必须显式放一个文件，可审计。

### 测试

新增 `tests/test_sns_registry.c`（7 项）：命中文件、未知属性返回空串、名字未 NUL 结尾、`value_size=91` 拒绝、路径穿越名字拒绝、超长值截断到 91 字节 + 终止符、行尾换行剥离。

| 平台 | 结果 |
| --- | --- |
| x86_64 meson（含新测试） | 5/5 通过 |
| x86_64 ASan+UBSan | `sns_registry` / `kebab_listener` / `kebab_paths` 3/3 通过 |
| aarch64 设备内编译（`nice 15`，3 秒） | 5/5 通过 |

| 产物 | 大小 | SHA-256 |
| --- | --- | --- |
| `hexagonrpc-kebab-source-v5-NOT-FOR-DEPLOY.tar` | 219,136 B | `445dfcf556ab9fc2c5b751bde73bcb122a04949a7b29aea876b87cab33484c76` |
| `hexagonrpc-kebab-arm64-v5-NOT-FOR-DEPLOY.tar` | 686,080 B | `b03725c061d36483aa516f06c699e77fe18e5972fdffebcc7a33e3394b1af425` |
| └ `hexagonrpcd`（aarch64） | 182,320 B | `ee2ff935e53359e587f4bf891fefe16ba58b87e224f457dc387def71385ba77d` |

## 六、仍然未知的

1. **DSP 会问哪些属性**：镜像里只有 `persist.vendor.` 这一个字面前缀，名字是运行时拼的。但原厂 `sns_reg.conf` 直接点名了两个（见第七节第 2 小节），实机日志可以用来证伪。
2. **大小表的实际长度**：设备侧 skel 只要求 `in[0].s >= 8`，而我们的校验要求恰好 16。如果实际固件发的是 8 字节，daemon 会打印 `Invalid number of input numbers: 8 (expected 16)` 并回 `AEE_EBADPARM`——**这条日志就是下一个分叉点**，不需要再猜。
3. **`get_property` 之后还会要什么**：`remote_handle_open` 只失败过一次，但上游 daemon 还有别的未实现路径（v4 已全部改成"回错误并继续"）。下一个未服务请求会带着 handle/method/缓冲区预览进日志。
4. **这条接口与"读不到真实传感器数据"的关系**：它只是配置期的一次属性查询，**不是**传感器数据通道；通过它不代表传感器能用。

## 七、上机前的静态预检（第三轮）

探针还没跑，但两件事可以先离线查清，免得上了机才发现载荷有洞。

### 1. 上一轮请求的 745 条路径，载荷 v3 全部覆盖

把 20:11 那份 3,921 行日志里的 `openat`/`stat`/`opendir` 参数全抽出来（**745 条不同路径**），按 `construct_root_dir()` 的真实映射逐条解析到载荷目录：

| 检查项 | 结果 |
| --- | --- |
| 虚拟树无法解析的路径 | **0** |
| 能解析但载荷里不存在的文件 | **0** |
| 前缀分布 | `/mnt/vendor/persist` 740、`/vendor/etc/sensors` 3、`/sys/devices/soc0` 2 |

也就是说那轮**没有一次失败是载荷缺文件**造成的（失败是接口不存在）。`/vendor/etc/acdbdata` 在树里有映射但从未被请求，载荷里也没有 `acdb/`，属于已知不影响当前流程的空缺。`/odm/etc/sensor/config` → 载荷 `sensors/odm-config/`（17 个文件，**含 `kona_bmi26x_0.json`**），第二轮"缺 `/odm` 映射导致 bmi26x 配置没被服务"的缺口在 v3 里已补上。

### 2. `sns_reg.conf` 就是注册表的契约，它点名了 DSP 会问哪两个属性

原厂 `/vendor/etc/sensors/sns_reg_config`（588 B）全文：

```
version=9
file=hw_platform=/sys/devices/soc0/hw_platform
file=platform_subtype=/sys/devices/soc0/platform_subtype
file=platform_subtype_id=/sys/devices/soc0/platform_subtype_id
file=platform_version=/sys/devices/soc0/platform_version
file=soc_id=/sys/devices/soc0/soc_id
file=revision=/sys/devices/soc0/revision
file=output=/mnt/vendor/persist/sensors/registry/registry
file=config=/odm/etc/sensor/config
property=persist.vendor.sensors.enable.property=/mnt/vendor/persist/sensors/registry/file1
property=persist.vendor.sensors.enable.property1=/mnt/vendor/persist/sensors/registry/file2
```

由此确定三件事：

1. 注册表的 6 个动态字段全部取自 `/sys/devices/soc0/*`，载荷里都有且与现场 sysfs 同哈希——平台门控走的就是这条路。
2. `file=config=/odm/etc/sensor/config` 证实 ODM 配置目录是注册表自己去找的，该映射必须存在。
3. **两条 `property=` 点名了会被请求的属性**：`persist.vendor.sensors.enable.property` 与 `…property1`，取值落到注册表目录下的 `file1`/`file2`。不是猜的，是原厂配置自己写的。v5 对任何属性都返回空串并成功（Android `property_get` 对未设置的语义），所以预期这两条得到空值、不会生成 `file1`/`file2`。

**这是探针的第一个可证伪预言**：实机日志里应出现至少两行
`sns-registry: get_property 'persist.vendor.sensors.enable.property' -> ''`（及 `…property1`）。若出现别的名字，说明属性名在运行时还有别的来源；若一行都没有，说明 DSP 没走到属性查询。

### 3. 注册表写入确实是 `fwrite`，而且会重试

固件 `0x68b85c`–`0x68ba34` 的字符串把 `sns_registry_osa.c` 的文件操作列全了：`fopen failed file=%s error=%i, retrying` / `opendir …retrying` / `mkdir …retrying` / `rename …retrying` / `stat` / `closedir` / `unlink …retrying` / `fwrite failed error=%i` / `fread` / `readdir`。

所以 20:11 那 **10 次 `Tried to open … for writing`** 不是 10 个不同写请求，而是**同一个逻辑写在重试**。整段没有"写失败就放弃初始化"的迹象——DSP 在第 10 次失败后照常去开 `sns_registry` 句柄，说明**写失败当前是被容忍的**：它影响的是版本号/校准能否落盘，不是服务能否起来。这也是当前不急着实现写支持的理由。

同时 `set_property` 在整个 SLPI 镜像里**零命中**，再次确认 `sns_registry` 只有 `get_property` 一个方法。

### 4. 顺带记下、本轮不处理

`0x68bd54` 起是 `sns_remote_proc_state_sensor.c` 的字符串：`sleepstate` / `Remote_proc_state` / `sns_sig_remote_proc_state` / `SMP2P registration failed with error:%d`——传感器服务里有个"远端处理器状态"传感器要注册 SMP2P。属后续独立课题，本轮只记录。

## 八、AP 侧接口全集：只有 `sns_registry` 一个是我们必须补的（第四轮）

上一轮之后还剩一个隐忧：探针会不会跑两步又要一个没实现的接口？这一轮把原厂镜像里**所有**"本地接口实现"翻了一遍，答案是**不会再有别的**。

### 怎么判定的

FastRPC 两个方向的命名容易混，先用一个可判定的特征把它们分开：**AP 侧实现（skel）**自己分拣 method 并直接干活（`sns_registry_skel` 就是调 `property_get`）；**AP 侧客户端（stub）**则是懒打开远端句柄再转发。反汇编 `libsns_device_mode_stub.so` 的 `sns_device_mode_skel_invoke`（`0x11d4`）正好是后者：

```
w22 = handle                      ; 全局 [0x4000+0x2e0]，初始 -1
if (handle == -1) remote_handle_open("sns_device_mode", &handle)
...
remote_handle_invoke(handle, sc, args)   ; 尾部 blr，转发给 DSP
```

所以它名字里虽有 `_skel_invoke`，实际是"**去调 DSP 的 skel**"，方向是 AP → DSP。

### 结果

| 库 | 大小 | 判定 |
| --- | --- | --- |
| `libsns_registry_skel.so` | 15,376 B | **AP 侧实现**（唯一的传感器专用 skeleton）→ 必须由我们提供 |
| `libsns_device_mode_stub.so` | 15,680 B | AP 侧**客户端**，DSP 提供 `sns_device_mode`（单方法 `set`） |
| `libsns_low_lat_stream_stub.so` | 16,456 B | AP 侧**客户端**，DSP 提供低延迟流（`config/init/poll/stop/…` 7 个方法） |
| `libsns_fastRPC_util.so` | 15,376 B | **不是接口**：只是帮别人打开 `sensorspd` 远端句柄的工具（`createstaticpd:sensorspd`、`/sys/kernel/boot_slpi`），导出 `sns_rpc_get_handle`/`sns_rpc_reset` |
| `libsnsdiaglog.so` | 28,768 B | **不是接口**：C++ diag 日志（`sensors_diag_log::*`），无远程方法 |

全镜像里含 `*_skel_invoke` 的库共 23 个，其余（`libadsprpc/libcdsprpc/libmdsprpc/libsdsprpc` 的通用 `%s_skel_invoke`、相机/音频/SNPE 等）都与传感器无关。

**结论**：传感器 PD 需要的 AP 侧接口 = `apps_std`（文件）+ `adsp_default_listener`（启动）+ `remotectl`（句柄）+ **`sns_registry`**（属性）。前三个上游已有，第四个是 v5 补的——**这一类"缺接口"的未知项到此清零**。

### 附带发现：AP 侧的传感器客户端通道本来就是通的

设备上现在就能查到：

```
Service Version Instance Node  Port
    400       1        0    9    13  Snapdragon Sensor Core service
```

也就是说 **SSC 的 QMI 服务已经通过 QRTR 注册在 node 9（SLPI）的 13 端口**，而 AP 侧访问它用的是 `AF_QIPCRTR` 套接字（`qrtr-ns` 在跑、`qrtr-lookup` 能列出），**完全不经过 hexagonrpcd**。这解释了整条链的形态：

- `hexagonrpcd` 负责的是**让 SSC 把配置/注册表读进来**（反向隧道：DSP → AP 要文件）；
- **取数据**是另一条路：AP → DSP，走 QRTR QMI（Android 上是 `libssc`/`sensors.ssc.so`，Linux 上是 libssc 那一类库）。

两件事互不阻塞。等 SSC 拿到配置后，客户端侧不需要改内核、不需要新设备节点。另外确认现有 3 个 IIO 设备是 `spmi adc@3100`（PMIC ADC），**不是**运动传感器，所以设备上目前确实没有任何可用的加速度/陀螺/光照源。

## 九、客户端那条路到底有多远（第五轮）

第四轮发现 SSC 的 QMI 服务已经在 QRTR 上广播，当时我把它当成"更便宜的起步"，并提议先发一个只读的 QMI 版本查询。**这一轮查清后发现那个判断不成立**，如实更正。

### 客户端协议长什么样

AOSP 的 CHRE 里有现成实现（`platform/slpi/see/sns_qmi_client.c`，Apache-2.0），它把整条链讲得很清楚：

| 层 | 内容 |
| --- | --- |
| QMI 服务 | `SNS_CLIENT_SVC`（service 400，instance 0） |
| 请求 | `SNS_CLIENT_REQ_V01`，结构 `sns_client_req_msg_v01 { payload[], payload_len }` |
| 响应 | `sns_client_resp_msg_v01 { client_id, result }` |
| 上报 | `sns_client_report_ind_msg_v01 { client_id, payload[], payload_len }` |
| 载荷 | **protobuf 编码的 `sns_client_request_msg`**（上报是 `sns_client_event_msg`） |

即：QMI 只是信封，真正的语义全在 protobuf 里；而且 protobuf 的字段号**没有**以 `FileDescriptorProto` 的形式存在 AP 侧库里（见第十五节——字段号其实能从序列化函数里读出来）。固件里倒是有 nanopb 的字段表符号（`sns_client_request_msg_fields`、`sns_suid_req_fields`、`sns_client_event_msg_sns_client_event_fields`），但那是 Hexagon 侧的数据结构，没有符号表就拿不到地址。

**结论：往 SSC 发一条有语义的请求，需要先把 QMI 的 TLV 编号和 protobuf 的字段号都还原出来**——这比"发个版本查询"重得多。所以我不再建议把它当作探针之前的低成本预演；它应该是**SSC 起来之后**的独立工作流（更现实的做法是构建 libssc，而不是自己重写协议）。

### 顺带修正一个更早的担心

之前担心 `sns_registry` 的"大小表"可能是 8 字节而不是 16 字节。把 `listener.c` 的两处代码连起来看，16 是**结构上必须**的：

- `check_inbuf_sizes()` 要求 `in[0].s == 4*(in_nums + in_bufs + out_bufs)`；
- `allocate_outbufs()` 从同一张表取**输出缓冲的大小**（`sizes[in_bufs + i]`）来 malloc；
- 设备侧 skel 校验 `value_size <= out[0].s`。

如果表只有 8 字节，daemon 就拿不到输出缓冲大小，`value_size <= 0` 会让所有请求失败。所以"8 字节"那种情况本身跑不通，**16 是唯一自洽的取值**（仍是推断，实机日志的 `Invalid number of input numbers` 是最终判据，但概率已经很低）。

## 十、执行与判读（一页版，第六轮）

### 前置核对（全部已离线验证，2026-11-07）

| 对象 | SHA-256 | 大小 |
| --- | --- | --- |
| `hexagonrpc-kebab-source-v5-NOT-FOR-DEPLOY.tar` | `445dfcf5…c76` | 219,136 B |
| `hexagonrpc-kebab-arm64-v5-NOT-FOR-DEPLOY.tar` | `b03725c0…425` | 686,080 B |
| └ 包内 `hexagonrpcd` | `ee2ff935…77d` | 182,320 B |
| `sensor-payload-v3-NOT-FOR-DEPLOY.tar` | `df99dc2c…cd5` | 2,918,400 B |

已核对：三个 tar 的哈希与磁盘一致；源码包**逐文件等于当前工作树**（`diff -r` 空）；源码包里确实有 `hexagonrpcd/sns_registry.c` 与 `tests/test_sns_registry.c`；包内 `MANIFEST.txt` 记录了协议契约与全部产物哈希；探针脚本里的三个 `EXPECT_` 与上表一致。

### 命令

```bash
cd local/sensors-20261107/probe
bash run-probe.sh --plan                       # 只打印，不碰设备
KEBAB_PROBE_CONFIRM=1 bash run-probe.sh --run 90   # 真正执行
```

### 判读表

| 日志/现象 | 含义 | 下一步 |
| --- | --- | --- |
| `sns-registry: get_property 'persist.vendor.sensors.enable.property' -> ''` | 新接口被命中，且属性名与 `sns_reg.conf` 预言一致 | 协议这一段通了，看后面还要什么 |
| 出现**别的**属性名 | 属性名还有别的来源（运行时拼接） | 把名字记下来，加进载荷 `properties/` |
| 一条 `sns-registry` 都没有 | DSP 没走到属性查询（更早失败） | 看 `Could not find local interface` / 退出码 |
| `Could not find local interface <X>` | 还有个没实现的本地接口 | 把 `<X>` 记下来；按第四轮方法去镜像里找 `lib<X>_skel.so` |
| `Unsupported method: N (sc)` | 已知接口上的未知方法 | N 就是方法号（例如注册表写入） |
| `Invalid number of input numbers: 8 (expected 16)` | 大小表实际是 8 字节（第九节推断被推翻） | 改 `check_inbuf_sizes`+`allocate_outbufs` 支持短表 |
| 退出码 3 | 触到 64 条未服务上限，答完最后一条再停（不是崩溃） | 数一数日志里的 `kebab-unserved` 类型分布 |
| 退出码 1 | FastRPC 传输/初始化失败 | 看 unit 日志前 20 行 |
| `ssc` 从 `present` 变 `ABSENT` | 传感器服务在试验期间消失（可能重启） | 立刻停止后续试验，保留证据 |
| `failed` 变大 / remoteproc 不是 running | 系统级异常 | 立刻停止，物理检查设备 |

### 回退

没有需要回退的东西：unit 是 `systemd-run` 的 transient 一次性单元，结束即消失；所有文件都在 `/tmp/kebab-ssc-probe-<时间戳>/`（0700），重启即清；没有写入任何分区、没有改 persist/校准、没有动内核与设备树。

## 十二、首轮实机试验（2026-11-07 23:06 起，用户已批准）

### 跑了什么

用户批准后，按第十节的计划执行。第一次因为**运行器自身的传输层**跑不起来（不是设备问题）：

- WSL 里 `ssh -b 192.168.1.195` 报 `Address family for hostname not supported`，`kebab` 别名解析到 `fc00::9c5` / `198.18.10.6` 都连不上；
- 根因：**设备在 USB 子网 `192.168.8.1`，只有 Windows 侧的 OpenSSH 有那条路由和密钥**；WSL 的 `192.168.1.195` 是 Windows 自己的地址，连过去是 Windows 的 sshd。
- 修法：运行器改为可覆盖的传输层（`KEBAB_SSH`/`KEBAB_SCP`/`KEBAB_SCP_BIND_OPT`/`KEBAB_WIN_PATHS`），在 WSL 里借用 `/mnt/c/Windows/System32/OpenSSH/ssh.exe`。另发现 **Windows 的 `scp.exe` 不支持 `-b`**，必须写成 `-o BindAddress=...`。

第 0 步先在设备上验证了沙箱参数（`DevicePolicy=closed` + `DeviceAllow=/dev/fastrpc-sdsp rw` 下 `open` 成功、100 ms 退出），确认不会因为沙箱导致假失败。

### 结果：daemon 起来了，DSP 一句话没说

| 轮次 | 时长 | 结果 |
| --- | --- | --- |
| 第一轮 | 90 s | 单元跑到 RuntimeMaxSec 被停，**日志只有 3 行 systemd 记录** |
| 第二轮 | 300 s | 同上，但换成 `stdbuf -oL -eL` 后拿到 daemon 自己的第一行 |
| 第三轮 | 1200 s（20 分钟） | 同样的 `Starting ... (INIT_ATTACH_SNS)` 一行，**全程 0 条 DSP 请求** |

第二轮的关键一行：

```
stdbuf[...]: Starting /tmp/kebab-ssc-probe-.../tool/hexagonrpcd (INIT_ATTACH_SNS) on /dev/fastrpc-sdsp
```

**这条说明 daemon 成功 attach 了**，并且此后 5 分钟**没有收到任何 DSP 请求**（`sns-registry` 0 行、`apps_std` 0 行、任何 `Could not open` 0 行）。

### 第一轮"全静默"是运行器的 bug，不是设备的问题

daemon 的 `Starting ...` 走 **stdout**，而 systemd 下 stdout 是管道 ⇒ 全缓冲 4 KB；单元被 `RuntimeMaxSec` 杀掉时缓冲区**没有 flush**，所以整轮输出全丢。用 `stdbuf -oL -eL` 包装后立刻可见。**这也是为什么第一轮看起来"什么都没发生"——它其实跑满了 90 秒。**

### 与 20:11 那次的关键对比

同一台机器、同一个 boot（`7065f22c…`，SLPI 自 11-05 21:52 起就没重启过）：

| | 20:11（上一阶段） | 今天 |
| --- | --- | --- |
| 二进制 | 包内**原版** `usr/libexec/hexagonrpc/hexagonrpcd` | 我们的 v5 |
| attach | 成功（同一行 `INIT_ATTACH_SNS`） | 成功 |
| DSP 请求 | **attach 后 1.13 秒开始**，1.6 秒内 1,489 条 | **0 条**（5 分钟） |

⇒ "监听器一出现 DSP 就会重试"这个假设**被今天的实验否掉了**；20:11 那次的请求更可能是 **SSC 自己的一次性初始化尝试**（或周期很长的重试）恰好落在那个窗口里。20:11 那次 daemon 在 3 秒后退出，初始化被打断，此后 SSC 不再敲门。

### 结论：SSC 不会再自己重试

20 分钟里一条请求都没有（`daemon_lines=0`，只有启动那一行）。结合 boot 时间线（SLPI 自 11-05 21:52 起没重启过）：

- 20:11 那次请求**不是**"监听器一出现就会重试"——否则今天 attach 成功后应当立刻重放；
- 更合理的解释：那是 **SSC 的一次性初始化尝试**（或间隔极长的重试）恰好落在 20:11 的 3 秒窗口里；daemon 在 1.6 秒后被打断，之后 SSC 就放弃了；
- 既然 5 分钟、20 分钟都不再敲，**继续等待没有意义**。要让 SSC 重新初始化，必须让**传感器子系统自己重新启动一次**。

这已经超出"在现有系统上做用户态试验"的范围，属于需要**单独明确批准**的操作，所以本轮到此为止。

### 没有造成影响

三轮试验期间与结束后：`slpi/cdsp/adsp` 全部 `running`、`failed` 单元 0、`qrtr-lookup` 里 `400 Snapdragon Sensor Core service` 一直在、电池 88%/Not charging、负载 0.1–0.3、温度无明显变化。没有写分区、没动 persist、没重启任何 remoteproc。

## 十二补、客户端协议：字段号已经从原厂库里取出来了（第八轮）

上一节说"字段号拿不到"，这一轮找到了办法，**收回那句话的一半**。

`libsnsapi.so` 虽然是原厂 AOSP-30 的产物，但**没有 strip**：`.dynsym` 里 3,273 个 `g DF .text` 符号，包含每个 protobuf 类的 `SerializeWithCachedSizes`。而 protobuf 的序列化函数对每个字段都是"先 `mov w0, #<字段号>`，再 `bl WireFormatLite::Write<类型>`"，于是**字段号 + 线格式**可以直接读出来：

| 消息 | 字段（号:线格式） | 说明 |
| --- | --- | --- |
| `sns_client_request_msg` | 1:Message, 2:Fixed32, 3:Message, 4:Message | 请求信封（suid / msg_id / request / suspend_config） |
| `sns_client_event_msg` | 1:Message, 2:Message | 上报信封（events / error） |
| `sns_client_event_msg_sns_client_event` | 1:Fixed32, 2:Fixed64, 3:Bytes | 单条事件（msg_id / timestamp / payload） |
| `sns_suid_req` | 1:String, 2:Bool, 3:Bool | **列出传感器**（data_type / register_updates / active） |
| `sns_suid_event` | 1:String, 2:Message | 返回一个 SUID |
| `sns_suid_sensor` | 1:Fixed64, 2:Fixed64 | SUID 本体 |
| `sns_std_request` | 1:Message, 2:Bytes, 3:Bool | 配置请求 |
| `sns_std_sensor_config` | 1:Float | 采样率 |
| `sns_std_sensor_event` | 1:Float, 2:Enum | 数据 / 状态 |
| `sns_registry_read_req` | 1:String | 注册表读请求 |

**证据边界要说清**：字段**号与线格式是反汇编读出来的**（硬证据）；字段**名字**是依据 SSC 一贯布局补的（库里的描述符块被剥掉了，`sample_rate`/`register_updates` 这些字符串一个都搜不到），**属推断**。

复现方式：取 `_ZNK<len><消息名>24SerializeWithCachedSizes` 的符号地址与长度，反汇编后配对 `mov w0, #N` 与随后那条 `bl ...WireFormatLite..Write<Type>`。

这意味着"往 SSC 发一条列出传感器的请求"在**协议层面**已经不再是未知数；剩下的未知只有 QMI 信封的 TLV 编号（`sns_client_req_msg_v01` 的 payload/payload_len 编号）。

### QMI 信封：IDL 对象找到了，但只解到一半

`libsnsapi.so` 导出 `SNS_CLIENT_SVC_qmi_idl_service_object_v01`（`.data`，0x170b18，0x48 字节）。读出来第一层是确定的：

```
+0x00  version=6
+0x08  service_id = 400        ← 与 qrtr-lookup 里那条 400 完全对上
+0x0c  max_msg_len = 0xf240
+0x18/+0x20/+0x28/+0x30 → .rodata 里的 IDL 表
```

顺着指针看下去，表里能辨认出四个消息号 `0x0020 / 0x0021 / 0x0022 / 0x0023`，与 CHRE 的 `REQ / RESP / REPORT_IND` 对应（QMI 惯例也是这个编号）。但**再往下的 TLV 字段表是 libqmi 的私有紧凑编码**，我没有把格式完全解出来，所以 `payload` 与 `payload_len` 的 TLV 编号仍然是未知——这一条留作后续，不影响当前主线。

## 十四、实机：重启后 DSP 第一次主动要东西，然后卡在"写"上（第八轮下半段）

用户批准路线 B（装开机自启 unit + 重启整机）。unit `kebab-ssc-listener.service` 装好后重启，新 boot `1419ceed…`，10:52:48 监听器在开机时起来，**DSP 第一次主动发请求**：

```
10:52:48  Starting … (INIT_ATTACH_SNS) on /dev/fastrpc-sdsp
10:52:48  Could not open /mnt/vendor/persist/engineermode/cali_coe: No such file or directory
10:52:48  Could not open oemconfig.so: No such file or directory
10:52:49  Tried to open /mnt/vendor/persist/sensors/registry/registry/../temp.json for writing
          × 3,355 次 / 75 秒（峰值 ~2,900 次/分，退避到 ~660 次/分），之后停止
```

**这推翻第三轮"写失败被容忍"的结论**——那个结论是从 20:11 那次 1.6 秒就退出的日志里读出来的，只看到重试的开头。现在看到完整形态：**DSP 会为同一个写打开重试上千次**，期间完全不往下走（`sns_registry` 请求 0 条）。所以写支持不是可选项，是必经之路。

### r6：给 apps_std 加写支持

上游 hexagonfs 是**只读设计**（源码注释 "Virtual read-only filesystem"），`apps_std_fopen_with_env` 对 `w`/`a` 直接返回 `AEE_EUNSUPPORTED`。r6 的改法：

1. **路径归一 + 白名单**：先把设备路径做词法归一（折叠 `//`、消 `.`、解 `..`、越界即拒），再匹配白名单前缀（`/mnt/vendor/persist/sensors/registry/` 与 `/persist/sensors/registry/`），只允许**单层文件名**。DSP 那条 `registry/registry/../temp.json` 归一到 `…/sensors/registry/temp.json`，落到 `<payload>/sensors/temp.json`——**仍在私有副本里，不碰真 persist**。
2. **可写句柄**：句柄号从 `4096` 起（hexagonfs 的 fd 只在 0..255），单独一张表；`fclose`/`fseek` 先判号段再分流。
3. **`fwrite` 按方法号 5 实现**：上游 `.def` 里 5 号正好是空位（1/2/3/4 之后、9 之前），签名照 `fread` 对称猜成 `in[0]={fd,buf_size}`、`in[1]`=数据、`out[0]={written}`。**这是推断，不是确认**——如果号或布局不对，daemon 会打 `Invalid number of input numbers` 或 DSP 收到错误。

x86_64 `gcc -fsyntax-only` 通过；设备内 `nice -n 15` 直接 gcc 构建成功（`2196f4f5…`，79,632 B；设备上没有 meson/ninja）。已部署到 `/var/lib/kebab-ssc-listener/bin/`，旧 v5 留作 `hexagonrpcd.v5.bak`（`ee2ff935…`）。

### 现在卡在：需要再重启一次才能验证

重启单元后 DSP **没有任何新请求**——它那 3,000 多次重试已经耗尽，不会因为重新 attach 而重来（这一点和"attach 触发初始化"的假设一致：init 只在开机后一次）。所以要验证 r6 只能再重启一次整机。

## 十三、下一步：需要一个"让传感器子系统重新初始化"的决定

两条路，都需要在现有批准之外再单独点头：

### A. 就地重启 SLPI remoteproc（快，但有真实风险）

监听器先挂上，然后：

```bash
echo stop  | sudo tee /sys/class/remoteproc/remoteproc0/state
echo start | sudo tee /sys/class/remoteproc/remoteproc0/state
```

- **好处**：只要几秒；SSC 重新初始化时监听器已经在，正好接住那一串 `apps_std`/`sns_registry` 请求。
- **风险**：mainline 上 SLPI 的 stop/start 没有经过验证。停不掉会在 10 秒超时后报错；起来了也可能 glink/fastrpc 通道没重建，**最坏情况需要整机重启才能恢复**。SLPI 只承载传感器 PD，不承载 Wi-Fi（MPSS）与音频（ADSP），所以影响面限于传感器相关。

### B. 装一个开机自启的临时 unit，然后重启整机（慢，但最贴近原厂时序）

- **好处**：和 Android 上 `sscrpcd` 的时序一致——监听器在 SSC 初始化之前就已经在；一次成功率高。
- **代价**：要写一个 `/etc/systemd/system/` 下的 unit（设备改动）+ 一次整机重启，回退需要显式删除。
- 重启会不会丢当前会话：DSH/酒馆/NapCat/三档/限充都是 enabled 的服务，重启后自动回来，但**当前 SSH 会话与正在跑的任务会中断**。

### C. 先停在这里

把 v5 与载荷留着，等你有大块时间（比如晚上）再做——因为 A 有把设备弄到必须重启的可能，最好人在旁边。

**我的建议**：如果你现在能接受"最坏重启一次"，选 **B**（时序最可靠、且重启是你熟悉的操作）；如果想快，选 **A**，但请先确认你能物理接触设备。

### 两条路线的脚本已经写好并离线验证过（2026-11-07 第七轮）

批准之后不用再临时写，直接跑对应脚本即可。两个都在 `local/sensors-20261107/probe/`：

| 脚本 | 作用 | 确认开关 | 不确认时 |
| --- | --- | --- | --- |
| `opt-a-bounce-slpi.sh` | 挂监听器 → stop/start SLPI → 盯 120 秒 → 收日志 | `KEBAB_BOUNCE_CONFIRM=1` | 只打印计划（`--plan`） |
| `opt-b-boot-listener.sh` | 上传候选件到 `/var/lib/kebab-ssc-listener` → 写 `/etc/systemd/system/kebab-ssc-listener.service` → `enable` | `KEBAB_INSTALL_CONFIRM=1` | 只打印计划 |

`opt-b` 的 unit 用同一套沙箱参数（`PrivateNetwork`、`DevicePolicy=closed`、只放行 `/dev/fastrpc-sdsp`、`MemoryMax=256M`、`CPUQuota=50%`），`Restart=always`，`ExecStart` 包了 `stdbuf -oL -eL`（第三轮那个"日志被缓冲吞掉"的坑）。卸载：`opt-b-boot-listener.sh --uninstall`（停服务、禁自启、删 unit 与 `/var/lib` 目录）。

离线验证发现并修掉两个真 bug：

1. `opt-b` 原先把 unit 文件内容写成 `UNIT_CONTENT='$(printf ...)'`，单引号让 `$(...)` 根本不会展开——**模拟时才发现设备收到的是那段字面文本而不是 unit 内容**；改成 quoted heredoc。
2. 载荷 tar 顶层是 `payload/`，原来 `-C $DEST/payload` 会解成 `payload/payload/...`；改成 `-C $DEST`。

模拟方式：用假的 `ssh`/`scp` 把远端脚本原样回显，逐个检查展开结果（第九轮那套办法）。（v5 + 私有载荷 v3）：目的只是让 DSP 走过 `sns_registry` 这一步并记录后续请求，不是取数据。风险与门槛与第二轮记录相同：可能触发协处理器故障或整机重启，需要明确批准与现场条件确认。**本轮没有做任何实机 DSP 操作。**
