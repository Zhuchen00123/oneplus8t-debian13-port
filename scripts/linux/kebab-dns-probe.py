#!/usr/bin/env python3
"""One DNS query through a specific bearer interface.

Why this exists: ICMP is the cheapest probe and the first thing an operator
may filter.  A DNS answer proves the bearer carries real IP traffic in both
directions, and it uses the resolver the network handed us in WDS
"Get Current Settings" (TLV 0x15/0x16) instead of touching /etc/resolv.conf.

  kebab-dns-probe.py --iface mhi_hwip0 --server <bearer dns> --name example.com

The socket is bound with SO_BINDTODEVICE (needs root), so the query cannot
leave through Wi-Fi.  Exit code: 0 answer received, 1 no answer/timeout,
2 usage or bind error, 3 --selftest failure.
"""
import argparse
import json
import random
import socket
import struct
import sys

SO_BINDTODEVICE = 25
QTYPE_A = 1
QTYPE_AAAA = 28


def build_query(name, qid=None, qtype=QTYPE_A):
    """Return (qid, wire).  Pure, so the packet layout is testable offline."""
    if qid is None:
        qid = random.randint(0, 0xFFFF)
    header = struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    qname = b""
    for label in name.rstrip(".").split("."):
        if not label:
            raise ValueError("empty label in " + name)
        if len(label) > 63:
            raise ValueError("label too long: " + label)
        qname += bytes([len(label)]) + label.encode("ascii")
    qname += b"\x00"
    return qid, header + qname + struct.pack("!HH", qtype, 1)


def skip_name(data, off):
    """Walk a (possibly compressed) DNS name, return the offset after it."""
    while True:
        if off >= len(data):
            raise ValueError("name runs past the packet")
        n = data[off]
        if n == 0:
            return off + 1
        if n & 0xC0 == 0xC0:          # compression pointer: 2 bytes, done
            if off + 2 > len(data):
                raise ValueError("truncated compression pointer")
            return off + 2
        if n & 0xC0:
            raise ValueError("reserved label type 0x%02x" % n)
        off += 1 + n


def parse_response(data, qid=None):
    """Return {id, rcode, answers, ips} or raise ValueError."""
    if len(data) < 12:
        raise ValueError("shorter than a DNS header")
    rid, flags, qdc, anc, nsc, arc = struct.unpack("!HHHHHH", data[:12])
    if qid is not None and rid != qid:
        raise ValueError("transaction id mismatch")
    rcode = flags & 0x000F
    off = 12
    for _ in range(qdc):
        off = skip_name(data, off)
        off += 4
    ips = []
    for _ in range(anc):
        off = skip_name(data, off)
        if off + 10 > len(data):
            raise ValueError("truncated answer record")
        rtype, rclass, ttl, rdlen = struct.unpack("!HHIH", data[off:off + 10])
        off += 10
        rdata = data[off:off + rdlen]
        off += rdlen
        if rtype == QTYPE_A and rdlen == 4:
            ips.append(socket.inet_ntoa(rdata))
        elif rtype == QTYPE_AAAA and rdlen == 16:
            ips.append(socket.inet_ntop(socket.AF_INET6, rdata))
    return {"id": rid, "rcode": rcode, "answers": anc, "ips": ips}


def selftest():
    """Pure checks: packet layout plus a hand-built response."""
    bad = 0
    qid, wire = build_query("example.com", qid=0x1234)
    if qid != 0x1234 or wire[:2] != b"\x12\x34":
        print("FAIL query header"); bad += 1
    if wire[2:4] != b"\x01\x00" or wire[4:6] != b"\x00\x01":
        print("FAIL query flags/qdcount"); bad += 1
    if wire[12:12 + 13] != b"\x07example\x03com\x00":
        print("FAIL qname encoding: %r" % wire[12:25]); bad += 1
    if wire[-4:] != struct.pack("!HH", QTYPE_A, 1):
        print("FAIL qtype/qclass"); bad += 1
    # hand-built answer: one A record 93.184.216.34 for example.com
    resp = struct.pack("!HHHHHH", 0x1234, 0x8180, 1, 1, 0, 0)
    resp += b"\x07example\x03com\x00" + struct.pack("!HH", 1, 1)
    resp += b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 300, 4) + bytes([93, 184, 216, 34])
    got = parse_response(resp, 0x1234)
    if got["rcode"] != 0 or got["answers"] != 1 or got["ips"] != ["93.184.216.34"]:
        print("FAIL parse happy path: %r" % (got,)); bad += 1
    # truncated / garbage must raise, not be mistaken for an answer
    for blob in (b"", b"\x00" * 11, resp[:20]):
        try:
            parse_response(blob, 0x1234)
            print("FAIL truncated packet accepted: %r" % blob); bad += 1
        except ValueError:
            pass
    # NXDOMAIN must be visible as rcode 3, not as "no answer" silence
    nx = struct.pack("!HHHHHH", 0x1234, 0x8183, 1, 0, 0, 0) + b"\x07example\x03com\x00" + struct.pack("!HH", 1, 1)
    if parse_response(nx, 0x1234)["rcode"] != 3:
        print("FAIL NXDOMAIN rcode"); bad += 1
    if bad:
        print("DNS SELFTEST FAIL (%d)" % bad)
        return 3
    print("DNS SELFTEST PASS")
    return 0


def main():
    ap = argparse.ArgumentParser(description="one DNS query through a bearer interface")
    ap.add_argument("--iface", default="")
    ap.add_argument("--server", default="223.5.5.5", help="resolver IP (use the bearer DNS when known)")
    ap.add_argument("--name", default="example.com")
    ap.add_argument("--timeout", type=float, default=4.0)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    family = socket.AF_INET6 if ":" in args.server else socket.AF_INET
    qtype = QTYPE_AAAA if family == socket.AF_INET6 else QTYPE_A
    qid, wire = build_query(args.name, qtype=qtype)
    sock = socket.socket(family, socket.SOCK_DGRAM)
    try:
        if args.iface:
            sock.setsockopt(socket.SOL_SOCKET, SO_BINDTODEVICE, args.iface.encode() + b"\x00")
        sock.settimeout(args.timeout)
        sock.connect((args.server, 53))
        sock.send(wire)
        data = sock.recv(2048)
    except OSError as exc:
        out = {"iface": args.iface, "server": args.server, "name": args.name,
               "error": str(exc), "ok": False}
        print(json.dumps(out) if args.json else "DNS FAIL " + str(exc))
        return 1
    finally:
        sock.close()

    out = {"iface": args.iface, "server": args.server, "name": args.name}
    try:
        parsed = parse_response(data, qid)
    except ValueError as exc:
        out.update({"error": "malformed reply: " + str(exc), "ok": False})
        print(json.dumps(out) if args.json else "DNS FAIL " + out["error"])
        return 1
    out.update(parsed)
    out["ok"] = parsed["rcode"] == 0 and bool(parsed["ips"])
    if args.json:
        print(json.dumps(out))
    elif out["ok"]:
        print("DNS OK %s via %s -> %s" % (args.name, args.server, ",".join(parsed["ips"])))
    else:
        print("DNS FAIL %s via %s rcode=%d answers=%d" % (args.name, args.server, parsed["rcode"], parsed["answers"]))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
