//! Linux runtime for the `track` subcommand: external-target tracking
//! gated in-kernel by cgroup id + uid and refined here by tgid/start_ticks.
//!
//! This module is the only place in the crate that names an external PID.  It
//! never starts, stops or controls the target: it only loads its own BPF
//! programs, drains its own ring buffer, and drops every descriptor on the way
//! out.

use aya::{
    maps::{Array, RingBuf},
    programs::{loaded_programs, RawTracePoint},
    Ebpf,
};
use kebab_observer::{
    aux_clock_ticks, start_ticks,
    tls_core::fnv1a64,
    track_core::{
        annotate_scope, decode_track, parse_binder_state, parse_uptime_ns, tick_ns, BinderDirections,
        BinderState, TrackAccount, TrackArgs, TrackEvent, TrackFilter, Verdict, BINDER_SCOPE_SINGLE_SIDE,
        CFG_CAP, CFG_CGROUP, CFG_DEADLINE, CFG_EXCLUDE_TGID, CFG_PIDNS_DEV, CFG_PIDNS_INO,
        CFG_TGID, CFG_UID,
        CNT_BINDER, CNT_GATE, CNT_LOST, CNT_MAP_FAILED, CNT_OPENAT, CNT_PATH_LONG, CNT_READ_FAILED,
        CNT_SUBMITTED, CNT_SYS_ENTER, CNT_SYS_EXIT, CNT_TRUNCATED, CNT_USER_READ_FAILED,
        COUNTER_SLOTS, KIND_BINDER_RX, KIND_BINDER_TX, KIND_OPENAT, KIND_SYS_ENTER, KIND_SYS_EXIT,
        TRACK_EVENT_BYTES, TRACK_EVENT_CAP, TRACK_RING_BYTES, TRACK_USAGE,
    },
};
use serde_json::json;
use std::{
    error::Error,
    fs::{self, OpenOptions},
    io::{BufWriter, Write},
    os::unix::fs::{MetadataExt, OpenOptionsExt},
    path::Path,
    thread,
    time::{Duration, Instant},
};

type Result<T> = std::result::Result<T, Box<dyn Error>>;

#[cfg(target_arch = "aarch64")]
const OBJECT: &[u8] = aya::include_bytes_aligned!("../bpf/track.arm64.bpf.o");
#[cfg(target_arch = "aarch64")]
const OBJECT_NAME: &str = "track.arm64.bpf.o";
#[cfg(target_arch = "x86_64")]
const OBJECT: &[u8] = aya::include_bytes_aligned!("../bpf/track.x86_64.bpf.o");
#[cfg(target_arch = "x86_64")]
const OBJECT_NAME: &str = "track.x86_64.bpf.o";

/// Syscall programs are mandatory: without them the session is not a session.
const SYSCALL_PROGRAMS: [(&str, &str); 3] = [
    ("track_sys_enter", "sys_enter"),
    ("track_openat", "sys_enter"),
    ("track_sys_exit", "sys_exit"),
];
/// Binder programs are best effort: a kernel without Binder BTF must say so
/// instead of pretending the session covered Binder.
const BINDER_PROGRAMS: [(&str, &str); 2] = [
    ("track_binder_tx", "binder_transaction"),
    ("track_binder_rx", "binder_transaction_received"),
];

struct Target {
    tgid: u32,
    ns_tgid: u32,
    start_ticks: u64,
    start_boot_ns: u64,
    uid: u32,
    cgroup_id: u64,
    cgroup_path: String,
    pidns_dev: u64,
    pidns_ino: u64,
}

fn boottime_ns() -> Result<u64> {
    Ok(parse_uptime_ns(&fs::read_to_string("/proc/uptime")?)?)
}

/// Resolve the named target and *prove* the caller's claim about it: the uid
/// and the cgroup id in the command line must match what /proc and the
/// cgroupfs inode say, otherwise the run is refused before a probe is loaded.
fn resolve_target(tgid: u32, want_cgroup: u64, want_uid: u32) -> Result<Target> {
    let base = format!("/proc/{tgid}");
    let ticks = start_ticks(&fs::read_to_string(format!("{base}/stat"))?)?;
    let status = fs::read_to_string(format!("{base}/status"))?;
    let uid: u32 = status
        .lines()
        .find_map(|line| line.strip_prefix("Uid:"))
        .ok_or("target uid missing")?
        .split_whitespace()
        .next()
        .ok_or("invalid target uid")?
        .parse()?;
    if uid != want_uid {
        return Err(format!("target uid {uid} does not match --uid {want_uid}").into());
    }
    let cgroup_path = fs::read_to_string(format!("{base}/cgroup"))?
        .lines()
        .find_map(|line| line.strip_prefix("0::"))
        .ok_or("target cgroup v2 path missing")?
        .trim()
        .to_owned();
    let directory = Path::new("/sys/fs/cgroup").join(cgroup_path.trim_start_matches('/'));
    let inode = fs::metadata(&directory)?.ino();
    if inode != want_cgroup {
        return Err(format!(
            "target cgroup inode {inode} does not match --cgroup-id {want_cgroup}"
        )
        .into());
    }
    // NSpid lists the pid in every namespace the task belongs to, outermost
    // first, so the last entry is the pid inside the task's own namespace: that
    // is the number the BPF side compares with bpf_get_ns_current_pid_tgid.
    let ns_tgid = status
        .lines()
        .find_map(|line| line.strip_prefix("NSpid:"))
        .and_then(|list| list.split_whitespace().last())
        .and_then(|last| last.parse::<u32>().ok())
        .unwrap_or(tgid);
    let namespace = fs::metadata(format!("{base}/ns/pid"))?;
    Ok(Target {
        tgid,
        ns_tgid,
        start_ticks: ticks,
        start_boot_ns: 0,
        uid,
        cgroup_id: inode,
        cgroup_path,
        pidns_dev: namespace.dev(),
        pidns_ino: namespace.ino(),
    })
}

/// Verifier logs are kilobytes long; the record keeps the actionable first line
/// and the length, so a JSONL session line stays readable.
fn short_error(error: &str) -> (String, usize) {
    let first = error.lines().next().unwrap_or_default();
    (first.chars().take(320).collect(), error.len())
}

const BINDER_STATE_PATH: &str = "/sys/kernel/debug/binder/state";

/// Take the debugfs Binder snapshot and persist it next to the session file.
///
/// The tracepoints carry pids only, so the pid -> context mapping has to come
/// from debugfs while the target is alive.  A missing or unreadable dump is
/// recorded, never fatal: an unprivileged session simply has no service names
/// and every event says so instead of guessing one.
fn binder_snapshot(out: &Path) -> (BinderState, std::path::PathBuf, Option<String>) {
    let text_path = out.with_extension("binder-state.txt");
    let json_path = out.with_extension("binder-state.json");
    match fs::read_to_string(BINDER_STATE_PATH) {
        Ok(text) => {
            let state = parse_binder_state(&text);
            let _ = fs::write(&text_path, &text);
            let _ = fs::write(&json_path, state.to_json().to_string());
            (state, text_path, None)
        }
        Err(error) => (BinderState::default(), text_path, Some(error.to_string())),
    }
}


/// Streams decoded events to the session file and counts Binder directions.
///
/// Nothing is held back and nothing is paired: with one uid and one cgroup in
/// scope the tx and rx halves of a Binder conversation belong to different
/// transactions, so every event is written immediately with
/// `pairing_scope=single_side` and `paired=null`.  Deferring events to pair
/// them would be inventing a relationship the filter cannot see.
struct Emitter<'a, W: Write> {
    out: &'a mut W,
    state: &'a BinderState,
    directions: BinderDirections,
    kinds: [u64; 6],
}

impl<W: Write> Emitter<'_, W> {
    fn new<'a>(out: &'a mut W, state: &'a BinderState) -> Emitter<'a, W> {
        Emitter {
            out,
            state,
            directions: BinderDirections::default(),
            kinds: [0u64; 6],
        }
    }

    fn service_for(&self, pid: i64) -> Option<String> {
        self.state.context_for(pid).map(|name| name.to_owned())
    }

    fn note_kind(&mut self, kind: u32) {
        if let Some(slot) = self.kinds.get_mut(kind as usize) {
            *slot += 1;
        }
    }

    /// Write one admitted event with its pairing scope and Binder context.
    fn write(&mut self, event: &TrackEvent) -> std::io::Result<()> {
        match event.kind {
            KIND_BINDER_TX => {
                self.directions.note_tx(event.reply != 0);
                let service = self.service_for(event.arg1);
                let value = annotate_scope(event.to_json(), service.as_deref());
                writeln!(self.out, "{value}")?;
                self.note_kind(event.kind);
            }
            KIND_BINDER_RX => {
                self.directions.note_rx();
                let service = self.service_for(event.tgid as i64);
                let value = annotate_scope(event.to_json(), service.as_deref());
                writeln!(self.out, "{value}")?;
                self.note_kind(event.kind);
            }
            _ => {
                writeln!(self.out, "{}", event.to_json())?;
                self.note_kind(event.kind);
            }
        }
        Ok(())
    }

    fn into_parts(self) -> ([u64; 6], BinderDirections) {
        (self.kinds, self.directions)
    }
}


pub fn run(args: &[String]) -> u8 {
    let parsed = match TrackArgs::parse(args) {
        Ok(parsed) => parsed,
        Err(reason) => {
            eprintln!("track rejected: {reason}");
            eprintln!("{TRACK_USAGE}");
            return 2;
        }
    };
    match session(&parsed) {
        Ok(true) => 0,
        Ok(false) => 1,
        Err(error) => {
            eprintln!("track blocked: {error}");
            3
        }
    }
}

fn session(args: &TrackArgs) -> Result<bool> {
    let clock_ticks = aux_clock_ticks(&fs::read("/proc/self/auxv")?)?;
    let tick = tick_ns(clock_ticks).ok_or("unsupported CLK_TCK in auxv")?;
    let mut target = match args.tgid {
        Some(tgid) => Some(resolve_target(tgid, args.cgroup_id, args.uid)?),
        None => None,
    };
    if let Some(target) = target.as_mut() {
        target.start_boot_ns = target.start_ticks * tick;
    }
    let boot_id = fs::read_to_string("/proc/sys/kernel/random/boot_id")?
        .trim()
        .to_owned();
    let kernel = fs::read_to_string("/proc/sys/kernel/osrelease")?
        .trim()
        .to_owned();
    let started_ns = boottime_ns()?;
    let deadline_ns = started_ns + args.duration_s * 1_000_000_000;
    let mut out = BufWriter::with_capacity(
        1 << 16,
        OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&args.out)?,
    );

    let (binder_state, binder_state_path, binder_state_error) =
        binder_snapshot(Path::new(&args.out));

    let mut bpf = Ebpf::load(OBJECT)?;
    {
        let mut config = Array::<_, u64>::try_from(bpf.map_mut("CONFIG").ok_or("CONFIG missing")?)?;
        let values = [
            (CFG_CGROUP, args.cgroup_id),
            (CFG_UID, u64::from(args.uid)),
            (CFG_TGID, target.as_ref().map_or(0, |t| u64::from(t.ns_tgid))),
            (CFG_DEADLINE, deadline_ns),
            (CFG_CAP, TRACK_EVENT_CAP),
            (CFG_PIDNS_DEV, target.as_ref().map_or(0, |t| t.pidns_dev)),
            (CFG_PIDNS_INO, target.as_ref().map_or(0, |t| t.pidns_ino)),
            (CFG_EXCLUDE_TGID, u64::from(std::process::id())),
        ];
        for (index, value) in values {
            config.set(index, value, 0)?;
        }
    }
    let mut program_ids = Vec::new();
    for (name, event) in SYSCALL_PROGRAMS {
        let program: &mut RawTracePoint = bpf
            .program_mut(name)
            .ok_or("syscall program missing")?
            .try_into()?;
        program.load()?;
        program_ids.push(program.info()?.id());
        program.attach(event)?;
    }
    let mut binder_error = None;
    let mut binder_error_bytes: Option<usize> = None;
    for (name, event) in BINDER_PROGRAMS {
        let attempted = (|| -> Result<()> {
            let program: &mut RawTracePoint = bpf
                .program_mut(name)
                .ok_or("binder program missing")?
                .try_into()?;
            program.load()?;
            program_ids.push(program.info()?.id());
            program.attach(event)?;
            Ok(())
        })();
        if let Err(error) = attempted {
            let (text, total) = short_error(&error.to_string());
            binder_error = Some(format!("{name}: {text}"));
            binder_error_bytes = Some(total);
        }
    }
    if let Some(previous) = target.as_ref() {
        let again = resolve_target(previous.tgid, previous.cgroup_id, previous.uid)?;
        if again.start_ticks != previous.start_ticks || again.pidns_ino != previous.pidns_ino {
            return Err("target identity changed between resolve and attach".into());
        }
    }
    let mut ring = RingBuf::try_from(bpf.take_map("EVENTS").ok_or("EVENTS missing")?)?;
    let counters = Array::<_, u64>::try_from(bpf.take_map("COUNTERS").ok_or("COUNTERS missing")?)?;

    let session_record = json!({
        "record": "session",
        "schema": 1,
        "mode": "track",
        "boot_id": boot_id,
        "kernel": kernel,
        "started_at_boot_ns": started_ns,
        "deadline_boot_ns": deadline_ns,
        "duration_s": args.duration_s,
        "requested": {"uid": args.uid, "cgroup_id": args.cgroup_id, "tgid": args.tgid},
        "resolved": target.as_ref().map(|t| json!({
            "tgid": t.tgid,
            "ns_tgid": t.ns_tgid,
            "uid": t.uid,
            "start_ticks": t.start_ticks,
            "start_boot_ns": t.start_boot_ns,
            "cgroup_id": t.cgroup_id,
            "cgroup_path": t.cgroup_path,
            "pidns_dev": t.pidns_dev,
            "pidns_ino": t.pidns_ino,
        })),
        "clock_ticks_per_sec": clock_ticks,
        "tick_ns": tick,
        "event_cap": TRACK_EVENT_CAP,
        "record_bytes": TRACK_EVENT_BYTES,
        "ring_bytes": TRACK_RING_BYTES,
        "object": OBJECT_NAME,
        "object_bytes": OBJECT.len(),
        "object_fnv1a64": format!("{:016x}", fnv1a64(OBJECT)),
        "program_ids": program_ids,
        "attach": "raw_tracepoint",
        "binder_attached": binder_error.is_none(),
        "binder_error": binder_error,
        "binder_error_bytes": binder_error_bytes,
        "binder_state_path": binder_state_path.to_string_lossy(),
        "binder_state_procs": binder_state.procs.len(),
        "binder_state_error": binder_state_error.clone(),
        "binder_pairing_scope": BINDER_SCOPE_SINGLE_SIDE,
        "user_memory_read": true,
        "reads_only_openat_path": true,
        "tracefs_touched": false,
        "target_controlled": false,
    });
    writeln!(out, "{session_record}")?;
    println!("{session_record}");

    let filter: TrackFilter = args.filter(
        target.as_ref().map(|t| t.ns_tgid),
        target.as_ref().map(|t| t.start_ticks),
        clock_ticks,
    );
    let mut account = TrackAccount::new(TRACK_EVENT_CAP);
    let mut decode_errors = 0u64;
    let mut emitter = Emitter::new(&mut out, &binder_state);
    let wait = Duration::from_secs(args.duration_s) + Duration::from_millis(500);
    let begin = Instant::now();
    let mut quiet = 0u32;
    while begin.elapsed() <= wait {
        let mut batch = 0u32;
        while batch < 512 {
            let Some(bytes) = ring.next() else { break };
            batch += 1;
            match decode_track(&bytes) {
                Ok(event) => {
                    if let Verdict::Write = account.admit(&event, &filter) {
                        emitter.write(&event)?;
                    }
                }
                Err(_) => decode_errors += 1,
            }
        }
        if batch == 0 {
            quiet += 1;
        } else {
            quiet = 0;
        }
        if account.written() >= TRACK_EVENT_CAP && quiet >= 4 {
            break;
        }
        thread::sleep(Duration::from_millis(5));
    }

    // Dropping the object detaches every program this process owns; the ring
    // buffer is already ours, so anything still queued can be drained after.
    drop(bpf);
    let mut drained = 0u64;
    while let Some(bytes) = ring.next() {
        drained += 1;
        match decode_track(&bytes) {
            Ok(event) => {
                if let Verdict::Write = account.admit(&event, &filter) {
                    emitter.write(&event)?;
                }
            }
            Err(_) => decode_errors += 1,
        }
    }
    let (kinds, directions) = emitter.into_parts();
    let counts = (0..COUNTER_SLOTS)
        .map(|index| counters.get(&index, 0).unwrap_or(0))
        .collect::<Vec<_>>();
    let submitted = counts[CNT_SUBMITTED as usize];
    let bpf_truncated = counts[CNT_TRUNCATED as usize];
    let lost = counts[CNT_LOST as usize];
    let summary = account.summary();
    let truncated = summary.truncated || bpf_truncated > 0;
    let mut absent = false;
    for _ in 0..100 {
        let remaining = loaded_programs()
            .map(|info| info.map(|program| program.id()))
            .collect::<std::result::Result<Vec<_>, _>>()?;
        absent = program_ids.iter().all(|id| !remaining.contains(id));
        if absent {
            break;
        }
        thread::sleep(Duration::from_millis(10));
    }
    drop(ring);
    drop(counters);

    let mut failures = Vec::new();
    if decode_errors > 0 {
        failures.push("undecodable_records");
    }
    if !summary.integrity_ok() {
        failures.push("sequence_integrity");
    }
    if submitted != summary.decoded {
        failures.push("ring_not_fully_accounted");
    }
    if !absent {
        failures.push("program_ids_present");
    }
    if summary.over_cap > 0 {
        failures.push("event_past_cap");
    }
    if summary.written > TRACK_EVENT_CAP {
        failures.push("written_past_cap");
    }
    let passed = failures.is_empty();
    let closeout = json!({
        "record": "closeout",
        "mode": "track",
        "passed": passed,
        "failures": failures,
        "events_written": summary.written,
        "decoded": summary.decoded,
        "ring_drained_after_detach": drained,
        "unique_sequence": summary.unique,
        "first_sequence": summary.first_seq,
        "last_sequence": summary.last_seq,
        "sequence_gaps": summary.gaps,
        "duplicates": summary.duplicates,
        "inversions": summary.inversions,
        "event_cap": TRACK_EVENT_CAP,
        "over_cap": summary.over_cap,
        "truncated": truncated,
        "truncated_by_bpf": bpf_truncated,
        "lost": lost,
        "filtered": {
            "uid": summary.rejected_uid,
            "cgroup": summary.rejected_cgroup,
            "tgid": summary.rejected_tgid,
            "start_ticks": summary.rejected_start,
            "total": summary.rejected_total(),
        },
        "kinds": {
            "sys_enter": kinds[KIND_SYS_ENTER as usize],
            "sys_exit": kinds[KIND_SYS_EXIT as usize],
            "openat": kinds[KIND_OPENAT as usize],
            "binder_transaction": kinds[KIND_BINDER_TX as usize],
            "binder_transaction_received": kinds[KIND_BINDER_RX as usize],
        },
        "counts": {
            "gate": counts[CNT_GATE as usize],
            "submitted": submitted,
            "lost": lost,
            "truncated": bpf_truncated,
            "map_failed": counts[CNT_MAP_FAILED as usize],
            "read_failed": counts[CNT_READ_FAILED as usize],
            "user_read_failed": counts[CNT_USER_READ_FAILED as usize],
            "openat": counts[CNT_OPENAT as usize],
            "binder": counts[CNT_BINDER as usize],
            "path_long": counts[CNT_PATH_LONG as usize],
            "sys_enter": counts[CNT_SYS_ENTER as usize],
            "sys_exit": counts[CNT_SYS_EXIT as usize],
        },
        "ring_fully_accounted": submitted == summary.decoded,
        "decode_errors": decode_errors,
        "program_ids_absent": absent,
        "binder_attached": binder_error.is_none(),
        "binder_error": binder_error,
        "binder_pairs": directions.to_json(),
        "binder_state": {
            "path": binder_state_path.to_string_lossy(),
            "procs": binder_state.procs.len(),
            "error": binder_state_error,
        },
        "empty_session": summary.written == 0,
    });
    writeln!(out, "{closeout}")?;
    out.flush()?;
    out.get_ref().sync_all()?;
    println!("{closeout}");
    Ok(passed)
}


