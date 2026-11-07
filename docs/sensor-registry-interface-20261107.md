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

1. **DSP 会问哪些属性**：名字是运行时拼出来的（镜像里只有 `persist.vendor.` 这一个字面前缀），只有实机跑一次才能知道。日志会原样记下来。
2. **大小表的实际长度**：设备侧 skel 只要求 `in[0].s >= 8`，而我们的校验要求恰好 16。如果实际固件发的是 8 字节，daemon 会打印 `Invalid number of input numbers: 8 (expected 16)` 并回 `AEE_EBADPARM`——**这条日志就是下一个分叉点**，不需要再猜。
3. **`get_property` 之后还会要什么**：`remote_handle_open` 只失败过一次，但上游 daemon 还有别的未实现路径（v4 已全部改成"回错误并继续"）。下一个未服务请求会带着 handle/method/缓冲区预览进日志。
4. **这条接口与"读不到真实传感器数据"的关系**：它只是配置期的一次属性查询，**不是**传感器数据通道；通过它不代表传感器能用。

## 七、下一步

仍然需要一次**最小、限时、可监控**的实机用户态试验（v5 + 私有载荷 v3）：目的只是让 DSP 走过 `sns_registry` 这一步并记录后续请求，不是取数据。风险与门槛与第二轮记录相同：可能触发协处理器故障或整机重启，需要明确批准与现场条件确认。**本轮没有做任何实机 DSP 操作。**
