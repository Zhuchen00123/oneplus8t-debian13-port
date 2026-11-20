# r5 内核缺口清单（基于 r4 真机实测，2026-11-20）

> **状态：只写清单，不改内核、不连接手机做采集。** 本文所有 r4 现状都指向本轮真机证据或本轮现场只读测量；没有把旧文档结论当实测。
> 证据根：设备 `/srv/kebab-re-20261120/evidence/**`（t2/t4/t5/t6 窗口产物）、仓库 `rust/kebab-observer/**`、内核树 `sources/linux`（HEAD `71f4068cb254`，VERSION 7.2.0）。
> 等级标记：**[实测]** = 本轮真机或离线可复现实验直接得到；**[推断]** = 源码/测量外推，未直接验证；**[未验证]** = 需要 r5 窗口才能确认。

## 0. r4 现场（本轮只读测量，全部可复现）

| 项 | 值 | 命令 |
| --- | --- | --- |
| 内核 | `7.2.0-kebab-debian13-r4-venus+ #4 SMP PREEMPT Thu Nov 5 19:27:35 CST 2026 aarch64` | `uname -a` |
| 运行配置 | `/proc/config.gz` 53,214 B / 9,120 行 | `zcat /proc/config.gz | wc -l` |
| tracefs | 已挂载 `rw,nosuid,nodev,noexec`（`/sys/kernel/tracing`，debugfs 亦挂） | `mount | grep -E 'tracefs|debugfs'` |
| 事件面 | **142** 个事件目录 / **1,594** 条 available_events | `ls events | wc -l` / `wc -l < available_events` |
| 可挂函数 | **62,175**（available_filter_functions） | `wc -l < available_filter_functions` |
| 内核文本符号 | **95,080**（kallsyms `[tT]`） | `grep -c ' [tT] ' /proc/kallsyms` |
| tracers | `function_graph function nop` | `cat available_tracers` |
| syscall 事件 | **`events/syscalls` = 0 条**（只有 `raw_syscalls/{sys_enter,sys_exit}`） | `ls events/syscalls | wc -l` |
| binder 事件 | **31** 条（目录内另有 `enable`/`filter`；含 `binder_transaction`、`binder_transaction_received`、`binder_return`、`binder_transaction_fd_send/recv`、`binder_netlink_report`、`binder_alloc_*`） | `ls events/binder` |
| binder debugfs | `/sys/kernel/debug/binder/{state,state_hashed,stats,proc,failed_transaction_log}` 存在；容器停机时 `state` 为空 | `sudo ls /sys/kernel/debug/binder` |
| LSM/BPF 前提 | `perf_event_paranoid=2`、`unprivileged_bpf_disabled=2`、`kptr_restrict=0` | `cat /proc/sys/kernel/*` |
| 32 位用户态 | **不存在**：`/lib/ld-linux-armhf.so.3` 缺失、`dpkg --print-foreign-architectures` 为空 | 同左 |

关键 CONFIG（`zcat /proc/config.gz`）**[实测]**：

```
CONFIG_COMPAT=y                CONFIG_COMPAT_32BIT_TIME=y
CONFIG_FTRACE=y                CONFIG_FUNCTION_TRACER=y      CONFIG_FUNCTION_GRAPH_TRACER=y
CONFIG_DYNAMIC_FTRACE=y        CONFIG_DYNAMIC_FTRACE_WITH_ARGS=y
CONFIG_KPROBE_EVENTS=y         # CONFIG_KPROBE_EVENTS_ON_NOTRACE is not set
CONFIG_UPROBE_EVENTS=y         CONFIG_BPF=y  CONFIG_BPF_SYSCALL=y  CONFIG_BPF_JIT=y
CONFIG_BPF_LSM=y               CONFIG_BPF_EVENTS=y          CONFIG_DEBUG_INFO_BTF=y  CONFIG_KALLSYMS_ALL=y
CONFIG_ANDROID_BINDER_IPC=y    CONFIG_ANDROID_BINDER_DEVICES="binder,hwbinder,vndbinder"
# CONFIG_ANDROID_BINDERFS is not set
# CONFIG_FTRACE_SYSCALLS is not set
# CONFIG_FPROBE is not set
```

## 1. 汇总

| # | 主题 | r4 现状（证据） | 等级 | r5 内核改动 | 谁改 |
| --- | --- | --- | --- | --- | --- |
| G1 | 32 位 compat 追踪 | `COMPAT=y` 但零验证；设备无 32 位用户态 | 实测 | **无** | 观测器 + 离线夹具 |
| G2 | Binder 因果 | tx 51 / rx 55，`code=1599098439` 异常 | 实测 | **无**（tracepoint 已够） | 观测器（raw args 取错） |
| G3 | uprobe 可检测性 | 30 s 窗口未见反应；arm64 用 `brk` 打补丁 | 实测 + 推断 | **无** | 夹具 + A/B 设计 |
| G4 | binderfs | `ANDROID_BINDERFS not set`，只有 3 个静态设备 | 实测 | `CONFIG_ANDROID_BINDERFS=y` | 内核（可选） |
| G5 | 诊断配置收益/成本 | `events/syscalls=0`、`FPROBE` 缺失；arm64 已 select 所需 HAVE_* | 实测 + 推断 | `FTRACE_SYSCALLS=y`、`FPROBE=y`、`FPROBE_EVENTS=y` | 内核（可选） |
| G6 | 标识与容量 | cgroup id 每窗都变；cap/ring 编译期常量 | 实测 | **无** | 观测器 |
| G7 | 非内核项 | keystore2 uid、cmdline charger、guest 路径/dev 漂移 | 实测 | **无** | 已由 t2/t8 修 |

一句话：**本轮实测暴露的 4 个真缺口里，3 个（G1/G2/G6）不需要改内核**，只有 G4/G5 是内核 config 层面的可选增量；
旧计划里“加 FPROBE/FTRACE_SYSCALLS”的动机被本轮证据**部分修正**：它们解决的是归因与便捷性，不是“tracepoint 抓不到”的硬缺口。

## 2. 逐条

### G1 32 位 compat 追踪：**内核零改动**，缺的是夹具与 ABI 标注

- **r4 现状 [实测]**
  - 内核支持 compat：`CONFIG_COMPAT=y`、`CONFIG_COMPAT_32BIT_TIME=y`。
  - 目标进程纯 64 位：`evidence/step1/facts.json`（`maps_lib32=0`、`/system/lib64` 910 段 / `/system/lib` 0 段）；窗口 2 全部 `nr` 无 compat 位（`evidence/step2/FINDINGS.md §2.1`）。
  - APK 内仍有 49 个 `armeabi-v7a` .so（`evidence/step1/apk-direct-map.json`），只是本轮没有 32 位进程加载它们。
  - 设备**完全没有** 32 位用户态：`/lib/ld-linux-armhf.so.3` 不存在、无 foreign arch（本文 §0）。
  - 观测器把 `sys_enter.id` 直接当原生号写进 `arg0`（`rust/kebab-observer/bpf/track.bpf.c` → `SEC("raw_tracepoint/sys_enter")`）。
- **缺口**
  1. ARM32 与 ARM64 的系统调用号空间重叠但语义不同：ARM32 `open`=5 / `openat`=322，ARM64 `openat`=56、`open` 不存在。现在收到 `nr=5` 会按 ARM64 解释成 `setxattr` 一类，**静默错判**。
  2. 没有 aarch32 夹具 ⇒ compat 分支零验证（这一点 r4 无法自证）。
- **r5 改动（内核）**：无。compat 任务照样触发 `raw_syscalls:sys_enter`，`id` 就是 compat 号；内核侧没有可加的东西。
- **r5 改动（工具，最小）**：
  - BPF：用 `task = bpf_get_current_task_btf()`，CO-RE 读 `task->thread_info.flags`，取 `_TIF_32BIT`（`arch/arm64/include/asm/thread_info.h: #define _TIF_32BIT (1 << TIF_32BIT)`，arm64 的 `thread_info` 内嵌在 `task_struct`）→ 事件里加一个 `abi` 字节（0=native, 1=compat, 2=unknown）。
  - 用户态：按 `abi` 选号表；未知时只记 `nr` 并标 `nr_semantics=unknown`，不再猜名字。
- **验证命令**
  - 离线（可在 Windows/WSL 完成）：`qemu-arm` + 静态 armhf 夹具（`gcc-arm-linux-gnueabihf -static`，`build-essential` 之外零依赖），跑一次 `echo hi` 级负载，断言 `abi=1` 且 `nr` 按 ARM32 表解析；x86_64 WSL 回归断言 `abi=0`。
  - 真机：窗口内断言 `abi=0` 全量（本轮就该如此），并记录 `abi=1` 计数应为 0 —— 这本身是对 r4 结论的反证。
- **风险与回退**：CO-RE 读 `thread_info` 依赖 BTF 里该字段存在（`DEBUG_INFO_BTF=y`，但需 `bpftool btf dump file /sys/kernel/btf/vmlinux` 现场确认）；若缺失，回退到“只记 nr + `abi=unknown`”，绝不推断名字。

### G2 Binder 因果：**内核零改动**，`code=1599098439` 是我们自己读错了参数位置

- **r4 现状 [实测]**
  - 窗口 2：`binder_transaction` 51 / `binder_transaction_received` 55（`evidence/step2/FINDINGS.md §1/§2.2`、`cold.jsonl`）。
  - cold 段出现 `code=1599098439` = `0x5F5A5F67`，与 frida 段的 `5/20/13` 矛盾（`evidence/step2/FINDINGS.md §2.2`）。
  - 观测器：`SEC("raw_tracepoint/binder_transaction")` 里取 `args[0]` 当 `struct binder_transaction *` 后 CO-RE 解引用（`rust/kebab-observer/bpf/track.bpf.c`）。
  - 内核源码：`TRACE_EVENT(binder_transaction, TP_PROTO(bool reply, struct binder_transaction *t, struct binder_node *target_node), TP_ARGS(reply, t, target_node))`（`sources/linux/drivers/android/binder_trace.h`）⇒ raw tracepoint 的 `args[0]` 是 **`reply` 布尔值**，`args[1]` 才是 `t`。
  - tracepoint 携带的字段：`debug_id`、`target_node`、`to_proc`（pid）、`to_thread`、`reply`、`code`、`flags`（同文件 `TP_STRUCT__entry`）；`binder_transaction_received` 只带 `debug_id`。
  - 服务名不在 tracepoint 里；但 r4 有 `/sys/kernel/debug/binder/{state,proc,stats}`（本文 §0），容器运行时才有内容。
- **缺口**
  1. **参数位置错**：把 `reply(0/1)` 当指针解引用，`0x5F5A5F67` 这种垃圾必然出现。这条要**更正** FINDINGS §2.2 的表述：不是“字段偏移要复核”，而是 raw tracepoint 的第一个参数根本不是 `t`。
  2. **没有 reply 关联**：`reply` 标志与 `debug_id` 都在，但观测器没存、也没和 `binder_transaction_received` 的 `debug_id` 配对 ⇒ 无法回答“谁收了谁的回包”。
  3. **服务名缺失**：`to_proc` 只是 pid；map 到“system_server / 某 SDK 服务”需要外部快照。
- **r5 改动（内核）**：无。tracepoint 已经给出配对所需的一切（`debug_id` 双向 + `reply` + `to_proc` + `to_thread` + `code` + `flags`）。
  - **不建议**给 `drivers/android/binder.c` 加 interface-descriptor 的新 tracepoint：侵入上游、且 debugfs `state` 已能提供 pid↔服务名映射。
- **r5 改动（工具，必须做）**：
  - 修 raw args：`args[0]=reply`、`args[1]=t`（CO-RE 取 `debug_id/to_proc/to_thread/code/flags`）、`args[2]=target_node`。
  - 加配对：以 `debug_id` 为键把 tx（发方=当前 task）与 rx（收方=当前 task）连起来；`reply=1` 的事务单独标出。
  - 采集时同步一次 `debugfs state` 快照（`sudo cat /sys/kernel/debug/binder/state`）落到会话目录，事后把 `to_proc` 映射成 `proc <pid> <cmdline>` 与 context 名。
  - 记录 `causality=debug_id|time_only|none`，明确强度。
- **验证命令**
  - 离线：无法造 Binder（WSL 内核 BTF 无 `struct binder_transaction`，两程序被 verifier 拒；`evidence` 见 t3 交付说明）⇒ **必须在真机窗口验**。
  - 真机（窗口内，read-only 采集）：dump 一次 `format` 确认索引（`sudo cat /sys/kernel/tracing/events/binder/binder_transaction/format`，本轮已确认字段顺序 `debug_id@8 … code@28 flags@32`），再跑 30 s 会话，断言：`code` 全部 ∈ {已知 Binder code 集}、`tx/rx` 的 `debug_id` 配对率 >0、`reply` 计数 >0。
- **风险与回退**：debugfs `state` 在容器运行时的稳定性**未验证**（本轮容器停机，`state` 为空）；回退为只用 `debug_id` 配对、服务名留空并在报告里标注。

### G3 uprobe 可检测性与替代观测点：**内核零改动**，缺的是“自哈希 A/B”

- **r4 现状 [实测]**
  - `CONFIG_UPROBE_EVENTS=y`，`perf_event_paranoid=2`（宿主 root 才能挂）；`unprivileged_bpf_disabled=2`（应用侧不能自己用 BPF 反制）。
  - t6 窗口 3：两个目标真挂上（`apk-libssl` tuple / `sys-libssl` `inode_dev_drift` + `confirmation=map_files`），30 s 内 `calls=23`、`identity_rejected=0`、`failures=[]`、`passed=true`，应用存活、logcat 无 exit/crash（`evidence/step3/uprobe.jsonl`、`B2-logcat.txt`、`COMPARE.md`）。
  - **30 s 未观察到反应只说明这个窗口没反应**，不能外推“不可检测”。
- **机制（源码）[实测源码]**：arm64 的 uprobe 用 `brk` 指令替换目标指令（`arch/arm64/include/asm/uprobes.h`：`UPROBE_SWBP_INSN = BRK64_OPCODE_UPROBES`），写入走 `uprobe_write_opcode()` → `__replace_page()`（`kernel/events/uprobes.c`），即**对该 mm 的私有 CoW 页**打补丁。
  - 推论 **[推断]**：目标只要 `mprotect`+读自己 `.text`（或读 `/proc/self/mem`）做自哈希，就能看到探针点的 `brk`；且因为是每 mm 私有，不会跨进程泄漏。这解释了“为什么自哈希 A/B 必须做”。
- **缺口**：①没有自哈希夹具 ⇒ 不知道这类检测在 r4 上是否真会发生；②窗口只有 30 s，风控上报可能是异步的；③WebView/Chromium 的 BoringSSL 无导出符号，本工具不覆盖（t6 已写边界）。
- **r5 改动（内核）**：无。r4 已有的替代观测点足够：
  - `function_graph`（`available_tracers` 含它，62,175 个可挂函数 **[实测]**）——不需要 uprobe，也不改目标 `.text`；
  - `kprobe_events`（`KPROBE_EVENTS=y`）——可对内核函数取参数；`noinstr` 目标需 `KPROBE_EVENTS_ON_NOTRACE=y`（r4 未开，见 G5）；
  - `raw_syscalls` tracepoint（本轮一直在用）。
- **r5 改动（夹具/窗口设计，必须做）**：
  1. 新夹具 `rust/kebab-observer/fixtures/uprobe_selfhash_fixture.c`：`mprotect(PROT_READ)` 自己 `.text`，对目标函数做 FNV-1a 哈希，在探针挂上前/后各打一行；期望“挂上前=原字节、挂上后=brk”。这是**离线**可完成的（x86_64 WSL 与 aarch64 均可）。
  2. 窗口内 A/B 三组：①只 attach 不 load（t4 建议的对照）；②只挂 `SSL_read`（更少改写）；③完全不挂。用 app 存活时间 + logcat 对比。
  3. 对 WebView 明文，替代观测点只有：`function_graph/kprobe` 到 `libwebviewchromium` 的 BoringSSL 内部符号（需符号表，release 版通常无）或 Java 层 Conscrypt（`libjavacrypto` 有导出）。本轮**未验证**。
- **验证命令**：离线 `wsl -d Debian -- cc -O2 -o /tmp/selfhash fixtures/uprobe_selfhash_fixture.c && kebab-observer tls-attach --pid <pid> ...`；真机窗口按三组 A/B。
- **风险与回退**：自哈希夹具本身会改变目标被测行为（读 `.text` 会被某些加固库视为异常）；回退为只做“attach 不 load”的纯外部 A/B。

### G4 binderfs：单容器本轮**不需要**，多容器/隔离才需要（内核 config，可选）

- **r4 现状 [实测]**：`# CONFIG_ANDROID_BINDERFS is not set`；`ANDROID_BINDER_IPC=y` 且 `ANDROID_BINDER_DEVICES="binder,hwbinder,vndbinder"`（3 个静态设备、全局一份）；`/sys/fs/binder` 不存在；`grep binder /proc/modules` = 0（内建）。
- **缺口**
  1. 本轮 Waydroid 只有一个 Android 实例，3 个静态设备**够用** ⇒ binderfs 对当前目标没有收益（这一点必须写清，避免为“看起来先进”而开）。
  2. 一旦要多开实例或让容器与宿主隔离 binder 命名空间，静态设备做不到：所有实例共用同一组 `/dev/binder*`。
  3. binderfs 还提供 per-mount 的设备创建（`binder-control`）与统计，便于把“哪个实例在说话”分开。
- **r5 改动（内核）**：`CONFIG_ANDROID_BINDERFS=y`。依赖 `ANDROID_BINDER_IPC=y`（已满足）；仓库已有 `config/debian13-r5-binderfs.config`（只含 `CONFIG_LOCALVERSION="-kebab-debian13-r5-binderfs"` + `CONFIG_ANDROID_BINDERFS=y`）。
  - **合并注意**：若把该 fragment 并入统一 r5 内核，必须先删掉它自带的 `CONFIG_LOCALVERSION`，否则模块目录与启动项不一致（r4 现役 `-r4-venus+`，多一个 localversion 会让既有 585 个模块路径全不匹配）。
- **验证命令（r5 构建后）**
  - `ls /sys/fs/binder` / `mount -t binder binder /dev/binderfs`（root）→ 目录出现 `binder-control`；
  - `binder-control` 创建设备后 `ls -l /dev/binderfs`；
  - 容器仍能起来：`waydroid shell getprop sys.boot_completed` = 1（**该步骤属 device 的窗口，不属本文**）；
  - 回退：不挂 binderfs 时静态设备仍在，`ANDROID_BINDERFS=y` 不改变现有 `/dev/binder` 行为。
- **风险**：Android 13 的 init/ueventd 期望静态节点；若改用 binderfs，需要同步 lxc 的 bind 挂载（`/dev/binderfs/binder` → 容器内 `/dev/binder`）。**本轮不做**，列在这里是为了让 r5 决定“要不要为多实例付这个代价”。

### G5 FTRACE_SYSCALLS / FPROBE / FPROBE_EVENTS：收益是归因与便捷，不是硬缺口（内核 config，可选）

- **r4 现状 [实测]**
  - `# CONFIG_FTRACE_SYSCALLS is not set` ⇒ `events/syscalls` **0 条**，只有 `raw_syscalls/{sys_enter,sys_exit}`。观测器因此用两个 `raw_tracepoint/sys_enter`（全量 + openat 过滤）+ 手工号表（`bpf/track.bpf.c`）。
  - `# CONFIG_FPROBE is not set`；7.2 树里 `FPROBE_EVENTS` 存在（`depends on FPROBE`，`default y`）——不是新内核才有。
  - 依赖已满足：`DYNAMIC_FTRACE_WITH_ARGS=y`、`FUNCTION_GRAPH_TRACER=y`；arm64 已 `select HAVE_FUNCTION_GRAPH_FREGS / HAVE_FTRACE_GRAPH_FUNC / HAVE_REGS_AND_STACK_ACCESS_API / HAVE_SYSCALL_TRACEPOINTS`（`sources/linux/arch/arm64/Kconfig`、`kernel/trace/Kconfig`）⇒ **FPROBE 在 r4 的架构上可开，无架构阻塞**。
- **收益**
  1. `FTRACE_SYSCALLS` **[推断，但有源码依据]**：为每个系统调用生成类型化 tracepoint（`syscalls:sys_enter_openat` 直接给 `filename` 指针、`flags`、`mode`），观测器可以删掉号表与“先拿 nr 再猜参数”的逻辑，减少**用户态二次读内存**的次数（t4 的 openat 210 条就是这么来的，只是路径经 `bpf_probe_read_user_str`）。
  2. `FPROBE`/`FPROBE_EVENTS` **[推断]**：函数入口+返回带参数（BTF 类型），正是 FINDINGS §2.3 缺的“谁调用 exit(28)”：`f:do_exit do_exit`、`f:__arm64_sys_exit_group` 之类可以在**不改内核代码**的情况下拿到调用者 tid/comm 与返回。相比之下 `function_graph` 只能给调用树、没有参数值。
- **成本 [推断，需 r5 实测]**
  - `FTRACE_SYSCALLS`：内核镜像与 `syscall_metadata` 表增大（约 400+ 条事件定义），事件目录从 1,594 涨到 2,000 量级；未启用时静态 key 关闭，运行时开销可忽略。
  - `FPROBE`：`select FUNCTION_GRAPH_TRACER`（已 y），每次探测走 fgraph trampoline，单点开销高于 fentry；可用函数 62,175 个，**必须配 filter**（按 comm/pid）否则会把目标拖慢——这会反过来影响被测行为，是检测面的一部分。
- **r5 改动（建议新增 fragment，不改 r4 base）**

```
# config/debian13-r5-diag.config  （建议新增；与 debian13-r5-binderfs.config 合并时去掉后者 LOCALVERSION）
CONFIG_ANDROID_BINDERFS=y
CONFIG_FTRACE_SYSCALLS=y
CONFIG_FPROBE=y
CONFIG_FPROBE_EVENTS=y
# 评估项，默认不开（会让 noinstr/热路径也可挂 kprobe，风险更高）：
# CONFIG_KPROBE_EVENTS_ON_NOTRACE=y
```

- **验证命令（r5 构建后，按顺序）**
  1. `zcat /proc/config.gz | grep -E 'FPROBE|FTRACE_SYSCALLS|BINDERFS'`（三个 =y）；
  2. `ls /sys/kernel/tracing/events/syscalls | wc -l` 应从 **0** 变成 >400；
  3. `echo 'f:myexit do_exit' > /sys/kernel/tracing/dynamic_events` 成功且 `events/fprobe/myexit` 出现；
  4. `echo 1 > events/syscalls/sys_enter_openat/enable` + 一次打开文件，确认 `trace` 里出现 filename；
  5. 30 s 会话复跑 t4 的注入 A/B，断言能抓到 `exit_group(28)` 的发起者（tid/comm）——这是 r4 明确丢掉的那条证据（`FINDINGS §2.4`）。
- **风险与回退**：fprobe 在 PREEMPT 内核的稳定性、以及“开了 fprobe 会不会让加固库更容易发现我们”（`/sys/kernel/tracing` 全局可见，`kptr_restrict=0`）；回退方案是**单独 r5 内核 + 独立模块目录**，保留 r4 启动项，任何一项验证失败就回 r4（既有 r5 试机流程已支持一次 `fastboot boot` + 一次 reboot 回退）。

### G6 标识与容量：**内核零改动**，都是观测器/脚本层的账

- **cgroup id 不稳定 [实测]**：同一容器每次启动都换：43943（t2）→ 53102（t4 cold）→ 55646 / 58092（t4 frida 两跑）→ 63331（`evidence/step2/FINDINGS.md §2.5`）。
  - 缺口：任何写死 cgroup id 的配方会静默 `gate=0`。
  - r5 改动（工具）：门改成 `uid + cgroup path hash + ns inode` 双/三标识；或直接 `uid + comm 前缀`。内核无改动。
- **容量 [实测]**：ring 1 MiB + 单会话 cap 20,000 是编译期常量；冷启动段 gate 命中 1,278,557 次只写进 20,000 条（lost 163,570），注入段 lost 22,833，**`exit_group(28)` 因此丢失**（`FINDINGS §2.4`）。
  - r5 改动（工具）：ring 与 cap 变运行参数（或按事件类型分 ring），并支持“只对主线程全量 + 其余采样”的双会话。内核侧无阻塞：`BPF_MAP_TYPE_RINGBUF` 上限与 `BPF_MAX_*` 都由内核给出，**具体上限本轮未测 [未验证]**。
- **观测面数字 [实测]**（供 r5 取舍）：142 个事件目录 / 1,594 条事件 / 62,175 个可挂函数 / 95,080 个文本符号；`available_tracers` 含 `function_graph`。
- **安全前提 [实测]**：`perf_event_paranoid=2`、`unprivileged_bpf_disabled=2` ⇒ 全部采集需 root；r5 **建议保持不变**，并在所有采集脚本里显式检查（不要在 r5 放开，否则等于给加固方多一条反制面）。

### G7 明确不属于内核清单的项（防止误入手册）

| 项 | 现状/修法 | 证据 |
| --- | --- | --- |
| keystore2 属主错导致 system_server 重启循环 | 容器层 `chown 1017:1017 /data/misc/keystore/*`（t2 已修，可逆） | `evidence/step1/keystore-diag-*.txt`、`launch-diag.txt` |
| `ro.boot.mode=charger` 导致 Android 不 queue late-init | 容器层：去掉 cmdline 的 charger 令牌并**可写** bind 到容器 `/proc/cmdline`（t1 已实现 `work/lab-cmdline`） | t1 交付说明；`/srv/kebab-re-20261120/bin/up.sh` |
| 宿主路径 vs guest 路径 + loop dev 漂移导致 uprobe 关联失败 | 观测器已修（t8：`--maps-root` + `(dev,inode)` 元组 + `inode_dev_drift`） | `evidence/step3/report3.json`、`/srv/.../bin/tls_attach.rs` |
| WebView/Chromium BoringSSL 无导出符号 | 不覆盖；替代点见 G3 | `evidence/step3/COMPARE.md` |

## 3. 实施顺序（建议）

1. **先做零内核改动的三项**（G1 夹具、G2 参数修复 + debug_id 配对、G6 标识/容量）——它们能在 r4 上直接验证，且不引入新内核风险。
2. **再做 G5**（`FTRACE_SYSCALLS` + `FPROBE/FPROBE_EVENTS`）：这是唯一能补上 `exit(28)` 归因的路径；用独立 r5 内核 + 独立模块目录，一次 `fastboot boot` 试机，失败即回 r4。
3. **G4（binderfs）最后做，且只做一次**：单容器场景无收益，等真的需要多实例再开；开启代价是 lxc bind 配置同步。
4. G3 的自哈希夹具与三组 A/B 与第 1 步并行，结论进 r5 的“检测面”章节。

## 4. 未验证清单（本文没有做的事）

- 没有为任何一项改动构建 r5 内核，也没有验证 `CONFIG_FPROBE=y` 在 7.2.0 + PREEMPT + arm64 上真能编过（架构依赖已从源码确认可满足，但**未编译**）。
- 没有在真机窗口验证 G1/G2/G3 的观测结果（当前 device 窗口由其他成员独占；本文只做只读测量与源码核对）。
- binder debugfs `state` 在容器运行时是否可读、内容是否稳定：**未验证**（本轮容器停机，`state` 为空）。
- `BPF_MAP_TYPE_RINGBUF` 在 7.2 的实际大小上限：**未测**。
- 易盾是否真的做 `.text` 自哈希：**未验证**（只有机制推断 + 一次 30 s 未见反应）。
- t6 的“30 s 无反应”只覆盖 `libssl` 路径；Chromium 路径完全未观测。

## 5. 证据索引

| 证据 | 路径 | 本文用在哪 |
| --- | --- | --- |
| 目标进程/ABI/APK 偏移 | `/srv/kebab-re-20261120/evidence/step1/{facts.json,apk-direct-map.json,maps.txt}` | G1、G7 |
| 冷启动 + Frida 尾段 | `/srv/kebab-re-20261120/evidence/step2/{FINDINGS.md,cold.jsonl,frida*.jsonl,counts.json,timeline.md}` | G1、G2、G6 |
| uprobe 挂载与抓包 | `/srv/kebab-re-20261120/evidence/step3/{uprobe.jsonl,report3.json,COMPARE.md,http-sample.json,B2-logcat.txt}` | G3、G7 |
| 观测器源码 | `rust/kebab-observer/bpf/{track.bpf.c,tls_attach.bpf.c}`、`rust/kebab-observer/src/{track.rs,tls_attach.rs,tls_attach_core.rs}` | G1、G2、G3 |
| 内核源码 | `sources/linux@{71f4068cb254}`：`drivers/android/{binder_trace.h,Kconfig}`、`kernel/trace/Kconfig`、`arch/arm64/{Kconfig,include/asm/uprobes.h,include/asm/thread_info.h}`、`kernel/events/uprobes.c` | G2、G4、G5 |
| r4 运行配置 | 设备 `/proc/config.gz`（53,214 B / 9,120 行） | §0、G1、G4、G5、G6 |
| 已有 r5 config 片段 | `config/debian13-r5-binderfs.config`、`config/debian13-r5-integrated.config`、`config/debian13-r5-coresight.config` | G4、G5 |
| 旧计划（仅背景，不作实测依据） | `docs/r5-hybrid-ebpf-re-plan-20261116.md`、`docs/r5-ebpf-re-community-survey-20261116.md` | §1 注 |

## 6. 复现（本文所有只读测量）

```
# 运行配置与观测面（只需 root 读，不启停任何服务）
ssh kebab 'zcat /proc/config.gz | wc -l'
ssh kebab 'sudo -n sh -c "ls /sys/kernel/tracing/events | wc -l; wc -l < /sys/kernel/tracing/available_events; wc -l < /sys/kernel/tracing/available_filter_functions"'
ssh kebab 'sudo -n grep -c " [tT] " /proc/kallsyms'
ssh kebab 'ls /sys/kernel/tracing/events/syscalls | wc -l; ls /sys/kernel/tracing/events/binder | wc -l'
ssh kebab 'cat /proc/sys/kernel/perf_event_paranoid /proc/sys/kernel/unprivileged_bpf_disabled /proc/sys/kernel/kptr_restrict'
ssh kebab 'sudo -n cat /sys/kernel/tracing/events/binder/binder_transaction/format'
ssh kebab 'sudo -n ls /sys/kernel/debug/binder'
# 32 位用户态是否存在
ssh kebab 'ls /lib/ld-linux-armhf.so.3; dpkg --print-foreign-architectures'
# 源码侧事实
git -C sources/linux rev-parse --short=12 HEAD
grep -n 'TP_PROTO' sources/linux/drivers/android/binder_trace.h | head
grep -n 'select HAVE_FUNCTION_GRAPH_FREGS\|select HAVE_FTRACE_GRAPH_FUNC\|select HAVE_SYSCALL_TRACEPOINTS' sources/linux/arch/arm64/Kconfig
```
