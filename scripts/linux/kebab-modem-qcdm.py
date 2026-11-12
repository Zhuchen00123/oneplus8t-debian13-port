#!/usr/bin/env python3
"""QCDM (DM protocol) probe for the kebab SDX55 diag channel.

The modem's own diag stream would be the place to read real cause codes on a
live network, so this tool exists to find out whether that channel is usable
here at all.  It is deliberately small and it reports what the channel does
instead of pretending to understand it.

Finding on this board (/dev/wwan0qcdm0, DIAG channels bound by mhi_wwan_ctrl):
writing any frame makes the channel return a byte-exact copy of it, wrapped as
0x13 <escaped frame> <2 bytes> 0x7e, twice.  A real DIAG answer to VERNO would
be command 0x00 plus an ASCII version string, so this is a transport echo and
not a diag response.  'capture' records raw bytes for later decoding; 'verno'
demonstrates the echo.

Framing this tool *sends* (DM protocol): 0x7e | escaped payload | escaped CRC16
(0x8408 reflected, init 0, low byte first) | 0x7e.
"""
import argparse
import os
import select
import sys
import time

DEV = "/dev/wwan0qcdm0"
FLAG = 0x7E
ESC = 0x7D
ESC_XOR = 0x20

DIAG_VERNO_F = 0x00


def crc16(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def escape(data):
    out = bytearray()
    for b in data:
        if b in (FLAG, ESC):
            out += bytes([ESC, b ^ ESC_XOR])
        else:
            out.append(b)
    return bytes(out)


def build(payload):
    crc = crc16(payload)
    return bytes([FLAG]) + escape(payload) + escape(bytes([crc & 0xFF, crc >> 8])) + bytes([FLAG])


def unescape(data):
    out = bytearray()
    i = 0
    while i < len(data):
        if data[i] == ESC and i + 1 < len(data):
            out.append(data[i + 1] ^ ESC_XOR)
            i += 2
        else:
            out.append(data[i])
            i += 1
    return bytes(out)


def read_raw(fd, deadline):
    """Everything the device sends, no filtering: on this channel the raw bytes
    are the evidence."""
    buf = b""
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.5)
        if not r:
            continue
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        buf += chunk
    return buf


def main():
    ap = argparse.ArgumentParser(description="QCDM probe for the SDX55 diag channel")
    ap.add_argument("command", choices=["verno", "capture"])
    ap.add_argument("--dev", default=DEV)
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--out", default="")
    ap.add_argument("--timeout", type=float, default=3.0)
    args = ap.parse_args()

    try:
        fd = os.open(args.dev, os.O_RDWR | os.O_NOCTTY)
    except OSError as exc:
        sys.exit(f"cannot open {args.dev}: {exc} (need root?)")

    try:
        if args.command == "verno":
            req = build(bytes([DIAG_VERNO_F]))
            os.write(fd, req)
            raw = read_raw(fd, time.time() + args.timeout)
            print(f"request : {req.hex()}")
            print(f"response: {raw.hex() if raw else '<nothing>'}")
            seen = unescape(raw)
            if req in seen:
                print("verdict : echo -- the channel returned the request itself; no DIAG")
                print("          handler answered (see docs/sdx55-modemmanager-20261112.md 4.9)")
                return 2
            if not raw:
                print("verdict : silent -- nothing came back")
                return 3
            print("verdict : non-echo data; run 'capture' and decode it")
            return 0

        print(f"capturing raw diag bytes for {args.seconds}s from {args.dev}")
        raw = read_raw(fd, time.time() + args.seconds)
        chunks = [raw[i:i + 32].hex() for i in range(0, len(raw), 32)]
        print(f"bytes  : {len(raw)} in {len(chunks)} lines")
        for line in chunks[:20]:
            print("  " + line)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write("\n".join(chunks) + ("\n" if chunks else ""))
            print(f"wrote  : {args.out}")
        return 0 if raw else 4
    finally:
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
