# 运动世界真机实测：问题记录（2026-11-20，持续更新）

> 规则：**只记本轮真机/本轮静态证据能支撑的问题**；每条给"现象 / 根因 / 证据 / 现有处置 / 是否内核相关 / r5 候选"。
> 内核相关条目在最前面（K 编号），用户态与环境问题在后（U 编号）。t4/t6 的新发现继续往这里加。

## 0. 摘要表

| 编号 | 一句话 | 内核相关 | 状态 |
|---|---|---|---|
| K1 | 插 USB 供电开机 → cmdline 带 `androidboot.mode=charger` → Android 只跑 charger、不 queue late-init | 是（内核 cmdline） | 已绕过（容器级 lab-cmdline） |
| K2 | r4 无 `CONFIG_ANDROID_BINDERFS`，只有内建 binder 节点 | 是 | 单容器可用；多容器/隔离不行 |
| K3 | r4 无 `CONFIG_FTRACE_SYSCALLS`，tracefs 没有 syscalls 命名事件 | 是 | 用 raw_tracepoint 自采绕过 |
| K4 | r4 无 `CONFIG_FPROBE/FPROBE_EVENTS` | 是 | 只能 kprobe（改指令）替代 |
| K5 | 32 位 compat 追踪缺口（Thumb 位、编号空间、寄存器宽度） | 是 | 工具侧适配，待 t4 真机确认 |
| K6 | `perf_event_paranoid=2`、`unprivileged_bpf_disabled=2` | 是（安全策略） | 全程 root 执行；容器内不可用 |
| K7 | 宿主 /proc pid 与内核 task->tgid 不同视图，直接比对会 0 事件 | 否（观测坑） | track 已用 ns pid + start_ticks 处理 |
| K8 | Binder BPF 程序依赖目标内核的 binder BTF；WSL 缺、r4 有 | 是（可移植性） | r4 实测 binder_attached=true |
| U1 | `config_session` 强制 rbind `$XDG_RUNTIME_DIR/pulse/native`，文件缺失则 lxc-start 失败 | 否 | up.sh 预建文件 |
| U2 | Android 13 first-stage init 会 `chmod(/proc/cmdline,0440)`，只读 bind 触发 InitFatalReboot(signal 6) | 否（Android 行为） | 用可写 bind |
| U3 | usb0 默认路由（192.168.8.2, metric 100）无公网却抢默认出口；resolved DNS 也跟着它 | 否（环境） | 窗口内 wlan0 metric 50 + 改 DNS，收尾还原 |
| U4 | 根分区 98% 满（剩 166MB），镜像 2.3GB 只能放 /srv | 否（环境） | 所有新文件只写 /srv |
| U5 | 充满后 100% + "Not charging" | 否 | 正常现象，不当作故障 |
| U6 | debian 用户 ping 缺 CAP_NET_RAW | 否 | 改用 curl/TCP 验证 |
| U7 | 收窗后 `waydroid status` 报未初始化 | 否 | 正常：cfg 在 bind 挂载树里 |
| K9 | Binder 只有参数没有因果（无服务名/reply 配对）；cold 段 51 条里 1 条 code=0x5F504E47 异常 | 是（观测） | 待按 tracepoint format 复核；非系统性 |
| K10 | exit(28) 的内核事件没抓到：ring 1 MiB + cap 20,000 在注入段先满（lost 16.3 万），只剩 Zygote logcat | 否（观测设计） | 列为短板，r5 清单给拆分方案 |
| U13 | cgroup id 每次容器启动都变（43943→53102→58092→55646→63331） | 否 | 配方必须现场 stat，写死会静默 gate=0 |

---

## 1. 内核相关（K）

### K1 充电器模式：插 USB 供电开机会让 Android 永不完成启动
- **现象**：容器、ADB 都正常，但 `sys.boot_completed` 永远为空（等 120 s 也一样）；dmesg 在 `processing action (charger)` 后静默。
- **根因**：宿主 `/proc/cmdline` 含两份 `androidboot.mode=charger` 与 `androidboot.startupmode=dc_charger`（USB 供电开机时由 bootloader 传入）；Android init 取 `ro.boot.mode=charger`，只执行 `(charger)` 动作，不 queue `late-init` ⇒ 无 vold/zygote/SurfaceFlinger。
- **证据**：`/srv/kebab-re-20261120/evidence/prep/SUMMARY.md`（窗口 3/4/5 各 120 s 复现、窗口 6 InitFatalReboot）、`window-20261120T101458Z.log`；另见 [charger 模式发现](sportsworld-charger-mode-finding-20261120.md)。
- **处置**：容器级生成 `work/lab-cmdline`（去掉 charger 令牌，保留 `androidboot.mode=normal`）bind 到容器 `/proc/cmdline`；修后 16 s 到 `boot_completed=1`。不写持久配置。
- **r5 候选**：不改内核即可；若要根治需 bootloader/内核 cmdline 层面处理（不在本轮边界）。

### K2 没有 binderfs
- **现象**：`/proc/filesystems` 无 `binder`，`/dev/binderfs` 不存在；`systemctl start waydroid-container` 不会自动 mount binderfs。
- **根因**：`CONFIG_ANDROID_BINDER_IPC=y` + `CONFIG_ANDROID_BINDER_DEVICES="binder,hwbinder,vndbinder"`，但 **`CONFIG_ANDROID_BINDERFS` 未设**。
- **证据**：`/proc/config.gz`（本轮实测）、`/proc/misc`（258 binder / 259 hwbinder / 260 vndbinder）、`/dev/binder` 存在。
- **影响**：单容器 Waydroid 可用（waydroid 检测到已有节点就直接 bind 进容器）；**多容器、每容器独立 binder 命名空间、隔离实验不可用**。
- **处置**：本轮不启用多容器。
- **r5 候选**：`CONFIG_ANDROID_BINDERFS=y`（已在本轮 r5 计划内）。

### K3 没有 FTRACE_SYSCALLS
- **现象**：`/sys/kernel/debug/tracing/events/syscalls` 为空；只能用 raw_tracepoint 自己采。
- **证据**：`/proc/config.gz` 无 `CONFIG_FTRACE_SYSCALLS`；tracefs 已挂载（`/sys/kernel/tracing`、`/sys/kernel/debug/tracing`）。
- **影响**：少一套"命名 syscall 事件"做交叉对照；perf/ftrace 直接按名字过滤不可用。
- **处置**：t3 的 track 用 raw_tracepoint 工作，已验证 8784 事件 0 丢失。
- **r5 候选**：`CONFIG_FTRACE_SYSCALLS=y`（计划已列）。

### K4 没有 FPROBE
- **证据**：`/proc/config.gz` 无 `CONFIG_FPROBE` / `CONFIG_FPROBE_EVENTS`。
- **影响**：内核函数入口/返回动态事件只能用 kprobe（插入断点、改指令），不能用 fprobe 的低开销 ftrace 入口。
- **r5 候选**：`CONFIG_FPROBE=y` + `CONFIG_FPROBE_EVENTS=y`（需先验依赖闭包）。

### K5 32 位 compat 追踪缺口
- **静态证据**：APK 里 `lib/armeabi-v7a/libssl.so` 的 `SSL_write` st_value=111,613（奇数），`lib/armeabi/libssl.so`=116,157（奇数）——**Thumb 位**，实际代码偏移要 `& ~1`；ARM32 还有独立的 syscall 编号空间与 32 位寄存器约定。
- **内核侧**：ARM64 的普通 syscall ftrace 排除 compat（`arch/arm64/include/asm/ftrace.h`），raw_tracepoint 的 id 也按 compat 编号空间走，不能套用 aarch64 规则。
- **影响**：如果目标有 32 位线程/进程（APK 同时带 ARM32 库），不单独处理会得到错偏移或错编号。
- **处置**：工具侧对 ARM32 做 `st_value & ~1` 与独立编号映射；t4 真机确认目标是否真有 32 位线程。
- **r5 候选**：不是 Kconfig 项；属于观测器适配 + 文档。

### K6 安全策略：perf/BPF 只能 root
- **证据**：`/proc/sys/kernel/perf_event_paranoid=2`、`/proc/sys/kernel/unprivileged_bpf_disabled=2`。
- **影响**：所有 BPF/perf 操作必须宿主 root；容器内（Android）不能自己加载观察器；也不能靠"容器里跑个工具"绕开。
- **处置**：collector 一律宿主 root 运行；不给容器开放 BPF。
- **r5 候选**：保持现状（这是安全基线，不建议放松）。

### K7 pid 命名空间视图错位（观测坑，不是内核 bug）
- **现象**：WSL 上实测 `/proc` 看到 1577358，内核 `task->tgid` 是 1577953；直接比会静默 0 事件。
- **处置**：track 用 `bpf_get_ns_current_pid_tgid()` 在目标 pidns 内比较，用户态允许 ns/proc 任一视图命中，再用 `start_boottime` 精确防 PID 复用。
- **真机**：Waydroid app 也在自己的 pidns，宿主 pid 与容器 pid 两套都要记录（已写进 t2 提示）。

### K8 Binder 程序的可移植性依赖 BTF
- **现象**：WSL 内核无 binder 类型 BTF → `track_binder_tx/rx` 被 verifier 拒绝；r4 有 `CONFIG_DEBUG_INFO_BTF=y`，实测 `binder_attached=true`。
- **影响**：同一份 BPF 对象在不同内核上装载结果不同，不能拿 WSL 的失败/成功推断手机。
- **处置**：device 侧看 closeout 的 `binder_attached` 字段定性。

---

## 2. 用户态/环境问题（U）

### U1 pulse socket 强制 rbind 导致 lxc-start 失败
- **现象**：`lxc-start` 报 `Failed to setup mount entries`。
- **根因**：waydroid 生成的 `config_session` 把 `$XDG_RUNTIME_DIR/pulse/native` 以 `rbind,create=file`（无 optional）写入；文件不存在时挂载失败。
- **处置**：up.sh 在开窗前预建该文件。

### U2 /proc/cmdline 必须可写
- **现象**：只读 bind 后 Android `InitFatalReboot(signal 6)`（窗口 6，dmesg ts 85208）。
- **根因**：Android 13 first-stage init 会 `chmod("/proc/cmdline", 0440)`。
- **处置**：bind 用可写选项。

### U3 USB 默认路由抢出口 + DNS 被带偏
- **证据**：`ip route`（default via 192.168.8.2 dev usb0 proto static metric 100）、`resolvectl status`（usb0 DNS 192.168.8.2 为 current server，解析超时）；`curl --interface wlan0 --resolve ...` = 200，但默认路径超时。
- **处置**：窗口内 `ip route add default via 192.168.1.1 dev wlan0 metric 50` + `resolvectl default-route usb0 no`，收尾按 journal 还原；USB SSH 不受影响。

### U4 根分区将满
- **证据**：`/dev/sda15` 6.9G 用 6.6G（剩 166MB，98%）；`/srv` 193G 可用。
- **处置**：镜像、userdata 副本、日志、BPF JSONL 全部只写 `/srv/kebab-re-20261120`；窗口内临时文件用完即删。

### U5 充电状态
- 100% + `Not charging` 是充满停充，正常；不当作异常拒止（用户已明确）。

### U6 ping 不可用
- debian 用户无 `CAP_NET_RAW`；连通性用 `curl`/TCP 探测，不用 ping 下结论。

### U7 收窗后 waydroid 显示未初始化
- cfg 与镜像路径都在 bind 挂载的 work 树里；卸掉绑定后 `waydroid status` 报未初始化是预期状态，不是故障。

---

## 3. 待补
- t4（窗口 2）若确认目标进程存在 32 位线程，K5 升级为"实测确认"并补编号/寄存器细节。
- t4/t6 若发现 uprobe 被检测（时序/退出差异），新增 K9「uprobe 可检测性」条目。
- r5 清单（t7）将按 K 编号逐条映射到 Kconfig/补丁/验证命令。

---

## 5. t4（窗口 2）新增（2026-11-20，纯内核 tracepoint + Frida 注入）

### 硬结论（先记结论，再记问题）
- exit(28) **复现 2/2**：用 /srv 既有 frida-server（53,702,368 B，sha256 前 16 `4eebf1fbc66ff54a`，零下载），attach+load 成功后进程在 0.201 s / 0.754 s 消失，logcat 两次都是 `Process ... exited cleanly (28)`。
- **最强路径级证据**：注入瞬间 app 读取 `/proc/self/task/*/status` 从冷启动基线 9 次涨到 196 次（×21.8），`/proc/self/task` 目录读取 9→215（×23.9），且集中在死亡前 0.2 s 内。这是 ptrace/TracerPid 自检的典型路径特征（不等于已证明是易盾而不是 frida-agent 自己，需 detach 不 load 的 A/B）。
- 冷启动段：gate=1,278,557（uid 10127 + cgroup 53102 双门命中）；openat 210 条 / 78 条路径（MMKV 锁 58、base.apk 直映探测 27、/proc/self/maps 14、/proc/self/task 9）；binder tx 51 / rx 55。
- 现场 stat 的 cgroup id 与 t2 不同：43943（t2）→ 53102（cold）→ 58092（frida）→ 55646/63331（其它窗口）。

### K9 Binder 观测只有参数、没有因果
- tracepoint format 实测（`/sys/kernel/tracing/events/binder/binder_transaction/format`）：字段为 debug_id/target_node/to_proc/to_thread/reply/code/flags。
- 工具按 `struct binder_transaction` 指针读，51 条里 50 条 code 正常（34/2/101/6/9/96…），**1 条是 0x5F504E47（ASCII "GNP_" 形状）异常**；to_proc_pid 只到宿主 pid（180611/180316/…），没有服务名，也没有 reply 与 received 的配对。
- 结论：**不是系统性字段错位**（50/51 合理），但单条异常与"无服务名/无 reply 配对"一起，说明当前 Binder 证据只能回答"谁给谁发了 code 多少"，不能回答"哪个服务、哪次 reply"。
- r5 清单要给的：Binder 服务名解析（`binder_node`/`binder_proc` 的 name 或 context）+ reply 配对 + 异常事件的逐字段复核。

### K10 exit(28) 在内核侧缺席（容量/设计问题）
- cold/frida 两段都在注入后很快把 ring 1 MiB + cap 20,000 打满：cold lost 163,570 / truncated 670,008；frida lost 22,833。
- run2 把 trace 起点提前 1 s、死亡进窗口，仍被上限挤掉 ⇒ **exit_group(28) 的内核事件没抓到**，只有 Zygote logcat。
- 这是**观测方案的容量短板**（cap 是编译期常量、ring 不可调），不是内核缺陷；r5 清单应给"按事件类拆 ring / 动态 cap / 只对 exit_group 开小专用程序"的改法。

### U13 cgroup id 随容器启动变化
- 43943（t2）→ 53102（t4 cold）→ 58092（t4 frida）→ 55646/63331（其它窗口）。
- 影响：任何按 cgroup 过滤的配方必须**现场** `stat -c %i /sys/fs/cgroup$(awk -F: '/^0::/{print $3}' /proc/PID/cgroup)`，写死会静默 gate=0（本次实测证据：错误 id 时 gate=0、empty_session=true，不报错）。

### t4 给出的 r4 短板（原文摘要，进 t7 清单）
- 2.1 32 位 compat 零验证（主进程纯 64 位，APK 内 49 个 armeabi-v7a .so 无 aarch32 fixture）。
- 2.2 Binder 只有参数没有因果（见 K9）。
- 2.3 uprobe/fentry 关闭 ⇒ 无法判定 exit(28) 由 Java 还是 native（libnesec/libnetseckit）发起。
- 2.4 容量（见 K10）。
- 2.5 cgroup id 漂移（见 U13）。

### U15 WebView/Chromium 的 TLS 不可观测（t6 第 2 次窗口确认）
- **事实**：t6 第 2 次窗口两个 target 都真挂上（apk-libssl association=tuple/58 段；sys-libssl inode_dev_drift/map_files/4 段），抓到 6 请求/6 响应全 200，但 `http-sample.json` 的 `source=sys-libssl`，host 全是 `b-gtc.gepush.com`、`ido.gepush.com`、`hmma.baidu.com` —— **个推/百度统计等三方 SDK，不是运动世界第一方业务**。
- **原因**：登录/访客页是 H5，TLS 在 `libwebviewchromium.so` 内的 BoringSSL，静态链接、无导出符号，uprobe 挂不上。
- **现状**：工具能抓 Java/OkHttp（Conscrypt→system libssl）与原生 OpenSSL（APK libssl）两条路，但抓不到 WebView 这条路。
- **后续**：窗口 3b 用界面操作把 app 推到第一方请求页面试一次；仍不行就列为 r5 观测缺口（候选：Chromium net-stack 符号/内置调试、代理侧明文、或明确“本工具不覆盖 WebView”）。
- **检测判断（只报观察）**：30 s 内 23 次调用、`attached_probes=8`、`identity_rejected=0`、`failures=[]`、app 存活、logcat 无 exit/died/crash ⇒ **未观察到检测反应**；强度：真挂上=强，30 s 未检测=中，不可外推为“易盾不检测 uprobe”。

---

## 6. t7 复核后的更正（2026-11-20，重要）

> 依据：`docs/r5-kernel-gap-list-20261120.md` + 内核源码核对。以下条目推翻/下调了前面几节的判断。

### K9 更正：Binder 的异常 code 是我们自己读错了参数位置，不是内核缺口
- 内核源码 `sources/linux/drivers/android/binder_trace.h:106-109`：`TRACE_EVENT(binder_transaction, TP_PROTO(bool reply, struct binder_transaction *t, struct binder_node *target_node), ...)`
- ⇒ raw tracepoint 的 `args[0]` 是 **reply(bool)**，`args[1]` 才是 `struct binder_transaction *t`。观测器把 `args[0]` 当 t 解引用，必然读到垃圾（`0x5F5A5F67`）。
- tracepoint 本身已带 `debug_id`（双向）、`reply`、`to_proc/to_thread`、`code/flags` ⇒ **配对只需改工具**；服务名用 r4 已有的 `debugfs /sys/kernel/debug/binder/state` 快照补。**不建议**给 binder.c 加新 tracepoint。
- 状态：K9 从"内核观测缺口"降级为"工具 bug"，内核零改动。

### K5 更正：32 位 compat 内核侧零改动
- `CONFIG_COMPAT=y` + `CONFIG_COMPAT_32BIT_TIME=y`；compat 任务照样触发 `raw_syscalls:sys_enter`，`id` 就是 compat 号。
- 真缺的是：①观测器把 nr 当原生号解释（ARM32 `open`=5 / `openat`=322 vs ARM64 `openat`=56 ⇒ 静默错判）；②设备**根本没有 32 位用户态**（`/lib/ld-linux-armhf.so.3` 缺失、无 foreign arch），夹具只能离线（qemu-arm）。
- 工具改动：CO-RE 读 `task->thread_info.flags & _TIF_32BIT`，事件加 `abi` 字节（0=native/1=compat/2=unknown），未知时只记 nr 不猜名字。

### K3/K4 降级：FPROBE / FTRACE_SYSCALLS 是"可选归因"，不是硬缺口
- 实测 `events/syscalls` = **0 条**（`FTRACE_SYSCALLS` off），观测器是用 `raw_syscalls` + 号表拿到 openat 的。
- 它们解决的是**归因与便捷**（`exit_group(28)` 谁发起、省手工号表），不是"抓不到"。
- r4 架构依赖已满足（arm64 select `HAVE_FUNCTION_GRAPH_FREGS`/`HAVE_FTRACE_GRAPH_FUNC`/`HAVE_REGS_AND_STACK_ACCESS_API`，`DYNAMIC_FTRACE_WITH_ARGS=y`），可开但未编译验证。

### G3 补记：uprobe 在 arm64 上**原理上可被目标自哈希发现**
- arm64 uprobe 用 `brk` 替换指令 + `__replace_page()` 每 mm 私有 CoW；目标读自己 `.text` 能算出与磁盘不同的内容。
- ⇒ t6 的"30 s 未观察到反应"只能算**中强度**；r5 要补自哈希夹具 + 三组 A/B（无探针 / 挂探针 / detach 后）。

### 汇总句
本轮实测暴露的 4 个真缺口里，**3 个（compat / Binder / 容量与身份）不需要改内核**，只有 binderfs 与诊断配置是内核层可选增量。旧计划"加 FPROBE/FTRACE_SYSCALLS"的动机被本轮证据部分修正。

## 7. t10/t11 验证记录（2026-11-20）

### 已验证通过
- **Binder 修正实测通过**：新版 track 在窗口里抓 15 s，binder 6 条（3 tx / 3 rx），`code=[3,20,3]`，旧的 `0x5F5A5F67` 不再出现；`reply` 布尔、`to_proc_pid`、`debug_id` 齐全；`binder_state` 解析到 108 个 proc。证据 `evidence/verify/binder-fixed.jsonl`。
- **自哈希夹具控制组 CLEAN**：不挂探针 10,035 ms / 101 次调用，前后 sha256 一致。证据 `evidence/verify/selfhash-clean.jsonl`。

### U17 load bias 判据误拒静态可执行（工具 bug，阻断 B 组）
- **现象**：`tls-attach` exit 3：`load bias disagreement: segment at 0x23c000 implies 0x0, this one implies 0x10000`，夹具因此未被探针修改，仍是 clean，不能当检测证据。
- **根因**：静态可执行（selfhash，musl 静态）多个 PT_LOAD 的 (p_vaddr - p_offset) 本来就可能不同；工具要求“多段推导的 bias 必须一致”会误拒。
- **处置**：t12 改为“只用包含目标符号文件偏移的那一段推导 + 其余段仅交叉检查 + 支持 --load-bias 覆盖”。
- **影响**：B 组（挂探针=DETECTED）仍未验证；A 组不受影响。

### U18 Binder tx/rx 的 debug_id 差 1 不是字段 bug，是配对语义错
- t11 观察到 tx `2706195/2706197/2706223` ↔ rx `2706196/2706198/2706224`（全部 +1），paired=0。
- 内核源码核对：`binder_transaction` 与 `binder_transaction_received` 的 debug_id 都是 `t->debug_id`，字段读取正确。
- 真正原因：**单侧身份过滤**（只看 app 的 uid/cgroup）时，tx 是 app 发出的请求（reply=false），rx 是投递给 app 的**回复**——属于另一笔 transaction，debug_id 自然不同。tx↔rx 配对只有在过滤范围同时覆盖发送方与接收方时才成立。
- 处置：t12 把 closeout 改成 direction 统计 + `pairing_scope=single_side` 说明；可选支持双侧过滤后再配对。
- **对 r5 清单的影响**：G2（Binder 因果）不能简单写成“改工具就能配对”，要写成“单侧不可配对；双侧过滤或额外关联才能在 r5 拿到因果”。



### U16 业务 UI 是 WebView：第一方流量不在任何可挂 libssl 上（t9 确认）
- 窗口 3b 带探针走完「同意→游客登录→4 tap + 2 swipe（跑步/校园跑候选、下拉刷新、上滑、地图位）」，45 s 内 7 请求/7 响应，host **只有** `b-gtc.gepush.com`、`ido.gepush.com`（个推）；`first_party=0`、`third_party=2`。
- `apk-libssl` 挂上但 **calls=0**；`sys-libssl` calls=22（write 7 / read 15）。末次焦点落到 **`.mvi.WebViewActivity`** ⇒ 业务 UI 本身是 H5/WebView，Chromium 的 BoringSSL 静态链接、无导出符号。
- **结论**：在这个 app 的登录/访客阶段，第一方请求走 WebView，libssl 类 uprobe 抓不到；这不是工具 bug，是覆盖边界。
- **工具硬限制**：`tls-attach` **最多 2 个 --target**（rc=2 原文 "at most 2 --target values are supported"），想同时挂 libjavacrypto 时加不进去；属工具限制，r5/工具清单可提上限。
- 三态判定：`http-sample3.json = NO_FIRST_PARTY`；没把 SDK 请求说成业务请求。



### U14 tls-attach 的 maps 关联按路径匹配 → 容器目标全部误拒（t6 BLOCKED 根因）
- **现象**：t6 的 B 次三次 attach 全部被工具自身拒绝 `exit 3`（`is not mapped by pid ...; refusing to attach to an inode the target never mapped`），两个 target 的 `mapped_segments=0`；但同进程 `/proc/<pid>/maps` 里 base.apk 有 58 行（dev 103:0c / inode 1443630，与 `--report` 目标 dev/inode 一致）、`/system/lib64/libssl.so` 有 4 行。
- **根因**：①工具用**路径字符串**做 maps 关联，容器 maps 显示 guest 路径 `/data/app/...`、`/system/lib64/...`，而我们给的是宿主路径；②系统库所在 rootfs 是 loop 挂载，**st_dev 每窗都变**（实测 00:46→00:48）。
- **处置**：新建 t6fix 任务，改 (st_dev, st_ino) 元组匹配 + `--maps-root`，现场 stat 不复用 report 缓存；修好后重跑 t6。
- **影响**：t6 判 BLOCKED，A/B 不构成 uprobe 检测对照；`http-sample.json` 记 BLOCKED，没有伪造事件。
- **仍然有效的旁证**（A 基线）：登录页稳定可达、12–14 条 ESTAB 全 443、对端 198.18.0.0/15、无 uprobe 时无 exit(28)。
- **归因更正（t8 调查结果）**：三次 exit 3 只有 sys-libssl 被拒，APK target 其实已通过；旧 `--report` 不读 maps 才显示 `mapped_segments=0`（误导性）。真因是 system-libssl 的宿主路径 stat 出 dev `00:46/00:48`，与容器 maps 里同一 inode 3388 的 dev 不同（rootfs loop 重挂），旧代码要求 dev+inode 严格相等、没有 inode 漂移回退。
- **修复与验证**：t8 改为 (dev major:minor, inode) 元组 + inode_dev_drift（需 map_files/basename 确认）+ `--maps-root`（attach_path 落在容器命名空间）；队长在真机用 python3 进程实测 `association=tuple / confirmation=tuple / mapped_segments=4 / runtime_va` 有效，8/8 SHA-256 一致。t6 已重新派发。



---

## 4. t2（窗口 1）新增（2026-11-20）

### U8 keystore2 属主错误导致 system_server 死循环
- **现象**：`am start` 报 `Can't find service: activity`；keystore2 panic `Failed to attach database persistent`，system_server 反复重启。
- **根因**：`/data/misc/keystore/*.sqlite` 属主是 1000（宿主 debian 的 uid 在容器里被当成 system），而 keystore2 以 1017 运行，打不开数据库。
- **处置**：`chown 1017:1017`（可逆，记录在 `logs/fix-journal.txt`）。这是旧 userdata 从 11/15 带过来的状态，不是本轮引入。
- **为什么会发生**：userdata 目录是宿主文件系统直接 bind 进容器的，宿主 uid 与 Android uid 共用同一套数字，历史文件属性会被 Android 误读。

### U9 uiautomator 在本镜像不可用
- **现象**：`uiautomator dump` 输出为空。
- **处置**：页面证据改用截图 + `dumpsys window` / `dumpsys activity` 的 mCurrentFocus。

### U10 宿主代理 fake-IP 隐藏真实对端
- **现象**：app 的 12 条 ESTABLISHED 对端全在 `198.18.0.0/15`（fake-IP 网段）。
- **影响**：只看容器内 `/proc/net/tcp` 无法知道真实服务器；连接归因必须靠宿主侧观测（或代理日志）。TLS 明文抓取不受影响（明文在加密前读）。
- **处置**：t4/t6 归因时标明"对端=fake-IP，真实目的地未知"。

### U11 任务期间非计划复位 1 次
- **事实**：boot_id `9de52f19-…` → `ac1e9a59-…`，发生在容器启 Android 图形栈阶段；前一 boot 无 panic、pstore 为空；之后 9 个窗口未复现；六服务/限充/USB SSH 正常。
- **关联**：设备上已有 `kebab-reboot-observer`（BUDGET_REACHED，自 10/26），属同类问题，建议单独立项，不计入 Waydroid 回归。
- **影响**：t2 证据跨了两个 boot；时间戳类证据需要按 boot_id 分段看。

### U12 ZIP 偏移计算坑（方法学）
- `data_offset` 必须用 **local file header** 的 name_len **和 extra_len**：本 APK 的 `lib/arm64-v8a/libssl.so` 的 extra_len=28,187，漏掉就少算 28,187 字节。
- Android 的 `/proc/pid/maps` 只显示 `base.apk` 和**页对齐**的 file offset，不打印 `apk!/lib/...`；「15 个 .so / 36 段」是按页 offset 反查 ZIP 得到的推断，不是 maps 里的名字。
- **已在设备上重算（installed base.apk，SHA 与原件一致 4049a546…）**：

| 库 / 符号 | ZIP data offset | ELF 偏移 | APK 内绝对偏移 |
|---|---|---|---|
| lib/arm64-v8a/libssl.so | 98,336,768 | — | — |
| SSL_write | | 177,456 | **98,514,224** |
| SSL_read | | 177,260 | 98,514,028 |
| SSL_CTX_new | | 180,292 | 98,517,060 |
| SSL_do_handshake | | 186,440 | 98,523,208 |
| SSL_new | | 187,028 | 98,523,796 |
| libcrypto.so | 51,888,128 | — | — |
| libnesec.so | 291,766,272 | — | — |

### K5 状态更新
- t2 实测：目标进程 `app_process64` = ELF64 / 0xB7，`AT_PHENT=56`，`/system/lib64` 910 段、`/system/lib` 0 段 ⇒ **本轮目标是纯 64 位，未触发 32 位路径**。
- ARM32 库在 APK 里存在但未加载；K5 作为"通用 compat 缺口"保留，等真遇到 32 位目标再升级为实测。

