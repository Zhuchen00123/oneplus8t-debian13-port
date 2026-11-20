/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * kebab-observer "track": shipper for an externally named target
 * (Waydroid app uid/cgroup), gated in-kernel by cgroup id + uid, and
 * refined in userspace by tgid/start_ticks.
 *
 * Fixed 256-byte records = 128-byte header + 128-byte path window, so every
 * record has one ABI and the ring buffer never has to guess a size.
 *
 * Evidence boundaries of this object:
 *   - reads user memory (openat path, bounded 128 B) -- unlike the TLS
 *     fixture object, which never reads user memory;
 *   - CO-RE field access for task_struct / binder_proc / binder_transaction;
 *     pt_regs is read at constant ABI offsets instead of CO-RE, because the
 *     arm64 pt_regs register file lives inside anonymous unions;
 *   - nothing here writes to tracefs and nothing here is a security control.
 */
typedef unsigned int u32;
typedef unsigned long long u64;
typedef long long s64;
typedef unsigned char u8;
#define SEC(s) __attribute__((section(s), used))
#define INLINE static __attribute__((always_inline)) inline
struct bpf_map_def { u32 type, key_size, value_size, max_entries, map_flags, id, pinning; };
/* Array(4->8), 16 slots: see CFG_* below. */
struct bpf_map_def SEC("maps") CONFIG = {2, 4, 8, 16, 0, 0, 0};
/* Array(4->8), 16 slots: see CNT_* below. */
struct bpf_map_def SEC("maps") COUNTERS = {2, 4, 8, 16, 0, 0, 0};
struct bpf_map_def SEC("maps") SEQUENCE = {2, 4, 8, 1, 0, 0, 0};
struct bpf_map_def SEC("maps") EVENTS = {27, 0, 0, 1048576, 0, 0, 0};
/* (tgid<<32)|tid -> syscall nr, so sys_exit can name the syscall it returns from. */
struct bpf_map_def SEC("maps") INFLIGHT = {1, 8, 8, 1024, 0, 0, 0};

static void *(*lookup)(void *, const void *) = (void *)1;
static long (*update)(void *, const void *, const void *, u64) = (void *)2;
static long (*remove_key)(void *, const void *) = (void *)3;
static u32 (*cpu_id)(void) = (void *)8;
static long (*get_comm)(void *, u32) = (void *)16;
static u64 (*pid_tid)(void) = (void *)14;
static u64 (*uid_gid)(void) = (void *)15;
static u64 (*cgroup_id)(void) = (void *)80;
static long (*read_kernel)(void *, u32, const void *) = (void *)113;
static long (*read_user_str)(void *, u32, const void *) = (void *)114;
struct pidns_info { u32 pid, tgid; };
static long (*ns_pid_tid)(u64, u64, struct pidns_info *, u32) = (void *)120;
static u64 (*boot_ns)(void) = (void *)125;
static void *(*reserve)(void *, u64, u64) = (void *)131;
static void (*submit)(void *, u64) = (void *)132;
static void (*discard)(void *, u64) = (void *)133;
static void *(*current_task)(void) = (void *)158;

struct task_struct {
    struct task_struct *group_leader;
    u64 start_boottime;
    int tgid;
} __attribute__((preserve_access_index));
struct binder_proc { int pid; } __attribute__((preserve_access_index));
struct binder_thread { int pid; } __attribute__((preserve_access_index));
struct binder_transaction {
    int debug_id;
    struct binder_proc *to_proc;
    struct binder_thread *to_thread;
    unsigned int code, flags;
} __attribute__((preserve_access_index));

/* CONFIG indices; mirrored by kebab_observer::track_core::CFG_*. */
#define CFG_CGROUP 0
#define CFG_UID 1
#define CFG_TGID 2
#define CFG_DEADLINE 3
#define CFG_CAP 4
#define CFG_PIDNS_DEV 5
#define CFG_PIDNS_INO 6
#define CFG_EXCLUDE_TGID 7
/* COUNTERS indices; mirrored by kebab_observer::track_core::CNT_*. */
#define CNT_GATE 0
#define CNT_SUBMITTED 1
#define CNT_LOST 2
#define CNT_TRUNCATED 3
#define CNT_MAP_FAILED 4
#define CNT_READ_FAILED 5
#define CNT_USER_READ_FAILED 6
#define CNT_OPENAT 7
#define CNT_BINDER 8
#define CNT_PATH_LONG 9
#define CNT_SYS_ENTER 10
#define CNT_SYS_EXIT 11
/* kind values; mirrored by kebab_observer::track_core::KIND_*. */
#define KIND_SYS_ENTER 1
#define KIND_SYS_EXIT 2
#define KIND_BINDER_TX 3
#define KIND_BINDER_RX 4
#define KIND_OPENAT 5
#define TRACK_PATH_MAX 128
#define TRACK_EVENT_BYTES 256

struct raw_ctx { u64 args[3]; };
struct track_event {
    u64 ts_ns;        /* 0   bpf_ktime_get_boot_ns */
    u64 pid_tid;      /* 8   global (tgid << 32) | tid */
    u64 start_ns;     /* 16  leader start_boottime, ns since boot */
    u64 cgroup_id;    /* 24  bpf_get_current_cgroup_id */
    u64 sequence;     /* 32  reservation-order serial */
    u64 ns_pid_tid;   /* 40  (tgid << 32) | tid in the target pidns, 0 = unknown */
    u64 arg0;         /* 48  syscall arg0 / binder debug_id */
    s64 arg1;         /* 56  syscall arg1 / binder to_proc pid */
    s64 arg2;         /* 64  syscall arg2 / binder code */
    s64 result;       /* 72  syscall return value */
    u32 kind;         /* 80 */
    u32 cpu;          /* 84 */
    u32 uid;          /* 88 */
    u32 nr;           /* 92  syscall nr, 0xffffffff when unknown */
    u32 flags;        /* 96  binder transaction flags */
    u32 path_len;     /* 100 bytes incl. NUL, 0 when no path was read */
    u8 comm[16];      /* 104 bpf_get_current_comm */
    u32 reply;        /* 120 binder: 1 when the transaction is a reply */
    u32 to_thread;    /* 124 binder: destination thread pid, 0 when none */
    u8 path[TRACK_PATH_MAX]; /* 128 openat path window */
};
_Static_assert(sizeof(struct track_event) == TRACK_EVENT_BYTES, "track event ABI");

#define READ(dst, src, field) read_kernel(&(dst), sizeof(dst), __builtin_preserve_access_index(&(src)->field))

/*
 * struct pt_regs syscall-argument offsets, read at the frozen syscall ABI
 * offsets instead of through CO-RE (arm64 keeps regs[31] inside an anonymous
 * union, x86_64 pt_regs has a fixed member order).  0/8/16 = x0/x1/x2 on
 * arm64; 112/104/96 = di/si/dx on x86_64 (r15..ss order).
 */
#if defined(__TARGET_ARCH_x86)
#define NR_OPENAT 257
#define PT_ARG0 112
#define PT_ARG1 104
#define PT_ARG2 96
#elif defined(__TARGET_ARCH_arm64)
#define NR_OPENAT 56
#define PT_ARG0 0
#define PT_ARG1 8
#define PT_ARG2 16
#else
#error "track.bpf.c needs -D__TARGET_ARCH_x86 or -D__TARGET_ARCH_arm64"
#endif

INLINE u64 config(u32 index) {
    u64 *p = lookup(&CONFIG, &index);
    return p ? *p : 0;
}

INLINE void count(u32 index) {
    u64 *p = lookup(&COUNTERS, &index);
    if (p) __sync_fetch_and_add(p, 1);
}

INLINE int read_reg(u64 *dst, u64 regs, u32 offset) {
    *dst = 0;
    return read_kernel(dst, sizeof(*dst), (const void *)(regs + offset)) != 0;
}

INLINE u64 birth(void) {
    struct task_struct *task = current_task();
    struct task_struct *leader = 0;
    u64 ns = 0;
    if (!task || READ(leader, task, group_leader) || !leader) { count(CNT_MAP_FAILED); return 0; }
    if (READ(ns, leader, start_boottime)) { count(CNT_MAP_FAILED); return 0; }
    return ns;
}

/*
 * The optional tgid narrowing is compared in the *target's own pid namespace*:
 * a /proc pid read by the collector and task->tgid are only the same number when
 * both live in the same namespace (WSL runs the distro inside one, Waydroid
 * puts the app in one), so bpf_get_ns_current_pid_tgid is the only comparison
 * that holds in both worlds.
 */
INLINE int ns_tgid_ok(u64 want) {
    struct pidns_info info = {};
    u64 dev = config(CFG_PIDNS_DEV);
    u64 ino = config(CFG_PIDNS_INO);
    if (!dev || !ino) return 0;
    if (ns_pid_tid(dev, ino, &info, sizeof(info))) return 0;
    return (u32)info.tgid == (u32)want;
}

/*
 * The in-kernel half of the filter: cgroup id AND uid AND the optional tgid,
 * plus the session deadline.  start_ticks is deliberately NOT compared here;
 * it is the userspace half of the filter (one tick granularity, and a
 * mismatch must be reported, not silently dropped).
 */
INLINE int gate(void) {
    u64 deadline = config(CFG_DEADLINE);
    u64 want = config(CFG_TGID);
    u64 myself = config(CFG_EXCLUDE_TGID);
    if (!deadline || boot_ns() >= deadline) return 0;
    if (myself && (pid_tid() >> 32) == myself) return 0; /* never observe the collector */
    if (cgroup_id() != config(CFG_CGROUP)) return 0;
    if ((u32)uid_gid() != (u32)config(CFG_UID)) return 0;
    if (want && !ns_tgid_ok(want)) return 0;
    count(CNT_GATE);
    return 1;
}

INLINE struct track_event *take(u64 *seq) {
    struct track_event *e = reserve(&EVENTS, sizeof(struct track_event), 0);
    u32 zero = 0;
    u64 *sequence;
    if (!e) { count(CNT_LOST); return 0; }
    sequence = lookup(&SEQUENCE, &zero);
    if (!sequence) { discard(e, 0); count(CNT_MAP_FAILED); return 0; }
    *seq = __sync_fetch_and_add(sequence, 1);
    if (*seq >= config(CFG_CAP)) { discard(e, 0); count(CNT_TRUNCATED); return 0; }
    return e;
}

INLINE void fill(struct track_event *e, u64 seq, u32 kind) {
    struct pidns_info info = {};
    u32 zero = 0;
    e->ts_ns = boot_ns();
    e->pid_tid = pid_tid();
    e->start_ns = birth();
    e->cgroup_id = cgroup_id();
    e->sequence = seq;
    e->ns_pid_tid = 0;
    if (config(CFG_PIDNS_DEV) && config(CFG_PIDNS_INO) &&
        !ns_pid_tid(config(CFG_PIDNS_DEV), config(CFG_PIDNS_INO), &info, sizeof(info))) {
        e->ns_pid_tid = ((u64)info.tgid << 32) | info.pid;
    }
    e->arg0 = 0;
    e->arg1 = 0;
    e->arg2 = 0;
    e->result = 0;
    e->kind = kind;
    e->cpu = cpu_id();
    e->uid = (u32)uid_gid();
    e->nr = 0xffffffffu;
    e->flags = 0;
    e->path_len = 0;
    e->comm[0] = 0;
    get_comm(e->comm, sizeof(e->comm));
    e->reply = 0;
    e->to_thread = 0;
    (void)zero;
}

INLINE void push(struct track_event *e) {
    submit(e, 0);
    count(CNT_SUBMITTED);
}

SEC("raw_tracepoint/sys_enter") int track_sys_enter(struct raw_ctx *ctx) {
    u64 key, nr, seq = 0, a0 = 0, a1 = 0, a2 = 0;
    struct track_event *e;
    if (!gate()) return 0;
    nr = ctx->args[1];
    key = pid_tid();
    if (update(&INFLIGHT, &key, &nr, 0)) count(CNT_MAP_FAILED);
    if (nr == NR_OPENAT) return 0; /* track_openat ships the richer record */
    if (read_reg(&a0, ctx->args[0], PT_ARG0) | read_reg(&a1, ctx->args[0], PT_ARG1) |
        read_reg(&a2, ctx->args[0], PT_ARG2)) {
        count(CNT_READ_FAILED);
    }
    if ((e = take(&seq)) != 0) {
        fill(e, seq, KIND_SYS_ENTER);
        e->nr = (u32)nr;
        e->arg0 = a0;
        e->arg1 = (s64)a1;
        e->arg2 = (s64)a2;
        push(e);
        count(CNT_SYS_ENTER);
    }
    return 0;
}

SEC("raw_tracepoint/sys_exit") int track_sys_exit(struct raw_ctx *ctx) {
    u64 key, seq = 0;
    u64 *pending;
    struct track_event *e;
    if (!gate()) return 0;
    key = pid_tid();
    pending = lookup(&INFLIGHT, &key);
    if ((e = take(&seq)) != 0) {
        fill(e, seq, KIND_SYS_EXIT);
        e->nr = pending ? (u32)*pending : 0xffffffffu;
        e->result = (s64)ctx->args[1];
        push(e);
        count(CNT_SYS_EXIT);
    }
    remove_key(&INFLIGHT, &key);
    return 0;
}

SEC("raw_tracepoint/sys_enter") int track_openat(struct raw_ctx *ctx) {
    u64 nr, seq = 0, a0 = 0, a1 = 0, a2 = 0;
    long n;
    struct track_event *e;
    if (!gate()) return 0;
    nr = ctx->args[1];
    if (nr != NR_OPENAT) return 0;
    if (read_reg(&a0, ctx->args[0], PT_ARG0) | read_reg(&a1, ctx->args[0], PT_ARG1) |
        read_reg(&a2, ctx->args[0], PT_ARG2)) {
        count(CNT_READ_FAILED);
        return 0;
    }
    if ((e = take(&seq)) == 0) return 0;
    fill(e, seq, KIND_OPENAT);
    e->nr = (u32)nr;
    e->arg0 = a0;
    e->arg1 = (s64)a1;
    e->arg2 = (s64)a2;
    n = read_user_str(e->path, TRACK_PATH_MAX, (const void *)a1);
    if (n <= 0) {
        e->path_len = 0;
        count(CNT_USER_READ_FAILED);
    } else {
        e->path_len = (u32)n;
        if (n >= TRACK_PATH_MAX) count(CNT_PATH_LONG);
    }
    push(e);
    count(CNT_OPENAT);
    return 0;
}

/* Kernel signature (sources/linux/drivers/android/binder_trace.h):
 *   TRACE_EVENT(binder_transaction,
 *               TP_PROTO(bool reply, struct binder_transaction *t,
 *                        struct binder_node *target_node))
 * so raw args[0] is the reply flag, args[1] is the transaction and args[2] is
 * the target node.  Reading args[0] as a pointer produces garbage (the t4
 * window reported code=0x5F5A5F67 that way). */
SEC("raw_tracepoint/binder_transaction") int track_binder_tx(struct raw_ctx *ctx) {
    u64 reply = ctx->args[0];
    struct binder_transaction *t = (void *)ctx->args[1];
    struct binder_proc *to = 0;
    struct binder_thread *to_thread = 0;
    int dest = 0, id = 0, to_tid = 0;
    u32 code = 0, flags = 0;
    u64 seq = 0;
    struct track_event *e;
    if (!gate()) return 0;
    if (!t || READ(to, t, to_proc)) { count(CNT_READ_FAILED); return 0; }
    if (to && READ(dest, to, pid)) { count(CNT_READ_FAILED); return 0; }
    if (READ(to_thread, t, to_thread)) { count(CNT_READ_FAILED); return 0; }
    if (to_thread && READ(to_tid, to_thread, pid)) { count(CNT_READ_FAILED); return 0; }
    if (READ(id, t, debug_id) || READ(code, t, code) || READ(flags, t, flags)) {
        count(CNT_READ_FAILED);
        return 0;
    }
    if ((e = take(&seq)) == 0) return 0;
    fill(e, seq, KIND_BINDER_TX);
    e->arg0 = (u32)id;
    e->arg1 = (s64)dest;
    e->arg2 = (s64)(u32)code;
    e->flags = flags;
    e->reply = reply != 0 ? 1 : 0;
    e->to_thread = (u32)to_tid;
    push(e);
    count(CNT_BINDER);
    return 0;
}

/* Kernel signature: TRACE_EVENT(binder_transaction_received,
 *   TP_PROTO(struct binder_transaction *t)) -- only one argument, so here
 * args[0] really is the transaction.  Do not "fix" this one to args[1]. */
SEC("raw_tracepoint/binder_transaction_received") int track_binder_rx(struct raw_ctx *ctx) {
    struct binder_transaction *t = (void *)ctx->args[0];
    int id = 0;
    u64 seq = 0;
    struct track_event *e;
    if (!gate()) return 0;
    if (!t || READ(id, t, debug_id)) { count(CNT_READ_FAILED); return 0; }
    if ((e = take(&seq)) == 0) return 0;
    fill(e, seq, KIND_BINDER_RX);
    e->arg0 = (u32)id;
    push(e);
    count(CNT_BINDER);
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
