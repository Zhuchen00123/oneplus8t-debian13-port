//! Portable (Windows + Linux) core for the own-TLS-fixture observer.
//!
//! This module contains every decision that can be tested without a kernel:
//! wire decoding for the SSL_write probe pair, pairing/accounting of the
//! bounded sample stream, payload-formula verification, identity binding
//! helpers (procfs / ELF build-id / gnu dev encoding), and run-directory
//! naming rules.  No unsafe code, no syscalls, no formatting of unknown bytes.

use serde_json::Value;

/// Size of the fixed probe record header, in bytes (ABI with the BPF object).
pub const RECORD_HEADER: usize = 112;
/// Maximum number of payload bytes a single record may carry.
pub const SAMPLE_CAP: usize = 4096;
/// Total size of one ring-buffer record: header + sample area.
pub const RING_RECORD: usize = RECORD_HEADER + SAMPLE_CAP;
/// Number of counter slots exposed by the BPF object.
pub const COUNTER_SLOTS: usize = 16;
/// Number of configuration slots exposed by the BPF object.
pub const CONFIG_SLOTS: usize = 32;
/// Hard cap on events per session (mirrors the BPF configuration).
pub const EVENT_LIMIT: u64 = 10000;
/// Hard cap on accumulated captured payload per session.
pub const PAYLOAD_BUDGET: u64 = 1 << 20;
/// Ring buffer size in bytes.
pub const RING_BYTES: u32 = 1 << 20;
/// Declared upper bound of BPF memory the object asks the kernel for.
pub const BPF_ALLOC_DECLARED_BYTES: u64 = 8 << 20;

/// CONFIG slot: target host TGID.
pub const CFG_TGID: u32 = 0;
/// CONFIG slot: expected group-leader start time in 100 Hz ticks.
pub const CFG_START: u32 = 1;
/// CONFIG slot: AT_CLKTCK (only 100 is accepted).
pub const CFG_HZ: u32 = 2;
/// CONFIG slot: BOOTTIME deadline in ns.
pub const CFG_DEADLINE: u32 = 3;
/// CONFIG slot: expected uid.
pub const CFG_UID: u32 = 4;
/// CONFIG slot: maximum emitted events.
pub const CFG_MAX_EVENTS: u32 = 5;
/// CONFIG slot: sample cap in bytes.
pub const CFG_SAMPLE_CAP: u32 = 6;
/// CONFIG slot: pid-namespace st_dev.
pub const CFG_NS_DEV: u32 = 7;
/// CONFIG slot: pid-namespace st_ino.
pub const CFG_NS_INO: u32 = 8;
/// Highest alias id the BPF pointer map can hand out (map capacity).
pub const ALIAS_MAX_ID: u32 = 32;

/// Counter slot: entry probe hits.
pub const CNT_ENTRY: u32 = 0;
/// Counter slot: exit probe hits.
pub const CNT_EXIT: u32 = 1;
/// Counter slot: records successfully submitted.
pub const CNT_SUBMITTED: u32 = 2;
/// Counter slot: ring-buffer reservations that failed.
pub const CNT_RESERVE_FAILED: u32 = 3;
/// Counter slot: identity rejections.
pub const CNT_IDENTITY_REJECTED: u32 = 4;
/// Counter slot: map update/delete failures.
pub const CNT_MAP_FAILED: u32 = 5;
/// Counter slot: task-structure read failures.
pub const CNT_BIRTH_FAILED: u32 = 6;
/// Counter slot: exit probes without a matching entry.
pub const CNT_UNPAIRED: u32 = 7;
/// Counter slot: events dropped by the sequence limit.
pub const CNT_SEQ_OVERFLOW: u32 = 8;
/// Counter slot: nesting deeper than 8.
pub const CNT_DEPTH_OVERFLOW: u32 = 9;
/// Counter slot: user-buffer read attempts.
pub const CNT_USER_READ_ATTEMPTS: u32 = 10;
/// Counter slot: user bytes actually read.
pub const CNT_USER_READ_BYTES: u32 = 11;
/// Counter slot: records marked truncated.
pub const CNT_TRUNCATED: u32 = 12;
/// Counter slot: declarations <= 0.
pub const CNT_LEN_INVALID: u32 = 13;
/// Counter slot: user-buffer reads that failed.
pub const CNT_READ_FAILED: u32 = 14;

/// Record kind: SSL_write entry.
pub const KIND_ENTER: u32 = 1;
/// Record kind: SSL_write return.
pub const KIND_EXIT: u32 = 2;

/// Flag: the declared length exceeded the sample cap.
pub const FLAG_TRUNCATED: u32 = 0x1;
/// Flag: the declared length was <= 0, so no user buffer was touched.
pub const FLAG_LEN_INVALID: u32 = 0x2;
/// Flag: the bounded user read failed.
pub const FLAG_READ_FAILED: u32 = 0x4;
/// Flag: a positive bounded user read was performed.
pub const FLAG_USER_READ: u32 = 0x8;
/// Flag: nesting overflow (never emitted with a payload).
pub const FLAG_DEPTH_OVERFLOW: u32 = 0x10;
/// Flag: exit without a matching entry.
pub const FLAG_UNPAIRED: u32 = 0x20;

/// Maximum supported nesting depth for entry/exit pairing.
pub const MAX_DEPTH: u32 = 8;

/// Counter slot names, in slot order.
pub const COUNTER_NAMES: [&str; COUNTER_SLOTS] = [
    "entry",
    "exit",
    "submitted",
    "reserve_failed",
    "identity_rejected",
    "map_failed",
    "birth_read_failed",
    "unpaired",
    "seq_overflow",
    "depth_overflow",
    "user_read_attempts",
    "user_read_bytes",
    "truncated",
    "len_invalid",
    "read_failed",
    "spare",
];

/// CAP_SYS_ADMIN bit index.
pub const CAP_SYS_ADMIN: u32 = 21;
/// CAP_PERFMON bit index.
pub const CAP_PERFMON: u32 = 38;
/// CAP_BPF bit index.
pub const CAP_BPF: u32 = 39;

/// Fixed script lengths, in execution order.
pub const SCRIPT_LENGTHS: [i64; 4] = [512, 384, 8192, -1];
/// Fixed script directions, in execution order.
pub const SCRIPT_DIRECTIONS: [&str; 4] = ["c2s", "s2c", "c2s", "c2s"];
/// Expected captured bytes per script step.
pub const SCRIPT_CAPTURED: [u32; 4] = [512, 384, SAMPLE_CAP as u32, 0];
/// Expected peer bytes for the client-to-server direction.
pub const PEER_C2S_TOTAL: i64 = 512 + 8192;
/// Expected peer bytes for the server-to-client direction.
pub const PEER_S2C_TOTAL: i64 = 384;

// ---------------------------------------------------------------------------
// Declared BPF map budget (B1)
//
// The nine declarations below mirror bpf/tls_fixture.bpf.c:25-35, i.e. the
// object this crate embeds and loads.  Their total is the *logical* declared
// map memory of that object: a ring buffer counts its capacity, every other
// map counts max_entries * (key_size + value_size).  It is a declaration
// total -- never the kernel's actual resident/memlock/JIT/allocator cost, and
// never a cgroup or system quota.
// ---------------------------------------------------------------------------

/// BPF map type `BPF_MAP_TYPE_HASH` (priced by `max_entries * (key + value)`).
pub const BPF_MAP_TYPE_HASH: u32 = 1;
/// BPF map type `BPF_MAP_TYPE_ARRAY` (priced by `max_entries * (key + value)`).
pub const BPF_MAP_TYPE_ARRAY: u32 = 2;
/// BPF map type `BPF_MAP_TYPE_RINGBUF` (priced by capacity, no key and value).
pub const BPF_MAP_TYPE_RINGBUF: u32 = 27;

/// One `SEC("maps")` declaration of the embedded BPF object.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BpfMapDecl {
    /// Map name as it appears in the object's map section.
    pub name: &'static str,
    /// Declared `bpf_map_type`.
    pub map_type: u32,
    /// Declared key size in bytes (0 for a ring buffer).
    pub key_size: u32,
    /// Declared value size in bytes (0 for a ring buffer).
    pub value_size: u32,
    /// Declared maximum entries; a ring buffer declares its capacity here.
    pub max_entries: u32,
}

impl BpfMapDecl {
    /// Build one declaration (const so the table below is compile-time data).
    const fn new(
        name: &'static str,
        map_type: u32,
        key_size: u32,
        value_size: u32,
        max_entries: u32,
    ) -> Self {
        BpfMapDecl {
            name,
            map_type,
            key_size,
            value_size,
            max_entries,
        }
    }
}

/// `CONFIG` (source line 25): run configuration slots.
pub const BPF_MAP_CONFIG: BpfMapDecl =
    BpfMapDecl::new("CONFIG", BPF_MAP_TYPE_ARRAY, 4, 8, CONFIG_SLOTS as u32);
/// `COUNTERS` (source line 26): per-slot event counters.
pub const BPF_MAP_COUNTERS: BpfMapDecl =
    BpfMapDecl::new("COUNTERS", BPF_MAP_TYPE_ARRAY, 4, 8, COUNTER_SLOTS as u32);
/// `SEQUENCE` (source line 27): one run-wide record sequence counter.
pub const BPF_MAP_SEQUENCE: BpfMapDecl = BpfMapDecl::new("SEQUENCE", BPF_MAP_TYPE_ARRAY, 4, 8, 1);
/// `CALLID` (source line 28): one run-wide call-id counter.
pub const BPF_MAP_CALLID: BpfMapDecl = BpfMapDecl::new("CALLID", BPF_MAP_TYPE_ARRAY, 4, 8, 1);
/// `EVENTS` (source line 29): the 1 MiB ring buffer, key and value are zero.
pub const BPF_MAP_EVENTS: BpfMapDecl =
    BpfMapDecl::new("EVENTS", BPF_MAP_TYPE_RINGBUF, 0, 0, RING_BYTES);
/// `INFLIGHT` (source line 30): per-thread nesting stack.
pub const BPF_MAP_INFLIGHT: BpfMapDecl =
    BpfMapDecl::new("INFLIGHT", BPF_MAP_TYPE_HASH, 8, 24, 1024);
/// `DEPTH` (source line 31): per-thread entry depth.
pub const BPF_MAP_DEPTH: BpfMapDecl = BpfMapDecl::new("DEPTH", BPF_MAP_TYPE_HASH, 4, 4, 64);
/// `ALIAS` (source line 34): private `SSL*` -> run-local id map.
pub const BPF_MAP_ALIAS: BpfMapDecl = BpfMapDecl::new("ALIAS", BPF_MAP_TYPE_HASH, 8, 4, 32);
/// `ALIASSEQ` (source line 35): one run-wide alias-id counter.
pub const BPF_MAP_ALIASSEQ: BpfMapDecl = BpfMapDecl::new("ALIASSEQ", BPF_MAP_TYPE_ARRAY, 4, 8, 1);

/// The nine map declarations of `bpf/tls_fixture.bpf.c:25-35`, in source order.
pub const BPF_MAP_DECLS: [BpfMapDecl; 9] = [
    BPF_MAP_CONFIG,
    BPF_MAP_COUNTERS,
    BPF_MAP_SEQUENCE,
    BPF_MAP_CALLID,
    BPF_MAP_EVENTS,
    BPF_MAP_INFLIGHT,
    BPF_MAP_DEPTH,
    BPF_MAP_ALIAS,
    BPF_MAP_ALIASSEQ,
];

/// Map names the object must declare, in declaration order.
pub const BPF_MAP_NAMES: [&str; 9] = [
    BPF_MAP_CONFIG.name,
    BPF_MAP_COUNTERS.name,
    BPF_MAP_SEQUENCE.name,
    BPF_MAP_CALLID.name,
    BPF_MAP_EVENTS.name,
    BPF_MAP_INFLIGHT.name,
    BPF_MAP_DEPTH.name,
    BPF_MAP_ALIAS.name,
    BPF_MAP_ALIASSEQ.name,
];

/// Why a declaration set cannot be priced as a logical declaration total.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BpfBudgetError {
    /// The set does not declare a map the object must contain.
    Missing(&'static str),
    /// The set declares the same map name twice.
    Duplicate(&'static str),
    /// The set declares a map the object does not contain.
    Unexpected(&'static str),
    /// The map type has no pricing rule in this revision.
    UnsupportedType {
        /// Name of the offending declaration.
        name: &'static str,
        /// Declared `bpf_map_type`.
        map_type: u32,
    },
    /// The key/value/entries shape contradicts the declared map type.
    BadShape {
        /// Name of the offending declaration.
        name: &'static str,
    },
    /// The declared sum overflowed `u64`.
    Overflow,
    /// The declared sum exceeds `BPF_ALLOC_DECLARED_BYTES`.
    OverLimit {
        /// Sum of the declarations.
        total: u64,
        /// Declaration limit that was exceeded.
        limit: u64,
    },
}

/// Byte-identical string comparison usable from `const fn`.
const fn name_eq(a: &str, b: &str) -> bool {
    let a = a.as_bytes();
    let b = b.as_bytes();
    if a.len() != b.len() {
        return false;
    }
    let mut i = 0;
    while i < a.len() {
        if a[i] != b[i] {
            return false;
        }
        i += 1;
    }
    true
}

/// Why one declaration cannot be priced from its raw fields.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MapPriceError {
    /// The map type has no pricing rule in this revision.
    UnsupportedType(u32),
    /// The key/value/entries shape contradicts the declared map type.
    BadShape,
    /// The declared byte count overflowed `u64`.
    Overflow,
}

/// Bytes one declaration asks for, priced from its raw fields.
///
/// This is the single pricing rule shared by the compile-time table and by the
/// map section actually embedded in the object: a ring buffer counts its
/// capacity and must declare no key/value, a hash or array map counts
/// `max_entries * (key_size + value_size)`, and any other type is refused
/// instead of being priced as zero.
pub const fn map_declared_bytes(
    map_type: u32,
    key_size: u32,
    value_size: u32,
    max_entries: u32,
) -> Result<u64, MapPriceError> {
    match map_type {
        BPF_MAP_TYPE_RINGBUF => {
            if key_size != 0
                || value_size != 0
                || max_entries == 0
                || (max_entries & (max_entries - 1)) != 0
            {
                return Err(MapPriceError::BadShape);
            }
            Ok(max_entries as u64)
        }
        BPF_MAP_TYPE_ARRAY | BPF_MAP_TYPE_HASH => {
            if key_size == 0 || value_size == 0 || max_entries == 0 {
                return Err(MapPriceError::BadShape);
            }
            let per_entry = match (key_size as u64).checked_add(value_size as u64) {
                Some(sum) => sum,
                None => return Err(MapPriceError::Overflow),
            };
            match per_entry.checked_mul(max_entries as u64) {
                Some(bytes) => Ok(bytes),
                None => Err(MapPriceError::Overflow),
            }
        }
        other => Err(MapPriceError::UnsupportedType(other)),
    }
}

/// Bytes one table declaration asks for, with the map name in the error.
pub const fn declared_map_bytes(decl: &BpfMapDecl) -> Result<u64, BpfBudgetError> {
    match map_declared_bytes(
        decl.map_type,
        decl.key_size,
        decl.value_size,
        decl.max_entries,
    ) {
        Ok(bytes) => Ok(bytes),
        Err(MapPriceError::UnsupportedType(map_type)) => Err(BpfBudgetError::UnsupportedType {
            name: decl.name,
            map_type,
        }),
        Err(MapPriceError::BadShape) => Err(BpfBudgetError::BadShape { name: decl.name }),
        Err(MapPriceError::Overflow) => Err(BpfBudgetError::Overflow),
    }
}

/// Total logical declared map bytes of `decls`.
///
/// The set must declare exactly the nine maps of the embedded object, every
/// declaration must be priceable, the sum must not overflow, and it must stay
/// within `BPF_ALLOC_DECLARED_BYTES`.  A missing map is an error, never a
/// smaller total.
pub const fn declared_bpf_map_bytes(decls: &[BpfMapDecl]) -> Result<u64, BpfBudgetError> {
    let mut i = 0;
    while i < decls.len() {
        let name = decls[i].name;
        let mut known = false;
        let mut n = 0;
        while n < BPF_MAP_NAMES.len() {
            if name_eq(name, BPF_MAP_NAMES[n]) {
                known = true;
            }
            n += 1;
        }
        if !known {
            return Err(BpfBudgetError::Unexpected(name));
        }
        let mut j = i + 1;
        while j < decls.len() {
            if name_eq(decls[j].name, name) {
                return Err(BpfBudgetError::Duplicate(name));
            }
            j += 1;
        }
        i += 1;
    }
    let mut want = 0;
    while want < BPF_MAP_NAMES.len() {
        let mut found = false;
        let mut m = 0;
        while m < decls.len() {
            if name_eq(decls[m].name, BPF_MAP_NAMES[want]) {
                found = true;
            }
            m += 1;
        }
        if !found {
            return Err(BpfBudgetError::Missing(BPF_MAP_NAMES[want]));
        }
        want += 1;
    }
    let mut total = 0u64;
    let mut k = 0;
    while k < decls.len() {
        let bytes = match declared_map_bytes(&decls[k]) {
            Ok(bytes) => bytes,
            Err(error) => return Err(error),
        };
        total = match total.checked_add(bytes) {
            Some(sum) => sum,
            None => return Err(BpfBudgetError::Overflow),
        };
        k += 1;
    }
    if total > BPF_ALLOC_DECLARED_BYTES {
        return Err(BpfBudgetError::OverLimit {
            total,
            limit: BPF_ALLOC_DECLARED_BYTES,
        });
    }
    Ok(total)
}

/// Logical declared map bytes of the embedded object: 1,082,852 B.
///
/// Computed from `BPF_MAP_DECLS` at compile time, so a table that omits a map,
/// carries an unsupported type, overflows or exceeds `BPF_ALLOC_DECLARED_BYTES`
/// fails the build instead of reporting a smaller number.
pub const BPF_MAP_DECLARED_BYTES: u64 = match declared_bpf_map_bytes(&BPF_MAP_DECLS) {
    Ok(bytes) => bytes,
    Err(_) => panic!("the BPF map declaration table is not priceable"),
};

// ---------------------------------------------------------------------------
// Embedded-object map metadata
//
// The total above is only trustworthy if the object that is actually loaded
// declares those nine maps.  The loader parses the embedded object's own
// `maps` section before aya sees it and refuses everything that does not price
// to the same declaration total.
// ---------------------------------------------------------------------------

/// Size of one legacy `struct bpf_map_def` record in the `maps` section.
const BPF_MAP_DEF_BYTES: u64 = 28;
/// ELF section type of a symbol table.
const ELF_SECTION_SYMTAB: u32 = 2;
/// ELF symbol type of a data object.
const ELF_SYMBOL_OBJECT: u8 = 1;
/// ELF machine id of the BPF architecture.
const ELF_MACHINE_BPF: u16 = 247;
/// One ELF64 section header entry is 64 bytes.
const ELF_SECTION_HEADER_BYTES: u16 = 64;
/// One ELF64 symbol table entry is 24 bytes.
const ELF_SYMBOL_BYTES: u64 = 24;

/// One map declaration parsed out of an embedded object's `maps` section.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ObjectMapDecl {
    /// Map name taken from the object's symbol table.
    pub name: String,
    /// Declared `bpf_map_type`.
    pub map_type: u32,
    /// Declared key size in bytes.
    pub key_size: u32,
    /// Declared value size in bytes.
    pub value_size: u32,
    /// Declared maximum entries (capacity for a ring buffer).
    pub max_entries: u32,
}

/// Why an embedded object's map declarations are refused before load.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MapObjectError {
    /// The image is not a little-endian ELF64 relocatable BPF object.
    Elf(&'static str),
    /// A required section is missing or malformed.
    Section(&'static str),
    /// The `maps` section is not a whole number of declarations.
    Truncated,
    /// A symbol or declaration cannot be read.
    Malformed(&'static str),
    /// The object does not declare a map the table requires.
    MissingMap(String),
    /// The object declares a map the table does not know.
    UnexpectedMap(String),
    /// A declaration cannot be priced.
    Unpriceable {
        /// Name of the offending declaration.
        name: String,
        /// Why the declaration cannot be priced.
        reason: MapPriceError,
    },
    /// A declaration field differs from the table entry of the same name.
    FieldMismatch {
        /// Map name.
        name: String,
        /// Field name.
        field: &'static str,
        /// Value declared by the object.
        object: u32,
        /// Value the table (and therefore the report) uses.
        table: u32,
    },
    /// The declared sum overflowed `u64`.
    Overflow,
    /// The declared sum exceeds `BPF_ALLOC_DECLARED_BYTES`.
    OverLimit {
        /// Sum of the declarations.
        total: u64,
        /// Declaration limit that was exceeded.
        limit: u64,
    },
}

impl std::fmt::Display for MapPriceError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            MapPriceError::UnsupportedType(map_type) => {
                write!(f, "unsupported map type {map_type}")
            }
            MapPriceError::BadShape => write!(f, "key/value/entries shape contradicts the type"),
            MapPriceError::Overflow => write!(f, "declared bytes overflow u64"),
        }
    }
}

impl std::fmt::Display for MapObjectError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            MapObjectError::Elf(reason) => write!(f, "not a usable BPF object: {reason}"),
            MapObjectError::Section(reason) => write!(f, "object section problem: {reason}"),
            MapObjectError::Truncated => write!(f, "maps section is not a whole number of records"),
            MapObjectError::Malformed(reason) => write!(f, "object metadata problem: {reason}"),
            MapObjectError::MissingMap(name) => write!(f, "object does not declare map {name}"),
            MapObjectError::UnexpectedMap(name) => write!(f, "object declares unknown map {name}"),
            MapObjectError::Unpriceable { name, reason } => {
                write!(f, "map {name} cannot be priced: {reason}")
            }
            MapObjectError::FieldMismatch {
                name,
                field,
                object,
                table,
            } => write!(
                f,
                "map {name} {field} is {object} in the object but {table} in the table"
            ),
            MapObjectError::Overflow => write!(f, "declared bytes overflow u64"),
            MapObjectError::OverLimit { total, limit } => {
                write!(f, "declared {total} bytes exceeds the {limit} byte limit")
            }
        }
    }
}

fn object_u16(image: &[u8], offset: usize) -> Option<u16> {
    Some(u16::from_le_bytes(
        image.get(offset..offset.checked_add(2)?)?.try_into().ok()?,
    ))
}

fn object_u32(image: &[u8], offset: usize) -> Option<u32> {
    Some(u32::from_le_bytes(
        image.get(offset..offset.checked_add(4)?)?.try_into().ok()?,
    ))
}

fn object_u64(image: &[u8], offset: usize) -> Option<u64> {
    Some(u64::from_le_bytes(
        image.get(offset..offset.checked_add(8)?)?.try_into().ok()?,
    ))
}

/// One ELF64 section header, as far as the map parser needs it.
#[derive(Debug, Clone, Copy)]
struct ObjectSection {
    name_offset: u32,
    section_type: u32,
    offset: u64,
    size: u64,
    link: u32,
    entry_size: u64,
}

fn object_section(
    image: &[u8],
    table_offset: u64,
    entry_size: u16,
    index: usize,
) -> Option<ObjectSection> {
    let entry_size = usize::from(entry_size);
    if entry_size < usize::from(ELF_SECTION_HEADER_BYTES) {
        return None;
    }
    let base = usize::try_from(table_offset)
        .ok()?
        .checked_add(index.checked_mul(entry_size)?)?;
    if base.checked_add(usize::from(ELF_SECTION_HEADER_BYTES))? > image.len() {
        return None;
    }
    Some(ObjectSection {
        name_offset: object_u32(image, base)?,
        section_type: object_u32(image, base + 4)?,
        offset: object_u64(image, base + 24)?,
        size: object_u64(image, base + 32)?,
        link: object_u32(image, base + 40)?,
        entry_size: object_u64(image, base + 56)?,
    })
}

fn object_section_bytes<'a>(image: &'a [u8], section: &ObjectSection) -> Option<&'a [u8]> {
    let start = usize::try_from(section.offset).ok()?;
    let length = usize::try_from(section.size).ok()?;
    image.get(start..start.checked_add(length)?)
}

fn object_name(strings: &[u8], name_offset: u32) -> Option<&str> {
    let rest = strings.get(usize::try_from(name_offset).ok()?..)?;
    let end = rest.iter().position(|byte| *byte == 0)?;
    std::str::from_utf8(&rest[..end]).ok()
}

/// Parse the map declarations of the embedded object's own `maps` section.
///
/// Only object metadata is read (section headers, the `maps` section and the
/// `.symtab`/string table that names its records); nothing is loaded,
/// attached or written.
pub fn object_map_declarations(image: &[u8]) -> Result<Vec<ObjectMapDecl>, MapObjectError> {
    if !image.starts_with(b"\x7fELF") || image.len() < 64 {
        return Err(MapObjectError::Elf("no ELF magic"));
    }
    if image[4] != 2 || image[5] != 1 || image[6] != 1 {
        return Err(MapObjectError::Elf("not a little-endian ELF64 image"));
    }
    if object_u16(image, 16) != Some(1) {
        return Err(MapObjectError::Elf("not a relocatable object"));
    }
    if object_u16(image, 18) != Some(ELF_MACHINE_BPF) {
        return Err(MapObjectError::Elf("not a BPF object"));
    }
    let table_offset =
        object_u64(image, 40).ok_or(MapObjectError::Elf("missing section table offset"))?;
    let table_entry_size =
        object_u16(image, 58).ok_or(MapObjectError::Elf("missing section entry size"))?;
    let section_count =
        object_u16(image, 60).ok_or(MapObjectError::Elf("missing section count"))?;
    let names_index =
        object_u16(image, 62).ok_or(MapObjectError::Elf("missing section name index"))?;
    if section_count == 0 || section_count > 4096 {
        return Err(MapObjectError::Section("section count is out of range"));
    }
    if names_index >= section_count {
        return Err(MapObjectError::Section(
            "section name table index is out of range",
        ));
    }
    let names = object_section(
        image,
        table_offset,
        table_entry_size,
        usize::from(names_index),
    )
    .ok_or(MapObjectError::Section("section name table header"))?;
    let names = object_section_bytes(image, &names)
        .ok_or(MapObjectError::Section("section name table bytes"))?;
    let mut maps = None;
    let mut symtab = None;
    for index in 0..usize::from(section_count) {
        let section = object_section(image, table_offset, table_entry_size, index)
            .ok_or(MapObjectError::Section("section header"))?;
        let name = object_name(names, section.name_offset)
            .ok_or(MapObjectError::Section("section name text"))?;
        if name == "maps" {
            maps = Some((index, section));
        } else if name == ".symtab" {
            symtab = Some(section);
        }
    }
    let (maps_index, maps) = maps.ok_or(MapObjectError::Section("maps section is missing"))?;
    let symtab = symtab.ok_or(MapObjectError::Section(".symtab section is missing"))?;
    if symtab.section_type != ELF_SECTION_SYMTAB {
        return Err(MapObjectError::Section(".symtab has the wrong type"));
    }
    if symtab.entry_size != ELF_SYMBOL_BYTES {
        return Err(MapObjectError::Section(".symtab has the wrong entry size"));
    }
    let strings = object_section(
        image,
        table_offset,
        table_entry_size,
        usize::try_from(symtab.link).map_err(|_| MapObjectError::Section(".symtab link"))?,
    )
    .ok_or(MapObjectError::Section(".symtab string table header"))?;
    let strings = object_section_bytes(image, &strings)
        .ok_or(MapObjectError::Section(".symtab string table bytes"))?;
    let maps_bytes =
        object_section_bytes(image, &maps).ok_or(MapObjectError::Section("maps section bytes"))?;
    if !(maps_bytes.len() as u64).is_multiple_of(BPF_MAP_DEF_BYTES) {
        return Err(MapObjectError::Truncated);
    }
    let symbol_count = symtab.size / ELF_SYMBOL_BYTES;
    if symbol_count == 0 || symbol_count > 65_536 {
        return Err(MapObjectError::Section(".symtab size is out of range"));
    }
    let symbol_table =
        usize::try_from(symtab.offset).map_err(|_| MapObjectError::Section(".symtab offset"))?;
    let mut found: Vec<(u64, ObjectMapDecl)> = Vec::new();
    let mut index = 0u64;
    while index < symbol_count {
        let base = symbol_table
            .checked_add(
                usize::try_from(index * ELF_SYMBOL_BYTES)
                    .map_err(|_| MapObjectError::Malformed("symbol offset"))?,
            )
            .ok_or(MapObjectError::Malformed("symbol offset"))?;
        let end = base
            .checked_add(usize::try_from(ELF_SYMBOL_BYTES).expect("symbol size fits"))
            .ok_or(MapObjectError::Malformed("symbol offset"))?;
        if end > image.len() {
            return Err(MapObjectError::Malformed("symbol entry is out of range"));
        }
        let name_offset =
            object_u32(image, base).ok_or(MapObjectError::Malformed("symbol name"))?;
        let info = image
            .get(base + 4)
            .copied()
            .ok_or(MapObjectError::Malformed("symbol info"))?;
        let section_index =
            object_u16(image, base + 6).ok_or(MapObjectError::Malformed("symbol section"))?;
        let value = object_u64(image, base + 8).ok_or(MapObjectError::Malformed("symbol value"))?;
        let size = object_u64(image, base + 16).ok_or(MapObjectError::Malformed("symbol size"))?;
        index += 1;
        if usize::from(section_index) != maps_index {
            continue;
        }
        if info & 0x0f != ELF_SYMBOL_OBJECT {
            return Err(MapObjectError::Malformed("maps symbol is not an object"));
        }
        if size != BPF_MAP_DEF_BYTES {
            return Err(MapObjectError::Malformed("maps symbol has the wrong size"));
        }
        if value % BPF_MAP_DEF_BYTES != 0 || value + BPF_MAP_DEF_BYTES > maps.size {
            return Err(MapObjectError::Malformed(
                "maps symbol offset is out of range",
            ));
        }
        let name =
            object_name(strings, name_offset).ok_or(MapObjectError::Malformed("map name text"))?;
        let record =
            usize::try_from(value).map_err(|_| MapObjectError::Malformed("record offset"))?;
        let decl = ObjectMapDecl {
            name: name.to_string(),
            map_type: object_u32(maps_bytes, record)
                .ok_or(MapObjectError::Malformed("map type"))?,
            key_size: object_u32(maps_bytes, record + 4)
                .ok_or(MapObjectError::Malformed("map key size"))?,
            value_size: object_u32(maps_bytes, record + 8)
                .ok_or(MapObjectError::Malformed("map value size"))?,
            max_entries: object_u32(maps_bytes, record + 12)
                .ok_or(MapObjectError::Malformed("map entries"))?,
        };
        if object_u32(maps_bytes, record + 16) != Some(0)
            || object_u32(maps_bytes, record + 20) != Some(0)
            || object_u32(maps_bytes, record + 24) != Some(0)
        {
            return Err(MapObjectError::Malformed(
                "map flags/id/pinning are not zero",
            ));
        }
        if found
            .iter()
            .any(|(_, existing): &(u64, ObjectMapDecl)| existing.name == decl.name)
        {
            return Err(MapObjectError::UnexpectedMap(decl.name));
        }
        found.push((value, decl));
    }
    found.sort_by_key(|(offset, _)| *offset);
    let declarations: Vec<ObjectMapDecl> = found.into_iter().map(|(_, decl)| decl).collect();
    if declarations.len() as u64 != maps.size / BPF_MAP_DEF_BYTES {
        return Err(MapObjectError::Section(
            "maps section is not fully declared",
        ));
    }
    Ok(declarations)
}

/// Declared logical map bytes of an embedded object, validated before load.
///
/// The order of the checks is: the object must parse; it must declare exactly
/// the nine table maps; every declaration must be priceable (unsupported type,
/// bad shape and overflow are refused); the sum must stay within
/// `BPF_ALLOC_DECLARED_BYTES`; and every field must agree with the table that
/// the closeout prints.
pub fn object_declared_bpf_map_bytes(image: &[u8]) -> Result<u64, MapObjectError> {
    let declarations = object_map_declarations(image)?;
    for decl in &declarations {
        if !BPF_MAP_DECLS.iter().any(|known| known.name == decl.name) {
            return Err(MapObjectError::UnexpectedMap(decl.name.clone()));
        }
    }
    for known in &BPF_MAP_DECLS {
        if !declarations.iter().any(|decl| decl.name == known.name) {
            return Err(MapObjectError::MissingMap(known.name.to_string()));
        }
    }
    let mut total = 0u64;
    for decl in &declarations {
        let bytes = map_declared_bytes(
            decl.map_type,
            decl.key_size,
            decl.value_size,
            decl.max_entries,
        )
        .map_err(|reason| MapObjectError::Unpriceable {
            name: decl.name.clone(),
            reason,
        })?;
        total = total.checked_add(bytes).ok_or(MapObjectError::Overflow)?;
    }
    if total > BPF_ALLOC_DECLARED_BYTES {
        return Err(MapObjectError::OverLimit {
            total,
            limit: BPF_ALLOC_DECLARED_BYTES,
        });
    }
    for known in &BPF_MAP_DECLS {
        let decl = declarations
            .iter()
            .find(|decl| decl.name == known.name)
            .ok_or_else(|| MapObjectError::MissingMap(known.name.to_string()))?;
        let fields = [
            ("type", known.map_type, decl.map_type),
            ("key_size", known.key_size, decl.key_size),
            ("value_size", known.value_size, decl.value_size),
            ("max_entries", known.max_entries, decl.max_entries),
        ];
        for (field, table, object) in fields {
            if table != object {
                return Err(MapObjectError::FieldMismatch {
                    name: known.name.to_string(),
                    field,
                    object,
                    table,
                });
            }
        }
    }
    Ok(total)
}

/// One decoded BPF record, header only.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WireEvent {
    /// Probe timestamp in BOOTTIME nanoseconds.
    pub ts_ns: u64,
    /// Monotonic record sequence inside the session.
    pub sequence: u64,
    /// Per-entry call identifier used to pair entry and exit.
    pub call_id: u64,
    /// (tgid << 32) | tid of the probed thread.
    pub actor_pid_tid: u64,
    /// Group-leader start time in BOOTTIME nanoseconds.
    pub actor_start_ns: u64,
    /// Kernel cgroup id.
    pub actor_cgroup: u64,
    /// (tgid << 32) | tid inside the bound pid namespace.
    pub namespace_pid_tid: u64,
    /// Pid-namespace inode the probe resolved against.
    pub namespace_inode: u64,
    /// The num argument of SSL_write, signed low 32 bits.
    pub declared: i64,
    /// Return value of SSL_write, signed low 32 bits (exit records only).
    pub result: i64,
    /// Run-local opaque alias of the SSL object.
    pub alias: u32,
    /// Number of payload bytes captured in this record.
    pub captured: u32,
    /// KIND_ENTER or KIND_EXIT.
    pub kind: u32,
    /// Bit set of FLAG_*.
    pub flags: u32,
    /// CPU the probe ran on.
    pub cpu: u32,
    /// Effective uid of the probed task.
    pub uid: u32,
    /// Nesting depth of the call.
    pub depth: u32,
    /// Reserved, must be zero.
    pub spare: u32,
}

/// A fully decoded record: header plus the zero-initialised sample area.
#[derive(Debug, Clone)]
pub struct Record {
    /// Decoded header.
    pub wire: WireEvent,
    /// Fixed zero-initialised sample area; only wire.captured bytes are valid.
    pub payload: [u8; SAMPLE_CAP],
}

impl Record {
    /// The valid prefix of the sample area.
    pub fn payload(&self) -> &[u8] {
        &self.payload[..self.wire.captured as usize]
    }
    /// Whether the record carries a truncated positive read.
    pub fn truncated(&self) -> bool {
        self.wire.flags & FLAG_TRUNCATED != 0
    }
    /// Whether the record performed a user-buffer read.
    pub fn read_attempted(&self) -> bool {
        self.wire.flags & FLAG_USER_READ != 0
    }
    /// Whether the record refused to touch user memory because len <= 0.
    pub fn len_invalid(&self) -> bool {
        self.wire.flags & FLAG_LEN_INVALID != 0
    }
    /// Whether the bounded user read failed.
    pub fn read_failed(&self) -> bool {
        self.wire.flags & FLAG_READ_FAILED != 0
    }
}

/// Reasons a wire record can be rejected. None of them echo payload bytes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DecodeError {
    /// The slice did not have exactly RING_RECORD bytes.
    Length(usize),
    /// The record kind was neither entry nor exit.
    Kind(u32),
    /// captured exceeded the sample cap.
    Captured(u32),
    /// The reserved field was not zero.
    Reserved(u32),
}

impl std::fmt::Display for DecodeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DecodeError::Length(n) => write!(f, "record length {n} is not {RING_RECORD}"),
            DecodeError::Kind(k) => write!(f, "unknown record kind {k}"),
            DecodeError::Captured(n) => write!(f, "captured {n} exceeds {SAMPLE_CAP}"),
            DecodeError::Reserved(n) => write!(f, "reserved field {n} is not zero"),
        }
    }
}

/// Decode one ring-buffer record into a header plus a zero-filled sample area.
///
/// The sample area is always fully zeroed first and then filled with exactly
/// captured bytes, so no stale memory can be interpreted.
pub fn decode_record(bytes: &[u8]) -> Result<Record, DecodeError> {
    if bytes.len() != RING_RECORD {
        return Err(DecodeError::Length(bytes.len()));
    }
    let q = |o: usize| u64::from_le_bytes(bytes[o..o + 8].try_into().unwrap());
    let d = |o: usize| u32::from_le_bytes(bytes[o..o + 4].try_into().unwrap());
    let wire = WireEvent {
        ts_ns: q(0),
        sequence: q(8),
        call_id: q(16),
        actor_pid_tid: q(24),
        actor_start_ns: q(32),
        actor_cgroup: q(40),
        namespace_pid_tid: q(48),
        namespace_inode: q(56),
        declared: q(64) as i64,
        result: q(72) as i64,
        alias: d(80),
        captured: d(84),
        kind: d(88),
        flags: d(92),
        cpu: d(96),
        uid: d(100),
        depth: d(104),
        spare: d(108),
    };
    if wire.kind != KIND_ENTER && wire.kind != KIND_EXIT {
        return Err(DecodeError::Kind(wire.kind));
    }
    if wire.captured as usize > SAMPLE_CAP {
        return Err(DecodeError::Captured(wire.captured));
    }
    if wire.spare != 0 {
        return Err(DecodeError::Reserved(wire.spare));
    }
    let mut payload = [0u8; SAMPLE_CAP];
    let captured = wire.captured as usize;
    payload[..captured].copy_from_slice(&bytes[RECORD_HEADER..RECORD_HEADER + captured]);
    Ok(Record { wire, payload })
}

/// One SSL_write call reconstructed from an entry/exit pair.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CallRow {
    /// Call identifier assigned by the BPF entry probe.
    pub call_id: u64,
    /// Run-local SSL object alias.
    pub alias: u32,
    /// Declared length from the entry record.
    pub declared: i64,
    /// Captured payload length.
    pub captured: u32,
    /// Entry flags.
    pub flags: u32,
    /// Nesting depth recorded at entry.
    pub depth: u32,
    /// Return value, if the matching exit was observed.
    pub result: Option<i64>,
    /// Sequence number of the entry record.
    pub entry_sequence: u64,
}

impl CallRow {
    /// Whether the declaration exceeded the cap.
    pub fn truncated(&self) -> bool {
        self.flags & FLAG_TRUNCATED != 0
    }
    /// Whether the entry performed a user read.
    pub fn read_attempted(&self) -> bool {
        self.flags & FLAG_USER_READ != 0
    }
    /// Whether the entry saw declared <= 0.
    pub fn len_invalid(&self) -> bool {
        self.flags & FLAG_LEN_INVALID != 0
    }
    /// Whether the return value exceeded the declaration.
    pub fn abnormal_ret(&self) -> bool {
        matches!(self.result, Some(r) if r > self.declared && self.declared > 0)
    }
    /// Whether the return value was positive but short.
    pub fn partial_write(&self) -> bool {
        matches!(self.result, Some(r) if r > 0 && r < self.declared)
    }
}

/// Result of pairing a session's records.
#[derive(Debug, Clone, Default)]
pub struct Analysis {
    /// Number of entry records.
    pub entries: usize,
    /// Number of exit records.
    pub exits: usize,
    /// Calls in entry order.
    pub rows: Vec<CallRow>,
    /// Entries that never saw an exit.
    pub expired: usize,
    /// Exits without a matching entry.
    pub unpaired: usize,
    /// Exits that reused an already-completed call id.
    pub duplicate_exits: usize,
    /// Enter/exit pairs observed at a non-zero depth.
    pub nested: usize,
    /// Calls whose return value exceeded the declaration.
    pub abnormal_ret: usize,
    /// Calls whose return value was short.
    pub partial: usize,
}

impl Analysis {
    /// Distinct SSL object aliases, sorted ascending.
    pub fn aliases(&self) -> Vec<u32> {
        let mut v: Vec<u32> = self.rows.iter().map(|r| r.alias).collect();
        v.sort_unstable();
        v.dedup();
        v
    }
    /// Total captured payload bytes accepted in this session.
    pub fn payload_bytes(&self) -> u64 {
        self.rows.iter().map(|r| u64::from(r.captured)).sum()
    }
}

/// Pair entry and exit records in arrival order.
pub fn analyze(records: &[Record]) -> Analysis {
    let mut a = Analysis::default();
    for r in records {
        if r.wire.kind == KIND_ENTER {
            a.entries += 1;
            if r.wire.depth != 0 {
                a.nested += 1;
            }
            a.rows.push(CallRow {
                call_id: r.wire.call_id,
                alias: r.wire.alias,
                declared: r.wire.declared,
                captured: r.wire.captured,
                flags: r.wire.flags,
                depth: r.wire.depth,
                result: None,
                entry_sequence: r.wire.sequence,
            });
        } else {
            a.exits += 1;
            let hit = a
                .rows
                .iter_mut()
                .rev()
                .find(|row| row.call_id == r.wire.call_id && row.result.is_none());
            match hit {
                Some(row) => {
                    row.result = Some(r.wire.result);
                    if row.abnormal_ret() {
                        a.abnormal_ret += 1;
                    }
                    if row.partial_write() {
                        a.partial += 1;
                    }
                }
                None => {
                    if a.rows.iter().any(|row| row.call_id == r.wire.call_id) {
                        a.duplicate_exits += 1;
                    } else {
                        a.unpaired += 1;
                    }
                }
            }
        }
    }
    a.expired = a.rows.iter().filter(|r| r.result.is_none()).count();
    a
}

/// Named counters in the contract classification.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CounterReport {
    /// Records the BPF object submitted successfully.
    pub submitted: u64,
    /// Records the userspace collector decoded.
    pub consumed: u64,
    /// Records lost anywhere between reserve and decode.
    pub lost: u64,
    /// Bounded user reads that failed.
    pub read_failed: u64,
    /// Map update/delete failures.
    pub map_failed: u64,
    /// Entries with no exit before the session closed.
    pub expired: u64,
    /// Records marked truncated.
    pub truncated: u64,
    /// Probe hits rejected by identity checks.
    pub identity_rejected: u64,
    /// Declarations <= 0.
    pub len_invalid: u64,
    /// Returns larger than the declaration.
    pub abnormal_ret: u64,
    /// User-buffer read attempts.
    pub user_read_attempts: u64,
    /// User bytes read.
    pub user_read_bytes: u64,
    /// Captured payload bytes accepted.
    pub payload_bytes: u64,
    /// Nesting overflows.
    pub depth_overflow: u64,
    /// Exits without a matching entry.
    pub unpaired: u64,
    /// Entry records observed.
    pub entries: u64,
    /// Exit records observed.
    pub exits: u64,
}

/// Build the named counter report from raw BPF counters and the analysis.
///
/// lost is not just submitted-consumed: it also includes reserve failures and
/// sequence overflows, so equal counts alone can never fake a pass.
pub fn counter_report(raw: &[u64], analysis: &Analysis) -> CounterReport {
    let at = |i: u32| raw.get(i as usize).copied().unwrap_or(0);
    let submitted = at(CNT_SUBMITTED);
    let consumed = analysis.entries as u64 + analysis.exits as u64;
    let dropped = submitted.saturating_sub(consumed);
    CounterReport {
        submitted,
        consumed,
        lost: at(CNT_RESERVE_FAILED) + at(CNT_SEQ_OVERFLOW) + dropped,
        read_failed: at(CNT_READ_FAILED),
        map_failed: at(CNT_MAP_FAILED),
        expired: analysis.expired as u64,
        truncated: at(CNT_TRUNCATED),
        identity_rejected: at(CNT_IDENTITY_REJECTED),
        len_invalid: at(CNT_LEN_INVALID),
        abnormal_ret: analysis.abnormal_ret as u64,
        user_read_attempts: at(CNT_USER_READ_ATTEMPTS),
        user_read_bytes: at(CNT_USER_READ_BYTES),
        payload_bytes: analysis.payload_bytes(),
        depth_overflow: at(CNT_DEPTH_OVERFLOW),
        unpaired: analysis.unpaired as u64 + at(CNT_UNPAIRED),
        entries: at(CNT_ENTRY),
        exits: at(CNT_EXIT),
    }
}

/// One byte of the frozen synthetic payload.
pub fn payload_byte(index: usize) -> u8 {
    if index < 7 {
        b"TLSFIX1"[index]
    } else if index == 7 {
        0
    } else {
        (0x54u64.wrapping_add(7 * index as u64) & 0xFF) as u8
    }
}

/// The frozen synthetic payload for len bytes.
pub fn expected_payload(len: usize) -> Vec<u8> {
    (0..len).map(payload_byte).collect()
}

/// Compare a captured prefix against the frozen payload formula.
pub fn payload_matches(captured: &[u8], declared: i64) -> bool {
    if declared <= 0 {
        return captured.is_empty();
    }
    if captured.len() as i64 > declared {
        return false;
    }
    captured
        .iter()
        .enumerate()
        .all(|(i, b)| *b == payload_byte(i))
}

/// Result of binding one decoded call to one script step.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RequestView {
    /// 1-based script index.
    pub index: usize,
    /// Direction string from the frozen script.
    pub direction: &'static str,
    /// Declared length.
    pub declared: i64,
    /// Captured bytes.
    pub captured: u32,
    /// Whether the capture was truncated by the cap.
    pub truncated: bool,
    /// Whether the probe touched user memory.
    pub read_attempted: bool,
    /// Return value, if seen.
    pub ret: i64,
    /// Whether the captured prefix matches the frozen formula.
    pub payload_match: bool,
    /// Run-local SSL object alias.
    pub alias: u32,
    /// Nesting depth.
    pub depth: u32,
}

/// Bind calls to the frozen script, in entry order.
pub fn bind_script(rows: &[CallRow], records: &[Record]) -> Result<Vec<RequestView>, &'static str> {
    if rows.len() != SCRIPT_LENGTHS.len() {
        return Err("script arity mismatch");
    }
    let mut out = Vec::with_capacity(rows.len());
    for (i, row) in rows.iter().enumerate() {
        if row.declared != SCRIPT_LENGTHS[i] {
            return Err("declared length deviates from the frozen script");
        }
        let ret = row.result.ok_or("call has no return record")?;
        let payload = records
            .iter()
            .find(|r| r.wire.kind == KIND_ENTER && r.wire.call_id == row.call_id)
            .map(|r| r.payload())
            .ok_or("entry payload missing")?;
        out.push(RequestView {
            index: i + 1,
            direction: SCRIPT_DIRECTIONS[i],
            declared: row.declared,
            captured: row.captured,
            truncated: row.truncated(),
            read_attempted: row.read_attempted(),
            ret,
            payload_match: payload_matches(payload, row.declared),
            alias: row.alias,
            depth: row.depth,
        });
    }
    Ok(out)
}

/// Expected positive-session counter values, for exact comparison.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PositiveExpectation {
    /// Entry records.
    pub entries: u64,
    /// Exit records.
    pub exits: u64,
    /// Submitted records.
    pub submitted: u64,
    /// Consumed records.
    pub consumed: u64,
    /// Truncated samples.
    pub truncated: u64,
    /// Invalid declarations.
    pub len_invalid: u64,
    /// User-read attempts.
    pub user_read_attempts: u64,
    /// User bytes read.
    pub user_read_bytes: u64,
    /// Payload bytes accepted.
    pub payload_bytes: u64,
    /// Distinct aliases.
    pub aliases: usize,
}

/// The expectation the contract freezes for the positive session.
pub const POSITIVE_EXPECTATION: PositiveExpectation = PositiveExpectation {
    entries: 4,
    exits: 4,
    submitted: 8,
    consumed: 8,
    truncated: 1,
    len_invalid: 1,
    user_read_attempts: 3,
    user_read_bytes: 4992,
    payload_bytes: 4992,
    aliases: 2,
};

/// Evaluate the positive session against the frozen expectation.
pub fn check_positive(c: &CounterReport, aliases: &[u32]) -> Vec<&'static str> {
    let e = POSITIVE_EXPECTATION;
    let mut bad = Vec::new();
    if c.entries != e.entries {
        bad.push("entry count");
    }
    if c.exits != e.exits {
        bad.push("exit count");
    }
    if c.submitted != e.submitted || c.consumed != e.consumed || c.lost != 0 {
        bad.push("submitted/consumed/lost");
    }
    if c.read_failed != 0 || c.map_failed != 0 || c.expired != 0 || c.unpaired != 0 {
        bad.push("unexpected read/map/expired/unpaired");
    }
    if c.depth_overflow != 0 || c.abnormal_ret != 0 {
        bad.push("unexpected depth overflow or abnormal return");
    }
    if c.truncated != e.truncated || c.len_invalid != e.len_invalid {
        bad.push("truncated/len_invalid");
    }
    if c.user_read_attempts != e.user_read_attempts
        || c.user_read_bytes != e.user_read_bytes
        || c.payload_bytes != e.payload_bytes
    {
        bad.push("user read accounting");
    }
    if aliases.len() != e.aliases {
        bad.push("alias cardinality");
    }
    bad
}

/// gnu_dev_makedev, as glibc encodes it into st_dev.
pub fn gnu_makedev(major: u64, minor: u64) -> u64 {
    (minor & 0xff)
        | ((major & 0xfff) << 8)
        | ((minor & 0xffff_ff00) << 12)
        | ((major & 0xffff_f000) << 32)
}

/// One /proc/<pid>/maps line.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MapsEntry {
    /// Start address.
    pub start: u64,
    /// End address.
    pub end: u64,
    /// Permission string (rwxp).
    pub perms: String,
    /// File offset.
    pub offset: u64,
    /// Device major.
    pub dev_major: u32,
    /// Device minor.
    pub dev_minor: u32,
    /// Inode.
    pub inode: u64,
    /// Mapped path, empty for anonymous mappings.
    pub path: String,
}

impl MapsEntry {
    /// Whether this mapping is executable.
    pub fn executable(&self) -> bool {
        self.perms.as_bytes().get(2) == Some(&b'x')
    }
    /// st_dev value this mapping device encodes to.
    pub fn st_dev(&self) -> u64 {
        gnu_makedev(u64::from(self.dev_major), u64::from(self.dev_minor))
    }
}

/// Parse a single /proc/<pid>/maps line.
pub fn parse_maps_line(line: &str) -> Result<MapsEntry, &'static str> {
    let mut parts = line.splitn(6, ' ');
    let range = parts.next().ok_or("maps range missing")?;
    let perms = parts.next().ok_or("maps perms missing")?;
    let offset = parts.next().ok_or("maps offset missing")?;
    let dev = parts.next().ok_or("maps dev missing")?;
    let inode = parts.next().ok_or("maps inode missing")?;
    let path = parts.next().unwrap_or("").trim().to_string();
    let (start_s, end_s) = range.split_once('-').ok_or("maps range malformed")?;
    let (maj_s, min_s) = dev.split_once(':').ok_or("maps dev malformed")?;
    if perms.len() != 4 {
        return Err("maps perms malformed");
    }
    Ok(MapsEntry {
        start: u64::from_str_radix(start_s, 16).map_err(|_| "maps start malformed")?,
        end: u64::from_str_radix(end_s, 16).map_err(|_| "maps end malformed")?,
        perms: perms.to_string(),
        offset: u64::from_str_radix(offset, 16).map_err(|_| "maps offset malformed")?,
        dev_major: u32::from_str_radix(maj_s, 16).map_err(|_| "maps major malformed")?,
        dev_minor: u32::from_str_radix(min_s, 16).map_err(|_| "maps minor malformed")?,
        inode: inode.parse().map_err(|_| "maps inode malformed")?,
        path,
    })
}

/// Every executable mapping whose path ends with needle.
pub fn exec_mappings(maps: &str, needle: &str) -> Vec<MapsEntry> {
    maps.lines()
        .filter_map(|line| parse_maps_line(line).ok())
        .filter(|e| e.executable() && !e.path.is_empty() && e.path.ends_with(needle))
        .collect()
}

/// Extract the GNU build-id from an ELF64 little-endian image.
pub fn elf_build_id(image: &[u8]) -> Result<Vec<u8>, &'static str> {
    if image.len() < 64 || &image[0..4] != b"\x7fELF" {
        return Err("not an ELF image");
    }
    if image[4] != 2 || image[5] != 1 {
        return Err("only ELF64 little-endian is supported");
    }
    let u16_at = |o: usize| u16::from_le_bytes([image[o], image[o + 1]]);
    let u32_at =
        |o: usize| u32::from_le_bytes([image[o], image[o + 1], image[o + 2], image[o + 3]]);
    let u64_at = |o: usize| {
        let mut b = [0u8; 8];
        b.copy_from_slice(&image[o..o + 8]);
        u64::from_le_bytes(b)
    };
    let shoff = u64_at(0x28) as usize;
    let shentsize = u16_at(0x3a) as usize;
    let shnum = u16_at(0x3c) as usize;
    let shstrndx = u16_at(0x3e) as usize;
    if shentsize < 64 || shnum == 0 || shstrndx >= shnum {
        return Err("ELF section table missing");
    }
    if shoff + shentsize * shnum > image.len() {
        return Err("ELF section table truncated");
    }
    let section = |i: usize| -> (u32, u32, u64, u64) {
        let base = shoff + i * shentsize;
        (
            u32_at(base),
            u32_at(base + 4),
            u64_at(base + 0x18),
            u64_at(base + 0x20),
        )
    };
    let (_, _, str_off, str_size) = section(shstrndx);
    let str_off = str_off as usize;
    let str_size = str_size as usize;
    if str_off + str_size > image.len() {
        return Err("ELF section name table truncated");
    }
    for i in 0..shnum {
        let (name_off, sh_type, off, size) = section(i);
        let name_off = name_off as usize;
        if name_off >= str_size {
            continue;
        }
        let name_bytes = &image[str_off + name_off..str_off + str_size];
        let end = name_bytes
            .iter()
            .position(|b| *b == 0)
            .unwrap_or(name_bytes.len());
        if &name_bytes[..end] != b".note.gnu.build-id" {
            continue;
        }
        if sh_type != 7 {
            return Err("build-id section is not SHT_NOTE");
        }
        let off = off as usize;
        let size = size as usize;
        if off + size > image.len() {
            return Err("build-id section truncated");
        }
        let mut cursor = 0usize;
        while cursor + 12 <= size {
            let namesz = u32_at(off + cursor) as usize;
            let descsz = u32_at(off + cursor + 4) as usize;
            let kind = u32_at(off + cursor + 8);
            let name_start = cursor + 12;
            let name_end = name_start + namesz;
            let desc_start = (name_end + 3) & !3;
            let desc_end = desc_start + descsz;
            if name_end > size || desc_end > size {
                return Err("build-id note truncated");
            }
            if kind == 3 && namesz == 4 && &image[off + name_start..off + name_start + 3] == b"GNU"
            {
                return Ok(image[off + desc_start..off + desc_end].to_vec());
            }
            cursor = (desc_end + 3) & !3;
        }
        return Err("build-id note missing");
    }
    Err("build-id section missing")
}

/// Lower-case hex encoding.
pub fn hex_lower(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

/// Truncate a 64-bit register value to its signed low 32 bits.
///
/// This is the only conversion the probe applies to the num argument and the
/// return value of SSL_write.  The architecture only decides which register
/// the BPF object reads; the arithmetic is identical.
pub fn signed_low32(raw: u64) -> i64 {
    raw as u32 as i32 as i64
}

/// Third argument of SSL_write as decoded from x86_64 rdx.
pub fn x86_64_declared(rdx: u64) -> i64 {
    signed_low32(rdx)
}

/// Return value of SSL_write as decoded from x86_64 rax.
pub fn x86_64_result(rax: u64) -> i64 {
    signed_low32(rax)
}

/// Third argument of SSL_write as decoded from arm64 regs[2].
pub fn arm64_declared(regs2: u64) -> i64 {
    signed_low32(regs2)
}

/// Return value of SSL_write as decoded from arm64 regs[0].
pub fn arm64_result(regs0: u64) -> i64 {
    signed_low32(regs0)
}

/// FNV-1a 64 over a byte slice, as frozen in the contract.
pub fn fnv1a64(bytes: &[u8]) -> u64 {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for b in bytes {
        h ^= u64::from(*b);
        h = h.wrapping_mul(0x0000_0100_0000_01b3);
    }
    h
}

/// Whether a run id has the frozen shape: 16 lower-case hex characters.
pub fn run_id_is_valid(run_id: &str) -> bool {
    run_id.len() == 16
        && run_id
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// The only permitted private run directory for a run id.
pub fn run_dir_path(run_id: &str) -> String {
    format!("/tmp/kebab-observer-tls-fixture-{run_id}")
}

/// Whether a path is exactly the private run directory of run_id.
pub fn run_dir_matches(path: &str, run_id: &str) -> bool {
    run_id_is_valid(run_id) && path == run_dir_path(run_id)
}

/// Parse the CapEff field of /proc/<pid>/status.
pub fn cap_eff(status: &str) -> Result<u64, &'static str> {
    let line = status
        .lines()
        .find(|l| l.starts_with("CapEff:"))
        .ok_or("CapEff missing")?;
    let hex = line.split_whitespace().nth(1).ok_or("CapEff empty")?;
    u64::from_str_radix(hex, 16).map_err(|_| "CapEff malformed")
}

/// Whether a capability bit is present in a CapEff value.
pub fn has_cap(bits: u64, cap: u32) -> bool {
    cap < 64 && (bits >> cap) & 1 == 1
}

/// Parse a numeric field of /proc/<pid>/status such as Threads or PPid.
pub fn status_number(status: &str, key: &str) -> Result<u64, &'static str> {
    let prefix = format!("{key}:");
    let line = status
        .lines()
        .find(|l| l.starts_with(&prefix))
        .ok_or("status field missing")?;
    line[prefix.len()..]
        .trim()
        .parse()
        .map_err(|_| "status field malformed")
}

/// Parse the last value of the NSpid field of /proc/<pid>/status.
pub fn status_nspid(status: &str) -> Result<u64, &'static str> {
    status
        .lines()
        .find(|l| l.starts_with("NSpid:"))
        .ok_or("NSpid missing")?
        .split_whitespace()
        .skip(1)
        .last()
        .ok_or("NSpid empty")?
        .parse()
        .map_err(|_| "NSpid malformed")
}

/// Parse the first uid of the Uid field of /proc/<pid>/status.
pub fn status_uid(status: &str) -> Result<u32, &'static str> {
    let line = status
        .lines()
        .find(|l| l.starts_with("Uid:"))
        .ok_or("Uid missing")?;
    line.split_whitespace()
        .nth(1)
        .ok_or("Uid empty")?
        .parse()
        .map_err(|_| "Uid malformed")
}

/// Parse the first gid of the Gid field of /proc/<pid>/status.
pub fn status_gid(status: &str) -> Result<u32, &'static str> {
    let line = status
        .lines()
        .find(|l| l.starts_with("Gid:"))
        .ok_or("Gid missing")?;
    line.split_whitespace()
        .nth(1)
        .ok_or("Gid empty")?
        .parse()
        .map_err(|_| "Gid malformed")
}

/// Structured gap for a payload that does not match the frozen formula.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PayloadGap {
    /// Script step the gap belongs to.
    pub index: usize,
    /// Declared length.
    pub declared: i64,
    /// Captured length.
    pub captured: u32,
    /// Index of the first mismatching byte, if any.
    pub mismatch_at: Option<usize>,
    /// Per-byte match flags for the first eight bytes.
    pub prefix_match: Vec<bool>,
}

/// Describe a payload mismatch without exposing unknown bytes.
pub fn payload_gap(index: usize, declared: i64, captured: u32, bytes: &[u8]) -> PayloadGap {
    let mismatch_at = bytes
        .iter()
        .enumerate()
        .position(|(i, b)| *b != payload_byte(i));
    let prefix_match = bytes
        .iter()
        .take(8)
        .enumerate()
        .map(|(i, b)| *b == payload_byte(i))
        .collect();
    PayloadGap {
        index,
        declared,
        captured,
        mismatch_at,
        prefix_match,
    }
}

// ---------------------------------------------------------------------------
// SHA-256 (FIPS 180-4) - only used to bind the C workload binary to a digest.
// Implemented here so the observer does not need a new crate.
// ---------------------------------------------------------------------------

const SHA256_K: [u32; 64] = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

/// SHA-256 digest of a byte slice.
#[allow(clippy::needless_range_loop)]
pub fn sha256(data: &[u8]) -> [u8; 32] {
    let mut h: [u32; 8] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
        0x5be0cd19,
    ];
    let bit_len = (data.len() as u64).wrapping_mul(8);
    let mut msg = Vec::with_capacity(data.len() + 72);
    msg.extend_from_slice(data);
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&bit_len.to_be_bytes());
    for chunk in msg.as_chunks::<64>().0 {
        let mut w = [0u32; 64];
        for i in 0..16 {
            w[i] = u32::from_be_bytes([
                chunk[4 * i],
                chunk[4 * i + 1],
                chunk[4 * i + 2],
                chunk[4 * i + 3],
            ]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1);
        }
        let (mut a, mut b, mut c, mut d) = (h[0], h[1], h[2], h[3]);
        let (mut e, mut f, mut g, mut hh) = (h[4], h[5], h[6], h[7]);
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ ((!e) & g);
            let t1 = hh
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(SHA256_K[i])
                .wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(maj);
            hh = g;
            g = f;
            f = e;
            e = d.wrapping_add(t1);
            d = c;
            c = b;
            b = a;
            a = t1.wrapping_add(t2);
        }
        h[0] = h[0].wrapping_add(a);
        h[1] = h[1].wrapping_add(b);
        h[2] = h[2].wrapping_add(c);
        h[3] = h[3].wrapping_add(d);
        h[4] = h[4].wrapping_add(e);
        h[5] = h[5].wrapping_add(f);
        h[6] = h[6].wrapping_add(g);
        h[7] = h[7].wrapping_add(hh);
    }
    let mut out = [0u8; 32];
    for i in 0..8 {
        out[4 * i..4 * i + 4].copy_from_slice(&h[i].to_be_bytes());
    }
    out
}

/// Lower-case hex SHA-256 of a byte slice.
pub fn sha256_hex(data: &[u8]) -> String {
    hex_lower(&sha256(data))
}

// ---------------------------------------------------------------------------
// Verdicts and teardown decisions
// ---------------------------------------------------------------------------

/// Exit code for a passing batch.
pub const EXIT_PASS: u8 = 0;
/// Exit code for a failing batch.
pub const EXIT_FAIL: u8 = 1;
/// Exit code for a rejected invocation.
pub const EXIT_USAGE: u8 = 2;
/// Exit code for a blocked environment.
pub const EXIT_BLOCKED: u8 = 3;
/// Exit code for an incomplete (budget-capped) batch.
pub const EXIT_PARTIAL: u8 = 4;

/// Final verdict of one batch.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verdict {
    /// Every scenario passed, the environment was sufficient, and the budget
    /// was respected.
    Pass,
    /// Every scenario passed but the batch ran past its internal budget.
    Partial,
    /// At least one scenario or teardown step failed.
    Fail,
    /// The host cannot provide a prerequisite; the implementation was not
    /// proven wrong.
    Blocked,
}

impl Verdict {
    /// Stable name used in the closeout record.
    pub fn as_str(self) -> &'static str {
        match self {
            Verdict::Pass => "PASS",
            Verdict::Partial => "PARTIAL",
            Verdict::Fail => "FAIL",
            Verdict::Blocked => "BLOCKED",
        }
    }
    /// Exit code for this verdict.
    pub fn exit_code(self) -> u8 {
        match self {
            Verdict::Pass => EXIT_PASS,
            Verdict::Partial => EXIT_PARTIAL,
            Verdict::Fail => EXIT_FAIL,
            Verdict::Blocked => EXIT_BLOCKED,
        }
    }
    /// Whether this verdict counts as a passing batch.
    pub fn is_pass(self) -> bool {
        self == Verdict::Pass
    }
}

/// Combine the scenario outcome, the environment class and the budget cap.
///
/// A blocked environment is never reported as a failure of the implementation,
/// and a passing batch that overran its budget is reported as PARTIAL, never
/// as PASS.
pub fn final_verdict(passed: bool, blocked: bool, capped: bool) -> Verdict {
    if blocked {
        Verdict::Blocked
    } else if !passed {
        Verdict::Fail
    } else if capped {
        Verdict::Partial
    } else {
        Verdict::Pass
    }
}

/// Whether the private run directory may be removed under this verdict.
///
/// Only a clean PASS removes the run directory; PARTIAL, BLOCKED and FAIL
/// always keep it so the evidence stays on disk.
pub fn should_remove_run_dir(verdict: Verdict) -> bool {
    verdict == Verdict::Pass
}

// ---------------------------------------------------------------------------
// Workload descriptor contract
// ---------------------------------------------------------------------------

/// Validate the descriptor set a fixture presents after its READY handshake.
///
/// The contract is exact: only fds 0, 1 and 2 may exist, and each must be a
/// pipe or the null device.  Sockets, regular files, inherited descriptors
/// above 2 and any other character device (for example a pty master) are
/// rejected.
pub fn stdio_fds_ok(entries: &[(u32, String)]) -> Result<(), &'static str> {
    if entries.is_empty() {
        return Err("workload exposes no descriptors");
    }
    let mut seen = [false; 3];
    for (fd, target) in entries {
        if *fd > 2 {
            return Err("workload holds an inherited descriptor above 2");
        }
        seen[*fd as usize] = true;
        if target.starts_with("socket:") {
            return Err("workload holds a socket descriptor");
        }
        if !(target.starts_with("pipe:[") || target == "/dev/null") {
            return Err("workload holds a non-stdio descriptor");
        }
    }
    if seen.iter().any(|s| !s) {
        return Err("workload is missing a stdio descriptor");
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Record schemas (contract 5.8) and manifest field sets
// ---------------------------------------------------------------------------

/// Fixture uid the workload and its probes must run as.
pub const FIXTURE_UID: u32 = 1000;
/// Fixture gid the workload must run as.
pub const FIXTURE_GID: u32 = 1000;
/// f_type of a pid-namespace file on this host (nsfs).
pub const NSFS_MAGIC: u64 = 0x6e73_6673;

/// batch record: required keys (contract 5.8).
pub const BATCH_REQUIRED_KEYS: [&str; 9] = [
    "record",
    "schema",
    "batch_run_id",
    "boot_id",
    "kernel",
    "mode",
    "euid",
    "embedded_bpf",
    "run_dir_path",
];

/// session record: required keys (contract 5.8, flat names).
pub const SESSION_REQUIRED_KEYS: [&str; 27] = [
    "record",
    "schema",
    "batch_run_id",
    "run_id",
    "run_dir_path",
    "session_id",
    "boot_id",
    "started_at_boot_ns",
    "wrong_birth_test",
    "target_tgid",
    "target_ppid",
    "target_start_ticks",
    "target_uid",
    "target_gid",
    "target_nspid",
    "pidns_dev",
    "pidns_ino",
    "nsfs_magic",
    "cgroup_path",
    "cgroup_id",
    "cgroup_inode_expected",
    "libssl",
    "attach",
    "program_ids",
    "link_ids",
    "identity_checks",
    "workload",
];

/// session_closeout: extra keys on top of the session set.
pub const SESSION_CLOSEOUT_KEYS: [&str; 8] = [
    "passed",
    "failure_reasons",
    "issues",
    "counters_available",
    "counters",
    "requests",
    "peer",
    "cleanup",
];

/// Every counter name reported by the collector.
pub const COUNTER_REPORT_KEYS: [&str; 17] = [
    "submitted",
    "consumed",
    "lost",
    "read_failed",
    "map_failed",
    "expired",
    "truncated",
    "identity_rejected",
    "len_invalid",
    "abnormal_ret",
    "user_read_attempts",
    "user_read_bytes",
    "payload_bytes",
    "depth_overflow",
    "unpaired",
    "entries",
    "exits",
];

/// session_closeout.cleanup keys (contract 5.8).
pub const SESSION_CLEANUP_KEYS: [&str; 4] =
    ["prog_ids_absent", "child_gone", "fd_before", "fd_after"];

/// batch closeout.cleanup keys (contract 5.8).
pub const CLEANUP_KEYS: [&str; 5] = [
    "own_prog_ids_absent",
    "own_child_gone",
    "fd_equal",
    "run_dir_removed",
    "run_dir_path",
];

/// libssl block keys (contract 5.8).
pub const LIBSSL_KEYS: [&str; 7] = [
    "path",
    "dev",
    "ino",
    "size",
    "mtime_s",
    "build_id",
    "maps_exec_match",
];

/// attach block keys (contract 5.8).
pub const ATTACH_KEYS: [&str; 6] = [
    "mechanism",
    "pmu_type",
    "pid_scope",
    "symbol",
    "symbol_version",
    "file_offset",
];

/// peer block keys (contract 5.8).
pub const PEER_KEYS: [&str; 4] = ["c2s_received", "c2s_match", "s2c_received", "s2c_match"];

/// request entry keys (contract 5.8).
pub const REQUEST_KEYS: [&str; 8] = [
    "direction",
    "declared",
    "captured",
    "truncated",
    "read_attempted",
    "ret",
    "payload_match",
    "alias",
];

/// crash_summary required keys (parent supervision record).
pub const CRASH_SUMMARY_KEYS: [&str; 20] = [
    "record",
    "schema",
    "source",
    "child_closeout_absent",
    "source_session",
    "source_run_id",
    "source_run_dir_path",
    "crash_collector_pid",
    "crash_program_ids",
    "target_tgid",
    "target_ppid",
    "target_start_ticks",
    "target_uid",
    "target_alive_before_kill",
    "program_ids_present_before_kill",
    "killed_by_signal",
    "program_ids_absent_within_3s",
    "fixture_gone_within_11s",
    "manifest_before_kill",
    "cleanup",
];

fn is_hex_len(value: &Value, len: usize) -> bool {
    value
        .as_str()
        .map(|s| s.len() == len && s.bytes().all(|b| b.is_ascii_hexdigit()))
        .unwrap_or(false)
}

fn is_hex64(value: &Value) -> bool {
    is_hex_len(value, 64)
}

fn is_boot_id(value: &Value) -> bool {
    match value.as_str() {
        Some(text) => text.len() == 36 && text.chars().filter(|c| *c == '-').count() == 4,
        None => false,
    }
}

fn require_keys(map: &serde_json::Map<String, Value>, keys: &[&str]) -> Result<(), &'static str> {
    for key in keys {
        if !map.contains_key(*key) {
            return Err("record is missing a contract field");
        }
    }
    Ok(())
}

fn require_u64(map: &serde_json::Map<String, Value>, key: &str) -> Result<u64, &'static str> {
    map.get(key)
        .and_then(Value::as_u64)
        .ok_or("contract field is missing or not an unsigned integer")
}

fn require_i64(map: &serde_json::Map<String, Value>, key: &str) -> Result<i64, &'static str> {
    map.get(key)
        .and_then(Value::as_i64)
        .ok_or("contract field is missing or not an integer")
}

fn require_bool(map: &serde_json::Map<String, Value>, key: &str) -> Result<bool, &'static str> {
    map.get(key)
        .and_then(Value::as_bool)
        .ok_or("contract field is missing or not a boolean")
}

fn require_str(map: &serde_json::Map<String, Value>, key: &str) -> Result<String, &'static str> {
    map.get(key)
        .and_then(Value::as_str)
        .map(|s| s.to_string())
        .ok_or("contract field is missing or not a string")
}

fn require_object<'a>(
    map: &'a serde_json::Map<String, Value>,
    key: &str,
) -> Result<&'a serde_json::Map<String, Value>, &'static str> {
    map.get(key)
        .and_then(Value::as_object)
        .ok_or("contract field is missing or not an object")
}

fn require_positive(map: &serde_json::Map<String, Value>, key: &str) -> Result<u64, &'static str> {
    let value = require_u64(map, key)?;
    if value == 0 {
        return Err("contract field must be greater than zero");
    }
    Ok(value)
}

/// Validate the teardown block of the batch closeout.
pub fn cleanup_fields_ok(value: &Value) -> Result<(), &'static str> {
    let map = value.as_object().ok_or("cleanup is not an object")?;
    require_keys(map, &CLEANUP_KEYS)?;
    for key in [
        "own_prog_ids_absent",
        "own_child_gone",
        "fd_equal",
        "run_dir_removed",
    ] {
        require_bool(map, key)?;
    }
    let path = require_str(map, "run_dir_path")?;
    if !run_dir_is_valid_path(&path) {
        return Err("cleanup run_dir_path is not a private run directory");
    }
    Ok(())
}

/// Validate the teardown block of a session_closeout (contract names only).
pub fn session_cleanup_ok(value: &Value) -> Result<(), &'static str> {
    let map = value
        .as_object()
        .ok_or("session cleanup is not an object")?;
    if map.len() != SESSION_CLEANUP_KEYS.len() {
        return Err("session cleanup carries keys outside the contract");
    }
    require_keys(map, &SESSION_CLEANUP_KEYS)?;
    require_bool(map, "prog_ids_absent")?;
    require_bool(map, "child_gone")?;
    require_u64(map, "fd_before")?;
    require_u64(map, "fd_after")?;
    Ok(())
}

fn run_dir_is_valid_path(path: &str) -> bool {
    match path.strip_prefix("/tmp/kebab-observer-tls-fixture-") {
        Some(run_id) => run_id_is_valid(run_id),
        None => false,
    }
}

fn session_id_is_contract(session_id: &str) -> bool {
    matches!(session_id, "positive" | "wrong_birth" | "crash_recovery")
}

fn validate_libssl(map: &serde_json::Map<String, Value>) -> Result<(), &'static str> {
    let libssl = require_object(map, "libssl")?;
    require_keys(libssl, &LIBSSL_KEYS)?;
    let path = require_str(libssl, "path")?;
    if !path.starts_with('/') || !path.contains("libssl") {
        return Err("libssl path is not an absolute libssl mapping");
    }
    for key in ["dev", "ino", "size", "mtime_s"] {
        require_positive(libssl, key)?;
    }
    if !is_hex_len(libssl.get("build_id").unwrap_or(&Value::Null), 40) {
        return Err("libssl build_id is not a 40 hex character id");
    }
    if !require_bool(libssl, "maps_exec_match")? {
        return Err("libssl executable mapping does not match the file");
    }
    Ok(())
}

fn validate_attach(map: &serde_json::Map<String, Value>) -> Result<(), &'static str> {
    let attach = require_object(map, "attach")?;
    require_keys(attach, &ATTACH_KEYS)?;
    if require_str(attach, "mechanism")? != "perf_event_open_uprobe_pmu" {
        return Err("attach mechanism is not the PMU uprobe path");
    }
    require_positive(attach, "pmu_type")?;
    if !require_str(attach, "pid_scope")?.starts_with("Some(") {
        return Err("attach pid_scope is not Some(tgid)");
    }
    if require_str(attach, "symbol")? != "SSL_write" {
        return Err("attach symbol is not SSL_write");
    }
    if require_str(attach, "symbol_version")?.is_empty() {
        return Err("attach symbol_version is empty");
    }
    require_positive(attach, "file_offset")?;
    Ok(())
}

fn validate_identity_checks(map: &serde_json::Map<String, Value>) -> Result<(), &'static str> {
    let checks = map
        .get("identity_checks")
        .and_then(Value::as_array)
        .ok_or("identity_checks is not an array")?;
    if checks.is_empty() {
        return Err("identity_checks is empty");
    }
    for check in checks {
        let check = check.as_object().ok_or("identity check is not an object")?;
        if require_str(check, "stage")?.is_empty() {
            return Err("identity check stage is empty");
        }
        require_bool(check, "passed")?;
    }
    Ok(())
}

fn validate_workload(map: &serde_json::Map<String, Value>) -> Result<(), &'static str> {
    let workload = require_object(map, "workload")?;
    let path = require_str(workload, "path")?;
    if path.is_empty() {
        return Err("workload path is empty");
    }
    if !is_hex64(workload.get("sha256").unwrap_or(&Value::Null)) {
        return Err("workload sha256 is not a 64 hex digest");
    }
    require_positive(workload, "size")?;
    Ok(())
}

fn validate_peer(map: &serde_json::Map<String, Value>) -> Result<(), &'static str> {
    let peer = require_object(map, "peer")?;
    require_keys(peer, &PEER_KEYS)?;
    require_i64(peer, "c2s_received")?;
    require_i64(peer, "s2c_received")?;
    require_bool(peer, "c2s_match")?;
    require_bool(peer, "s2c_match")?;
    Ok(())
}

fn validate_requests(map: &serde_json::Map<String, Value>) -> Result<(), &'static str> {
    let requests = map
        .get("requests")
        .and_then(Value::as_array)
        .ok_or("requests is not an array")?;
    for request in requests {
        let request = request.as_object().ok_or("request is not an object")?;
        require_keys(request, &REQUEST_KEYS)?;
        let direction = require_str(request, "direction")?;
        if direction != "c2s" && direction != "s2c" {
            return Err("request direction is not c2s or s2c");
        }
        require_i64(request, "declared")?;
        require_u64(request, "captured")?;
        require_i64(request, "ret")?;
        require_u64(request, "alias")?;
        require_bool(request, "truncated")?;
        require_bool(request, "read_attempted")?;
        require_bool(request, "payload_match")?;
    }
    Ok(())
}

/// Validate a session or session_closeout record against contract 5.8.
///
/// This is the contract, not a convenience check: the collector runs it on the
/// seed record and on the closeout before printing, so a dropped, renamed or
/// nulled field can not slip into the evidence.
pub fn session_record_ok(value: &Value, closeout: bool) -> Result<(), &'static str> {
    let map = value.as_object().ok_or("session record is not an object")?;
    require_keys(map, &SESSION_REQUIRED_KEYS)?;
    let record = require_str(map, "record")?;
    if closeout {
        if record != "session_closeout" {
            return Err("closeout record name is wrong");
        }
        require_keys(map, &SESSION_CLOSEOUT_KEYS)?;
    } else if record != "session" {
        return Err("session record name is wrong");
    }
    if require_u64(map, "schema")? != 1 {
        return Err("session schema is not 1");
    }
    let batch_run_id = require_str(map, "batch_run_id")?;
    let run_id = require_str(map, "run_id")?;
    if !run_id_is_valid(&run_id) || !run_id_is_valid(&batch_run_id) {
        return Err("run id is malformed");
    }
    if batch_run_id != run_id {
        return Err("batch_run_id and run_id disagree");
    }
    if require_str(map, "run_dir_path")? != run_dir_path(&run_id) {
        return Err("run_dir_path does not match the run id");
    }
    let session_id = require_str(map, "session_id")?;
    if !session_id_is_contract(&session_id) {
        return Err("session_id is not one of positive|wrong_birth|crash_recovery");
    }
    if !is_boot_id(map.get("boot_id").unwrap_or(&Value::Null)) {
        return Err("boot_id is missing or malformed");
    }
    require_positive(map, "started_at_boot_ns")?;
    require_bool(map, "wrong_birth_test")?;
    require_positive(map, "target_tgid")?;
    require_positive(map, "target_ppid")?;
    require_positive(map, "target_start_ticks")?;
    if require_u64(map, "target_uid")? != u64::from(FIXTURE_UID) {
        return Err("target_uid is not the fixture uid");
    }
    if require_u64(map, "target_gid")? != u64::from(FIXTURE_GID) {
        return Err("target_gid is not the fixture gid");
    }
    require_positive(map, "target_nspid")?;
    require_positive(map, "pidns_dev")?;
    require_positive(map, "pidns_ino")?;
    if require_u64(map, "nsfs_magic")? != NSFS_MAGIC {
        return Err("nsfs_magic is not nsfs");
    }
    if !require_str(map, "cgroup_path")?.starts_with('/') {
        return Err("cgroup_path is not absolute");
    }
    require_positive(map, "cgroup_id")?;
    require_positive(map, "cgroup_inode_expected")?;
    validate_libssl(map)?;
    validate_attach(map)?;
    let ids = map
        .get("program_ids")
        .and_then(Value::as_array)
        .ok_or("program_ids is not an array")?;
    if ids.len() != 2 || ids.iter().any(|v| v.as_u64().unwrap_or(0) == 0) {
        return Err("program_ids is not two loaded program ids");
    }
    let link_ids = map
        .get("link_ids")
        .and_then(Value::as_array)
        .ok_or("link_ids is not an array")?;
    if link_ids.iter().any(|v| v.as_u64().is_none()) {
        return Err("link_ids holds a non unsigned element");
    }
    validate_identity_checks(map)?;
    validate_workload(map)?;
    if !closeout {
        return Ok(());
    }
    if require_bool(map, "passed").is_err() {
        return Err("passed is not a boolean");
    }
    for key in ["failure_reasons", "issues"] {
        let list = map
            .get(key)
            .and_then(Value::as_array)
            .ok_or("closeout string list missing")?;
        if list.iter().any(|v| v.as_str().is_none()) {
            return Err("closeout string list holds a non-string");
        }
    }
    require_bool(map, "counters_available")?;
    let counters = require_object(map, "counters")?;
    for key in COUNTER_REPORT_KEYS {
        if counters.get(key).and_then(Value::as_u64).is_none() {
            return Err("counter field missing or not unsigned");
        }
    }
    validate_requests(map)?;
    validate_peer(map)?;
    session_cleanup_ok(map.get("cleanup").ok_or("session cleanup missing")?)?;
    Ok(())
}

/// Validate the batch record.
pub fn batch_record_ok(value: &Value) -> Result<(), &'static str> {
    let map = value.as_object().ok_or("batch record is not an object")?;
    require_keys(map, &BATCH_REQUIRED_KEYS)?;
    if require_str(map, "record")? != "batch" {
        return Err("batch record name is wrong");
    }
    if require_u64(map, "schema")? != 1 {
        return Err("batch schema is not 1");
    }
    let run_id = require_str(map, "batch_run_id")?;
    if !run_id_is_valid(&run_id) {
        return Err("batch_run_id is malformed");
    }
    if require_str(map, "run_dir_path")? != run_dir_path(&run_id) {
        return Err("batch run_dir_path does not match the run id");
    }
    if !is_boot_id(map.get("boot_id").unwrap_or(&Value::Null)) {
        return Err("batch boot_id is missing or malformed");
    }
    if require_str(map, "kernel")?.is_empty() {
        return Err("batch kernel is empty");
    }
    if require_str(map, "mode")? != "wsl_x86_64_owned_fixture" {
        return Err("batch mode is not the owned fixture mode");
    }
    require_u64(map, "euid")?;
    let objects = map
        .get("embedded_bpf")
        .and_then(Value::as_array)
        .ok_or("embedded_bpf is not an array")?;
    if objects.is_empty() {
        return Err("embedded_bpf is empty");
    }
    for object in objects {
        let object = object
            .as_object()
            .ok_or("embedded_bpf entry is not an object")?;
        if require_str(object, "name")?.is_empty() {
            return Err("embedded object name is empty");
        }
        require_positive(object, "bytes")?;
        if !is_hex_len(object.get("fnv1a64").unwrap_or(&Value::Null), 16) {
            return Err("embedded object fnv1a64 is not 16 hex characters");
        }
    }
    Ok(())
}

/// Validate the parent supervision record printed after the crash child dies.
pub fn crash_summary_ok(value: &Value) -> Result<(), &'static str> {
    let map = value.as_object().ok_or("crash_summary is not an object")?;
    require_keys(map, &CRASH_SUMMARY_KEYS)?;
    if require_str(map, "record")? != "crash_summary" {
        return Err("crash_summary record name is wrong");
    }
    if require_u64(map, "schema")? != 1 {
        return Err("crash_summary schema is not 1");
    }
    if require_str(map, "source")? != "parent_after_kill" {
        return Err("crash_summary source is not parent_after_kill");
    }
    if !require_bool(map, "child_closeout_absent")? {
        return Err("crash_summary does not assert the child closeout is absent");
    }
    if require_str(map, "source_session")? != "crash_recovery" {
        return Err("crash_summary source_session is not crash_recovery");
    }
    let run_id = require_str(map, "source_run_id")?;
    if !run_id_is_valid(&run_id) {
        return Err("crash_summary source_run_id is malformed");
    }
    if require_str(map, "source_run_dir_path")? != run_dir_path(&run_id) {
        return Err("crash_summary run dir path does not match the run id");
    }
    require_positive(map, "crash_collector_pid")?;
    let ids = map
        .get("crash_program_ids")
        .and_then(Value::as_array)
        .ok_or("crash_program_ids is not an array")?;
    if ids.len() != 2 || ids.iter().any(|v| v.as_u64().unwrap_or(0) == 0) {
        return Err("crash_program_ids is not two program ids");
    }
    require_positive(map, "target_tgid")?;
    require_positive(map, "target_ppid")?;
    require_positive(map, "target_start_ticks")?;
    if require_u64(map, "target_uid")? != u64::from(FIXTURE_UID) {
        return Err("crash target uid is not the fixture uid");
    }
    for key in [
        "target_alive_before_kill",
        "program_ids_present_before_kill",
        "killed_by_signal",
        "program_ids_absent_within_3s",
        "fixture_gone_within_11s",
    ] {
        if !require_bool(map, key)? {
            return Err("crash_summary assertion is not satisfied");
        }
    }
    let manifest = require_object(map, "manifest_before_kill")?;
    if !require_bool(manifest, "ok")? {
        return Err("crash_summary manifest was not valid before the kill");
    }
    let cleanup = require_object(map, "cleanup")?;
    for key in [
        "prog_ids_absent",
        "child_gone",
        "run_dir_removed",
        "verified_before_remove",
    ] {
        require_bool(cleanup, key)?;
    }
    if require_str(cleanup, "run_dir_path")? != run_dir_path(&run_id) {
        return Err("crash_summary cleanup path does not match the run id");
    }
    Ok(())
}

/// Identity key of one owned process, derived from /proc.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct IdentityKey {
    /// Process group id (host pid).
    pub tgid: u64,
    /// Parent pid.
    pub ppid: u64,
    /// Start time in clock ticks since boot.
    pub start_ticks: u64,
    /// Effective uid.
    pub uid: u32,
    /// Effective gid.
    pub gid: u32,
    /// Pid-namespace device (nsfs, follow).
    pub ns_dev: u64,
    /// Pid-namespace inode (nsfs, follow).
    pub ns_ino: u64,
}

/// Compare two identity snapshots; any drift means pid reuse or a retarget.
pub fn identity_ok(expected: &IdentityKey, observed: &IdentityKey) -> Result<(), &'static str> {
    if expected.tgid == 0 || expected.start_ticks == 0 || expected.ns_ino == 0 {
        return Err("expected identity is not initialised");
    }
    if observed.tgid != expected.tgid {
        return Err("tgid changed");
    }
    if observed.ppid != expected.ppid {
        return Err("ppid changed");
    }
    if observed.start_ticks != expected.start_ticks {
        return Err("start time changed (pid reuse)");
    }
    if observed.uid != expected.uid || observed.gid != expected.gid {
        return Err("uid/gid changed");
    }
    if observed.uid != FIXTURE_UID || observed.gid != FIXTURE_GID {
        return Err("process does not run as the fixture uid/gid");
    }
    if observed.ns_dev != expected.ns_dev || observed.ns_ino != expected.ns_ino {
        return Err("pid namespace changed");
    }
    Ok(())
}

/// Facts needed to decide whether a private run directory may be removed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RunDirFacts {
    /// Absolute path of the directory.
    pub path: String,
    /// Run id the directory must encode.
    pub run_id: String,
    /// Whether the path is a symlink.
    pub is_symlink: bool,
    /// Whether the path is a directory.
    pub is_dir: bool,
    /// Owner uid.
    pub uid: u32,
    /// Permission bits.
    pub mode: u32,
    /// run_id read back from the manifest, if readable.
    pub manifest_run_id: Option<String>,
}

/// Decide whether the run directory is still provably ours.
///
/// Used by both the normal teardown and the crash-path cleanup, so the
/// symlink/owner/mode/run_id rules exist in exactly one place.
pub fn run_dir_removable(facts: &RunDirFacts) -> Result<(), &'static str> {
    if facts.is_symlink {
        return Err("run dir is a symlink");
    }
    if !facts.is_dir {
        return Err("run dir is not a directory");
    }
    if !run_dir_matches(&facts.path, &facts.run_id) {
        return Err("run dir path does not match its run id");
    }
    if facts.uid != 0 {
        return Err("run dir owner is not root");
    }
    if facts.mode & 0o777 != 0o700 {
        return Err("run dir mode is not 0700");
    }
    match facts.manifest_run_id.as_deref() {
        Some(id) if id == facts.run_id => Ok(()),
        Some(_) => Err("manifest run_id mismatch"),
        None => Err("manifest run_id missing"),
    }
}

/// Check the alias generation of one bound script.
///
/// The alias is a 32-bit run-local opaque token: one value per SSL object, no
/// cryptographic claim. A change inside one direction means the object was
/// replaced or the probe mixed objects up, which this observer refuses instead
/// of pretending to model object generations.
pub fn alias_generation_ok(views: &[RequestView]) -> Result<usize, &'static str> {
    if views.len() < 2 {
        return Err("not enough calls to bind an alias");
    }
    let mut c2s: Option<u32> = None;
    let mut s2c: Option<u32> = None;
    for view in views {
        if !alias_id_is_opaque(view.alias) {
            return Err("alias is not a run-local opaque id");
        }
        let slot = if view.direction == "c2s" {
            &mut c2s
        } else if view.direction == "s2c" {
            &mut s2c
        } else {
            return Err("unknown direction");
        };
        match slot {
            Some(seen) if *seen != view.alias => {
                return Err("alias changed inside one direction (object reuse or mismatch)");
            }
            Some(_) => {}
            None => *slot = Some(view.alias),
        }
    }
    let (Some(client), Some(server)) = (c2s, s2c) else {
        return Err("one direction never carried an alias");
    };
    if client == server {
        return Err("client and server objects share one alias");
    }
    Ok(2)
}

/// Whether an alias is a run-local counter id rather than an address encoding.
///
/// The BPF side hands out ids 1..=ALIAS_MAX_ID from a private pointer map; the
/// pointer itself is only a map key and is never written into a record. A value
/// outside this range means some other encoding leaked into the record.
pub fn alias_id_is_opaque(alias: u32) -> bool {
    (1..=ALIAS_MAX_ID).contains(&alias)
}

/// Symbol information an uprobe attachment needs, read from the ELF itself.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SymbolInfo {
    /// Version definition string, for example OPENSSL_3.0.0.
    pub version: String,
    /// File offset of the symbol inside the mapped library.
    pub file_offset: u64,
    /// Virtual address of the symbol.
    pub value: u64,
}

fn elf_cstr(image: &[u8], offset: usize) -> Result<String, &'static str> {
    let rest = image
        .get(offset..)
        .ok_or("string offset outside the file")?;
    let end = rest
        .iter()
        .position(|b| *b == 0)
        .ok_or("string is not terminated")?;
    std::str::from_utf8(&rest[..end])
        .map(|s| s.to_string())
        .map_err(|_| "string is not UTF-8")
}

/// Resolve a dynamic symbol's version and file offset from an ELF64 image.
#[allow(clippy::too_many_lines)]
pub fn elf_symbol_info(image: &[u8], symbol: &str) -> Result<SymbolInfo, &'static str> {
    if image.len() < 64 || &image[0..4] != b"\x7fELF" {
        return Err("not an ELF image");
    }
    if image[4] != 2 || image[5] != 1 {
        return Err("not a little-endian ELF64 image");
    }
    let u16at = |o: usize| -> Result<u16, &'static str> {
        image
            .get(o..o + 2)
            .map(|b| u16::from_le_bytes([b[0], b[1]]))
            .ok_or("truncated ELF header")
    };
    let u32at = |o: usize| -> Result<u32, &'static str> {
        image
            .get(o..o + 4)
            .map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]))
            .ok_or("truncated ELF header")
    };
    let u64at = |o: usize| -> Result<u64, &'static str> {
        image
            .get(o..o + 8)
            .map(|b| u64::from_le_bytes([b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7]]))
            .ok_or("truncated ELF header")
    };
    let phoff = u64at(0x20)? as usize;
    let shoff = u64at(0x28)? as usize;
    let phentsize = u16at(0x36)? as usize;
    let phnum = u16at(0x38)? as usize;
    let shentsize = u16at(0x3a)? as usize;
    let shnum = u16at(0x3c)? as usize;
    let shstrndx = u16at(0x3e)? as usize;
    if shentsize < 64 || shnum == 0 || shoff == 0 {
        return Err("ELF has no usable section table");
    }
    let shstr_base = shoff + shstrndx * shentsize;
    let shstr_off = u64at(shstr_base + 0x18)? as usize;
    let shstr_size = u64at(shstr_base + 0x20)? as usize;
    let shstr = image
        .get(shstr_off..shstr_off + shstr_size)
        .ok_or("section name table outside the file")?;
    let mut dynsym: Option<(usize, usize, usize, u32)> = None;
    let mut dynstr: Option<(usize, usize)> = None;
    let mut versym: Option<(usize, usize)> = None;
    let mut verdef: Option<(usize, usize)> = None;
    for index in 0..shnum {
        let base = shoff + index * shentsize;
        let name_off = u32at(base)? as usize;
        let name = elf_cstr(shstr, name_off)?;
        let offset = u64at(base + 0x18)? as usize;
        let size = u64at(base + 0x20)? as usize;
        let entsize = u64at(base + 0x38)? as usize;
        let link = u32at(base + 0x28)?;
        match name.as_str() {
            ".dynsym" => dynsym = Some((offset, size, entsize.max(24), link)),
            ".dynstr" => dynstr = Some((offset, size)),
            ".gnu.version" => versym = Some((offset, size)),
            ".gnu.version_d" => verdef = Some((offset, size)),
            _ => {}
        }
    }
    let (sym_off, sym_size, sym_ent, _sym_link) = dynsym.ok_or("ELF has no .dynsym")?;
    let (str_off, str_size) = dynstr.ok_or("ELF has no .dynstr")?;
    let strings = image
        .get(str_off..str_off + str_size)
        .ok_or(".dynstr outside the file")?;
    let count = sym_size / sym_ent.max(1);
    let mut found: Option<(usize, u64)> = None;
    for index in 0..count {
        let base = sym_off + index * sym_ent;
        let name_off = u32at(base)? as usize;
        let shndx = u16at(base + 6)?;
        let value = u64at(base + 8)?;
        if shndx == 0 || name_off == 0 {
            continue;
        }
        if elf_cstr(strings, name_off)? == symbol {
            found = Some((index, value));
            break;
        }
    }
    let (index, value) = found.ok_or("symbol is not exported by this library")?;
    let version = match versym {
        Some((off, size)) => {
            let at = off + index * 2;
            if at + 2 > off + size {
                return Err("version index outside .gnu.version");
            }
            let raw = u16at(at)? & 0x7fff;
            if raw <= 1 {
                "unversioned".to_string()
            } else {
                let (def_off, def_size) = verdef.ok_or("ELF has no .gnu.version_d")?;
                let mut cursor = def_off;
                let mut name = None;
                while cursor + 20 <= def_off + def_size {
                    let vd_ndx = u16at(cursor + 4)?;
                    let vd_aux = u32at(cursor + 12)? as usize;
                    let vd_next = u32at(cursor + 16)? as usize;
                    if vd_ndx == raw {
                        let aux = cursor + vd_aux;
                        let name_off = u32at(aux)? as usize;
                        name = Some(elf_cstr(strings, name_off)?);
                        break;
                    }
                    if vd_next == 0 {
                        break;
                    }
                    cursor += vd_next;
                }
                name.ok_or("version definition not found")?
            }
        }
        None => "unversioned".to_string(),
    };
    if phnum == 0 || phentsize < 56 {
        return Err("ELF has no program headers");
    }
    let mut file_offset = None;
    for index in 0..phnum {
        let base = phoff + index * phentsize;
        let p_type = u32at(base)?;
        if p_type != 1 {
            continue;
        }
        let p_offset = u64at(base + 0x08)?;
        let p_vaddr = u64at(base + 0x10)?;
        let p_memsz = u64at(base + 0x28)?;
        if value >= p_vaddr && value < p_vaddr.saturating_add(p_memsz) {
            file_offset = Some(value - p_vaddr + p_offset);
            break;
        }
    }
    let file_offset = file_offset.ok_or("symbol is not inside a loadable segment")?;
    if file_offset == 0 {
        return Err("symbol has a zero file offset");
    }
    Ok(SymbolInfo {
        version,
        file_offset,
        value,
    })
}

/// Validate the session manifest written before and after every session.
pub fn manifest_fields_ok(value: &Value, require_end: bool) -> Result<(), &'static str> {
    let map = value.as_object().ok_or("manifest is not an object")?;
    if map.get("record").and_then(Value::as_str) != Some("manifest") {
        return Err("manifest record name is wrong");
    }
    if map.get("schema").and_then(Value::as_u64) != Some(1) {
        return Err("manifest schema is not 1");
    }
    let run_id = map
        .get("run_id")
        .and_then(Value::as_str)
        .ok_or("manifest run_id missing")?;
    if !run_id_is_valid(run_id) {
        return Err("manifest run_id is malformed");
    }
    if map.get("path").and_then(Value::as_str) != Some(run_dir_path(run_id).as_str()) {
        return Err("manifest path does not match the run id");
    }
    if map.get("owner_euid").and_then(Value::as_u64) != Some(0) {
        return Err("manifest owner_euid is not 0");
    }
    match map.get("boot_id").and_then(Value::as_str) {
        Some(boot) if boot.len() == 36 => {}
        _ => return Err("manifest boot_id missing"),
    }
    if map.get("created_at_unix").and_then(Value::as_u64).is_none() {
        return Err("manifest created_at_unix missing");
    }
    if map.get("updated_at_unix").and_then(Value::as_u64).is_none() {
        return Err("manifest updated_at_unix missing");
    }
    let files = map
        .get("files")
        .and_then(Value::as_array)
        .ok_or("manifest files missing")?;
    if files
        .iter()
        .any(|f| f.as_str().map(|s| s.is_empty()).unwrap_or(true))
    {
        return Err("manifest files are malformed");
    }
    let sessions = map
        .get("sessions")
        .and_then(Value::as_object)
        .ok_or("manifest sessions missing")?;
    for entry in sessions.values() {
        let entry = entry
            .as_object()
            .ok_or("manifest session is not an object")?;
        let start = entry
            .get("start")
            .and_then(Value::as_object)
            .ok_or("manifest session start missing")?;
        if start.get("at_unix").and_then(Value::as_u64).is_none() {
            return Err("manifest start time missing");
        }
        let target = start
            .get("target")
            .and_then(Value::as_object)
            .ok_or("manifest start target missing")?;
        if target.get("tgid").and_then(Value::as_u64).unwrap_or(0) == 0 {
            return Err("manifest start tgid missing");
        }
        if target
            .get("start_ticks")
            .and_then(Value::as_u64)
            .unwrap_or(0)
            == 0
        {
            return Err("manifest start birth missing");
        }
        let ids = start
            .get("bpf_program_ids")
            .and_then(Value::as_array)
            .ok_or("manifest bpf ids missing")?;
        if ids.len() != 2 || ids.iter().any(|v| v.as_u64().is_none()) {
            return Err("manifest bpf ids are malformed");
        }
        let workload = start
            .get("workload")
            .and_then(Value::as_object)
            .ok_or("manifest workload missing")?;
        if !is_hex64(workload.get("sha256").unwrap_or(&Value::Null)) {
            return Err("manifest workload sha256 missing");
        }
        if workload.get("size").and_then(Value::as_u64).unwrap_or(0) == 0 {
            return Err("manifest workload size missing");
        }
        match entry.get("end") {
            None => {
                if require_end {
                    return Err("manifest session end missing");
                }
            }
            Some(end) => {
                let end = end
                    .as_object()
                    .ok_or("manifest session end is not an object")?;
                if end.get("at_unix").and_then(Value::as_u64).is_none() {
                    return Err("manifest end time missing");
                }
                if end.get("passed").and_then(Value::as_bool).is_none() {
                    return Err("manifest end verdict missing");
                }
            }
        }
    }
    Ok(())
}

/// Whether two workload identities name the same bytes.
pub fn workload_binding_ok(before: &Value, after: &Value) -> bool {
    let field = |v: &Value, key: &str| v.get(key).cloned().unwrap_or(Value::Null);
    field(before, "sha256") == field(after, "sha256")
        && field(before, "size") == field(after, "size")
        && field(before, "path") == field(after, "path")
        && is_hex64(&field(before, "sha256"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn record(kind: u32, call_id: u64, declared: i64, with_payload: bool) -> Record {
        let mut r = Record {
            wire: WireEvent {
                ts_ns: 1,
                sequence: call_id,
                call_id,
                actor_pid_tid: (7u64 << 32) | 8,
                actor_start_ns: 9,
                actor_cgroup: 10,
                namespace_pid_tid: (7u64 << 32) | 8,
                namespace_inode: 11,
                declared,
                result: 0,
                alias: 0x1234,
                captured: 0,
                kind,
                flags: 0,
                cpu: 0,
                uid: 1000,
                depth: 0,
                spare: 0,
            },
            payload: [0u8; SAMPLE_CAP],
        };
        if with_payload && declared > 0 {
            let n = usize::try_from(declared).unwrap().min(SAMPLE_CAP);
            for i in 0..n {
                r.payload[i] = payload_byte(i);
            }
            r.wire.captured = n as u32;
            r.wire.flags |= FLAG_USER_READ;
            if usize::try_from(declared).unwrap() > n {
                r.wire.flags |= FLAG_TRUNCATED;
            }
        } else if declared <= 0 {
            r.wire.flags |= FLAG_LEN_INVALID;
        }
        r
    }

    #[test]
    fn synthetic_wire_signed_low32_and_zero_init() {
        let mut bytes = [0u8; RING_RECORD];
        bytes[64..72].copy_from_slice(&(-1i64).to_le_bytes());
        bytes[72..80].copy_from_slice(&(-13i64).to_le_bytes());
        bytes[84..88].copy_from_slice(&3u32.to_le_bytes());
        bytes[88..92].copy_from_slice(&KIND_ENTER.to_le_bytes());
        bytes[RECORD_HEADER] = 1;
        bytes[RECORD_HEADER + 2] = 3;
        let rec = decode_record(&bytes).unwrap();
        assert_eq!(rec.wire.declared, -1);
        assert_eq!(rec.wire.result, -13);
        assert_eq!(rec.payload(), &[1u8, 0, 3]);
        assert!(rec.payload[SAMPLE_CAP - 1] == 0);
        assert_eq!(
            decode_record(&bytes[..RING_RECORD - 1]).unwrap_err(),
            DecodeError::Length(RING_RECORD - 1)
        );
        let mut bad = bytes;
        bad[88..92].copy_from_slice(&9u32.to_le_bytes());
        assert_eq!(decode_record(&bad).unwrap_err(), DecodeError::Kind(9));
        let mut over = bytes;
        over[84..88].copy_from_slice(&(SAMPLE_CAP as u32 + 1).to_le_bytes());
        assert!(matches!(
            decode_record(&over).unwrap_err(),
            DecodeError::Captured(_)
        ));
        let mut spare = bytes;
        spare[108..112].copy_from_slice(&7u32.to_le_bytes());
        assert!(matches!(
            decode_record(&spare).unwrap_err(),
            DecodeError::Reserved(_)
        ));
    }

    #[test]
    fn synthetic_payload_formula_and_prefix() {
        let p = expected_payload(12);
        assert_eq!(&p[..7], b"TLSFIX1");
        assert_eq!(p[7], 0);
        assert_eq!(p[8], 0x54u8.wrapping_add(56));
        assert!(payload_matches(&expected_payload(512), 512));
        assert!(!payload_matches(b"TLSFIX2", 8));
        assert!(payload_matches(&[], -1));
        assert!(payload_matches(&expected_payload(SAMPLE_CAP), 8192));
        assert!(!payload_matches(&expected_payload(4), 2));
    }

    #[test]
    fn synthetic_pairing_nesting_and_gaps() {
        let mut a = record(KIND_ENTER, 1, 512, true);
        a.wire.flags = FLAG_USER_READ;
        let mut b = record(KIND_EXIT, 1, 512, false);
        b.wire.result = 512;
        let mut c = record(KIND_ENTER, 2, -1, false);
        c.wire.flags = FLAG_LEN_INVALID;
        let mut d = record(KIND_EXIT, 2, -1, false);
        d.wire.result = -1;
        let analysis = analyze(&[a.clone(), b.clone(), c.clone(), d.clone()]);
        assert_eq!((analysis.entries, analysis.exits), (2, 2));
        assert_eq!(analysis.expired, 0);
        assert_eq!(analysis.unpaired, 0);
        assert_eq!(analysis.rows[0].result, Some(512));
        assert_eq!(analysis.rows[1].result, Some(-1));
        assert_eq!(analysis.payload_bytes(), 512);

        let mut e = record(KIND_EXIT, 9, 0, false);
        e.wire.result = 1;
        assert_eq!(analyze(&[e]).unpaired, 1);

        let missing = analyze(&[record(KIND_ENTER, 3, 16, true)]);
        assert_eq!(missing.expired, 1);

        let mut f = record(KIND_EXIT, 1, 512, false);
        f.wire.result = 512;
        assert_eq!(analyze(&[a, b, f]).duplicate_exits, 1);

        let mut nested = record(KIND_ENTER, 4, 16, true);
        nested.wire.depth = 3;
        assert_eq!(analyze(&[nested]).nested, 1);
    }

    #[test]
    fn synthetic_return_semantics() {
        let mut entry = record(KIND_ENTER, 1, 100, true);
        entry.wire.captured = 100;
        let mut exit = record(KIND_EXIT, 1, 100, false);
        exit.wire.result = 150;
        let a = analyze(&[entry.clone(), exit.clone()]);
        assert_eq!(a.abnormal_ret, 1);
        assert!(a.rows[0].abnormal_ret());
        let mut short = exit.clone();
        short.wire.result = 40;
        let b = analyze(&[entry.clone(), short]);
        assert_eq!(b.partial, 1);
        assert!(b.rows[0].partial_write());
        let mut zero = exit;
        zero.wire.result = 0;
        let c = analyze(&[entry, zero]);
        assert_eq!(c.abnormal_ret, 0);
        assert_eq!(c.partial, 0);
    }

    #[test]
    fn synthetic_counter_accounting_is_not_count_equality() {
        let mut raw = [0u64; COUNTER_SLOTS];
        raw[CNT_SUBMITTED as usize] = 8;
        raw[CNT_ENTRY as usize] = 4;
        raw[CNT_EXIT as usize] = 4;
        raw[CNT_TRUNCATED as usize] = 1;
        raw[CNT_LEN_INVALID as usize] = 1;
        raw[CNT_USER_READ_ATTEMPTS as usize] = 3;
        raw[CNT_USER_READ_BYTES as usize] = 4992;
        let mut recs = vec![
            record(KIND_ENTER, 1, 512, true),
            record(KIND_ENTER, 2, 384, true),
            record(KIND_ENTER, 3, 8192, true),
            record(KIND_ENTER, 4, -1, false),
        ];
        for r in recs.iter_mut().skip(1).take(2) {
            r.wire.alias = 0x2222;
        }
        for (i, len) in [(1u64, 512i64), (2, 384), (3, 8192), (4, -1)] {
            let mut e = record(KIND_EXIT, i, len, false);
            e.wire.result = len;
            recs.push(e);
        }
        let analysis = analyze(&recs);
        let report = counter_report(&raw, &analysis);
        assert_eq!(report.payload_bytes, 4992);
        assert_eq!(report.lost, 0);
        assert_eq!(analysis.aliases().len(), 2);
        assert!(check_positive(&report, &analysis.aliases()).is_empty());

        raw[CNT_RESERVE_FAILED as usize] = 1;
        let report = counter_report(&raw, &analysis);
        assert!(!check_positive(&report, &analysis.aliases()).is_empty());
    }

    #[test]
    fn synthetic_script_binding_rejects_deviation() {
        let mut recs = Vec::new();
        for (i, len) in SCRIPT_LENGTHS.iter().enumerate() {
            let mut e = record(KIND_ENTER, i as u64 + 1, *len, *len > 0);
            e.wire.alias = if i == 1 { 2 } else { 1 };
            recs.push(e);
            let mut x = record(KIND_EXIT, i as u64 + 1, *len, false);
            x.wire.result = if *len > 0 { *len } else { -1 };
            recs.push(x);
        }
        let analysis = analyze(&recs);
        let views = bind_script(&analysis.rows, &recs).unwrap();
        assert_eq!(views.len(), 4);
        assert_eq!(views[1].direction, "s2c");
        assert!(views[2].truncated);
        assert_eq!(views[2].captured as usize, SAMPLE_CAP);
        assert!(views[3].payload_match);
        assert!(!views[3].read_attempted);
        assert_eq!(views[0].ret, 512);

        let mut deviated = recs.clone();
        deviated[0].wire.declared = 511;
        let a = analyze(&deviated);
        assert!(bind_script(&a.rows, &deviated).is_err());
    }

    #[test]
    fn synthetic_dev_encoding_matches_observed_hosts() {
        assert_eq!(gnu_makedev(8, 0x30), 2096);
        assert_eq!(gnu_makedev(0, 0x3b), 59);
        let line = "7f0000000000-7f0000001000 r-xp 00000000 08:30 1594 /usr/lib/x86_64-linux-gnu/libssl.so.3";
        let e = parse_maps_line(line).unwrap();
        assert_eq!(e.st_dev(), 2096);
        assert_eq!(e.inode, 1594);
        assert!(e.executable());
        assert!(e.path.ends_with("libssl.so.3"));
        assert!(parse_maps_line("bad line").is_err());
        let maps = "1000-2000 rw-p 0 00:00 0 \n7f00-8f00 r-xp 0 08:30 1594 /usr/lib/x86_64-linux-gnu/libssl.so.3\n";
        let found = exec_mappings(maps, "libssl.so.3");
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].st_dev(), 2096);
    }

    #[test]
    fn synthetic_elf_build_id_positive_and_negative() {
        let shstr = b"\0.shstrtab\0.note.gnu.build-id\0";
        let note: [u8; 24] = [
            4, 0, 0, 0, 4, 0, 0, 0, 3, 0, 0, 0, b'G', b'N', b'U', 0, 0xde, 0xad, 0xbe, 0xef, 0, 0,
            0, 0,
        ];
        let shstr_off = 64 + 64 * 3;
        let note_off = shstr_off + shstr.len();
        let mut image = vec![0u8; note_off + note.len()];
        image[0..4].copy_from_slice(b"\x7fELF");
        image[4] = 2;
        image[5] = 1;
        image[shstr_off..shstr_off + shstr.len()].copy_from_slice(shstr);
        image[note_off..note_off + note.len()].copy_from_slice(&note);
        let shoff = 64usize;
        image[0x28..0x30].copy_from_slice(&(shoff as u64).to_le_bytes());
        image[0x3a..0x3c].copy_from_slice(&64u16.to_le_bytes());
        image[0x3c..0x3e].copy_from_slice(&3u16.to_le_bytes());
        image[0x3e..0x40].copy_from_slice(&1u16.to_le_bytes());
        let put = |img: &mut [u8], base: usize, name: u32, ty: u32, off: u64, size: u64| {
            img[base..base + 4].copy_from_slice(&name.to_le_bytes());
            img[base + 4..base + 8].copy_from_slice(&ty.to_le_bytes());
            img[base + 0x18..base + 0x20].copy_from_slice(&off.to_le_bytes());
            img[base + 0x20..base + 0x28].copy_from_slice(&size.to_le_bytes());
        };
        put(
            &mut image,
            shoff + 64,
            1,
            3,
            shstr_off as u64,
            shstr.len() as u64,
        );
        put(
            &mut image,
            shoff + 128,
            11,
            7,
            note_off as u64,
            note.len() as u64,
        );
        assert_eq!(elf_build_id(&image).unwrap(), vec![0xde, 0xad, 0xbe, 0xef]);
        assert_eq!(hex_lower(&[0xde, 0xad]), "dead");
        assert!(elf_build_id(b"\x7fELF").is_err());
        assert!(elf_build_id(&[0u8; 64]).is_err());
    }

    #[test]
    fn synthetic_fnv_and_run_id() {
        assert_eq!(fnv1a64(b""), 0xcbf2_9ce4_8422_2325);
        assert_eq!(fnv1a64(b"a"), 0xaf63_dc4c_8601_ec8c);
        assert!(run_id_is_valid("0123456789abcdef"));
        assert!(!run_id_is_valid("0123456789ABCDEF"));
        assert!(!run_id_is_valid("0123456789abcde"));
        assert!(!run_id_is_valid("../0123456789abc"));
        assert_eq!(
            run_dir_path("0123456789abcdef"),
            "/tmp/kebab-observer-tls-fixture-0123456789abcdef"
        );
        assert!(run_dir_matches(
            "/tmp/kebab-observer-tls-fixture-0123456789abcdef",
            "0123456789abcdef"
        ));
        assert!(!run_dir_matches(
            "/tmp/kebab-observer-tls-fixture-0123456789abcdee",
            "0123456789abcdef"
        ));
        assert!(!run_dir_matches("/tmp/other", "0123456789abcdef"));
    }

    #[test]
    fn synthetic_status_parsing_and_capabilities() {
        let status = "Name:\tkebab\nUid:\t1000\t1000\t1000\t1000\nGid:\t1000\t1000\t1000\t1000\nThreads:\t1\nPPid:\t42\nNSpid:\t7\nCapEff:\t000001ffffffffff\n";
        assert_eq!(status_uid(status).unwrap(), 1000);
        assert_eq!(status_gid(status).unwrap(), 1000);
        assert_eq!(status_number(status, "Threads").unwrap(), 1);
        assert_eq!(status_number(status, "PPid").unwrap(), 42);
        assert_eq!(status_nspid(status).unwrap(), 7);
        let caps = cap_eff(status).unwrap();
        assert!(has_cap(caps, CAP_SYS_ADMIN));
        assert!(has_cap(caps, CAP_BPF));
        assert!(has_cap(caps, CAP_PERFMON));
        assert!(!has_cap(0, CAP_BPF));
        assert!(!has_cap(u64::MAX, 64));
        assert_eq!(cap_eff("Name:\tx\n"), Err("CapEff missing"));
    }

    #[test]
    fn synthetic_register_decode_both_arches() {
        // Identical synthetic register snapshots decoded through both
        // architectures: the probe only decides which register it reads.
        for raw in [
            0u64,
            1,
            512,
            8192,
            0xFFFF_FFFF,
            0xFFFF_FFFE,
            0xFFFF_FFFF_FFFF_FFFF,
            0x0000_0001_0000_0200,
        ] {
            assert_eq!(x86_64_declared(raw), arm64_declared(raw));
            assert_eq!(x86_64_result(raw), arm64_result(raw));
            assert_eq!(x86_64_declared(raw), signed_low32(raw));
        }
        assert_eq!(x86_64_declared(0xFFFF_FFFF_FFFF_FFFF), -1);
        assert_eq!(arm64_declared(0xFFFF_FFFF_FFFF_FFFF), -1);
        assert_eq!(x86_64_result(0xFFFF_FFFF_FFFF_FFFD), -3);
        assert_eq!(arm64_result(0x0000_0001_0000_0200), 512);
        assert_eq!(arm64_declared(0xFFFF_FFFF_FFFF_F000), -4096);
        assert_eq!(x86_64_result(0x0000_0000_8000_0000), -2_147_483_648);
    }

    #[test]
    fn synthetic_read_failure_loss_and_timeout_injection() {
        // A failed bounded read carries no payload but still pairs.
        let mut entry = record(KIND_ENTER, 1, 512, false);
        entry.wire.flags = FLAG_READ_FAILED;
        let mut exit = record(KIND_EXIT, 1, 512, false);
        exit.wire.result = 512;
        let a = analyze(&[entry, exit]);
        assert_eq!(a.payload_bytes(), 0);
        assert_eq!(a.rows[0].result, Some(512));
        assert!(a.rows[0].flags & FLAG_READ_FAILED != 0);

        // Submitted but not consumed must be visible as lost.
        let mut raw = [0u64; COUNTER_SLOTS];
        raw[CNT_SUBMITTED as usize] = 8;
        let report = counter_report(&raw, &a);
        assert_eq!(report.lost, 6);
        assert!(!check_positive(&report, &a.aliases()).is_empty());

        // An entry that never returned is expired, never a silent pass.
        let only_entry = analyze(&[record(KIND_ENTER, 7, 16, true)]);
        let expired = counter_report(&[0u64; COUNTER_SLOTS], &only_entry);
        assert_eq!(expired.expired, 1);
        assert!(!check_positive(&expired, &only_entry.aliases()).is_empty());
    }

    #[test]
    fn synthetic_sha256_known_vectors() {
        assert_eq!(
            sha256_hex(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(
            sha256_hex(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"
        );
        // A 64-byte input forces a second block after padding.
        assert_eq!(sha256(&[0u8; 64]).len(), 32);
        assert_ne!(sha256(b"abc"), sha256(b"abd"));
    }

    #[test]
    fn synthetic_verdict_and_cleanup_policy() {
        assert_eq!(final_verdict(true, false, false), Verdict::Pass);
        assert_eq!(final_verdict(true, false, true), Verdict::Partial);
        assert_eq!(final_verdict(false, false, false), Verdict::Fail);
        assert_eq!(final_verdict(false, false, true), Verdict::Fail);
        assert_eq!(final_verdict(false, true, false), Verdict::Blocked);
        assert_eq!(final_verdict(true, true, true), Verdict::Blocked);
        assert_eq!(Verdict::Pass.exit_code(), 0);
        assert_eq!(Verdict::Fail.exit_code(), 1);
        assert_eq!(Verdict::Partial.exit_code(), 4);
        assert_eq!(Verdict::Blocked.exit_code(), 3);
        // Only a clean PASS may delete the evidence directory.
        assert!(should_remove_run_dir(Verdict::Pass));
        assert!(!should_remove_run_dir(Verdict::Partial));
        assert!(!should_remove_run_dir(Verdict::Fail));
        assert!(!should_remove_run_dir(Verdict::Blocked));
        assert!(Verdict::Pass.is_pass());
        assert_eq!(Verdict::Partial.as_str(), "PARTIAL");
    }

    #[test]
    fn synthetic_stdio_fd_contract() {
        let ok = vec![
            (0u32, "pipe:[101]".to_string()),
            (1u32, "pipe:[102]".to_string()),
            (2u32, "/dev/null".to_string()),
        ];
        assert_eq!(stdio_fds_ok(&ok), Ok(()));

        let mut inherited = ok.clone();
        inherited.push((3, "/dev/ptmx".to_string()));
        assert_eq!(
            stdio_fds_ok(&inherited),
            Err("workload holds an inherited descriptor above 2")
        );

        let socket = vec![
            (0u32, "pipe:[1]".to_string()),
            (1u32, "socket:[42]".to_string()),
            (2u32, "/dev/null".to_string()),
        ];
        assert_eq!(
            stdio_fds_ok(&socket),
            Err("workload holds a socket descriptor")
        );

        let mut regular = ok.clone();
        regular[2] = (
            2,
            "/tmp/kebab-observer-tls-fixture-x/manifest.json".to_string(),
        );
        assert_eq!(
            stdio_fds_ok(&regular),
            Err("workload holds a non-stdio descriptor")
        );

        let pty = vec![
            (0u32, "pipe:[1]".to_string()),
            (1u32, "pipe:[2]".to_string()),
            (2u32, "/dev/ptmx".to_string()),
        ];
        assert_eq!(
            stdio_fds_ok(&pty),
            Err("workload holds a non-stdio descriptor")
        );

        let missing = vec![
            (0u32, "pipe:[1]".to_string()),
            (1u32, "pipe:[2]".to_string()),
        ];
        assert_eq!(
            stdio_fds_ok(&missing),
            Err("workload is missing a stdio descriptor")
        );
        assert_eq!(stdio_fds_ok(&[]), Err("workload exposes no descriptors"));
    }

    fn sample_session(closeout: bool) -> Value {
        let run_id = "0123456789abcdef";
        let mut value = json!({
            "record": "session",
            "schema": 1,
            "batch_run_id": run_id,
            "run_id": run_id,
            "run_dir_path": run_dir_path(run_id),
            "session_id": "positive",
            "boot_id": "9c8d7c02-8ce7-42ae-9820-f950a179a621",
            "started_at_boot_ns": 108_320_000_000u64,
            "wrong_birth_test": false,
            "target_tgid": 259,
            "target_ppid": 253,
            "target_start_ticks": 10831,
            "target_uid": 1000,
            "target_gid": 1000,
            "target_nspid": 259,
            "pidns_dev": 4,
            "pidns_ino": 4_026_532_230u64,
            "nsfs_magic": NSFS_MAGIC,
            "cgroup_path": "/",
            "cgroup_id": 1,
            "cgroup_inode_expected": 1,
            "libssl": {
                "path": "/usr/lib/x86_64-linux-gnu/libssl.so.3",
                "dev": 2096,
                "ino": 1594,
                "size": 1_109_952u64,
                "mtime_s": 1_760_000_000u64,
                "build_id": "4f18cdec63410d6a18616448c922fb580ca2c292",
                "maps_exec_match": true,
            },
            "attach": {
                "mechanism": "perf_event_open_uprobe_pmu",
                "pmu_type": 9,
                "pid_scope": "Some(tgid)",
                "symbol": "SSL_write",
                "symbol_version": "OPENSSL_3.0.0",
                "file_offset": 0x1a2b30,
            },
            "program_ids": [112, 113],
            "link_ids": [],
            "identity_checks": [{"stage": "spawn", "passed": true}],
            "workload": {
                "path": "/codexprojects/oneplus8t/rust/kebab-observer/target/tls-fixture/tls-fixture",
                "size": 54960,
                "sha256": "d327315b859a8f3df9731cb005d9770af8834a41628654a1c6d2fafa23985257",
            },
            "target": {"tgid": 259, "ppid": 253, "start_ticks": 10831},
        });
        if closeout {
            value["record"] = json!("session_closeout");
            value["passed"] = json!(true);
            value["failure_reasons"] = json!([]);
            value["issues"] = json!([]);
            value["counters_available"] = json!(true);
            value["counters"] = json!({
                "submitted": 8, "consumed": 8, "lost": 0, "read_failed": 0,
                "map_failed": 0, "expired": 0, "truncated": 1,
                "identity_rejected": 0, "len_invalid": 1, "abnormal_ret": 0,
                "user_read_attempts": 3, "user_read_bytes": 4992,
                "payload_bytes": 4992, "depth_overflow": 0, "unpaired": 0,
                "entries": 4, "exits": 4,
            });
            value["requests"] = json!([{
                "direction": "c2s", "declared": 512, "captured": 512,
                "truncated": false, "read_attempted": true, "ret": 512,
                "payload_match": true, "alias": 1u64,
            }]);
            value["peer"] = json!({
                "c2s_received": 8704, "c2s_match": true,
                "s2c_received": 384, "s2c_match": true,
            });
            value["cleanup"] = json!({
                "prog_ids_absent": true,
                "child_gone": true,
                "fd_before": 4,
                "fd_after": 4,
            });
        }
        value
    }

    fn sample_batch() -> Value {
        let run_id = "0123456789abcdef";
        json!({
            "record": "batch",
            "schema": 1,
            "batch_run_id": run_id,
            "run_dir_path": run_dir_path(run_id),
            "boot_id": "9c8d7c02-8ce7-42ae-9820-f950a179a621",
            "kernel": "6.6.87.2-microsoft-standard-WSL2",
            "mode": "wsl_x86_64_owned_fixture",
            "euid": 0,
            "embedded_bpf": [
                {"name": "tls_fixture.x86_64.bpf.o", "bytes": 50848, "fnv1a64": "3d0e6a4255eb6dbc"},
                {"name": "syscalls.bpf.o", "bytes": 39600, "fnv1a64": "b2fcd81758f12503"},
            ],
        })
    }

    fn sample_crash_summary() -> Value {
        let run_id = "fedcba9876543210";
        json!({
            "record": "crash_summary",
            "schema": 1,
            "source": "parent_after_kill",
            "child_closeout_absent": true,
            "source_session": "crash_recovery",
            "source_run_id": run_id,
            "source_run_dir_path": run_dir_path(run_id),
            "crash_collector_pid": 628,
            "crash_program_ids": [138, 139],
            "target_tgid": 630,
            "target_ppid": 628,
            "target_start_ticks": 11283,
            "target_uid": 1000,
            "target_alive_before_kill": true,
            "program_ids_present_before_kill": true,
            "killed_by_signal": true,
            "program_ids_absent_within_3s": true,
            "fixture_gone_within_11s": true,
            "manifest_before_kill": {"readable": true, "ok": true, "files": ["manifest.json"]},
            "cleanup": {
                "prog_ids_absent": true,
                "child_gone": true,
                "run_dir_removed": true,
                "verified_before_remove": true,
                "run_dir_path": run_dir_path(run_id),
            },
        })
    }

    #[test]
    fn synthetic_session_schema_full_field_set() {
        let seed = sample_session(false);
        let closeout = sample_session(true);
        assert_eq!(session_record_ok(&seed, false), Ok(()));
        assert_eq!(session_record_ok(&closeout, true), Ok(()));
        // A seed is not a closeout and vice versa.
        assert!(session_record_ok(&seed, true).is_err());
        assert!(session_record_ok(&closeout, false).is_err());
        // Every contract key is load bearing.
        for key in SESSION_REQUIRED_KEYS {
            let mut broken = closeout.clone();
            broken.as_object_mut().unwrap().remove(key);
            assert!(
                session_record_ok(&broken, true).is_err(),
                "dropping {key} must be rejected"
            );
        }
        for key in SESSION_CLOSEOUT_KEYS {
            let mut broken = closeout.clone();
            broken.as_object_mut().unwrap().remove(key);
            assert!(
                session_record_ok(&broken, true).is_err(),
                "dropping closeout {key} must be rejected"
            );
        }
        // Nested types, not just presence.
        let cases: Vec<(&str, Value)> = vec![
            ("target_uid", json!(1001)),
            ("target_gid", json!(0)),
            ("target_tgid", json!(0)),
            ("target_ppid", json!(0)),
            ("target_start_ticks", json!(0)),
            ("target_nspid", json!("259")),
            ("pidns_dev", json!(0)),
            ("pidns_ino", json!(0)),
            ("nsfs_magic", json!(0x1234)),
            ("cgroup_path", json!("relative")),
            ("cgroup_id", json!(0)),
            ("cgroup_inode_expected", json!(0)),
            ("started_at_boot_ns", json!(0)),
            ("program_ids", json!([112])),
            ("program_ids", json!([112, 0])),
            ("link_ids", json!(["x"])),
            ("identity_checks", json!([])),
            ("workload", Value::Null),
            ("libssl", json!({"path": "/x/libssl.so.3"})),
            ("attach", json!({"mechanism": "kprobe"})),
            ("peer", Value::Null),
            ("requests", json!([{"direction": "c2s"}])),
            (
                "cleanup",
                json!({"own_prog_ids_absent": true, "own_child_gone": true, "fd_before": 1, "fd_after": 1}),
            ),
            (
                "cleanup",
                json!({"prog_ids_absent": true, "child_gone": true, "fd_before": 1}),
            ),
            ("counters", json!({})),
            ("batch_run_id", json!("0011223344556677")),
            ("boot_id", json!("short")),
            ("session_id", json!("crash_probe")),
            ("session_id", json!("external_app")),
        ];
        for (key, replacement) in cases {
            let mut broken = closeout.clone();
            broken[key] = replacement.clone();
            assert!(
                session_record_ok(&broken, true).is_err(),
                "rejecting {key} = {replacement} must fail"
            );
        }
        // Nested field level mutations.
        let nested: Vec<(&str, &str, Value)> = vec![
            ("libssl", "mtime_s", json!(0)),
            ("libssl", "maps_exec_match", json!(false)),
            ("libssl", "build_id", json!("deadbeef")),
            ("libssl", "path", json!("libssl.so.3")),
            ("attach", "pmu_type", json!(0)),
            ("attach", "symbol_version", json!("")),
            ("attach", "file_offset", json!(0)),
            ("attach", "pid_scope", json!("None")),
            ("attach", "symbol", json!("SSL_read")),
            ("attach", "mechanism", json!("perf_event_open_kprobe")),
            ("workload", "sha256", json!("short")),
            ("workload", "size", json!(0)),
            ("workload", "path", json!("")),
        ];
        for (outer, inner, replacement) in nested {
            let mut broken = closeout.clone();
            broken[outer][inner] = replacement.clone();
            assert!(
                session_record_ok(&broken, true).is_err(),
                "rejecting {outer}.{inner} = {replacement} must fail"
            );
        }
        // Request entries are typed one by one.
        for key in REQUEST_KEYS {
            let mut broken = closeout.clone();
            broken["requests"][0].as_object_mut().unwrap().remove(key);
            assert!(
                session_record_ok(&broken, true).is_err(),
                "dropping request.{key} must be rejected"
            );
        }
        // The seed carries the same workload and identity block.
        let mut null_workload = seed.clone();
        null_workload["workload"] = Value::Null;
        assert!(session_record_ok(&null_workload, false).is_err());
        let mut no_attach = seed.clone();
        no_attach.as_object_mut().unwrap().remove("attach");
        assert!(session_record_ok(&no_attach, false).is_err());
    }

    #[test]
    fn synthetic_batch_and_crash_summary_schema() {
        let batch = sample_batch();
        assert_eq!(batch_record_ok(&batch), Ok(()));
        for key in BATCH_REQUIRED_KEYS {
            let mut broken = batch.clone();
            broken.as_object_mut().unwrap().remove(key);
            assert!(
                batch_record_ok(&broken).is_err(),
                "batch must require {key}"
            );
        }
        let mut wrong_kernel = batch.clone();
        wrong_kernel["kernel"] = json!("");
        assert!(batch_record_ok(&wrong_kernel).is_err());
        let mut old_shape = batch.clone();
        old_shape.as_object_mut().unwrap().remove("embedded_bpf");
        old_shape["objects"] = json!([]);
        assert!(batch_record_ok(&old_shape).is_err());
        let mut bad_fnv = batch.clone();
        bad_fnv["embedded_bpf"][0]["fnv1a64"] = json!("xyz");
        assert!(batch_record_ok(&bad_fnv).is_err());

        let crash = sample_crash_summary();
        assert_eq!(crash_summary_ok(&crash), Ok(()));
        for key in CRASH_SUMMARY_KEYS {
            let mut broken = crash.clone();
            broken.as_object_mut().unwrap().remove(key);
            assert!(
                crash_summary_ok(&broken).is_err(),
                "crash must require {key}"
            );
        }
        let mut claims_closeout = crash.clone();
        claims_closeout["child_closeout_absent"] = json!(false);
        assert!(crash_summary_ok(&claims_closeout).is_err());
        let mut wrong_source = crash.clone();
        wrong_source["source"] = json!("child");
        assert!(crash_summary_ok(&wrong_source).is_err());
        let mut not_killed = crash.clone();
        not_killed["killed_by_signal"] = json!(false);
        assert!(crash_summary_ok(&not_killed).is_err());
        let mut as_closeout = crash.clone();
        as_closeout["record"] = json!("session_closeout");
        assert!(session_record_ok(&as_closeout, true).is_err());
    }

    #[test]
    fn synthetic_pairing_reentrant_pairs() {
        // Two calls nested on one thread: enter(a) enter(b) exit(b) exit(a).
        let records = vec![
            record_of(KIND_ENTER, 7, 11, 512, 0, 0),
            record_of(KIND_ENTER, 7, 12, 384, 0, 1),
            record_of(KIND_EXIT, 7, 12, 0, 384, 1),
            record_of(KIND_EXIT, 7, 11, 0, 512, 0),
        ];
        let analysis = analyze(&records);
        assert_eq!(analysis.entries, 2);
        assert_eq!(analysis.exits, 2);
        assert_eq!(analysis.rows.len(), 2);
        assert_eq!(analysis.nested, 1);
        assert_eq!(analysis.expired, 0);
        assert_eq!(analysis.unpaired, 0);
        assert_eq!(analysis.duplicate_exits, 0);
        assert!(analysis.rows.iter().all(|r| r.result.is_some()));
        // The inner call is the one at depth 1 and it pairs with the inner exit.
        assert_eq!(analysis.rows[1].depth, 1);
        assert_eq!(analysis.rows[1].result, Some(384));
        // An exit without an entry, then a reused call id.
        let orphans = vec![
            record_of(KIND_EXIT, 7, 99, 0, 1, 0),
            record_of(KIND_ENTER, 7, 5, 16, 0, 0),
            record_of(KIND_EXIT, 7, 5, 0, 16, 0),
            record_of(KIND_EXIT, 7, 5, 0, 16, 0),
        ];
        let analysis = analyze(&orphans);
        assert_eq!(analysis.unpaired, 1);
        assert_eq!(analysis.duplicate_exits, 1);
        assert_eq!(analysis.expired, 0);
        // An entry without any exit expires and never looks paired.
        let expired = vec![record_of(KIND_ENTER, 7, 8, 8, 0, 0)];
        let analysis = analyze(&expired);
        assert_eq!(analysis.expired, 1);
        assert_eq!(analysis.rows[0].result, None);
    }

    #[test]
    fn synthetic_run_dir_symlink_and_identity() {
        let run_id = "0123456789abcdef";
        let path = run_dir_path(run_id);
        let good = RunDirFacts {
            path: path.clone(),
            run_id: run_id.to_string(),
            is_symlink: false,
            is_dir: true,
            uid: 0,
            mode: 0o700,
            manifest_run_id: Some(run_id.to_string()),
        };
        assert_eq!(run_dir_removable(&good), Ok(()));
        let symlink = RunDirFacts {
            is_symlink: true,
            ..good.clone()
        };
        assert_eq!(run_dir_removable(&symlink), Err("run dir is a symlink"));
        let not_dir = RunDirFacts {
            is_dir: false,
            ..good.clone()
        };
        assert!(run_dir_removable(&not_dir).is_err());
        let other_owner = RunDirFacts {
            uid: 1000,
            ..good.clone()
        };
        assert!(run_dir_removable(&other_owner).is_err());
        let loose_mode = RunDirFacts {
            mode: 0o777,
            ..good.clone()
        };
        assert!(run_dir_removable(&loose_mode).is_err());
        let wrong_path = RunDirFacts {
            path: "/tmp/kebab-observer-tls-fixture-0011223344556677".to_string(),
            ..good.clone()
        };
        assert!(run_dir_removable(&wrong_path).is_err());
        let mismatch = RunDirFacts {
            manifest_run_id: Some("0011223344556677".to_string()),
            ..good.clone()
        };
        assert_eq!(
            run_dir_removable(&mismatch),
            Err("manifest run_id mismatch")
        );
        let missing = RunDirFacts {
            manifest_run_id: None,
            ..good.clone()
        };
        assert_eq!(run_dir_removable(&missing), Err("manifest run_id missing"));
        let traversal = RunDirFacts {
            path: "/tmp/kebab-observer-tls-fixture-0123456789abcdef/../x".to_string(),
            ..good.clone()
        };
        assert!(run_dir_removable(&traversal).is_err());
    }

    #[test]
    fn synthetic_identity_rejection_matrix() {
        let expected = IdentityKey {
            tgid: 259,
            ppid: 253,
            start_ticks: 10831,
            uid: 1000,
            gid: 1000,
            ns_dev: 4,
            ns_ino: 4_026_532_230,
        };
        assert_eq!(identity_ok(&expected, &expected), Ok(()));
        let uninitialised = IdentityKey {
            start_ticks: 0,
            ..expected
        };
        assert!(identity_ok(&uninitialised, &expected).is_err());
        let cases: Vec<(&str, IdentityKey)> = vec![
            (
                "tgid",
                IdentityKey {
                    tgid: 260,
                    ..expected
                },
            ),
            (
                "ppid",
                IdentityKey {
                    ppid: 1,
                    ..expected
                },
            ),
            (
                "start_ticks",
                IdentityKey {
                    start_ticks: 10832,
                    ..expected
                },
            ),
            ("uid", IdentityKey { uid: 0, ..expected }),
            ("gid", IdentityKey { gid: 0, ..expected }),
            (
                "ns_dev",
                IdentityKey {
                    ns_dev: 59,
                    ..expected
                },
            ),
            (
                "ns_ino",
                IdentityKey {
                    ns_ino: 4_026_532_231,
                    ..expected
                },
            ),
        ];
        for (what, observed) in cases {
            assert!(
                identity_ok(&expected, &observed).is_err(),
                "{what} drift must be rejected"
            );
        }
        // Same tgid and birth but a different namespace is still a rejection.
        let same_pid_other_ns = IdentityKey {
            ns_ino: 1,
            ..expected
        };
        assert_eq!(
            identity_ok(&expected, &same_pid_other_ns),
            Err("pid namespace changed")
        );
    }

    #[test]
    fn synthetic_object_reuse_and_mismatch() {
        let view = |index: usize, alias: u32| RequestView {
            index,
            direction: SCRIPT_DIRECTIONS[index - 1],
            declared: SCRIPT_LENGTHS[index - 1],
            captured: 0,
            truncated: false,
            read_attempted: false,
            ret: SCRIPT_LENGTHS[index - 1],
            payload_match: true,
            alias,
            depth: 0,
        };
        let good = vec![view(1, 1), view(2, 2), view(3, 1), view(4, 1)];
        assert_eq!(alias_generation_ok(&good), Ok(2));
        // The client object changes mid session: reuse, not a new generation.
        let reused = vec![view(1, 1), view(2, 2), view(3, 3), view(4, 1)];
        assert_eq!(
            alias_generation_ok(&reused),
            Err("alias changed inside one direction (object reuse or mismatch)")
        );
        // Client and server collapse into one alias: a probe mix-up.
        let collapsed = vec![view(1, 1), view(2, 1), view(3, 1), view(4, 1)];
        assert_eq!(
            alias_generation_ok(&collapsed),
            Err("client and server objects share one alias")
        );
        let zero = vec![view(1, 0), view(2, 2)];
        assert_eq!(
            alias_generation_ok(&zero),
            Err("alias is not a run-local opaque id")
        );
        let single = vec![view(1, 1)];
        assert_eq!(
            alias_generation_ok(&single),
            Err("not enough calls to bind an alias")
        );
        // An alias is a 32-bit token by construction; the observer never claims
        // anything beyond opaque identity for one run.
        let width = std::mem::size_of::<u32>() * 8;
        assert_eq!(width, 32);
        assert!(u64::from(u32::MAX) == 0xffff_ffff);
    }

    #[test]
    fn synthetic_alias_id_is_opaque_and_not_a_pointer() {
        // The production predicate the session runs on every bound call.
        assert!(alias_id_is_opaque(1));
        assert!(alias_id_is_opaque(ALIAS_MAX_ID));
        assert!(!alias_id_is_opaque(0));
        assert!(!alias_id_is_opaque(ALIAS_MAX_ID + 1));
        // A pointer-derived value is far outside the counter range.
        assert!(!alias_id_is_opaque(0xdead_beef));
        assert!(!alias_id_is_opaque(0x0000_7f4a));
        // alias_generation_ok refuses such a value in a real call row.
        let mut pointer_like = RequestView {
            index: 1,
            direction: SCRIPT_DIRECTIONS[0],
            declared: SCRIPT_LENGTHS[0],
            captured: 0,
            truncated: false,
            read_attempted: false,
            ret: SCRIPT_LENGTHS[0],
            payload_match: true,
            alias: 0x7f00_0010,
            depth: 0,
        };
        assert_eq!(
            alias_generation_ok(&[pointer_like.clone()]),
            Err("not enough calls to bind an alias")
        );
        pointer_like.index = 2;
        pointer_like.direction = SCRIPT_DIRECTIONS[1];
        pointer_like.declared = SCRIPT_LENGTHS[1];
        assert_eq!(
            alias_generation_ok(&[
                pointer_like.clone(),
                RequestView {
                    alias: 1,
                    ..pointer_like.clone()
                }
            ]),
            Err("alias is not a run-local opaque id")
        );
        // Nothing in the printed records can carry the raw SSL pointer.
        let closeout = sample_session(true);
        let text = closeout.to_string();
        assert!(!text.contains("0x7f"));
        assert!(closeout.get("salt").is_none());
        assert!(closeout.get("ssl_ptr").is_none());
        for request in closeout["requests"].as_array().unwrap() {
            let alias = request["alias"].as_u64().unwrap() as u32;
            assert!(alias_id_is_opaque(alias));
        }
    }

    #[test]
    fn synthetic_elf_symbol_version_and_offset() {
        let image = build_symbol_elf();
        let info = elf_symbol_info(&image, "SSL_write").expect("symbol must resolve");
        assert_eq!(info.version, "OPENSSL_3.0.0");
        assert_eq!(info.value, 0x2010);
        // Segment maps vaddr 0x2000 to file offset 0x300.
        assert_eq!(info.file_offset, 0x310);
        assert!(elf_symbol_info(&image, "SSL_read").is_err());
        assert!(elf_symbol_info(b"not an elf", "SSL_write").is_err());
        // A truncated image must be refused, not misparsed.
        assert!(elf_symbol_info(&image[..40], "SSL_write").is_err());
    }

    #[test]
    fn synthetic_workload_binding_and_cleanup_gate() {
        let before = json!({"path": "/x/tls-fixture", "size": 54864, "sha256": "c".repeat(64)});
        let mut after = before.clone();
        assert!(workload_binding_ok(&before, &after));
        after["sha256"] = json!("d".repeat(64));
        assert!(!workload_binding_ok(&before, &after));
        after = before.clone();
        after["size"] = json!(54865);
        assert!(!workload_binding_ok(&before, &after));
        after = before.clone();
        after["path"] = json!("/y/tls-fixture");
        assert!(!workload_binding_ok(&before, &after));
        assert!(!workload_binding_ok(&json!({}), &json!({})));

        let run_id = "0011223344556677";
        let batch_cleanup = json!({
            "own_prog_ids_absent": true,
            "own_child_gone": true,
            "fd_equal": true,
            "run_dir_removed": true,
            "run_dir_path": run_dir_path(run_id),
        });
        assert_eq!(cleanup_fields_ok(&batch_cleanup), Ok(()));
        let mut bad = batch_cleanup.clone();
        bad["run_dir_path"] = json!("/tmp/other");
        assert!(cleanup_fields_ok(&bad).is_err());
        // Session cleanup uses the contract names only.
        let session_cleanup = json!({
            "prog_ids_absent": true,
            "child_gone": true,
            "fd_before": 4,
            "fd_after": 4,
        });
        assert_eq!(session_cleanup_ok(&session_cleanup), Ok(()));
        let mut batch_names = session_cleanup.clone();
        batch_names["own_child_gone"] = json!(true);
        assert!(session_cleanup_ok(&batch_names).is_err());
        let mut missing = session_cleanup.clone();
        missing.as_object_mut().unwrap().remove("fd_after");
        assert!(session_cleanup_ok(&missing).is_err());
        // The gate that decides deletion is the verdict, not a flag.
        assert!(!should_remove_run_dir(final_verdict(true, false, true)));
        assert!(should_remove_run_dir(final_verdict(true, false, false)));
    }

    /// Build a tiny ELF64 with one PT_LOAD, a .dynsym entry and a verdef.
    fn build_symbol_elf() -> Vec<u8> {
        let mut image = vec![0u8; 0x340];
        // Header.
        image[0..4].copy_from_slice(b"\x7fELF");
        image[4] = 2; // ELFCLASS64
        image[5] = 1; // little endian
        image[6] = 1; // version
        image[0x10..0x12].copy_from_slice(&2u16.to_le_bytes()); // ET_EXEC
        image[0x12..0x14].copy_from_slice(&62u16.to_le_bytes()); // x86_64
        image[0x20..0x28].copy_from_slice(&64u64.to_le_bytes()); // e_phoff
        image[0x28..0x30].copy_from_slice(&0x80u64.to_le_bytes()); // e_shoff
        image[0x34..0x36].copy_from_slice(&64u16.to_le_bytes()); // e_ehsize
        image[0x36..0x38].copy_from_slice(&56u16.to_le_bytes()); // e_phentsize
        image[0x38..0x3a].copy_from_slice(&1u16.to_le_bytes()); // e_phnum
        image[0x3a..0x3c].copy_from_slice(&64u16.to_le_bytes()); // e_shentsize
        image[0x3c..0x3e].copy_from_slice(&6u16.to_le_bytes()); // e_shnum
        image[0x3e..0x40].copy_from_slice(&5u16.to_le_bytes()); // e_shstrndx
                                                                // One PT_LOAD: vaddr 0x2000 -> offset 0x300.
        let ph = 64;
        image[ph..ph + 4].copy_from_slice(&1u32.to_le_bytes());
        image[ph + 4..ph + 8].copy_from_slice(&5u32.to_le_bytes());
        image[ph + 8..ph + 16].copy_from_slice(&0x300u64.to_le_bytes());
        image[ph + 0x10..ph + 0x18].copy_from_slice(&0x2000u64.to_le_bytes());
        image[ph + 0x20..ph + 0x28].copy_from_slice(&0x40u64.to_le_bytes());
        image[ph + 0x28..ph + 0x30].copy_from_slice(&0x40u64.to_le_bytes());
        // Section name table.
        let shstr = b"\0.dynstr\0.dynsym\0.gnu.version\0.gnu.version_d\0.shstrtab\0";
        let shstr_off = 0x200usize;
        image[shstr_off..shstr_off + shstr.len()].copy_from_slice(shstr);
        let dynstr: &[u8] = b"\0SSL_write\0OPENSSL_3.0.0\0";
        let dynstr_off = 0x240usize;
        image[dynstr_off..dynstr_off + dynstr.len()].copy_from_slice(dynstr);
        let dynsym_off = 0x280usize;
        // Two entries: the null symbol and SSL_write.
        let sym = dynsym_off + 24;
        image[sym..sym + 4].copy_from_slice(&1u32.to_le_bytes()); // st_name
        image[sym + 4] = 0x12; // STB_GLOBAL | STT_FUNC
        image[sym + 6..sym + 8].copy_from_slice(&6u16.to_le_bytes()); // st_shndx
        image[sym + 8..sym + 16].copy_from_slice(&0x2010u64.to_le_bytes()); // st_value
        image[sym + 16..sym + 24].copy_from_slice(&8u64.to_le_bytes());
        let versym_off = 0x2b0usize;
        image[versym_off..versym_off + 2].copy_from_slice(&0u16.to_le_bytes());
        image[versym_off + 2..versym_off + 4].copy_from_slice(&3u16.to_le_bytes());
        // verdef: index 3 -> OPENSSL_3.0.0
        let verdef_off = 0x2c0usize;
        let vd = verdef_off;
        image[vd..vd + 2].copy_from_slice(&1u16.to_le_bytes()); // vd_version
        image[vd + 2..vd + 4].copy_from_slice(&1u16.to_le_bytes()); // vd_flags
        image[vd + 4..vd + 6].copy_from_slice(&3u16.to_le_bytes()); // vd_ndx
        image[vd + 6..vd + 8].copy_from_slice(&1u16.to_le_bytes()); // vd_cnt
        image[vd + 12..vd + 16].copy_from_slice(&20u32.to_le_bytes()); // vd_aux
        image[vd + 16..vd + 20].copy_from_slice(&0u32.to_le_bytes()); // vd_next
        image[vd + 20..vd + 24].copy_from_slice(&11u32.to_le_bytes()); // vda_name
                                                                       // Section headers.
        let sections: [(u32, u32, usize, usize); 6] = [
            (0, 0, 0, 0),
            (1, 3, dynstr_off, dynstr.len()),
            (9, 11, dynsym_off, 48),
            (17, 0x6fff_fff6, versym_off, 4),
            (30, 0x6fff_fffd, verdef_off, 28),
            (45, 3, shstr_off, shstr.len()),
        ];
        for (index, (name, kind, off, size)) in sections.into_iter().enumerate() {
            let base = 0x80 + index * 64;
            image[base..base + 4].copy_from_slice(&name.to_le_bytes());
            image[base + 4..base + 8].copy_from_slice(&kind.to_le_bytes());
            image[base + 0x18..base + 0x20].copy_from_slice(&(off as u64).to_le_bytes());
            image[base + 0x20..base + 0x28].copy_from_slice(&(size as u64).to_le_bytes());
            if index == 2 {
                image[base + 0x38..base + 0x40].copy_from_slice(&24u64.to_le_bytes());
            }
        }
        image
    }

    fn record_of(
        kind: u32,
        tid: u64,
        call_id: u64,
        declared: i64,
        result: i64,
        depth: u32,
    ) -> Record {
        Record {
            wire: WireEvent {
                ts_ns: call_id,
                sequence: call_id,
                call_id,
                actor_pid_tid: (7u64 << 32) | tid,
                actor_start_ns: 1,
                actor_cgroup: 1,
                namespace_pid_tid: (7u64 << 32) | tid,
                namespace_inode: 1,
                declared,
                result,
                alias: 11,
                captured: 0,
                kind,
                flags: 0,
                cpu: 0,
                uid: 1000,
                depth,
                spare: 0,
            },
            payload: [0u8; SAMPLE_CAP],
        }
    }

    #[test]
    fn synthetic_payload_gap_reports_without_bytes() {
        let mut bytes = expected_payload(16);
        bytes[5] = 0xff;
        let gap = payload_gap(2, 8192, 16, &bytes);
        assert_eq!(gap.index, 2);
        assert_eq!(gap.mismatch_at, Some(5));
        assert_eq!(gap.prefix_match.len(), 8);
        assert!(gap.prefix_match[0]);
        assert!(!gap.prefix_match[5]);
    }

    #[test]
    fn synthetic_bpf_declared_budget_covers_all_nine_maps() {
        assert_eq!(BPF_MAP_DECLS.len(), 9);
        assert_eq!(BPF_MAP_NAMES.len(), 9);
        assert_eq!(BPF_ALLOC_DECLARED_BYTES, 8_388_608);
        assert_eq!(BPF_MAP_DECLARED_BYTES, 1_082_852);
        let declared = BPF_MAP_DECLARED_BYTES;
        let limit = BPF_ALLOC_DECLARED_BYTES;
        assert!(declared <= limit);
        // The pre-fix total silently dropped ALIAS (384 B) and ALIASSEQ (12 B).
        assert_ne!(BPF_MAP_DECLARED_BYTES, 1_082_456);
        assert_eq!(1_082_456 + 384 + 12, BPF_MAP_DECLARED_BYTES);
        assert_eq!(declared_map_bytes(&BPF_MAP_ALIAS), Ok(384));
        assert_eq!(declared_map_bytes(&BPF_MAP_ALIASSEQ), Ok(12));
        let expected = [
            ("CONFIG", 384u64),
            ("COUNTERS", 192),
            ("SEQUENCE", 12),
            ("CALLID", 12),
            ("EVENTS", 1_048_576),
            ("INFLIGHT", 32_768),
            ("DEPTH", 512),
            ("ALIAS", 384),
            ("ALIASSEQ", 12),
        ];
        let mut total = 0u64;
        for (name, bytes) in expected {
            let decl = BPF_MAP_DECLS
                .iter()
                .find(|decl| decl.name == name)
                .unwrap_or_else(|| panic!("map {name} missing"));
            assert_eq!(declared_map_bytes(decl), Ok(bytes), "map {name}");
            total += bytes;
        }
        assert_eq!(total, BPF_MAP_DECLARED_BYTES);
        assert_eq!(declared_bpf_map_bytes(&BPF_MAP_DECLS), Ok(1_082_852));
    }

    #[test]
    fn synthetic_bpf_declared_budget_rejects_missing_maps_and_alias_drift() {
        let without_alias: Vec<BpfMapDecl> = BPF_MAP_DECLS
            .iter()
            .copied()
            .filter(|decl| decl.name != "ALIAS")
            .collect();
        assert_eq!(without_alias.len(), 8);
        assert_eq!(
            declared_bpf_map_bytes(&without_alias),
            Err(BpfBudgetError::Missing("ALIAS"))
        );
        let without_aliasseq: Vec<BpfMapDecl> = BPF_MAP_DECLS
            .iter()
            .copied()
            .filter(|decl| decl.name != "ALIASSEQ")
            .collect();
        assert_eq!(
            declared_bpf_map_bytes(&without_aliasseq),
            Err(BpfBudgetError::Missing("ALIASSEQ"))
        );
        // An empty set must not price as zero.
        assert_eq!(
            declared_bpf_map_bytes(&[]),
            Err(BpfBudgetError::Missing("CONFIG"))
        );
        // A duplicate name is not silently counted twice.
        let mut duplicated = BPF_MAP_DECLS.to_vec();
        duplicated.push(BPF_MAP_CONFIG);
        assert_eq!(
            declared_bpf_map_bytes(&duplicated),
            Err(BpfBudgetError::Duplicate("CONFIG"))
        );
        // A map the object does not declare is refused.
        let mut renamed = BPF_MAP_DECLS;
        let last = renamed.len() - 1;
        renamed[last] = BpfMapDecl::new("ALIASSEQ_X", BPF_MAP_TYPE_ARRAY, 4, 8, 1);
        assert_eq!(
            declared_bpf_map_bytes(&renamed),
            Err(BpfBudgetError::Unexpected("ALIASSEQ_X"))
        );
        // Changing the ALIAS capacity changes the total: nothing is cached.
        let mut larger_alias = BPF_MAP_DECLS;
        let alias = larger_alias
            .iter()
            .position(|decl| decl.name == "ALIAS")
            .expect("ALIAS declaration");
        larger_alias[alias] = BpfMapDecl::new("ALIAS", BPF_MAP_TYPE_HASH, 8, 4, 64);
        assert_eq!(declared_bpf_map_bytes(&larger_alias), Ok(1_083_236));
        assert_ne!(declared_bpf_map_bytes(&larger_alias), Ok(1_082_852));
        // Retyping ALIAS as a ring buffer is a shape error, not a silent zero.
        let mut retyped = BPF_MAP_DECLS;
        retyped[alias] = BpfMapDecl::new("ALIAS", BPF_MAP_TYPE_RINGBUF, 8, 4, 32);
        assert_eq!(
            declared_bpf_map_bytes(&retyped),
            Err(BpfBudgetError::BadShape { name: "ALIAS" })
        );
        // An unsupported map type is refused.
        let mut unsupported = BPF_MAP_DECLS;
        unsupported[last] = BpfMapDecl::new("ALIASSEQ", 13, 4, 8, 1);
        assert_eq!(
            declared_bpf_map_bytes(&unsupported),
            Err(BpfBudgetError::UnsupportedType {
                name: "ALIASSEQ",
                map_type: 13
            })
        );
    }

    #[test]
    fn synthetic_bpf_declared_budget_rejects_overflow_and_over_limit() {
        let index_of = |name: &str| {
            BPF_MAP_DECLS
                .iter()
                .position(|decl| decl.name == name)
                .unwrap_or_else(|| panic!("map {name} missing"))
        };
        // Per-entry and total arithmetic are checked, not wrapping.
        let mut huge = BPF_MAP_DECLS;
        huge[index_of("COUNTERS")] =
            BpfMapDecl::new("COUNTERS", BPF_MAP_TYPE_ARRAY, u32::MAX, u32::MAX, u32::MAX);
        assert_eq!(declared_bpf_map_bytes(&huge), Err(BpfBudgetError::Overflow));
        // Exactly the 8 MiB declaration limit is accepted ...
        let mut at_limit = BPF_MAP_DECLS;
        at_limit[index_of("CONFIG")] = BpfMapDecl::new("CONFIG", BPF_MAP_TYPE_ARRAY, 4, 8, 608_845);
        assert_eq!(declared_bpf_map_bytes(&at_limit), Ok(8_388_608));
        // ... one entry more is refused.
        let mut over = BPF_MAP_DECLS;
        over[index_of("CONFIG")] = BpfMapDecl::new("CONFIG", BPF_MAP_TYPE_ARRAY, 4, 8, 608_846);
        assert_eq!(
            declared_bpf_map_bytes(&over),
            Err(BpfBudgetError::OverLimit {
                total: 8_388_620,
                limit: 8_388_608
            })
        );
        // A 16 MiB ring is over the limit without overflowing anything.
        let mut big_ring = BPF_MAP_DECLS;
        big_ring[index_of("EVENTS")] =
            BpfMapDecl::new("EVENTS", BPF_MAP_TYPE_RINGBUF, 0, 0, 16 << 20);
        assert_eq!(
            declared_bpf_map_bytes(&big_ring),
            Err(BpfBudgetError::OverLimit {
                total: 16_811_492,
                limit: 8_388_608
            })
        );
        // Shape contradictions are refused.
        let mut ring_key = BPF_MAP_DECLS;
        ring_key[index_of("EVENTS")] =
            BpfMapDecl::new("EVENTS", BPF_MAP_TYPE_RINGBUF, 4, 0, 1_048_576);
        assert_eq!(
            declared_bpf_map_bytes(&ring_key),
            Err(BpfBudgetError::BadShape { name: "EVENTS" })
        );
        let mut odd_ring = BPF_MAP_DECLS;
        odd_ring[index_of("EVENTS")] =
            BpfMapDecl::new("EVENTS", BPF_MAP_TYPE_RINGBUF, 0, 0, 1_000_000);
        assert_eq!(
            declared_bpf_map_bytes(&odd_ring),
            Err(BpfBudgetError::BadShape { name: "EVENTS" })
        );
        let mut no_entries = BPF_MAP_DECLS;
        no_entries[index_of("DEPTH")] = BpfMapDecl::new("DEPTH", BPF_MAP_TYPE_HASH, 4, 4, 0);
        assert_eq!(
            declared_bpf_map_bytes(&no_entries),
            Err(BpfBudgetError::BadShape { name: "DEPTH" })
        );
    }

    #[test]
    fn synthetic_bpf_declared_budget_matches_object_map_source() {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/bpf/tls_fixture.bpf.c");
        let source = std::fs::read_to_string(path).expect("read bpf/tls_fixture.bpf.c");
        let map_lines = source
            .lines()
            .filter(|line| line.contains("SEC(\"maps\")"))
            .count();
        assert_eq!(map_lines, 9, "the object source must declare nine maps");
        let mut parsed = Vec::new();
        for line in source.lines() {
            let Some(rest) = line
                .trim()
                .strip_prefix("struct bpf_map_def SEC(\"maps\") ")
            else {
                continue;
            };
            let (name, tail) = rest.split_once('=').expect("map declaration has '='");
            let body = tail
                .trim()
                .trim_end_matches(';')
                .strip_prefix('{')
                .and_then(|body| body.strip_suffix('}'))
                .expect("map declaration has a brace body");
            let fields: Vec<u32> = body
                .split(',')
                .map(|field| field.trim().parse::<u32>().expect("map field is a u32"))
                .collect();
            assert_eq!(fields.len(), 7, "map {name} has seven fields");
            parsed.push((
                name.trim().to_string(),
                fields[0],
                fields[1],
                fields[2],
                fields[3],
            ));
        }
        assert_eq!(parsed.len(), BPF_MAP_DECLS.len());
        for (decl, (name, map_type, key_size, value_size, max_entries)) in
            BPF_MAP_DECLS.iter().zip(parsed.iter())
        {
            assert_eq!(decl.name, name.as_str(), "map name/order");
            assert_eq!(decl.map_type, *map_type, "map {name} type");
            assert_eq!(decl.key_size, *key_size, "map {name} key size");
            assert_eq!(decl.value_size, *value_size, "map {name} value size");
            assert_eq!(decl.max_entries, *max_entries, "map {name} entries");
        }
        assert_eq!(declared_bpf_map_bytes(&BPF_MAP_DECLS), Ok(1_082_852));
    }

    fn align_up(value: usize, align: usize) -> usize {
        value.div_ceil(align) * align
    }

    fn add_string(table: &mut Vec<u8>, text: &str) -> u32 {
        let offset = table.len() as u32;
        table.extend_from_slice(text.as_bytes());
        table.push(0);
        offset
    }

    struct FixtureSection {
        name: u32,
        section_type: u32,
        offset: usize,
        size: usize,
        link: u32,
        entry_size: u64,
    }

    fn write_section_header(
        image: &mut [u8],
        table_offset: usize,
        index: usize,
        section: &FixtureSection,
    ) {
        let base = table_offset + index * 64;
        image[base..base + 4].copy_from_slice(&section.name.to_le_bytes());
        image[base + 4..base + 8].copy_from_slice(&section.section_type.to_le_bytes());
        image[base + 24..base + 32].copy_from_slice(&(section.offset as u64).to_le_bytes());
        image[base + 32..base + 40].copy_from_slice(&(section.size as u64).to_le_bytes());
        image[base + 40..base + 44].copy_from_slice(&section.link.to_le_bytes());
        image[base + 56..base + 64].copy_from_slice(&section.entry_size.to_le_bytes());
    }

    struct FixtureLayout {
        headers: usize,
        symbols: usize,
        records: usize,
    }

    fn map_records(declarations: &[(&str, u32, u32, u32, u32)]) -> Vec<u8> {
        let mut records = Vec::new();
        for (_, map_type, key_size, value_size, max_entries) in declarations {
            for field in [*map_type, *key_size, *value_size, *max_entries, 0, 0, 0] {
                records.extend_from_slice(&field.to_le_bytes());
            }
        }
        records
    }

    /// Minimal little-endian ELF64 BPF object carrying a `maps` section, one
    /// object symbol per declaration and a `.symtab` naming them.
    fn synthetic_map_object(
        declarations: &[(&str, u32, u32, u32, u32)],
    ) -> (Vec<u8>, FixtureLayout) {
        let mut strings = vec![0u8];
        let maps_name = add_string(&mut strings, "maps");
        let symtab_name = add_string(&mut strings, ".symtab");
        let strtab_name = add_string(&mut strings, ".strtab");
        let names: Vec<u32> = declarations
            .iter()
            .map(|(name, ..)| add_string(&mut strings, name))
            .collect();
        let records = map_records(declarations);
        let strings_offset = 64usize;
        let records_offset = align_up(strings_offset + strings.len(), 4);
        let symbols_offset = align_up(records_offset + records.len(), 8);
        let symbol_count = declarations.len() + 1;
        let symbols_size = symbol_count * 24;
        let headers_offset = align_up(symbols_offset + symbols_size, 8);
        let mut image = vec![0u8; headers_offset + 4 * 64];
        image[..4].copy_from_slice(b"\x7fELF");
        image[4] = 2; // ELF64
        image[5] = 1; // little endian
        image[6] = 1; // version
        image[16..18].copy_from_slice(&1u16.to_le_bytes()); // ET_REL
        image[18..20].copy_from_slice(&247u16.to_le_bytes()); // EM_BPF
        image[20..24].copy_from_slice(&1u32.to_le_bytes());
        image[40..48].copy_from_slice(&(headers_offset as u64).to_le_bytes());
        image[52..54].copy_from_slice(&64u16.to_le_bytes());
        image[58..60].copy_from_slice(&64u16.to_le_bytes());
        image[60..62].copy_from_slice(&4u16.to_le_bytes());
        image[62..64].copy_from_slice(&1u16.to_le_bytes());
        image[strings_offset..strings_offset + strings.len()].copy_from_slice(&strings);
        image[records_offset..records_offset + records.len()].copy_from_slice(&records);
        for (position, name) in names.iter().enumerate() {
            let base = symbols_offset + (position + 1) * 24;
            image[base..base + 4].copy_from_slice(&name.to_le_bytes());
            image[base + 4] = 0x11; // GLOBAL | OBJECT
            image[base + 6..base + 8].copy_from_slice(&2u16.to_le_bytes()); // maps section
            image[base + 8..base + 16].copy_from_slice(&((position * 28) as u64).to_le_bytes());
            image[base + 16..base + 24].copy_from_slice(&28u64.to_le_bytes());
        }
        write_section_header(
            &mut image,
            headers_offset,
            1,
            &FixtureSection {
                name: strtab_name,
                section_type: 3, // SHT_STRTAB
                offset: strings_offset,
                size: strings.len(),
                link: 0,
                entry_size: 0,
            },
        );
        write_section_header(
            &mut image,
            headers_offset,
            2,
            &FixtureSection {
                name: maps_name,
                section_type: 1, // SHT_PROGBITS
                offset: records_offset,
                size: records.len(),
                link: 0,
                entry_size: 0,
            },
        );
        write_section_header(
            &mut image,
            headers_offset,
            3,
            &FixtureSection {
                name: symtab_name,
                section_type: 2, // SHT_SYMTAB
                offset: symbols_offset,
                size: symbols_size,
                link: 1,
                entry_size: 24,
            },
        );
        (
            image,
            FixtureLayout {
                headers: headers_offset,
                symbols: symbols_offset,
                records: records_offset,
            },
        )
    }

    fn nine_map_declarations() -> [(&'static str, u32, u32, u32, u32); 9] {
        [
            ("CONFIG", BPF_MAP_TYPE_ARRAY, 4, 8, 32),
            ("COUNTERS", BPF_MAP_TYPE_ARRAY, 4, 8, 16),
            ("SEQUENCE", BPF_MAP_TYPE_ARRAY, 4, 8, 1),
            ("CALLID", BPF_MAP_TYPE_ARRAY, 4, 8, 1),
            ("EVENTS", BPF_MAP_TYPE_RINGBUF, 0, 0, 1 << 20),
            ("INFLIGHT", BPF_MAP_TYPE_HASH, 8, 24, 1024),
            ("DEPTH", BPF_MAP_TYPE_HASH, 4, 4, 64),
            ("ALIAS", BPF_MAP_TYPE_HASH, 8, 4, 32),
            ("ALIASSEQ", BPF_MAP_TYPE_ARRAY, 4, 8, 1),
        ]
    }

    #[test]
    fn synthetic_bpf_object_maps_match_table_when_built() {
        let (image, _) = synthetic_map_object(&nine_map_declarations());
        assert_eq!(object_declared_bpf_map_bytes(&image), Ok(1_082_852));
        let declarations = object_map_declarations(&image).expect("parse synthetic object");
        assert_eq!(declarations.len(), 9);
        for (declaration, known) in declarations.iter().zip(BPF_MAP_DECLS.iter()) {
            assert_eq!(declaration.name, known.name);
            assert_eq!(declaration.map_type, known.map_type);
            assert_eq!(declaration.key_size, known.key_size);
            assert_eq!(declaration.value_size, known.value_size);
            assert_eq!(declaration.max_entries, known.max_entries);
        }
        // The object that verify commands 2/3 rebuild is exercised when it is
        // present; the synthetic image above is the portable coverage.
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/bpf/tls_fixture.x86_64.bpf.o");
        match std::fs::read(path) {
            Ok(real) => {
                assert_eq!(object_declared_bpf_map_bytes(&real), Ok(1_082_852));
                assert_eq!(
                    object_map_declarations(&real)
                        .expect("parse built object")
                        .len(),
                    9
                );
            }
            Err(error) => println!("built BPF object not readable ({error}); synthetic ELF only"),
        }
    }

    #[test]
    fn synthetic_bpf_object_maps_reject_missing_extra_and_field_drift() {
        let mut missing_alias = nine_map_declarations().to_vec();
        missing_alias.retain(|(name, ..)| *name != "ALIAS");
        let (image, _) = synthetic_map_object(&missing_alias);
        assert_eq!(
            object_declared_bpf_map_bytes(&image),
            Err(MapObjectError::MissingMap("ALIAS".to_string()))
        );
        let mut missing_aliasseq = nine_map_declarations().to_vec();
        missing_aliasseq.retain(|(name, ..)| *name != "ALIASSEQ");
        let (image, _) = synthetic_map_object(&missing_aliasseq);
        assert_eq!(
            object_declared_bpf_map_bytes(&image),
            Err(MapObjectError::MissingMap("ALIASSEQ".to_string()))
        );
        let mut extra = nine_map_declarations().to_vec();
        extra.push(("EXTRA", BPF_MAP_TYPE_ARRAY, 4, 8, 1));
        let (image, _) = synthetic_map_object(&extra);
        assert_eq!(
            object_declared_bpf_map_bytes(&image),
            Err(MapObjectError::UnexpectedMap("EXTRA".to_string()))
        );
        let mut drifted = nine_map_declarations();
        drifted[7] = ("ALIAS", BPF_MAP_TYPE_HASH, 8, 4, 64);
        let (image, _) = synthetic_map_object(&drifted);
        assert_eq!(
            object_declared_bpf_map_bytes(&image),
            Err(MapObjectError::FieldMismatch {
                name: "ALIAS".to_string(),
                field: "max_entries",
                object: 64,
                table: 32,
            })
        );
        // Exactly the 8 MiB limit still has to agree with the table.
        let mut at_limit = nine_map_declarations();
        at_limit[0] = ("CONFIG", BPF_MAP_TYPE_ARRAY, 4, 8, 608_845);
        let (image, _) = synthetic_map_object(&at_limit);
        assert_eq!(
            object_declared_bpf_map_bytes(&image),
            Err(MapObjectError::FieldMismatch {
                name: "CONFIG".to_string(),
                field: "max_entries",
                object: 608_845,
                table: 32,
            })
        );
    }

    #[test]
    fn synthetic_bpf_object_maps_reject_unpriceable_overflow_and_over_limit() {
        let mut unsupported = nine_map_declarations();
        unsupported[8] = ("ALIASSEQ", 13, 4, 8, 1);
        let (image, _) = synthetic_map_object(&unsupported);
        assert_eq!(
            object_declared_bpf_map_bytes(&image),
            Err(MapObjectError::Unpriceable {
                name: "ALIASSEQ".to_string(),
                reason: MapPriceError::UnsupportedType(13),
            })
        );
        let mut ring_key = nine_map_declarations();
        ring_key[4] = ("EVENTS", BPF_MAP_TYPE_RINGBUF, 4, 0, 1 << 20);
        let (image, _) = synthetic_map_object(&ring_key);
        assert_eq!(
            object_declared_bpf_map_bytes(&image),
            Err(MapObjectError::Unpriceable {
                name: "EVENTS".to_string(),
                reason: MapPriceError::BadShape,
            })
        );
        let mut huge = nine_map_declarations();
        huge[1] = ("COUNTERS", BPF_MAP_TYPE_ARRAY, u32::MAX, u32::MAX, u32::MAX);
        let (image, _) = synthetic_map_object(&huge);
        assert_eq!(
            object_declared_bpf_map_bytes(&image),
            Err(MapObjectError::Unpriceable {
                name: "COUNTERS".to_string(),
                reason: MapPriceError::Overflow,
            })
        );
        // Three declarations that each fit but whose sum overflows u64.
        let mut sum_overflow = nine_map_declarations();
        for index in [0usize, 1, 3] {
            sum_overflow[index] = (
                sum_overflow[index].0,
                BPF_MAP_TYPE_ARRAY,
                u32::MAX,
                u32::MAX,
                1 << 30,
            );
        }
        let (image, _) = synthetic_map_object(&sum_overflow);
        assert_eq!(
            object_declared_bpf_map_bytes(&image),
            Err(MapObjectError::Overflow)
        );
        let mut big_ring = nine_map_declarations();
        big_ring[4] = ("EVENTS", BPF_MAP_TYPE_RINGBUF, 0, 0, 16 << 20);
        let (image, _) = synthetic_map_object(&big_ring);
        assert_eq!(
            object_declared_bpf_map_bytes(&image),
            Err(MapObjectError::OverLimit {
                total: 16_811_492,
                limit: 8_388_608,
            })
        );
    }

    #[test]
    fn synthetic_bpf_object_maps_reject_malformed_objects() {
        assert_eq!(
            object_declared_bpf_map_bytes(b"not an elf"),
            Err(MapObjectError::Elf("no ELF magic"))
        );
        let (mut image, _) = synthetic_map_object(&nine_map_declarations());
        image[18..20].copy_from_slice(&62u16.to_le_bytes()); // x86_64, not BPF
        assert_eq!(
            object_declared_bpf_map_bytes(&image),
            Err(MapObjectError::Elf("not a BPF object"))
        );
        // A maps section that is not a whole number of 28-byte records.
        let (mut image, layout) = synthetic_map_object(&nine_map_declarations());
        let maps_header = layout.headers + 2 * 64;
        image[maps_header + 32..maps_header + 40]
            .copy_from_slice(&((9 * 28 - 4) as u64).to_le_bytes());
        assert_eq!(
            object_declared_bpf_map_bytes(&image),
            Err(MapObjectError::Truncated)
        );
        // A maps section header whose name no longer resolves.
        let (mut image, layout) = synthetic_map_object(&nine_map_declarations());
        let maps_header = layout.headers + 2 * 64;
        image[maps_header..maps_header + 4].copy_from_slice(&0u32.to_le_bytes());
        assert_eq!(
            object_declared_bpf_map_bytes(&image),
            Err(MapObjectError::Section("maps section is missing"))
        );
        // A map symbol that is not attached to the maps section covers 8 of 9.
        let (mut image, layout) = synthetic_map_object(&nine_map_declarations());
        image[layout.symbols + 24 + 6..layout.symbols + 24 + 8]
            .copy_from_slice(&0u16.to_le_bytes());
        assert_eq!(
            object_declared_bpf_map_bytes(&image),
            Err(MapObjectError::Section(
                "maps section is not fully declared"
            ))
        );
        // A map symbol with the wrong record size.
        let (mut image, layout) = synthetic_map_object(&nine_map_declarations());
        image[layout.symbols + 24 + 16..layout.symbols + 24 + 24]
            .copy_from_slice(&16u64.to_le_bytes());
        assert_eq!(
            object_declared_bpf_map_bytes(&image),
            Err(MapObjectError::Malformed("maps symbol has the wrong size"))
        );
        // A record whose reserved map_flags field is not zero.
        let (mut image, layout) = synthetic_map_object(&nine_map_declarations());
        image[layout.records + 16..layout.records + 20].copy_from_slice(&1u32.to_le_bytes());
        assert_eq!(
            object_declared_bpf_map_bytes(&image),
            Err(MapObjectError::Malformed(
                "map flags/id/pinning are not zero"
            ))
        );
        // A .symtab section with the wrong type.
        let (mut image, layout) = synthetic_map_object(&nine_map_declarations());
        let symtab_header = layout.headers + 3 * 64;
        image[symtab_header + 4..symtab_header + 8].copy_from_slice(&1u32.to_le_bytes());
        assert_eq!(
            object_declared_bpf_map_bytes(&image),
            Err(MapObjectError::Section(".symtab has the wrong type"))
        );
    }
}
