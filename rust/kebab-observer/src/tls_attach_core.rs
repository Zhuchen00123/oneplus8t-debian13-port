// SPDX-License-Identifier: Apache-2.0
//! Portable half of the external TLS attach tool: wire decoding, entry/return
//! pairing, HTTP structure extraction with redaction, target-spec parsing and
//! the ELF PT_LOAD / load-bias arithmetic used to prove that a file offset is
//! not an ASLR address.
//!
//! Everything here is pure and unit-tested on Windows; the Linux runtime in
//! tls_attach only adds probes, /proc reads and file access.

use serde_json::{json, Value};

/// Fixed record header size; ABI with bpf/tls_attach.bpf.c.
pub const HEADER_BYTES: usize = 144;
/// Bounded sample cap per call (the task's 4 KiB bound).
pub const SAMPLE_CAP: usize = 4096;
/// One ring-buffer record.
pub const RECORD_BYTES: usize = HEADER_BYTES + SAMPLE_CAP;
/// Ring buffer size: 1 MiB.
pub const RING_BYTES: u32 = 1 << 20;
/// Hard per-session event cap.
pub const EVENT_CAP: u64 = 20_000;
/// Session duration bound, seconds.
pub const DURATION_MAX_S: u64 = 120;
/// How many library slots the object carries.
pub const MAX_TARGETS: usize = 2;
/// Longest accepted label.
pub const LABEL_MAX: usize = 48;

/// CONFIG array indices; ABI with the C object.
pub const CFG_TGID: u32 = 0;
pub const CFG_START: u32 = 1;
pub const CFG_HZ: u32 = 2;
pub const CFG_DEADLINE: u32 = 3;
pub const CFG_UID: u32 = 4;
pub const CFG_MAX_EVENTS: u32 = 5;
pub const CFG_SAMPLE_CAP: u32 = 6;
pub const CFG_NS_DEV: u32 = 7;
pub const CFG_NS_INO: u32 = 8;
pub const CFG_CGROUP: u32 = 9;
/// Number of CONFIG slots the object declares.
pub const CONFIG_SLOTS: u32 = 32;
/// Number of COUNTERS slots.
pub const COUNTER_SLOTS: u32 = 16;

pub const CNT_ENTER_WRITE: u32 = 0;
pub const CNT_EXIT_WRITE: u32 = 1;
pub const CNT_ENTER_READ: u32 = 2;
pub const CNT_EXIT_READ: u32 = 3;
pub const CNT_SUBMITTED: u32 = 4;
pub const CNT_RESERVE_FAILED: u32 = 5;
pub const CNT_IDENTITY_REJECTED: u32 = 6;
pub const CNT_MAP_FAILED: u32 = 7;
pub const CNT_EVENT_CAPPED: u32 = 8;
pub const CNT_DEPTH_OVERFLOW: u32 = 9;
pub const CNT_USER_READ: u32 = 10;
pub const CNT_USER_READ_BYTES: u32 = 11;
pub const CNT_USER_READ_FAILED: u32 = 12;
pub const CNT_LEN_INVALID: u32 = 13;
pub const CNT_TRUNCATED: u32 = 14;
pub const CNT_UNPAIRED: u32 = 15;

/// Counter names in slot order.
pub const COUNTER_NAMES: [&str; COUNTER_SLOTS as usize] = [
    "enter_write",
    "exit_write",
    "enter_read",
    "exit_read",
    "submitted",
    "reserve_failed",
    "identity_rejected",
    "map_failed",
    "event_capped",
    "depth_overflow",
    "user_read",
    "user_read_bytes",
    "user_read_failed",
    "len_invalid",
    "truncated",
    "unpaired",
];

pub const KIND_ENTER: u32 = 1;
pub const KIND_EXIT: u32 = 2;
pub const DIR_WRITE: u32 = 1;
pub const DIR_READ: u32 = 2;

pub const FLAG_TRUNCATED: u32 = 0x1;
pub const FLAG_LEN_INVALID: u32 = 0x2;
pub const FLAG_READ_FAILED: u32 = 0x4;
pub const FLAG_USER_READ: u32 = 0x8;
pub const FLAG_DEPTH_OVERFLOW: u32 = 0x10;
pub const FLAG_UNPAIRED: u32 = 0x20;
pub const FLAG_PAYLOAD_ON_EXIT: u32 = 0x40;

/// Deepest nesting the object tracks.
pub const MAX_DEPTH: u32 = 8;
/// Uid value that disables the uid condition.
pub const UID_ANY: u32 = 0xffff_ffff;

/// One decoded record header plus the valid prefix of its sample area.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WireCall {
    pub ts_ns: u64,
    pub sequence: u64,
    pub call_id: u64,
    pub ssl: u64,
    pub start_ns: u64,
    pub cgroup_id: u64,
    pub ns_pid_tid: u64,
    pub declared: i64,
    pub result: i64,
    pub buf: u64,
    pub captured: u32,
    pub kind: u32,
    pub flags: u32,
    pub cpu: u32,
    pub uid: u32,
    pub depth: u32,
    pub direction: u32,
    pub tag: u32,
    pub tid: u32,
    pub tgid: u32,
    pub comm: [u8; 16],
    pub payload: Vec<u8>,
}

/// Why a ring record was refused. None of the variants echo payload bytes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DecodeError {
    Length(usize),
    Kind(u32),
    Direction(u32),
    Tag(u32),
    Captured(u32),
    Depth(u32),
    Reserved(u32),
}

impl std::fmt::Display for DecodeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DecodeError::Length(n) => write!(f, "record length {n} is not {RECORD_BYTES}"),
            DecodeError::Kind(k) => write!(f, "unknown record kind {k}"),
            DecodeError::Direction(d) => write!(f, "unknown direction {d}"),
            DecodeError::Tag(t) => write!(f, "library tag {t} exceeds {MAX_TARGETS}"),
            DecodeError::Captured(c) => write!(f, "captured {c} exceeds {SAMPLE_CAP}"),
            DecodeError::Depth(d) => write!(f, "depth {d} exceeds {MAX_DEPTH}"),
            DecodeError::Reserved(p) => write!(f, "reserved bytes {p} are not zero"),
        }
    }
}

fn u32_at(bytes: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes(bytes[offset..offset + 4].try_into().unwrap())
}

fn u64_at(bytes: &[u8], offset: usize) -> u64 {
    u64::from_le_bytes(bytes[offset..offset + 8].try_into().unwrap())
}

/// Decode one ring record. Strict: any field outside its contract is refused
/// rather than interpreted.
pub fn decode(bytes: &[u8]) -> Result<WireCall, DecodeError> {
    if bytes.len() != RECORD_BYTES {
        return Err(DecodeError::Length(bytes.len()));
    }
    let kind = u32_at(bytes, 84);
    if kind != KIND_ENTER && kind != KIND_EXIT {
        return Err(DecodeError::Kind(kind));
    }
    let direction = u32_at(bytes, 104);
    if direction != DIR_WRITE && direction != DIR_READ {
        return Err(DecodeError::Direction(direction));
    }
    let tag = u32_at(bytes, 108);
    if tag as usize >= MAX_TARGETS {
        return Err(DecodeError::Tag(tag));
    }
    let captured = u32_at(bytes, 80);
    if captured as usize > SAMPLE_CAP {
        return Err(DecodeError::Captured(captured));
    }
    let depth = u32_at(bytes, 100);
    if depth > MAX_DEPTH {
        return Err(DecodeError::Depth(depth));
    }
    if bytes[136..HEADER_BYTES].iter().any(|byte| *byte != 0) {
        return Err(DecodeError::Reserved(1));
    }
    let mut comm = [0u8; 16];
    comm.copy_from_slice(&bytes[120..136]);
    Ok(WireCall {
        ts_ns: u64_at(bytes, 0),
        sequence: u64_at(bytes, 8),
        call_id: u64_at(bytes, 16),
        ssl: u64_at(bytes, 24),
        start_ns: u64_at(bytes, 32),
        cgroup_id: u64_at(bytes, 40),
        ns_pid_tid: u64_at(bytes, 48),
        declared: u64_at(bytes, 56) as i64,
        result: u64_at(bytes, 64) as i64,
        buf: u64_at(bytes, 72),
        captured,
        kind,
        flags: u32_at(bytes, 88),
        cpu: u32_at(bytes, 92),
        uid: u32_at(bytes, 96),
        depth,
        direction,
        tag,
        tid: u32_at(bytes, 112),
        tgid: u32_at(bytes, 116),
        comm,
        payload: bytes[HEADER_BYTES..HEADER_BYTES + captured as usize].to_vec(),
    })
}

impl WireCall {
    pub fn is_enter(&self) -> bool {
        self.kind == KIND_ENTER
    }
    pub fn direction_name(&self) -> &'static str {
        if self.direction == DIR_WRITE { "write" } else { "read" }
    }
    pub fn comm_text(&self) -> String {
        let end = self.comm.iter().position(|b| *b == 0).unwrap_or(self.comm.len());
        self.comm[..end]
            .iter()
            .map(|b| if (0x20..0x7f).contains(b) { *b as char } else { '?' })
            .collect()
    }
}

/// One call reconstructed from its entry and (optional) return record.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Call {
    pub call_id: u64,
    pub tag: u32,
    pub direction: u32,
    pub tid: u32,
    pub tgid: u32,
    pub ns_pid_tid: u64,
    pub ssl_id: u32,
    pub comm: String,
    pub declared: i64,
    pub result: Option<i64>,
    pub captured: u32,
    pub flags: u32,
    pub depth: u32,
    pub entry_sequence: u64,
    pub entry_ts_ns: u64,
    pub exit_ts_ns: Option<u64>,
    pub payload: Vec<u8>,
    pub cgroup_id: u64,
    pub uid: u32,
    pub cpu: u32,
}

impl Call {
    pub fn truncated(&self) -> bool {
        self.flags & FLAG_TRUNCATED != 0
    }
    pub fn len_invalid(&self) -> bool {
        self.flags & FLAG_LEN_INVALID != 0
    }
    pub fn read_failed(&self) -> bool {
        self.flags & FLAG_READ_FAILED != 0
    }
    pub fn unpaired(&self) -> bool {
        self.flags & FLAG_UNPAIRED != 0
    }
    /// Bytes of this call the peer actually transferred: the return value
    /// bounds a read, a write is bounded by what the probe sampled.
    pub fn valid_bytes(&self) -> u64 {
        match (self.direction, self.result) {
            (DIR_READ, Some(ret)) if ret > 0 => (ret as u64).min(u64::from(self.captured)),
            (DIR_READ, _) => 0,
            _ => u64::from(self.captured),
        }
    }
    /// Non-reversible fingerprint of the sampled bytes.
    pub fn payload_fnv1a64(&self) -> u64 {
        let mut hash = 0xcbf2_9ce4_8422_2325u64;
        for byte in &self.payload {
            hash ^= u64::from(*byte);
            hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
        }
        hash
    }
    /// Whether the call carries plaintext worth scanning for HTTP structure.
    pub fn has_plaintext(&self) -> bool {
        self.valid_bytes() > 0 && !self.payload.is_empty()
    }
}

/// Reasons a paired call is refused.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PairError {
    /// A return record arrived with no matching entry.
    UnpairedExit,
    /// Two entry records carried the same call id.
    DuplicateEntry(u64),
    /// A return record arrived twice for one call.
    DuplicateExit(u64),
}

/// Pairs entry and return records by the BPF-assigned call id.
///
/// Reentrancy needs no special handling here: the id is assigned per call and
/// the depth travels with it, so nested calls pair independently. What the
/// pairer does police is the pathological cases - an exit without an entry, a
/// duplicate id, a duplicate exit - because those would otherwise be silently
/// counted as successful captures.
#[derive(Default)]
pub struct Pairer {
    pending: std::collections::BTreeMap<u64, WireCall>,
    exit_seen: std::collections::BTreeSet<u64>,
    aliases: Vec<u64>,
    pub unpaired_exits: u64,
    pub duplicate_entries: u64,
    pub duplicate_exits: u64,
}

impl Pairer {
    pub fn new() -> Self {
        Self::default()
    }

    /// Number of calls currently waiting for their return record.
    pub fn depth(&self) -> usize {
        self.pending.len()
    }

    /// Run-local opaque id for a raw SSL pointer: a small counter, never the
    /// pointer and never an encoding of it.
    fn alias(&mut self, ssl: u64) -> u32 {
        if ssl == 0 {
            return 0;
        }
        if let Some(index) = self.aliases.iter().position(|value| *value == ssl) {
            return index as u32 + 1;
        }
        self.aliases.push(ssl);
        self.aliases.len() as u32
    }

    /// Feed one record; returns a call when this record completes it.
    pub fn admit(&mut self, record: WireCall) -> Result<Option<Call>, PairError> {
        if record.is_enter() {
            if self.pending.contains_key(&record.call_id) {
                self.duplicate_entries += 1;
                return Err(PairError::DuplicateEntry(record.call_id));
            }
            // The raw pointer stays private to the pairer: the alias is
            // assigned at completion time and never leaves this run.
            self.pending.insert(record.call_id, record);
            return Ok(None);
        }
        if self.exit_seen.contains(&record.call_id) {
            self.duplicate_exits += 1;
            return Err(PairError::DuplicateExit(record.call_id));
        }
        let Some(entry) = self.pending.remove(&record.call_id) else {
            self.unpaired_exits += 1;
            return Err(PairError::UnpairedExit);
        };
        self.exit_seen.insert(record.call_id);
        let flags = entry.flags | record.flags;
        let payload = if entry.direction == DIR_READ {
            record.payload.clone()
        } else {
            entry.payload.clone()
        };
        let captured = if entry.direction == DIR_READ {
            record.captured
        } else {
            entry.captured
        };
        Ok(Some(Call {
            call_id: entry.call_id,
            tag: entry.tag,
            direction: entry.direction,
            tid: entry.tid,
            tgid: entry.tgid,
            ns_pid_tid: entry.ns_pid_tid,
            ssl_id: self.alias(entry.ssl),
            comm: entry.comm_text(),
            declared: entry.declared,
            result: Some(record.result),
            captured,
            flags,
            depth: entry.depth,
            entry_sequence: entry.sequence,
            entry_ts_ns: entry.ts_ns,
            exit_ts_ns: Some(record.ts_ns),
            payload,
            cgroup_id: entry.cgroup_id,
            uid: entry.uid,
            cpu: entry.cpu,
        }))
    }

    /// Calls whose return record never arrived, as unpaired rows.
    pub fn finish(&mut self) -> Vec<Call> {
        let pending = std::mem::take(&mut self.pending);
        pending
            .into_values()
            .map(|entry| Call {
                call_id: entry.call_id,
                tag: entry.tag,
                direction: entry.direction,
                tid: entry.tid,
                tgid: entry.tgid,
                ns_pid_tid: entry.ns_pid_tid,
                ssl_id: 0,
                comm: entry.comm_text(),
                declared: entry.declared,
                result: None,
                captured: entry.captured,
                flags: entry.flags | FLAG_UNPAIRED,
                depth: entry.depth,
                entry_sequence: entry.sequence,
                entry_ts_ns: entry.ts_ns,
                exit_ts_ns: None,
                payload: entry.payload,
                cgroup_id: entry.cgroup_id,
                uid: entry.uid,
                cpu: entry.cpu,
            })
            .collect()
    }
}

/// Classify a query parameter value without keeping it.
fn param_type(value: &[u8]) -> &'static str {
    if value.is_empty() {
        return "empty";
    }
    if value.len() > 64 {
        return "long";
    }
    let Ok(text) = std::str::from_utf8(value) else {
        return "binary";
    };
    if text.chars().all(|c| c.is_ascii_digit()) {
        return "num";
    }
    if text.len() >= 16 && text.chars().all(|c| c.is_ascii_hexdigit()) {
        return "hex";
    }
    let dashes = text.matches('-').count();
    if text.len() == 36 && dashes == 4 && text.chars().all(|c| c.is_ascii_hexdigit() || c == '-') {
        return "uuid";
    }
    if text.matches('.').count() == 2 && text.len() > 40 {
        return "jwt";
    }
    if text.eq_ignore_ascii_case("true") || text.eq_ignore_ascii_case("false") {
        return "bool";
    }
    "text"
}

/// Normalise a path: keep its shape, drop entity ids.
///
/// /api/v1/users/12345/runs becomes /api/v1/users/{id}/runs; long hex, uuid
/// and numeric segments are replaced, everything else is kept verbatim.
pub fn normalize_path(path: &str) -> String {
    let mut out = String::with_capacity(path.len() + 1);
    if path.starts_with('/') {
        out.push('/');
    }
    let segments: Vec<&str> = path
        .split('/')
        .filter(|segment| !segment.is_empty())
        .map(|segment| {
            let numeric = segment.chars().all(|c| c.is_ascii_digit());
            let hexish = segment.len() >= 16 && segment.chars().all(|c| c.is_ascii_hexdigit());
            let uuidish = segment.len() == 36 && segment.matches('-').count() == 4;
            if numeric || hexish || uuidish {
                "{id}"
            } else {
                segment
            }
        })
        .collect();
    out.push_str(&segments.join("/"));
    if out.is_empty() {
        out.push('/');
    }
    out
}

fn header_lookup(block: &str, name: &str) -> Option<String> {
    for line in block.split("\r\n") {
        let Some((key, value)) = line.split_once(':') else {
            continue;
        };
        if key.trim().eq_ignore_ascii_case(name) {
            let value = value.trim();
            if value.is_empty() || value.len() > 256 || !value.is_ascii() {
                return None;
            }
            return Some(value.to_string());
        }
    }
    None
}

fn header_names(block: &str) -> Vec<String> {
    let mut names = Vec::new();
    for line in block.split("\r\n") {
        let Some((key, _)) = line.split_once(':') else {
            continue;
        };
        let key = key.trim();
        if key.is_empty() || key.len() > 64 || !key.is_ascii() {
            continue;
        }
        let lower = key.to_ascii_lowercase();
        if names.len() < 32 && !names.contains(&lower) {
            names.push(lower);
        }
    }
    names
}

/// Split bytes into head and body at the first CRLFCRLF.
fn split_head(bytes: &[u8]) -> (&[u8], &[u8]) {
    if let Some(position) = bytes.windows(4).position(|window| window == b"\r\n\r\n") {
        (&bytes[..position], &bytes[position + 4..])
    } else {
        (bytes, &[])
    }
}

/// What a captured plaintext looked like, after redaction.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HttpFacts {
    /// A request line plus header names and query parameter names only.
    Request {
        method: String,
        path: String,
        host: Option<String>,
        scheme: Option<&'static str>,
        header_names: Vec<String>,
        params: Vec<(String, &'static str)>,
        body_bytes: u64,
        content_type: Option<String>,
    },
    /// A status line plus content metadata.
    Response {
        status: u16,
        content_type: Option<String>,
        content_length: Option<u64>,
        header_names: Vec<String>,
    },
    /// Bytes that do not start with an HTTP request or status line.
    Other { first_bytes_ascii: bool },
}

/// Extract the desensitised HTTP shape of a captured plaintext.
///
/// Header values are never returned except for Host, Content-Type and
/// Content-Length; query parameter values are reduced to a coarse type; the
/// payload bytes never leave this function.
pub fn http_facts(bytes: &[u8]) -> HttpFacts {
    let (head, body) = split_head(bytes);
    let head_text = String::from_utf8_lossy(head);
    let first = head_text.split("\r\n").next().unwrap_or("");
    let header_block = &head_text[first.len().min(head_text.len())..];
    if let Some(rest) = first.strip_prefix("HTTP/1.") {
        let mut parts = rest.splitn(2, ' ');
        let _minor = parts.next().unwrap_or("");
        let status = parts
            .next()
            .unwrap_or("")
            .split(' ')
            .next()
            .unwrap_or("")
            .parse::<u16>()
            .ok();
        return match status {
            Some(status) => HttpFacts::Response {
                status,
                content_type: header_lookup(header_block, "content-type"),
                content_length: header_lookup(header_block, "content-length")
                    .and_then(|value| value.parse::<u64>().ok()),
                header_names: header_names(header_block),
            },
            None => HttpFacts::Other {
                first_bytes_ascii: first.is_ascii(),
            },
        };
    }
    let fields: Vec<&str> = first.split(' ').collect();
    if fields.len() != 3 || !fields[2].starts_with("HTTP/1.") {
        return HttpFacts::Other {
            first_bytes_ascii: first.is_ascii(),
        };
    }
    let method = fields[0];
    if method.is_empty()
        || method.len() > 12
        || !method.chars().all(|c| c.is_ascii_uppercase() || c == '-')
    {
        return HttpFacts::Other {
            first_bytes_ascii: first.is_ascii(),
        };
    }
    let target = fields[1];
    let scheme = if target.starts_with("https://") {
        Some("https")
    } else if target.starts_with("http://") {
        Some("http")
    } else {
        None
    };
    let (host_from_target, target) = match scheme {
        Some(_) => match target.split_once("://") {
            Some((_, rest)) => match rest.split_once('/') {
                Some((host, path)) => (Some(host.to_string()), format!("/{path}")),
                None => (Some(rest.to_string()), "/".to_string()),
            },
            None => (None, target.to_string()),
        },
        None => (None, target.to_string()),
    };
    let (path_part, query_part) = match target.split_once('?') {
        Some((path, query)) => (path, Some(query)),
        None => (target.as_str(), None),
    };
    let mut params = Vec::new();
    if let Some(query) = query_part {
        for pair in query.split('&').take(32) {
            let (name, value) = match pair.split_once('=') {
                Some((name, value)) => (name, value.as_bytes()),
                None => (pair, &[][..]),
            };
            if name.is_empty() || name.len() > 48 || !name.is_ascii() {
                continue;
            }
            params.push((name.to_ascii_lowercase(), param_type(value)));
        }
    }
    let host = header_lookup(header_block, "host").or(host_from_target);
    HttpFacts::Request {
        method: method.to_string(),
        path: normalize_path(path_part),
        host: host.map(|value| {
            let mut host = value.to_ascii_lowercase();
            if let Some((name, port)) = host.rsplit_once(':') {
                if port.chars().all(|c| c.is_ascii_digit()) {
                    host = name.to_string();
                }
            }
            host
        }),
        scheme,
        header_names: header_names(header_block),
        params,
        body_bytes: body.len() as u64,
        content_type: header_lookup(header_block, "content-type"),
    }
}

impl HttpFacts {
    /// JSON form; guaranteed to contain no payload bytes.
    pub fn to_json(&self) -> Value {
        match self {
            HttpFacts::Request {
                method,
                path,
                host,
                scheme,
                header_names,
                params,
                body_bytes,
                content_type,
            } => json!({
                "kind": "request",
                "method": method,
                "scheme": scheme,
                "host": host,
                "path": path,
                "header_names": header_names,
                "params": params
                    .iter()
                    .map(|(name, kind)| json!({"name": name, "type": kind}))
                    .collect::<Vec<_>>(),
                "content_type": content_type,
                "body_bytes": body_bytes,
            }),
            HttpFacts::Response {
                status,
                content_type,
                content_length,
                header_names,
            } => json!({
                "kind": "response",
                "status": status,
                "content_type": content_type,
                "content_length": content_length,
                "header_names": header_names,
            }),
            HttpFacts::Other { first_bytes_ascii } => json!({
                "kind": "other",
                "first_bytes_ascii": first_bytes_ascii,
            }),
        }
    }
}

/// How one probe target was named.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Attach {
    /// Absolute file offset inside the target file.
    Offset(u64),
    /// Symbol resolved from the ELF at the elf base.
    Symbol(String),
}

impl Attach {
    pub fn describe(&self) -> String {
        match self {
            Attach::Offset(offset) => format!("offset:{offset}"),
            Attach::Symbol(name) => format!("symbol:{name}"),
        }
    }
    pub fn symbol(&self) -> Option<&str> {
        match self {
            Attach::Symbol(name) => Some(name),
            Attach::Offset(_) => None,
        }
    }
    pub fn offset(&self) -> Option<u64> {
        match self {
            Attach::Offset(offset) => Some(*offset),
            Attach::Symbol(_) => None,
        }
    }
}

/// One --target specification.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TargetSpec {
    /// Free-form label that travels with every event of this slot.
    pub label: String,
    /// Absolute path of the mapped file (an APK or a plain .so).
    pub path: String,
    /// Offset of the ELF image inside the file; 0 for a plain .so.
    pub elf_base: u64,
    /// Write-direction attach: an absolute file offset or a symbol name.
    pub write: Attach,
    /// Optional read-direction attach.
    pub read: Option<Attach>,
}

impl TargetSpec {
    /// Parse LABEL=PATH[@ELF_BASE]:WRITE[:READ].
    pub fn parse(spec: &str) -> Result<Self, String> {
        let (label, rest) = spec
            .split_once('=')
            .ok_or_else(|| format!("target '{spec}' is not LABEL=PATH[@ELF_BASE]:WRITE[:READ]"))?;
        if label.is_empty() || label.len() > LABEL_MAX || !label.is_ascii() {
            return Err(format!("target label '{label}' must be 1..={LABEL_MAX} ASCII bytes"));
        }
        if label.contains(':') || label.contains('=') || label.contains('/') {
            return Err(format!("target label '{label}' must not contain ':', '=' or '/'"));
        }
        let fields: Vec<&str> = rest.split(':').collect();
        if fields.len() < 2 || fields.len() > 3 {
            return Err(format!(
                "target '{label}' needs PATH:WRITE[:READ], got {} field(s)",
                fields.len()
            ));
        }
        let (path, elf_base) = match fields[0].split_once('@') {
            Some((path, base)) => (
                path,
                base.parse::<u64>()
                    .map_err(|_| format!("target '{label}' elf base '{base}' is not a number"))?,
            ),
            None => (fields[0], 0),
        };
        if !path.starts_with('/') {
            return Err(format!("target '{label}' path '{path}' must be absolute"));
        }
        if path.len() > 4096 || path.contains('\n') {
            return Err(format!("target '{label}' path is not a usable path"));
        }
        let parse_attach = |text: &str| -> Result<Attach, String> {
            if text.is_empty() {
                return Err(format!("target '{label}' has an empty attach"));
            }
            if text.bytes().all(|b| b.is_ascii_digit()) {
                return text
                    .parse::<u64>()
                    .map(Attach::Offset)
                    .map_err(|_| format!("target '{label}' offset '{text}' does not fit u64"));
            }
            if text.len() > 128
                || !text
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_@$+-.".contains(&b))
            {
                return Err(format!("target '{label}' symbol '{text}' is not a symbol name"));
            }
            Ok(Attach::Symbol(text.to_string()))
        };
        let write = parse_attach(fields[1])?;
        let read = match fields.get(2) {
            Some(text) => Some(parse_attach(text)?),
            None => None,
        };
        Ok(TargetSpec {
            label: label.to_string(),
            path: path.to_string(),
            elf_base,
            write,
            read,
        })
    }
}

/// Parsed command line of the tls-attach subcommand.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AttachArgs {
    pub pid: u32,
    pub start_ticks: Option<u64>,
    pub uid: Option<u32>,
    pub cgroup_id: Option<u64>,
    pub duration_s: u64,
    pub out: String,
    pub targets: Vec<TargetSpec>,
    /// Resolve and print only; never load a program.
    pub report_only: bool,
    /// Prefix that maps a path into the target's own mount namespace. Defaults
    /// to /proc/PID/root; 'off' (or an empty value) disables the mapping.
    pub maps_root: MapsRoot,
    /// Operator-supplied load bias.  When set, the per-segment derivation is
    /// skipped, but the resolved address is still validated against PT_LOAD and
    /// the target's executable mappings.
    pub load_bias: Option<i64>,
}

/// --maps-root selector: keep "not given" distinguishable from "disabled".
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub enum MapsRoot {
    #[default]
    Default,
    Off,
    Path(String),
}

impl MapsRoot {
    pub fn parse(text: &str) -> Self {
        if text == "off" || text == "none" || text.is_empty() {
            MapsRoot::Off
        } else {
            MapsRoot::Path(text.to_string())
        }
    }

    pub fn resolve(&self, pid: u32) -> Option<String> {
        match self {
            MapsRoot::Default => Some(format!("/proc/{pid}/root")),
            MapsRoot::Off => None,
            MapsRoot::Path(path) => Some(path.clone()),
        }
    }
}

/// Usage line, shared with the runtime error path.
pub const USAGE: &str = "usage: kebab-observer tls-attach --pid PID --duration 1..=120 --out NEW_JSONL_FILE --target LABEL=PATH[@ELF_BASE]:WRITE[:READ] [--target ...] [--start-ticks TICKS] [--uid UID] [--cgroup-id ID] [--maps-root DIR|off] [--load-bias N] [--report]";

impl AttachArgs {
    /// Parse the subcommand arguments.
    pub fn parse(argv: &[String]) -> Result<Self, String> {
        let mut pid: Option<u32> = None;
        let mut start_ticks: Option<u64> = None;
        let mut uid: Option<u32> = None;
        let mut cgroup_id: Option<u64> = None;
        let mut duration_s: Option<u64> = None;
        let mut load_bias: Option<i64> = None;
        let mut out: Option<String> = None;
        let mut targets: Vec<TargetSpec> = Vec::new();
        let mut report_only = false;
        let mut maps_root: Option<MapsRoot> = None;
        let mut index = 0;
        while index < argv.len() {
            let flag = argv[index].as_str();
            let take = |slot: usize| -> Result<String, String> {
                argv.get(slot + 1)
                    .cloned()
                    .ok_or_else(|| format!("{flag} needs a value"))
            };
            match flag {
                "--pid" => {
                    let text = take(index)?;
                    index += 1;
                    let parsed = text
                        .parse::<u32>()
                        .map_err(|_| format!("--pid '{text}' is not a pid"))?;
                    if parsed == 0 {
                        return Err("--pid must be non-zero".to_string());
                    }
                    if pid.replace(parsed).is_some() {
                        return Err("--pid given twice".to_string());
                    }
                }
                "--start-ticks" => {
                    let text = take(index)?;
                    index += 1;
                    let parsed = text
                        .parse::<u64>()
                        .map_err(|_| format!("--start-ticks '{text}' is not a number"))?;
                    if parsed == 0 {
                        return Err("--start-ticks must be non-zero".to_string());
                    }
                    if start_ticks.replace(parsed).is_some() {
                        return Err("--start-ticks given twice".to_string());
                    }
                }
                "--uid" => {
                    let text = take(index)?;
                    index += 1;
                    let parsed = text
                        .parse::<u32>()
                        .map_err(|_| format!("--uid '{text}' is not a uid"))?;
                    if parsed == UID_ANY {
                        return Err(format!("--uid {UID_ANY} is reserved for 'any'"));
                    }
                    if uid.replace(parsed).is_some() {
                        return Err("--uid given twice".to_string());
                    }
                }
                "--cgroup-id" => {
                    let text = take(index)?;
                    index += 1;
                    let parsed = text
                        .parse::<u64>()
                        .map_err(|_| format!("--cgroup-id '{text}' is not a number"))?;
                    if parsed == 0 {
                        return Err("--cgroup-id must be non-zero".to_string());
                    }
                    if cgroup_id.replace(parsed).is_some() {
                        return Err("--cgroup-id given twice".to_string());
                    }
                }
                "--duration" => {
                    let text = take(index)?;
                    index += 1;
                    let parsed = text
                        .parse::<u64>()
                        .map_err(|_| format!("--duration '{text}' is not a number"))?;
                    if !(1..=DURATION_MAX_S).contains(&parsed) {
                        return Err(format!("--duration must be 1..={DURATION_MAX_S}"));
                    }
                    if duration_s.replace(parsed).is_some() {
                        return Err("--duration given twice".to_string());
                    }
                }
                "--out" => {
                    let text = take(index)?;
                    index += 1;
                    if text.is_empty() {
                        return Err("--out must not be empty".to_string());
                    }
                    if out.replace(text).is_some() {
                        return Err("--out given twice".to_string());
                    }
                }
                "--target" => {
                    let text = take(index)?;
                    index += 1;
                    if targets.len() >= MAX_TARGETS {
                        return Err(format!("at most {MAX_TARGETS} --target values are supported"));
                    }
                    let spec = TargetSpec::parse(&text)?;
                    if targets.iter().any(|existing| existing.label == spec.label) {
                        return Err(format!("target label '{}' given twice", spec.label));
                    }
                    targets.push(spec);
                }
                "--maps-root" => {
                    let text = take(index)?;
                    index += 1;
                    if maps_root.replace(MapsRoot::parse(&text)).is_some() {
                        return Err("--maps-root given twice".to_string());
                    }
                }
                "--load-bias" => {
                    let text = take(index)?;
                    index += 1;
                    if load_bias.replace(text.parse::<i64>().map_err(|_| {
                        format!("--load-bias '{text}' is not a signed 64-bit number")
                    })?)
                    .is_some()
                    {
                        return Err("--load-bias given twice".to_string());
                    }
                }
                "--report" => report_only = true,
                other => return Err(format!("unknown argument '{other}'")),
            }
            index += 1;
        }
        let pid = pid.ok_or_else(|| "missing --pid".to_string())?;
        // Report mode loads nothing and writes nothing, so the session knobs are
        // optional there and required everywhere else.
        let duration_s = if report_only {
            duration_s.unwrap_or(1)
        } else {
            duration_s.ok_or_else(|| "missing --duration".to_string())?
        };
        let out = if report_only {
            out.unwrap_or_default()
        } else {
            out.ok_or_else(|| "missing --out".to_string())?
        };
        if targets.is_empty() {
            return Err("at least one --target is required".to_string());
        }
        Ok(AttachArgs {
            pid,
            start_ticks,
            uid,
            cgroup_id,
            duration_s,
            out,
            targets,
            report_only,
            maps_root: maps_root.unwrap_or_default(),
            load_bias,
        })
    }

    /// Labels in slot order, padded to MAX_TARGETS.
    pub fn labels(&self) -> Vec<String> {
        let mut labels: Vec<String> = self.targets.iter().map(|target| target.label.clone()).collect();
        while labels.len() < MAX_TARGETS {
            labels.push(String::new());
        }
        labels
    }
}

/// One ELF PT_LOAD segment.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PtLoad {
    pub offset: u64,
    pub vaddr: u64,
    pub filesz: u64,
    pub memsz: u64,
    pub flags: u32,
}

/// A parsed ELF header plus its PT_LOAD segments.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ElfMeta {
    pub class64: bool,
    pub little_endian: bool,
    pub machine: u16,
    pub elf_type: u16,
    pub loads: Vec<PtLoad>,
    pub sections: Vec<Section>,
    /// End of the program-header and section-header tables inside the image.
    pub table_end: u64,
}

impl ElfMeta {
    /// Executable PT_LOAD containing a file offset relative to the ELF image.
    pub fn executable_load(&self, relative: u64) -> Option<&PtLoad> {
        self.loads.iter().find(|load| {
            load.flags & 1 != 0 && relative >= load.offset && relative < load.offset + load.filesz
        })
    }
}

/// Parse the ELF header and program headers out of an image that may be a whole
/// plain .so or an embedded image inside a container (APK).
pub fn parse_elf(image: &[u8]) -> Result<ElfMeta, String> {
    if image.len() < 64 || &image[..4] != b"\x7fELF" {
        return Err("not an ELF image (magic mismatch)".to_string());
    }
    let class64 = match image[4] {
        2 => true,
        1 => return Err("ELF32 images are not supported by this tool".to_string()),
        other => return Err(format!("unknown ELF class {other}")),
    };
    let little_endian = match image[5] {
        1 => true,
        2 => false,
        other => return Err(format!("unknown ELF data encoding {other}")),
    };
    let reader = |offset: usize, size: usize| -> Result<u64, String> {
        if offset + size > image.len() {
            return Err(format!(
                "ELF field at {offset} is outside the {}-byte image",
                image.len()
            ));
        }
        let slice = &image[offset..offset + size];
        Ok(if little_endian {
            match size {
                2 => u16::from_le_bytes(slice.try_into().unwrap()) as u64,
                4 => u32::from_le_bytes(slice.try_into().unwrap()) as u64,
                _ => u64::from_le_bytes(slice.try_into().unwrap()),
            }
        } else {
            match size {
                2 => u16::from_be_bytes(slice.try_into().unwrap()) as u64,
                4 => u32::from_be_bytes(slice.try_into().unwrap()) as u64,
                _ => u64::from_be_bytes(slice.try_into().unwrap()),
            }
        })
    };
    let elf_type = reader(16, 2)? as u16;
    let machine = reader(18, 2)? as u16;
    let phoff = reader(32, if class64 { 8 } else { 4 })?;
    let phentsize = reader(if class64 { 54 } else { 42 }, 2)? as usize;
    let phnum = reader(if class64 { 56 } else { 44 }, 2)? as usize;
    if phnum == 0 || phnum > 64 {
        return Err(format!("ELF declares {phnum} program headers"));
    }
    let want = if class64 { 56 } else { 32 };
    if phentsize < want {
        return Err(format!("program header entry size {phentsize} is below {want}"));
    }
    let mut loads = Vec::new();
    for index in 0..phnum {
        let base = phoff as usize + index * phentsize;
        if reader(base, 4)? != 1 {
            continue;
        }
        let load = if class64 {
            PtLoad {
                flags: reader(base + 4, 4)? as u32,
                offset: reader(base + 8, 8)?,
                vaddr: reader(base + 16, 8)?,
                filesz: reader(base + 32, 8)?,
                memsz: reader(base + 40, 8)?,
            }
        } else {
            PtLoad {
                offset: reader(base + 4, 4)?,
                vaddr: reader(base + 8, 4)?,
                filesz: reader(base + 16, 4)?,
                memsz: reader(base + 20, 4)?,
                flags: reader(base + 24, 4)? as u32,
            }
        };
        loads.push(load);
    }
    if loads.is_empty() {
        return Err("ELF has no PT_LOAD segment".to_string());
    }
    let (shoff, shentsize, shnum) = header_sections(&reader)?;
    let mut sections = Vec::new();
    let mut table_end = phoff + phnum as u64 * phentsize as u64;
    if shoff != 0 && shnum != 0 {
        let want_section = if class64 { 64 } else { 40 };
        if shentsize < want_section || shnum > 4096 {
            return Err(format!(
                "section table shape is not usable: {shnum} entries of {shentsize} bytes"
            ));
        }
        table_end = table_end.max(shoff + shnum * shentsize);
        for index in 0..shnum {
            let base = (shoff + index * shentsize) as usize;
            let section = if class64 {
                Section {
                    sh_type: reader(base + 4, 4)? as u32,
                    addr: reader(base + 16, 8)?,
                    offset: reader(base + 24, 8)?,
                    size: reader(base + 32, 8)?,
                    link: reader(base + 40, 4)? as u32,
                    entsize: reader(base + 56, 8)?,
                }
            } else {
                Section {
                    sh_type: reader(base + 4, 4)? as u32,
                    addr: reader(base + 12, 4)?,
                    offset: reader(base + 16, 4)?,
                    size: reader(base + 20, 4)?,
                    link: reader(base + 24, 4)? as u32,
                    entsize: reader(base + 36, 4)?,
                }
            };
            sections.push(section);
        }
    }
    Ok(ElfMeta {
        class64,
        little_endian,
        machine,
        elf_type,
        loads,
        sections,
        table_end,
    })
}

/// How many bytes must be readable from the image start for the header, the
/// program-header table and the section-header table.
///
/// The runtime uses this to read an APK-hosted image without loading the whole
/// APK: section headers live at the end of the .so, so the window has to be
/// derived from the header instead of guessed.
pub fn header_window(image: &[u8]) -> Result<u64, String> {
    if image.len() < 64 {
        return Err(format!("only {} bytes available for the ELF header", image.len()));
    }
    if &image[..4] != b"ELF" {
        return Err("not an ELF image (magic mismatch)".to_string());
    }
    let class64 = match image[4] {
        2 => true,
        1 => return Err("ELF32 images are not supported by this tool".to_string()),
        other => return Err(format!("unknown ELF class {other}")),
    };
    let little_endian = image[5] == 1;
    let reader = |offset: usize, size: usize| -> Result<u64, String> {
        if offset + size > image.len() {
            return Err(format!("ELF field at {offset} is outside the image window"));
        }
        let slice = &image[offset..offset + size];
        Ok(if little_endian {
            match size {
                2 => u16::from_le_bytes(slice.try_into().unwrap()) as u64,
                4 => u32::from_le_bytes(slice.try_into().unwrap()) as u64,
                _ => u64::from_le_bytes(slice.try_into().unwrap()),
            }
        } else {
            match size {
                2 => u16::from_be_bytes(slice.try_into().unwrap()) as u64,
                4 => u32::from_be_bytes(slice.try_into().unwrap()) as u64,
                _ => u64::from_be_bytes(slice.try_into().unwrap()),
            }
        })
    };
    let phoff = reader(32, if class64 { 8 } else { 4 })?;
    let phentsize = reader(if class64 { 54 } else { 42 }, 2)?;
    let phnum = reader(if class64 { 56 } else { 44 }, 2)?;
    let (shoff, shentsize, shnum) = header_sections(&reader)?;
    let mut need = phoff + phnum * phentsize;
    if shoff != 0 && shnum != 0 {
        need = need.max(shoff + shnum * shentsize);
    }
    Ok(need)
}

fn header_sections(
    reader: &impl Fn(usize, usize) -> Result<u64, String>,
) -> Result<(u64, u64, u64), String> {
    Ok((reader(40, 8)?, reader(58, 2)?, reader(60, 2)?))
}

/// One ELF section header, kept only for dynamic-symbol resolution.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Section {
    pub sh_type: u32,
    pub addr: u64,
    pub offset: u64,
    pub size: u64,
    pub link: u32,
    pub entsize: u64,
}

/// Section type of the dynamic symbol table.
pub const SHT_DYNSYM: u32 = 11;

impl ElfMeta {
    /// File offset and virtual address of an exported dynamic symbol.
    ///
    /// st_value is a virtual address; for a shared object it has to be
    /// translated through the same PT_LOAD arithmetic the loader used, and that
    /// translation is exactly what turns the value into the file offset the
    /// kernel's uprobe takes.
    pub fn symbol_file_offset(&self, image: &[u8], name: &str) -> Result<(u64, u64), String> {
        let dynsym = self
            .sections
            .iter()
            .find(|section| section.sh_type == SHT_DYNSYM)
            .ok_or_else(|| "ELF has no SHT_DYNSYM section".to_string())?;
        let strtab = self
            .sections
            .get(dynsym.link as usize)
            .ok_or_else(|| format!("dynsym links to missing section {}", dynsym.link))?;
        let entry_size = if self.class64 {
            dynsym.entsize.max(24)
        } else {
            dynsym.entsize.max(16)
        };
        let count = dynsym.size / entry_size;
        for index in 0..count {
            let base = (dynsym.offset + index * entry_size) as usize;
            if base as u64 + entry_size > image.len() as u64 {
                return Err(format!("dynsym entry {index} is outside the image window"));
            }
            let name_offset = u32::from_le_bytes(image[base..base + 4].try_into().unwrap()) as u64;
            if name_offset == 0 {
                continue;
            }
            let Some(candidate) = read_cstr(image, strtab.offset + name_offset) else {
                continue;
            };
            if candidate != name {
                continue;
            }
            let value = if self.class64 {
                u64::from_le_bytes(image[base + 8..base + 16].try_into().unwrap())
            } else {
                u32::from_le_bytes(image[base + 4..base + 8].try_into().unwrap()) as u64
            };
            let file_offset = self
                .vaddr_to_file_offset(value)
                .ok_or_else(|| format!("symbol {name} at {value:#x} is outside every PT_LOAD"))?;
            return Ok((file_offset, value));
        }
        Err(format!("symbol {name} is not exported by the ELF image"))
    }

    /// Translate a virtual address to a file offset through the PT_LOAD table.
    pub fn vaddr_to_file_offset(&self, address: u64) -> Option<u64> {
        self.loads.iter().find_map(|load| {
            if address >= load.vaddr && address < load.vaddr + load.filesz {
                Some(load.offset + (address - load.vaddr))
            } else {
                None
            }
        })
    }

    /// Bytes that must be readable from the image start to walk the section
    /// table, the dynamic symbol table and its string table.
    pub fn section_window(&self) -> u64 {
        self.sections
            .iter()
            .map(|section| section.offset.saturating_add(section.size))
            .chain(std::iter::once(self.table_end))
            .max()
            .unwrap_or(0)
    }
}

fn read_cstr(image: &[u8], offset: u64) -> Option<&str> {
    let start = usize::try_from(offset).ok()?;
    if start >= image.len() {
        return None;
    }
    let rest = &image[start..];
    let end = rest.iter().position(|byte| *byte == 0)?;
    std::str::from_utf8(&rest[..end]).ok()
}

/// One /proc/<pid>/maps line that maps the target inode.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MapSegment {
    pub start: u64,
    pub end: u64,
    pub file_offset: u64,
    pub executable: bool,
}

/// glibc's major/minor split of a raw st_dev, so the numbers compare with the
/// hex tokens /proc/<pid>/maps prints.
pub fn dev_parts(dev: u64) -> (u64, u64) {
    let major = ((dev >> 8) & 0xfff) | ((dev >> 32) & !0xfff);
    let minor = (dev & 0xff) | ((dev >> 12) & !0xff);
    (major, minor)
}

pub fn dev_text(dev: u64) -> String {
    let (major, minor) = dev_parts(dev);
    format!("{major:02x}:{minor:02x}")
}

pub fn dev_pair_text(pair: (u64, u64)) -> String {
    format!("{:02x}:{:02x}", pair.0, pair.1)
}

pub fn file_basename(path: &str) -> &str {
    match path.rsplit_once('/') {
        Some((_, name)) => name,
        None => path,
    }
}

/// A parsed /proc/<pid>/maps line.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MapsLine {
    pub start: u64,
    pub end: u64,
    pub file_offset: u64,
    pub dev_major: u64,
    pub dev_minor: u64,
    pub inode: u64,
    pub perms: String,
    pub path: String,
}

impl MapsLine {
    pub fn executable(&self) -> bool {
        self.perms.contains('x')
    }

    pub fn dev(&self) -> (u64, u64) {
        (self.dev_major, self.dev_minor)
    }

    pub fn segment(&self) -> MapSegment {
        MapSegment {
            start: self.start,
            end: self.end,
            file_offset: self.file_offset,
            executable: self.executable(),
        }
    }

    pub fn range(&self) -> String {
        format!("{:x}-{:x}", self.start, self.end)
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct MapsParse {
    pub lines: Vec<MapsLine>,
    pub skipped: usize,
}

pub fn parse_maps(text: &str) -> MapsParse {
    let mut parsed = MapsParse::default();
    for line in text.lines() {
        if line.trim().is_empty() {
            continue;
        }
        let mut parts = line.splitn(6, ' ');
        let (Some(range), Some(perms), Some(offset), Some(dev), Some(inode)) = (
            parts.next(),
            parts.next(),
            parts.next(),
            parts.next(),
            parts.next(),
        ) else {
            parsed.skipped += 1;
            continue;
        };
        let path = parts.next().unwrap_or("").trim().to_string();
        let (Some((start, end)), Some(offset), Some((major, minor)), Some(inode)) = (
            range.split_once('-'),
            parse_hex(offset),
            dev.split_once(':'),
            parse_dec(inode),
        ) else {
            parsed.skipped += 1;
            continue;
        };
        let (Some(start), Some(end), Some(dev_major), Some(dev_minor)) = (
            parse_hex(start),
            parse_hex(end),
            parse_hex(major),
            parse_hex(minor),
        ) else {
            parsed.skipped += 1;
            continue;
        };
        parsed.lines.push(MapsLine {
            start,
            end,
            file_offset: offset,
            dev_major,
            dev_minor,
            inode,
            perms: perms.to_string(),
            path,
        });
    }
    parsed
}

fn parse_hex(text: &str) -> Option<u64> {
    u64::from_str_radix(text, 16).ok()
}

fn parse_dec(text: &str) -> Option<u64> {
    text.parse::<u64>().ok()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AssocKind {
    /// (st_dev major:minor, st_ino) matched: the strong proof.
    Tuple,
    /// The inode matched but st_dev did not. Two mount instances of the same
    /// filesystem (a loop image mounted twice) report the same inode under
    /// different device numbers, so this is reported as drift, not as a match.
    DevDrift,
    None,
}

impl AssocKind {
    pub fn as_str(self) -> &'static str {
        match self {
            AssocKind::Tuple => "tuple",
            AssocKind::DevDrift => "inode_dev_drift",
            AssocKind::None => "none",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Assoc {
    pub kind: AssocKind,
    pub indices: Vec<usize>,
    pub maps_dev: Option<(u64, u64)>,
}

/// Associate a file identity with the maps lines that map it.
///
/// Primary is the (dev, inode) tuple. When only the inode matches the result is
/// DevDrift and the caller decides whether the rest of the proof (basename,
/// map_files confirmation) is strong enough to proceed.
pub fn associate(lines: &[MapsLine], dev: (u64, u64), inode: u64) -> Assoc {
    if inode == 0 {
        return Assoc {
            kind: AssocKind::None,
            indices: Vec::new(),
            maps_dev: None,
        };
    }
    let tuple: Vec<usize> = lines
        .iter()
        .enumerate()
        .filter(|(_, line)| line.inode == inode && line.dev() == dev)
        .map(|(index, _)| index)
        .collect();
    if !tuple.is_empty() {
        return Assoc {
            kind: AssocKind::Tuple,
            indices: tuple,
            maps_dev: Some(dev),
        };
    }
    let drift: Vec<usize> = lines
        .iter()
        .enumerate()
        .filter(|(_, line)| line.inode == inode)
        .map(|(index, _)| index)
        .collect();
    if drift.is_empty() {
        return Assoc {
            kind: AssocKind::None,
            indices: Vec::new(),
            maps_dev: None,
        };
    }
    Assoc {
        kind: AssocKind::DevDrift,
        maps_dev: Some(lines[drift[0]].dev()),
        indices: drift,
    }
}

const PAGE: u64 = 4096;

fn align_down(value: u64, page: u64) -> u64 {
    value & !(page - 1)
}

/// Where the load bias came from.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BiasSource {
    /// Derived from the one PT_LOAD that holds the probe offset.
    Segment,
    /// Handed in by the operator through --load-bias.
    Override,
}

impl BiasSource {
    pub fn as_str(self) -> &'static str {
        match self {
            BiasSource::Segment => "segment",
            BiasSource::Override => "override",
        }
    }
}

/// Proof that a file offset can be turned into a runtime address.
///
/// The bias is derived from the single PT_LOAD that holds the probe offset, not
/// from every segment: an ELF may give its segments different
/// (p_vaddr - p_offset) values (a static executable usually does, because each
/// segment is aligned on its own), and requiring them all to agree rejected
/// legitimate attach targets.  The other segments are still cross-checked, but
/// a disagreement is a warning that goes into the session record instead of a
/// refusal.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BiasProof {
    /// Runtime address minus link-time address.
    pub bias: i64,
    pub source: BiasSource,
    /// Start of the mapped segment the bias was derived from, when one exists.
    pub anchor_start: Option<u64>,
    /// Link-time address and file offset of that same segment.
    pub anchor_vaddr: u64,
    pub anchor_file_offset: u64,
    /// How many mapped segments were cross-checked against the anchor.
    pub cross_checked: usize,
    /// Segments whose own derivation differs from the anchor's.
    pub disagreements: Vec<String>,
}

impl BiasProof {
    pub fn agrees(&self) -> bool {
        self.disagreements.is_empty()
    }
}

/// Derive (or take) the load bias that belongs to one probe file offset.
///
/// `file_offset` decides which PT_LOAD is the anchor, so two probes in different
/// segments each get their own bias.  With `override_bias` the derivation is
/// skipped entirely; the caller still validates the resulting runtime address
/// against PT_LOAD and the executable mappings (`resolve_va`), so an override
/// can not place a probe outside the target's code.
pub fn load_bias_for(
    segments: &[MapSegment],
    elf_base: u64,
    loads: &[PtLoad],
    file_offset: u64,
    override_bias: Option<i64>,
) -> Result<BiasProof, String> {
    if loads.is_empty() {
        return Err("the image has no PT_LOAD segment".to_string());
    }
    let relative = file_offset
        .checked_sub(elf_base)
        .ok_or_else(|| format!("offset {file_offset} is below the ELF base {elf_base}"))?;
    let anchor_load = loads
        .iter()
        .find(|load| relative >= load.offset && relative < load.offset.saturating_add(load.filesz))
        .ok_or_else(|| format!("offset {file_offset} falls outside every PT_LOAD segment"))?;
    let anchor_page = align_down(elf_base.saturating_add(anchor_load.offset), PAGE);
    let anchor_segment = segments.iter().find(|segment| segment.file_offset == anchor_page);

    if let Some(bias) = override_bias {
        return Ok(BiasProof {
            bias,
            source: BiasSource::Override,
            anchor_start: anchor_segment.map(|segment| segment.start),
            anchor_vaddr: anchor_load.vaddr,
            anchor_file_offset: anchor_page,
            cross_checked: 0,
            disagreements: Vec::new(),
        });
    }

    let segment = anchor_segment.ok_or_else(|| {
        format!(
            "no /proc/<pid>/maps segment matches the PT_LOAD at {:#x} (file offset {anchor_page:#x}) that holds offset {file_offset}",
            anchor_load.vaddr
        )
    })?;
    let bias = segment.start as i64 - align_down(anchor_load.vaddr, PAGE) as i64;

    let mut disagreements = Vec::new();
    let mut cross_checked = 0usize;
    for other in segments {
        let Some(load) = loads.iter().find(|load| {
            align_down(elf_base.saturating_add(load.offset), PAGE) == other.file_offset
        }) else {
            continue;
        };
        if load.offset == anchor_load.offset {
            continue;
        }
        cross_checked += 1;
        let expected = other.start as i64 - align_down(load.vaddr, PAGE) as i64;
        if expected != bias {
            disagreements.push(format!(
                "segment at {:#x} (PT_LOAD vaddr {:#x}) implies {expected:#x}, anchor implies {bias:#x}",
                other.start, load.vaddr
            ));
        }
    }

    Ok(BiasProof {
        bias,
        source: BiasSource::Segment,
        anchor_start: Some(segment.start),
        anchor_vaddr: anchor_load.vaddr,
        anchor_file_offset: anchor_page,
        cross_checked,
        disagreements,
    })
}

/// Runtime address of a file offset, validated against PT_LOAD and maps.
pub fn resolve_va(
    segments: &[MapSegment],
    elf_base: u64,
    loads: &[PtLoad],
    bias: i64,
    file_offset: u64,
) -> Result<u64, String> {
    if file_offset < elf_base {
        return Err(format!(
            "file offset {file_offset} is below the ELF base {elf_base}"
        ));
    }
    let relative = file_offset - elf_base;
    let load = loads
        .iter()
        .find(|load| relative >= load.offset && relative < load.offset + load.filesz)
        .ok_or_else(|| format!("offset {file_offset} falls outside every PT_LOAD segment"))?;
    if load.flags & 1 == 0 {
        return Err(format!(
            "offset {file_offset} is inside a PT_LOAD at {:#x} that is not executable",
            load.vaddr
        ));
    }
    let va = bias as i128 + load.vaddr as i128 + (relative - load.offset) as i128;
    if va < 0 || va > u64::MAX as i128 {
        return Err("resolved address is outside the address space".to_string());
    }
    let va = va as u64;
    let covered = segments
        .iter()
        .any(|segment| segment.executable && va >= segment.start && va < segment.end);
    if !covered {
        return Err(format!(
            "resolved address {va:#x} is not inside an executable mapping of the target"
        ));
    }
    Ok(va)
}

/// Page-aligned window that contains a file offset.
pub fn page_aligned_read(file_offset: u64, want: usize) -> (u64, usize) {
    let start = align_down(file_offset, PAGE);
    (start, (file_offset - start) as usize + want)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn put64(bytes: &mut [u8], offset: usize, value: u64) {
        bytes[offset..offset + 8].copy_from_slice(&value.to_le_bytes());
    }

    fn put32(bytes: &mut [u8], offset: usize, value: u32) {
        bytes[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
    }

    fn enter_record(call_id: u64, direction: u32, payload: &[u8]) -> Vec<u8> {
        let mut bytes = vec![0u8; RECORD_BYTES];
        put64(&mut bytes, 0, 1_000);
        put64(&mut bytes, 16, call_id);
        put64(&mut bytes, 24, 0xdead_beef);
        put32(&mut bytes, 80, payload.len() as u32);
        put32(&mut bytes, 84, KIND_ENTER);
        put32(&mut bytes, 88, FLAG_USER_READ);
        put32(&mut bytes, 104, direction);
        put32(&mut bytes, 112, 42);
        put32(&mut bytes, 116, 42);
        bytes[120..124].copy_from_slice(b"Test");
        bytes[HEADER_BYTES..HEADER_BYTES + payload.len()].copy_from_slice(payload);
        bytes
    }

    fn exit_record(call_id: u64, direction: u32, result: i64, payload: &[u8]) -> Vec<u8> {
        let mut bytes = enter_record(call_id, direction, payload);
        put32(&mut bytes, 84, KIND_EXIT);
        put64(&mut bytes, 64, result as u64);
        put32(&mut bytes, 80, payload.len() as u32);
        bytes
    }

    #[test]
    fn synthetic_decode_accepts_a_well_formed_record() {
        let bytes = enter_record(7, DIR_WRITE, b"hello");
        let record = decode(&bytes).expect("record decodes");
        assert_eq!(record.call_id, 7);
        assert_eq!(record.captured, 5);
        assert_eq!(record.payload, b"hello");
        assert_eq!(record.direction, DIR_WRITE);
        assert_eq!(record.comm_text(), "Test");
        assert!(record.is_enter());
    }

    #[test]
    fn synthetic_decode_refuses_broken_abi() {
        let bytes = enter_record(1, DIR_WRITE, b"x");
        assert_eq!(decode(&bytes[..100]), Err(DecodeError::Length(100)));
        let mut broken = enter_record(1, DIR_WRITE, b"x");
        put32(&mut broken, 84, 9);
        assert_eq!(decode(&broken), Err(DecodeError::Kind(9)));
        let mut broken = enter_record(1, DIR_WRITE, b"x");
        put32(&mut broken, 104, 7);
        assert_eq!(decode(&broken), Err(DecodeError::Direction(7)));
        let mut broken = enter_record(1, DIR_WRITE, b"x");
        put32(&mut broken, 108, MAX_TARGETS as u32);
        assert_eq!(decode(&broken), Err(DecodeError::Tag(MAX_TARGETS as u32)));
        let mut broken = enter_record(1, DIR_WRITE, b"x");
        put32(&mut broken, 80, SAMPLE_CAP as u32 + 1);
        assert_eq!(
            decode(&broken),
            Err(DecodeError::Captured(SAMPLE_CAP as u32 + 1))
        );
        let mut broken = enter_record(1, DIR_WRITE, b"x");
        put32(&mut broken, 100, MAX_DEPTH + 1);
        assert_eq!(decode(&broken), Err(DecodeError::Depth(MAX_DEPTH + 1)));
        let mut broken = enter_record(1, DIR_WRITE, b"x");
        broken[136] = 1;
        assert_eq!(decode(&broken), Err(DecodeError::Reserved(1)));
    }

    #[test]
    fn synthetic_pairing_pairs_write_and_read() {
        let mut pairer = Pairer::new();
        let entry = decode(&enter_record(1, DIR_WRITE, b"GET / HTTP/1.1\r\n\r\n")).unwrap();
        assert!(pairer.admit(entry).unwrap().is_none());
        let exit = decode(&exit_record(1, DIR_WRITE, 18, b"")).unwrap();
        let call = pairer.admit(exit).unwrap().unwrap();
        assert_eq!(call.direction, DIR_WRITE);
        assert_eq!(call.result, Some(18));
        assert_eq!(call.valid_bytes(), 18);
        assert!(!call.unpaired());
        assert_eq!(call.depth, 0);
        assert_eq!(pairer.depth(), 0);
    }

    #[test]
    fn synthetic_pairing_is_reentrant() {
        let mut pairer = Pairer::new();
        let outer = decode(&enter_record(1, DIR_WRITE, b"outer")).unwrap();
        let inner = decode(&enter_record(2, DIR_WRITE, b"inner")).unwrap();
        pairer.admit(outer).unwrap();
        pairer.admit(inner).unwrap();
        assert_eq!(pairer.depth(), 2);
        let inner_exit = pairer
            .admit(decode(&exit_record(2, DIR_WRITE, 5, b"")).unwrap())
            .unwrap()
            .expect("inner completes first");
        let outer_exit = pairer
            .admit(decode(&exit_record(1, DIR_WRITE, 5, b"")).unwrap())
            .unwrap()
            .expect("outer completes second");
        assert_eq!(inner_exit.call_id, 2);
        assert_eq!(outer_exit.call_id, 1);
        assert_eq!(inner_exit.payload, b"inner");
        assert_eq!(outer_exit.payload, b"outer");
    }

    #[test]
    fn synthetic_pairing_reports_the_pathological_cases() {
        let mut pairer = Pairer::new();
        let exit = decode(&exit_record(9, DIR_WRITE, 1, b"")).unwrap();
        assert_eq!(pairer.admit(exit), Err(PairError::UnpairedExit));
        assert_eq!(pairer.unpaired_exits, 1);
        pairer
            .admit(decode(&enter_record(3, DIR_WRITE, b"a")).unwrap())
            .unwrap();
        let duplicate = decode(&enter_record(3, DIR_WRITE, b"b")).unwrap();
        assert_eq!(pairer.admit(duplicate), Err(PairError::DuplicateEntry(3)));
        pairer
            .admit(decode(&exit_record(3, DIR_WRITE, 1, b"")).unwrap())
            .unwrap();
        let twice = decode(&exit_record(3, DIR_WRITE, 1, b"")).unwrap();
        assert_eq!(pairer.admit(twice), Err(PairError::DuplicateExit(3)));
    }

    #[test]
    fn synthetic_finish_surfaces_calls_without_a_return() {
        let mut pairer = Pairer::new();
        pairer
            .admit(decode(&enter_record(5, DIR_READ, b"")).unwrap())
            .unwrap();
        let unfinished = pairer.finish();
        assert_eq!(unfinished.len(), 1);
        assert!(unfinished[0].unpaired());
        assert_eq!(unfinished[0].direction, DIR_READ);
    }

    #[test]
    fn synthetic_read_counts_only_bytes_the_call_returned() {
        let mut pairer = Pairer::new();
        pairer
            .admit(decode(&enter_record(1, DIR_READ, b"")).unwrap())
            .unwrap();
        let call = pairer
            .admit(decode(&exit_record(1, DIR_READ, 20, b"HTTP/1.1 200 OK\r\n\r\n")).unwrap())
            .unwrap()
            .unwrap();
        assert_eq!(call.captured, 19);
        assert_eq!(call.valid_bytes(), 19);
        let mut short = Pairer::new();
        short
            .admit(decode(&enter_record(2, DIR_READ, b"")).unwrap())
            .unwrap();
        let call = short
            .admit(decode(&exit_record(2, DIR_READ, 4, b"HTTP/1.1 200 OK\r\n\r\n")).unwrap())
            .unwrap()
            .unwrap();
        assert_eq!(call.valid_bytes(), 4);
    }

    #[test]
    fn synthetic_alias_is_a_run_local_counter() {
        let mut pairer = Pairer::new();
        for (call_id, ssl) in [(1u64, 0x1000u64), (2, 0x2000), (3, 0x1000)] {
            let mut bytes = enter_record(call_id, DIR_WRITE, b"x");
            put64(&mut bytes, 24, ssl);
            pairer.admit(decode(&bytes).unwrap()).unwrap();
            let call = pairer
                .admit(decode(&exit_record(call_id, DIR_WRITE, 1, b"")).unwrap())
                .unwrap()
                .unwrap();
            match call_id {
                1 => assert_eq!(call.ssl_id, 1),
                2 => assert_eq!(call.ssl_id, 2),
                _ => assert_eq!(call.ssl_id, 1),
            }
            assert_ne!(call.ssl_id as u64, ssl);
        }
    }

    #[test]
    fn synthetic_request_structure_is_desensitised() {
        let payload = b"POST /api/v1/runs/12345/points?token=abcd1234deadbeef&lat=39.9&lng=116.3 HTTP/1.1\r\nHost: api.example.com:443\r\nAuthorization: Bearer secret\r\nContent-Type: application/json\r\nContent-Length: 42\r\n\r\n{\"secret\":1}";
        let facts = http_facts(payload);
        let HttpFacts::Request {
            method,
            path,
            host,
            params,
            header_names,
            body_bytes,
            ..
        } = facts.clone()
        else {
            panic!("expected a request");
        };
        assert_eq!(method, "POST");
        assert_eq!(path, "/api/v1/runs/{id}/points");
        assert_eq!(host.as_deref(), Some("api.example.com"));
        assert_eq!(
            params,
            vec![
                ("token".to_string(), "hex"),
                ("lat".to_string(), "text"),
                ("lng".to_string(), "text")
            ]
        );
        assert!(header_names.contains(&"authorization".to_string()));
        assert_eq!(body_bytes, 12);
        let json = facts.to_json().to_string();
        for forbidden in ["abcd1234deadbeef", "Bearer", "39.9", "116.3", "secret"] {
            assert!(!json.contains(forbidden), "leaked {forbidden} in {json}");
        }
    }

    #[test]
    fn synthetic_absolute_form_request_keeps_the_host() {
        let facts = http_facts(b"GET https://api.example.com:8443/v2/a/77 HTTP/1.1\r\n\r\n");
        let json = facts.to_json();
        assert_eq!(json["scheme"], "https");
        assert_eq!(json["host"], "api.example.com");
        assert_eq!(json["path"], "/v2/a/{id}");
    }

    #[test]
    fn synthetic_response_structure_keeps_only_metadata() {
        let payload = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 512\r\nSet-Cookie: sid=abc\r\n\r\n{}";
        let json = http_facts(payload).to_json();
        assert_eq!(json["kind"], "response");
        assert_eq!(json["status"], 200);
        assert_eq!(json["content_type"], "application/json");
        assert_eq!(json["content_length"], 512);
        assert!(!json.to_string().contains("sid=abc"));
    }

    #[test]
    fn synthetic_non_http_payload_is_reported_as_other() {
        let json = http_facts(&[0x16, 0x03, 0x81, 0x02]).to_json();
        assert_eq!(json["kind"], "other");
        assert_eq!(json["first_bytes_ascii"], false);
        assert_eq!(http_facts(b"garbage").to_json()["kind"], "other");
    }

    #[test]
    fn synthetic_target_spec_parses_both_forms() {
        let apk = TargetSpec::parse("apk-libssl=/data/app/base.apk@98336768:98514224:98514028")
            .expect("apk form parses");
        assert_eq!(apk.label, "apk-libssl");
        assert_eq!(apk.elf_base, 98_336_768);
        assert_eq!(apk.write, Attach::Offset(98_514_224));
        assert_eq!(apk.read, Some(Attach::Offset(98_514_028)));
        let so = TargetSpec::parse("system-libssl=/system/lib64/libssl.so:SSL_write").unwrap();
        assert_eq!(so.elf_base, 0);
        assert_eq!(so.write, Attach::Symbol("SSL_write".to_string()));
        assert_eq!(so.read, None);
        let both = TargetSpec::parse("x=/p/libssl.so:SSL_write:SSL_read").unwrap();
        assert_eq!(both.read, Some(Attach::Symbol("SSL_read".to_string())));
        assert!(TargetSpec::parse("nolabel").is_err());
        assert!(TargetSpec::parse("x=relative/path:1").is_err());
        assert!(TargetSpec::parse("x=/p:1:2:3").is_err());
        assert!(TargetSpec::parse("x=/p:bad symbol").is_err());
        assert!(TargetSpec::parse("x=/p@zz:1").is_err());
        assert!(TargetSpec::parse("a/b=/p:1").is_err());
    }

    #[test]
    fn synthetic_cli_rejects_bad_combinations() {
        let ok: Vec<String> = [
            "--pid", "42", "--duration", "5", "--out", "/tmp/x.jsonl", "--target", "l=/p:1",
        ]
        .iter()
        .map(|value| value.to_string())
        .collect();
        let args = AttachArgs::parse(&ok).expect("minimal argv parses");
        assert_eq!(args.pid, 42);
        assert_eq!(args.targets.len(), 1);
        assert!(!args.report_only);
        assert_eq!(args.labels(), vec!["l".to_string(), String::new()]);
        let mut bad = ok.clone();
        bad[3] = "0".to_string();
        assert_eq!(
            AttachArgs::parse(&bad),
            Err("--duration must be 1..=120".to_string())
        );
        let mut with_report = ok.clone();
        with_report.push("--report".to_string());
        assert!(AttachArgs::parse(&with_report).unwrap().report_only);
        let mut unknown = ok.clone();
        unknown.push("--nope".to_string());
        assert_eq!(
            AttachArgs::parse(&unknown),
            Err("unknown argument '--nope'".to_string())
        );
        let mut missing = ok.clone();
        missing.pop();
        assert_eq!(
            AttachArgs::parse(&missing),
            Err("--target needs a value".to_string())
        );
        let mut three = ok.clone();
        for label in ["l2", "l3"] {
            three.push("--target".to_string());
            three.push(format!("{label}=/p:2"));
        }
        assert_eq!(
            AttachArgs::parse(&three),
            Err("at most 2 --target values are supported".to_string())
        );
        let mut dup = ok.clone();
        dup.push("--target".to_string());
        dup.push("l=/p:2".to_string());
        assert_eq!(
            AttachArgs::parse(&dup),
            Err("target label 'l' given twice".to_string())
        );
    }

    fn synthetic_elf(class64: bool, loads: &[(u64, u64, u64, u64, u32)]) -> Vec<u8> {
        let mut image = vec![0u8; 4096];
        image[..4].copy_from_slice(b"\x7fELF");
        image[4] = if class64 { 2 } else { 1 };
        image[5] = 1;
        image[16..18].copy_from_slice(&3u16.to_le_bytes());
        image[18..20].copy_from_slice(&0xb7u16.to_le_bytes());
        if class64 {
            put64(&mut image, 32, 64);
        } else {
            put32(&mut image, 32, 52);
        }
        let entsize: u16 = if class64 { 56 } else { 32 };
        let entsize_at = if class64 { 54 } else { 42 };
        image[entsize_at..entsize_at + 2].copy_from_slice(&entsize.to_le_bytes());
        let phnum_at = if class64 { 56 } else { 44 };
        image[phnum_at..phnum_at + 2].copy_from_slice(&(loads.len() as u16).to_le_bytes());
        let base0 = if class64 { 64 } else { 52 };
        for (index, (offset, vaddr, filesz, memsz, flags)) in loads.iter().enumerate() {
            let base = base0 + index * entsize as usize;
            put32(&mut image, base, 1);
            put32(&mut image, base + 4, *flags);
            if class64 {
                put64(&mut image, base + 8, *offset);
                put64(&mut image, base + 16, *vaddr);
                put64(&mut image, base + 32, *filesz);
                put64(&mut image, base + 40, *memsz);
            } else {
                put32(&mut image, base + 4, *offset as u32);
                put32(&mut image, base + 8, *vaddr as u32);
                put32(&mut image, base + 16, *filesz as u32);
                put32(&mut image, base + 20, *memsz as u32);
                put32(&mut image, base + 24, *flags);
            }
        }
        image
    }

    #[test]
    fn synthetic_elf_parses_program_headers() {
        let image = synthetic_elf(
            true,
            &[(0, 0, 0x1000, 0x1000, 5), (0x2000, 0x3000, 0x400, 0x400, 4)],
        );
        let meta = parse_elf(&image).expect("ELF parses");
        assert!(meta.class64);
        assert!(meta.little_endian);
        assert_eq!(meta.machine, 0xb7);
        assert_eq!(meta.elf_type, 3);
        assert_eq!(meta.loads.len(), 2);
        assert_eq!(meta.loads[0].flags, 5);
        assert!(meta.executable_load(0x10).is_some());
        assert!(meta.executable_load(0x2000).is_none());
        assert!(parse_elf(b"not an elf").is_err());
        let elf32 = synthetic_elf(false, &[(0, 0, 0x1000, 0x1000, 5)]);
        assert_eq!(
            parse_elf(&elf32),
            Err("ELF32 images are not supported by this tool".to_string())
        );
    }

    #[test]
    fn synthetic_load_bias_and_va_are_consistent() {
        let elf_base = 98_336_768u64;
        let image = synthetic_elf(
            true,
            &[(0, 0, 0x1000, 0x1000, 5), (0x2a000, 0x3a000, 0x9000, 0x9000, 5)],
        );
        let meta = parse_elf(&image).expect("ELF parses");
        let segments = [
            MapSegment {
                start: 0x7f00_0000,
                end: 0x7f00_1000,
                file_offset: elf_base,
                executable: false,
            },
            MapSegment {
                start: 0x7f03_a000,
                end: 0x7f04_3000,
                file_offset: elf_base + 0x2a000,
                executable: true,
            },
        ];
        let probe = elf_base + 0x2a010;
        let proof = load_bias_for(&segments, elf_base, &meta.loads, probe, None).expect("bias");
        assert_eq!(proof.bias, 0x7f00_0000);
        assert_eq!(proof.source, BiasSource::Segment);
        assert!(proof.agrees());
        assert_eq!(proof.cross_checked, 1);
        assert_eq!(proof.anchor_start, Some(0x7f03_a000));
        assert_eq!(proof.anchor_vaddr, 0x3a000);
        assert_eq!(proof.anchor_file_offset, elf_base + 0x2a000);
        let va = resolve_va(&segments, elf_base, &meta.loads, proof.bias, probe).unwrap();
        assert_eq!(va, 0x7f03_a010);
        assert!(resolve_va(&segments, elf_base, &meta.loads, proof.bias, 0x7f03_a010).is_err());
        assert!(
            resolve_va(&segments, elf_base, &meta.loads, proof.bias, elf_base + 0x1_0000).is_err()
        );
    }

    #[test]
    fn load_bias_comes_from_the_segment_holding_the_offset_only() {
        // Real shape reported from the device: a static executable whose
        // PT_LOADs give different (p_vaddr - p_offset) values.  Requiring them
        // all to agree refused the fixture; only the probe's own segment may
        // decide, and the others are a warning.
        let image = synthetic_elf(
            true,
            &[
                (0x0, 0x20_0000, 0x1000, 0x1000, 4),
                (0x1000, 0x21_1000, 0x1000, 0x1000, 5),
                (0x2_2000, 0x22_2000, 0x1000, 0x1000, 6),
            ],
        );
        let meta = parse_elf(&image).expect("ELF parses");
        let segments = [
            MapSegment {
                start: 0x20_0000,
                end: 0x20_1000,
                file_offset: 0x0,
                executable: false,
            },
            MapSegment {
                start: 0x21_1000,
                end: 0x21_2000,
                file_offset: 0x1000,
                executable: true,
            },
            // Mapped one 64 KiB page above where its link address points.
            MapSegment {
                start: 0x23_2000,
                end: 0x23_3000,
                file_offset: 0x2_2000,
                executable: false,
            },
        ];
        let probe = 0x1974; // inside the executable PT_LOAD
        let proof = load_bias_for(&segments, 0, &meta.loads, probe, None).expect("bias derives");
        assert_eq!(proof.bias, 0);
        assert_eq!(proof.anchor_start, Some(0x21_1000));
        assert_eq!(proof.cross_checked, 2);
        assert!(!proof.agrees(), "the shifted segment is reported");
        assert_eq!(proof.disagreements.len(), 1);
        assert!(proof.disagreements[0].contains("0x232000"));
        // The probe still resolves through the anchor, which is the point.
        let va = resolve_va(&segments, 0, &meta.loads, proof.bias, probe).unwrap();
        assert_eq!(va, 0x21_1974);
    }

    #[test]
    fn load_bias_override_skips_derivation_but_still_checks_the_landing_point() {
        let image = synthetic_elf(true, &[(0x1000, 0x21_1000, 0x1000, 0x1000, 5)]);
        let meta = parse_elf(&image).expect("ELF parses");
        let segments = [MapSegment {
            start: 0x21_1000,
            end: 0x21_2000,
            file_offset: 0x1000,
            executable: true,
        }];
        let proof =
            load_bias_for(&segments, 0, &meta.loads, 0x1974, Some(0)).expect("override taken");
        assert_eq!(proof.bias, 0);
        assert_eq!(proof.source, BiasSource::Override);
        assert_eq!(proof.cross_checked, 0);
        assert!(proof.agrees());
        assert_eq!(proof.anchor_start, Some(0x21_1000));
        let va = resolve_va(&segments, 0, &meta.loads, proof.bias, 0x1974).unwrap();
        assert_eq!(va, 0x21_1974);
        // An override may not place the probe outside the executable mappings.
        assert!(resolve_va(&segments, 0, &meta.loads, 0x10_0000, 0x1974).is_err());
        // Even a "derivable" offset takes the override when one is given.
        let forced = load_bias_for(&segments, 0, &meta.loads, 0x1974, Some(0x3000)).expect("forced");
        assert_eq!(forced.bias, 0x3000);
        assert_eq!(forced.source, BiasSource::Override);
    }

    #[test]
    fn load_bias_refuses_when_the_anchor_segment_is_not_mapped() {
        let image = synthetic_elf(true, &[(0x1000, 0x21_1000, 0x1000, 0x1000, 5)]);
        let meta = parse_elf(&image).expect("ELF parses");
        let none = [MapSegment {
            start: 0x1000,
            end: 0x2000,
            file_offset: 0x999_9000,
            executable: true,
        }];
        let error = load_bias_for(&none, 0, &meta.loads, 0x1974, None).expect_err("no anchor");
        assert!(error.contains("no /proc/<pid>/maps segment matches"), "{error}");
        let outside = load_bias_for(&none, 0, &meta.loads, 0x99_0000, None).expect_err("no load");
        assert!(outside.contains("falls outside every PT_LOAD"), "{outside}");
    }

    #[test]
    fn synthetic_page_aligned_window_covers_the_offset() {
        assert_eq!(page_aligned_read(0x1010, 16), (0x1000, 32));
        assert_eq!(page_aligned_read(0x1000, 8), (0x1000, 8));
    }

    #[test]
    fn synthetic_normalize_path_keeps_shape() {
        assert_eq!(
            normalize_path("/api/v1/runs/987/points"),
            "/api/v1/runs/{id}/points"
        );
        assert_eq!(normalize_path("/"), "/");
        assert_eq!(normalize_path("/a/0123456789abcdef0123456789abcdef"), "/a/{id}");
        assert_eq!(
            normalize_path("/u/2f1c9b0e-4a55-4c1e-9b2c-1d3e4f5a6b7c/x"),
            "/u/{id}/x"
        );
    }
/// Minimal 64-bit ELF with one PT_LOAD, one SHT_DYNSYM and one STRTAB, so
    /// symbol resolution is exercised without any external file.
    fn synthetic_elf_with_symbol(name: &str, value: u64) -> Vec<u8> {
        let mut image = vec![0u8; 2048];
        image[..4].copy_from_slice(b"\x7fELF");
        image[4] = 2;
        image[5] = 1;
        image[16..18].copy_from_slice(&3u16.to_le_bytes());
        image[18..20].copy_from_slice(&0xb7u16.to_le_bytes());
        put64(&mut image, 32, 64);
        image[54..56].copy_from_slice(&56u16.to_le_bytes());
        image[56..58].copy_from_slice(&1u16.to_le_bytes());
        put64(&mut image, 40, 0x300);
        image[58..60].copy_from_slice(&64u16.to_le_bytes());
        image[60..62].copy_from_slice(&3u16.to_le_bytes());
        // PT_LOAD: file 0..0x800 maps at 0x1000, executable.
        put32(&mut image, 64, 1);
        put32(&mut image, 68, 5);
        put64(&mut image, 72, 0);
        put64(&mut image, 80, 0x1000);
        put64(&mut image, 96, 0x800);
        put64(&mut image, 104, 0x800);
        // Section 1: SHT_DYNSYM at 0x200, two entries, linking section 2.
        put32(&mut image, 0x300 + 64 + 4, SHT_DYNSYM);
        put64(&mut image, 0x300 + 64 + 24, 0x200);
        put64(&mut image, 0x300 + 64 + 32, 48);
        put32(&mut image, 0x300 + 64 + 40, 2);
        put64(&mut image, 0x300 + 64 + 56, 24);
        // Section 2: SHT_STRTAB at 0x280.
        put32(&mut image, 0x300 + 128 + 4, 3);
        put64(&mut image, 0x300 + 128 + 24, 0x280);
        put64(&mut image, 0x300 + 128 + 32, (name.len() + 2) as u64);
        // dynsym entry 1: name offset 1, st_value = the symbol address.
        put32(&mut image, 0x200 + 24, 1);
        put64(&mut image, 0x200 + 24 + 8, value);
        put64(&mut image, 0x200 + 24 + 16, 4);
        image[0x280] = 0;
        image[0x281..0x281 + name.len()].copy_from_slice(name.as_bytes());
        image
    }

    #[test]
    fn synthetic_dynsym_resolves_to_a_file_offset() {
        let image = synthetic_elf_with_symbol("SSL_write", 0x1110);
        let meta = parse_elf(&image).expect("ELF with sections parses");
        assert_eq!(meta.sections.len(), 3);
        assert!(meta.section_window() <= image.len() as u64);
        let (file_offset, value) = meta
            .symbol_file_offset(&image, "SSL_write")
            .expect("symbol resolves");
        assert_eq!(value, 0x1110);
        assert_eq!(file_offset, 0x110);
        // The embedded base is added by the caller, never by the ELF math.
        let elf_base = 98_336_768u64;
        assert_eq!(elf_base + file_offset, 98_336_768 + 0x110);
        assert!(meta.symbol_file_offset(&image, "SSL_read").is_err());
        let window = header_window(&image).expect("header window is computable");
        assert_eq!(window, 0x300 + 3 * 64);
    }

    #[test]
    fn synthetic_vaddr_translation_is_load_relative() {
        let image = synthetic_elf(true, &[(0, 0x1000, 0x800, 0x800, 5)]);
        let meta = parse_elf(&image).expect("ELF parses");
        assert_eq!(meta.vaddr_to_file_offset(0x1000), Some(0));
        assert_eq!(meta.vaddr_to_file_offset(0x1004), Some(4));
        assert_eq!(meta.vaddr_to_file_offset(0x2000), None);
    }
}





#[cfg(test)]
mod maps_association_tests {
    use super::*;

    /// Real /proc/<pid>/maps sample captured in the t6 window 3 on kebab:
    /// three base.apk segments plus the two summary lines the capture script
    /// appended (which the parser has to skip).
    const STEP3_APK_HEAD: &str = include_str!("../fixtures/maps-samples/step3-maps-apk-head.txt");

    const APK_INODE: u64 = 1_443_630;
    const APK_DEV: (u64, u64) = (0x103, 0x0c);

    #[test]
    fn maps_parse_real_step3_apk_sample() {
        let parsed = parse_maps(STEP3_APK_HEAD);
        assert_eq!(parsed.lines.len(), 3, "three base.apk segments");
        assert_eq!(parsed.skipped, 2, "the two summary lines are not maps lines");
        let first = &parsed.lines[0];
        assert_eq!(first.start, 0xfffe_172d_7000);
        assert_eq!(first.end, 0xfffe_173e_9000);
        assert_eq!(first.file_offset, 0x03dd_c000);
        assert_eq!(first.dev(), APK_DEV);
        assert_eq!(first.inode, APK_INODE);
        assert_eq!(first.perms, "r-xp");
        assert!(first.executable());
        assert_eq!(dev_text_of(first), "103:0c");
        assert!(first.path.ends_with("/base.apk"));
        assert!(first.path.starts_with("/data/app/"));
        assert_eq!(parsed.lines[1].file_offset, 0x03ee_d000);
        assert_eq!(parsed.lines[2].perms, "rw-p");
        assert!(!parsed.lines[2].executable());
    }

    fn dev_text_of(line: &MapsLine) -> String {
        dev_pair_text(line.dev())
    }

    #[test]
    fn maps_associate_real_step3_apk_is_tuple() {
        let parsed = parse_maps(STEP3_APK_HEAD);
        let assoc = associate(&parsed.lines, APK_DEV, APK_INODE);
        assert_eq!(assoc.kind, AssocKind::Tuple);
        assert_eq!(assoc.indices, vec![0, 1, 2]);
        assert_eq!(assoc.maps_dev, Some(APK_DEV));
        let segments: Vec<MapSegment> = assoc
            .indices
            .iter()
            .map(|index| parsed.lines[*index].segment())
            .collect();
        assert!(segments.iter().any(|segment| segment.executable));
    }

    #[test]
    fn maps_associate_real_step3_apk_survives_dev_drift() {
        // The same file seen through a second mount of the same image: the inode
        // is unchanged, the device number is not.
        let parsed = parse_maps(STEP3_APK_HEAD);
        let drifted = (0x103, 0x48);
        let assoc = associate(&parsed.lines, drifted, APK_INODE);
        assert_eq!(assoc.kind, AssocKind::DevDrift);
        assert_eq!(assoc.indices.len(), 3);
        assert_eq!(assoc.maps_dev, Some(APK_DEV), "the maps device is reported, not invented");
    }

    #[test]
    fn maps_associate_system_libssl_from_t6_log() {
        // Reconstructed from the t6 evidence (report2.json + uprobe.out): the
        // guest view of the rootfs library is inode 3388 under a loop device
        // whose number changed between windows (00:46 -> 00:48). The capture
        // holds four segments; two are enough to pin the association.
        let text = concat!(
            "7f0000000000-7f0000100000 r-xp 00000000 00:46 3388   /system/lib64/libssl.so\n",
            "7f0000100000-7f0000101000 r--p 00100000 00:46 3388   /system/lib64/libssl.so\n",
        );
        let parsed = parse_maps(text);
        assert_eq!(parsed.lines.len(), 2);
        assert_eq!(parsed.skipped, 0);
        assert_eq!(associate(&parsed.lines, (0x00, 0x46), 3388).kind, AssocKind::Tuple);
        let drift = associate(&parsed.lines, (0x00, 0x48), 3388);
        assert_eq!(drift.kind, AssocKind::DevDrift);
        assert_eq!(drift.indices.len(), 2);
        assert_eq!(drift.maps_dev, Some((0x00, 0x46)));
    }

    #[test]
    fn maps_associate_rejects_unrelated_identity() {
        let parsed = parse_maps(STEP3_APK_HEAD);
        assert_eq!(associate(&parsed.lines, APK_DEV, 9999).kind, AssocKind::None);
        assert_eq!(associate(&parsed.lines, APK_DEV, 0).kind, AssocKind::None);
        assert_eq!(associate(&[], APK_DEV, APK_INODE).kind, AssocKind::None);
    }

    #[test]
    fn maps_parse_keeps_paths_with_spaces_and_skips_junk() {
        let text = concat!(
            "1000-2000 r-xp 00000000 08:01 42   /data/app/a b/base.apk\n",
            "not a maps line\n",
            "2000-3000 rw-p 00001000 08:01 42\n",
            "3000-4000 rw-p 00000000 00:00 0   [anon:dalvik]\n",
        );
        let parsed = parse_maps(text);
        assert_eq!(parsed.skipped, 1);
        assert_eq!(parsed.lines.len(), 3);
        assert_eq!(parsed.lines[0].path, "/data/app/a b/base.apk");
        assert_eq!(parsed.lines[1].path, "");
        assert_eq!(parsed.lines[1].inode, 42);
        assert_eq!(parsed.lines[2].inode, 0);
        assert_eq!(parsed.lines[2].path, "[anon:dalvik]");
        assert_eq!(associate(&parsed.lines, (0x00, 0x00), 0).kind, AssocKind::None);
    }

    #[test]
    fn dev_parts_round_trips_glibc_encoding() {
        assert_eq!(dev_parts(0x1030c), (0x103, 0x0c));
        assert_eq!(dev_text(0x1030c), "103:0c");
        assert_eq!(dev_parts(0x0046), (0x00, 0x46));
        assert_eq!(dev_text(0x0046), "00:46");
        fn makedev(major: u64, minor: u64) -> u64 {
            ((major & 0xfff) << 8)
                | (minor & 0xff)
                | ((major & !0xfff) << 32)
                | ((minor & !0xff) << 12)
        }
        // glibc packs the minor's high part at bit 12, which overlaps the major's
        // high part, so the encoding round-trips for the minors Linux actually
        // reports (the device numbers in this lab are all minor < 0x100).
        for (major, minor) in [
            (0u64, 0u64),
            (0x103, 0x0c),
            (0x08, 0x01),
            (0x00, 0x46),
            (0xff, 0xff),
            (0x103, 0xff),
        ] {
            assert_eq!(dev_parts(makedev(major, minor)), (major, minor), "{major:04x}:{minor:04x}");
        }
        assert_eq!(file_basename("/system/lib64/libssl.so"), "libssl.so");
        assert_eq!(file_basename("base.apk"), "base.apk");
    }
}


#[cfg(test)]
mod maps_root_args_tests {
    use super::*;

    fn argv(items: &[&str]) -> Vec<String> {
        items.iter().map(|item| (*item).to_string()).collect()
    }

    #[test]
    fn maps_root_selector_keeps_default_off_and_path_apart() {
        assert_eq!(MapsRoot::parse("off"), MapsRoot::Off);
        assert_eq!(MapsRoot::parse("none"), MapsRoot::Off);
        assert_eq!(MapsRoot::parse(""), MapsRoot::Off);
        assert_eq!(MapsRoot::parse("/tmp/guest"), MapsRoot::Path("/tmp/guest".to_string()));
        assert_eq!(MapsRoot::default(), MapsRoot::Default);
        assert_eq!(MapsRoot::default().resolve(7), Some("/proc/7/root".to_string()));
        assert_eq!(MapsRoot::Off.resolve(7), None);
        assert_eq!(
            MapsRoot::Path("/mnt/x".to_string()).resolve(7),
            Some("/mnt/x".to_string())
        );
    }

    #[test]
    fn maps_root_flag_parses_and_defaults() {
        let base = [
            "--pid",
            "42",
            "--duration",
            "5",
            "--out",
            "/tmp/x.jsonl",
            "--target",
            "l=/tmp/libssl.so:0",
        ];
        let args = AttachArgs::parse(&argv(&base)).expect("plain args");
        assert_eq!(args.maps_root, MapsRoot::Default);
        let mut with_flag = argv(&base);
        with_flag.push("--maps-root".to_string());
        with_flag.push("/proc/42/root".to_string());
        let args = AttachArgs::parse(&with_flag).expect("with maps root");
        assert_eq!(args.maps_root, MapsRoot::Path("/proc/42/root".to_string()));
        let mut disabled = argv(&base);
        disabled.push("--maps-root".to_string());
        disabled.push("off".to_string());
        assert_eq!(AttachArgs::parse(&disabled).expect("off").maps_root, MapsRoot::Off);
        let mut twice = with_flag.clone();
        twice.push("--maps-root".to_string());
        twice.push("off".to_string());
        assert!(AttachArgs::parse(&twice).is_err(), "twice must be refused");
        let mut missing = argv(&base);
        missing.push("--maps-root".to_string());
        assert!(AttachArgs::parse(&missing).is_err(), "missing value must be refused");
    }
}
