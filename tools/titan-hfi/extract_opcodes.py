#!/usr/bin/env python3
"""Re-derive the Titan HFI opcode -> payload-size table from the firmware.

B0 established the table by hand.  This script makes it reproducible: it reads
the Thumb disassembly of ProcessAsyncCommand, decodes its TBH jump table, walks
each branch to the CheckCommandSize() call and reads the size immediate.  It
then compares the result against TITAN_HFI_OPCODE_TABLE in titan_hfi.h, so a
future edit that "remembers" a number wrongly fails loudly.

Inputs
    --disasm   llvm-objdump text, produced with
                 llvm-objdump -d --triple=thumbv7a-none-eabi \
                     --no-show-raw-insn CAMERA_ICP.elf > disasm-thumb.txt
    --elf      the same ELF (used to read the jump table bytes and to verify
               the firmware SHA-256)
    --header   titan_hfi.h (defaults to the copy next to this script)

Usage
    python3 extract_opcodes.py --disasm /tmp/disasm-thumb.txt \
        --elf local/titan-b0/CAMERA_ICP.elf

Exit status is 0 only when every opcode matches the header.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import sys

EXPECTED_SHA256 = "cb98cec20d1073d4b2872342b3db2185b57422c73e9e7f327887f1b039a1ceb9"
TITAN_VERSION_STRING = "CICP.FW.1.0.82-4-ICA_REG"

FUNC_NAME = "ProcessAsyncCommand"
TBH_COUNT = 15                    # opcodes 1..0xF

RE_SYMBOL = re.compile(r"^([0-9a-f]{8}) <([^>]+)>:\s*$")
RE_INSN = re.compile(r"^\s+([0-9a-f]+):\s+(.*)$")
RE_BL = re.compile(r"^bl\s+(?:0x([0-9a-f]+)\s+)?<([^>+]+)(?:\+\S+)?>")
RE_ADDR_ONLY = re.compile(r"^0x([0-9a-f]+)$")


def _mov_re(reg):
    return re.compile(r"^movs?\.?w?\s+%s,\s+#(0x[0-9a-fA-F]+|\d+)\s*$" % reg)


RE_MOV_R1 = _mov_re("r1")
RE_MOVW_R1 = re.compile(r"^movw\s+r1,\s+#(0x[0-9a-fA-F]+)\s*$")
RE_MOVT_R1 = re.compile(r"^movt\s+r1,\s+#(0x[0-9a-fA-F]+)\s*$")
RE_MOV_R0 = _mov_re("r0")
RE_MOVW_R0 = re.compile(r"^movw\s+r0,\s+#(0x[0-9a-fA-F]+)\s*$")
RE_MOVT_R0 = re.compile(r"^movt\s+r0,\s+#(0x[0-9a-fA-F]+)\s*$")
RE_LDR_NUMHANDLES = re.compile(
    r"^ldr(?:\.w)?\s+(r\d+),\s+\[r\d+,\s+#0x1c\]\s*$")
RE_CMP_REG_1 = re.compile(r"^cmp\s+(r\d+),\s+#0x1\s*$")
RE_STRD_CACHE = re.compile(r"^strd\s+r10,\s+(r\d+),\s+\[sp,\s+#72\]\s*$")


class Elf32:
    """Minimal ELF32 little-endian reader: enough to map vaddr -> bytes."""

    def __init__(self, path):
        with open(path, "rb") as fh:
            self.blob = fh.read()
        if self.blob[:4] != b"\x7fELF":
            raise ValueError("%s is not an ELF file" % path)
        if self.blob[4] != 1:
            raise ValueError("not ELF32")
        if self.blob[5] != 1:
            raise ValueError("not little-endian")
        (self.e_machine,) = struct.unpack_from("<H", self.blob, 0x12)
        (self.e_phoff,) = struct.unpack_from("<I", self.blob, 0x1C)
        (self.e_phentsize,) = struct.unpack_from("<H", self.blob, 0x2A)
        (self.e_phnum,) = struct.unpack_from("<H", self.blob, 0x2C)
        self.segments = []
        for i in range(self.e_phnum):
            base = self.e_phoff + i * self.e_phentsize
            p_type, p_offset, p_vaddr, _p_paddr, p_filesz = \
                struct.unpack_from("<IIIII", self.blob, base)
            if p_type == 1 and p_filesz:        # PT_LOAD
                self.segments.append((p_vaddr, p_vaddr + p_filesz, p_offset))

    @property
    def sha256(self):
        return hashlib.sha256(self.blob).hexdigest()

    def read_vaddr(self, vaddr, size):
        for start, end, offset in self.segments:
            if start <= vaddr and vaddr + size <= end:
                rel = offset + (vaddr - start)
                return self.blob[rel:rel + size]
        raise ValueError("vaddr 0x%x (+%d) is not inside a PT_LOAD segment"
                         % (vaddr, size))


class Disassembly:
    """Address-indexed view of llvm-objdump --no-show-raw-insn output."""

    def __init__(self, path):
        self.symbols = {}       # name -> address
        self.symbol_addrs = []  # sorted (address, name)
        self.insns = []         # sorted (address, text)
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = RE_SYMBOL.match(line.rstrip("\n"))
                if m:
                    addr = int(m.group(1), 16)
                    self.symbols[m.group(2)] = addr
                    self.symbol_addrs.append((addr, m.group(2)))
                    continue
                m = RE_INSN.match(line.rstrip("\n"))
                if m:
                    # llvm-objdump separates mnemonic and operands with tabs and
                    # appends "@ imm = #..." annotations; data directives also
                    # carry a raw byte dump, so collapse whitespace and skip them.
                    text = " ".join(m.group(2).split("@ imm")[0].split())
                    if not text or text.startswith("."):
                        continue
                    if any(d in text for d in (".word", ".short", ".byte", ".long")):
                        continue
                    self.insns.append((int(m.group(1), 16), text))
        self.insns.sort()
        self.symbol_addrs.sort()
        self._by_addr = dict(self.insns)

    def next_symbol_after(self, addr):
        for sym_addr, name in self.symbol_addrs:
            if sym_addr > addr:
                return sym_addr, name
        return None, None

    def func_range(self, name):
        if name not in self.symbols:
            raise KeyError("symbol %s not found in the disassembly" % name)
        start = self.symbols[name]
        end, _ = self.next_symbol_after(start)
        return start, end


def scan_branch(disasm, start, end, numhandles_reg=None):
    """Walk one jump-table target and recover its CheckCommandSize immediate.

    Returns a dict with the payload size, the cache-maintenance size (an
    independent second immediate in the same branch) and whether the branch
    demands exactly one handle.
    """
    r1 = None
    r1_hi = None
    cache_regs = {}
    handle_cmp = False
    seen_bl = False
    seen = 0
    result = {"target": start, "payload_bytes": None, "cache_bytes": None,
              "requires_one_handle": False, "check_call": None}

    for addr, text in disasm.insns:
        if addr < start:
            continue
        if end is not None and addr >= end:
            break
        seen += 1

        m = RE_MOV_R1.match(text)
        if m:
            r1 = int(m.group(1), 0)
            r1_hi = None
            continue
        m = RE_MOVW_R1.match(text)
        if m:
            r1 = int(m.group(1), 0)
            r1_hi = None
            continue
        m = RE_MOVT_R1.match(text)
        if m:
            if r1 is None:
                r1 = 0
            r1_hi = int(m.group(1), 0)
            continue

        m = RE_MOV_R0.match(text)
        if m:
            cache_regs["r0"] = int(m.group(1), 0)
            continue
        m = RE_MOVW_R0.match(text)
        if m:
            cache_regs["r0"] = int(m.group(1), 0)
            continue
        m = RE_MOVT_R0.match(text)
        if m:
            cache_regs["r0"] = (cache_regs.get("r0", 0) & 0xFFFF) | (int(m.group(1), 0) << 16)
            continue

        m = RE_STRD_CACHE.match(text)
        if m:
            result["cache_bytes"] = cache_regs.get(m.group(1))
            continue

        m = RE_LDR_NUMHANDLES.match(text)
        if m:
            numhandles_reg = numhandles_reg or m.group(1)
            continue

        m = RE_CMP_REG_1.match(text)
        if (m and numhandles_reg and m.group(1) == numhandles_reg
                and not seen_bl and seen <= 2):
            # "cmp <nNumHandles_reg>, #0x1 ; bne <shared error>" opens the branch
            handle_cmp = True
            continue

        m = RE_BL.match(text)
        if m:
            seen_bl = True
        if m and m.group(2) == "CheckCommandSize":
            result["check_call"] = addr
            if r1_hi is not None:
                r1 = (r1 or 0) | (r1_hi << 16)
            result["payload_bytes"] = r1
            break
        if m and m.group(2) in (FUNC_NAME,) and addr != start:
            # a tail call back to the dispatcher ends the branch
            break

    result["requires_one_handle"] = handle_cmp
    return result


def parse_header_table(path):
    """Extract TITAN_HFI_OPCODE_TABLE entries from the C header."""
    pattern = re.compile(r"X\((0x[0-9A-Fa-f]+),\s*([A-Za-z0-9_]+),\s*(\d+)\)")
    table = {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            m = pattern.search(line)
            if m:
                table[int(m.group(1), 16)] = (m.group(2), int(m.group(3)))
    if not table:
        raise ValueError("no TITAN_HFI_OPCODE_TABLE entries in %s" % path)
    return table


def parse_header_constants(path):
    """Pull the geometry constants out of the header for the report."""
    wanted = ["MAX_PKT_SIZE", "QTBL_HDR_SIZE", "Q_HDR_SIZE", "ASYNC_MIN_SIZE",
              "ASYNC_FIXED_HDR", "QTBL_MAP_WINDOW"]
    found = {}
    pattern = re.compile(r"^#define\s+TITAN_HFI_(%s)\s+(\d+)u?"
                         % "|".join(wanted))
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            m = pattern.match(line)
            if m:
                found[m.group(1)] = int(m.group(2))
    return found


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--disasm", required=True, help="llvm-objdump thumb disassembly")
    ap.add_argument("--elf", required=True, help="the firmware ELF the disassembly came from")
    ap.add_argument("--header", default=os.path.join(here, "titan_hfi.h"))
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    ap.add_argument("--lenient", action="store_true",
                    help="report mismatches but still exit 0 (for investigation)")
    args = ap.parse_args(argv)

    elf = Elf32(args.elf)
    disasm = Disassembly(args.disasm)
    header = parse_header_table(args.header)
    constants = parse_header_constants(args.header)

    problems = []
    if elf.sha256 != EXPECTED_SHA256:
        problems.append("firmware SHA-256 %s != expected %s"
                        % (elf.sha256, EXPECTED_SHA256))

    start, end = disasm.func_range(FUNC_NAME)

    tbh_addr = None
    for addr, text in disasm.insns:
        if addr < start or (end is not None and addr >= end):
            continue
        if text.split()[0] == "tbh":
            tbh_addr = addr
            break
    if tbh_addr is None:
        print("no tbh instruction inside %s" % FUNC_NAME, file=sys.stderr)
        return 2

    table_addr = tbh_addr + 4
    raw = elf.read_vaddr(table_addr, TBH_COUNT * 2)
    offsets = list(struct.unpack("<%dH" % TBH_COUNT, raw))

    # entry prologue: which register receives pkt->nNumHandles (+0x1c)
    numhandles_reg = None
    for addr, text in disasm.insns:
        if addr < start or (end is not None and addr >= end):
            continue
        m = RE_LDR_NUMHANDLES.match(text)
        if m:
            numhandles_reg = m.group(1)
            break

    rows = []
    for index, off in enumerate(offsets):
        opcode = index + 1
        target = table_addr + 2 * off
        info = scan_branch(disasm, target, end, numhandles_reg)
        hdr = header.get(opcode)
        rows.append({
            "opcode": opcode,
            "target": target,
            "tbh_offset": off,
            "payload_bytes": info["payload_bytes"],
            "cache_bytes": info["cache_bytes"],
            "requires_one_handle": info["requires_one_handle"],
            "header_name": hdr[0] if hdr else None,
            "header_bytes": hdr[1] if hdr else None,
            "match": (hdr is not None and info["payload_bytes"] == hdr[1]),
        })

    for row in rows:
        if row["header_bytes"] is None:
            problems.append("opcode 0x%02x missing from the header table" % row["opcode"])
        elif not row["match"]:
            problems.append("opcode 0x%02x: firmware %s B, header %s B"
                            % (row["opcode"], row["payload_bytes"], row["header_bytes"]))
        if (row["payload_bytes"] is not None and row["cache_bytes"] is not None
                and row["payload_bytes"] != row["cache_bytes"]):
            problems.append("opcode 0x%02x: CheckCommandSize %d B but the cache "
                            "maintenance call passes %d B"
                            % (row["opcode"], row["payload_bytes"], row["cache_bytes"]))

    if args.json:
        print(json.dumps({
            "firmware_sha256": elf.sha256,
            "function": {"name": FUNC_NAME, "start": start, "end": end},
            "tbh_addr": tbh_addr,
            "jump_table_addr": table_addr,
            "numhandles_offset": 28,
            "numhandles_reg": numhandles_reg,
            "header_constants": constants,
            "rows": rows,
            "problems": problems,
        }, indent=2))
    else:
        print("firmware  %s" % args.elf)
        print("sha256    %s%s" % (elf.sha256,
                                  "" if elf.sha256 == EXPECTED_SHA256
                                  else "  <-- UNEXPECTED"))
        print("function  %s @ 0x%x .. %s" % (FUNC_NAME, start,
                                             "0x%x" % end if end else "end"))
        print("tbh       @ 0x%x, jump table @ 0x%x (%d entries, halfwords relative to 0x%x)"
              % (tbh_addr, table_addr, TBH_COUNT, table_addr))
        print("nNumHandles read from pkt+0x1c into %s" % numhandles_reg)
        if constants:
            print("header    %s" % ", ".join("%s=%d" % (k, v)
                                             for k, v in sorted(constants.items())))
        print()
        print("opcode  target     payload  cache  handles  header name                  bytes  verdict")
        for row in rows:
            print("0x%02x    0x%06x  %7s  %5s  %7s  %-26s  %5s  %s" % (
                row["opcode"], row["target"],
                "-" if row["payload_bytes"] is None else row["payload_bytes"],
                "-" if row["cache_bytes"] is None else row["cache_bytes"],
                "== 1" if row["requires_one_handle"] else "-",
                row["header_name"] or "(absent)",
                "-" if row["header_bytes"] is None else row["header_bytes"],
                "match" if row["match"] else "MISMATCH"))
        print()
        if problems:
            print("%d problem(s):" % len(problems))
            for p in problems:
                print("  - %s" % p)
        else:
            print("all %d opcodes match titan_hfi.h" % len(rows))

    if problems and not args.lenient:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
