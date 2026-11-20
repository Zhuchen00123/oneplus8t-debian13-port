/* SPDX-License-Identifier: GPL-2.0-only */
/* Own-TLS-fixture probes: exactly one uprobe and one uretprobe on SSL_write.
 *
 * Properties enforced here:
 *  - identity (pidns/tgid, uid, group-leader start_boottime against the
 *    run's tick config) is checked *before* any user buffer is touched;
 *  - the SSL object alias is a per-run counter id from a private pointer map,
 *    never the pointer and never an encoding of it;
 *  - a declared length <= 0 never reaches bpf_probe_read_user;
 *  - a positive declared length is capped at 4096 bytes and the record keeps
 *    declared/captured/truncated separately;
 *  - user memory is only read, never written; return values are never changed;
 *  - none of this writes tracefs, cgroup, or any kernel state.
 */
typedef unsigned int u32;
typedef unsigned long long u64;
typedef long long s64;

#define SEC(n) __attribute__((section(n), used))
#define INLINE static __attribute__((always_inline)) inline

/* Legacy map definition style, matching the existing Binder object so the
 * same aya 0.13.1 loader handles both. */
struct bpf_map_def { u32 type, key_size, value_size, max_entries, map_flags, id, pinning; };
struct bpf_map_def SEC("maps") CONFIG   = {2, 4, 8, 32, 0, 0, 0};
struct bpf_map_def SEC("maps") COUNTERS = {2, 4, 8, 16, 0, 0, 0};
struct bpf_map_def SEC("maps") SEQUENCE = {2, 4, 8, 1, 0, 0, 0};
struct bpf_map_def SEC("maps") CALLID   = {2, 4, 8, 1, 0, 0, 0};
struct bpf_map_def SEC("maps") EVENTS   = {27, 0, 0, 1048576, 0, 0, 0};
struct bpf_map_def SEC("maps") INFLIGHT = {1, 8, 24, 1024, 0, 0, 0};
struct bpf_map_def SEC("maps") DEPTH    = {1, 4, 4, 64, 0, 0, 0};
/* Private SSL* -> run-local id map. The pointer is only ever a key; only the
 * small counter id reaches the record. */
struct bpf_map_def SEC("maps") ALIAS    = {1, 8, 4, 32, 0, 0, 0};
struct bpf_map_def SEC("maps") ALIASSEQ = {2, 4, 8, 1, 0, 0, 0};
#define ALIAS_MAX 32

static void *(*lookup)(void *, const void *) = (void *)1;
static long (*update)(void *, const void *, const void *, u64) = (void *)2;
static long (*remove_key)(void *, const void *) = (void *)3;
static u32 (*cpu_id)(void) = (void *)8;
static u64 (*pid_tid)(void) = (void *)14;
static u64 (*uid_gid)(void) = (void *)15;
static u64 (*cgroup_id)(void) = (void *)80;
static long (*read_user)(void *, u32, const void *) = (void *)112;
static long (*read_kernel)(void *, u32, const void *) = (void *)113;
struct pidns_info { u32 pid, tgid; };
static long (*ns_pid_tid)(u64, u64, struct pidns_info *, u32) = (void *)120;
static u64 (*boot_ns)(void) = (void *)125;
static void *(*reserve)(void *, u64, u64) = (void *)131;
static void (*submit)(void *, u64) = (void *)132;
static void *(*current_task)(void) = (void *)158;

/* CONFIG layout (index -> meaning). Offsets are ABI between this object and
 * the Rust collector; both sides assert the same numbers. */
#define CFG_TGID 0
#define CFG_START 1
#define CFG_HZ 2
#define CFG_DEADLINE 3
#define CFG_UID 4
#define CFG_MAX_EVENTS 5
#define CFG_SAMPLE_CAP 6
#define CFG_NS_DEV 7
#define CFG_NS_INO 8
#define CFG_SLOTS 32

#define CNT_ENTRY 0
#define CNT_EXIT 1
#define CNT_SUBMITTED 2
#define CNT_RESERVE_FAILED 3
#define CNT_IDENTITY_REJECTED 4
#define CNT_MAP_FAILED 5
#define CNT_BIRTH_FAILED 6
#define CNT_UNPAIRED 7
#define CNT_SEQ_OVERFLOW 8
#define CNT_DEPTH_OVERFLOW 9
#define CNT_USER_READ_ATTEMPTS 10
#define CNT_USER_READ_BYTES 11
#define CNT_TRUNCATED 12
#define CNT_LEN_INVALID 13
#define CNT_READ_FAILED 14
#define CNT_SLOTS 16

#define SAMPLE_CAP 4096
#define REC_HEADER 112
#define KIND_ENTER 1
#define KIND_EXIT 2

#define FLAG_TRUNCATED 0x1
#define FLAG_LEN_INVALID 0x2
#define FLAG_READ_FAILED 0x4
#define FLAG_USER_READ 0x8
#define FLAG_DEPTH_OVERFLOW 0x10
#define FLAG_UNPAIRED 0x20

struct task_struct {
    struct task_struct *group_leader;
    u64 start_boottime;
    int tgid;
} __attribute__((preserve_access_index));

struct tls_event {
    u64 ts_ns;
    u64 sequence;
    u64 call_id;
    u64 actor_pid_tid;
    u64 actor_start_ns;
    u64 actor_cgroup;
    u64 namespace_pid_tid;
    u64 namespace_inode;
    s64 declared;
    s64 result;
    u32 alias;
    u32 captured;
    u32 kind;
    u32 flags;
    u32 cpu;
    u32 uid;
    u32 depth;
    u32 spare;
};
_Static_assert(sizeof(struct tls_event) == REC_HEADER, "tls event ABI");

struct inflight {
    u64 call_id;
    s64 declared;
    u32 alias;
    u32 depth;
};
_Static_assert(sizeof(struct inflight) == 24, "inflight ABI");

#define READ(dst, src, field) read_kernel(&(dst), sizeof(dst), __builtin_preserve_access_index(&(src)->field))

#if defined(__TARGET_ARCH_x86)
/* x86_64 struct pt_regs as delivered to a uprobe. */
struct pt_regs {
    u64 r15, r14, r13, r12, bp, bx, r11, r10, r9, r8;
    u64 ax, cx, dx, si, di, orig_ax, ip, cs, flags, sp, ss;
};
#define PARM1(r) ((r)->di)
#define PARM2(r) ((r)->si)
#define PARM3(r) ((r)->dx)
#define RETVAL(r) ((r)->ax)
#elif defined(__TARGET_ARCH_arm64)
/* arm64 struct pt_regs: x0..x30, sp, pc, pstate (compile-only evidence). */
struct pt_regs { u64 regs[31]; u64 sp; u64 pc; u64 pstate; };
#define PARM1(r) ((r)->regs[0])
#define PARM2(r) ((r)->regs[1])
#define PARM3(r) ((r)->regs[2])
#define RETVAL(r) ((r)->regs[0])
#else
#error "tls_fixture.bpf.c needs -D__TARGET_ARCH_x86 or -D__TARGET_ARCH_arm64"
#endif

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

INLINE int active(void) {
    u64 end = config(CFG_DEADLINE);
    return end != 0 && boot_ns() < end;
}

INLINE u64 born(struct task_struct *task) {
    struct task_struct *leader = 0;
    u64 ns = 0;
    if (!task) { count(CNT_BIRTH_FAILED); return 0; }
    if (READ(leader, task, group_leader) || !leader) { count(CNT_BIRTH_FAILED); return 0; }
    if (READ(ns, leader, start_boottime)) { count(CNT_BIRTH_FAILED); return 0; }
    return ns;
}

INLINE int selected(void) {
    struct pidns_info info = {};
    if (!active()) return 0;
    if (ns_pid_tid(config(CFG_NS_DEV), config(CFG_NS_INO), &info, sizeof(info))) return 0;
    if ((u64)info.tgid != config(CFG_TGID)) return 0;
    if ((u32)uid_gid() != (u32)config(CFG_UID)) { count(CNT_IDENTITY_REJECTED); return 0; }
    u64 ns = born(current_task());
    if (ns == 0) { count(CNT_IDENTITY_REJECTED); return 0; }
    if (config(CFG_HZ) != 100) { count(CNT_IDENTITY_REJECTED); return 0; }
    if (ns / 10000000ULL != config(CFG_START)) { count(CNT_IDENTITY_REJECTED); return 0; }
    return 1;
}

/* Run-local opaque alias of SSL*: a small counter id handed out on first
 * sight of a pointer. The pointer itself is only a key inside this map; no
 * record and no userspace value ever carries it, and the id is not a function
 * of the pointer (no xor/hash of the address).
 *
 * The counter is a plain read-modify-write: the fixture is single threaded and
 * probes are tgid-scoped, and a collision is refused downstream by
 * alias_generation_ok (one id per object, distinct across directions). */
INLINE u32 alias_of(u64 ptr) {
    u32 zero = 0;
    u32 *known = lookup(&ALIAS, &ptr);
    u64 *seq;
    u64 next;
    u32 id;
    u32 *winner;
    if (known) return *known;
    seq = lookup(&ALIASSEQ, &zero);
    if (!seq) { count(CNT_MAP_FAILED); return 0; }
    next = *seq + 1;
    if (next > (u64)ALIAS_MAX) { count(CNT_MAP_FAILED); return 0; }
    *seq = next;
    id = (u32)next;
    /* 1 == BPF_NOEXIST: the first writer wins, later writers read its id. */
    if (update(&ALIAS, &ptr, &id, 1) != 0) {
        winner = lookup(&ALIAS, &ptr);
        if (!winner) { count(CNT_MAP_FAILED); return 0; }
        return *winner;
    }
    return id;
}

INLINE u64 next_call(void) {
    u32 zero = 0;
    u64 *p = lookup(&CALLID, &zero);
    if (!p) return 0;
    return __sync_fetch_and_add(p, 1) + 1;
}

INLINE u32 depth_get(u32 tid) {
    u32 *p = lookup(&DEPTH, &tid);
    return p ? *p : 0;
}

INLINE void depth_set(u32 tid, u32 value) {
    if (value == 0) {
        remove_key(&DEPTH, &tid);
        return;
    }
    if (update(&DEPTH, &tid, &value, 0)) count(CNT_MAP_FAILED);
}

INLINE u64 inflight_key(u32 tid, u32 depth) {
    return ((u64)tid << 8) | (u64)depth;
}

INLINE struct tls_event *open_event(u32 kind) {
    u32 zero = 0;
    u64 *sequence = lookup(&SEQUENCE, &zero);
    struct tls_event *e;
    u64 seq;
    struct pidns_info info = {};
    if (!sequence) return 0;
    if (!active()) return 0;
    seq = __sync_fetch_and_add(sequence, 1);
    if (seq >= config(CFG_MAX_EVENTS)) { count(CNT_SEQ_OVERFLOW); return 0; }
    e = reserve(&EVENTS, REC_HEADER + SAMPLE_CAP, 0);
    if (!e) { count(CNT_RESERVE_FAILED); return 0; }
    e->ts_ns = boot_ns();
    e->sequence = seq;
    e->call_id = 0;
    e->actor_pid_tid = pid_tid();
    e->actor_start_ns = born(current_task());
    e->actor_cgroup = cgroup_id();
    if (ns_pid_tid(config(CFG_NS_DEV), config(CFG_NS_INO), &info, sizeof(info))) {
        e->namespace_pid_tid = 0;
        e->namespace_inode = 0;
    } else {
        e->namespace_pid_tid = ((u64)info.tgid << 32) | (u64)info.pid;
        e->namespace_inode = config(CFG_NS_INO);
    }
    e->declared = 0;
    e->result = 0;
    e->alias = 0;
    e->captured = 0;
    e->kind = kind;
    e->flags = 0;
    e->cpu = cpu_id();
    e->uid = (u32)uid_gid();
    e->depth = 0;
    e->spare = 0;
    return e;
}

INLINE void close_event(struct tls_event *e) {
    if (!e) return;
    submit(e, 0);
    count(CNT_SUBMITTED);
}

/* Positive-length snapshot, capped, never executed for len <= 0. */
INLINE void fill_payload(struct tls_event *e, const void *buf, s64 declared) {
    /* Everything here is u32 on purpose: the verifier must see the read size
     * as an unsigned value with min 0 (a 64-bit signed range is rejected). */
    u32 cap = (u32)config(CFG_SAMPLE_CAP);
    u32 want;
    long rc;
    if (declared <= 0) {
        e->flags |= FLAG_LEN_INVALID;
        count(CNT_LEN_INVALID);
        return;
    }
    if (cap == 0 || cap > (u32)SAMPLE_CAP) cap = (u32)SAMPLE_CAP;
    want = (u32)declared;
    /* cap is already <= SAMPLE_CAP, so this one comparison covers both the
     * fixed cap and the configured one, and keeps want <= 4096. */
    if (cap < want) {
        want = cap;
        e->flags |= FLAG_TRUNCATED;
        count(CNT_TRUNCATED);
    }
    count(CNT_USER_READ_ATTEMPTS);
    /* The verifier needs the read size to be a non-negative scalar no larger
     * than the sample area.  Reduce through a volatile slot so the compiler
     * cannot fold the mask/clamp away and hand the verifier a 64-bit signed
     * range.  want is already <= 4096, so neither step changes the value. */
    {
        volatile u32 vwant = want;
        u32 n = vwant & 0x1fffu;
        if (n > (u32)SAMPLE_CAP) n = (u32)SAMPLE_CAP;
        rc = read_user((void *)((char *)e + REC_HEADER), n, buf);
    }
    if (rc != 0) {
        e->flags |= FLAG_READ_FAILED;
        count(CNT_READ_FAILED);
        return;
    }
    e->captured = want;
    e->flags |= FLAG_USER_READ;
    counter_add(CNT_USER_READ_BYTES, (u64)want);
}

SEC("uprobe/SSL_write")
int tls_ssl_write_enter(struct pt_regs *ctx) {
    u64 tid64 = pid_tid();
    u32 tid = (u32)tid64;
    u32 depth;
    u32 alias;
    u64 call;
    u64 key;
    s64 declared;
    struct inflight infl = {};
    struct tls_event *e;

    if (!selected()) return 0;
    count(CNT_ENTRY);
    depth = depth_get(tid);
    if (depth >= 8) { count(CNT_DEPTH_OVERFLOW); return 0; }
    declared = (s64)(int)(u32)PARM3(ctx);
    alias = alias_of((u64)PARM1(ctx));
    call = next_call();
    if (call == 0) { count(CNT_MAP_FAILED); return 0; }
    key = inflight_key(tid, depth);
    infl.call_id = call;
    infl.declared = declared;
    infl.alias = alias;
    infl.depth = depth;
    if (update(&INFLIGHT, &key, &infl, 0)) { count(CNT_MAP_FAILED); return 0; }
    depth_set(tid, depth + 1);

    e = open_event(KIND_ENTER);
    if (!e) return 0;
    e->call_id = call;
    e->declared = declared;
    e->alias = alias;
    e->depth = depth;
    fill_payload(e, (const void *)PARM2(ctx), declared);
    close_event(e);
    return 0;
}

SEC("uretprobe/SSL_write")
int tls_ssl_write_return(struct pt_regs *ctx) {
    u64 tid64 = pid_tid();
    u32 tid = (u32)tid64;
    u32 depth;
    u64 key;
    struct inflight *infl;
    struct tls_event *e;
    s64 result;

    if (!selected()) return 0;
    count(CNT_EXIT);
    depth = depth_get(tid);
    if (depth == 0) { count(CNT_UNPAIRED); return 0; }
    depth -= 1;
    key = inflight_key(tid, depth);
    infl = lookup(&INFLIGHT, &key);
    result = (s64)(int)(u32)RETVAL(ctx);
    if (!infl) {
        count(CNT_UNPAIRED);
        depth_set(tid, depth);
        return 0;
    }
    e = open_event(KIND_EXIT);
    if (e) {
        e->call_id = infl->call_id;
        e->declared = infl->declared;
        e->alias = infl->alias;
        e->result = result;
        e->depth = depth;
    }
    remove_key(&INFLIGHT, &key);
    depth_set(tid, depth);
    close_event(e);
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
