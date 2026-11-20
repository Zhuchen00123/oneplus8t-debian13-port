//! Linux-only runtime for the own-TLS-fixture observer.
//!
//! One process owns exactly one private run directory, one C TLS workload and
//! one pair of uprobes on the classic SSL_write of the system libssl.  Nothing
//! here attaches by name, uses pid=None, writes tracefs, or touches another
//! process memory or return values.

use aya::{
    maps::{Array, RingBuf},
    programs::{loaded_programs, UProbe},
    Ebpf,
};
use kebab_observer::tls_core as core;
use serde_json::{json, Value};
use std::{
    fs::{self, DirBuilder, OpenOptions},
    io::{BufRead, BufReader, Read, Write},
    os::unix::{
        fs::{DirBuilderExt, MetadataExt, OpenOptionsExt},
        process::CommandExt,
    },
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::mpsc,
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

#[cfg(target_arch = "x86_64")]
const TLS_OBJECT: &[u8] = aya::include_bytes_aligned!("../bpf/tls_fixture.x86_64.bpf.o");
#[cfg(target_arch = "x86_64")]
const TLS_OBJECT_NAME: &str = "tls_fixture.x86_64.bpf.o";
#[cfg(target_arch = "aarch64")]
const TLS_OBJECT: &[u8] = aya::include_bytes_aligned!("../bpf/tls_fixture.arm64.bpf.o");
#[cfg(target_arch = "aarch64")]
const TLS_OBJECT_NAME: &str = "tls_fixture.arm64.bpf.o";
const SYSCALL_OBJECT: &[u8] = aya::include_bytes_aligned!("../bpf/syscalls.bpf.o");
const SYSCALL_OBJECT_NAME: &str = "syscalls.bpf.o";

const EXPECTED_BUILD_ID: &str = "4f18cdec63410d6a18616448c922fb580ca2c292";
const FIXTURE_UID: u32 = 1000;
const FIXTURE_GID: u32 = 1000;
const SESSION_SECONDS: u64 = 2;
const CRASH_SESSION_SECONDS: u64 = 6;
const CRASH_HOLD_SECONDS: u64 = 60;
const BATCH_BUDGET_SECONDS: u64 = 55;

/// No workload digest is pinned in this revision.  The batch records the
/// pre-spawn and post-session digests side by side so a verifier can bind them
/// to the build artifact; when a digest is pinned here a mismatch is FAIL.
const EXPECTED_WORKLOAD_SHA256: Option<&str> = None;

/// Exit code for a failing batch.
pub const EXIT_FAIL: u8 = core::EXIT_FAIL;
/// Exit code for a rejected invocation.
pub const EXIT_USAGE: u8 = core::EXIT_USAGE;
/// Exit code for a blocked environment.
pub const EXIT_BLOCKED: u8 = core::EXIT_BLOCKED;

/// Why a step could not proceed.
#[derive(Debug, Clone)]
pub struct Failure {
    /// True when the environment (not the implementation) is insufficient.
    pub blocked: bool,
    /// Human-readable reasons; never contain payload bytes.
    pub reasons: Vec<String>,
}

impl Failure {
    fn failed(reason: impl Into<String>) -> Self {
        Failure {
            blocked: false,
            reasons: vec![reason.into()],
        }
    }
    fn blocked(reason: impl Into<String>) -> Self {
        Failure {
            blocked: true,
            reasons: vec![reason.into()],
        }
    }
    fn describe(&self) -> String {
        format!(
            "{}: {}",
            if self.blocked { "blocked" } else { "failed" },
            self.reasons.join("; ")
        )
    }
}

fn read_proc(path: &str) -> Result<String, String> {
    fs::read_to_string(path).map_err(|e| format!("read {path}: {e}"))
}

fn proc_status(pid: u32) -> Result<String, String> {
    read_proc(&format!("/proc/{pid}/status"))
}

fn proc_stat_ticks(pid: u32) -> Result<u64, String> {
    let text = read_proc(&format!("/proc/{pid}/stat"))?;
    kebab_observer::start_ticks(&text).map_err(|e| e.to_string())
}

fn ns_identity(pid: u32) -> Result<(u64, u64, u64), String> {
    // Follow the magic link: the C workload reports stat("/proc/self/ns/pid"),
    // so both sides must resolve the same nsfs inode (dev=4 on this host).
    let md = fs::metadata(format!("/proc/{pid}/ns/pid")).map_err(|e| format!("stat ns: {e}"))?;
    // The procfs magic-link inode is only recorded for the audit trail; it is
    // never used for identity decisions.
    let link = fs::symlink_metadata(format!("/proc/{pid}/ns/pid"))
        .map_err(|e| format!("lstat ns link: {e}"))?;
    Ok((md.dev(), md.ino(), link.ino()))
}

fn cgroup_text(pid: u32) -> Result<String, String> {
    read_proc(&format!("/proc/{pid}/cgroup"))
}

fn fd_entries(pid: u32) -> Result<Vec<(u32, String)>, String> {
    let mut out = Vec::new();
    let dir = fs::read_dir(format!("/proc/{pid}/fd")).map_err(|e| format!("read fd dir: {e}"))?;
    for entry in dir {
        let entry = entry.map_err(|e| format!("fd entry: {e}"))?;
        let name = entry.file_name().to_string_lossy().into_owned();
        let fd: u32 = name.parse().map_err(|_| "fd name malformed".to_string())?;
        let target = fs::read_link(entry.path()).map_err(|e| format!("readlink: {e}"))?;
        out.push((fd, target.to_string_lossy().into_owned()));
    }
    out.sort_by_key(|(fd, _)| *fd);
    Ok(out)
}

fn count_fds() -> Result<usize, String> {
    let dir = fs::read_dir("/proc/self/fd").map_err(|e| format!("read own fd dir: {e}"))?;
    let mut n = 0usize;
    for entry in dir {
        entry.map_err(|e| format!("own fd entry: {e}"))?;
        n += 1;
    }
    Ok(n.saturating_sub(1))
}

fn boottime_ns() -> Result<u64, String> {
    let text = read_proc("/proc/uptime")?;
    let mut parts = text
        .split_whitespace()
        .next()
        .ok_or("missing uptime")?
        .split('.');
    let seconds: u64 = parts
        .next()
        .ok_or("missing seconds")?
        .parse()
        .map_err(|_| "bad uptime")?;
    let fraction = parts.next().ok_or("missing uptime fraction")?;
    if fraction.len() != 2 {
        return Err("unexpected uptime precision".into());
    }
    Ok(seconds
        .checked_mul(1_000_000_000)
        .ok_or("uptime overflow")?
        .checked_add(fraction.parse::<u64>().map_err(|_| "bad fraction")? * 10_000_000)
        .ok_or("uptime overflow")?)
}

fn now_unix() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn boot_id() -> Result<String, String> {
    let raw = read_proc("/proc/sys/kernel/random/boot_id")?;
    let id = raw.trim().to_string();
    if id.len() != 36 || id.chars().filter(|c| *c == '-').count() != 4 {
        return Err("boot_id has an unexpected shape".into());
    }
    Ok(id)
}

fn clock_ticks() -> Result<u64, String> {
    let auxv = fs::read("/proc/self/auxv").map_err(|e| format!("read auxv: {e}"))?;
    kebab_observer::aux_clock_ticks(&auxv).map_err(|e| e.to_string())
}

fn random_bytes() -> Result<[u8; 8], String> {
    // /dev/urandom is a character device: fs::read would block forever waiting
    // for EOF, so read exactly the bytes we need.
    let mut file = fs::File::open("/dev/urandom").map_err(|e| format!("open urandom: {e}"))?;
    let mut raw = [0u8; 8];
    file.read_exact(&mut raw)
        .map_err(|e| format!("read urandom: {e}"))?;
    Ok(raw)
}

fn make_run_id() -> Result<String, String> {
    Ok(core::hex_lower(&random_bytes()?))
}

/// Kernel release string of this host, as the contract's batch field.
fn kernel_string() -> Result<String, String> {
    let text = read_proc("/proc/sys/kernel/osrelease")?;
    let text = text.trim();
    if text.is_empty() {
        return Err("kernel release is empty".into());
    }
    Ok(text.to_string())
}

/// uprobe PMU type from sysfs: the perf_event_open flavour the attach uses.
fn uprobe_pmu_type() -> Result<u64, String> {
    let text = read_proc("/sys/bus/event_source/devices/uprobe/type")?;
    let text = text.trim();
    let value: u64 = text
        .parse()
        .map_err(|_| format!("uprobe PMU type {text:?} is not a number"))?;
    if value == 0 {
        return Err("uprobe PMU type is zero".into());
    }
    Ok(value)
}

/// cgroup v2 path of a process plus the kernfs inode its cgroup id reports.
fn cgroup_facts(pid: u32) -> Result<(String, u64), String> {
    let text = cgroup_text(pid)?;
    let path = text
        .rsplit(':')
        .next()
        .map(|s| s.trim().to_string())
        .filter(|s| s.starts_with('/'))
        .ok_or("cgroup line has no absolute path")?;
    let dir = if path == "/" {
        "/sys/fs/cgroup".to_string()
    } else {
        format!("/sys/fs/cgroup{path}")
    };
    let meta = fs::metadata(&dir).map_err(|e| format!("stat cgroup dir {dir}: {e}"))?;
    let ino = meta.ino();
    if ino == 0 {
        return Err("cgroup dir inode is zero".into());
    }
    Ok((path, ino))
}

/// Symbol version and file offset of SSL_write inside the mapped library.
fn libssl_symbol(path: &str) -> Result<core::SymbolInfo, Failure> {
    let image = fs::read(path).map_err(|e| Failure::failed(format!("read {path}: {e}")))?;
    core::elf_symbol_info(&image, "SSL_write")
        .map_err(|e| Failure::failed(format!("SSL_write symbol info in {path}: {e}")))
}

fn capability_bits() -> Result<u64, String> {
    let status = read_proc("/proc/self/status")?;
    core::cap_eff(&status).map_err(|e| e.to_string())
}

/// Declared BPF map memory this object asks the kernel for.
///
/// This is the logical sum of the nine map declarations of the embedded object
/// (`core::BPF_MAP_DECLS`, priced by `core::declared_bpf_map_bytes` and checked
/// against `core::BPF_ALLOC_DECLARED_BYTES` at compile time).  It is a
/// declaration total, not the kernel's actual resident memory, and not a
/// cgroup or system quota.
fn declared_bpf_bytes() -> u64 {
    core::BPF_MAP_DECLARED_BYTES
}

fn elf_build_id_of(path: &str) -> Result<String, String> {
    let image = fs::read(path).map_err(|e| format!("read {path}: {e}"))?;
    core::elf_build_id(&image)
        .map(|b| core::hex_lower(&b))
        .map_err(|e| e.to_string())
}

fn is_elf_library(path: &Path) -> bool {
    fs::metadata(path).map(|m| m.is_file()).unwrap_or(false)
}

fn fixture_path() -> Result<PathBuf, Failure> {
    let exe = std::env::current_exe().map_err(|e| Failure::blocked(format!("current_exe: {e}")))?;
    let dir = exe
        .parent()
        .ok_or_else(|| Failure::blocked("release directory missing"))?;
    let candidate = dir.join("../../tls-fixture/tls-fixture");
    let resolved = candidate
        .canonicalize()
        .map_err(|e| Failure::blocked(format!("tls-fixture workload not found: {e}")))?;
    if !is_elf_library(&resolved) {
        return Err(Failure::blocked(
            "tls-fixture workload is not a regular file",
        ));
    }
    let parent = resolved
        .parent()
        .ok_or_else(|| Failure::blocked("workload parent missing"))?;
    if !parent.ends_with("tls-fixture") {
        return Err(Failure::blocked(
            "tls-fixture workload outside target/tls-fixture",
        ));
    }
    Ok(resolved)
}

/// Digest and file identity of the C workload, computed before it is spawned.
#[derive(Debug, Clone, PartialEq, Eq)]
struct WorkloadId {
    path: String,
    size: u64,
    sha256: String,
    dev: u64,
    ino: u64,
}

impl WorkloadId {
    fn to_json(&self) -> Value {
        json!({
            "path": self.path,
            "size": self.size,
            "sha256": self.sha256,
            "dev": self.dev,
            "ino": self.ino,
        })
    }
}

fn workload_identity(path: &Path) -> Result<WorkloadId, Failure> {
    let bytes = fs::read(path).map_err(|e| Failure::blocked(format!("read workload: {e}")))?;
    let md = fs::metadata(path).map_err(|e| Failure::blocked(format!("stat workload: {e}")))?;
    Ok(WorkloadId {
        path: path.to_string_lossy().into_owned(),
        size: md.len(),
        sha256: core::sha256_hex(&bytes),
        dev: md.dev(),
        ino: md.ino(),
    })
}

#[derive(Clone)]
struct RunDir {
    run_id: String,
    path: PathBuf,
}

impl RunDir {
    fn create(boot_id: &str) -> Result<RunDir, Failure> {
        for _ in 0..8 {
            let run_id = make_run_id().map_err(Failure::blocked)?;
            let text = core::run_dir_path(&run_id);
            let path = PathBuf::from(&text);
            if fs::symlink_metadata(&path).is_ok() {
                continue;
            }
            let mut builder = DirBuilder::new();
            builder.mode(0o700);
            match builder.create(&path) {
                Ok(()) => {}
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(e) => return Err(Failure::blocked(format!("create run dir: {e}"))),
            }
            let md = fs::symlink_metadata(&path)
                .map_err(|e| Failure::blocked(format!("lstat run dir: {e}")))?;
            if !md.is_dir() || md.uid() != 0 || md.mode() & 0o777 != 0o700 {
                return Err(Failure::failed(
                    "run dir is not a 0700 directory owned by root",
                ));
            }
            let run = RunDir { run_id, path };
            run.write_manifest(&json!({
                "record": "manifest",
                "schema": 1,
                "run_id": run.run_id,
                "path": run.path_text(),
                "owner_euid": 0,
                "boot_id": boot_id,
                "created_at_unix": now_unix(),
                "files": [],
                "sessions": {},
            }))?;
            run.record(
                "created",
                json!({"at_unix": now_unix(), "boot_id": boot_id}),
            )?;
            return Ok(run);
        }
        Err(Failure::blocked("could not allocate a fresh run id"))
    }

    fn path_text(&self) -> String {
        self.path.to_string_lossy().into_owned()
    }

    fn manifest_path(&self) -> PathBuf {
        self.path.join("manifest.json")
    }

    fn file_list(&self) -> Vec<String> {
        let mut names: Vec<String> = fs::read_dir(&self.path)
            .map(|dir| {
                dir.filter_map(|e| e.ok())
                    .map(|e| e.file_name().to_string_lossy().into_owned())
                    .collect()
            })
            .unwrap_or_default();
        names.sort();
        names
    }

    fn write_manifest(&self, value: &Value) -> Result<(), Failure> {
        let text = serde_json::to_string(value).map_err(|e| Failure::failed(e.to_string()))?;
        let mut file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .open(self.manifest_path())
            .map_err(|e| Failure::blocked(format!("open manifest: {e}")))?;
        file.write_all(text.as_bytes())
            .and_then(|_| file.write_all(b"\n"))
            .map_err(|e| Failure::failed(format!("write manifest: {e}")))?;
        Ok(())
    }

    /// Merge one key into the manifest, refreshing the file list and time.
    ///
    /// The manifest is the only place that survives an abnormal exit, so it is
    /// updated before and after every session, not just at the end.
    fn record(&self, key: &str, value: Value) -> Result<(), Failure> {
        let mut map = match fs::read_to_string(self.manifest_path()) {
            Ok(text) => match serde_json::from_str::<Value>(text.trim()) {
                Ok(Value::Object(map)) => map,
                _ => serde_json::Map::new(),
            },
            Err(_) => serde_json::Map::new(),
        };
        map.insert("run_id".into(), json!(self.run_id));
        map.insert("path".into(), json!(self.path_text()));
        map.insert("schema".into(), json!(1));
        map.insert("owner_euid".into(), json!(0));
        map.insert(key.to_string(), value);
        map.insert("updated_at_unix".into(), json!(now_unix()));
        map.insert("files".into(), json!(self.file_list()));
        self.write_manifest(&Value::Object(map))
    }

    /// Merge one session phase into sessions.<session_id>.<phase>.
    ///
    /// The manifest is rewritten before and after every session so an abnormal
    /// exit still leaves the child pid, its birth tick, the probe ids, the
    /// workload digest and the file list on disk.
    fn record_session(&self, session_id: &str, phase: &str, value: Value) -> Result<(), Failure> {
        let mut map = match fs::read_to_string(self.manifest_path()) {
            Ok(text) => match serde_json::from_str::<Value>(text.trim()) {
                Ok(Value::Object(map)) => map,
                _ => serde_json::Map::new(),
            },
            Err(_) => serde_json::Map::new(),
        };
        let mut sessions = match map.remove("sessions") {
            Some(Value::Object(sessions)) => sessions,
            _ => serde_json::Map::new(),
        };
        let mut entry = match sessions.remove(session_id) {
            Some(Value::Object(entry)) => entry,
            _ => serde_json::Map::new(),
        };
        entry.insert(phase.to_string(), value);
        sessions.insert(session_id.to_string(), Value::Object(entry));
        map.insert("sessions".into(), Value::Object(sessions));
        self.write_manifest(&Value::Object(map))?;
        self.record("tick", json!(now_unix()))
    }

    fn read_manifest(&self) -> Result<Value, Failure> {
        let text = fs::read_to_string(self.manifest_path())
            .map_err(|e| Failure::failed(format!("read manifest: {e}")))?;
        serde_json::from_str(text.trim())
            .map_err(|e| Failure::failed(format!("manifest json: {e}")))
    }

    fn remove_if_owned(&self) -> Result<(), Failure> {
        let text = self.path_text();
        let md = fs::symlink_metadata(&self.path)
            .map_err(|e| Failure::failed(format!("lstat run dir: {e}")))?;
        let manifest = self.manifest_path();
        let manifest_run_id = fs::read_to_string(&manifest).ok().and_then(|body| {
            serde_json::from_str::<Value>(body.trim())
                .ok()
                .and_then(|v| v.get("run_id").and_then(Value::as_str).map(str::to_string))
        });
        core::run_dir_removable(&core::RunDirFacts {
            path: text,
            run_id: self.run_id.clone(),
            is_symlink: md.file_type().is_symlink(),
            is_dir: md.is_dir(),
            uid: md.uid(),
            mode: md.mode(),
            manifest_run_id,
        })
        .map_err(|e| Failure::failed(format!("run dir identity: {e}")))?;
        fs::remove_file(&manifest).map_err(|e| Failure::failed(format!("remove manifest: {e}")))?;
        fs::remove_dir(&self.path).map_err(|e| Failure::failed(format!("remove run dir: {e}")))?;
        Ok(())
    }
}

struct Fixture {
    child: Child,
    rx: mpsc::Receiver<String>,
    reader: Option<thread::JoinHandle<()>>,
    pid: u32,
    start_ticks: u64,
    ppid: u64,
    uid: u32,
    gid: u32,
    ready: Value,
    libssl_path: String,
    libssl_dev: u64,
    libssl_ino: u64,
    libssl_size: u64,
    build_id: String,
    ns_dev: u64,
    ns_ino: u64,
    ns_link_ino: u64,
    cgroup: String,
    cgroup_path: String,
    cgroup_inode: u64,
    nspid: u64,
    libssl_mtime_s: u64,
    maps_exec_match: bool,
    fd_entries: Vec<(u32, String)>,
    workload: WorkloadId,
    identity_checks: Vec<Value>,
    expected_identity: Option<core::IdentityKey>,
}

impl Fixture {
    fn spawn(path: &Path, workload: WorkloadId) -> Result<Fixture, Failure> {
        let mut child = Command::new(path)
            .uid(FIXTURE_UID)
            .gid(FIXTURE_GID)
            .env_clear()
            .current_dir("/")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| Failure::blocked(format!("spawn workload: {e}")))?;
        let pid = child.id();
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| Failure::blocked("workload stdout missing"))?;
        let (tx, rx) = mpsc::channel::<String>();
        let reader = thread::spawn(move || {
            let buf = BufReader::new(stdout);
            for line in buf.lines() {
                match line {
                    Ok(line) => {
                        if tx.send(line).is_err() {
                            return;
                        }
                    }
                    Err(_) => return,
                }
            }
        });
        let mut fixture = Fixture {
            child,
            rx,
            reader: Some(reader),
            pid,
            start_ticks: 0,
            ppid: 0,
            uid: 0,
            gid: 0,
            ready: Value::Null,
            libssl_path: String::new(),
            libssl_dev: 0,
            libssl_ino: 0,
            libssl_size: 0,
            build_id: String::new(),
            ns_dev: 0,
            ns_ino: 0,
            ns_link_ino: 0,
            cgroup: String::new(),
            cgroup_path: String::new(),
            cgroup_inode: 0,
            nspid: 0,
            libssl_mtime_s: 0,
            maps_exec_match: false,
            fd_entries: Vec::new(),
            workload,
            identity_checks: Vec::new(),
            expected_identity: None,
        };
        let line = fixture
            .rx
            .recv_timeout(Duration::from_secs(5))
            .map_err(|_| Failure::failed("READY timeout"))?;
        let ready: Value =
            serde_json::from_str(&line).map_err(|e| Failure::failed(format!("READY json: {e}")))?;
        if ready.get("record").and_then(Value::as_str) != Some("ready") {
            return Err(Failure::failed("workload did not report READY"));
        }
        fixture.ready = ready;
        fixture.verify_identity("spawn")?;
        Ok(fixture)
    }

    /// Re-check every identity field and record the outcome.
    ///
    /// Called at spawn, after attach and right after GO.  A failure here is a
    /// hard FAIL: the session would otherwise attribute events to a process
    /// that is no longer provably the one we attached to.
    fn verify_identity(&mut self, stage: &'static str) -> Result<(), Failure> {
        let at_ns = boottime_ns().unwrap_or(0);
        match self.identity_body() {
            Ok(detail) => {
                self.identity_checks.push(json!({
                    "stage": stage,
                    "at_ns": at_ns,
                    "passed": true,
                    "detail": detail,
                }));
                Ok(())
            }
            Err(f) => {
                self.identity_checks.push(json!({
                    "stage": stage,
                    "at_ns": at_ns,
                    "passed": false,
                    "failure_reasons": f.reasons,
                }));
                Err(Failure {
                    blocked: f.blocked,
                    reasons: f
                        .reasons
                        .iter()
                        .map(|r| format!("identity re-check at {stage}: {r}"))
                        .collect(),
                })
            }
        }
    }

    fn identity_body(&mut self) -> Result<Value, Failure> {
        let status = proc_status(self.pid).map_err(Failure::failed)?;
        let uid = core::status_uid(&status).map_err(|e| Failure::failed(e.to_string()))?;
        let gid = core::status_gid(&status).map_err(|e| Failure::failed(e.to_string()))?;
        let threads =
            core::status_number(&status, "Threads").map_err(|e| Failure::failed(e.to_string()))?;
        let ppid =
            core::status_number(&status, "PPid").map_err(|e| Failure::failed(e.to_string()))?;
        if uid != FIXTURE_UID || gid != FIXTURE_GID {
            return Err(Failure::failed("workload runs with an unexpected uid/gid"));
        }
        if threads != 1 {
            return Err(Failure::failed("workload is not single-threaded"));
        }
        if ppid != u64::from(std::process::id()) {
            return Err(Failure::failed("workload ppid is not this collector"));
        }
        self.uid = uid;
        self.gid = gid;
        self.ppid = ppid;
        let ticks = proc_stat_ticks(self.pid).map_err(Failure::failed)?;
        if self.start_ticks != 0 && ticks != self.start_ticks {
            return Err(Failure::failed("workload start time changed mid-session"));
        }
        self.start_ticks = ticks;
        let (dev, ino, link_ino) = ns_identity(self.pid).map_err(Failure::failed)?;
        if self.ns_ino != 0 && (dev != self.ns_dev || ino != self.ns_ino) {
            return Err(Failure::failed("pid namespace changed mid-session"));
        }
        self.ns_dev = dev;
        self.ns_ino = ino;
        self.ns_link_ino = link_ino;
        let cgroup = cgroup_text(self.pid).map_err(Failure::failed)?;
        let own_cgroup = cgroup_text(std::process::id()).map_err(Failure::failed)?;
        if cgroup != own_cgroup {
            return Err(Failure::failed(
                "workload cgroup differs from the collector",
            ));
        }
        self.cgroup = cgroup;
        let (cgroup_path, cgroup_inode) = cgroup_facts(self.pid).map_err(Failure::failed)?;
        self.cgroup_path = cgroup_path;
        self.cgroup_inode = cgroup_inode;
        self.nspid = core::status_nspid(&status).map_err(|e| Failure::failed(e.to_string()))?;
        let maps = read_proc(&format!("/proc/{}/maps", self.pid)).map_err(Failure::failed)?;
        let found = core::exec_mappings(&maps, "libssl.so.3");
        if found.len() != 1 {
            return Err(Failure::failed(format!(
                "expected exactly one executable libssl mapping, found {}",
                found.len()
            )));
        }
        let entry = &found[0];
        if !self.libssl_path.is_empty() && entry.path != self.libssl_path {
            return Err(Failure::failed("libssl mapping changed during the session"));
        }
        self.libssl_path = entry.path.clone();
        self.libssl_dev = entry.st_dev();
        self.libssl_ino = entry.inode;
        let file_meta = fs::metadata(&self.libssl_path)
            .map_err(|e| Failure::failed(format!("stat {}: {e}", self.libssl_path)))?;
        let mtime_s = file_meta.mtime();
        if mtime_s <= 0 {
            return Err(Failure::failed("libssl mtime is not a positive timestamp"));
        }
        self.libssl_mtime_s = mtime_s as u64;
        // The executable mapping must be backed by this exact file, not merely
        // by a file with the same name.
        if file_meta.dev() != self.libssl_dev || file_meta.ino() != self.libssl_ino {
            return Err(Failure::failed(
                "libssl executable mapping is not backed by the mapped file",
            ));
        }
        self.maps_exec_match = true;
        self.build_id = elf_build_id_of(&self.libssl_path).map_err(Failure::failed)?;
        if self.build_id != EXPECTED_BUILD_ID {
            return Err(Failure::failed(format!(
                "libssl build-id {} is not the frozen {EXPECTED_BUILD_ID}",
                self.build_id
            )));
        }
        let ready_dev = self.ready["libssl"]["dev"].as_u64().unwrap_or(u64::MAX);
        let ready_ino = self.ready["libssl"]["ino"].as_u64().unwrap_or(u64::MAX);
        let ready_path = self.ready["libssl"]["path"].as_str().unwrap_or("");
        if ready_dev != self.libssl_dev
            || ready_ino != self.libssl_ino
            || ready_path != self.libssl_path
        {
            return Err(Failure::failed(
                "workload and collector disagree about the libssl mapping",
            ));
        }
        self.libssl_size = self.ready["libssl"]["size"].as_u64().unwrap_or(0);
        let ready_dev_ns = self.ready["nsfs"]["dev"].as_u64().unwrap_or(u64::MAX);
        let ready_ino_ns = self.ready["nsfs"]["ino"].as_u64().unwrap_or(u64::MAX);
        if ready_dev_ns != self.ns_dev || ready_ino_ns != self.ns_ino {
            return Err(Failure::failed(
                "workload and collector disagree about the pid namespace",
            ));
        }
        if self.ready["nsfs"]["magic"].as_u64() != Some(0x6e73_6673) {
            return Err(Failure::failed("workload namespace is not nsfs"));
        }
        let ready_pid = self.ready["pid"].as_u64().unwrap_or(0);
        if ready_pid != u64::from(self.pid) {
            return Err(Failure::failed(
                "workload pid does not match the spawned child",
            ));
        }
        let entries = fd_entries(self.pid).map_err(Failure::failed)?;
        core::stdio_fds_ok(&entries)
            .map_err(|e| Failure::failed(format!("workload descriptor contract: {e}")))?;
        self.fd_entries = entries;
        // Every field above must still describe the same process; any drift is
        // a retarget and a hard failure.
        let observed = core::IdentityKey {
            tgid: u64::from(self.pid),
            ppid: self.ppid,
            start_ticks: self.start_ticks,
            uid: self.uid,
            gid: self.gid,
            ns_dev: self.ns_dev,
            ns_ino: self.ns_ino,
        };
        match self.expected_identity {
            Some(expect) => core::identity_ok(&expect, &observed).map_err(Failure::failed)?,
            None => self.expected_identity = Some(observed),
        }
        Ok(json!({
            "pid": self.pid,
            "ppid": self.ppid,
            "uid": self.uid,
            "gid": self.gid,
            "threads": 1,
            "start_ticks": self.start_ticks,
            "pidns": {
                "dev": self.ns_dev,
                "ino": self.ns_ino,
                "procfs_link_ino": self.ns_link_ino,
                "nsfs_magic": 0x6e736673u64,
            },
            "cgroup_path_equal": true,
            "cgroup_path": self.cgroup_path,
            "cgroup_inode": self.cgroup_inode,
            "nspid": self.nspid,
            "libssl": {
                "path": self.libssl_path,
                "dev": self.libssl_dev,
                "ino": self.libssl_ino,
                "size": self.libssl_size,
                "mtime_s": self.libssl_mtime_s,
                "build_id": self.build_id,
                "maps_exec_match": self.maps_exec_match,
            },
            "fds": self.fd_entries.iter().map(|(fd, t)| json!({"fd": fd, "target": t})).collect::<Vec<_>>(),
            "fd_contract": "exactly 0/1/2, pipe or /dev/null only",
            "workload": self.workload.to_json(),
        }))
    }

    fn go(&mut self) -> Result<(), Failure> {
        let stdin = self
            .child
            .stdin
            .as_mut()
            .ok_or_else(|| Failure::failed("workload stdin missing"))?;
        stdin
            .write_all(b"G")
            .map_err(|e| Failure::failed(format!("write GO: {e}")))?;
        stdin
            .flush()
            .map_err(|e| Failure::failed(format!("flush GO: {e}")))?;
        Ok(())
    }

    fn next_line(&self, wait: Duration) -> Result<String, Failure> {
        self.rx
            .recv_timeout(wait)
            .map_err(|_| Failure::failed("workload produced no further record"))
    }

    fn wait(&mut self, wait: Duration) -> Result<std::process::ExitStatus, Failure> {
        let deadline = Instant::now() + wait;
        loop {
            match self.child.try_wait() {
                Ok(Some(status)) => return Ok(status),
                Ok(None) => {
                    if Instant::now() >= deadline {
                        let _ = self.child.kill();
                        let _ = self.child.wait();
                        return Err(Failure::failed("workload did not exit in time"));
                    }
                    thread::sleep(Duration::from_millis(10));
                }
                Err(e) => return Err(Failure::failed(format!("wait workload: {e}"))),
            }
        }
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
        let _ = self.child.stdin.take();
        if let Some(handle) = self.reader.take() {
            let _ = handle.join();
        }
    }
}

fn classify_bpf_error(context: &str, error: &str, caps: u64) -> Failure {
    let lower = error.to_lowercase();
    let permission_like = lower.contains("eperm")
        || lower.contains("eacces")
        || lower.contains("operation not permitted")
        || lower.contains("permission denied");
    let lockdown = fs::read_to_string("/sys/kernel/security/lockdown").unwrap_or_default();
    let lockdown_active = lockdown.contains("integrity") || lockdown.contains("confidentiality");
    if permission_like
        && (!core::has_cap(caps, core::CAP_BPF)
            || !core::has_cap(caps, core::CAP_PERFMON)
            || lockdown_active)
    {
        return Failure::blocked(format!("{context}: host permission boundary: {error}"));
    }
    Failure {
        blocked: false,
        reasons: vec![format!("{context}: {error}")],
    }
}

/// Refuse to load an object whose own map declarations are not the nine maps
/// the reported budget is priced from.
///
/// This runs before `Ebpf::load`, so a missing, extra, unpriced, overflowing
/// or over-limit declaration fails the batch instead of being loaded and
/// reported as a smaller total.  The reported total itself stays the
/// compile-time table price (`core::BPF_MAP_DECLARED_BYTES`); this gate proves
/// the object about to be loaded is exactly that table.
fn check_declared_bpf_budget() -> Result<(), Failure> {
    let object_bytes = core::object_declared_bpf_map_bytes(TLS_OBJECT).map_err(|error| {
        Failure::failed(format!(
            "embedded {TLS_OBJECT_NAME} map declarations rejected: {error}"
        ))
    })?;
    if object_bytes != core::BPF_MAP_DECLARED_BYTES {
        return Err(Failure::failed(format!(
            "embedded {TLS_OBJECT_NAME} declares {object_bytes} map bytes, table prices {}",
            core::BPF_MAP_DECLARED_BYTES
        )));
    }
    Ok(())
}

fn start_probes(
    fx: &Fixture,
    hz: u64,
    deadline_ns: u64,
    wrong_birth: bool,
    caps: u64,
) -> Result<(Ebpf, Vec<u32>), Failure> {
    check_declared_bpf_budget()?;
    let mut bpf = Ebpf::load(TLS_OBJECT)
        .map_err(|e| classify_bpf_error("Ebpf::load(tls_fixture)", &format!("{e:?}"), caps))?;
    {
        let mut config = Array::<_, u64>::try_from(
            bpf.map_mut("CONFIG")
                .ok_or_else(|| Failure::failed("CONFIG map missing"))?,
        )
        .map_err(|e| Failure::failed(format!("CONFIG map: {e}")))?;
        let birth = if wrong_birth {
            fx.start_ticks + 1
        } else {
            fx.start_ticks
        };
        let values: [(u32, u64); 9] = [
            (core::CFG_TGID, u64::from(fx.pid)),
            (core::CFG_START, birth),
            (core::CFG_HZ, hz),
            (core::CFG_DEADLINE, deadline_ns),
            (core::CFG_UID, u64::from(FIXTURE_UID)),
            (core::CFG_MAX_EVENTS, core::EVENT_LIMIT),
            (core::CFG_SAMPLE_CAP, core::SAMPLE_CAP as u64),
            (core::CFG_NS_DEV, fx.ns_dev),
            (core::CFG_NS_INO, fx.ns_ino),
        ];
        for (index, value) in values {
            config
                .set(index, value, 0)
                .map_err(|e| Failure::failed(format!("CONFIG set {index}: {e}")))?;
        }
    }
    let mut ids = Vec::new();
    for name in ["tls_ssl_write_enter", "tls_ssl_write_return"] {
        let program: &mut UProbe = bpf
            .program_mut(name)
            .ok_or_else(|| Failure::failed(format!("program {name} missing")))?
            .try_into()
            .map_err(|_| Failure::failed(format!("program {name} is not a uprobe")))?;
        program
            .load()
            .map_err(|e| classify_bpf_error(&format!("load {name}"), &format!("{e:?}"), caps))?;
        ids.push(
            program
                .info()
                .map_err(|e| Failure::failed(format!("info {name}: {e}")))?
                .id(),
        );
    }
    for name in ["tls_ssl_write_enter", "tls_ssl_write_return"] {
        let program: &mut UProbe = bpf
            .program_mut(name)
            .ok_or_else(|| Failure::failed(format!("program {name} missing")))?
            .try_into()
            .map_err(|_| Failure::failed(format!("program {name} is not a uprobe")))?;
        program
            .attach(
                Some("SSL_write"),
                0,
                Path::new(&fx.libssl_path),
                Some(fx.pid as i32),
            )
            .map_err(|e| {
                classify_bpf_error(
                    &format!("attach {name} to SSL_write"),
                    &format!("{e:?}"),
                    caps,
                )
            })?;
    }
    Ok((bpf, ids))
}

fn drain(
    ring: &mut RingBuf<aya::maps::MapData>,
    out: &mut Vec<core::Record>,
    issues: &mut Vec<String>,
) -> usize {
    let mut taken = 0usize;
    while taken < 512 {
        let Some(bytes) = ring.next() else { break };
        match core::decode_record(&bytes) {
            Ok(record) => out.push(record),
            Err(e) => issues.push(format!("undecodable record: {e}")),
        }
        taken += 1;
    }
    taken
}

fn loaded_ids() -> Result<Vec<u32>, Failure> {
    loaded_programs()
        .map(|info| info.map(|p| p.id()))
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| Failure::failed(format!("loaded_programs: {e}")))
}

fn program_ids_present(ids: &[u32], wait: Duration) -> Result<bool, Failure> {
    let deadline = Instant::now() + wait;
    loop {
        let live = loaded_ids()?;
        if ids.iter().all(|id| live.contains(id)) {
            return Ok(true);
        }
        if Instant::now() >= deadline {
            return Ok(false);
        }
        thread::sleep(Duration::from_millis(20));
    }
}

fn program_ids_absent(ids: &[u32], wait: Duration) -> Result<bool, Failure> {
    let deadline = Instant::now() + wait;
    loop {
        let live = loaded_ids()?;
        if ids.iter().all(|id| !live.contains(id)) {
            return Ok(true);
        }
        if Instant::now() >= deadline {
            return Ok(false);
        }
        thread::sleep(Duration::from_millis(20));
    }
}

fn pid_matches_start(pid: u32, ticks: u64) -> bool {
    match proc_stat_ticks(pid) {
        Ok(current) => current == ticks,
        Err(_) => false,
    }
}

fn wait_pid_gone(pid: u32, ticks: u64, wait: Duration) -> bool {
    let deadline = Instant::now() + wait;
    loop {
        if !pid_matches_start(pid, ticks) {
            return true;
        }
        if Instant::now() >= deadline {
            return false;
        }
        thread::sleep(Duration::from_millis(50));
    }
}

fn effective_uid() -> Result<u32, String> {
    let status = read_proc("/proc/self/status")?;
    let line = status
        .lines()
        .find(|l| l.starts_with("Uid:"))
        .ok_or("Uid missing")?;
    line.split_whitespace()
        .nth(2)
        .ok_or("Uid short")?
        .parse()
        .map_err(|_| "Uid malformed".to_string())
}

/// Everything one session accumulates, printed whether it passed or not.
///
/// The field names here are contract 5.8, not convenience names: seed and
/// closeout are built from the same map and both go through
/// core::session_record_ok before anything is printed.
struct SessionReport {
    session_id: &'static str,
    wrong_birth: bool,
    run_id: String,
    run_path: String,
    boot_id: String,
    started_at_boot_ns: u64,
    target: Option<Value>,
    target_tgid: u64,
    target_ppid: u64,
    target_start_ticks: u64,
    target_uid: u64,
    target_gid: u64,
    target_nspid: u64,
    pidns_dev: u64,
    pidns_ino: u64,
    cgroup_path: String,
    cgroup_id: u64,
    cgroup_inode_expected: u64,
    libssl: Option<Value>,
    attach: Option<Value>,
    program_ids: Vec<u32>,
    identity_checks: Vec<Value>,
    workload: Option<Value>,
    manifest_check: Option<Value>,
    counters: Option<core::CounterReport>,
    counter_note: Option<String>,
    requests: Vec<Value>,
    peer: Option<Value>,
    cleanup: Value,
    issues: Vec<String>,
    passed: bool,
    failure: Option<Vec<String>>,
}

impl SessionReport {
    fn new(
        session_id: &'static str,
        wrong_birth: bool,
        run: &RunDir,
        boot_id: &str,
    ) -> SessionReport {
        SessionReport {
            session_id,
            wrong_birth,
            run_id: run.run_id.clone(),
            run_path: run.path_text(),
            boot_id: boot_id.to_string(),
            started_at_boot_ns: boottime_ns().unwrap_or(0),
            target: None,
            target_tgid: 0,
            target_ppid: 0,
            target_start_ticks: 0,
            target_uid: 0,
            target_gid: 0,
            target_nspid: 0,
            pidns_dev: 0,
            pidns_ino: 0,
            cgroup_path: String::new(),
            cgroup_id: 0,
            cgroup_inode_expected: 0,
            libssl: None,
            attach: None,
            program_ids: Vec::new(),
            identity_checks: Vec::new(),
            workload: None,
            manifest_check: None,
            counters: None,
            counter_note: None,
            requests: Vec::new(),
            peer: None,
            cleanup: json!({
                "prog_ids_absent": false,
                "child_gone": false,
                "fd_before": 0,
                "fd_after": 0,
            }),
            issues: Vec::new(),
            passed: true,
            failure: None,
        }
    }

    /// Copy the identity block out of the live fixture into contract fields.
    fn set_identity(
        &mut self,
        fx: &Fixture,
        workload: &WorkloadId,
        symbol: &core::SymbolInfo,
        pmu: u64,
    ) {
        self.target_tgid = u64::from(fx.pid);
        self.target_ppid = fx.ppid;
        self.target_start_ticks = fx.start_ticks;
        self.target_uid = u64::from(fx.uid);
        self.target_gid = u64::from(fx.gid);
        self.target_nspid = fx.nspid;
        self.pidns_dev = fx.ns_dev;
        self.pidns_ino = fx.ns_ino;
        self.cgroup_path = fx.cgroup_path.clone();
        self.cgroup_inode_expected = fx.cgroup_inode;
        self.target = Some(json!({
            "tgid": fx.pid,
            "ppid": fx.ppid,
            "start_ticks": fx.start_ticks,
            "uid": fx.uid,
            "gid": fx.gid,
            "nspid": fx.nspid,
            "pidns": {
                "dev": fx.ns_dev,
                "ino": fx.ns_ino,
                "procfs_link_ino": fx.ns_link_ino,
                "nsfs_magic": core::NSFS_MAGIC,
            },
            "fds": fx.fd_entries.iter().map(|(fd, t)| json!({"fd": fd, "target": t})).collect::<Vec<_>>(),
        }));
        self.libssl = Some(json!({
            "path": fx.libssl_path,
            "dev": fx.libssl_dev,
            "ino": fx.libssl_ino,
            "size": fx.libssl_size,
            "mtime_s": fx.libssl_mtime_s,
            "build_id": fx.build_id,
            "maps_exec_match": fx.maps_exec_match,
        }));
        self.attach = Some(json!({
            "mechanism": "perf_event_open_uprobe_pmu",
            "pmu_type": pmu,
            "pid_scope": "Some(tgid)",
            "symbol": "SSL_write",
            "symbol_version": symbol.version,
            "file_offset": symbol.file_offset,
            "symbol_value": symbol.value,
            "object": TLS_OBJECT_NAME,
            "object_bytes": TLS_OBJECT.len(),
            "object_fnv1a64": format!("{:016x}", core::fnv1a64(TLS_OBJECT)),
        }));
        self.workload = Some(workload_block(workload, None));
    }

    fn contract_map(&self) -> serde_json::Map<String, Value> {
        let mut map = serde_json::Map::new();
        map.insert("schema".into(), json!(1));
        map.insert("batch_run_id".into(), json!(self.run_id));
        map.insert("run_id".into(), json!(self.run_id));
        map.insert("run_dir_path".into(), json!(self.run_path));
        map.insert("session_id".into(), json!(self.session_id));
        map.insert("boot_id".into(), json!(self.boot_id));
        map.insert("started_at_boot_ns".into(), json!(self.started_at_boot_ns));
        map.insert("wrong_birth_test".into(), json!(self.wrong_birth));
        map.insert("target".into(), self.target.clone().unwrap_or(Value::Null));
        map.insert("target_tgid".into(), json!(self.target_tgid));
        map.insert("target_ppid".into(), json!(self.target_ppid));
        map.insert("target_start_ticks".into(), json!(self.target_start_ticks));
        map.insert("target_uid".into(), json!(self.target_uid));
        map.insert("target_gid".into(), json!(self.target_gid));
        map.insert("target_nspid".into(), json!(self.target_nspid));
        map.insert("pidns_dev".into(), json!(self.pidns_dev));
        map.insert("pidns_ino".into(), json!(self.pidns_ino));
        map.insert("nsfs_magic".into(), json!(core::NSFS_MAGIC));
        map.insert("cgroup_path".into(), json!(self.cgroup_path));
        map.insert("cgroup_id".into(), json!(self.cgroup_id));
        map.insert(
            "cgroup_inode_expected".into(),
            json!(self.cgroup_inode_expected),
        );
        map.insert("libssl".into(), self.libssl.clone().unwrap_or(Value::Null));
        map.insert("attach".into(), self.attach.clone().unwrap_or(Value::Null));
        map.insert("program_ids".into(), json!(self.program_ids));
        map.insert("link_ids".into(), json!([]));
        map.insert(
            "link_ids_note".into(),
            json!(
                "aya 0.13.1 does not expose uprobe link ids; program ids are the teardown contract"
            ),
        );
        map.insert("identity_checks".into(), json!(self.identity_checks));
        map.insert(
            "workload".into(),
            self.workload.clone().unwrap_or(Value::Null),
        );
        map
    }

    fn seed_json(&self) -> Value {
        let mut map = self.contract_map();
        map.insert("record".into(), json!("session"));
        Value::Object(map)
    }

    fn closeout_json(&self) -> Value {
        let mut map = self.contract_map();
        map.insert("record".into(), json!("session_closeout"));
        map.insert("passed".into(), json!(self.passed));
        map.insert(
            "failure_reasons".into(),
            json!(self.failure.clone().unwrap_or_default()),
        );
        map.insert("issues".into(), json!(self.issues));
        map.insert("counters_available".into(), json!(self.counters.is_some()));
        map.insert(
            "counters".into(),
            match &self.counters {
                Some(c) => counter_json(c),
                None => zero_counters(),
            },
        );
        if let Some(note) = &self.counter_note {
            map.insert("counter_note".into(), json!(note));
        }
        map.insert("requests".into(), json!(self.requests));
        map.insert("peer".into(), self.peer.clone().unwrap_or(Value::Null));
        map.insert("cleanup".into(), self.cleanup.clone());
        map.insert(
            "manifest_check".into(),
            self.manifest_check.clone().unwrap_or(Value::Null),
        );
        Value::Object(map)
    }

    /// Print the seed only if the record really carries the contract fields.
    fn print_seed(&mut self) -> Result<(), Failure> {
        let seed = self.seed_json();
        if let Err(e) = core::session_record_ok(&seed, false) {
            self.passed = false;
            self.issues.push(format!("session seed schema: {e}"));
            return Err(Failure::failed(format!("session seed schema: {e}")));
        }
        println!("{seed}");
        Ok(())
    }
}

fn zero_counters() -> Value {
    json!({
        "submitted": 0, "consumed": 0, "lost": 0, "read_failed": 0, "map_failed": 0,
        "expired": 0, "truncated": 0, "identity_rejected": 0, "len_invalid": 0,
        "abnormal_ret": 0, "user_read_attempts": 0, "user_read_bytes": 0,
        "payload_bytes": 0, "depth_overflow": 0, "unpaired": 0, "entries": 0, "exits": 0,
    })
}

struct CrashChild {
    child: Child,
    rx: mpsc::Receiver<String>,
    reader: Option<thread::JoinHandle<()>>,
}

impl Drop for CrashChild {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
        if let Some(handle) = self.reader.take() {
            let _ = handle.join();
        }
    }
}

fn spawn_crash_child(seconds: u64) -> Result<CrashChild, Failure> {
    let exe = std::env::current_exe().map_err(|e| Failure::blocked(format!("current_exe: {e}")))?;
    let mut child = Command::new(exe)
        .arg("--tls-session")
        .arg("crash_recovery")
        .arg(seconds.to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| Failure::failed(format!("spawn crash session: {e}")))?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| Failure::failed("crash session stdout missing"))?;
    let (tx, rx) = mpsc::channel::<String>();
    let reader = thread::spawn(move || {
        let buf = BufReader::new(stdout);
        for line in buf.lines() {
            match line {
                Ok(line) => {
                    if tx.send(line).is_err() {
                        return;
                    }
                }
                Err(_) => return,
            }
        }
    });
    Ok(CrashChild {
        child,
        rx,
        reader: Some(reader),
    })
}

fn cleanup_killed_run(
    run_id: &str,
    prog_ids_absent: bool,
    child_gone: bool,
) -> Result<Value, Failure> {
    if !core::run_id_is_valid(run_id) {
        return Err(Failure::failed("killed session reported an invalid run id"));
    }
    let text = core::run_dir_path(run_id);
    let path = PathBuf::from(&text);
    let md = fs::symlink_metadata(&path)
        .map_err(|e| Failure::failed(format!("killed session run dir missing: {e}")))?;
    let manifest = path.join("manifest.json");
    let manifest_run_id = fs::read_to_string(&manifest).ok().and_then(|body| {
        serde_json::from_str::<Value>(body.trim())
            .ok()
            .and_then(|v| v.get("run_id").and_then(Value::as_str).map(str::to_string))
    });
    // The same decision function the normal teardown uses, so symlink, owner,
    // mode, path and manifest rules can not drift apart.
    core::run_dir_removable(&core::RunDirFacts {
        path: text.clone(),
        run_id: run_id.to_string(),
        is_symlink: md.file_type().is_symlink(),
        is_dir: md.is_dir(),
        uid: md.uid(),
        mode: md.mode(),
        manifest_run_id: manifest_run_id.clone(),
    })
    .map_err(|e| Failure::failed(format!("killed session run dir identity: {e}")))?;
    fs::remove_file(&manifest)
        .map_err(|e| Failure::failed(format!("remove killed manifest: {e}")))?;
    fs::remove_dir(&path).map_err(|e| Failure::failed(format!("remove killed run dir: {e}")))?;
    Ok(json!({
        "prog_ids_absent": prog_ids_absent,
        "child_gone": child_gone,
        "run_dir_removed": true,
        "verified_before_remove": true,
        "manifest_run_id": manifest_run_id,
        "run_dir_path": text,
    }))
}

fn run_crash_scenario(fd_before: usize) -> Result<Value, Failure> {
    let mut child = spawn_crash_child(CRASH_SESSION_SECONDS)?;
    let collector_pid = child.child.id();
    let session_line = child
        .rx
        .recv_timeout(Duration::from_secs(20))
        .map_err(|_| Failure::failed("crash session produced no session record"))?;
    let session: Value = serde_json::from_str(&session_line)
        .map_err(|e| Failure::failed(format!("crash session record json: {e}")))?;
    // The child prints the same contract session record the parent prints for
    // its own sessions; anything less is a broken child, not a crash result.
    core::session_record_ok(&session, false)
        .map_err(|e| Failure::failed(format!("crash child session schema: {e}")))?;
    if session["session_id"].as_str() != Some("crash_recovery") {
        return Err(Failure::failed(
            "crash child did not report the crash_recovery session",
        ));
    }
    let ids: Vec<u32> = session["program_ids"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(Value::as_u64)
                .map(|v| v as u32)
                .collect()
        })
        .unwrap_or_default();
    if ids.len() != 2 {
        return Err(Failure::failed(
            "crash session did not report two program ids",
        ));
    }
    let fixture_pid = session["target_tgid"].as_u64().unwrap_or(0) as u32;
    let fixture_ticks = session["target_start_ticks"].as_u64().unwrap_or(0);
    let run_id = session["run_id"].as_str().unwrap_or("").to_string();
    if fixture_pid == 0 || fixture_ticks == 0 || run_id.is_empty() {
        return Err(Failure::failed("crash session record is incomplete"));
    }
    // The target must still be alive and provably the one named in the record.
    let target_status = proc_status(fixture_pid)
        .map_err(|e| Failure::failed(format!("crash target status unreadable: {e}")))?;
    let target_uid =
        core::status_uid(&target_status).map_err(|e| Failure::failed(e.to_string()))?;
    let target_ppid =
        core::status_number(&target_status, "PPid").map_err(|e| Failure::failed(e.to_string()))?;
    let target_threads = core::status_number(&target_status, "Threads")
        .map_err(|e| Failure::failed(e.to_string()))?;
    let ticks_now = proc_stat_ticks(fixture_pid).map_err(Failure::failed)?;
    if target_uid != FIXTURE_UID {
        return Err(Failure::failed(
            "crash target is not running as the fixture uid",
        ));
    }
    if target_ppid != u64::from(collector_pid) {
        return Err(Failure::failed(
            "crash target ppid is not the crash collector",
        ));
    }
    if target_threads != 1 {
        return Err(Failure::failed("crash target is not single-threaded"));
    }
    if ticks_now != fixture_ticks {
        return Err(Failure::failed(
            "crash target start time does not match the session record",
        ));
    }
    // The child writes its manifest before it prints the session record, so an
    // incomplete manifest here means the pre-session record is broken.
    let manifest_path = PathBuf::from(core::run_dir_path(&run_id)).join("manifest.json");
    let manifest_state = match fs::read_to_string(&manifest_path) {
        Ok(text) => match serde_json::from_str::<Value>(text.trim()) {
            Ok(manifest) => {
                let checked = core::manifest_fields_ok(&manifest, false);
                json!({
                    "readable": true,
                    "ok": checked.is_ok(),
                    "error": checked.as_ref().err().map(|e| e.to_string()),
                    "sessions": manifest["sessions"].clone(),
                    "files": manifest["files"].clone(),
                })
            }
            Err(e) => json!({"readable": true, "ok": false, "error": e.to_string()}),
        },
        Err(e) => json!({"readable": false, "ok": false, "error": e.to_string()}),
    };
    if manifest_state["ok"].as_bool() != Some(true) {
        return Err(Failure::failed(
            "crash session manifest is missing pre-session fields",
        ));
    }
    // Both probe ids must be loaded before the kill, otherwise the teardown
    // assertion afterwards would be vacuous.
    let ids_present = program_ids_present(&ids, Duration::from_secs(2))?;
    if !ids_present {
        return Err(Failure::failed(
            "crash session never had both probe ids loaded",
        ));
    }
    let fixture_alive = pid_matches_start(fixture_pid, fixture_ticks);
    child
        .child
        .kill()
        .map_err(|e| Failure::failed(format!("kill crash session: {e}")))?;
    let status = child
        .child
        .wait()
        .map_err(|e| Failure::failed(format!("wait crash session: {e}")))?;
    let signal_terminated = status.code().is_none();
    let mut closeout_seen = false;
    while let Ok(line) = child.rx.recv_timeout(Duration::from_millis(200)) {
        if line.contains("\"closeout\"") || line.contains("\"session_closeout\"") {
            closeout_seen = true;
        }
    }
    let ids_gone = program_ids_absent(&ids, Duration::from_secs(3))?;
    let fixture_gone = wait_pid_gone(fixture_pid, fixture_ticks, Duration::from_secs(11));
    let cleanup = cleanup_killed_run(&run_id, ids_gone, fixture_gone)?;
    let fd_after = count_fds().map_err(Failure::failed)?;
    if !signal_terminated {
        return Err(Failure::failed(
            "crash collector was not terminated by a signal",
        ));
    }
    if closeout_seen {
        return Err(Failure::failed(
            "killed session still produced a closeout record",
        ));
    }
    if !ids_gone {
        return Err(Failure::failed(
            "killed session program ids survived past 3 s",
        ));
    }
    if !fixture_gone {
        return Err(Failure::failed("killed session fixture survived past 11 s"));
    }
    if fd_after != fd_before {
        return Err(Failure::failed(
            "collector fd count changed across the crash scenario",
        ));
    }
    // Parent supervision is its own record type: it must never look like a
    // closeout the killed child could have printed.
    let record = json!({
        "record": "crash_summary",
        "schema": 1,
        "source": "parent_after_kill",
        "child_closeout_absent": !closeout_seen,
        "source_session": "crash_recovery",
        "source_run_id": run_id,
        "source_run_dir_path": core::run_dir_path(&run_id),
        "crash_collector_pid": collector_pid,
        "crash_program_ids": ids,
        "target_tgid": fixture_pid,
        "target_ppid": target_ppid,
        "target_start_ticks": fixture_ticks,
        "target_uid": target_uid,
        "target_alive_before_kill": fixture_alive,
        "program_ids_present_before_kill": ids_present,
        "killed_by_signal": signal_terminated,
        "program_ids_absent_within_3s": ids_gone,
        "fixture_gone_within_11s": fixture_gone,
        "manifest_before_kill": manifest_state,
        "fd_before": fd_before,
        "fd_after": fd_after,
        "child_session_check": {
            "record": "session",
            "session_id": "crash_recovery",
            "schema_ok": true,
            "note": "the child's own seed record passed core::session_record_ok in this process",
        },
        "cleanup": cleanup,
    });
    if let Err(e) = core::crash_summary_ok(&record) {
        return Err(Failure::failed(format!("crash_summary schema: {e}")));
    }
    println!("{record}");
    Ok(record)
}

fn object_identity() -> Value {
    json!([
        {"name": TLS_OBJECT_NAME, "bytes": TLS_OBJECT.len(), "fnv1a64": format!("{:016x}", core::fnv1a64(TLS_OBJECT))},
        {"name": SYSCALL_OBJECT_NAME, "bytes": SYSCALL_OBJECT.len(), "fnv1a64": format!("{:016x}", core::fnv1a64(SYSCALL_OBJECT))},
    ])
}

fn disk_object_check() -> Value {
    let exe = match std::env::current_exe() {
        Ok(exe) => exe,
        Err(e) => return json!({"checked": false, "error": e.to_string()}),
    };
    let Some(dir) = exe.parent() else {
        return json!({"checked": false, "error": "no release directory"});
    };
    let path = dir.join("../../../bpf").join(TLS_OBJECT_NAME);
    match fs::read(&path) {
        Ok(bytes) => json!({
            "checked": true,
            "path": path.to_string_lossy(),
            "bytes": bytes.len(),
            "fnv1a64": format!("{:016x}", core::fnv1a64(&bytes)),
            "matches_embedded": bytes == TLS_OBJECT,
        }),
        Err(e) => json!({"checked": false, "path": path.to_string_lossy(), "error": e.to_string()}),
    }
}

fn counter_json(c: &core::CounterReport) -> Value {
    json!({
        "submitted": c.submitted,
        "consumed": c.consumed,
        "lost": c.lost,
        "read_failed": c.read_failed,
        "map_failed": c.map_failed,
        "expired": c.expired,
        "truncated": c.truncated,
        "identity_rejected": c.identity_rejected,
        "len_invalid": c.len_invalid,
        "abnormal_ret": c.abnormal_ret,
        "user_read_attempts": c.user_read_attempts,
        "user_read_bytes": c.user_read_bytes,
        "payload_bytes": c.payload_bytes,
        "depth_overflow": c.depth_overflow,
        "unpaired": c.unpaired,
        "entries": c.entries,
        "exits": c.exits,
    })
}

/// What one owned session produced, for the batch record.
#[derive(Debug, Clone)]
pub struct SessionSummary {
    /// Session identifier.
    pub id: &'static str,
    /// Whether the session met its contract.
    pub passed: bool,
    /// Named counters.
    pub counters: core::CounterReport,
    /// Raised issues.
    pub issues: Vec<String>,
    /// Own probe ids were gone after detach.
    pub own_prog_ids_absent: bool,
    /// The workload process is gone.
    pub own_child_gone: bool,
    /// The collector descriptor count returned to its starting value.
    pub fd_equal: bool,
}

#[allow(clippy::too_many_arguments)]
fn session_body(
    report: &mut SessionReport,
    session_id: &'static str,
    wrong_birth: bool,
    seconds: u64,
    caps: u64,
    run: &RunDir,
    fd_before: usize,
    path: &Path,
    expected: &WorkloadId,
) -> Result<(), Failure> {
    let mut fx = Fixture::spawn(path, expected.clone())?;
    report.identity_checks = fx.identity_checks.clone();
    let hz = clock_ticks().map_err(Failure::blocked)?;
    if hz != 100 {
        return Err(Failure::blocked("AT_CLKTCK is not 100; refusing to guess"));
    }
    let pmu = uprobe_pmu_type().map_err(Failure::blocked)?;
    let symbol = libssl_symbol(&fx.libssl_path)?;
    report.set_identity(&fx, expected, &symbol, pmu);
    report.cgroup_id = fx.cgroup_inode;
    let deadline_ns = boottime_ns().map_err(Failure::blocked)? + (seconds + 2) * 1_000_000_000;
    let (mut bpf, ids) = start_probes(&fx, hz, deadline_ns, wrong_birth, caps)?;
    report.program_ids = ids.clone();
    if let Err(f) = fx.verify_identity("post_attach") {
        report.identity_checks = fx.identity_checks.clone();
        return Err(f);
    }
    report.identity_checks = fx.identity_checks.clone();
    let mut ring = RingBuf::try_from(
        bpf.take_map("EVENTS")
            .ok_or_else(|| Failure::failed("EVENTS map missing"))?,
    )
    .map_err(|e| Failure::failed(format!("EVENTS map: {e}")))?;
    let counters = Array::<_, u64>::try_from(
        bpf.take_map("COUNTERS")
            .ok_or_else(|| Failure::failed("COUNTERS map missing"))?,
    )
    .map_err(|e| Failure::failed(format!("COUNTERS map: {e}")))?;
    run.record_session(
        session_id,
        "start",
        json!({
            "session_id": session_id,
            "at_unix": now_unix(),
            "at_ns": boottime_ns().unwrap_or(0),
            "target": report.target,
            "bpf_program_ids": ids,
            "workload": expected.to_json(),
            "run_dir_path": report.run_path,
            "identity_checks": report.identity_checks,
        }),
    )?;
    report.print_seed()?;
    fx.go()?;
    if let Err(f) = fx.verify_identity("post_go") {
        report.identity_checks = fx.identity_checks.clone();
        return Err(f);
    }
    report.identity_checks = fx.identity_checks.clone();
    let mut records = Vec::new();
    let begin = Instant::now();
    while begin.elapsed() < Duration::from_secs(seconds) {
        drain(&mut ring, &mut records, &mut report.issues);
        thread::sleep(Duration::from_millis(5));
    }
    drop(bpf);
    drain(&mut ring, &mut records, &mut report.issues);
    let mut raw = Vec::with_capacity(core::COUNTER_SLOTS);
    for index in 0..core::COUNTER_SLOTS as u32 {
        raw.push(
            counters
                .get(&index, 0)
                .map_err(|e| Failure::failed(format!("COUNTERS get {index}: {e}")))?,
        );
    }
    drop(ring);
    drop(counters);
    let target_pid = fx.pid;
    let target_ticks = fx.start_ticks;
    // The workload prints DONE and then holds until we close its stdin, so the
    // post-GO identity re-check always sees a live process.
    let done_line = fx.next_line(Duration::from_secs(5))?;
    drop(fx.child.stdin.take());
    let status = fx.wait(Duration::from_secs(5))?;
    let done: Value =
        serde_json::from_str(&done_line).map_err(|e| Failure::failed(format!("DONE json: {e}")))?;
    if done.get("record").and_then(Value::as_str) != Some("done") {
        return Err(Failure::failed("workload did not report DONE"));
    }
    if !status.success() {
        return Err(Failure::failed(format!("workload exited with {status}")));
    }
    let absent = program_ids_absent(&ids, Duration::from_secs(3))?;
    // Drop the fixture first: its stdin pipe write end lives in this process
    // and would otherwise look like a leak.
    drop(fx);
    let fd_after = count_fds().map_err(Failure::failed)?;
    let child_gone = !pid_matches_start(target_pid, target_ticks);
    if !absent {
        report
            .issues
            .push("own program ids still loaded after detach".into());
    }
    if !child_gone {
        report
            .issues
            .push("workload process survived the session".into());
    }
    if fd_after != fd_before {
        report.issues.push(format!(
            "collector fd count changed: {fd_before} -> {fd_after}"
        ));
    }
    report.cleanup = json!({
        "prog_ids_absent": absent,
        "child_gone": child_gone,
        "fd_before": fd_before as u64,
        "fd_after": fd_after as u64,
    });
    let analysis = core::analyze(&records);
    let counters_report = core::counter_report(&raw, &analysis);
    // The probe records the kernel cgroup id of every hit; all of them must be
    // one value, and that value must be the cgroup directory we stat'ed.
    let mut seen_cgroup: Option<u64> = None;
    for record in &records {
        match seen_cgroup {
            Some(seen) if seen != record.wire.actor_cgroup => {
                report
                    .issues
                    .push("probe saw more than one cgroup id".into());
            }
            Some(_) => {}
            None => seen_cgroup = Some(record.wire.actor_cgroup),
        }
    }
    if let Some(observed) = seen_cgroup {
        report.cgroup_id = observed;
        if observed != report.cgroup_inode_expected {
            report.issues.push(format!(
                "probe cgroup id {observed} does not match the cgroup inode {}",
                report.cgroup_inode_expected
            ));
        }
    }
    if wrong_birth {
        if counters_report.entries != 0 || counters_report.exits != 0 {
            report
                .issues
                .push("wrong-birth session emitted events".into());
        }
        if counters_report.identity_rejected < 8 {
            report
                .issues
                .push("wrong-birth identity rejections below 8".into());
        }
        if counters_report.user_read_attempts != 0 || counters_report.user_read_bytes != 0 {
            report
                .issues
                .push("wrong-birth session touched a user buffer".into());
        }
        if counters_report.submitted != 0 || counters_report.consumed != 0 {
            report
                .issues
                .push("wrong-birth session submitted records".into());
        }
    } else {
        let failed = core::check_positive(&counters_report, &analysis.aliases());
        for f in &failed {
            report
                .issues
                .push(format!("counter expectation failed: {f}"));
        }
        match core::bind_script(&analysis.rows, &records) {
            Ok(views) => {
                if views.len() == 4 {
                    if !views[0].payload_match || views[0].captured != 512 || views[0].ret != 512 {
                        report.issues.push("step 1 payload/return mismatch".into());
                    }
                    if views[1].direction != "s2c"
                        || views[1].captured != 384
                        || views[1].ret != 384
                    {
                        report
                            .issues
                            .push("step 2 direction/payload mismatch".into());
                    }
                    if views[2].captured as usize != core::SAMPLE_CAP
                        || !views[2].truncated
                        || views[2].ret != 8192
                    {
                        report
                            .issues
                            .push("step 3 over-cap capture is not truncated at 4096".into());
                    }
                    if views[3].ret != -1 || views[3].read_attempted || views[3].captured != 0 {
                        report
                            .issues
                            .push("step 4 negative length read a user buffer".into());
                    }
                    if let Err(e) = core::alias_generation_ok(&views) {
                        report.issues.push(format!("alias generation: {e}"));
                    }
                    if views.iter().any(|v| v.depth != 0) {
                        report.issues.push("unexpected nesting depth".into());
                    }
                }
                report.requests = views.iter().map(request_json).collect();
            }
            Err(e) => report.issues.push(format!("script binding: {e}")),
        }
        let peer_c2s = done["peer"]["c2s_received"].as_i64().unwrap_or(-1);
        let peer_s2c = done["peer"]["s2c_received"].as_i64().unwrap_or(-1);
        if peer_c2s != core::PEER_C2S_TOTAL || peer_s2c != core::PEER_S2C_TOTAL {
            report
                .issues
                .push("peer byte totals do not match the frozen script".into());
        }
        if done["peer"]["c2s_match"].as_bool() != Some(true)
            || done["peer"]["s2c_match"].as_bool() != Some(true)
        {
            report.issues.push("peer payload comparison failed".into());
        }
        for (i, view) in report.requests.iter().enumerate() {
            let step = &done["steps"][i];
            if step["ret"].as_i64() != view["ret"].as_i64() {
                report.issues.push(format!(
                    "workload and probe disagree about step {} return",
                    i + 1
                ));
            }
        }
    }
    report.peer = Some(json!({
        "c2s_received": done["peer"]["c2s_received"].as_i64().unwrap_or(-1),
        "c2s_match": done["peer"]["c2s_match"].as_bool().unwrap_or(false),
        "s2c_received": done["peer"]["s2c_received"].as_i64().unwrap_or(-1),
        "s2c_match": done["peer"]["s2c_match"].as_bool().unwrap_or(false),
    }));
    report.counters = Some(counters_report);
    let after = workload_identity(path)?;
    let unchanged = after == *expected;
    report.workload = Some(workload_block(expected, Some((&after, unchanged))));
    if !unchanged {
        report
            .issues
            .push("workload binary changed during the session".into());
    }
    run.record_session(
        session_id,
        "end",
        json!({
            "session_id": session_id,
            "at_unix": now_unix(),
            "passed": report.passed && report.issues.is_empty(),
            "counters": report.counters.as_ref().map(counter_json),
            "cleanup": report.cleanup,
            "issues": report.issues,
            "workload": report.workload,
        }),
    )?;
    // Read the manifest back and validate it against the contract: an on-disk
    // record that loses a field is a session failure, not a cosmetic problem.
    let manifest = run.read_manifest()?;
    let checked = core::manifest_fields_ok(&manifest, true);
    report.manifest_check = Some(json!({
        "path": run.manifest_path().to_string_lossy(),
        "ok": checked.is_ok(),
        "error": checked.as_ref().err().map(|e| e.to_string()),
        "sessions": manifest["sessions"].clone(),
        "files": manifest["files"].clone(),
        "updated_at_unix": manifest["updated_at_unix"].clone(),
        "created_at_unix": manifest["created_at_unix"].clone(),
        "boot_id": manifest["boot_id"].clone(),
    }));
    if let Err(e) = checked {
        report.issues.push(format!("manifest schema: {e}"));
    }
    Ok(())
}

fn request_json(view: &core::RequestView) -> Value {
    json!({
        "index": view.index,
        "direction": view.direction,
        "declared": view.declared,
        "captured": view.captured,
        "truncated": view.truncated,
        "read_attempted": view.read_attempted,
        "ret": view.ret,
        "payload_match": view.payload_match,
        "alias": view.alias,
        "depth": view.depth,
    })
}

/// Contract workload block: the digest is first class, the binding evidence is
/// attached next to it so a verifier can bind either way.
fn workload_block(workload: &WorkloadId, after: Option<(&WorkloadId, bool)>) -> Value {
    let mut value = workload.to_json();
    if let Value::Object(map) = &mut value {
        map.insert("pre_spawn".into(), workload.to_json());
        map.insert("pinned_expectation".into(), json!(EXPECTED_WORKLOAD_SHA256));
        map.insert(
            "verifier_note".into(),
            json!("pinned_expectation is null in this revision; the top-level sha256/size/path are the values an independent verifier must bind"),
        );
        map.insert(
            "bound_by".into(),
            json!("collector at spawn, re-checked after the session"),
        );
        match after {
            Some((after, unchanged)) => {
                map.insert("post_session".into(), after.to_json());
                map.insert("unchanged".into(), json!(unchanged));
            }
            None => {
                map.insert("post_session".into(), Value::Null);
                map.insert("unchanged".into(), Value::Null);
            }
        }
    }
    value
}

#[allow(clippy::too_many_arguments)]
fn run_owned_session(
    session_id: &'static str,
    wrong_birth: bool,
    seconds: u64,
    caps: u64,
    run: &RunDir,
    boot: &str,
    fd_before: usize,
    path: &Path,
    expected: &WorkloadId,
) -> Result<SessionSummary, Failure> {
    let mut report = SessionReport::new(session_id, wrong_birth, run, boot);
    let outcome = session_body(
        &mut report,
        session_id,
        wrong_birth,
        seconds,
        caps,
        run,
        fd_before,
        path,
        expected,
    );
    match &outcome {
        Ok(()) => {
            if !report.issues.is_empty() {
                report.passed = false;
            }
        }
        Err(f) => {
            report.passed = false;
            report.failure = Some(f.reasons.clone());
        }
    }
    let mut closeout = report.closeout_json();
    if let Err(e) = core::session_record_ok(&closeout, true) {
        report.passed = false;
        let mut reasons = report.failure.clone().unwrap_or_default();
        reasons.push(format!("session_closeout schema: {e}"));
        report.failure = Some(reasons);
        closeout = report.closeout_json();
    }
    println!("{closeout}");
    let summary = SessionSummary {
        id: session_id,
        passed: report.passed,
        counters: report.counters.clone().unwrap_or_default(),
        issues: report.issues.clone(),
        own_prog_ids_absent: report.cleanup["prog_ids_absent"].as_bool().unwrap_or(false),
        own_child_gone: report.cleanup["child_gone"].as_bool().unwrap_or(false),
        fd_equal: report.cleanup["fd_before"] == report.cleanup["fd_after"]
            && report.cleanup["fd_before"].as_u64().unwrap_or(0) > 0,
    };
    match outcome {
        Ok(()) => Ok(summary),
        Err(f) => Err(f),
    }
}

/// Batch-scoped state that must stay readable on every exit path.
#[derive(Default)]
struct BatchCtx {
    run: Option<RunDir>,
    run_id: Option<String>,
    run_path: Option<String>,
    boot_id: String,
    workload: Option<WorkloadId>,
    own_prog_ids_absent: bool,
    own_child_gone: bool,
    fd_equal: bool,
}

fn run_batch(
    ctx: &mut BatchCtx,
    scenarios: &mut Vec<Value>,
    fd_before: usize,
) -> Result<(), Failure> {
    let euid = effective_uid().map_err(Failure::blocked)?;
    if euid != 0 {
        return Err(Failure::blocked("tls-fixture-check must run as root"));
    }
    let caps = capability_bits().map_err(Failure::blocked)?;
    let boot = boot_id().map_err(Failure::blocked)?;
    ctx.boot_id = boot.clone();
    let dir = RunDir::create(&boot)?;
    // Save the exact run identity before anything else can fail: every exit
    // path below can now name the directory it created.
    ctx.run_id = Some(dir.run_id.clone());
    ctx.run_path = Some(dir.path_text());
    ctx.run = Some(dir);
    let path = fixture_path()?;
    let workload = workload_identity(&path)?;
    if let Some(want) = EXPECTED_WORKLOAD_SHA256 {
        if want != workload.sha256 {
            return Err(Failure::failed(format!(
                "workload sha256 {} does not match the pinned {want}",
                workload.sha256
            )));
        }
    }
    ctx.workload = Some(workload.clone());
    let disk = disk_object_check();
    let batch = json!({
        "record": "batch",
        "schema": 1,
        "batch_run_id": ctx.run_id,
        "run_dir_path": ctx.run_path,
        "boot_id": ctx.boot_id,
        "kernel": kernel_string().map_err(Failure::blocked)?,
        "mode": "wsl_x86_64_owned_fixture",
        "euid": euid,
        "capabilities": {
            "bpf": core::has_cap(caps, core::CAP_BPF),
            "perfmon": core::has_cap(caps, core::CAP_PERFMON),
            "sys_admin": core::has_cap(caps, core::CAP_SYS_ADMIN),
            "cap_eff": format!("{caps:016x}"),
            "note": "capability bits are a pre-check only; the real load/attach below decides",
        },
        "workload": workload.to_json(),
        "pinned_workload_sha256": EXPECTED_WORKLOAD_SHA256,
        "embedded_bpf": object_identity(),
        "disk_object": disk,
    });
    if let Err(e) = core::batch_record_ok(&batch) {
        return Err(Failure::failed(format!("batch record schema: {e}")));
    }
    println!("{batch}");
    let run = ctx
        .run
        .clone()
        .ok_or_else(|| Failure::failed("run directory disappeared"))?;
    let positive = run_owned_session(
        "positive",
        false,
        SESSION_SECONDS,
        caps,
        &run,
        &boot,
        fd_before,
        &path,
        &workload,
    )?;
    ctx.own_prog_ids_absent = positive.own_prog_ids_absent;
    ctx.own_child_gone = positive.own_child_gone;
    ctx.fd_equal = positive.fd_equal;
    for issue in &positive.issues {
        println!(
            "{}",
            json!({"record": "issue", "session": positive.id, "issue": issue})
        );
    }
    scenarios.push(json!({
        "session": "positive",
        "passed": positive.passed,
        "issues": positive.issues,
        "counters": counter_json(&positive.counters),
    }));
    let wrong = run_owned_session(
        "wrong_birth",
        true,
        SESSION_SECONDS,
        caps,
        &run,
        &boot,
        fd_before,
        &path,
        &workload,
    )?;
    ctx.own_prog_ids_absent = ctx.own_prog_ids_absent && wrong.own_prog_ids_absent;
    ctx.own_child_gone = ctx.own_child_gone && wrong.own_child_gone;
    ctx.fd_equal = ctx.fd_equal && wrong.fd_equal;
    for issue in &wrong.issues {
        println!(
            "{}",
            json!({"record": "issue", "session": wrong.id, "issue": issue})
        );
    }
    scenarios.push(json!({
        "session": "wrong_birth",
        "passed": wrong.passed,
        "issues": wrong.issues,
        "counters": counter_json(&wrong.counters),
    }));
    let crash = run_crash_scenario(fd_before)?;
    scenarios.push(crash.clone());
    // The crash scenario has its own record type; it passes when that record
    // validates against the contract, not because it carries a "passed" flag.
    let crash_ok =
        crash["record"].as_str() == Some("crash_summary") && core::crash_summary_ok(&crash).is_ok();
    let ok = positive.passed && wrong.passed && crash_ok;
    if !ok {
        return Err(Failure::failed("one or more scenarios failed"));
    }
    Ok(())
}

/// Run the full TLS fixture acceptance batch and return its exit code.
pub fn run_check() -> u8 {
    let started = Instant::now();
    let mut scenarios: Vec<Value> = Vec::new();
    let mut ctx = BatchCtx::default();
    let fd_before = count_fds().unwrap_or(usize::MAX);
    let outcome = run_batch(&mut ctx, &mut scenarios, fd_before);
    let elapsed = started.elapsed().as_secs();
    // Decide the budget class before deciding whether to delete the evidence.
    let capped = elapsed > BATCH_BUDGET_SECONDS;
    let blocked = outcome.as_ref().err().map(|f| f.blocked).unwrap_or(false);
    let mut reasons = outcome
        .as_ref()
        .err()
        .map(|f| f.reasons.clone())
        .unwrap_or_default();
    let mut verdict = core::final_verdict(outcome.is_ok(), blocked, capped);
    if let Err(f) = &outcome {
        eprintln!(
            "observer {}: {}",
            verdict.as_str().to_lowercase(),
            f.describe()
        );
    }
    let fd_after = count_fds().unwrap_or(usize::MAX);
    let mut cleanup = json!({
        "own_prog_ids_absent": ctx.own_prog_ids_absent,
        "own_child_gone": ctx.own_child_gone,
        "fd_equal": fd_after == fd_before,
        "run_dir_removed": false,
        "run_dir_path": ctx.run_path,
        "fd_before": fd_before,
        "fd_after": fd_after,
    });
    if core::should_remove_run_dir(verdict) {
        match ctx.run.clone() {
            Some(dir) => match dir.remove_if_owned() {
                Ok(()) => {
                    cleanup["run_dir_removed"] = json!(true);
                }
                Err(f) => {
                    // A failed teardown is a failure of the batch, not a note.
                    verdict = core::Verdict::Fail;
                    reasons.extend(f.reasons.clone());
                    cleanup["run_dir_removed"] = json!(false);
                    cleanup["remove_error"] = json!(f.reasons);
                }
            },
            None => {
                verdict = core::Verdict::Fail;
                reasons.push("run directory missing at cleanup time".into());
            }
        }
    }
    if let Err(e) = core::cleanup_fields_ok(&cleanup) {
        // A closeout that cannot name its own teardown state is a failure.
        verdict = core::Verdict::Fail;
        reasons.push(format!("closeout cleanup schema: {e}"));
    }
    println!(
        "{}",
        json!({
            "record": "closeout",
            "schema": 1,
            "batch_run_id": ctx.run_id,
            "run_dir_path": ctx.run_path,
            "boot_id": ctx.boot_id,
            "verdict": verdict.as_str(),
            "passed": verdict.is_pass(),
            "failure_reasons": reasons,
            "scenarios": scenarios,
            "cleanup": cleanup,
            "workload": ctx.workload.as_ref().map(WorkloadId::to_json),
            "budget": {
                "batch_seconds": elapsed,
                "max_session_seconds": SESSION_SECONDS,
                "max_batch_seconds": BATCH_BUDGET_SECONDS,
                "bpf_alloc_declared_bytes": declared_bpf_bytes(),
                "bpf_alloc_limit_bytes": core::BPF_ALLOC_DECLARED_BYTES,
                "sample_cap_bytes": core::SAMPLE_CAP,
                "event_limit": core::EVENT_LIMIT,
                "payload_budget_bytes": core::PAYLOAD_BUDGET,
            },
            "not_verified": [
                "arm64_live_attach",
                "r5_kernel",
                "android_or_sportsworld",
                "ssl_read_probe",
                "http_parsing",
                "quic",
                "external_pid_or_app_mode",
                "external_timeout_or_TERM_teardown",
            ],
            "external_timeout_cleanup_claim": false,
        })
    );
    verdict.exit_code()
}

fn hold_body(
    report: &mut SessionReport,
    path: &Path,
    workload: &WorkloadId,
    run: &RunDir,
    caps: u64,
    hold_seconds: u64,
) -> Result<(), Failure> {
    let mut fx = Fixture::spawn(path, workload.clone())?;
    report.identity_checks = fx.identity_checks.clone();
    let hz = clock_ticks().map_err(Failure::blocked)?;
    if hz != 100 {
        return Err(Failure::blocked("AT_CLKTCK is not 100; refusing to guess"));
    }
    let pmu = uprobe_pmu_type().map_err(Failure::blocked)?;
    let symbol = libssl_symbol(&fx.libssl_path)?;
    report.set_identity(&fx, workload, &symbol, pmu);
    report.cgroup_id = fx.cgroup_inode;
    let deadline_ns =
        boottime_ns().map_err(Failure::blocked)? + (CRASH_HOLD_SECONDS + 30) * 1_000_000_000;
    let (bpf, ids) = start_probes(&fx, hz, deadline_ns, false, caps)?;
    report.program_ids = ids.clone();
    fx.verify_identity("post_attach")?;
    report.identity_checks = fx.identity_checks.clone();
    run.record_session(
        "crash_recovery",
        "start",
        json!({
            "session_id": "crash_recovery",
            "at_unix": now_unix(),
            "target": report.target,
            "target_tgid": report.target_tgid,
            "target_start_ticks": report.target_start_ticks,
            "bpf_program_ids": ids,
            "program_ids": ids,
            "workload": workload.to_json(),
            "run_dir_path": report.run_path,
            "identity_checks": report.identity_checks,
            "note": "held without GO so the parent can kill a live collector",
        }),
    )?;
    // Announce the seed only after the on-disk record exists: the parent reads
    // the manifest the moment it sees this line.
    report.print_seed()?;
    // Keep the probes loaded and the workload alive but blocked on stdin. The
    // parent kills this process; if it ever returns here the hold failed.
    let _keep = &bpf;
    let _keep_fixture = &fx;
    thread::sleep(Duration::from_secs(hold_seconds.max(CRASH_HOLD_SECONDS)));
    report.passed = false;
    report.failure = Some(vec!["hold session was not killed by the parent".into()]);
    Ok(())
}

fn run_hold_session(seconds: u64) -> u8 {
    let euid = match effective_uid() {
        Ok(uid) => uid,
        Err(e) => {
            eprintln!("observer rejected: {e}");
            return EXIT_BLOCKED;
        }
    };
    if euid != 0 {
        eprintln!("observer rejected: internal session needs root");
        return EXIT_BLOCKED;
    }
    let caps = match capability_bits() {
        Ok(caps) => caps,
        Err(e) => {
            eprintln!("observer rejected: {e}");
            return EXIT_BLOCKED;
        }
    };
    let boot = match boot_id() {
        Ok(boot) => boot,
        Err(e) => {
            eprintln!("observer rejected: {e}");
            return EXIT_BLOCKED;
        }
    };
    let dir = match RunDir::create(&boot) {
        Ok(dir) => dir,
        Err(f) => {
            eprintln!("observer rejected: {:?}", f.reasons);
            return if f.blocked { EXIT_BLOCKED } else { EXIT_FAIL };
        }
    };
    let path = match fixture_path() {
        Ok(path) => path,
        Err(f) => {
            eprintln!("observer rejected: {:?}", f.reasons);
            return if f.blocked { EXIT_BLOCKED } else { EXIT_FAIL };
        }
    };
    let workload = match workload_identity(&path) {
        Ok(workload) => workload,
        Err(f) => {
            eprintln!("observer rejected: {:?}", f.reasons);
            return if f.blocked { EXIT_BLOCKED } else { EXIT_FAIL };
        }
    };
    let mut report = SessionReport::new("crash_recovery", false, &dir, &boot);
    let outcome = hold_body(&mut report, &path, &workload, &dir, caps, seconds);
    if let Err(f) = outcome {
        report.passed = false;
        report.failure = Some(f.reasons);
    }
    println!("{}", report.closeout_json());
    EXIT_FAIL
}

/// Internal child mode used only by the batch crash scenario.
pub fn run_session_child(scenario: &str, seconds: u64) -> u8 {
    if scenario != "crash_recovery" || !(1..=10).contains(&seconds) {
        eprintln!("observer rejected: unknown internal session mode");
        return EXIT_USAGE;
    }
    run_hold_session(seconds)
}
