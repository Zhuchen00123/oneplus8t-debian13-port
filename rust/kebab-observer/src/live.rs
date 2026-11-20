use aya::{
    maps::{Array, RingBuf},
    programs::{loaded_programs, RawTracePoint},
    Ebpf,
};
use kebab_observer::{aux_clock_ticks, decode, start_ticks};
use serde_json::json;
use std::{
    error::Error,
    fs::{self, OpenOptions},
    io::{BufWriter, Read, Write},
    os::unix::fs::{MetadataExt, OpenOptionsExt},
    path::Path,
    process::{Child, Command, Stdio},
    thread,
    time::{Duration, Instant},
};

type Result<T> = std::result::Result<T, Box<dyn Error>>;
const OBJECT: &[u8] = aya::include_bytes_aligned!("../bpf/syscalls.bpf.o");

struct Fixture(Child);
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn boottime_ns() -> Result<u64> {
    let text = fs::read_to_string("/proc/uptime")?;
    let mut parts = text
        .split_whitespace()
        .next()
        .ok_or("missing uptime")?
        .split('.');
    let seconds: u64 = parts.next().ok_or("missing seconds")?.parse()?;
    let fraction = parts.next().ok_or("missing uptime fraction")?;
    if fraction.len() != 2 {
        return Err("unexpected uptime precision".into());
    }
    Ok(seconds
        .checked_mul(1_000_000_000)
        .ok_or("uptime overflow")?
        .checked_add(fraction.parse::<u64>()? * 10_000_000)
        .ok_or("uptime overflow")?)
}

fn fixture_child(seconds: u64) -> Result<()> {
    if !(1..=10).contains(&seconds) {
        return Err("fixture duration rejected".into());
    }
    let mut go = [0u8; 1];
    std::io::stdin().read_exact(&mut go)?;
    if go != *b"G" {
        return Err("fixture handshake rejected".into());
    }
    let begin = Instant::now();
    while begin.elapsed() < Duration::from_secs(seconds) {
        let _ = fs::read_to_string("/proc/self/stat")?;
        // Controlled ENOENT provides a known syscall return value, without
        // recording any path or user buffer in the event stream.
        let error = fs::File::open("/proc/self/kebab-observer-intentionally-absent")
            .err()
            .ok_or("fixture unexpectedly opened absent file")?;
        if error.raw_os_error() != Some(2) {
            return Err("fixture ENOENT missing".into());
        }
        thread::sleep(Duration::from_millis(10));
    }
    Ok(())
}

fn proc_identity(pid: u32) -> Result<(u64, u64)> {
    let base = format!("/proc/{pid}");
    let ticks = start_ticks(&fs::read_to_string(format!("{base}/stat"))?)?;
    let status = fs::read_to_string(format!("{base}/status"))?;
    let uid = status
        .lines()
        .find_map(|line| line.strip_prefix("Uid:"))
        .ok_or("target uid missing")?
        .split_whitespace()
        .next()
        .ok_or("invalid uid")?
        .parse()?;
    Ok((ticks, uid))
}

/// Entry point for the binary: routes the new subcommands and otherwise
/// keeps the historical behaviour (usage errors still exit 2).
pub fn dispatch() -> u8 {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args == ["tls-fixture-check"] {
        return crate::tls_live::run_check();
    }
    if args.first().is_some_and(|first| first == "track") {
        return crate::track::run(&args[1..]);
    }
    if args.first().is_some_and(|first| first == "tls-attach") {
        return crate::tls_attach::run(&args[1..]);
    }
    if args.len() == 3 && args[0] == "--tls-session" {
        return match args[2].parse::<u64>() {
            Ok(seconds) => crate::tls_live::run_session_child(&args[1], seconds),
            Err(_) => 2,
        };
    }
    match run() {
        Ok(()) => 0,
        Err(error) => {
            eprintln!("observer rejected: {error}");
            2
        }
    }
}

pub fn run() -> Result<()> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args == ["inspect-owned"] {
        let mut owned = Vec::new();
        for info in loaded_programs() {
            let info = info?;
            if info
                .name_as_str()
                .is_some_and(|name| name.starts_with("observer_"))
            {
                owned.push(info.id());
            }
        }
        println!("{}", json!({"observer_program_ids":owned,"read_only":true}));
        return Ok(());
    }
    if args.len() == 2 && args[0] == "--fixture-child" {
        return fixture_child(args[1].parse()?);
    }
    if args.len() != 3 || !matches!(args[0].as_str(), "fixture" | "fixture-wrong-birth") {
        return Err("usage: kebab-observer fixture|fixture-wrong-birth SECONDS NEW_OUTPUT_FILE; \
external-target tracking is the separate 'track --uid UID --cgroup-id ID [--tgid PID] --duration 1..=60 --out NEW_JSONL_FILE' subcommand"
            .into());
    }
    let seconds: u64 = args[1].parse()?;
    if !(1..=10).contains(&seconds) {
        return Err("fixture seconds must be 1..10".into());
    }
    let wrong_birth = args[0] == "fixture-wrong-birth";
    let output = Path::new(&args[2]);
    let file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(output)?;
    let mut file = BufWriter::with_capacity(65536, file);
    let boot = fs::read_to_string("/proc/sys/kernel/random/boot_id")?
        .trim()
        .to_owned();
    let release = fs::read_to_string("/proc/sys/kernel/osrelease")?
        .trim()
        .to_owned();
    let hz = aux_clock_ticks(&fs::read("/proc/self/auxv")?)?;
    let child = Command::new(std::env::current_exe()?)
        .arg("--fixture-child")
        .arg(seconds.to_string())
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()?;
    let mut fixture = Fixture(child);
    let pid = fixture.0.id();
    let (ticks, uid) = proc_identity(pid)?;
    let ns = fs::metadata(format!("/proc/{pid}/ns/pid"))?;
    let status = fs::read_to_string(format!("/proc/{pid}/status"))?;
    let ns_pid: u64 = status
        .lines()
        .find_map(|s| s.strip_prefix("NSpid:"))
        .ok_or("NSpid missing")?
        .split_whitespace()
        .last()
        .ok_or("NSpid empty")?
        .parse()?;
    let mut bpf = Ebpf::load(OBJECT)?;
    {
        let mut config = Array::<_, u64>::try_from(bpf.map_mut("CONFIG").ok_or("CONFIG missing")?)?;
        let deadline = boottime_ns()? + (seconds + 2) * 1_000_000_000;
        let values = [
            ns_pid,
            ticks + u64::from(wrong_birth),
            hz,
            deadline,
            uid,
            0,
            10000,
            ns.dev(),
            ns.ino(),
            0,
            0,
            0,
        ];
        for (index, value) in values.iter().enumerate() {
            config.set(index as u32, *value, 0)?;
        }
    }
    let mut ids = Vec::new();
    for (name, event) in [
        ("observer_enter", "sys_enter"),
        ("observer_exit", "sys_exit"),
    ] {
        let program: &mut RawTracePoint =
            bpf.program_mut(name).ok_or("program missing")?.try_into()?;
        program.load()?;
        ids.push(program.info()?.id());
        program.attach(event)?;
    }
    // The Binder object is separate: WSL has no Binder BTF. This prevents
    // treating a syscall-only test as Android/Binder acceptance.
    if proc_identity(pid)? != (ticks, uid) {
        return Err("fixture identity changed".into());
    }
    let mut ring = RingBuf::try_from(bpf.take_map("EVENTS").ok_or("EVENTS missing")?)?;
    let counters = Array::<_, u64>::try_from(bpf.take_map("COUNTERS").ok_or("COUNTERS missing")?)?;
    writeln!(
        file,
        "{}",
        json!({"record":"session", "schema":1, "boot_id":boot,
        "kernel":release, "target_tgid":pid, "target_start_ticks":ticks, "target_uid":uid,
        "namespace_tgid":ns_pid,"namespace_inode":ns.ino(),
        "wrong_birth_test":wrong_birth, "seconds":seconds, "program_ids":ids,
        "mode":"owned_self_fixture", "app_injection":false, "user_memory_read":false})
    )?;
    fixture
        .0
        .stdin
        .as_mut()
        .ok_or("fixture stdin missing")?
        .write_all(b"G")?;
    let begin = Instant::now();
    let (mut events, mut enters, mut exits, mut enoent) = (0u64, 0u64, 0u64, 0u64);
    let mut record = |bytes: &[u8]| -> Result<()> {
        let event = decode(bytes)?;
        if event.namespace_tgid as u64 != ns_pid
            || event.namespace_inode != ns.ino()
            || event.actor_start_ns / 10_000_000 != ticks
            || event.uid as u64 != uid
        {
            return Err("out-of-scope event rejected".into());
        }
        enters += u64::from(event.kind == "sys_enter");
        exits += u64::from(event.kind == "sys_exit");
        enoent += u64::from(event.kind == "sys_exit" && event.result == -2);
        writeln!(file, "{}", serde_json::to_string(&event)?)?;
        events += 1;
        if events > 10000 {
            return Err("event cap exceeded".into());
        }
        Ok(())
    };
    let collection = (|| -> Result<()> {
        while begin.elapsed() < Duration::from_secs(seconds + 1) {
            let mut batch = 0;
            while batch < 256 {
                let Some(bytes) = ring.next() else {
                    break;
                };
                record(&bytes)?;
                batch += 1;
            }
            thread::sleep(Duration::from_millis(5));
        }
        Ok(())
    })();
    // These objects own every unpinned FD; no system-wide tracing state was
    // changed. Dropping bpf closes only this process's own links/programs.
    drop(bpf);
    let drain = (|| -> Result<()> {
        for _ in 0..10000 {
            let Some(bytes) = ring.next() else {
                return Ok(());
            };
            record(&bytes)?;
        }
        Err("final drain exceeded event cap".into())
    })();
    let _ = &record;
    let counts = (0..8)
        .map(|index| counters.get(&index, 0))
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(ring);
    drop(counters);
    let mut absent = false;
    for _ in 0..100 {
        let remaining = loaded_programs()
            .map(|info| info.map(|p| p.id()))
            .collect::<std::result::Result<Vec<_>, _>>()?;
        absent = ids.iter().all(|id| !remaining.contains(id));
        if absent {
            break;
        }
        thread::sleep(Duration::from_millis(10));
    }
    let child_status = fixture.0.wait()?;
    let passed = collection.is_ok()
        && drain.is_ok()
        && absent
        && child_status.success()
        && counts[1] == events
        && if wrong_birth {
            events == 0 && counts[3] > 0
        } else {
            enters > 0 && exits > 0 && enoent > 0
        };
    let summary = json!({"record":"closeout", "passed":passed, "events":events,
        "sys_enter":enters,"sys_exit":exits,"known_enoent":enoent,"counters":counts,
        "program_ids_absent":absent,"child_success":child_status.success(),"ring_fully_accounted":counts[1]==events,
        "binder_attached":false,"coresight_used":false});
    writeln!(file, "{summary}")?;
    file.flush()?;
    file.get_ref().sync_all()?;
    println!("{summary}");
    collection?;
    drain?;
    if !passed {
        return Err("fixture acceptance failed".into());
    }
    Ok(())
}
