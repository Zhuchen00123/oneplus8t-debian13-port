#![forbid(unsafe_code)]

pub mod tls_core;
pub mod tls_attach_core;
pub mod track_core;

use serde::Serialize;

pub const EVENT_BYTES: usize = 96;

#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct Event {
    pub boottime_ns: u64,
    pub actor_tgid: u32,
    pub actor_tid: u32,
    pub actor_start_ns: u64,
    pub actor_cgroup: u64,
    pub sequence: u64,
    pub namespace_tgid: u32,
    pub namespace_tid: u32,
    pub namespace_inode: u64,
    pub kind: &'static str,
    pub operation: i64,
    pub result: i64,
    pub cpu: u32,
    pub uid: u32,
    pub code: u32,
    pub flags: u32,
    pub extra: u32,
}

pub fn decode(bytes: &[u8]) -> Result<Event, &'static str> {
    if bytes.len() != EVENT_BYTES {
        return Err("event length mismatch");
    }
    let q = |offset| u64::from_le_bytes(bytes[offset..offset + 8].try_into().unwrap());
    let d = |offset| u32::from_le_bytes(bytes[offset..offset + 4].try_into().unwrap());
    let kind = match d(72) {
        1 => "sys_enter",
        2 => "sys_exit",
        3 => "binder_send",
        4 => "binder_received",
        _ => return Err("unknown event kind"),
    };
    Ok(Event {
        boottime_ns: q(0),
        actor_tgid: (q(8) >> 32) as u32,
        actor_tid: q(8) as u32,
        actor_start_ns: q(16),
        actor_cgroup: q(24),
        sequence: q(32),
        namespace_tgid: (q(40) >> 32) as u32,
        namespace_tid: q(40) as u32,
        namespace_inode: q(48),
        operation: q(56) as i64,
        result: q(64) as i64,
        kind,
        cpu: d(76),
        uid: d(80),
        code: d(84),
        flags: d(88),
        extra: d(92),
    })
}

pub fn start_ticks(stat: &str) -> Result<u64, &'static str> {
    let end = stat.rfind(')').ok_or("missing stat comm terminator")?;
    // After comm: state is field3; starttime is field22.
    stat[end + 1..]
        .split_whitespace()
        .nth(19)
        .ok_or("truncated proc stat")?
        .parse()
        .map_err(|_| "invalid proc starttime")
}

pub fn aux_clock_ticks(auxv: &[u8]) -> Result<u64, &'static str> {
    if !auxv.len().is_multiple_of(16) {
        return Err("invalid 64-bit auxv");
    }
    for row in auxv.as_chunks::<16>().0 {
        let tag = u64::from_le_bytes(row[..8].try_into().unwrap());
        let val = u64::from_le_bytes(row[8..].try_into().unwrap());
        if tag == 0 {
            break;
        }
        if tag == 17 {
            return if val == 100 {
                Ok(val)
            } else {
                Err("unsupported clock ticks")
            };
        }
    }
    Err("AT_CLKTCK missing")
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn wire_signed_values_and_actor_identity() {
        let mut b = [0u8; EVENT_BYTES];
        b[8..16].copy_from_slice(&((42u64 << 32) | 47).to_le_bytes());
        b[64..72].copy_from_slice(&(-13i64).to_le_bytes());
        b[72..76].copy_from_slice(&2u32.to_le_bytes());
        let e = decode(&b).unwrap();
        assert_eq!((e.actor_tgid, e.actor_tid, e.result), (42, 47, -13));
        assert_eq!(e.kind, "sys_exit");
    }
    #[test]
    fn wire_rejects_corruption() {
        assert!(decode(&[0u8; 95]).is_err());
        assert!(decode(&[0u8; 96]).is_err());
        assert!(decode(&[0u8; 97]).is_err());
    }
    #[test]
    fn stat_comm_can_contain_spaces_and_parentheses() {
        let tail = (3..=21)
            .map(|i| i.to_string())
            .collect::<Vec<_>>()
            .join(" ");
        assert_eq!(
            start_ticks(&format!("42 (a ) name) {tail} 123456 0")),
            Ok(123456)
        );
        assert!(start_ticks("42 (x) S 1").is_err());
    }
    #[test]
    fn auxv_requires_supported_abi() {
        let mut b = Vec::new();
        b.extend(17u64.to_le_bytes());
        b.extend(100u64.to_le_bytes());
        assert_eq!(aux_clock_ticks(&b), Ok(100));
        b[8..].copy_from_slice(&250u64.to_le_bytes());
        assert!(aux_clock_ticks(&b).is_err());
        assert!(aux_clock_ticks(&b[..15]).is_err());
    }
}
