#!/usr/bin/env python3
"""One TCP handshake bound to a specific interface.

ICMP and UDP DNS prove the bearer forwards packets; a completed TCP
handshake proves the operator state (NAT/session) is actually there for a
stateful flow.  ``--http`` additionally sends a HEAD and reads the status
line, which is the cheapest "the web works" check without TLS.

  kebab-tcp-probe.py --iface mhi_hwip0 --host 1.1.1.1 --port 443

Exit codes: 0 connected, 1 connect failed/timeout, 2 usage or bind error,
3 --selftest failure.
"""
import argparse
import json
import socket
import sys
import time

SO_BINDTODEVICE = 25


def status_line(raw):
    """Return the HTTP status line from a response head, or None. Pure."""
    text = raw.decode("latin-1", "replace")
    first = text.split("\r\n", 1)[0].strip()
    if first.startswith("HTTP/"):
        return first
    return None


def rc_name(rc):
    """Map a connect_ex() errno onto something a human reads fast."""
    import errno
    if rc == 0:
        return "connected"
    if rc == errno.ECONNREFUSED:
        return "refused"
    if rc == errno.EHOSTUNREACH:
        return "no-route"
    if rc == errno.ENETUNREACH:
        return "net-unreachable"
    # connect_ex() reports a non-blocking timeout as EINPROGRESS/EAGAIN on
    # Linux; calling that "error-11" would read like a bug in the probe.
    if rc in (errno.EAGAIN, errno.EINPROGRESS, errno.ETIMEDOUT):
        return "timeout"
    return "error-%d" % rc


def selftest():
    bad = 0
    if status_line(b"HTTP/1.1 204 No Content\r\nServer: x\r\n\r\n") != "HTTP/1.1 204 No Content":
        print("FAIL status line not parsed"); bad += 1
    if status_line(b"garbage") is not None:
        print("FAIL garbage accepted as status"); bad += 1
    if status_line(b"HTTP/1.0 200 OK\n") != "HTTP/1.0 200 OK":
        print("FAIL bare-LF status line"); bad += 1
    for rc, want in ((0, "connected"), (111, "refused"), (113, "no-route"), (101, "net-unreachable"), (11, "timeout")):
        got = rc_name(rc)
        if got != want:
            print("FAIL rc %d -> %s (want %s)" % (rc, got, want)); bad += 1
    if rc_name(9) != "error-9":
        print("FAIL unknown errno not passed through"); bad += 1
    if bad:
        print("TCP SELFTEST FAIL (%d)" % bad)
        return 3
    print("TCP SELFTEST PASS")
    return 0


def main():
    ap = argparse.ArgumentParser(description="one TCP handshake through a bearer iface")
    ap.add_argument("--iface", default="")
    ap.add_argument("--host", default="1.1.1.1")
    ap.add_argument("--port", type=int, default=443)
    ap.add_argument("--timeout", type=float, default=6.0)
    ap.add_argument("--http", action="store_true", help="send HEAD / and read the status line")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    family = socket.AF_INET6 if ":" in args.host else socket.AF_INET
    out = {"iface": args.iface, "host": args.host, "port": args.port}
    t0 = time.time()
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        if args.iface:
            sock.setsockopt(socket.SOL_SOCKET, SO_BINDTODEVICE, args.iface.encode() + b"\x00")
    except OSError as exc:
        out.update({"ok": False, "error": "bind: " + str(exc)})
        print(json.dumps(out) if args.json else "TCP FAIL " + out["error"])
        sock.close()
        return 2
    sock.settimeout(args.timeout)
    try:
        rc = sock.connect_ex((args.host, args.port))
    except OSError as exc:
        sock.close()
        out.update({"ok": False, "error": str(exc), "ms": int((time.time() - t0) * 1000)})
        print(json.dumps(out) if args.json else "TCP FAIL " + str(exc))
        return 1
    out["ms"] = int((time.time() - t0) * 1000)
    if rc != 0:
        sock.close()
        out.update({"ok": False, "rc": rc, "error": rc_name(rc)})
        print(json.dumps(out) if args.json else "TCP FAIL %s:%d %s (%d ms)" % (args.host, args.port, out["error"], out["ms"]))
        return 1
    out["ok"] = True
    if args.http:
        try:
            sock.settimeout(args.timeout)
            sock.sendall(b"HEAD / HTTP/1.0\r\nHost: " + args.host.encode() + b"\r\n\r\n")
            line = status_line(sock.recv(256))
            out["http_status"] = line
            if not line:
                out["ok"] = False
                out["error"] = "connected but no HTTP status line"
        except OSError as exc:
            out["http_error"] = str(exc)
    sock.close()
    if args.json:
        print(json.dumps(out))
    elif out["ok"]:
        print("TCP OK %s:%d in %d ms%s" % (args.host, args.port, out["ms"],
                                           (" (" + out["http_status"] + ")") if out.get("http_status") else ""))
    else:
        print("TCP FAIL %s:%d %s" % (args.host, args.port, out.get("error", "?")))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
