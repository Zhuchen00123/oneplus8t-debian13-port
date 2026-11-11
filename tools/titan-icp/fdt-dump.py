#!/usr/bin/env python3
"""Flattened-device-tree reader used for offline camera/ICP forensics.

No dtc and no libfdt: the stock DTBs on this project are only ever read, and
the tree is small enough to walk by hand.  Written for B2 (Titan ICP) because
the vendor DTB is the only authoritative source for the AP-side register
windows, the clock/regulator phandles and the ICP interrupt.

Examples
    # list every node whose path or compatible matches a substring
    ./fdt-dump.py stock-000.dtb --find cam-a5 --find cam-icp --find cam-cpas

    # dump every property of an exact node, as cells
    ./fdt-dump.py stock-000.dtb --path /soc/qcom,a5@ac00000

    # resolve a phandle (e.g. a supply or clock parent)
    ./fdt-dump.py stock-000.dtb --phandle 0x250
"""

from __future__ import annotations

import argparse
import struct
import sys

FDT_MAGIC = 0xD00DFEED
FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9

STRING_PROPS = {
    "compatible", "reg-names", "clock-names", "interrupt-names",
    "regulator-name", "status", "label", "src-clock-name", "fw_name",
    "clock-cntl-level", "regulator-names", "clock-output-names",
    "compat-hw-name", "arch-compat", "iop-firmware", "firmware-name",
}


class Fdt:
    def __init__(self, path):
        with open(path, "rb") as fh:
            self.blob = fh.read()
        (magic, totalsize, off_struct, off_strings, off_rsvmap, version,
         last_comp, boot_cpuid, size_strings, size_struct) = \
            struct.unpack_from(">10I", self.blob, 0)
        if magic != FDT_MAGIC:
            raise ValueError("not a flattened device tree: magic=0x%08x" % magic)
        self.version = version
        self.size = totalsize
        self.struct_off = off_struct
        self.size_struct = size_struct
        self.strings_off = off_strings

    def _string_at(self, off):
        end = self.blob.index(b"\x00", self.strings_off + off)
        return self.blob[self.strings_off + off:end].decode("ascii", "replace")

    def walk(self):
        off = self.struct_off
        limit = self.struct_off + self.size_struct
        depth = 0
        while off < limit:
            (tok,) = struct.unpack_from(">I", self.blob, off)
            off += 4
            if tok == FDT_BEGIN_NODE:
                end = self.blob.index(b"\x00", off)
                name = self.blob[off:end].decode("ascii", "replace")
                off = (end + 1 + 3) & ~3        # node names are NUL padded to 4 B
                depth += 1
                yield ("begin", depth, name, None)
            elif tok == FDT_END_NODE:
                yield ("end", depth, None, None)
                depth -= 1
            elif tok == FDT_PROP:
                (plen, nameoff) = struct.unpack_from(">II", self.blob, off)
                off += 8
                val = self.blob[off:off + plen]
                off += (plen + 3) & ~3
                yield ("prop", depth, self._string_at(nameoff), val)
            elif tok == FDT_NOP:
                continue
            elif tok in (FDT_END, 0):
                break
            else:
                raise ValueError("bad FDT token 0x%x" % tok)

    def nodes(self):
        stack, out, cur = [], [], None
        for kind, depth, name, val in self.walk():
            if kind == "begin":
                stack = stack[:depth - 1] + [name]
                cur = {"path": "/" + "/".join(stack[1:]), "props": {}}
                out.append(cur)
            elif kind == "end":
                cur = None
            elif cur is not None:
                cur["props"][name] = val
        return out


def cells(val):
    n = len(val) // 4
    return list(struct.unpack(">%dI" % n, val[:n * 4]))


def render(name, val, as_strings):
    if not val:
        return "(empty)"
    if as_strings or name in STRING_PROPS:
        return '"%s"' % val.replace(b"\x00", b"|").decode("ascii", "replace").strip("|")
    out = " ".join("0x%x" % c for c in cells(val))
    if len(val) % 4:
        out += " +raw:" + val[len(val) - len(val) % 4:].hex()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dtb")
    ap.add_argument("--find", action="append", default=[],
                    help="substring to match against node path or compatible")
    ap.add_argument("--path", action="append", default=[],
                    help="exact node path to dump")
    ap.add_argument("--phandle", action="append", default=[],
                    help="resolve a phandle (hex) to a node")
    ap.add_argument("--raw", action="store_true",
                    help="print every property as cells, never as a string")
    ap.add_argument("--count", action="store_true", help="only print node counts")
    args = ap.parse_args(argv)

    fdt = Fdt(args.dtb)
    nodes = fdt.nodes()

    if args.count:
        print("%s: version=%d nodes=%d" % (args.dtb, fdt.version, len(nodes)))
        return 0

    by_phandle = {}
    for node in nodes:
        ph = node["props"].get("phandle")
        if ph and len(ph) == 4:
            by_phandle[cells(ph)[0]] = node

    shown = 0
    if args.find:
        pats = [p.lower() for p in args.find]
        for node in nodes:
            hay = node["path"].lower()
            comp = node["props"].get("compatible", b"").lower()
            if not any(p in hay or p.encode() in comp for p in pats):
                continue
            print(node["path"])
            for k in sorted(node["props"]):
                print("    %-22s %s" % (k, render(k, node["props"][k], args.raw)))
            print()
            shown += 1

    for want in args.path:
        for node in nodes:
            if node["path"] != want:
                continue
            print(node["path"])
            for k in sorted(node["props"]):
                print("    %-22s %s" % (k, render(k, node["props"][k], args.raw)))
            print()
            shown += 1

    for spec in args.phandle:
        want = int(spec, 0)
        node = by_phandle.get(want)
        print("phandle 0x%x -> %s" % (want, node["path"] if node else "NOT FOUND"))
        if node:
            for k in ("compatible", "regulator-name", "reg"):
                if k in node["props"]:
                    print("    %-18s %s" % (k, render(k, node["props"][k], False)))

    if not shown and not args.phandle:
        print("no match", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

