//! Portable core for the `track` subcommand.
//!
//! Everything in this module is kernel-free: wire decoding, the userspace half
//! of the target filter, session accounting and CLI parsing.  All of it is
//! unit-tested on Windows, so an ABI or accounting change cannot hide behind a
//! compile-only ARM64 build.

use serde_json::{json, Map, Value};
use std::collections::HashSet;

/// Record size shipped by `bpf/track.bpf.c` (128-byte header + 128-byte path).
pub const TRACK_EVENT_BYTES: usize = 256;
/// Fixed header, identical for every kind.
pub const TRACK_HEADER_BYTES: usize = 128;
/// Bounded openat path window (bytes, including the NUL written by the kernel).
pub const TRACK_PATH_MAX: usize = 128;
/// Hard per-session event cap.  The BPF side refuses sequence numbers past it
/// and counts the refusal in `CNT_TRUNCATED`.
pub const TRACK_EVENT_CAP: u64 = 20_000;
/// Ring buffer capacity declared by the object.
pub const TRACK_RING_BYTES: u64 = 1_048_576;
/// Longest accepted session; keeps a track run inside one operator window.
pub const TRACK_DURATION_MAX_S: u64 = 60;

pub const KIND_SYS_ENTER: u32 = 1;
pub const KIND_SYS_EXIT: u32 = 2;
pub const KIND_BINDER_TX: u32 = 3;
pub const KIND_BINDER_RX: u32 = 4;
pub const KIND_OPENAT: u32 = 5;

// CONFIG indices, mirrored from bpf/track.bpf.c (CFG_*).
pub const CFG_CGROUP: u32 = 0;
pub const CFG_UID: u32 = 1;
pub const CFG_TGID: u32 = 2;
pub const CFG_DEADLINE: u32 = 3;
pub const CFG_CAP: u32 = 4;
pub const CFG_PIDNS_DEV: u32 = 5;
pub const CFG_PIDNS_INO: u32 = 6;
/// The collector's own tgid; the BPF gate refuses it so a cgroup+uid wide run
/// never observes its own reader.
pub const CFG_EXCLUDE_TGID: u32 = 7;

// COUNTERS indices, mirrored from bpf/track.bpf.c (CNT_*).
pub const CNT_GATE: u32 = 0;
pub const CNT_SUBMITTED: u32 = 1;
pub const CNT_LOST: u32 = 2;
pub const CNT_TRUNCATED: u32 = 3;
pub const CNT_MAP_FAILED: u32 = 4;
pub const CNT_READ_FAILED: u32 = 5;
pub const CNT_USER_READ_FAILED: u32 = 6;
pub const CNT_OPENAT: u32 = 7;
pub const CNT_BINDER: u32 = 8;
pub const CNT_PATH_LONG: u32 = 9;
pub const CNT_SYS_ENTER: u32 = 10;
pub const CNT_SYS_EXIT: u32 = 11;
/// Number of COUNTERS slots the collector reads back.
pub const COUNTER_SLOTS: u32 = 12;

pub const TRACK_USAGE: &str = "usage: kebab-observer track --uid UID --cgroup-id ID \
[--tgid PID] --duration 1..=60 --out NEW_JSONL_FILE";

/// One decoded `track` record.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TrackEvent {
    pub boottime_ns: u64,
    pub tgid: u32,
    pub tid: u32,
    pub start_ns: u64,
    pub cgroup_id: u64,
    pub sequence: u64,
    pub ns_tgid: u32,
    pub ns_tid: u32,
    pub arg0: u64,
    pub arg1: i64,
    pub arg2: i64,
    pub result: i64,
    pub kind: u32,
    pub cpu: u32,
    pub uid: u32,
    pub nr: u32,
    pub flags: u32,
    pub reply: u32,
    pub to_thread: u32,
    pub path_len: u32,
    pub comm: String,
    pub path: Option<String>,
}

impl TrackEvent {
    pub fn kind_name(&self) -> &'static str {
        match self.kind {
            KIND_SYS_ENTER => "sys_enter",
            KIND_SYS_EXIT => "sys_exit",
            KIND_BINDER_TX => "binder_transaction",
            KIND_BINDER_RX => "binder_transaction_received",
            KIND_OPENAT => "sys_enter_openat",
            _ => "unknown",
        }
    }

    /// True when the syscall nr is unknown because the matching enter was lost.
    pub fn nr_unknown(&self) -> bool {
        self.nr == u32::MAX
    }

    /// One JSON object per line.  Every kind shares the same identity fields so
    /// records can be joined by (tgid, tid, boottime_ns) alone.
    pub fn to_json(&self) -> Value {
        let mut fields = Map::new();
        fields.insert("record".to_owned(), json!("event"));
        fields.insert("sequence".to_owned(), json!(self.sequence));
        fields.insert("boottime_ns".to_owned(), json!(self.boottime_ns));
        fields.insert("kind".to_owned(), json!(self.kind_name()));
        fields.insert("tgid".to_owned(), json!(self.tgid));
        fields.insert("tid".to_owned(), json!(self.tid));
        fields.insert("comm".to_owned(), json!(self.comm));
        fields.insert("start_boot_ns".to_owned(), json!(self.start_ns));
        fields.insert("cgroup_id".to_owned(), json!(self.cgroup_id));
        fields.insert("uid".to_owned(), json!(self.uid));
        fields.insert("cpu".to_owned(), json!(self.cpu));
        fields.insert("ns_tgid".to_owned(), json!(self.ns_tgid));
        fields.insert("ns_tid".to_owned(), json!(self.ns_tid));
        match self.kind {
            KIND_SYS_ENTER | KIND_SYS_EXIT => {
                fields.insert("nr".to_owned(), json!(self.nr));
                fields.insert("nr_unknown".to_owned(), json!(self.nr_unknown()));
                fields.insert("arg0".to_owned(), json!(self.arg0));
                fields.insert("arg1".to_owned(), json!(self.arg1));
                fields.insert("arg2".to_owned(), json!(self.arg2));
                fields.insert("result".to_owned(), json!(self.result));
            }
            KIND_OPENAT => {
                fields.insert("nr".to_owned(), json!(self.nr));
                fields.insert("dirfd".to_owned(), json!(self.arg0 as i64 as i32));
                fields.insert("path".to_owned(), json!(self.path));
                fields.insert("path_len".to_owned(), json!(self.path_len));
            }
            KIND_BINDER_TX => {
                fields.insert("debug_id".to_owned(), json!(self.arg0 as u32));
                fields.insert("to_proc_pid".to_owned(), json!(self.arg1 as i32));
                fields.insert("to_thread_pid".to_owned(), json!(self.to_thread));
                fields.insert("code".to_owned(), json!(self.arg2 as u32));
                fields.insert("flags".to_owned(), json!(self.flags));
                // BPF copies args[0] (the tracepoint's bool reply) into its own
                // field; nothing here is derived from the syscall nr any more.
                fields.insert("reply".to_owned(), json!(self.reply != 0));
            }
            _ => {
                fields.insert("debug_id".to_owned(), json!(self.arg0 as u32));
            }
        }
        Value::Object(fields)
    }
}

fn c_string(bytes: &[u8]) -> String {
    let end = bytes.iter().position(|b| *b == 0).unwrap_or(bytes.len());
    String::from_utf8_lossy(&bytes[..end]).into_owned()
}

/// Decode one 256-byte `track` record.
///
/// Rejection is deliberate and coarse: a record that does not match the ABI of
/// its own kind is never repaired, it is refused, so a corrupt or truncated
/// ring record cannot silently become a plausible event.
pub fn decode_track(bytes: &[u8]) -> Result<TrackEvent, &'static str> {
    if bytes.len() != TRACK_EVENT_BYTES {
        return Err("track event length mismatch");
    }
    let q = |offset: usize| u64::from_le_bytes(bytes[offset..offset + 8].try_into().unwrap());
    let d = |offset: usize| u32::from_le_bytes(bytes[offset..offset + 4].try_into().unwrap());
    let kind = d(80);
    let path_len = d(100);
    match kind {
        KIND_SYS_ENTER | KIND_SYS_EXIT | KIND_BINDER_TX | KIND_BINDER_RX => {
            if path_len != 0 {
                return Err("unexpected path window");
            }
        }
        KIND_OPENAT => {
            if path_len as usize > TRACK_PATH_MAX {
                return Err("openat path length out of range");
            }
        }
        _ => return Err("unknown track kind"),
    }
    let pid_tid = q(8);
    if pid_tid >> 32 == 0 || pid_tid as u32 == 0 {
        return Err("zero actor identity");
    }
    let ns_pid_tid = q(40);
    let path = if kind == KIND_OPENAT && path_len > 0 {
        let text = c_string(&bytes[TRACK_HEADER_BYTES..TRACK_HEADER_BYTES + path_len as usize]);
        if text.is_empty() {
            return Err("empty openat path");
        }
        Some(text)
    } else {
        None
    };
    Ok(TrackEvent {
        boottime_ns: q(0),
        tgid: (pid_tid >> 32) as u32,
        tid: pid_tid as u32,
        start_ns: q(16),
        cgroup_id: q(24),
        sequence: q(32),
        ns_tgid: (ns_pid_tid >> 32) as u32,
        ns_tid: ns_pid_tid as u32,
        arg0: q(48),
        arg1: q(56) as i64,
        arg2: q(64) as i64,
        result: q(72) as i64,
        kind,
        cpu: d(84),
        uid: d(88),
        nr: d(92),
        flags: d(96),
        reply: d(120),
        to_thread: d(124),
        path_len,
        comm: c_string(&bytes[104..120]),
        path,
    })
}

/// Parse the first field of `/proc/uptime` ("12345.67 ...") into boot nanoseconds.
///
/// The kernel prints exactly two fraction digits, so a different precision is
/// a refusal, not something to round: the deadline in the BPF object is
/// computed from this value.
pub fn parse_uptime_ns(text: &str) -> Result<u64, &'static str> {
    let first = text.split_whitespace().next().ok_or("missing uptime")?;
    let (seconds, fraction) = first.split_once('.').ok_or("missing uptime fraction")?;
    if fraction.len() != 2 || !fraction.bytes().all(|b| b.is_ascii_digit()) {
        return Err("unexpected uptime precision");
    }
    let seconds: u64 = seconds.parse().map_err(|_| "invalid uptime seconds")?;
    let fraction: u64 = fraction.parse().map_err(|_| "invalid uptime fraction")?;
    seconds
        .checked_mul(1_000_000_000)
        .and_then(|ns| ns.checked_add(fraction * 10_000_000))
        .ok_or("uptime overflow")
}

/// Nanoseconds per clock tick, exactly as `nsec_to_clock_t` divides it.
pub fn tick_ns(clock_ticks_per_sec: u64) -> Option<u64> {
    let tick = 1_000_000_000u64.checked_div(clock_ticks_per_sec)?;
    if tick == 0 {
        None
    } else {
        Some(tick)
    }
}

/// `/proc/<pid>/stat` starttime is `floor(start_boottime / tick_ns)`, so the
/// comparison is exact with no tolerance window: one tick of drift is a
/// different process identity (PID reuse), not noise.
pub fn start_ticks_matches(start_boot_ns: u64, target_ticks: u64, clock_ticks_per_sec: u64) -> bool {
    match tick_ns(clock_ticks_per_sec) {
        Some(tick) => start_boot_ns / tick == target_ticks,
        None => false,
    }
}

/// Why a decoded event did not belong to the requested target.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Reject {
    Uid,
    Cgroup,
    Tgid,
    Start,
}

impl Reject {
    pub fn name(self) -> &'static str {
        match self {
            Reject::Uid => "uid_mismatch",
            Reject::Cgroup => "cgroup_mismatch",
            Reject::Tgid => "tgid_mismatch",
            Reject::Start => "start_ticks_mismatch",
        }
    }

    fn slot(self) -> usize {
        match self {
            Reject::Uid => 0,
            Reject::Cgroup => 1,
            Reject::Tgid => 2,
            Reject::Start => 3,
        }
    }
}

/// Userspace half of the filter.  The kernel already dropped everything whose
/// cgroup id or uid differs; this re-checks both (defence in depth) and adds
/// the tgid/start_ticks identity that the kernel cannot cheaply verify.
///
/// Two tgid views are accepted because a pid is only a number inside a
/// namespace: `ns_tgid` is the target's tgid in *its own* pid namespace (what
/// the BPF side compared), `proc_tgid` is the number /proc showed the
/// collector.  When collector and target share a namespace both are the same
/// value; when they do not, either one matching is enough, and the
/// boot-time start_ticks comparison below is what actually rules out PID reuse.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TrackFilter {
    pub uid: u32,
    pub cgroup_id: u64,
    pub ns_tgid: Option<u32>,
    pub proc_tgid: Option<u32>,
    pub start_ticks: Option<u64>,
    pub clock_ticks_per_sec: u64,
}

impl TrackFilter {
    pub fn check(&self, event: &TrackEvent) -> Result<(), Reject> {
        if event.uid != self.uid {
            return Err(Reject::Uid);
        }
        if event.cgroup_id != self.cgroup_id {
            return Err(Reject::Cgroup);
        }
        if self.ns_tgid.is_some() || self.proc_tgid.is_some() {
            let ns_match = self.ns_tgid.is_some_and(|tgid| event.ns_tgid == tgid);
            let proc_match = self.proc_tgid.is_some_and(|tgid| event.tgid == tgid);
            if !ns_match && !proc_match {
                return Err(Reject::Tgid);
            }
            if let Some(ticks) = self.start_ticks {
                if !start_ticks_matches(event.start_ns, ticks, self.clock_ticks_per_sec) {
                    return Err(Reject::Start);
                }
            }
        }
        Ok(())
    }
}

/// What the collector is allowed to do with one decoded event.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verdict {
    Write,
    Filtered(Reject),
    Duplicate,
    Capped,
}

/// Self-consistency accounting for one session.
#[derive(Debug, Clone)]
pub struct TrackAccount {
    cap: u64,
    decoded: u64,
    written: u64,
    duplicates: u64,
    inversions: u64,
    over_cap: u64,
    first_seq: Option<u64>,
    last_seq: Option<u64>,
    capped: bool,
    rejects: [u64; 4],
    observed: HashSet<u64>,
}

impl TrackAccount {
    pub fn new(cap: u64) -> Self {
        Self {
            cap,
            decoded: 0,
            written: 0,
            duplicates: 0,
            inversions: 0,
            over_cap: 0,
            first_seq: None,
            last_seq: None,
            capped: false,
            rejects: [0; 4],
            observed: HashSet::new(),
        }
    }

    pub fn admit(&mut self, event: &TrackEvent, filter: &TrackFilter) -> Verdict {
        self.decoded += 1;
        if !self.observed.insert(event.sequence) {
            self.duplicates += 1;
            return Verdict::Duplicate;
        }
        if let Some(last) = self.last_seq {
            if event.sequence < last {
                self.inversions += 1;
            }
        }
        self.first_seq = Some(match self.first_seq {
            Some(first) => first.min(event.sequence),
            None => event.sequence,
        });
        self.last_seq = Some(match self.last_seq {
            Some(last) => last.max(event.sequence),
            None => event.sequence,
        });
        if event.sequence >= self.cap {
            self.over_cap += 1;
            self.capped = true;
            return Verdict::Capped;
        }
        if let Err(reason) = filter.check(event) {
            self.rejects[reason.slot()] += 1;
            return Verdict::Filtered(reason);
        }
        if self.written >= self.cap {
            self.capped = true;
            return Verdict::Capped;
        }
        self.written += 1;
        Verdict::Write
    }

    pub fn written(&self) -> u64 {
        self.written
    }

    pub fn truncated(&self) -> bool {
        self.capped
    }

    pub fn summary(&self) -> TrackSummary {
        let unique = self.observed.len() as u64;
        let span = match (self.first_seq, self.last_seq) {
            (Some(first), Some(last)) => last - first + 1,
            _ => 0,
        };
        TrackSummary {
            cap: self.cap,
            decoded: self.decoded,
            written: self.written,
            unique,
            duplicates: self.duplicates,
            inversions: self.inversions,
            gaps: span.saturating_sub(unique),
            first_seq: self.first_seq,
            last_seq: self.last_seq,
            over_cap: self.over_cap,
            truncated: self.capped,
            rejected_uid: self.rejects[0],
            rejected_cgroup: self.rejects[1],
            rejected_tgid: self.rejects[2],
            rejected_start: self.rejects[3],
        }
    }
}

/// Frozen view of one session's accounting.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TrackSummary {
    pub cap: u64,
    pub decoded: u64,
    pub written: u64,
    pub unique: u64,
    pub duplicates: u64,
    pub inversions: u64,
    pub gaps: u64,
    pub first_seq: Option<u64>,
    pub last_seq: Option<u64>,
    pub over_cap: u64,
    pub truncated: bool,
    pub rejected_uid: u64,
    pub rejected_cgroup: u64,
    pub rejected_tgid: u64,
    pub rejected_start: u64,
}

impl TrackSummary {
    pub fn rejected_total(&self) -> u64 {
        self.rejected_uid + self.rejected_cgroup + self.rejected_tgid + self.rejected_start
    }

    /// A duplicate sequence or a hole below the highest sequence means the
    /// stream is not the stream this account describes.
    pub fn integrity_ok(&self) -> bool {
        self.duplicates == 0 && self.gaps == 0
    }
}

/// Parsed `track` command line.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TrackArgs {
    pub uid: u32,
    pub cgroup_id: u64,
    pub tgid: Option<u32>,
    pub duration_s: u64,
    pub out: String,
}

impl TrackArgs {
    pub fn parse(args: &[String]) -> Result<Self, &'static str> {
        let (mut uid, mut cgroup_id, mut tgid, mut duration_s, mut out) =
            (None, None, None, None, None);
        let mut index = 0;
        while index < args.len() {
            let flag = args[index].as_str();
            let value = args.get(index + 1).ok_or("track flag without a value")?;
            index += 2;
            match flag {
                "--uid" => {
                    if uid.is_some() {
                        return Err("duplicate --uid");
                    }
                    uid = Some(value.parse::<u32>().map_err(|_| "invalid --uid")?);
                }
                "--cgroup-id" => {
                    if cgroup_id.is_some() {
                        return Err("duplicate --cgroup-id");
                    }
                    let parsed = value.parse::<u64>().map_err(|_| "invalid --cgroup-id")?;
                    if parsed == 0 {
                        return Err("--cgroup-id must not be zero");
                    }
                    cgroup_id = Some(parsed);
                }
                "--tgid" => {
                    if tgid.is_some() {
                        return Err("duplicate --tgid");
                    }
                    let parsed = value.parse::<u32>().map_err(|_| "invalid --tgid")?;
                    if parsed == 0 {
                        return Err("--tgid must not be zero");
                    }
                    tgid = Some(parsed);
                }
                "--duration" => {
                    if duration_s.is_some() {
                        return Err("duplicate --duration");
                    }
                    let parsed = value.parse::<u64>().map_err(|_| "invalid --duration")?;
                    if !(1..=TRACK_DURATION_MAX_S).contains(&parsed) {
                        return Err("--duration must be 1..=60");
                    }
                    duration_s = Some(parsed);
                }
                "--out" => {
                    if out.is_some() {
                        return Err("duplicate --out");
                    }
                    if value.is_empty() {
                        return Err("empty --out");
                    }
                    out = Some(value.clone());
                }
                _ => return Err("unknown track flag"),
            }
        }
        Ok(Self {
            uid: uid.ok_or("missing --uid")?,
            cgroup_id: cgroup_id.ok_or("missing --cgroup-id")?,
            tgid,
            duration_s: duration_s.ok_or("missing --duration")?,
            out: out.ok_or("missing --out")?,
        })
    }

    pub fn filter(
        &self,
        ns_tgid: Option<u32>,
        start_ticks: Option<u64>,
        clock_ticks_per_sec: u64,
    ) -> TrackFilter {
        TrackFilter {
            uid: self.uid,
            cgroup_id: self.cgroup_id,
            ns_tgid,
            proc_tgid: self.tgid,
            start_ticks,
            clock_ticks_per_sec,
        }
    }
}

/// How much of the Binder conversation the session's identity filter admits.
///
/// One `track` run gates on a single uid and a single cgroup, so only one side
/// of every Binder conversation can be in scope.  That decides the pairing
/// question: with one side in scope a `binder_transaction` is the target
/// sending work and a `binder_transaction_received` is a *different*
/// transaction being delivered to it, so their `debug_id`s never describe one
/// transaction and cross-side pairing must not be attempted.
pub const BINDER_SCOPE_SINGLE_SIDE: &str = "single_side";

/// The sentence that keeps the closeout from reading as a pairing failure.
pub const BINDER_SCOPE_NOTE: &str = "one uid and one cgroup are in scope, so tx and rx are two different transactions; cross-side pairing is not attempted and paired stays null";

/// Why `reply_rx` cannot be reported at all.
pub const BINDER_REPLY_RX_NOTE: &str =
    "binder_transaction_received prints no reply flag, so it cannot be reported";

/// Direction counters for Binder, with no pairing decision attached.
///
/// t11 measured this directly: in a single-side session the tx and rx
/// `debug_id`s differed by one, which is exactly what two unrelated
/// transactions look like.  Reporting that as "0 paired" would invent a
/// failure, so this type only counts directions.
#[derive(Debug, Default)]
pub struct BinderDirections {
    pub tx: u64,
    pub rx: u64,
    pub reply_tx: u64,
    pub non_reply_tx: u64,
}

impl BinderDirections {
    pub fn note_tx(&mut self, reply: bool) {
        self.tx += 1;
        if reply {
            self.reply_tx += 1;
        } else {
            self.non_reply_tx += 1;
        }
    }

    pub fn note_rx(&mut self) {
        self.rx += 1;
    }

    pub fn to_json(&self) -> Value {
        json!({
            "key": "debug_id",
            "pairing_scope": BINDER_SCOPE_SINGLE_SIDE,
            "pairing_note": BINDER_SCOPE_NOTE,
            "paired": Value::Null,
            "tx_from_target": self.tx,
            "rx_to_target": self.rx,
            "reply_tx": self.reply_tx,
            "non_reply_tx": self.non_reply_tx,
            "reply_rx": Value::Null,
            "reply_rx_note": BINDER_REPLY_RX_NOTE,
            "debug_id_matches": 0,
            "debug_id_matches_note": "not attempted: one side is in scope",
        })
    }
}

/// Attach the pairing scope and the resolved Binder context to one event.
///
/// `paired` is null, not false: with one side in scope the question "did the
/// other half of this transaction show up" has no answer inside the session.
pub fn annotate_scope(mut value: Value, service: Option<&str>) -> Value {
    if let Some(fields) = value.as_object_mut() {
        fields.insert("pairing_scope".to_owned(), json!(BINDER_SCOPE_SINGLE_SIDE));
        fields.insert("paired".to_owned(), Value::Null);
        fields.insert(
            "service".to_owned(),
            match service {
                Some(name) => json!(name),
                None => Value::Null,
            },
        );
    }
    value
}

/// One `proc` block of `/sys/kernel/debug/binder/state`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BinderProc {
    pub pid: u32,
    pub context: Option<String>,
    pub threads: u32,
    pub nodes: u32,
    pub refs: u32,
}

/// The subset of the debugfs `binder state` dump this tool trusts.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct BinderState {
    pub procs: Vec<BinderProc>,
}

impl BinderState {
    /// Binder context ("binder", "hwbinder", "vndbinder") of one pid.
    pub fn context_for(&self, pid: i64) -> Option<&str> {
        if pid <= 0 {
            return None;
        }
        self.procs
            .iter()
            .find(|proc| proc.pid as i64 == pid)
            .and_then(|proc| proc.context.as_deref())
    }

    pub fn to_json(&self) -> Value {
        let records: Vec<Value> = self
            .procs
            .iter()
            .map(|proc| {
                json!({
                    "pid": proc.pid,
                    "context": proc.context,
                    "threads": proc.threads,
                    "nodes": proc.nodes,
                    "refs": proc.refs,
                })
            })
            .collect();
        json!({
            "procs": self.procs.len(),
            "records": records,
        })
    }
}

/// Parse the debugfs `state` dump.
///
/// The kernel prints one `proc <pid>` line followed by `context <name>` and a
/// handful of `  key: value` counters (drivers/android/binder.c).  Anything else is
/// ignored, so a newer kernel adding lines cannot break the parse; a dump that
/// contains no `proc` line simply yields an empty state.
pub fn parse_binder_state(text: &str) -> BinderState {
    let mut state = BinderState::default();
    for raw in text.lines() {
        let line = raw.trim_end();
        if let Some(rest) = line.strip_prefix("proc ") {
            if let Ok(pid) = rest.trim().parse::<u32>() {
                state.procs.push(BinderProc {
                    pid,
                    context: None,
                    threads: 0,
                    nodes: 0,
                    refs: 0,
                });
            }
            continue;
        }
        if let Some(rest) = line.strip_prefix("context ") {
            if let Some(proc) = state.procs.last_mut() {
                let name = rest.trim();
                if !name.is_empty() {
                    proc.context = Some(name.to_owned());
                }
            }
            continue;
        }
        let Some(proc) = state.procs.last_mut() else {
            continue;
        };
        let trimmed = line.trim_start();
        if let Some(rest) = trimmed.strip_prefix("threads: ") {
            proc.threads = rest.trim().parse().unwrap_or(0);
        } else if let Some(rest) = trimmed.strip_prefix("nodes: ") {
            proc.nodes = rest.trim().parse().unwrap_or(0);
        } else if let Some(rest) = trimmed.strip_prefix("refs: ") {
            // "refs: 3 s 1 w 2"
            proc.refs = rest
                .split_whitespace()
                .next()
                .and_then(|first| first.parse().ok())
                .unwrap_or(0);
        }
    }
    state
}


#[cfg(test)]
mod tests {
    use super::*;

    fn blank() -> Vec<u8> {
        vec![0u8; TRACK_EVENT_BYTES]
    }

    fn put_u64(buffer: &mut [u8], offset: usize, value: u64) {
        buffer[offset..offset + 8].copy_from_slice(&value.to_le_bytes());
    }

    fn put_u32(buffer: &mut [u8], offset: usize, value: u32) {
        buffer[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
    }

    fn event(seq: u64, kind: u32, tgid: u32, tid: u32, uid: u32, cgroup: u64, start: u64) -> Vec<u8> {
        let mut bytes = blank();
        put_u64(&mut bytes, 8, (u64::from(tgid) << 32) | u64::from(tid));
        put_u64(&mut bytes, 16, start);
        put_u64(&mut bytes, 24, cgroup);
        put_u64(&mut bytes, 32, seq);
        put_u32(&mut bytes, 80, kind);
        put_u32(&mut bytes, 88, uid);
        bytes[104..107].copy_from_slice(b"app");
        bytes
    }

    fn decode(bytes: &[u8]) -> TrackEvent {
        decode_track(bytes).expect("decodes")
    }

    #[test]
    fn synthetic_decode_header_and_syscall_fields() {
        let mut bytes = event(7, KIND_SYS_ENTER, 4242, 4243, 10123, 55, 999_000_000);
        put_u64(&mut bytes, 0, 123_456_789);
        put_u64(&mut bytes, 40, (11u64 << 32) | 12);
        put_u64(&mut bytes, 48, 0xffff_ffff_0000_0003);
        put_u64(&mut bytes, 56, u64::MAX); // -1 as i64
        put_u64(&mut bytes, 64, 77);
        put_u64(&mut bytes, 72, (-2i64) as u64);
        put_u32(&mut bytes, 84, 6);
        put_u32(&mut bytes, 92, 257);
        let decoded = decode(&bytes);
        assert_eq!(decoded.kind_name(), "sys_enter");
        assert_eq!((decoded.tgid, decoded.tid), (4242, 4243));
        assert_eq!((decoded.ns_tgid, decoded.ns_tid), (11, 12));
        assert_eq!((decoded.uid, decoded.cpu), (10123, 6));
        assert_eq!(decoded.boottime_ns, 123_456_789);
        assert_eq!(decoded.start_ns, 999_000_000);
        assert_eq!(decoded.cgroup_id, 55);
        assert_eq!(decoded.sequence, 7);
        assert_eq!(decoded.nr, 257);
        assert_eq!(decoded.arg0, 0xffff_ffff_0000_0003);
        assert_eq!((decoded.arg1, decoded.arg2), (-1, 77));
        assert_eq!(decoded.result, -2);
        assert_eq!(decoded.comm, "app");
        assert_eq!(decoded.path, None);
        assert!(!decoded.nr_unknown());
    }

    #[test]
    fn synthetic_decode_openat_path_is_bounded() {
        let mut bytes = event(1, KIND_OPENAT, 9, 9, 0, 1, 0);
        let path = b"/data/data/app/file\0";
        bytes[128..128 + path.len()].copy_from_slice(path);
        put_u32(&mut bytes, 100, path.len() as u32);
        put_u64(&mut bytes, 48, u64::MAX); // dirfd = -1
        let decoded = decode(&bytes);
        assert_eq!(decoded.kind_name(), "sys_enter_openat");
        assert_eq!(decoded.path.as_deref(), Some("/data/data/app/file"));
        assert_eq!(decoded.path_len as usize, path.len());
        assert_eq!(decoded.arg0 as i64 as i32, -1);

        // A full 128-byte window (kernel truncation) still decodes.
        let mut full = event(2, KIND_OPENAT, 9, 9, 0, 1, 0);
        for (index, slot) in full[128..256].iter_mut().enumerate() {
            *slot = if index == TRACK_PATH_MAX - 1 { 0 } else { b'a' };
        }
        put_u32(&mut full, 100, TRACK_PATH_MAX as u32);
        let decoded = decode(&full);
        assert_eq!(decoded.path.as_deref().map(str::len), Some(TRACK_PATH_MAX - 1));
    }

    #[test]
    fn synthetic_decode_rejects_abi_violations() {
        assert_eq!(decode_track(&[]).unwrap_err(), "track event length mismatch");
        assert_eq!(
            decode_track(&[0u8; TRACK_EVENT_BYTES - 1]).unwrap_err(),
            "track event length mismatch"
        );
        assert_eq!(
            decode_track(&[0u8; TRACK_EVENT_BYTES + 1]).unwrap_err(),
            "track event length mismatch"
        );
        assert_eq!(decode_track(&blank()).unwrap_err(), "unknown track kind");

        let mut path_window = event(1, KIND_SYS_EXIT, 1, 2, 0, 1, 0);
        put_u32(&mut path_window, 100, 4);
        assert_eq!(
            decode_track(&path_window).unwrap_err(),
            "unexpected path window"
        );

        let mut too_long = event(1, KIND_OPENAT, 1, 2, 0, 1, 0);
        put_u32(&mut too_long, 100, TRACK_PATH_MAX as u32 + 1);
        assert_eq!(
            decode_track(&too_long).unwrap_err(),
            "openat path length out of range"
        );

        let mut empty_path = event(1, KIND_OPENAT, 1, 2, 0, 1, 0);
        put_u32(&mut empty_path, 100, 1);
        assert_eq!(decode_track(&empty_path).unwrap_err(), "empty openat path");

        let no_actor = blank();
        let mut kind_only = no_actor.clone();
        put_u32(&mut kind_only, 80, KIND_SYS_ENTER);
        assert_eq!(
            decode_track(&kind_only).unwrap_err(),
            "zero actor identity"
        );
    }

    fn with_ns(mut bytes: Vec<u8>, ns_tgid: u32, ns_tid: u32) -> Vec<u8> {
        put_u64(&mut bytes, 40, (u64::from(ns_tgid) << 32) | u64::from(ns_tid));
        bytes
    }

    #[test]
    fn synthetic_filter_rejects_each_mismatch() {
        let filter = TrackFilter {
            uid: 10123,
            cgroup_id: 55,
            ns_tgid: Some(4242),
            proc_tgid: Some(4242),
            start_ticks: Some(999),
            clock_ticks_per_sec: 100,
        };
        let ok = decode(&event(1, KIND_SYS_ENTER, 4242, 4243, 10123, 55, 9_990_000_000));
        assert_eq!(filter.check(&ok), Ok(()));

        let wrong_uid = decode(&event(2, KIND_SYS_ENTER, 4242, 4243, 0, 55, 9_990_000_000));
        assert_eq!(filter.check(&wrong_uid), Err(Reject::Uid));

        let wrong_cgroup = decode(&event(3, KIND_SYS_ENTER, 4242, 4243, 10123, 56, 9_990_000_000));
        assert_eq!(filter.check(&wrong_cgroup), Err(Reject::Cgroup));

        let wrong_tgid = decode(&event(4, KIND_SYS_ENTER, 4241, 4243, 10123, 55, 9_990_000_000));
        assert_eq!(filter.check(&wrong_tgid), Err(Reject::Tgid));

        // Same tgid, one tick later: PID reuse, not the same process.
        let reused = decode(&event(5, KIND_SYS_ENTER, 4242, 4243, 10123, 55, 10_000_000_000));
        assert_eq!(filter.check(&reused), Err(Reject::Start));

        let cgroup_only = TrackFilter {
            ns_tgid: None,
            proc_tgid: None,
            start_ticks: None,
            ..filter
        };
        assert_eq!(cgroup_only.check(&reused), Ok(()));
        assert_eq!(cgroup_only.check(&wrong_tgid), Ok(()));
    }

    #[test]
    fn synthetic_filter_accepts_either_pid_namespace_view() {
        // Collector and target in the same namespace: both views are the same
        // number, so a /proc tgid alone is enough.
        let same_ns = TrackFilter {
            uid: 0,
            cgroup_id: 7,
            ns_tgid: Some(21),
            proc_tgid: Some(21),
            start_ticks: Some(5),
            clock_ticks_per_sec: 100,
        };
        let inside = decode(&with_ns(
            event(1, KIND_SYS_ENTER, 21, 22, 0, 7, 50_000_000),
            21,
            22,
        ));
        assert_eq!(same_ns.check(&inside), Ok(()));

        // Target inside its own pid namespace: the kernel-side event carries the
        // namespace tgid (9), /proc told the collector 4242.  Both must be let in.
        let nested = TrackFilter {
            ns_tgid: Some(9),
            proc_tgid: Some(4242),
            ..same_ns
        };
        let namespaced = decode(&with_ns(
            event(2, KIND_SYS_ENTER, 4242, 4243, 0, 7, 50_000_000),
            9,
            10,
        ));
        assert_eq!(nested.check(&namespaced), Ok(()));

        let other = decode(&with_ns(
            event(3, KIND_SYS_ENTER, 4244, 4245, 0, 7, 50_000_000),
            11,
            12,
        ));
        assert_eq!(nested.check(&other), Err(Reject::Tgid));
    }

    #[test]
    fn synthetic_uptime_parses_boot_seconds() {
        assert_eq!(parse_uptime_ns("0.00 0.00\n"), Ok(0));
        assert_eq!(parse_uptime_ns("12345.67 98765.43\n"), Ok(12_345_670_000_000));
        assert_eq!(parse_uptime_ns("7.05"), Ok(7_050_000_000));
        assert_eq!(parse_uptime_ns("7.5"), Err("unexpected uptime precision"));
        assert_eq!(parse_uptime_ns("7.555"), Err("unexpected uptime precision"));
        assert_eq!(parse_uptime_ns("7.xx"), Err("unexpected uptime precision"));
        assert_eq!(parse_uptime_ns("abc.def"), Err("unexpected uptime precision"));
        assert_eq!(parse_uptime_ns("nonsense"), Err("missing uptime fraction"));
        assert_eq!(parse_uptime_ns(""), Err("missing uptime"));
        assert_eq!(
            parse_uptime_ns("18446744074.00"),
            Err("uptime overflow")
        );
    }

    #[test]
    fn synthetic_start_ticks_are_exact_floor_semantics() {
        assert_eq!(tick_ns(100), Some(10_000_000));
        assert_eq!(tick_ns(1000), Some(1_000_000));
        assert_eq!(tick_ns(0), None);
        assert_eq!(tick_ns(u64::MAX), None);

        let tick = tick_ns(100).unwrap();
        let ticks = 1234u64;
        assert!(start_ticks_matches(ticks * tick, ticks, 100));
        assert!(start_ticks_matches(ticks * tick + tick - 1, ticks, 100));
        assert!(!start_ticks_matches((ticks + 1) * tick, ticks, 100));
        assert!(!start_ticks_matches(ticks * tick - 1, ticks, 100));
        assert!(!start_ticks_matches(ticks * tick, ticks, 0));
    }

    #[test]
    fn synthetic_accounting_contiguous_stream_has_no_gaps() {
        let filter = TrackFilter {
            uid: 0,
            cgroup_id: 1,
            ns_tgid: None,
            proc_tgid: None,
            start_ticks: None,
            clock_ticks_per_sec: 100,
        };
        let mut account = TrackAccount::new(TRACK_EVENT_CAP);
        for seq in 0..4u64 {
            let bytes = event(seq, KIND_SYS_ENTER, 5, 6, 0, 1, 0);
            assert_eq!(account.admit(&decode(&bytes), &filter), Verdict::Write);
        }
        let summary = account.summary();
        assert_eq!(summary.unique, 4);
        assert_eq!(summary.written, 4);
        assert_eq!(summary.gaps, 0);
        assert!(summary.integrity_ok());
        assert!(!summary.truncated);
    }

    #[test]
    fn synthetic_accounting_detects_hole_and_duplicate() {
        let filter = TrackFilter {
            uid: 0,
            cgroup_id: 1,
            ns_tgid: None,
            proc_tgid: None,
            start_ticks: None,
            clock_ticks_per_sec: 100,
        };
        let mut account = TrackAccount::new(TRACK_EVENT_CAP);
        for seq in [0u64, 1, 3] {
            let bytes = event(seq, KIND_SYS_EXIT, 5, 6, 0, 1, 0);
            account.admit(&decode(&bytes), &filter);
        }
        let summary = account.summary();
        assert_eq!(summary.unique, 3);
        assert_eq!(summary.gaps, 1);
        assert!(!summary.integrity_ok());

        let mut account = TrackAccount::new(TRACK_EVENT_CAP);
        let repeated = decode(&event(4, KIND_SYS_EXIT, 5, 6, 0, 1, 0));
        assert_eq!(account.admit(&repeated, &filter), Verdict::Write);
        assert_eq!(account.admit(&repeated, &filter), Verdict::Duplicate);
        let summary = account.summary();
        assert_eq!(summary.duplicates, 1);
        assert_eq!(summary.unique, 1);
        assert_eq!(summary.decoded, 2);
        assert!(!summary.integrity_ok());
    }

    #[test]
    fn synthetic_accounting_caps_and_truncates() {
        let filter = TrackFilter {
            uid: 0,
            cgroup_id: 1,
            ns_tgid: None,
            proc_tgid: None,
            start_ticks: None,
            clock_ticks_per_sec: 100,
        };
        let mut account = TrackAccount::new(3);
        for seq in 0..3u64 {
            let bytes = event(seq, KIND_SYS_ENTER, 5, 6, 0, 1, 0);
            assert_eq!(account.admit(&decode(&bytes), &filter), Verdict::Write);
        }
        // A fourth in-flight event is past the cap.
        let bytes = event(3, KIND_SYS_ENTER, 5, 6, 0, 1, 0);
        assert_eq!(account.admit(&decode(&bytes), &filter), Verdict::Capped);
        let summary = account.summary();
        assert_eq!(summary.written, 3);
        assert_eq!(summary.over_cap, 1);
        assert!(summary.truncated);
        assert_eq!(account.written(), 3);
        assert!(account.truncated());
    }

    #[test]
    fn synthetic_accounting_counts_filter_reasons() {
        let filter = TrackFilter {
            uid: 10123,
            cgroup_id: 55,
            ns_tgid: Some(4242),
            proc_tgid: Some(4242),
            start_ticks: Some(1),
            clock_ticks_per_sec: 100,
        };
        let mut account = TrackAccount::new(TRACK_EVENT_CAP);
        let cases = [
            event(0, KIND_SYS_ENTER, 4242, 4243, 0, 55, 10_000_000),
            event(1, KIND_SYS_ENTER, 4242, 4243, 10123, 56, 10_000_000),
            event(2, KIND_SYS_ENTER, 7, 4243, 10123, 55, 10_000_000),
            event(3, KIND_SYS_ENTER, 4242, 4243, 10123, 55, 20_000_000),
            event(4, KIND_BINDER_TX, 4242, 4243, 10123, 55, 10_000_000),
        ];
        for bytes in &cases {
            account.admit(&decode(bytes), &filter);
        }
        let summary = account.summary();
        assert_eq!(summary.rejected_uid, 1);
        assert_eq!(summary.rejected_cgroup, 1);
        assert_eq!(summary.rejected_tgid, 1);
        assert_eq!(summary.rejected_start, 1);
        assert_eq!(summary.rejected_total(), 4);
        assert_eq!(summary.written, 1);
    }

    #[test]
    fn synthetic_json_carries_kind_specific_fields() {
        let binder = decode(&{
            let mut bytes = event(3, KIND_BINDER_TX, 5, 6, 0, 1, 0);
            put_u64(&mut bytes, 48, 0x2a);
            put_u64(&mut bytes, 56, 4242);
            put_u64(&mut bytes, 64, 0x1f);
            put_u32(&mut bytes, 96, 0x10);
            put_u32(&mut bytes, 120, 1);
            put_u32(&mut bytes, 124, 4243);
            bytes
        });
        let json = binder.to_json();
        assert_eq!(json["record"], "event");
        assert_eq!(json["kind"], "binder_transaction");
        assert_eq!(json["debug_id"], 42);
        assert_eq!(json["to_proc_pid"], 4242);
        assert_eq!(json["to_thread_pid"], 4243);
        assert_eq!(json["code"], 31);
        assert_eq!(json["flags"], 16);
        assert_eq!(json["reply"], true);
        assert!(json.get("path").is_none());

        let openat = decode(&{
            let mut bytes = event(4, KIND_OPENAT, 5, 6, 0, 1, 0);
            bytes[128..134].copy_from_slice(b"/a/b\0\0");
            put_u32(&mut bytes, 100, 6);
            bytes
        });
        let json = openat.to_json();
        assert_eq!(json["kind"], "sys_enter_openat");
        assert_eq!(json["path"], "/a/b");
        assert_eq!(json["path_len"], 6);
    }

    #[test]
    fn synthetic_track_args_parse_and_reject() {
        let ok = |argv: &[&str]| TrackArgs::parse(&argv.iter().map(|s| s.to_string()).collect::<Vec<_>>());
        let parsed = ok(&[
            "--uid", "10123", "--cgroup-id", "42", "--tgid", "999", "--duration", "20", "--out",
            "/tmp/x.jsonl",
        ])
        .unwrap();
        assert_eq!(parsed.uid, 10123);
        assert_eq!(parsed.cgroup_id, 42);
        assert_eq!(parsed.tgid, Some(999));
        assert_eq!(parsed.duration_s, 20);
        assert_eq!(parsed.out, "/tmp/x.jsonl");

        let minimal = ok(&["--uid", "0", "--cgroup-id", "7", "--duration", "1", "--out", "o"]).unwrap();
        assert_eq!(minimal.tgid, None);

        for bad in [
            vec!["--uid", "1", "--cgroup-id", "2", "--duration", "0", "--out", "o"],
            vec!["--uid", "1", "--cgroup-id", "2", "--duration", "61", "--out", "o"],
            vec!["--uid", "1", "--cgroup-id", "0", "--duration", "5", "--out", "o"],
            vec!["--uid", "1", "--cgroup-id", "2", "--duration", "5"],
            vec!["--uid", "1", "--cgroup-id", "2", "--duration", "5", "--out", ""],
            vec!["--uid", "1", "--uid", "1", "--cgroup-id", "2", "--duration", "5", "--out", "o"],
            vec!["--uid", "1", "--cgroup-id", "2", "--duration", "5", "--out", "o", "--nope", "x"],
            vec!["--uid", "x", "--cgroup-id", "2", "--duration", "5", "--out", "o"],
            vec!["--uid", "1", "--cgroup-id", "2", "--duration", "5", "--out"],
            vec!["--tgid", "0", "--uid", "1", "--cgroup-id", "2", "--duration", "5", "--out", "o"],
        ] {
            assert!(ok(&bad).is_err(), "accepted invalid argv {bad:?}");
        }
    }
}


#[cfg(test)]
mod binder_scope_tests {
    use super::*;

    const DUMP: &str = "binder state:
proc 1234
context hwbinder
  threads: 7
  requested threads: 0+0/15
  ready threads 1
  free async space 520192
  nodes: 3
  refs: 9 s 4 w 5
  buffers: 2
  pending transactions: 0
proc 5678
context binder
  threads: 2
  nodes: 0
  refs: 1 s 1 w 0
proc 9999
  threads: 1
";

    #[test]
    fn binder_state_parses_proc_blocks_and_contexts() {
        let state = parse_binder_state(DUMP);
        assert_eq!(state.procs.len(), 3);
        assert_eq!(state.procs[0].pid, 1234);
        assert_eq!(state.procs[0].context.as_deref(), Some("hwbinder"));
        assert_eq!(state.procs[0].threads, 7);
        assert_eq!(state.procs[0].nodes, 3);
        assert_eq!(state.procs[0].refs, 9);
        assert_eq!(state.context_for(5678), Some("binder"));
        // A proc that never printed a context line keeps None; the tool must
        // not invent a service name for it.
        assert_eq!(state.procs[2].context, None);
        assert_eq!(state.context_for(9999), None);
        assert_eq!(state.context_for(0), None);
        assert_eq!(state.context_for(-5), None);
        assert_eq!(state.context_for(4242), None);
        let json = state.to_json();
        assert_eq!(json["procs"], 3);
        assert_eq!(json["records"][0]["context"], "hwbinder");
        assert!(json["records"][2]["context"].is_null());
    }

    #[test]
    fn binder_state_tolerates_junk_and_empty_dumps() {
        let empty = parse_binder_state("");
        assert_eq!(empty.procs.len(), 0);
        assert_eq!(empty.context_for(1), None);
        let junk = parse_binder_state("binder state:\ndead nodes:\n  node 4: u0 c0\n  threads: 3\n");
        assert_eq!(junk.procs.len(), 0);
        // A context line before any proc must not create a proc record.
        let orphan = parse_binder_state("context hwbinder\nproc 77\ncontext vndbinder\n");
        assert_eq!(orphan.procs.len(), 1);
        assert_eq!(orphan.context_for(77), Some("vndbinder"));
    }

    #[test]
    fn binder_directions_count_both_sides_without_pairing() {
        let mut directions = BinderDirections::default();
        directions.note_tx(false);
        directions.note_tx(true);
        directions.note_tx(false);
        directions.note_rx();
        directions.note_rx();
        assert_eq!(directions.tx, 3);
        assert_eq!(directions.reply_tx, 1);
        assert_eq!(directions.non_reply_tx, 2);
        assert_eq!(directions.rx, 2);
    }

    #[test]
    fn closeout_declares_single_side_and_never_claims_a_pairing_failure() {
        let mut directions = BinderDirections::default();
        directions.note_tx(false);
        directions.note_rx();
        let json = directions.to_json();
        assert_eq!(json["key"], "debug_id");
        assert_eq!(json["pairing_scope"], "single_side");
        assert_eq!(json["tx_from_target"], 1);
        assert_eq!(json["rx_to_target"], 1);
        assert_eq!(json["reply_tx"], 0);
        assert_eq!(json["non_reply_tx"], 1);
        // The two halves of a single-side session are different transactions,
        // so "paired" has no answer and must not be reported as false.
        assert!(json["paired"].is_null());
        assert!(json["reply_rx"].is_null());
        assert_eq!(json["debug_id_matches"], 0);
        let note = json["pairing_note"].as_str().expect("note is a string");
        assert!(note.contains("not attempted"), "note explains the scope");
        let reply_note = json["reply_rx_note"].as_str().expect("reply note");
        assert!(reply_note.contains("no reply flag"));
        // No failure wording anywhere in the closeout block.
        let text = serde_json::to_string(&json).expect("serialises");
        for banned in ["fail", "unpaired", "without_rx", "without_tx", "mismatch"] {
            assert!(!text.contains(banned), "closeout must not say {banned}: {text}");
        }
    }

    #[test]
    fn annotate_scope_marks_the_scope_and_the_service() {
        let with_service = annotate_scope(json!({"record": "event"}), Some("hwbinder"));
        assert_eq!(with_service["pairing_scope"], "single_side");
        assert!(with_service["paired"].is_null());
        assert_eq!(with_service["service"], "hwbinder");
        let without = annotate_scope(json!({"record": "event"}), None);
        assert_eq!(without["pairing_scope"], "single_side");
        assert!(without["service"].is_null());
        // An event that carries no binder context keeps whatever it had.
        assert_eq!(without["record"], "event");
    }

}
