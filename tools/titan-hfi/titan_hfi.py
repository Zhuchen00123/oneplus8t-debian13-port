#!/usr/bin/env python3
"""Titan (Kona CAMSS ICP/BPS/IPE) HFI wire codec, host side. Pure stdlib.

The authoritative contract is 'titan_hfi.h' in this directory; this module is
the byte-level mirror of it.  Every number here is cross-checked against the C
header by 'test_titan_hfi.py' and every opcode payload size is re-derived from
the shipped firmware by 'extract_opcodes.py'.

Firmware: CAMERA_ICP.elf, 3,922,232 B,
SHA-256 cb98cec20d1073d4b2872342b3db2185b57422c73e9e7f327887f1b039a1ceb9
(CICP.FW.1.0.82-4-ICA_REG / AAAAAA / HARV-PSOMANCH, titan.icp.1.0.release82).

The two things this module exists to get right:

1. the async command packet layout, which upstream hfi_cmd_async gets wrong by
   4 bytes, and
2. the payload placement rule enforced by the firmware's CheckCommandSize().

Firmware facts encoded here (measured, see docs/camera-titan-hfi-b0-20261111.md):

    struct HFI_CMD_IPEBPS_ASYNC_COMMAND_TYPE  (40 B for one handle)
      +0   nSize
      +4   ePacketType
      +8   eCmdOpcode
      +12  nUserArg1 (u64)      <- upstream puts fw_handle here
      +20  nUserArg2 (u64)
      +28  nNumHandles          <- upstream puts num_handles at +32
      +32  hHandles[n]
      +32+4*n  payload          (inline, or a u32 SMMU address)

    CheckCommandSize(pkt, expected_payload_bytes, direct) @0x22560:
      direct   : nSize >= expected + 32 + 4*nNumHandles
      indirect : nSize >= 40
    plus, in the receive loop: 0 < nSize <= 6019 and nSize % 4 == 0.
"""

from __future__ import annotations

import struct

__all__ = [
    "HFI_CMD_IPEBPS_ASYNC_COMMAND_DIRECT",
    "HFI_CMD_IPEBPS_ASYNC_COMMAND_INDIRECT",
    "HFI_CMD_IPEBPS_CREATE_HANDLE",
    "HFI_CMD_SYS_INIT",
    "HFI_MSG_EVENT_NOTIFY",
    "OPCODE_PAYLOAD_SIZE",
    "OPCODE_NAMES",
    "async_payload_off",
    "check_command_size",
    "decode",
    "decode_queue_table",
    "encode_async",
    "encode_create_handle",
    "encode_set_property",
    "encode_sys_command",
    "firmware_accepts",
    "property_items",
    "queue_header_bytes",
]

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------

MAX_PKT_SIZE = 6019            # 0x1783, ICPHostInterface_driverTask limit
QTBL_HDR_SIZE = 24
Q_HDR_SIZE = 956               # firmware hardcodes the 0x3bc stride
QTBL_QHDR0_OFFSET = 24
Q_MAX = 3
QTBL_MAP_WINDOW = 4096         # tryMemMapping(qtbl, 0x1000)
QTBL_VERSION = 0xFFFFFFFF

Q_CMD, Q_MSG, Q_DBG = 0, 1, 2

# queue header field offsets
Q_OFF_STATUS = 60
Q_OFF_START_ADDR = 124
Q_OFF_TYPE = 188
Q_OFF_Q_SIZE = 252
Q_OFF_PKT_SIZE = 316
Q_OFF_PKT_DROP_CNT = 380
Q_OFF_RX_WM = 444
Q_OFF_TX_WM = 508
Q_OFF_RX_REQ = 572
Q_OFF_TX_REQ = 636
Q_OFF_RX_IRQ = 700
Q_OFF_TX_IRQ = 764
Q_OFF_READ_IDX = 828
Q_OFF_WRITE_IDX = 892

DOMAIN_ICP, DOMAIN_IPE_BPS, DOMAIN_CDM, DOMAIN_DBG = 0, 1, 2, 3

HFI_CMD_SYS_INIT = 0x00010001
HFI_CMD_SYS_PC_PREP = 0x00010002
HFI_CMD_SYS_SET_PROPERTY = 0x00010003
HFI_CMD_SYS_GET_PROPERTY = 0x00010004   # not implemented by the firmware
HFI_CMD_SYS_PING = 0x00010005
HFI_CMD_SYS_RESET = 0x00010006

HFI_MSG_SYS_INIT_DONE = 0x00020001
HFI_MSG_SYS_PC_PREP_DONE = 0x00020002
HFI_MSG_SYS_DEBUG = 0x00020003
HFI_MSG_SYS_PING_ACK = 0x00020006
HFI_MSG_SYS_RESET_ACK = 0x00020007
HFI_MSG_EVENT_NOTIFY = 0x00020008

HFI_CMD_IPEBPS_CREATE_HANDLE = 0x01010008
HFI_CMD_IPEBPS_ASYNC_COMMAND_DIRECT = 0x0101000A
HFI_CMD_IPEBPS_ASYNC_COMMAND_INDIRECT = 0x0101000E
HFI_MSG_IPEBPS_CREATE_HANDLE_ACK = 0x01020008
HFI_MSG_IPEBPS_ASYNC_DIRECT_ACK = 0x0102000A
HFI_MSG_IPEBPS_ASYNC_INDIRECT_ACK = 0x0102000E

HFI_CMD_DBG_TRACE_BUFFER = 0x03010008
HFI_CMD_DBG_REG_DUMP = 0x03010009

# handle types: firmware rejects > 6, then groups with a bitmask
HANDLE_TYPE_MAX = 6
HANDLE_MASK_BPS = 0x62         # types 1, 5, 6
HANDLE_MASK_IPE = 0x1C         # types 2, 3, 4

# async packet geometry
ASYNC_FIXED_HDR = 32           # bytes before hHandles[]
ASYNC_MIN_SIZE = 40            # 32 + 4 * 1 + 4

# opcode -> payload bytes == the immediate handed to CheckCommandSize()
# (single source of truth is TITAN_HFI_OPCODE_TABLE in titan_hfi.h)
OPCODE_PAYLOAD_SIZE = {
    0x01: 840,      # _BpsConfigIo
    0x02: 2360,     # _BpsFrameProcess
    0x03: 16,       # _BpsAbort
    0x04: 8,        # _BpsDestroy
    0x05: 1600,     # _IpeConfigIo
    0x06: 14328,    # _IpeFrameProcess
    0x07: 16,       # _IpeAbort
    0x08: 8,        # _IpeDestroy
    0x09: 16,       # _BpsWaitForIpe
    0x0A: 16,       # _BpsWaitForBps
    0x0B: 16,       # _IpeWaitForBps
    0x0C: 16,       # _IpeWaitForIpe
    0x0D: 16,       # unknown, 16 B, never reaches hardware
    0x0E: 20,       # _IpeBpsMemMap (map)
    0x0F: 20,       # _IpeBpsMemMap (unmap)
}

OPCODE_NAMES = {
    0x01: "BPS_CONFIG_IO",
    0x02: "BPS_FRAME_PROCESS",
    0x03: "BPS_ABORT",
    0x04: "BPS_DESTROY",
    0x05: "IPE_CONFIG_IO",
    0x06: "IPE_FRAME_PROCESS",
    0x07: "IPE_ABORT",
    0x08: "IPE_DESTROY",
    0x09: "BPS_WAIT_FOR_IPE",
    0x0A: "BPS_WAIT_FOR_BPS",
    0x0B: "IPE_WAIT_FOR_BPS",
    0x0C: "IPE_WAIT_FOR_IPE",
    0x0D: "UNKNOWN_0D",
    0x0E: "MEM_MAP",
    0x0F: "MEM_UNMAP",
}

# Opcodes whose firmware branch opens with "nNumHandles == 1 else reject":
#   ldr r0, [pkt, #0x1c] at 0x1fc30, then per branch
#   cmp r0, #0x1 ; bne.w 0x1fc26   (the shared error return)
# Derived by extract_opcodes.py; the opcodes without the check are 0x03, 0x07,
# 0x0D, 0x0E and 0x0F.
OPCODES_REQUIRING_ONE_HANDLE = frozenset({
    0x01, 0x02, 0x04, 0x05, 0x06, 0x08, 0x09, 0x0A, 0x0B, 0x0C,
})

_U32 = struct.Struct("<I")
_U64 = struct.Struct("<Q")


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------

def async_payload_off(num_handles):
    """Payload offset inside an async command packet: 32 + 4*n."""
    if num_handles < 0:
        raise ValueError("num_handles must be >= 0")
    return ASYNC_FIXED_HDR + 4 * num_handles


def async_direct_min_size(payload_bytes, num_handles):
    """Smallest DIRECT packet carrying payload_bytes inline."""
    return payload_bytes + ASYNC_FIXED_HDR + 4 * num_handles


def check_command_size(nsize, payload_bytes, num_handles, direct):
    """Mirror of firmware CheckCommandSize() @0x22560 plus the queue limits.

    Returns (ok, reason); reason is "" when ok.
    """
    if nsize <= 0:
        return False, "nSize must be non-zero"
    if nsize & 3:
        return False, "nSize must be 4-byte aligned"
    if nsize > MAX_PKT_SIZE:
        return False, "nSize %d exceeds the %d B receive limit" % (nsize, MAX_PKT_SIZE)
    if direct:
        need = async_direct_min_size(payload_bytes, num_handles)
        if nsize < need:
            return False, ("DIRECT needs nSize >= %d (payload %d + 32 + 4*%d), got %d"
                           % (need, payload_bytes, num_handles, nsize))
        return True, ""
    if nsize < ASYNC_MIN_SIZE:
        return False, "INDIRECT needs nSize >= %d, got %d" % (ASYNC_MIN_SIZE, nsize)
    return True, ""


# --------------------------------------------------------------------------
# encoders
# --------------------------------------------------------------------------

def _hdr(nsize, pkt_type):
    return struct.pack("<II", nsize, pkt_type)


def encode_async(opcode, handles, payload, direct, user_arg1=0, user_arg2=0,
                 indirect_addr=None, enforce_payload_size=True):
    """Build one async command packet (host -> ICP).

    opcode        one of OPCODE_PAYLOAD_SIZE
    handles       sequence of handle ids, one u32 each; the firmware wants
                  exactly one for CONFIG_IO / FRAME_PROCESS / IPE_WAIT_FOR_IPE
    payload       payload bytes; for INDIRECT they are NOT placed in the packet
                  but their length is still validated against the opcode table
    direct        True  -> inline payload (respects the 6019 B packet limit)
                  False -> packet stores indirect_addr at 32 + 4*n
    indirect_addr u32 SMMU address holding the payload (INDIRECT only)

    Returns bytes.  Raises ValueError if the firmware would reject the packet.
    """
    if opcode not in OPCODE_PAYLOAD_SIZE:
        raise ValueError("unknown opcode 0x%02x" % opcode)
    handles = list(handles)
    if not handles:
        raise ValueError("at least one handle is required")
    if len(handles) > 16:
        raise ValueError("unreasonable handle count %d" % len(handles))
    if opcode in OPCODES_REQUIRING_ONE_HANDLE and len(handles) != 1:
        raise ValueError("opcode 0x%02x requires exactly one handle" % opcode)
    for h in handles:
        if not 0 <= h <= 0xFFFFFFFF:
            raise ValueError("handle 0x%x does not fit in u32" % h)

    payload = bytes(payload)
    expected = OPCODE_PAYLOAD_SIZE[opcode]
    if enforce_payload_size and len(payload) != expected:
        raise ValueError("opcode 0x%02x expects a %d B payload, got %d B"
                         % (opcode, expected, len(payload)))

    n = len(handles)
    pkt_type = (HFI_CMD_IPEBPS_ASYNC_COMMAND_DIRECT if direct
                else HFI_CMD_IPEBPS_ASYNC_COMMAND_INDIRECT)

    head = (struct.pack("<III", 0, pkt_type, opcode)
            + _U64.pack(user_arg1)
            + _U64.pack(user_arg2)
            + _U32.pack(n)
            + b"".join(_U32.pack(h) for h in handles))

    if direct:
        body = payload
    else:
        if indirect_addr is None:
            raise ValueError("INDIRECT needs indirect_addr (a u32 SMMU address)")
        if not 0 <= indirect_addr <= 0xFFFFFFFF:
            raise ValueError("indirect_addr 0x%x does not fit in u32" % indirect_addr)
        body = _U32.pack(indirect_addr)

    nsize = len(head) + len(body)
    ok, reason = check_command_size(nsize, len(payload), n, direct)
    if not ok:
        raise ValueError(reason)

    return _hdr(nsize, pkt_type) + head[8:] + body


def encode_create_handle(handle_type, user_arg1=0, user_arg2=0):
    """Build HFI_CMD_IPEBPS_CREATE_HANDLE (28 B)."""
    if not 0 <= handle_type <= HANDLE_TYPE_MAX:
        raise ValueError("handle type %d is rejected by the firmware" % handle_type)
    return struct.pack("<III", 28, HFI_CMD_IPEBPS_CREATE_HANDLE, handle_type) \
        + _U64.pack(user_arg1) + _U64.pack(user_arg2)


def encode_sys_command(pkt_type, client_data=None):
    """Build SYS_INIT (8 B) or PING / RESET (16 B)."""
    if pkt_type == HFI_CMD_SYS_INIT:
        if client_data is not None:
            raise ValueError("HFI_CMD_SYS_INIT carries no client data")
        return _hdr(8, pkt_type)
    if pkt_type in (HFI_CMD_SYS_PING, HFI_CMD_SYS_RESET):
        return _hdr(16, pkt_type) + _U64.pack(client_data or 0)
    raise ValueError("unsupported system command 0x%08x" % pkt_type)


def property_items(entries):
    """Pack (id, value0, value1) triples into 12 B property TLVs."""
    out = bytearray()
    for entry in entries:
        pid, v0, v1 = (list(entry) + [0, 0])[:3]
        out += struct.pack("<III", pid, v0, v1)
    return bytes(out)


def encode_set_property(entries):
    """Build HFI_CMD_SYS_SET_PROPERTY.

    Wire layout is nSize@0, ePacketType@4, nNumProperties@8, properties@12, so
    the packet is 12 B + 12 B per property (24 B for one property).  The 16 B
    DWARF struct is the declared body, not a minimum packet length.
    """
    body = property_items(entries)
    n = len(body) // 12
    nsize = 12 + len(body)
    if nsize > MAX_PKT_SIZE:
        raise ValueError("property packet exceeds the receive limit")
    return _hdr(nsize, HFI_CMD_SYS_SET_PROPERTY) + _U32.pack(n) + body


# --------------------------------------------------------------------------
# queue table
# --------------------------------------------------------------------------

def queue_header_bytes(queues, version=QTBL_VERSION, qhdr0_offset=QTBL_QHDR0_OFFSET,
                       qhdr_size=Q_HDR_SIZE, size=None):
    """Serialise the queue table the host hands to the firmware.

    queues: sequence of dicts with at least
              start_addr (u32 byte address of the queue buffer)
              type       (0 cmd, 1 msg, 2 dbg)
              q_size     (capacity in 4-byte words)
            optional: status, pkt_size, rx_wm, tx_wm, read_idx, write_idx.
    Returns bytes of 24 + n*956.  The firmware ignores version and qhdr_size but
    both are written for completeness.
    """
    queues = list(queues)
    if not 1 <= len(queues) <= Q_MAX:
        raise ValueError("1..%d queues, got %d" % (Q_MAX, len(queues)))

    total = qhdr0_offset + len(queues) * Q_HDR_SIZE
    if size is None:
        size = total
    if total > QTBL_MAP_WINDOW:
        raise ValueError("queue table %d B does not fit the 4 KiB mapping window"
                         % total)

    out = bytearray()
    out += struct.pack("<IIIIII", version, size, qhdr0_offset, qhdr_size,
                       len(queues), len(queues))
    out += b"\x00" * (qhdr0_offset - QTBL_HDR_SIZE)

    for q in queues:
        blob = bytearray(Q_HDR_SIZE)
        struct.pack_into("<I", blob, Q_OFF_STATUS, q.get("status", 0))
        struct.pack_into("<I", blob, Q_OFF_START_ADDR, q["start_addr"])
        struct.pack_into("<I", blob, Q_OFF_TYPE, q["type"])
        struct.pack_into("<I", blob, Q_OFF_Q_SIZE, q["q_size"])
        struct.pack_into("<I", blob, Q_OFF_PKT_SIZE, q.get("pkt_size", 0))
        struct.pack_into("<I", blob, Q_OFF_PKT_DROP_CNT, q.get("pkt_drop_cnt", 0))
        struct.pack_into("<I", blob, Q_OFF_RX_WM, q.get("rx_wm", 0))
        struct.pack_into("<I", blob, Q_OFF_TX_WM, q.get("tx_wm", 0))
        struct.pack_into("<I", blob, Q_OFF_RX_REQ, q.get("rx_req", 0))
        struct.pack_into("<I", blob, Q_OFF_TX_REQ, q.get("tx_req", 0))
        struct.pack_into("<I", blob, Q_OFF_RX_IRQ, q.get("rx_irq_status", 0))
        struct.pack_into("<I", blob, Q_OFF_TX_IRQ, q.get("tx_irq_status", 0))
        struct.pack_into("<I", blob, Q_OFF_READ_IDX, q.get("read_idx", 0))
        struct.pack_into("<I", blob, Q_OFF_WRITE_IDX, q.get("write_idx", 0))
        out += blob

    return bytes(out)


def decode_queue_table(blob):
    """Parse a queue table previously produced by queue_header_bytes()."""
    if len(blob) < QTBL_HDR_SIZE:
        raise ValueError("queue table shorter than its 24 B header")
    (version, size, qhdr0_offset, qhdr_size, num_q, num_active) = \
        struct.unpack_from("<IIIIII", blob, 0)
    queues = []
    for i in range(num_q):
        base = qhdr0_offset + i * Q_HDR_SIZE
        if base + Q_HDR_SIZE > len(blob):
            raise ValueError("queue table truncated at queue %d" % i)
        queues.append({
            "status": struct.unpack_from("<I", blob, base + Q_OFF_STATUS)[0],
            "start_addr": struct.unpack_from("<I", blob, base + Q_OFF_START_ADDR)[0],
            "type": struct.unpack_from("<I", blob, base + Q_OFF_TYPE)[0],
            "q_size": struct.unpack_from("<I", blob, base + Q_OFF_Q_SIZE)[0],
            "pkt_size": struct.unpack_from("<I", blob, base + Q_OFF_PKT_SIZE)[0],
            "pkt_drop_cnt": struct.unpack_from("<I", blob, base + Q_OFF_PKT_DROP_CNT)[0],
            "rx_wm": struct.unpack_from("<I", blob, base + Q_OFF_RX_WM)[0],
            "tx_wm": struct.unpack_from("<I", blob, base + Q_OFF_TX_WM)[0],
            "read_idx": struct.unpack_from("<I", blob, base + Q_OFF_READ_IDX)[0],
            "write_idx": struct.unpack_from("<I", blob, base + Q_OFF_WRITE_IDX)[0],
        })
    return {
        "version": version,
        "size": size,
        "q_hdr0_offset": qhdr0_offset,
        "q_hdr_size": qhdr_size,
        "num_queues": num_q,
        "num_active_queues": num_active,
        "queues": queues,
    }


# --------------------------------------------------------------------------
# decoder
# --------------------------------------------------------------------------

def _u32(buf, off):
    return struct.unpack_from("<I", buf, off)[0]


def _u64(buf, off):
    return struct.unpack_from("<Q", buf, off)[0]


def decode(pkt):
    """Decode one packet from either direction.

    Returns a dict with at least:
      nSize, actual_size, size_ok, ePacketType, domain, kind, name
    and type-specific fields.  Never raises on unknown packet types: it returns
    kind="unknown" so a driver can log and drop, like the firmware does.
    """
    pkt = bytes(pkt)
    if len(pkt) < 8:
        raise ValueError("packet shorter than the 8 B header")
    nsize = _u32(pkt, 0)
    ptype = _u32(pkt, 4)
    out = {
        "nSize": nsize,
        "actual_size": len(pkt),
        "size_ok": nsize == len(pkt) and not (nsize & 3) and 0 < nsize <= MAX_PKT_SIZE,
        "ePacketType": ptype,
        "domain": (ptype >> 24) & 7,
    }

    if ptype in (HFI_CMD_IPEBPS_ASYNC_COMMAND_DIRECT,
                 HFI_CMD_IPEBPS_ASYNC_COMMAND_INDIRECT):
        direct = ptype == HFI_CMD_IPEBPS_ASYNC_COMMAND_DIRECT
        opcode = _u32(pkt, 8)
        nhandles = _u32(pkt, 28)
        if nhandles > 16:
            raise ValueError("nNumHandles %d is out of range" % nhandles)
        off = async_payload_off(nhandles)
        if len(pkt) < off:
            raise ValueError("async packet shorter than its payload offset")
        handles = [_u32(pkt, 32 + 4 * i) for i in range(nhandles)]
        payload_off = off
        if direct:
            payload = pkt[payload_off:nsize] if nsize <= len(pkt) else pkt[payload_off:]
            indirect_addr = None
        else:
            if len(pkt) < payload_off + 4:
                raise ValueError("INDIRECT packet has no room for its address")
            payload = None
            indirect_addr = _u32(pkt, payload_off)
        expected = OPCODE_PAYLOAD_SIZE.get(opcode)
        out.update({
            "kind": "async",
            "name": OPCODE_NAMES.get(opcode, "opcode_0x%02x" % opcode),
            "direct": direct,
            "eCmdOpcode": opcode,
            "nUserArg1": _u64(pkt, 12),
            "nUserArg2": _u64(pkt, 20),
            "nNumHandles": nhandles,
            "hHandles": handles,
            "payload_offset": payload_off,
            "payload": payload,
            "indirect_addr": indirect_addr,
            "expected_payload_size": expected,
            "payload_size_ok": (expected is None or payload is None
                                or len(payload) == expected),
        })
        if direct and expected is not None:
            ok, reason = check_command_size(nsize, expected, nhandles, True)
        else:
            ok, reason = check_command_size(nsize, expected or 0, nhandles, False)
        out["firmware_check"] = ok
        out["firmware_check_reason"] = reason
        return out

    if ptype == HFI_CMD_IPEBPS_CREATE_HANDLE:
        out.update({
            "kind": "create_handle",
            "name": "CREATE_HANDLE",
            "eHandleType": _u32(pkt, 8),
            "nUserArg1": _u64(pkt, 12),
            "nUserArg2": _u64(pkt, 20),
        })
        return out

    if ptype == HFI_MSG_IPEBPS_CREATE_HANDLE_ACK:
        out.update({
            "kind": "create_handle_ack",
            "name": "CREATE_HANDLE_ACK",
            "eErrorType": _u32(pkt, 8),
            "hHandle": _u32(pkt, 12),
            "nUserArg1": _u64(pkt, 16),
            "nUserArg2": _u64(pkt, 24),
        })
        return out

    if ptype in (HFI_MSG_IPEBPS_ASYNC_DIRECT_ACK,
                 HFI_MSG_IPEBPS_ASYNC_INDIRECT_ACK):
        opcode = _u32(pkt, 8)
        out.update({
            "kind": "async_ack",
            "name": "ASYNC_ACK(%s)" % OPCODE_NAMES.get(opcode, "0x%02x" % opcode),
            "eCmdOpcode": opcode,
            "nUserArg1": _u64(pkt, 12),
            "nUserArg2": _u64(pkt, 20),
            "eErrorType": _u32(pkt, 28),
            "cmdPayload": pkt[32:nsize] if nsize <= len(pkt) else pkt[32:],
        })
        return out

    if ptype == HFI_MSG_EVENT_NOTIFY:
        out.update({
            "kind": "event",
            "name": "EVENT_NOTIFY",
            "nSessionID": _u32(pkt, 8),
            "nEventID": _u32(pkt, 12),
            "eventData1": _u32(pkt, 16),
            "eventData2": _u32(pkt, 20),
            "rgExtEventData": pkt[24:nsize],
        })
        return out

    if ptype == HFI_MSG_SYS_INIT_DONE:
        out.update({
            "kind": "init_done",
            "name": "SYS_INIT_DONE",
            "eErrorType": _u32(pkt, 8),
            "nNumProperties": _u32(pkt, 12),
            "properties": pkt[16:nsize],
        })
        return out

    if ptype == HFI_MSG_SYS_PC_PREP_DONE:
        out.update({"kind": "pc_prep_done", "name": "SYS_PC_PREP_DONE",
                    "eErrorType": _u32(pkt, 8)})
        return out

    if ptype in (HFI_CMD_SYS_PING, HFI_CMD_SYS_RESET,
                 HFI_MSG_SYS_PING_ACK, HFI_MSG_SYS_RESET_ACK):
        out.update({
            "kind": "system_echo",
            "name": {HFI_CMD_SYS_PING: "SYS_PING",
                     HFI_CMD_SYS_RESET: "SYS_RESET",
                     HFI_MSG_SYS_PING_ACK: "SYS_PING_ACK",
                     HFI_MSG_SYS_RESET_ACK: "SYS_RESET_ACK"}[ptype],
            "nClientData": _u64(pkt, 8) if len(pkt) >= 16 else None,
        })
        return out

    if ptype == HFI_CMD_SYS_INIT:
        out.update({"kind": "system", "name": "SYS_INIT"})
        return out

    if ptype == HFI_CMD_SYS_SET_PROPERTY:
        n = _u32(pkt, 8)
        props = []
        for i in range(n):
            base = 12 + 12 * i
            if base + 12 > len(pkt):
                break
            props.append({"id": _u32(pkt, base),
                          "value0": _u32(pkt, base + 4),
                          "value1": _u32(pkt, base + 8)})
        out.update({"kind": "set_property", "name": "SYS_SET_PROPERTY",
                    "nNumProperties": n, "properties": props})
        return out

    if ptype == HFI_MSG_SYS_DEBUG:
        out.update({
            "kind": "debug",
            "name": "SYS_DEBUG",
            "eMsgType": _u32(pkt, 8),
            "nMsgSize": _u32(pkt, 12),
            "nTimeStampHi": _u32(pkt, 16),
            "nTimeStampLo": _u32(pkt, 20),
            "rgMsgData": pkt[24:nsize],
        })
        return out

    out.update({"kind": "unknown", "name": "packet_type_0x%08x" % ptype})
    return out


def firmware_accepts(pkt):
    """Would the receive loop + CheckCommandSize accept this packet?

    Returns (ok, reason).  Only the size rules are simulated; the firmware also
    validates the opcode range and the per-opcode handle count.
    """
    try:
        info = decode(pkt)
    except ValueError as exc:
        return False, str(exc)
    if not info["size_ok"]:
        return False, "nSize %d does not match %d bytes / limits" % (
            info["nSize"], info["actual_size"])
    if info["kind"] == "async":
        ok = info.get("firmware_check", True)
        return bool(ok), info.get("firmware_check_reason", "")
    return True, ""
