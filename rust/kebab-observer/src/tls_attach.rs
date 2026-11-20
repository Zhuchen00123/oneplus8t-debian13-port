// SPDX-License-Identifier: Apache-2.0
//! External TLS attach: uprobes on SSL_write / SSL_read of a library that the
//! collector did not build, addressed by file offset so an APK-hosted .so
//! (extractNativeLibs=false) is probed on its real inode and not on a copy.
//!
//! Boundaries: nothing here starts or stops the target, writes into its memory,
//! touches tracefs, installs a CA or disables verification. The only user
//! memory access is a bounded read of the call's own argument buffer.

use std::fs::{self, File, OpenOptions};
use std::io::{BufWriter, Read, Seek, SeekFrom, Write};
use std::os::unix::fs::MetadataExt;
use std::path::Path;
use std::time::{Duration, Instant};

use aya::maps::{Array, RingBuf};
use aya::programs::UProbe;
use aya::Ebpf;
use kebab_observer::tls_attach_core as core;
use serde_json::{json, Value};

#[cfg(target_arch = "aarch64")]
const OBJECT: &[u8] = aya::include_bytes_aligned!("../bpf/tls_attach.arm64.bpf.o");
#[cfg(target_arch = "aarch64")]
const OBJECT_NAME: &str = "tls_attach.arm64.bpf.o";
#[cfg(target_arch = "x86_64")]
const OBJECT: &[u8] = aya::include_bytes_aligned!("../bpf/tls_attach.x86_64.bpf.o");
#[cfg(target_arch = "x86_64")]
const OBJECT_NAME: &str = "tls_attach.x86_64.bpf.o";

/// Exit codes, shared with the other subcommands.
pub const EXIT_PASS: u8 = 0;
pub const EXIT_FAIL: u8 = 1;
pub const EXIT_USAGE: u8 = 2;
pub const EXIT_BLOCKED: u8 = 3;

/// Program table: (name, slot, direction write?, enter?).
const PROGRAMS: [(&str, u8, bool, bool); 8] = [
    ("ta_write_enter_0", 0, true, true),
    ("ta_write_exit_0", 0, true, false),
    ("ta_read_enter_0", 0, false, true),
    ("ta_read_exit_0", 0, false, false),
    ("ta_write_enter_1", 1, true, true),
    ("ta_write_exit_1", 1, true, false),
    ("ta_read_enter_1", 1, false, true),
    ("ta_read_exit_1", 1, false, false),
];

struct Failure {
    blocked: bool,
    message: String,
}

impl Failure {
    fn failed(message: impl Into<String>) -> Self {
        Self {
            blocked: false,
            message: message.into(),
        }
    }
    fn blocked(message: impl Into<String>) -> Self {
        Self {
            blocked: true,
            message: message.into(),
        }
    }
}

fn read_proc(path: &str) -> Result<String, Failure> {
    fs::read_to_string(path).map_err(|error| Failure::blocked(format!("{path}: {error}")))
}

fn proc_stat_ticks(pid: u32) -> Result<u64, Failure> {
    let stat = read_proc(&format!("/proc/{pid}/stat"))?;
    let close = stat
        .rfind(')')
        .ok_or_else(|| Failure::failed("stat has no comm terminator"))?;
    let fields: Vec<&str> = stat[close + 1..].split_whitespace().collect();
    fields
        .get(19)
        .and_then(|value| value.parse::<u64>().ok())
        .ok_or_else(|| Failure::failed("stat has no starttime field"))
}

fn proc_status_fields(pid: u32) -> Result<(u32, u32, Vec<u32>), Failure> {
    let status = read_proc(&format!("/proc/{pid}/status"))?;
    let mut uid = None;
    let mut gid = None;
    let mut nspid = Vec::new();
    for line in status.lines() {
        if let Some(rest) = line.strip_prefix("Uid:") {
            uid = rest.split_whitespace().next().and_then(|v| v.parse().ok());
        } else if let Some(rest) = line.strip_prefix("Gid:") {
            gid = rest.split_whitespace().next().and_then(|v| v.parse().ok());
        } else if let Some(rest) = line.strip_prefix("NSpid:") {
            nspid = rest
                .split_whitespace()
                .filter_map(|value| value.parse::<u32>().ok())
                .collect();
        }
    }
    let uid = uid.ok_or_else(|| Failure::failed("status has no Uid line"))?;
    let gid = gid.unwrap_or(0);
    Ok((uid, gid, nspid))
}

fn cgroup_inode(pid: u32) -> Result<Option<u64>, Failure> {
    let text = read_proc(&format!("/proc/{pid}/cgroup"))?;
    for line in text.lines() {
        let mut parts = line.splitn(3, ':');
        let (Some(_id), Some(_controllers), Some(path)) =
            (parts.next(), parts.next(), parts.next())
        else {
            continue;
        };
        let directory = format!("/sys/fs/cgroup{path}");
        if let Ok(metadata) = fs::metadata(&directory) {
            return Ok(Some(metadata.ino()));
        }
    }
    Ok(None)
}

fn ns_ids(pid: u32) -> Result<(u64, u64), Failure> {
    let metadata = fs::metadata(format!("/proc/{pid}/ns/pid"))
        .map_err(|error| Failure::blocked(format!("/proc/{pid}/ns/pid: {error}")))?;
    Ok((metadata.dev(), metadata.ino()))
}

fn boottime_ns() -> Result<u64, Failure> {
    let text = read_proc("/proc/uptime")?;
    let first = text
        .split_whitespace()
        .next()
        .ok_or_else(|| Failure::failed("uptime is empty"))?;
    let (whole, fraction) = first.split_once('.').unwrap_or((first, "0"));
    let seconds: u64 = whole
        .parse()
        .map_err(|_| Failure::failed("uptime seconds are not a number"))?;
    let mut nanos: u64 = 0;
    for (index, digit) in fraction.chars().take(9).enumerate() {
        let value = digit
            .to_digit(10)
            .ok_or_else(|| Failure::failed("uptime fraction is not numeric"))?;
        nanos += u64::from(value) * 10u64.pow(8 - index as u32);
    }
    Ok(seconds * 1_000_000_000 + nanos)
}

fn clock_ticks() -> Result<u64, Failure> {
    let output = std::process::Command::new("getconf")
        .arg("CLK_TCK")
        .output()
        .map_err(|error| Failure::blocked(format!("getconf CLK_TCK: {error}")))?;
    let text = String::from_utf8_lossy(&output.stdout);
    text.trim()
        .parse::<u64>()
        .map_err(|_| Failure::failed("getconf CLK_TCK is not a number"))
}

fn boot_id() -> String {
    fs::read_to_string("/proc/sys/kernel/random/boot_id")
        .map(|text| text.trim().to_string())
        .unwrap_or_default()
}

fn kernel_string() -> String {
    fs::read_to_string("/proc/sys/kernel/osrelease")
        .map(|text| text.trim().to_string())
        .unwrap_or_default()
}

fn effective_uid() -> u32 {
    fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|status| {
            status
                .lines()
                .find_map(|line| line.strip_prefix("Uid:"))
                .and_then(|rest| rest.split_whitespace().next())
                .and_then(|value| value.parse::<u32>().ok())
        })
        .unwrap_or(0)
}

fn fnv1a64(bytes: &[u8]) -> u64 {
    let mut hash = 0xcbf2_9ce4_8422_2325u64;
    for byte in bytes {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    hash
}

/// One snapshot of /proc/<pid>/maps plus how well it parsed.
struct MapsSnapshot {
    lines: Vec<core::MapsLine>,
    parsed: usize,
    skipped: usize,
}

fn read_maps(pid: u32) -> Result<MapsSnapshot, Failure> {
    let text = read_proc(&format!("/proc/{pid}/maps"))?;
    let parsed = core::parse_maps(&text);
    Ok(MapsSnapshot {
        parsed: parsed.lines.len(),
        skipped: parsed.skipped,
        lines: parsed.lines,
    })
}

fn stat_opt(path: &str) -> Option<(u64, u64)> {
    fs::metadata(path).ok().map(|meta| (meta.dev(), meta.ino()))
}

/// Identity of the file behind a maps line, read through map_files. That
/// interface is privileged, so a caller that cannot read it falls back to
/// inode+basename and records which proof it actually used.
fn map_files_identity(pid: u32, line: &core::MapsLine) -> Option<(u64, u64)> {
    stat_opt(&format!("/proc/{pid}/map_files/{}", line.range()))
}

/// Read a window of a file that may be huge (an APK); never the whole file.
fn read_window(path: &str, offset: u64, want: u64) -> Result<Vec<u8>, Failure> {
    let mut file = File::open(path)
        .map_err(|error| Failure::blocked(format!("open {path}: {error}")))?;
    file.seek(SeekFrom::Start(offset))
        .map_err(|error| Failure::blocked(format!("seek {path}: {error}")))?;
    let mut buffer = vec![0u8; want as usize];
    let mut filled = 0usize;
    while filled < buffer.len() {
        match file.read(&mut buffer[filled..]) {
            Ok(0) => break,
            Ok(n) => filled += n,
            Err(error) => {
                return Err(Failure::blocked(format!("read {path}: {error}")));
            }
        }
    }
    buffer.truncate(filled);
    Ok(buffer)
}

/// Load enough of the embedded ELF image to walk its headers and sections.
fn load_elf_image(path: &str, elf_base: u64) -> Result<(Vec<u8>, core::ElfMeta), Failure> {
    const LIMIT: u64 = 64 << 20;
    let header = read_window(path, elf_base, 4096)?;
    let window = core::header_window(&header).map_err(|error| {
        Failure::blocked(format!("{path}@{elf_base}: {error}"))
    })?;
    let mut image = read_window(path, elf_base, window.min(LIMIT).max(header.len() as u64))?;
    let mut meta = core::parse_elf(&image)
        .map_err(|error| Failure::blocked(format!("{path}@{elf_base}: {error}")))?;
    let need = meta.section_window();
    if need > image.len() as u64 {
        image = read_window(path, elf_base, need.min(LIMIT))?;
        meta = core::parse_elf(&image)
            .map_err(|error| Failure::blocked(format!("{path}@{elf_base}: {error}")))?;
        if meta.section_window() > image.len() as u64 {
            return Err(Failure::blocked(format!(
                "{path}@{elf_base}: section tables need {} bytes, cap is {LIMIT}",
                meta.section_window()
            )));
        }
    }
    Ok((image, meta))
}

struct Probe {
    form: String,
    symbol: Option<String>,
    symbol_vaddr: Option<u64>,
    file_offset: u64,
    va: Option<u64>,
    pt_load_verified: bool,
    maps_verified: bool,
    segment: Option<Value>,
    bias: Option<i64>,
    bias_source: Option<&'static str>,
    bias_cross_checked: usize,
    bias_warnings: Vec<String>,
    bias_anchor: Option<Value>,
}

struct TargetPlan {
    label: String,
    given_path: String,
    attach_path: String,
    resolution: &'static str,
    dev: String,
    inode: u64,
    maps_dev: Option<String>,
    association: &'static str,
    confirmation: &'static str,
    map_files_verified: bool,
    dev_drift: bool,
    maps_checked: bool,
    maps_lines: usize,
    maps_skipped: usize,
    candidates: Vec<String>,
    elf_base: u64,
    class64: bool,
    machine: u16,
    bias: Option<i64>,
    bias_source: Option<&'static str>,
    bias_cross_checked: usize,
    bias_warnings: Vec<String>,
    arch_compatible: bool,
    mapped_segments: usize,
    mapped_paths: Vec<String>,
    exec_perms: String,
    write: Probe,
    read: Option<Probe>,
}

fn resolve_probe(
    attach: &core::Attach,
    image: &[u8],
    meta: &core::ElfMeta,
    elf_base: u64,
    segments: &[core::MapSegment],
    override_bias: Option<i64>,
) -> Result<Probe, Failure> {
    let (file_offset, symbol_vaddr, symbol_name, form) = match attach {
        core::Attach::Offset(offset) => (*offset, None, None, "offset".to_string()),
        core::Attach::Symbol(name) => {
            let (relative, vaddr) = meta
                .symbol_file_offset(image, name)
                .map_err(|error| Failure::blocked(format!("symbol {name}: {error}")))?;
            (
                elf_base + relative,
                Some(vaddr),
                Some(name.clone()),
                "symbol".to_string(),
            )
        }
    };
    if file_offset < elf_base {
        return Err(Failure::blocked(format!(
            "offset {file_offset} is below the ELF base {elf_base}"
        )));
    }
    let relative = file_offset - elf_base;
    if meta.executable_load(relative).is_none() {
        return Err(Failure::blocked(format!(
            "offset {file_offset} is not inside an executable PT_LOAD of the image"
        )));
    }
    let mut probe = Probe {
        form,
        symbol: symbol_name,
        symbol_vaddr,
        file_offset,
        va: None,
        pt_load_verified: true,
        maps_verified: false,
        segment: None,
        bias: None,
        bias_source: None,
        bias_cross_checked: 0,
        bias_warnings: Vec::new(),
        bias_anchor: None,
    };
    if segments.is_empty() {
        // No maps to prove anything against (report mode on an unmapped file).
        // An explicit bias would be unverifiable, so it is refused instead of
        // silently trusted.
        if override_bias.is_some() {
            return Err(Failure::blocked(format!(
                "offset {file_offset}: --load-bias needs /proc/<pid>/maps to verify the landing point"
            )));
        }
        return Ok(probe);
    }
    let proof = core::load_bias_for(segments, elf_base, &meta.loads, file_offset, override_bias)
        .map_err(|error| Failure::blocked(format!("offset {file_offset}: {error}")))?;
    let va = core::resolve_va(segments, elf_base, &meta.loads, proof.bias, file_offset)
        .map_err(|error| Failure::blocked(format!("offset {file_offset}: {error}")))?;
    probe.va = Some(va);
    probe.maps_verified = true;
    probe.segment = segments
        .iter()
        .find(|segment| segment.executable && va >= segment.start && va < segment.end)
        .map(|segment| {
            json!({
                "start": segment.start,
                "end": segment.end,
                "file_offset": segment.file_offset,
            })
        });
    probe.bias = Some(proof.bias);
    probe.bias_source = Some(proof.source.as_str());
    probe.bias_cross_checked = proof.cross_checked;
    probe.bias_warnings = proof.disagreements;
    probe.bias_anchor = proof.anchor_start.map(|start| {
        json!({
            "start": start,
            "vaddr": proof.anchor_vaddr,
            "file_offset": proof.anchor_file_offset,
        })
    });
    Ok(probe)
}

/// What the association ladder decided for one target.
struct Resolved {
    path: String,
    resolution: &'static str,
    dev: u64,
    inode: u64,
    assoc: core::Assoc,
    candidates: Vec<String>,
    confirmed_by: &'static str,
}

/// Resolve the path a uprobe should attach to and prove it is the file the
/// target has mapped.
///
/// The path is not the identity: a container maps guest paths (/data/app/...,
/// /system/...) while the lab holds host paths, and the loop-mounted rootfs
/// changes its device number between windows. So the ladder is
/// (1) stat as given, (2) stat through the target's own namespace
/// (--maps-root, default /proc/PID/root), (3) stat every maps path with the
/// same inode through that namespace, and then match by (dev, inode) with an
/// inode-only fallback that is reported as device drift.
fn resolve_attach_path(
    spec: &core::TargetSpec,
    pid: u32,
    maps: Option<&MapsSnapshot>,
    maps_root: Option<&str>,
) -> Result<Resolved, Failure> {
    let mut reference: Option<(String, &'static str, (u64, u64))> = None;
    if let Some(id) = stat_opt(&spec.path) {
        reference = Some((spec.path.clone(), "as_given", id));
    } else if let Some(root) = maps_root {
        let joined = format!("{root}{}", spec.path);
        if let Some(id) = stat_opt(&joined) {
            reference = Some((joined, "maps_root", id));
        }
    }
    let Some((ref_path, ref_resolution, (ref_dev, ref_inode))) = reference else {
        return Err(Failure::blocked(format!(
            "cannot stat {} as given and not through --maps-root either: the path is not visible from this namespace",
            spec.path
        )));
    };
    let mut candidates: Vec<String> = Vec::new();
    let push = |candidates: &mut Vec<String>, path: String| {
        if !candidates.contains(&path) && candidates.len() < 16 {
            candidates.push(path);
        }
    };
    push(&mut candidates, spec.path.clone());
    if let Some(root) = maps_root {
        if !spec.path.starts_with(root) {
            push(&mut candidates, format!("{root}{}", spec.path));
        }
        if let Some(maps) = maps {
            for line in &maps.lines {
                if line.inode == ref_inode && line.path.starts_with('/') {
                    push(&mut candidates, format!("{root}{}", line.path));
                }
            }
        }
    }
    let Some(maps) = maps else {
        return Ok(Resolved {
            path: ref_path,
            resolution: ref_resolution,
            dev: ref_dev,
            inode: ref_inode,
            assoc: core::Assoc {
                kind: core::AssocKind::None,
                indices: Vec::new(),
                maps_dev: None,
            },
            candidates,
            confirmed_by: "none",
        });
    };
    let mut best: Option<Resolved> = None;
    for path in &candidates {
        let Some((dev, inode)) = stat_opt(path) else { continue };
        if inode != ref_inode {
            continue;
        }
        let assoc = core::associate(&maps.lines, core::dev_parts(dev), inode);
        let resolution = if *path == spec.path {
            ref_resolution
        } else {
            "maps_root+guest_path"
        };
        if assoc.kind == core::AssocKind::Tuple {
            return Ok(Resolved {
                path: path.clone(),
                resolution,
                dev,
                inode,
                assoc,
                candidates,
                confirmed_by: "tuple",
            });
        }
        if assoc.kind == core::AssocKind::DevDrift && best.is_none() {
            let verified = assoc
                .indices
                .iter()
                .any(|index| map_files_identity(pid, &maps.lines[*index]) == Some((dev, inode)));
            let same_name = assoc.indices.first().is_some_and(|index| {
                core::file_basename(&maps.lines[*index].path) == core::file_basename(path)
            });
            best = Some(Resolved {
                path: path.clone(),
                resolution,
                dev,
                inode,
                assoc,
                candidates: candidates.clone(),
                confirmed_by: if verified {
                    "map_files"
                } else if same_name {
                    "basename"
                } else {
                    "none"
                },
            });
        }
    }
    best.ok_or_else(|| {
        let mut nearby: Vec<String> = Vec::new();
        for line in maps.lines.iter() {
            if core::file_basename(&line.path) == core::file_basename(&spec.path) && nearby.len() < 3 {
                nearby.push(format!(
                    "{} dev {} inode {}",
                    line.path,
                    core::dev_pair_text(line.dev()),
                    line.inode
                ));
            }
        }
        Failure::blocked(format!(
            "{} (dev {} inode {ref_inode}) is not mapped by pid {pid}: {} maps lines parsed, {} skipped; tried {:?}; same-name entries: {:?}; refusing to attach to an inode the target never mapped",
            spec.path,
            core::dev_text(ref_dev),
            maps.parsed,
            maps.skipped,
            candidates,
            nearby
        ))
    })
}
fn plan_target(
    spec: &core::TargetSpec,
    pid: u32,
    maps: Option<&MapsSnapshot>,
    maps_root: Option<&str>,
    require_mapping: bool,
    load_bias: Option<i64>,
) -> Result<TargetPlan, Failure> {
    // Always stat now: a device number captured by an earlier --report is a
    // different window's fact and must never be reused.
    let resolved = resolve_attach_path(spec, pid, maps, maps_root)?;
    let dev = resolved.dev;
    let inode = resolved.inode;
    let ctx = |error: String| {
        Failure::blocked(format!("{} (attach path {}): {error}", spec.path, resolved.path))
    };
    let mut segments = Vec::new();
    let mut mapped_paths: Vec<String> = Vec::new();
    let mut exec_perms = String::new();
    if let Some(maps) = maps {
        for index in &resolved.assoc.indices {
            let line = &maps.lines[*index];
            segments.push(line.segment());
            if !mapped_paths.contains(&line.path) && mapped_paths.len() < 8 {
                mapped_paths.push(line.path.clone());
            }
            if line.executable() && exec_perms.is_empty() {
                exec_perms = line.perms.clone();
            }
        }
    }
    if require_mapping {
        let Some(maps) = maps else {
            return Err(ctx("no maps snapshot, so no mapping can be proven".to_string()));
        };
        if resolved.assoc.kind == core::AssocKind::None {
            return Err(ctx(format!(
                "the target never mapped this inode ({} maps lines parsed)",
                maps.parsed
            )));
        }
        if resolved.confirmed_by == "none" {
            return Err(ctx(
                "inode matches a mapped file but neither map_files nor the basename confirms it; refusing"
                    .to_string(),
            ));
        }
        if !segments.iter().any(|segment| segment.executable) {
            return Err(ctx("mapped by the target but has no executable segment".to_string()));
        }
    }
    let (image, meta) =
        load_elf_image(&resolved.path, spec.elf_base).map_err(|error| ctx(error.message))?;
    // A uprobe can only fire in code the collector's own kernel ABI can execute,
    // so a cross-architecture library is refused at attach time. Report mode is
    // pure static analysis and only records the mismatch.
    let expected_machine = if cfg!(target_arch = "aarch64") { 183u16 } else { 62u16 };
    let arch_compatible = meta.machine == expected_machine;
    if !arch_compatible && require_mapping {
        return Err(ctx(format!(
            "is ELF machine {:#x}, this collector can only probe {expected_machine:#x}",
            meta.machine
        )));
    }
    // Each probe derives its own bias from the PT_LOAD that holds its offset;
    // --load-bias overrides that derivation for every probe of the target.
    let write = resolve_probe(
        &spec.write,
        &image,
        &meta,
        spec.elf_base,
        &segments,
        load_bias,
    )?;
    let read = match &spec.read {
        Some(attach) => Some(resolve_probe(
            attach,
            &image,
            &meta,
            spec.elf_base,
            &segments,
            load_bias,
        )?),
        None => None,
    };
    let bias = write.bias;
    let bias_source = write.bias_source;
    let bias_cross_checked = write.bias_cross_checked;
    let mut merged = write.bias_warnings.clone();
    if let Some(probe) = &read {
        merged.extend(probe.bias_warnings.iter().cloned());
    }
    let mut bias_warnings: Vec<String> = Vec::new();
    for warning in merged {
        if !bias_warnings.contains(&warning) {
            bias_warnings.push(warning);
        }
    }
    Ok(TargetPlan {
        label: spec.label.clone(),
        given_path: spec.path.clone(),
        attach_path: resolved.path,
        resolution: resolved.resolution,
        dev: core::dev_text(dev),
        inode,
        maps_dev: resolved.assoc.maps_dev.map(core::dev_pair_text),
        association: resolved.assoc.kind.as_str(),
        confirmation: resolved.confirmed_by,
        map_files_verified: resolved.confirmed_by == "map_files",
        dev_drift: resolved.assoc.kind == core::AssocKind::DevDrift,
        maps_checked: maps.is_some(),
        maps_lines: maps.map(|maps| maps.parsed).unwrap_or(0),
        maps_skipped: maps.map(|maps| maps.skipped).unwrap_or(0),
        candidates: resolved.candidates,
        elf_base: spec.elf_base,
        class64: meta.class64,
        machine: meta.machine,
        bias,
        bias_source,
        bias_cross_checked,
        bias_warnings,
        arch_compatible,
        mapped_segments: segments.len(),
        mapped_paths,
        exec_perms,
        write,
        read,
    })
}

fn probe_json(probe: &Probe) -> Value {
    json!({
        "form": probe.form,
        "symbol": probe.symbol,
        "symbol_vaddr": probe.symbol_vaddr,
        "file_offset": probe.file_offset,
        "runtime_va": probe.va,
        "pt_load_verified": probe.pt_load_verified,
        "maps_verified": probe.maps_verified,
        "maps_segment": probe.segment,
        "load_bias": probe.bias,
        "load_bias_source": probe.bias_source,
        "load_bias_cross_checked": probe.bias_cross_checked,
        "load_bias_warnings": probe.bias_warnings,
        "load_bias_anchor": probe.bias_anchor,
    })
}

fn target_json(plan: &TargetPlan) -> Value {
    json!({
        "label": plan.label,
        "path": plan.given_path,
        "attach_path": plan.attach_path,
        "resolution": plan.resolution,
        "dev": plan.dev,
        "inode": plan.inode,
        "maps_dev": plan.maps_dev,
        "association": plan.association,
        "confirmation": plan.confirmation,
        "map_files_verified": plan.map_files_verified,
        "dev_drift": plan.dev_drift,
        "maps_checked": plan.maps_checked,
        "maps_lines": plan.maps_lines,
        "maps_skipped": plan.maps_skipped,
        "candidates_tried": plan.candidates,
        "elf_base": plan.elf_base,
        "class": if plan.class64 { "ELF64" } else { "ELF32" },
        "machine": format!("{:#x}", plan.machine),
        "arch_compatible_with_collector": plan.arch_compatible,
        "load_bias": plan.bias,
        "load_bias_source": plan.bias_source,
        "load_bias_cross_checked": plan.bias_cross_checked,
        "load_bias_warnings": plan.bias_warnings,
        "mapped_segments": plan.mapped_segments,
        "mapped_paths": plan.mapped_paths,
        "executable_perms": plan.exec_perms,
        "write": probe_json(&plan.write),
        "read": plan.read.as_ref().map(probe_json),
    })
}

fn main_target_json(
    pid: u32,
    start_ticks: u64,
    uid: u32,
    gid: u32,
    nspid: &[u32],
    cgroup: Option<u64>,
    pidns: (u64, u64),
) -> Value {
    let (ns_dev, ns_ino) = pidns;
    json!({
        "pid": pid,
        "start_ticks": start_ticks,
        "uid": uid,
        "gid": gid,
        "nspid": nspid,
        "ns_tgid": nspid.last().copied(),
        "cgroup_id": cgroup,
        "pidns_dev": ns_dev,
        "pidns_ino": ns_ino,
    })
}

pub fn run(argv: &[String]) -> u8 {
    match execute(argv) {
        Ok(code) => code,
        Err(failure) => {
            if failure.blocked {
                eprintln!("tls-attach blocked: {}", failure.message);
                EXIT_BLOCKED
            } else {
                eprintln!("tls-attach failed: {}", failure.message);
                EXIT_FAIL
            }
        }
    }
}

fn execute(argv: &[String]) -> Result<u8, Failure> {
    let args = match core::AttachArgs::parse(argv) {
        Ok(args) => args,
        Err(error) => {
            // Usage problems have their own exit code.
            eprintln!("tls-attach rejected: {error}");
            eprintln!("{}", core::USAGE);
            return Ok(EXIT_USAGE);
        }
    };
    run_args(args)
}

/// Everything the kernel gate needs to recognise the target, plus the two
/// checks the operator asked for.
struct Identity {
    start_ticks: u64,
    uid: u32,
    gid: u32,
    nspid: Vec<u32>,
    ns_tgid: u32,
    cgroup: Option<u64>,
    ns_dev: u64,
    ns_ino: u64,
}

impl Identity {
    fn to_json(&self, pid: u32) -> Value {
        main_target_json(
            pid,
            self.start_ticks,
            self.uid,
            self.gid,
            &self.nspid,
            self.cgroup,
            (self.ns_dev, self.ns_ino),
        )
    }
}

fn resolve_identity(args: &core::AttachArgs) -> Result<Identity, Failure> {
    let pid = args.pid;
    let start_ticks = proc_stat_ticks(pid)?;
    if let Some(wanted) = args.start_ticks {
        if wanted != start_ticks {
            return Err(Failure::blocked(format!(
                "target start_ticks is {start_ticks}, --start-ticks says {wanted}: this is not the process that was measured"
            )));
        }
    }
    let (uid, gid, nspid) = proc_status_fields(pid)?;
    if let Some(wanted) = args.uid {
        if wanted != uid {
            return Err(Failure::blocked(format!(
                "target uid is {uid}, --uid says {wanted}"
            )));
        }
    }
    let cgroup = cgroup_inode(pid)?;
    if let Some(wanted) = args.cgroup_id {
        match cgroup {
            Some(actual) if actual == wanted => {}
            Some(actual) => {
                return Err(Failure::blocked(format!(
                    "target cgroup inode is {actual}, --cgroup-id says {wanted}"
                )))
            }
            None => {
                return Err(Failure::blocked(
                    "target has no resolvable cgroup path, --cgroup-id cannot be honoured",
                ))
            }
        }
    }
    let ns_tgid = nspid
        .last()
        .copied()
        .ok_or_else(|| Failure::blocked("target status has no NSpid line"))?;
    let (ns_dev, ns_ino) = ns_ids(pid)?;
    Ok(Identity {
        start_ticks,
        uid,
        gid,
        nspid,
        ns_tgid,
        cgroup,
        ns_dev,
        ns_ino,
    })
}

fn run_args(args: core::AttachArgs) -> Result<u8, Failure> {
    let pid = args.pid;
    let now = boottime_ns()?;
    // Report mode is pure static analysis, so an unreadable /proc (another
    // user's namespace link, or no target at all) must not stop it: the failure
    // is recorded in the record instead.
    let identity = resolve_identity(&args);
    // Maps are read in both modes so a --report never claims "not mapped" just
    // because it was not looking; only an attach treats an unreadable snapshot
    // as fatal.
    let maps_root_owned = args.maps_root.resolve(pid);
    let maps_root = maps_root_owned.as_deref();
    let maps_result = read_maps(pid);
    let (maps, maps_note) = match maps_result {
        Ok(snapshot) => (Some(snapshot), None),
        Err(error) => (None, Some(error.message)),
    };
    if !args.report_only {
        if let Some(note) = &maps_note {
            return Err(Failure::blocked(format!(
                "cannot read the target's maps, so no mapping can be proven: {note}"
            )));
        }
    }
    let require_mapping = !args.report_only;
    let mut plans = Vec::new();
    for spec in &args.targets {
        plans.push(plan_target(
            spec,
            pid,
            maps.as_ref(),
            maps_root,
            require_mapping,
            args.load_bias,
        )?);
    }
    if args.report_only {
        let target_summary = match &identity {
            Ok(identity) => identity.to_json(pid),
            Err(error) => serde_json::json!({
                "available": false,
                "error": error.message,
            }),
        };
        let report = serde_json::json!({
            "record": "report",
            "schema": 1,
            "mode": "tls-attach",
            "object": OBJECT_NAME,
            "object_bytes": OBJECT.len(),
            "kernel": kernel_string(),
            "boot_id": boot_id(),
            "clock_ticks_per_sec": clock_ticks().ok(),
            "at_boot_ns": now,
            "target": target_summary,
            "targets": plans.iter().map(target_json).collect::<Vec<_>>(),
            "maps_root": maps_root,
            "maps_checked": maps.is_some(),
            "maps_lines": maps.as_ref().map(|maps| maps.parsed),
            "maps_skipped": maps.as_ref().map(|maps| maps.skipped),
            "maps_error": maps_note,
            "attached": false,
            "note": "report mode resolves the ELF, the maps association and the symbol offsets; nothing is loaded",
        });
        println!("{report}");
        return Ok(EXIT_PASS);
    }
    let identity = identity?;
    let ticks = clock_ticks()?;
    let labels = args.labels();
    let event_cap = core::EVENT_CAP;
    let deadline = now + args.duration_s * 1_000_000_000;
    let mut bpf = Ebpf::load(OBJECT).map_err(|error| {
        Failure::blocked(format!("Ebpf::load({OBJECT_NAME}): {error:?}"))
    })?;
    {
        let mut config = Array::<_, u64>::try_from(
            bpf.map_mut("CONFIG")
                .ok_or_else(|| Failure::failed("CONFIG map missing"))?,
        )
        .map_err(|error| Failure::failed(format!("CONFIG map: {error}")))?;
        let values: [(u32, u64); 10] = [
            (core::CFG_TGID, u64::from(identity.ns_tgid)),
            (core::CFG_START, identity.start_ticks),
            (core::CFG_HZ, ticks),
            (core::CFG_DEADLINE, deadline),
            (core::CFG_UID, u64::from(identity.uid)),
            (core::CFG_MAX_EVENTS, event_cap),
            (core::CFG_SAMPLE_CAP, core::SAMPLE_CAP as u64),
            (core::CFG_NS_DEV, identity.ns_dev),
            (core::CFG_NS_INO, identity.ns_ino),
            (core::CFG_CGROUP, identity.cgroup.unwrap_or(0)),
        ];
        for (index, value) in values {
            config
                .set(index, value, 0)
                .map_err(|error| Failure::failed(format!("CONFIG set {index}: {error}")))?;
        }
    }
    let mut ids = Vec::new();
    for (name, _slot, _write, _enter) in PROGRAMS {
        let program: &mut UProbe = bpf
            .program_mut(name)
            .ok_or_else(|| Failure::failed(format!("program {name} missing")))?
            .try_into()
            .map_err(|_| Failure::failed(format!("program {name} is not a uprobe")))?;
        program
            .load()
            .map_err(|error| Failure::blocked(format!("load {name}: {error:?}")))?;
        ids.push(
            program
                .info()
                .map_err(|error| Failure::failed(format!("info {name}: {error}")))?
                .id(),
        );
    }
    let mut attached = 0usize;
    let mut slot_attached = [false; core::MAX_TARGETS];
    let mut attach_errors: Vec<String> = Vec::new();
    for (name, slot, write, _enter) in PROGRAMS {
        let slot = slot as usize;
        let Some(plan) = plans.get(slot) else {
            continue;
        };
        let probe = if write { Some(&plan.write) } else { plan.read.as_ref() };
        let Some(probe) = probe else {
            continue;
        };
        let program: &mut UProbe = bpf
            .program_mut(name)
            .and_then(|program| program.try_into().ok())
            .ok_or_else(|| Failure::failed(format!("program {name} is not a uprobe")))?;
        match program.attach(None, probe.file_offset, Path::new(&plan.attach_path), None) {
            Ok(_) => {
                attached += 1;
                slot_attached[slot] = true;
            }
            Err(error) => {
                attach_errors.push(format!("{name} -> {}: {error:?}", plan.label));
            }
        }
    }
    if attached == 0 {
        return Err(Failure::blocked(format!(
            "no probe could be attached: {}",
            attach_errors.join("; ")
        )));
    }
    let mut ring = RingBuf::try_from(
        bpf.take_map("EVENTS")
            .ok_or_else(|| Failure::failed("EVENTS map missing"))?,
    )
    .map_err(|error| Failure::failed(format!("EVENTS ring: {error}")))?;

    let mut out = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&args.out)
        .map_err(|error| Failure::blocked(format!("create {}: {error}", args.out)))?;
    // SAFETY: mode is written before the descriptor is used for data.
    let mut writer = BufWriter::new(&mut out);
    let mut pairer = core::Pairer::new();
    let mut decode_errors = 0u64;
    let mut decoded = 0u64;
    let mut written = 0u64;
    let mut calls = 0u64;
    let mut unpaired_calls = 0u64;
    let mut truncated_calls = 0u64;
    let mut len_invalid_calls = 0u64;
    let mut read_failed_calls = 0u64;
    let mut payload_bytes = 0u64;
    let mut first_sequence: Option<u64> = None;
    let mut last_sequence: Option<u64> = None;
    let mut gaps = 0u64;
    let mut inversions = 0u64;
    let mut per_library: Vec<Value> = Vec::new();
    let mut http_requests = 0u64;
    let mut http_responses = 0u64;
    let mut http_other = 0u64;
    let mut hosts: Vec<String> = Vec::new();
    let mut methods: Vec<String> = Vec::new();
    let mut paths: Vec<String> = Vec::new();
    let mut statuses: Vec<u16> = Vec::new();
    let mut library_stats: Vec<(u64, u64, u64)> = vec![(0, 0, 0); core::MAX_TARGETS];

    let started = Instant::now();
    let mut quiet = 0u32;
    loop {
        let mut taken = 0usize;
        while let Some(bytes) = ring.next() {
            match core::decode(&bytes) {
                Ok(record) => {
                    decoded += 1;
                    match (first_sequence, last_sequence) {
                        (None, _) => first_sequence = Some(record.sequence),
                        (Some(_), Some(previous)) => {
                            if record.sequence <= previous {
                                inversions += 1;
                            } else if record.sequence != previous + 1 {
                                gaps += 1;
                            }
                        }
                        _ => {}
                    }
                    last_sequence = Some(record.sequence);
                    match pairer.admit(record) {
                        Ok(Some(call)) => {
                            emit_call(
                                &mut writer,
                                &call,
                                &labels,
                                &mut library_stats,
                                &mut http_requests,
                                &mut http_responses,
                                &mut http_other,
                                &mut hosts,
                                &mut methods,
                                &mut paths,
                                &mut statuses,
                            )?;
                            calls += 1;
                            written += 1;
                            payload_bytes += call.valid_bytes();
                            if call.truncated() {
                                truncated_calls += 1;
                            }
                            if call.len_invalid() {
                                len_invalid_calls += 1;
                            }
                            if call.read_failed() {
                                read_failed_calls += 1;
                            }
                        }
                        Ok(None) => {}
                        Err(_) => {
                            decode_errors += 1;
                        }
                    }
                }
                Err(_) => decode_errors += 1,
            }
            taken += 1;
        }
        if taken == 0 {
            quiet += 1;
        } else {
            quiet = 0;
        }
        if boottime_ns()? >= deadline || (written >= event_cap && quiet >= 8) {
            break;
        }
        if started.elapsed() > Duration::from_secs(args.duration_s + 30) {
            break;
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    let counts: Vec<u64> = {
        let counters = Array::<_, u64>::try_from(
            bpf.map("COUNTERS")
                .ok_or_else(|| Failure::failed("COUNTERS map missing"))?,
        )
        .map_err(|error| Failure::failed(format!("COUNTERS map: {error}")))?;
        (0..core::COUNTER_SLOTS)
            .map(|index| counters.get(&index, 0).unwrap_or(0))
            .collect()
    };
    // Detach everything before the final drain so the ring is the only source
    // left, then prove no probe survived.
    drop(bpf);
    let mut after_detach = 0usize;
    while let Some(bytes) = ring.next() {
        after_detach += 1;
        match core::decode(&bytes) {
            Ok(record) => {
                decoded += 1;
                last_sequence = Some(record.sequence);
                if let Ok(Some(call)) = pairer.admit(record) {
                    emit_call(
                        &mut writer,
                        &call,
                        &labels,
                        &mut library_stats,
                        &mut http_requests,
                        &mut http_responses,
                        &mut http_other,
                        &mut hosts,
                        &mut methods,
                        &mut paths,
                        &mut statuses,
                    )?;
                    calls += 1;
                    written += 1;
                    payload_bytes += call.valid_bytes();
                    if call.truncated() {
                        truncated_calls += 1;
                    }
                    if call.len_invalid() {
                        len_invalid_calls += 1;
                    }
                    if call.read_failed() {
                        read_failed_calls += 1;
                    }
                }
            }
            Err(_) => decode_errors += 1,
        }
    }
    for call in pairer.finish() {
        emit_call(
            &mut writer,
            &call,
            &labels,
            &mut library_stats,
            &mut http_requests,
            &mut http_responses,
            &mut http_other,
            &mut hosts,
            &mut methods,
            &mut paths,
            &mut statuses,
        )?;
        calls += 1;
        written += 1;
        unpaired_calls += 1;
    }
    let program_ids_present = ids.iter().any(|id| *id != 0);
    let ids_absent = program_ids_absent(&ids, Duration::from_secs(3));
    let submitted = counts[core::CNT_SUBMITTED as usize];
    let lost = counts[core::CNT_RESERVE_FAILED as usize];
    let empty_session = calls == 0;
    let mut failures = Vec::new();
    if decode_errors > 0 {
        failures.push(format!("decode_errors={decode_errors}"));
    }
    if gaps > 0 {
        failures.push(format!("sequence_gaps={gaps}"));
    }
    if inversions > 0 {
        failures.push(format!("sequence_inversions={inversions}"));
    }
    if pairer.unpaired_exits > 0 {
        failures.push(format!("unpaired_exits={}", pairer.unpaired_exits));
    }
    if pairer.duplicate_entries > 0 || pairer.duplicate_exits > 0 {
        failures.push(format!(
            "duplicate_records={}/{}",
            pairer.duplicate_entries, pairer.duplicate_exits
        ));
    }
    if unpaired_calls > 0 {
        failures.push(format!("calls_without_return={unpaired_calls}"));
    }
    if written > event_cap {
        failures.push(format!("events_written={written} exceeds cap {event_cap}"));
    }
    if !ids_absent {
        failures.push("program_ids_still_loaded".to_string());
    }
    if decoded + lost < submitted {
        failures.push(format!(
            "ring_not_fully_accounted: decoded {decoded} + lost {lost} < submitted {submitted}"
        ));
    }
    // A target that exits inside the window is normal; what must not happen is the
    // same pid coming back with a different identity while the gate would still
    // accept its events.
    let ticks_now = proc_stat_ticks(pid);
    let target_gone = ticks_now.is_err();
    let identity_stable = if target_gone {
        true
    } else {
        let (uid_now, _, nspid_now) = proc_status_fields(pid).unwrap_or((0, 0, Vec::new()));
        uid_now == identity.uid
            && ticks_now.ok() == Some(identity.start_ticks)
            && nspid_now.last().copied() == Some(identity.ns_tgid)
    };
    if !identity_stable {
        failures.push("target_identity_changed_during_session".to_string());
    }
    let passed = failures.is_empty();

    for (index, plan) in plans.iter().enumerate() {
        let (library_calls, library_writes, library_reads) = library_stats[index];
        per_library.push(json!({
            "label": plan.label,
            "path": plan.attach_path,
            "inode": plan.inode,
            "dev": plan.dev,
            "write": probe_json(&plan.write),
            "read": plan.read.as_ref().map(probe_json),
            "attached": slot_attached[index],
            "calls": library_calls,
            "writes": library_writes,
            "reads": library_reads,
        }));
    }
    let mut counter_json = serde_json::Map::new();
    for (index, name) in core::COUNTER_NAMES.iter().enumerate() {
        counter_json.insert((*name).to_string(), json!(counts[index]));
    }
    let session = json!({
        "record": "session",
        "schema": 1,
        "mode": "tls-attach",
        "object": OBJECT_NAME,
        "object_bytes": OBJECT.len(),
        "object_fnv1a64": format!("{:016x}", fnv1a64(OBJECT)),
        "kernel": kernel_string(),
        "boot_id": boot_id(),
        "euid": effective_uid(),
        "clock_ticks_per_sec": ticks,
        "started_at_boot_ns": now,
        "deadline_boot_ns": deadline,
        "duration_s": args.duration_s,
        "ring_bytes": core::RING_BYTES,
        "record_bytes": core::RECORD_BYTES,
        "sample_cap": core::SAMPLE_CAP,
        "event_cap": event_cap,
        "pid_scope": "all_processes_filtered_in_bpf",
        "maps_root": maps_root,
        "maps_lines": maps.as_ref().map(|maps| maps.parsed),
        "maps_skipped": maps.as_ref().map(|maps| maps.skipped),
        "identity_filter": {
            "ns_tgid": identity.ns_tgid,
            "start_ticks": identity.start_ticks,
            "uid": identity.uid,
            "cgroup_id": identity.cgroup,
        },
        "target": identity.to_json(pid),
        "targets": plans.iter().map(target_json).collect::<Vec<_>>(),
        "program_ids": ids,
        "payload_bytes_emitted": false,
        "writes_user_memory": false,
        "tracefs_touched": false,
    });
    writeln!(writer, "{session}").map_err(|error| Failure::failed(format!("write: {error}")))?;
    let closeout = json!({
        "record": "closeout",
        "schema": 1,
        "passed": passed,
        "failures": failures,
        "counts": Value::Object(counter_json),
        "decoded": decoded,
        "decode_errors": decode_errors,
        "calls": calls,
        "events_written": written,
        "unpaired_calls": unpaired_calls,
        "unpaired_exits": pairer.unpaired_exits,
        "duplicate_entries": pairer.duplicate_entries,
        "duplicate_exits": pairer.duplicate_exits,
        "truncated_calls": truncated_calls,
        "len_invalid_calls": len_invalid_calls,
        "read_failed_calls": read_failed_calls,
        "payload_bytes": payload_bytes,
        "empty_session": empty_session,
        "ring_drained_after_detach": after_detach,
        "ring_fully_accounted": decoded + lost >= submitted,
        "first_sequence": first_sequence,
        "last_sequence": last_sequence,
        "sequence_gaps": gaps,
        "inversions": inversions,
        "event_cap": event_cap,
        "event_capped_by_bpf": counts[core::CNT_EVENT_CAPPED as usize],
        "identity_rejected_by_bpf": counts[core::CNT_IDENTITY_REJECTED as usize],
        "identity_stable": identity_stable,
        "target_gone_during_session": target_gone,
        "program_ids_present": program_ids_present,
        "program_ids_absent_after_detach": ids_absent,
        "attach_errors": attach_errors,
        "attached_probes": attached,
        "per_library": per_library,
        "http": {
            "requests": http_requests,
            "responses": http_responses,
            "other": http_other,
            "hosts": hosts,
            "methods": methods,
            "paths": paths,
            "statuses": statuses,
        },
    });
    writeln!(writer, "{closeout}").map_err(|error| Failure::failed(format!("write: {error}")))?;
    writer
        .flush()
        .map_err(|error| Failure::failed(format!("flush: {error}")))?;
    drop(writer);
    out.sync_all()
        .map_err(|error| Failure::failed(format!("sync: {error}")))?;
    if passed {
        Ok(EXIT_PASS)
    } else {
        eprintln!("tls-attach closeout failed: {}", json!(failures));
        Ok(EXIT_FAIL)
    }
}

#[allow(clippy::too_many_arguments)]
fn emit_call(
    writer: &mut BufWriter<&mut File>,
    call: &core::Call,
    labels: &[String],
    library_stats: &mut [(u64, u64, u64)],
    http_requests: &mut u64,
    http_responses: &mut u64,
    http_other: &mut u64,
    hosts: &mut Vec<String>,
    methods: &mut Vec<String>,
    paths: &mut Vec<String>,
    statuses: &mut Vec<u16>,
) -> Result<(), Failure> {
    let label = labels
        .get(call.tag as usize)
        .cloned()
        .unwrap_or_default();
    let http = if call.has_plaintext() {
        let facts = core::http_facts(&call.payload[..call.valid_bytes() as usize]);
        match &facts {
            core::HttpFacts::Request {
                method,
                path,
                host,
                ..
            } => {
                *http_requests += 1;
                if let Some(host) = host {
                    if !hosts.contains(host) && hosts.len() < 16 {
                        hosts.push(host.clone());
                    }
                }
                if !methods.contains(method) && methods.len() < 16 {
                    methods.push(method.clone());
                }
                if !paths.contains(path) && paths.len() < 16 {
                    paths.push(path.clone());
                }
            }
            core::HttpFacts::Response { status, .. } => {
                *http_responses += 1;
                if !statuses.contains(status) && statuses.len() < 16 {
                    statuses.push(*status);
                }
            }
            core::HttpFacts::Other { .. } => *http_other += 1,
        }
        Some(facts.to_json())
    } else {
        None
    };
    if let Some(stats) = library_stats.get_mut(call.tag as usize) {
        stats.0 += 1;
        if call.direction == core::DIR_WRITE {
            stats.1 += 1;
        } else {
            stats.2 += 1;
        }
    }
    let record = json!({
        "record": "call",
        "library": label,
        "call_id": call.call_id,
        "direction": if call.direction == core::DIR_WRITE { "write" } else { "read" },
        "tid": call.tid,
        "tgid": call.tgid,
        "ns_tid": (call.ns_pid_tid & 0xffff_ffff) as u32,
        "ns_tgid": (call.ns_pid_tid >> 32) as u32,
        "comm": call.comm,
        "ssl": format!("ssl-{}", call.ssl_id),
        "declared": call.declared,
        "result": call.result,
        "captured": call.captured,
        "valid_bytes": call.valid_bytes(),
        "truncated": call.truncated(),
        "len_invalid": call.len_invalid(),
        "read_failed": call.read_failed(),
        "unpaired": call.unpaired(),
        "depth": call.depth,
        "enter_ts_ns": call.entry_ts_ns,
        "exit_ts_ns": call.exit_ts_ns,
        "cgroup_id": call.cgroup_id,
        "uid": call.uid,
        "cpu": call.cpu,
        "payload_fnv1a64": format!("{:016x}", call.payload_fnv1a64()),
        "payload_bytes_emitted": false,
        "http": http,
    });
    writeln!(writer, "{record}").map_err(|error| Failure::failed(format!("write: {error}")))
}

fn program_ids_absent(ids: &[u32], wait: Duration) -> bool {
    let deadline = Instant::now() + wait;
    loop {
        let live: Vec<u32> = aya::programs::loaded_programs()
            .filter_map(Result::ok)
            .map(|info| info.id())
            .collect();
        if ids.iter().all(|id| !live.contains(id)) {
            return true;
        }
        if Instant::now() >= deadline {
            return false;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}
