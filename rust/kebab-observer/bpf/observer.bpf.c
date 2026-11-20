/* SPDX-License-Identifier: GPL-2.0-only */
/* Fixed metadata schema; CO-RE field access, no user memory or user probes. */
typedef unsigned int u32;
typedef unsigned long long u64;
typedef long long s64;
#define SEC(s) __attribute__((section(s), used))
#define INLINE static __attribute__((always_inline)) inline
struct bpf_map_def { u32 type, key_size, value_size, max_entries, map_flags, id, pinning; };
struct bpf_map_def SEC("maps") CONFIG = {2, 4, 8, 12, 0, 0, 0};
struct bpf_map_def SEC("maps") TARGET_ID = {2, 4, 8, 2, 0, 0, 0};
struct bpf_map_def SEC("maps") COUNTERS = {2, 4, 8, 8, 0, 0, 0};
struct bpf_map_def SEC("maps") SEQUENCE = {2, 4, 8, 1, 0, 0, 0};
struct bpf_map_def SEC("maps") EVENTS = {27, 0, 0, 1048576, 0, 0, 0};
struct bpf_map_def SEC("maps") INFLIGHT = {1, 8, 8, 1024, 0, 0, 0};
struct bpf_map_def SEC("maps") TRANSACTIONS = {1, 4, 8, 1024, 0, 0, 0};
static void *(*lookup)(void *, const void *) = (void *)1;
static long (*update)(void *, const void *, const void *, u64) = (void *)2;
static long (*remove_key)(void *, const void *) = (void *)3;
static u32 (*cpu_id)(void) = (void *)8;
static u64 (*pid_tid)(void) = (void *)14;
static u64 (*uid_gid)(void) = (void *)15;
static u64 (*cgroup_id)(void) = (void *)80;
static long (*read_kernel)(void *, u32, const void *) = (void *)113;
struct pidns_info { u32 pid, tgid; };
static long (*ns_pid_tid)(u64, u64, struct pidns_info *, u32) = (void *)120;
static u64 (*boot_ns)(void) = (void *)125;
static void *(*reserve)(void *, u64, u64) = (void *)131;
static void (*submit)(void *, u64) = (void *)132;
static void *(*current_task)(void) = (void *)158;
struct task_struct {
    struct task_struct *group_leader;
    u64 start_boottime;
    int tgid;
} __attribute__((preserve_access_index));
struct binder_proc { int pid; struct task_struct *tsk; } __attribute__((preserve_access_index));
struct binder_transaction {
    int debug_id;
    struct binder_proc *to_proc;
    unsigned int code, flags;
} __attribute__((preserve_access_index));
struct raw_ctx { u64 args[3]; };
struct event {
    u64 ts_ns, actor_pid_tid, actor_start_ns, actor_cgroup, sequence, namespace_pid_tid, namespace_inode;
    s64 operation, result;
    u32 kind, cpu, uid, code, flags, extra;
};
_Static_assert(sizeof(struct event) == 96, "event ABI");
#define READ(dst, src, field) read_kernel(&(dst), sizeof(dst), __builtin_preserve_access_index(&(src)->field))
INLINE u64 config(u32 index) {
    u64 *p = lookup(&CONFIG, &index);
    return p ? *p : 0;
}
INLINE void count(u32 index) {
    u64 *p = lookup(&COUNTERS, &index);
    if (p) __sync_fetch_and_add(p, 1);
}
INLINE int active(void) {
    u64 end = config(3);
    return end && boot_ns() < end;
}
INLINE u64 birth(struct task_struct *task) {
    struct task_struct *leader = 0;
    u64 ns = 0;
    if (!task || READ(leader, task, group_leader) || !leader || READ(ns, leader, start_boottime)) {
        count(5); return 0;
    }
    return ns;
}
INLINE int matches(struct task_struct *task) {
    u32 zero = 0, one = 1;
    u64 *global = lookup(&TARGET_ID, &zero), *start = lookup(&TARGET_ID, &one);
    int tgid = 0;
    if (!global || !start || !*global || !*start || !task || READ(tgid, task, tgid) || (u32)tgid != *global) return 0;
    u64 ns = birth(task);
    if (!ns || ns != *start) { count(3); return 0; }
    return 1;
}
INLINE int selected(void) {
    if (!active()) return 0;
    struct pidns_info info = {};
    if (ns_pid_tid(config(7), config(8), &info, sizeof(info)) || info.tgid != config(0)) return 0;
    if ((u32)uid_gid() != config(4)) { count(3); return 0; }
    u64 ns = birth(current_task());
    if (!ns || config(2) != 100 || ns / 10000000 != config(1)) { count(3); return 0; }
    u32 zero = 0, one = 1;
    u64 *global = lookup(&TARGET_ID, &zero), *start = lookup(&TARGET_ID, &one);
    if (!global || !start) return 0;
    if (!*global) { *start = ns; *global = pid_tid() >> 32; }
    return matches(current_task());
}
INLINE void emit(u32 kind, s64 op, s64 result, u32 code, u32 flags, u32 extra) {
    if (!active()) return;
    u32 zero = 0;
    u64 *sequence = lookup(&SEQUENCE, &zero);
    if (!sequence) return;
    u64 seq = __sync_fetch_and_add(sequence, 1);
    if (seq >= config(6)) { count(7); return; }
    struct event *e = reserve(&EVENTS, sizeof(*e), 0);
    if (!e) { count(2); return; }
    e->ts_ns = boot_ns(); e->actor_pid_tid = pid_tid();
    e->actor_start_ns = birth(current_task()); e->actor_cgroup = cgroup_id();
    e->sequence = seq; e->operation = op; e->result = result;
    struct pidns_info info = {};
    if (ns_pid_tid(config(7), config(8), &info, sizeof(info))) {
        e->namespace_pid_tid = 0; e->namespace_inode = 0;
    } else {
        e->namespace_pid_tid = ((u64)info.tgid << 32) | info.pid;
        e->namespace_inode = config(8);
    }
    e->kind = kind; e->cpu = cpu_id(); e->uid = (u32)uid_gid();
    e->code = code; e->flags = flags; e->extra = extra;
    submit(e, 0); count(1);
}
SEC("raw_tracepoint/sys_enter") int observer_enter(struct raw_ctx *ctx) {
    if (!selected()) return 0;
    u64 key = pid_tid(), id = ctx->args[1];
    count(0);
    if (update(&INFLIGHT, &key, &id, 0)) count(4);
    emit(1, id, 0, 0, 0, 0);
    return 0;
}
SEC("raw_tracepoint/sys_exit") int observer_exit(struct raw_ctx *ctx) {
    if (!selected()) return 0;
    u64 key = pid_tid(), *id = lookup(&INFLIGHT, &key);
    s64 operation = id ? (s64)*id : -1;
    if (!id) count(6);
    emit(2, operation, ctx->args[1], 0, 0, 0);
    remove_key(&INFLIGHT, &key);
    return 0;
}
#ifndef OBSERVER_SYSCALL_ONLY
SEC("raw_tracepoint/binder_transaction") int observer_binder(struct raw_ctx *ctx) {
    if (!active()) return 0;
    struct binder_transaction *t = (void *)ctx->args[1];
    struct binder_proc *to = 0;
    struct task_struct *target = 0;
    int dest = 0, id = 0;
    u32 code = 0, flags = 0;
    if (!t || READ(to, t, to_proc)) { count(5); return 0; }
    if (to && (READ(target, to, tsk) || READ(dest, to, pid))) { count(5); return 0; }
    if (!selected() && !matches(target)) return 0;
    if (READ(id, t, debug_id) || READ(code, t, code) || READ(flags, t, flags)) { count(5); return 0; }
    u64 ts = boot_ns();
    if (update(&TRANSACTIONS, &id, &ts, 0)) count(4);
    emit(3, id, dest, code, flags, ctx->args[0] != 0);
    return 0;
}
SEC("raw_tracepoint/binder_transaction_received") int observer_received(struct raw_ctx *ctx) {
    if (!active()) return 0;
    struct binder_transaction *t = (void *)ctx->args[0];
    int id = 0;
    if (!t || READ(id, t, debug_id)) { count(5); return 0; }
    if (!selected() && !lookup(&TRANSACTIONS, &id)) return 0;
    emit(4, id, 0, 0, 0, 0);
    remove_key(&TRANSACTIONS, &id);
    return 0;
}
#endif
char LICENSE[] SEC("license") = "GPL";
