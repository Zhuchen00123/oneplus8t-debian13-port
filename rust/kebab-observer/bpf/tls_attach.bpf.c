/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * tls-attach probes: entry/return pairs for SSL_write and SSL_read on ONE
 * userspace library, addressed by *file offset* (an APK-hosted .so inside a
 * ZIP is a plain inode + offset for the kernel, which is exactly how Android
 * maps extractNativeLibs=false libraries).
 *
 * Two library slots ("tags") are compiled in, so one session can carry an
 * APK-hosted libssl and a plain system BoringSSL at the same time; every event
 * carries its slot so the report can name which stack produced a call.
 *
 * Properties enforced here:
 *  - identity is proven *before* any user buffer is touched: group-leader
 *    start_boottime against the run's tick config, tgid inside the target's
 *    own pid namespace, optional cgroup id and uid;
 *  - a declared length <= 0 never reaches bpf_probe_read_user;
 *  - a positive length is capped at 4096 and declared/captured are separate;
 *  - user memory is only read, never written; return values are never changed;
 *  - no tracefs, no cgroup write, no kernel state change.
 */
typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long long u64;
typedef long long s64;

#define SEC(n) __attribute__((section(n), used))
#define INLINE static __attribute__((always_inline)) inline

struct bpf_map_def { u32 type, key_size, value_size, max_entries, map_flags, id, pinning; };
struct bpf_map_def SEC("maps") CONFIG   = {2, 4, 8, 32, 0, 0, 0};
struct bpf_map_def SEC("maps") COUNTERS = {2, 4, 8, 16, 0, 0, 0};
struct bpf_map_def SEC("maps") SEQUENCE = {2, 4, 8, 1, 0, 0, 0};
struct bpf_map_def SEC("maps") CALLID   = {2, 4, 8, 1, 0, 0, 0};
struct bpf_map_def SEC("maps") EVENTS   = {27, 0, 0, 1048576, 0, 0, 0};
struct bpf_map_def SEC("maps") DEPTH    = {1, 8, 8, 512, 0, 0, 0};
/* (tid, direction, depth) -> the call's entry state, so the return probe can
 * name the call and read the response buffer the caller passed in. */
struct bpf_map_def SEC("maps") INFLIGHT = {1, 8, 32, 512, 0, 0, 0};

static void *(*lookup)(void *, const void *) = (void *)1;
static long (*update)(void *, const void *, const void *, u64) = (void *)2;
static long (*remove_key)(void *, const void *) = (void *)3;
static u32 (*cpu_id)(void) = (void *)8;
static u64 (*pid_tid)(void) = (void *)14;
static u64 (*uid_gid)(void) = (void *)15;
static long (*get_comm)(void *, u32) = (void *)16;
static u64 (*cgroup_id)(void) = (void *)80;
static long (*read_user)(void *, u32, const void *) = (void *)112;
static long (*read_kernel)(void *, u32, const void *) = (void *)113;
struct pidns_info { u32 pid, tgid; };
static long (*ns_pid_tid)(u64, u64, struct pidns_info *, u32) = (void *)120;
static u64 (*boot_ns)(void) = (void *)125;
static void *(*reserve)(void *, u64, u64) = (void *)131;
static void (*submit)(void *, u64) = (void *)132;
static void (*discard)(void *, u64) = (void *)133;
static void *(*current_task)(void) = (void *)158;

/* CONFIG layout; ABI with kebab_observer::tls_attach_core::CFG_*. */
#define CFG_TGID 0     /* tgid inside the target pid namespace */
#define CFG_START 1    /* /proc/<pid>/stat starttime, in clock ticks */
#define CFG_HZ 2       /* AT_CLKTCK */
#define CFG_DEADLINE 3 /* BOOTTIME ns after which the gate closes */
#define CFG_UID 4      /* 0xffffffff disables the uid check */
#define CFG_MAX_EVENTS 5
#define CFG_SAMPLE_CAP 6
#define CFG_NS_DEV 7
#define CFG_NS_INO 8
#define CFG_CGROUP 9   /* 0 disables the cgroup check */

/* COUNTERS layout. */
#define CNT_ENTER_WRITE 0
#define CNT_EXIT_WRITE 1
#define CNT_ENTER_READ 2
#define CNT_EXIT_READ 3
#define CNT_SUBMITTED 4
#define CNT_RESERVE_FAILED 5
#define CNT_IDENTITY_REJECTED 6
#define CNT_MAP_FAILED 7
#define CNT_EVENT_CAPPED 8
#define CNT_DEPTH_OVERFLOW 9
#define CNT_USER_READ 10
#define CNT_USER_READ_BYTES 11
#define CNT_USER_READ_FAILED 12
#define CNT_LEN_INVALID 13
#define CNT_TRUNCATED 14
#define CNT_UNPAIRED 15

#define KIND_ENTER 1
#define KIND_EXIT 2
#define DIR_WRITE 1
#define DIR_READ 2

#define FLAG_TRUNCATED 0x1
#define FLAG_LEN_INVALID 0x2
#define FLAG_READ_FAILED 0x4
#define FLAG_USER_READ 0x8
#define FLAG_DEPTH_OVERFLOW 0x10
#define FLAG_UNPAIRED 0x20
#define FLAG_PAYLOAD_ON_EXIT 0x40

#define MAX_DEPTH 8
#define TA_HEADER 144
#define SAMPLE_CAP 4096
#define TA_RECORD (TA_HEADER + SAMPLE_CAP)

struct task_struct {
    struct task_struct *group_leader;
    u64 start_boottime;
} __attribute__((preserve_access_index));

#if defined(__TARGET_ARCH_x86)
struct pt_regs {
    u64 r15, r14, r13, r12, bp, bx, r11, r10, r9, r8;
    u64 ax, cx, dx, si, di, orig_ax, ip, cs, flags, sp, ss;
};
#define PARM1(r) ((r)->di)
#define PARM2(r) ((r)->si)
#define PARM3(r) ((r)->dx)
#define RETVAL(r) ((r)->ax)
#elif defined(__TARGET_ARCH_arm64)
struct pt_regs { u64 regs[31]; u64 sp; u64 pc; u64 pstate; };
#define PARM1(r) ((r)->regs[0])
#define PARM2(r) ((r)->regs[1])
#define PARM3(r) ((r)->regs[2])
#define RETVAL(r) ((r)->regs[0])
#else
#error "tls_attach.bpf.c needs -D__TARGET_ARCH_x86 or -D__TARGET_ARCH_arm64"
#endif

struct ta_event {
    u64 ts_ns;        /* 0   BOOTTIME ns */
    u64 sequence;     /* 8   reservation-order serial */
    u64 call_id;      /* 16  entry/return pairing id */
    u64 ssl;          /* 24  raw SSL*, userspace turns it into an opaque id */
    u64 start_ns;     /* 32  group-leader start_boottime */
    u64 cgroup_id;    /* 40 */
    u64 ns_pid_tid;   /* 48  (tgid << 32) | tid inside the target namespace */
    s64 declared;     /* 56  num argument of the call */
    s64 result;       /* 64  return value (exit records) */
    u64 buf;          /* 72  raw user buffer, userspace never emits it */
    u32 captured;     /* 80  valid payload bytes */
    u32 kind;         /* 84  KIND_ENTER | KIND_EXIT */
    u32 flags;        /* 88  FLAG_* */
    u32 cpu;          /* 92 */
    u32 uid;          /* 96 */
    u32 depth;        /* 100 nesting depth of this call */
    u32 direction;    /* 104 DIR_WRITE | DIR_READ */
    u32 tag;          /* 108 library slot */
    u32 tid;          /* 112 global tid */
    u32 tgid;         /* 116 global tgid */
    u8 comm[16];      /* 120 */
    u8 pad[8];        /* 136 */
    u8 payload[SAMPLE_CAP];
};
_Static_assert(sizeof(struct ta_event) == TA_RECORD, "ta event ABI");

struct inflight {
    u64 buf;
    u64 call_id;
    u64 ssl;
    s64 declared;
};

INLINE u64 config(u32 index) {
    u64 *p = lookup(&CONFIG, &index);
    return p ? *p : 0;
}

INLINE void count(u32 index) {
    u64 *p = lookup(&COUNTERS, &index);
    if (p) __sync_fetch_and_add(p, 1);
}

INLINE void counter_add(u32 index, u64 value) {
    u64 *p = lookup(&COUNTERS, &index);
    if (p) __sync_fetch_and_add(p, value);
}

/* Group-leader start time in BOOTTIME ns, read through CO-RE. */
INLINE u64 birth(void) {
    struct task_struct *task = current_task();
    struct task_struct *leader = 0;
    u64 ns = 0;
    if (!task) { count(CNT_MAP_FAILED); return 0; }
    if (read_kernel(&leader, sizeof(leader), __builtin_preserve_access_index(&task->group_leader)) || !leader) {
        count(CNT_MAP_FAILED);
        return 0;
    }
    if (read_kernel(&ns, sizeof(ns), __builtin_preserve_access_index(&leader->start_boottime))) {
        count(CNT_MAP_FAILED);
        return 0;
    }
    return ns;
}

/*
 * Identity gate.  The same inode can be executed by unrelated processes when a
 * system library is the target, so the tgid *inside the target's namespace*
 * plus the group-leader start time carry the identity, and the optional cgroup
 * id and uid stay as cheap extra conditions.
 */
INLINE int gate(void) {
    struct pidns_info info = {};
    u64 deadline = config(CFG_DEADLINE);
    u64 hz = config(CFG_HZ);
    u64 start_ns;
    u64 dev = config(CFG_NS_DEV);
    u64 ino = config(CFG_NS_INO);
    u64 cg = config(CFG_CGROUP);
    u64 want_uid = config(CFG_UID);
    if (!deadline || boot_ns() >= deadline) return 0;
    if (!hz || !dev || !ino) { count(CNT_IDENTITY_REJECTED); return 0; }
    start_ns = birth();
    if (!start_ns || start_ns / (1000000000 / hz) != config(CFG_START)) {
        count(CNT_IDENTITY_REJECTED);
        return 0;
    }
    if (ns_pid_tid(dev, ino, &info, sizeof(info))) { count(CNT_IDENTITY_REJECTED); return 0; }
    if ((u32)info.tgid != (u32)config(CFG_TGID)) { count(CNT_IDENTITY_REJECTED); return 0; }
    if (cg && cgroup_id() != cg) { count(CNT_IDENTITY_REJECTED); return 0; }
    if (want_uid != 0xffffffffu && (u32)uid_gid() != (u32)want_uid) {
        count(CNT_IDENTITY_REJECTED);
        return 0;
    }
    return 1;
}

INLINE u64 key_depth(u64 tid, u32 direction) {
    return (tid << 8) | ((u64)direction << 4);
}

INLINE u64 key_inflight(u64 tid, u32 direction, u32 depth) {
    return (tid << 16) | ((u64)direction << 8) | (u64)depth;
}

INLINE u32 depth_of(u64 tid, u32 direction) {
    u64 key = key_depth(tid, direction);
    u64 *value = lookup(&DEPTH, &key);
    return value ? (u32)*value : 0;
}

INLINE void set_depth(u64 tid, u32 direction, u32 depth) {
    u64 key = key_depth(tid, direction);
    u64 stored = depth;
    if (update(&DEPTH, &key, &stored, 0)) count(CNT_MAP_FAILED);
}

INLINE struct ta_event *take(u64 *seq) {
    struct ta_event *e = reserve(&EVENTS, sizeof(struct ta_event), 0);
    u32 zero = 0;
    u64 *sequence;
    if (!e) { count(CNT_RESERVE_FAILED); return 0; }
    sequence = lookup(&SEQUENCE, &zero);
    if (!sequence) {
        count(CNT_MAP_FAILED);
        discard(e, 0);
        return 0;
    }
    *seq = __sync_fetch_and_add(sequence, 1);
    if (*seq >= config(CFG_MAX_EVENTS)) {
        count(CNT_EVENT_CAPPED);
        discard(e, 0);
        return 0;
    }
    return e;
}

INLINE void fill(struct ta_event *e, u64 seq, u32 kind, u32 direction, u32 tag, u32 depth) {
    u64 pid_tid_value = pid_tid();
    struct pidns_info info = {};
    u64 dev = config(CFG_NS_DEV);
    u64 ino = config(CFG_NS_INO);
    e->ts_ns = boot_ns();
    e->sequence = seq;
    e->kind = kind;
    e->direction = direction;
    e->tag = tag;
    e->depth = depth;
    e->flags = 0;
    e->captured = 0;
    e->declared = 0;
    e->result = 0;
    e->buf = 0;
    e->ssl = 0;
    e->cpu = cpu_id();
    e->uid = (u32)uid_gid();
    e->cgroup_id = cgroup_id();
    e->tid = (u32)pid_tid_value;
    e->tgid = (u32)(pid_tid_value >> 32);
    e->start_ns = birth();
    e->ns_pid_tid = 0;
    if (dev && ino && !ns_pid_tid(dev, ino, &info, sizeof(info))) {
        e->ns_pid_tid = ((u64)info.tgid << 32) | info.pid;
    }
    e->comm[0] = 0;
    get_comm(e->comm, sizeof(e->comm));
    e->pad[0] = 0;
    e->pad[1] = 0;
    e->pad[2] = 0;
    e->pad[3] = 0;
    e->pad[4] = 0;
    e->pad[5] = 0;
    e->pad[6] = 0;
    e->pad[7] = 0;
}

/* Bounded user read of the call argument / response buffer. */
INLINE u32 sample(struct ta_event *e, u64 buf, s64 declared) {
    s64 cap = config(CFG_SAMPLE_CAP);
    u32 want;
    long rc;
    if (cap <= 0) return 0;
    if (cap > SAMPLE_CAP) cap = SAMPLE_CAP;
    if (declared <= 0) {
        e->flags |= FLAG_LEN_INVALID;
        count(CNT_LEN_INVALID);
        return 0;
    }
    /* Clamp first, then read: the helper's size argument has to be provably
     * bounded for the verifier, not merely small at run time, and the bound has
     * to live in a genuine u32 so no signed range leaks into the helper. */
    want = (u32)declared;
    if (declared > cap) {
        e->flags |= FLAG_TRUNCATED;
        count(CNT_TRUNCATED);
        want = (u32)cap;
    }
    if (want > (u32)SAMPLE_CAP) want = (u32)SAMPLE_CAP;
    if (want == 0) return 0;
    counter_add(CNT_USER_READ, 1);
    /* Reduce through a volatile slot so the compiler cannot fold the mask or the
     * clamp away and hand the verifier a 64-bit signed range: want is already
     * <= 4096, so neither step changes the value. */
    {
        volatile u32 vwant = want;
        u32 n = vwant & 0x1fffu;
        if (n > (u32)SAMPLE_CAP) n = (u32)SAMPLE_CAP;
        rc = read_user(e->payload, n, (const void *)buf);
    }
    if (rc != 0) {
        e->flags |= FLAG_READ_FAILED;
        count(CNT_USER_READ_FAILED);
        return 0;
    }
    e->flags |= FLAG_USER_READ;
    counter_add(CNT_USER_READ_BYTES, (u64)want);
    return want;
}

INLINE int enter(struct pt_regs *ctx, u32 direction, u32 tag) {
    u64 tid = pid_tid() & 0xffffffffu;
    u64 call_id;
    u32 zero = 0;
    u32 depth;
    u64 *call_sequence;
    u64 seq = 0;
    struct ta_event *e;
    u64 buf;
    s64 declared;
    struct inflight state;
    if (!gate()) return 0;
    depth = depth_of(tid, direction);
    call_sequence = lookup(&CALLID, &zero);
    if (!call_sequence) { count(CNT_MAP_FAILED); return 0; }
    call_id = __sync_fetch_and_add(call_sequence, 1);
    buf = (u64)PARM2(ctx);
    declared = (s64)(int)(u32)PARM3(ctx);
    if (depth < MAX_DEPTH) {
        u64 key = key_inflight(tid, direction, depth);
        state.buf = buf;
        state.call_id = call_id;
        state.ssl = (u64)PARM1(ctx);
        state.declared = declared;
        if (update(&INFLIGHT, &key, &state, 0)) count(CNT_MAP_FAILED);
        set_depth(tid, direction, depth + 1);
    } else {
        count(CNT_DEPTH_OVERFLOW);
    }
    if ((e = take(&seq)) != 0) {
        fill(e, seq, KIND_ENTER, direction, tag, depth);
        e->call_id = call_id;
        e->ssl = (u64)PARM1(ctx);
        e->declared = declared;
        e->buf = buf;
        if (depth >= MAX_DEPTH) e->flags |= FLAG_DEPTH_OVERFLOW;
        if (direction == DIR_WRITE) {
            e->captured = sample(e, buf, declared);
        } else {
            e->flags |= FLAG_PAYLOAD_ON_EXIT;
            if (declared <= 0) {
                e->flags |= FLAG_LEN_INVALID;
                count(CNT_LEN_INVALID);
            }
        }
        submit(e, 0);
        count(CNT_SUBMITTED);
        if (direction == DIR_WRITE) count(CNT_ENTER_WRITE); else count(CNT_ENTER_READ);
    }
    return 0;
}

INLINE int exit_call(struct pt_regs *ctx, u32 direction, u32 tag) {
    u64 tid = pid_tid() & 0xffffffffu;
    u32 depth = depth_of(tid, direction);
    u32 inner = depth ? depth - 1 : 0;
    u64 seq = 0;
    s64 result = (s64)(int)(u32)RETVAL(ctx);
    struct ta_event *e;
    struct inflight *state = 0;
    if (!gate()) return 0;
    if (depth) {
        u64 key = key_inflight(tid, direction, inner);
        state = lookup(&INFLIGHT, &key);
        if (state) remove_key(&INFLIGHT, &key);
    }
    if ((e = take(&seq)) != 0) {
        fill(e, seq, KIND_EXIT, direction, tag, inner);
        e->result = result;
        if (state) {
            e->call_id = state->call_id;
            e->ssl = state->ssl;
            e->declared = state->declared;
            e->buf = state->buf;
            if (direction == DIR_READ) e->captured = sample(e, state->buf, result);
        } else {
            e->flags |= FLAG_UNPAIRED;
            count(CNT_UNPAIRED);
        }
        submit(e, 0);
        count(CNT_SUBMITTED);
        if (direction == DIR_WRITE) count(CNT_EXIT_WRITE); else count(CNT_EXIT_READ);
    }
    set_depth(tid, direction, depth ? inner : 0);
    return 0;
}

#define GEN_ENTER(name, direction, tag) \
    SEC("uprobe/" #name) int name(struct pt_regs *ctx) { return enter(ctx, direction, tag); }
#define GEN_EXIT(name, direction, tag) \
    SEC("uretprobe/" #name) int name(struct pt_regs *ctx) { return exit_call(ctx, direction, tag); }

GEN_ENTER(ta_write_enter_0, DIR_WRITE, 0)
GEN_EXIT(ta_write_exit_0, DIR_WRITE, 0)
GEN_ENTER(ta_read_enter_0, DIR_READ, 0)
GEN_EXIT(ta_read_exit_0, DIR_READ, 0)
GEN_ENTER(ta_write_enter_1, DIR_WRITE, 1)
GEN_EXIT(ta_write_exit_1, DIR_WRITE, 1)
GEN_ENTER(ta_read_enter_1, DIR_READ, 1)
GEN_EXIT(ta_read_exit_1, DIR_READ, 1)

char LICENSE[] SEC("license") = "GPL";
