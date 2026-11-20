# kebab-observer (own TLS fixture observer, first version)

This crate is the **first working version** of an owned real-TLS fixture
observer.  It captures the entry and return of the *classic* `SSL_write`
symbol of the **system OpenSSL** for one **self-owned** C workload, inside
WSL, and nothing else.

It does **not** observe 运动世界, the r5 kernel, Android, or any external
process.  No claim in this file extends to those targets.

## Why it exists

The paused r5/Android work needed an honest answer to "what does an
`SSL_write` entry/return observation actually give us?"  A synthetic fixture
answers that offline: a certificate, a TLS 1.2 handshake over a memory BIO
pair, a frozen request script, and a BPF probe pair that must reproduce exact
counters.  Everything the fixture does is in-process; there is no socket, no
network port, no HTTP parsing, and no `SSL_read` probe.

## Layout

| Path | Role |
| --- | --- |
| `src/tls_core.rs` | Portable decode / pairing / payload-formula / identity helpers.  All of it is unit-tested on Windows. |
| `src/tls_live.rs` | Linux-only runtime: private run dir, fixture spawn, aya load/attach, ring drain, closeout. |
| `src/live.rs` | Pre-existing syscall observer (`inspect-owned`, `fixture`, `fixture-wrong-birth`).  Unchanged semantics. |
| `bpf/tls_fixture.bpf.c` | Two uprobe programs on `SSL_write` (entry + return), identity checks, per-tid depth, inflight map, 1 MiB ring buffer. |
| `bpf/observer.bpf.c` | Pre-existing syscall observer object (still built by verify command 1). |
| `bpf/track.bpf.c` | External-target tracker: cgroup+uid gate, namespace-aware tgid gate, `sys_enter`/`sys_exit`, bounded `openat` path read, Binder transaction pair, 1 MiB ring, 20 000-event cap. |
| `src/track_core.rs` | Portable decode / two-view filter / accounting / CLI parsing for `track`; every rule is unit-tested on Windows. |
| `src/track.rs` | Linux runtime for `track`: target identity proof, object load/attach, ring drain, JSONL session+closeout. |
| `scripts/track-smoke.sh` | Self-target WSL smoke test (creates its own workload; no device, no Waydroid). |
| `fixtures/tls_fixture.c` | The C TLS workload: in-memory handshake and the frozen script. |

## Build

All Rust builds run on Windows in `F:\codexprojects\oneplus8t\rust\kebab-observer`
so that `.cargo/config.toml` (rust-lld) applies:

```
cargo test --locked --offline
cargo clippy --locked --offline --all-targets -- -D warnings
cargo build --locked --offline --release --target x86_64-unknown-linux-musl
cargo build --locked --offline --release --target aarch64-unknown-linux-musl
```

The `track` object has its own two commands (x86_64 for the WSL smoke test,
arm64 for the device):

```
wsl.exe -d Debian --exec /usr/bin/clang-19 -target bpfel -D__TARGET_ARCH_x86 -O2 -g -Wall -Wextra -Werror -c /codexprojects/oneplus8t/rust/kebab-observer/bpf/track.bpf.c -o /codexprojects/oneplus8t/rust/kebab-observer/bpf/track.x86_64.bpf.o
wsl.exe -d Debian --exec /usr/bin/clang-19 -target bpfel -D__TARGET_ARCH_arm64 -O2 -g -Wall -Wextra -Werror -c /codexprojects/oneplus8t/rust/kebab-observer/bpf/track.bpf.c -o /codexprojects/oneplus8t/rust/kebab-observer/bpf/track.arm64.bpf.o
wsl.exe -d Debian -u root --exec /bin/bash /codexprojects/oneplus8t/rust/kebab-observer/scripts/track-smoke.sh
```

The BPF objects and the C workload are built inside WSL (clang-19 / cc) and
the `.o` files are embedded into the binary with
`aya::include_bytes_aligned!`.  The batch closeout prints the FNV-1a 64 of
both the embedded object and the on-disk object it was built from, so a stale
`.o` cannot masquerade as this version.

## Run (the only acceptance entry point)

```
wsl.exe -d Debian -u root --exec /usr/bin/timeout --signal=TERM --kill-after=2s 60s \
  /codexprojects/oneplus8t/rust/kebab-observer/target/x86_64-unknown-linux-musl/release/kebab-observer \
  tls-fixture-check
```

Exit codes: `0` pass, `1` fail, `2` usage rejected, `3` blocked by the host,
`4` incomplete batch (budget exhausted).

The subcommand accepts **no arguments**.  There is no way to name an external
PID, an arbitrary executable, or another library: the target is always the
fixture spawned by this process, and the probe site is always the
`SSL_write` of the `libssl.so.3` mapping of that child (attached with
`Some(tgid)`; `pid=None` is never used).

### What one batch does

1. `positive` — 2 s capture over a script of four `SSL_write` calls
   (512 c2s, 384 s2c, 8192 c2s, then a deliberate `-1`).  Expected: 4 entries,
   4 exits, 8 submitted, 8 consumed, 0 lost, 1 truncated at 4096, 1 invalid
   length with **no** user read, 3 user reads / 4992 bytes, 2 distinct SSL
   object aliases, and peer-received 8704 c2s + 384 s2c bytes matching the
   frozen payload formula.
2. `wrong_birth` — the same workload with a start-time that is off by one
   tick.  Expected: **0** events, **0** user reads, ≥ 8 identity rejections.
3. `crash_recovery` — a child collector is `SIGKILL`ed mid-session.  Expected:
   its own program ids gone within 3 s, its fixture gone within 11 s, no
   closeout from it, and its private run dir removed after identity checks.
   The supervisor's conclusion is printed as its own record type
   (`record=crash_summary`, `source=parent_after_kill`,
   `child_closeout_absent=true`, `source_session=crash_recovery`), never as a
   `session_closeout`: only a real child collector may emit that, and this one
   was killed before it could.

### Records (contract 5.8 names, validated before printing)

One JSON object per line, in this order: `batch`, then per session a `session`
seed, a `session_closeout`, then `crash_summary`, then the `closeout`.

* `batch`: `kernel` (`/proc/sys/kernel/osrelease`), `mode`, `euid`,
  `embedded_bpf[{name,bytes,fnv1a64}]`, `disk_object`, capabilities.
* `session` / `session_closeout`: flat contract fields (`batch_run_id`,
  `run_id`, `run_dir_path`, `session_id` ∈ positive|wrong_birth|crash_recovery,
  `started_at_boot_ns`, `target_tgid/ppid/start_ticks/uid/gid/nspid`,
  `pidns_dev/ino`, `nsfs_magic`, `cgroup_path/cgroup_id/cgroup_inode_expected`,
  `libssl{path,dev,ino,size,mtime_s,build_id,maps_exec_match}`,
  `attach{mechanism,pmu_type,pid_scope,symbol,symbol_version,file_offset}`,
  `program_ids`, `link_ids`, `identity_checks`, `workload`).
  `core::session_record_ok` runs on the seed **and** on the closeout before
  either is printed, so a dropped, renamed, nulled or retyped field turns the
  session red instead of slipping into the evidence.  `cgroup_id` is the id
  the probes observed in `bpf_get_current_cgroup_id` and must equal the
  kernfs inode of the process's cgroup directory (`cgroup_inode_expected`).
  The session closeout's `cleanup` uses the contract keys
  `prog_ids_absent/child_gone/fd_before/fd_after`; only the batch closeout
  uses `own_*`/fd_equal.
* `crash_summary`: the parent's post-kill supervision record (see above).

### Fixture and privilege model

* The workload runs as uid/gid 1000 with a **cleared environment**
  (`env_clear`): no `LD_PRELOAD`, no `OPENSSL_CONF`, no `SSLKEYLOGFILE`, no
  inherited anything.  The collector alone runs as root.
* The workload closes every inherited descriptor above 2 before it prints
  READY (enumeration of `/proc/self/fd` plus a 3..255 sweep), so it holds
  exactly fds 0/1/2.  The collector re-reads `/proc/<pid>/fd` and fails the
  session unless the set is exactly {0,1,2} and every target is a pipe or
  `/dev/null`: a socket, a regular file, an inherited descriptor above 2 or
  any other character device (a pty master included) is a hard failure.  The
  fixture reports the number of descriptors it closed in its READY line.
* Trust is only the fixture's own in-memory self-signed EC P-256 certificate.
  No system CA store is read or modified, and no verification is disabled
  globally.
* The private run dir is `/tmp/kebab-observer-tls-fixture-<run_id>` with
  `run_id` = 16 lower-case hex characters, created with mode 0700, never
  reused, never a symlink.  A manifest records ownership; removal re-checks
  the exact absolute path, owner, mode and `run_id` first.

### Budgets (hard, not aspirational)

| Limit | Value |
| --- | --- |
| Single session capture | 1–10 s (this batch uses 2 s) |
| Whole batch | ≤ 55 s internal / 60 s external `timeout` |
| Ring buffer | 1 MiB |
| Declared BPF map memory | 1,082,852 B (limit 8 MiB) |
| Single sample | ≤ 4096 B |
| Accumulated samples | ≤ 1 MiB |
| Events | ≤ 10,000 |
| Nesting depth | ≤ 8 |

Declared BPF map memory is the **logical** sum of the nine map declarations of
the loaded object (`bpf/tls_fixture.bpf.c:25-35`, mirrored by
`tls_core::BPF_MAP_DECLS`): the ring buffer counts its capacity and every other
map counts `max_entries * (key_size + value_size)`, so ALIAS (384 B) and
ALIASSEQ (12 B) are part of the 1,082,852 B.  It is a declaration total, not the
kernel's actual resident/memlock/JIT/allocator cost, and not a cgroup quota.

These are **logical** limits enforced by this code and the BPF program.  No
system-wide cgroup memory quota is applied, and none is claimed.

### Counter vocabulary

`submitted`, `consumed`, `lost`, `read_failed`, `map_failed`, `expired`,
`truncated`, `identity_rejected`, `len_invalid`, `abnormal_ret`,
`user_read_attempts`, `user_read_bytes`, `payload_bytes`, `depth_overflow`,
`unpaired`.  A pass requires each of them to hit its expected value;
`submitted == consumed` alone is never treated as success.

## Semantics that are deliberately *not* claimed

* The entry record is a snapshot, not an atomic copy of what the peer
  eventually saw.
* A call is paired with its return by per-thread call id; that is a
  single-call association, not a general session/connection generation model.
* SSL objects appear only as run-local opaque **ids**.  The probe keeps a
  private pointer -> id map in the kernel (`ALIAS`, capacity 32, plus a
  one-slot counter): the `SSL*` is only ever a map key, and the record
  carries the small counter id (1, 2, ...).  There is no xor/hash of the
  address and no pointer half anywhere in a record or in userspace; the id is
  a 32-bit opaque token with no cryptographic claim.  The collector refuses a
  session whose ids are outside `1..=32`, are zero, change inside one
  direction, or collapse the client and server object into one id.
  The fixture never frees or reuses an `SSL*` inside a session, so no reuse
  detection is claimed: a change is refused as a mismatch instead.
* No TLS decryption, no `SSL_read` probe, no HTTP parsing, no QUIC, no
  external app, no Android, no r5 kernel.

## Evidence classes (never mixed)

1. **Synthetic (Windows)** — `cargo test`: wire decode, pairing (including
   nested enter/enter/exit/exit), counters, payload formula, dev encoding,
   ELF build-id, run-id rules, ELF symbol version/offset, the §5.8 session /
   batch / crash_summary schemas (per-key and per-nested-type negative cases),
   identity rejection, run-dir symlink/owner/mode rules, alias reuse and
   pointer-free alias ids.  Every test name starts with `synthetic_` and the
   legacy ones stay portable.
2. **WSL x86_64 live** — verify command 14 above, on this machine, against the
   system `libssl.so.3` (`build-id 4f18cdec63410d6a18616448c922fb580ca2c292`),
   and `scripts/track-smoke.sh` for the `track` pipeline (its own workload).
3. **ARM64 compile-only** — `cargo build/clippy --target aarch64-unknown-linux-musl`
   plus the `-D__TARGET_ARCH_arm64` BPF object.  Nothing here is ever
   reported as an ARM64 attach or as an r5 result.

## External-target tracking (`kebab-observer track`)

`track` is the one subcommand that names a process this crate did not
create.  It is still an observer only: it starts nothing, stops nothing, writes
nothing into the target, and touches no tracefs.  Its window is bounded by
`--duration` (1..=60 s) and by a hard 20 000-event cap.

```
kebab-observer track --uid UID --cgroup-id ID [--tgid PID] --duration 1..=60 --out NEW.jsonl
```

Exit codes: `0` closeout passed, `1` self-consistency failure, `2` usage rejected,
`3` blocked before or while attaching (refused identity, missing target,
program load/attach failure).

### Filtering (two halves)

| Half | Where | Condition |
| --- | --- | --- |
| kernel | `gate()` | `bpf_get_current_cgroup_id() == --cgroup-id` **and** `bpf_get_current_uid_gid()` low word `== --uid`, plus the session deadline, minus the collector's own tgid |
| kernel | `gate()` with `--tgid` | `bpf_get_ns_current_pid_tgid()` must report the target tgid **in the target's own pid namespace** |
| userspace | `track_core::TrackFilter` | re-checks uid/cgroup, accepts the target tgid in either the namespace or the `/proc` view, then requires `start_boottime / tick_ns == stat starttime` — exact, no tolerance, because one tick of drift is a different process (PID reuse) |

`--tgid` is resolved and proven before any probe is loaded:
`/proc/PID/status` uid and `NSpid`, `/proc/PID/cgroup` →
`/sys/fs/cgroup<path>` inode (that inode is the same number
`bpf_get_current_cgroup_id()` returns), and `/proc/PID/ns/pid` dev+ino.
A mismatch is refused with exit 3 and **no output file is created**.

Why the namespace view matters: when the target lives in its own PID namespace,
`/proc` shows one number and `task->tgid` another.  Measured on WSL:
`/proc` pid 1577358, kernel-side `tgid` 1577953.  A naive tgid
comparison silently yields an empty session; the two-view comparison above does
not.

### Events (one JSON object per line)

`{"record":"session",...}` first, then `{"record":"event",...}` lines,
then one `{"record":"closeout","passed":bool,...}` line.  Records are 256 bytes:
a 128-byte header plus a 128-byte path window.

| kind | fields beyond the shared header |
| --- | --- |
| `sys_enter` | `nr`, `arg0`.. `arg2`, `nr_unknown` |
| `sys_exit` | `nr`, `result` |
| `sys_enter_openat` | `nr`, `dirfd`, `path`, `path_len` (the only user-memory read, bounded to 128 B) |
| `binder_transaction` | `debug_id`, `to_proc_pid`, `code`, `flags`, `reply` |
| `binder_transaction_received` | `debug_id` |

Every event carries `sequence`, `boottime_ns`, `tgid`/`tid`, `ns_tgid`/`ns_tid`,
`comm`, `start_boot_ns`, `cgroup_id`, `uid`, `cpu`.

Closeout counters: `counts.gate` (gate passes), `counts.submitted`,
`counts.lost` (ring full), `counts.truncated` (past the 20 000 cap),
`counts.map_failed`, `counts.read_failed` (kernel struct read),
`counts.user_read_failed` (path unreadable), `counts.path_long` (path hit
the 128-byte window).  `sequence_gaps` must be 0 — a hole means the stream is not
the stream the summary describes — and `ring_fully_accounted` must be true.

`sys_exit` ≈ `sys_enter + sys_enter_openat`, because an openat ships the
richer record instead of a plain enter.

`empty_session: true` with `counts.gate: 0` means nothing matched the
filter (wrong uid/cgroup/tgid, or an idle target).  It is reported, never hidden
as a successful capture.

### Verification status of this subcommand

| piece | state |
| --- | --- |
| decode / two-view filter / accounting / CLI | 12 unit tests, `cargo test` on Windows |
| x86_64 WSL live pipeline | `scripts/track-smoke.sh` **SMOKE PASS**: 7 284 events, 911 openat records with `path="/etc/hostname"`, 0 lost, 0 sequence gaps; a second run hits the 20 000 cap with `truncated=true`, `events_written=20000`, `last_sequence=19999` |
| aarch64 boot/attach | **not run**: compile-only (`clippy -D warnings` + `cargo build --target aarch64-unknown-linux-musl`) |
| Binder events | **not verified live**: the WSL kernel rejects both Binder programs at load (no Binder types in its BTF). The session records `binder_attached:false` and the first line of the verifier error instead of pretending Binder was covered. The r4 device does expose `/sys/kernel/debug/tracing/events/binder/binder_transaction{,_received}` and `/sys/kernel/btf/vmlinux`, so the ARM64 object is expected to load there — an expectation, not evidence |
| device run | **not run**: no device collection window was opened for this crate |

## Stop and rollback


Everything this batch creates is either removed on the way out or is a build
artifact under `target/`.  A stuck run leaves at most
`/tmp/kebab-observer-tls-fixture-<run_id>` and a `kebab-observer` process
tree; both belong to this batch and the closeout names the run id.

The only teardown path with live evidence is an **in-batch** `SIGKILL` of a
child collector: the kernel detaches the probes when that process dies, and
closing its stdin makes the workload exit on its own.  Reclaim after an
**external** `timeout` signal or an operator `TERM`/`KILL` of the batch
process is **NOT_TESTED**: the closeout prints
`external_timeout_cleanup_claim: false` and lists
`external_timeout_or_TERM_teardown` under `not_verified`, and that field
must stay false until a separate approved window proves it.  The external
`timeout` in verify command 14 is a safety net, not evidence.

## External TLS attach (kebab-observer tls-attach)

This is the third step's collector: it attaches uprobes to `SSL_write` and `SSL_read` of a TLS
library the collector did not build, including a library that only exists **inside an APK**
(`extractNativeLibs=false`). It is an observer: it starts and stops nothing, writes nothing into the
target, installs no CA, does not disable verification, touches no tracefs, and the only user memory
it reads is the call's own argument buffer, bounded to 4 KiB.

Files: `bpf/tls_attach.bpf.c` (8 probes), `src/tls_attach_core.rs` (portable, 20 unit tests),
`src/tls_attach.rs` (Linux runtime), `fixtures/tls_attach_fixture.c` (own workload),
`scripts/tls-attach-smoke.sh` (self-target smoke test).

### Invocation

```
kebab-observer tls-attach --pid PID --duration 1..=120 --out NEW.jsonl \
    --target LABEL=PATH[@ELF_BASE]:WRITE[:READ] [--target ...] [--start-ticks T] [--uid U] [--cgroup-id ID]
kebab-observer tls-attach --pid PID --report --target LABEL=PATH[@ELF_BASE]:WRITE[:READ]
```

WRITE and READ are either an **absolute file offset** (an APK-hosted library, where the offset is
relative to the APK file) or a **dynamic symbol name** (a plain .so). `ELF_BASE` is the offset of the
ELF image inside `PATH`: 0 for a plain .so, the ZIP data offset for a library inside an APK. Two targets
fit in one session, and every event carries its label, so one run can compare, say, an APK-hosted
libssl with the system BoringSSL.

Real values for 运动世界 7.4.01 (arm64-v8a `libssl.so`, ZIP data offset 98,336,768):

```
--target apk-libssl=/data/app/.../base.apk@98336768:98514224:98514028
--target system-libssl=/system/lib64/libssl.so:SSL_write:SSL_read
```

`--report` resolves the ELF, the PT_LOAD segment and the symbol offsets and prints one JSON line
without loading a program or requiring the file to be mapped. It is the pre-flight check, and it also
works cross-architecture, which is how the host can read out the device offsets it cannot attach to.

### Validation before any probe is loaded

1. **/proc identity**: `/proc/PID/stat` starttime, `/proc/PID/status` uid and `NSpid` (last field =
   tgid inside the target's own pid namespace), `/proc/PID/ns/pid` dev+ino, and the cgroup path's
   inode. A mismatch with `--start-ticks`, `--uid` or `--cgroup-id` refuses the session.
2. **inode proof**: `stat(PATH)` gives dev+inode; the target's `/proc/PID/maps` must contain that same
   dev+inode with an executable segment. A file the target never mapped is refused instead of being
   attached to (this is what stops a temporary copy of a library from standing in for the real one).
3. **ELF proof**: the ELF at `ELF_BASE` is parsed from the real file (program headers, section table,
   `.dynsym`); the requested offset must fall inside an executable `PT_LOAD`.
4. **load-bias proof**: the bias is derived per mapped segment (`map_start - align(PtLoad.vaddr)`) and
   must agree across every segment; the offset is then translated to a runtime address that has to land
   inside an executable mapping. This is the check that refuses an ASLR address handed in as a file offset.

The uprobe itself is attached with **pid=None** and filtered inside the BPF program: a uprobe perf event
bound to one pid only fires for one thread, and a TLS library is called from whichever thread is doing
IO. The gate therefore carries the full identity (group-leader starttime in clock ticks, tgid inside the
target pid namespace, uid, optional cgroup) plus the session deadline, and drops everything else before
touching memory.

### Events

`{"record":"session"}` first, then one `{"record":"call"}` per paired call, then `{"record":"closeout"}`.
Records are 4240 bytes (144-byte header + 4 KiB sample area); the ring buffer is 1 MiB and the session
cap is 20,000 records.

Per call: direction (write/read), `tid`/`tgid`/`ns_tid`/`ns_tgid`, `comm`, an **opaque SSL id**
(`ssl-1`, `ssl-2`: a per-run counter, never the pointer), `declared`, `result`, `captured`,
`valid_bytes`, `truncated`, `len_invalid`, `read_failed`, `unpaired`, `depth`, timestamps,
`payload_fnv1a64` (non-reversible fingerprint) and `http`.

Plaintext is captured at the entry probe for a write and at the return probe for a read (where the bytes
only exist once the call returns). `payload_bytes_emitted` is false in every record: the payload never
reaches the file. What is recorded is the desensitised structure - method, scheme, host, normalised path
(numeric/hex/uuid segments become `{id}`), header **names** only, query parameter **names and coarse
types**, response status, content-type and content-length. `Host`, `Content-Type` and
`Content-Length` are the only header values that survive; cookies, tokens, coordinates and body bytes
do not.

Counters: `enter_write`/`exit_write`/`enter_read`/`exit_read`, `submitted`, `reserve_failed`,
`identity_rejected`, `map_failed`, `event_capped`, `depth_overflow`, `user_read`,
`user_read_bytes`, `user_read_failed`, `len_invalid`, `truncated`, `unpaired`.
`passed` is false on any of: undecodable record, sequence gap or inversion, duplicate id, exit without
entry, call without return, past the cap, a program id that survives detach, a ring that does not add up
to what the probes submitted, or a target whose identity changed while the session was open.
`empty_session` reports a session that matched nothing instead of pretending it succeeded.

### Evidence status

| piece | state |
| --- | --- |
| decode / pairing / reentrancy / HTTP redaction / spec parsing / ELF + dynsym math | 20 unit tests, `cargo test` on Windows |
| x86_64 WSL live attach to `/usr/lib/x86_64-linux-gnu/libssl.so.3` by symbol | `scripts/tls-attach-smoke.sh` **SMOKE PASS**: 13 paired calls (6 write / 7 read), one nested call at depth >= 1, 2 truncated, 4 declared<=0, HTTP request + response structures extracted, all redaction assertions hold, 4 negatives (wrong starttime, wrong uid, unmapped inode, usage) refused with the right exit code |
| APK-internal offsets | the tool's own ELF/dynsym path resolved `libssl.so` inside the real 331 MB APK at base 98,336,768 to **SSL_write 98,514,224 / SSL_read 98,514,028** (`st_value` 177,456 / 177,260), matching the independent static analysis byte for byte |
| aarch64 attach | **not run**: compile-only (`clippy -D warnings` + `cargo build --target aarch64-unknown-linux-musl`) |
| real app plaintext | **not run**: no device capture window was opened by this crate's author, so no 运动世界 request was ever inspected; the host only computed offsets |

### Repro

```
wsl.exe -d Debian --exec /usr/bin/clang-19 -target bpfel -D__TARGET_ARCH_arm64 -O2 -g -Wall -Wextra -Werror -c /codexprojects/oneplus8t/rust/kebab-observer/bpf/tls_attach.bpf.c -o /codexprojects/oneplus8t/rust/kebab-observer/bpf/tls_attach.arm64.bpf.o
cargo test --locked --offline
cargo clippy --locked --offline --all-targets --target aarch64-unknown-linux-musl -- -D warnings
cargo build --locked --offline --release --target aarch64-unknown-linux-musl
wsl.exe -d Debian -u root --exec /bin/bash /codexprojects/oneplus8t/rust/kebab-observer/scripts/tls-attach-smoke.sh
# pre-flight on the device, no probe loaded:
sudo -n /srv/kebab-re-20261120/bin/kebab-observer tls-attach --pid 1 --report \
  '--target' 'apk-libssl=/data/app/.../base.apk@98336768:SSL_write:SSL_read'
```

### Container paths and device drift (t8)

The t6 window 3 failure was the collector refusing itself: `is not mapped by pid <pid>` on a process
whose `/proc/<pid>/maps` did contain both targets (58 `base.apk` lines, 4 `libssl` lines, measured in the
same window). Two facts explain it, and both are now handled explicitly:

1. **The path is not the identity.** A container maps guest paths (`/data/app/...`, `/system/...`) while
   the lab holds host paths, so comparing path strings matches nothing. Association is now the
   `(st_dev major:minor, st_ino)` tuple; the path is only used for messages and as the attach target.
2. **The loop-mounted rootfs changes its device number.** Measured on kebab inside one window: the same
   `/system/lib64/libssl.so` inode 3388 was reported under `00:46` by one run and `00:48` by the next.
   Matching only on the tuple would refuse a file the target demonstrably maps, so an inode match under a
   different device is accepted as **device drift**, recorded, and only when `map_files` or the basename
   confirms it is the same file.

`--maps-root DIR` (default `/proc/PID/root`, `off` disables) resolves a path through the target's own
mount namespace. The ladder per target is: stat as given, then stat `MAPS_ROOT + path`, then stat every
maps path that carries the same inode through `MAPS_ROOT`. The candidate that matched is what the uprobe
attaches to, and it is recorded, so the next window cannot silently attach to the wrong file.

```
--target apk-libssl=/data/app/.../base.apk@98336768:98514224:98514028     # guest path: resolved via --maps-root
--target sys-libssl=/system/lib64/libssl.so:SSL_write:SSL_read          # guest path, same rule
--target host-libssl=/srv/.../work/rootfs/system/lib64/libssl.so:SSL_write:SSL_read   # host path: twins found by inode
```

Stat is fresh in every run; a device number captured by an earlier `--report` is never reused. Records now
carry `attach_path`, `resolution` (`as_given` / `maps_root` / `maps_root+guest_path`), `association`
`tuple` / `inode_dev_drift` / `none`, `maps_dev`, `confirmation` (`tuple` / `map_files` /
`basename` / `none`), `maps_checked`, `maps_lines`, `maps_skipped` and `candidates_tried`. A refusal
prints the maps line count, every candidate tried, and the same-name maps entries with their `dev` and
`inode`, which is what turns the next window's failure into evidence.

`--report` now reads maps too (when it can) and records the association, so a pre-flight no longer shows
`mapped_segments: 0` for a file that is mapped. Reports stay tolerant of an unreadable `/proc` and of a
file that is not mapped yet; an attach still refuses in both cases.

Regression evidence (offline, no device):

- `fixtures/maps-samples/step3-maps-apk-head.txt` is the real t6 sample (3 base.apk segments under
  `103:0c` inode 1443630 plus the two summary lines the capture appended). Unit tests parse it, associate
  it by tuple, associate it again with a perturbed device (`103:48`) as drift, and refuse an unrelated
  inode. A second case replays the `libssl` half of the same window (inode 3388, `00:46` in maps, `00:48`
  from stat).
- `scripts/tls-attach-smoke.sh` now also rehearses the container case: the target is given as a guest
  path (`/system/lib64/libssl.so`) that only exists under `--maps-root`, both in `--report` and in a live
  attach, and asserts `mapped_segments > 0`, `association == tuple` and `maps_verified` on both probes.
- Negatives still hold: unmapped inode `exit 3`, unresolvable path `exit 3` (message names the namespace),
  wrong starttime `exit 3`, usage `exit 2`, and none of them creates an output file.
