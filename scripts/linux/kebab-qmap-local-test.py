#!/usr/bin/env python3
"""Local QMAP rehearsal for the kebab SDX55 data path (no SIM, no modem).

Two modes, both flashless and both confined to throw-away devices:

  veth  egress only.  A veth pair is ARPHRD_ETHER, so it can only show what
        rmnet puts on the wire: MAP header, mux_id, pkt_len, MTU 1500-4.

  tun   full duplex, and the faithful one.  A tun device in IFF_TUN mode is
        ARPHRD_NONE with hard_header_len 0, which is exactly what mhi_net
        presents to the stack.  We write one MAP-encapsulated ICMP echo
        request into /dev/net/tun, the kernel deaggregates it through rmnet,
        answers on the logical endpoint, and the reply comes back through the
        same tun fd with a freshly built MAP header.  That exercises ingress
        deaggregation, mux endpoint lookup, egress encapsulation and the MTU
        headroom in one shot.

  mapcmd  EXPERIMENTAL AND NOT VERIFIED.  Injects a QMAP flow-control command
        into a tun-backed rmnet link to see whether the kernel ACKs it.  This
        path panicked the box once (2026-11-13: hard reset, no pstore record,
        panic=15 auto-reboot; see MM doc 4.20), so do NOT run it on a machine
        you are not willing to lose, and never enable ingress-commands on the
        real link until the cause is understood.

Filesystem/route side effects: two netdev names (qmapv*/qmapt*) and one /32
outside any real subnet; everything is removed at the end unless --keep.

Run as root.  Exit code 0 only when every requested mode passes.
"""
import argparse
import fcntl
import json
import os
import select
import shutil
import socket
import struct
import subprocess
import sys
import time

RMNET_KO = "/usr/local/lib/kebab-modem/rmnet/rmnet.ko"
VETH0 = "qmapv0"
VETH1 = "qmapv1"
TUN_NAME = "qmapt0"
TUNSETIFF = 0x400454CA
IFF_TUN = 0x0001
ETH_P_MAP = 0x00F9


def run(cmd, check=False):
    p = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True)
    if check and p.returncode != 0:
        raise SystemExit("command failed: " + str(cmd) + " / " + p.stdout + p.stderr)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def module_loaded():
    return os.path.isdir("/sys/module/rmnet")


def csum(data):
    if len(data) % 2:
        data += bytes(1)
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) + data[i + 1]
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def csum_ok(data):
    return csum(data) == 0


def icmp_echo(ident, seq, payload, reply=False):
    body = struct.pack("!BBHHH", 0 if reply else 8, 0, 0, ident, seq) + payload
    c = csum(body)
    return body[:2] + struct.pack("!H", c) + body[4:]


def ipv4(src, dst, proto, payload, ident):
    hdr = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), ident, 0, 64, proto, 0,
                      socket.inet_aton(src), socket.inet_aton(dst))
    c = csum(hdr)
    return hdr[:10] + struct.pack("!H", c) + hdr[12:] + payload


def map_hdr(mux, payload_len, flags=0):
    return struct.pack("!BBH", flags, mux, payload_len)


def parse_map(frame, expect_mux):
    """Split a MAP frame into (ok, info). Info carries everything we assert."""
    if len(frame) < 4 + 20:
        return False, {"why": "short frame", "len": len(frame)}
    flags, mux, plen = struct.unpack("!BBH", frame[:4])
    inner = frame[4:]
    info = {"flags": flags, "mux_id": mux, "pkt_len": plen, "inner_len": len(inner)}
    if flags & 0x80:
        return False, dict(info, why="MAP command flag set")
    if mux != expect_mux:
        return False, dict(info, why="wrong mux id (want %d)" % expect_mux)
    if plen != len(inner):
        return False, dict(info, why="pkt_len != inner length")
    if inner[0] >> 4 != 4:
        return False, dict(info, why="inner is not IPv4")
    ihl = (inner[0] & 0x0F) * 4
    if ihl < 20 or len(inner) < ihl:
        return False, dict(info, why="bad IHL")
    if not csum_ok(inner[:ihl]):
        return False, dict(info, why="inner IPv4 checksum invalid")
    info["src"] = socket.inet_ntoa(inner[12:16])
    info["dst"] = socket.inet_ntoa(inner[16:20])
    info["proto"] = inner[9]
    info["ip_csum_ok"] = True
    return True, info


# ---------------------------------------------------------------- veth mode
def veth_roundtrip(mux_ids):
    res = {"mode": "veth", "links": [], "frames": {}, "bad": [], "ok": False}
    run("ip link del " + VETH0)
    run("ip link add " + VETH0 + " type veth peer name " + VETH1, check=True)
    run("ip link set " + VETH0 + " up", check=True)
    run("ip link set " + VETH1 + " up", check=True)
    made = []
    for mux in mux_ids:
        name = "qmapv" + str(mux)
        run("ip link del " + name)
        run("ip link add link " + VETH0 + " name " + name + " type rmnet mux_id " + str(mux), check=True)
        run("ip addr add 10.99." + str(mux) + ".1/24 dev " + name, check=True)
        run("ip link set " + name + " up", check=True)
        run("ip addr add 10.99." + str(mux) + ".2/24 dev " + VETH1, check=True)
        run("ip neigh replace 10.99." + str(mux) + ".2 lladdr 02:00:00:00:00:02 dev " + name)
        mtu = int(run("cat /sys/class/net/" + name + "/mtu")[1] or 0)
        flags = run("ip -d link show " + name)[1]
        res["links"].append({"mux": mux, "iface": name, "mtu": mtu,
                             "mtu_ok": mtu == 1496,
                             "deagg": "INGRESS_DEAGGREGATION" in flags})
        made.append((mux, name))

    sniffer = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
    sniffer.bind((VETH1, 0))
    for seq, (mux, name) in enumerate(made, start=1):
        for shot in range(3):
            marker = b"kebab-qmap-" + str(mux).encode()
            pkt = ipv4("10.99." + str(mux) + ".1", "10.99." + str(mux) + ".2", 1,
                       icmp_echo(0x4B00 + mux, seq * 10 + shot, marker), 0x1000 + mux + shot)
            raw = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
            raw.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, name.encode())
            raw.sendto(pkt, ("10.99." + str(mux) + ".2", 0))
            raw.close()
            time.sleep(0.1)
    frames = []
    sniffer.settimeout(0.3)
    deadline = time.time() + 1.5
    while time.time() < deadline:
        try:
            frames.append(sniffer.recv(4096))
        except socket.timeout:
            continue
    sniffer.close()

    for pkt in frames:
        if len(pkt) < 24:
            continue
        mux = pkt[1]
        if mux not in mux_ids:
            continue
        ok, info = parse_map(pkt, mux)
        if not ok:
            if info.get("why") == "inner is not IPv4":
                # IPv6 (ND/MLD) going out the same logical endpoint is also MAP
                # wrapped; it is not part of the assertion.
                res["skipped_non_ipv4"] = res.get("skipped_non_ipv4", 0) + 1
                continue
            res["bad"].append(info)
            continue
        if info["src"] != "10.99." + str(mux) + ".1" or info["dst"] != "10.99." + str(mux) + ".2":
            res["bad"].append(dict(info, why="wrong addresses"))
            continue
        marker = b"kebab-qmap-" + str(mux).encode()
        if marker not in pkt:
            res["bad"].append(dict(info, why="payload marker missing"))
            continue
        res["frames"][str(mux)] = res["frames"].get(str(mux), 0) + 1

    missing = [m for m in mux_ids if str(m) not in res["frames"]]
    res["missing_mux"] = missing
    res["ok"] = (not missing) and (not res["bad"])
    for mux in mux_ids:
        run("ip link del qmapv" + str(mux))
    run("ip link del " + VETH0)
    return res


# ----------------------------------------------------------------- tun mode
def tun_open(name):
    fd = os.open("/dev/net/tun", os.O_RDWR)
    ifr = struct.pack("16sH", name.encode(), IFF_TUN)
    ifr += bytes(40 - len(ifr))
    fcntl.ioctl(fd, TUNSETIFF, ifr)
    return fd


def tun_roundtrip(mux):
    res = {"mode": "tun", "frames": [], "bad": [], "ok": False}
    rdev = "qmaptu" + str(mux)
    fd = tun_open(TUN_NAME)
    try:
        run("ip link set " + TUN_NAME + " up", check=True)
        dev_type = run("cat /sys/class/net/" + TUN_NAME + "/type")[1]
        run("ip link del " + rdev)
        run("ip link add link " + TUN_NAME + " name " + rdev + " type rmnet mux_id " + str(mux), check=True)
        run("ip addr add 10.88." + str(mux) + ".1/24 dev " + rdev, check=True)
        run("ip link set " + rdev + " up", check=True)

        mtu = int(run("cat /sys/class/net/" + rdev + "/mtu")[1] or 0)
        flags = run("ip -d link show " + rdev)[1]
        res["links"] = [{"mux": mux, "iface": rdev, "real": TUN_NAME,
                         "real_type": dev_type, "mtu": mtu, "mtu_ok": mtu == 1496,
                         "deagg": "INGRESS_DEAGGREGATION" in flags}]

        # One MAP-encapsulated ICMP echo request, source outside the box.
        marker = b"kebab-qmap-tun-" + str(mux).encode()
        req = ipv4("10.88." + str(mux) + ".2", "10.88." + str(mux) + ".1", 1,
                   icmp_echo(0x5157, 7, marker), 0x2222)
        frame = struct.pack("!HH", 0, ETH_P_MAP) + map_hdr(mux, len(req)) + req
        os.write(fd, frame)

        deadline = time.time() + 3.0
        while time.time() < deadline:
            r, _, _ = select.select([fd], [], [], 0.3)
            if not r:
                continue
            buf = os.read(fd, 4096)
            if len(buf) < 4:
                continue
            proto = struct.unpack("!H", buf[2:4])[0]
            body = buf[4:]
            entry = {"pi_proto": hex(proto), "len": len(body)}
            if proto != ETH_P_MAP:
                # The kernel also queues IPv6 (ND/MLD) on this device; those are
                # plain packets, not MAP, and are not part of the assertion.
                res["skipped_non_map"] = res.get("skipped_non_map", 0) + 1
                continue
            ok, info = parse_map(body, mux)
            if not ok:
                if info.get("why") == "inner is not IPv4":
                    # rmnet also MAP-encapsulates IPv6 (e.g. our ND traffic).
                    res["skipped_non_ipv4"] = res.get("skipped_non_ipv4", 0) + 1
                else:
                    res["bad"].append(dict(entry, why=info.get("why"), map=info))
                continue
            inner = body[4:]
            icmp = inner[(inner[0] & 0x0F) * 4:]
            itype = icmp[0] if icmp else -1
            ident, seq = struct.unpack("!HH", icmp[4:8]) if len(icmp) >= 8 else (0, 0)
            entry.update({"map": info, "icmp_type": itype, "icmp_id": ident, "icmp_seq": seq,
                          "reply": itype == 0 and ident == 0x5157 and seq == 7,
                          "marker": marker in body,
                          "src": info.get("src"), "dst": info.get("dst")})
            if entry["src"] != "10.88." + str(mux) + ".1" or entry["dst"] != "10.88." + str(mux) + ".2":
                res["bad"].append(dict(entry, why="reply addresses are not swapped"))
                continue
            if not entry["reply"]:
                res["bad"].append(dict(entry, why="inner is not the ICMP echo reply we expect"))
                continue
            if not entry["marker"]:
                res["bad"].append(dict(entry, why="payload marker missing in the reply"))
                continue
            res["frames"].append(entry)
        res["ok"] = bool(res["frames"]) and not res["bad"]
        res["counters"] = run("ip -s link show " + rdev)[1].splitlines()[-2:]
    finally:
        run("ip link del " + rdev)
        os.close(fd)
    return res


# --------------------------------------------------------- MAP command mode
# rmnet_handlers.c drops a MAP control frame unless the port has
# RMNET_FLAGS_INGRESS_MAP_COMMANDS ("ingress-commands on" in iproute2):
#
#     if (map_header->flags & MAP_CMD_FLAG) {
#         if (port->data_format & RMNET_FLAGS_INGRESS_MAP_COMMANDS)
#             return rmnet_map_command(skb, port);
#         goto free_skb;
#     }
#
# So if the modem ever sends a QMAP flow-control command, a link created
# without that flag never ACKs it.  This mode was written to prove the path
# locally -- and the single run of it on 2026-11-13 ended in a hard reset with
# no panic record (MM doc 4.20), so it is left in the tree UNVERIFIED and must
# not be used to justify enabling ingress-commands.  If you run it, do so from a
# serial console or with pstore configured.
# the ACK comes back through the tun fd, and a disabled TX queue really does
# hold the reply until FLOW_ENABLE.
MAP_CMD_FLAG = 0x80
CMD_FLOW_DISABLE = 1
CMD_FLOW_ENABLE = 2
CMD_REQUEST = 0
CMD_ACK = 1


def map_cmd_frame(mux, name, txn, cmd_type=CMD_REQUEST, seq=7, qos=0):
    # struct rmnet_map_control_command: u8 name, u8 type:2+rsvd:6, u16 rsvd,
    # u32 txn, then the flow-control union (u16 family/rsvd, __be16 seq, __be32 qos)
    cmd = struct.pack('<BBHI', name, cmd_type & 0x03, 0, txn)
    cmd += struct.pack('<H', 0)
    cmd += struct.pack('!H', seq)
    cmd += struct.pack('!I', qos)
    return map_hdr(mux, len(cmd), MAP_CMD_FLAG) + cmd


def read_frames(fd, seconds):
    out = []
    deadline = time.time() + seconds
    while True:
        left = deadline - time.time()
        if left <= 0:
            break
        r, _, _ = select.select([fd], [], [], min(left, 0.3))
        if not r:
            continue
        try:
            buf = os.read(fd, 4096)
        except OSError:
            break
        if len(buf) < 4:
            continue
        out.append((struct.unpack('!H', buf[2:4])[0], buf[4:]))
    return out


def find_ack(frames, mux, name, txn):
    for proto, body in frames:
        if proto != ETH_P_MAP or len(body) < 4 + 16:
            continue
        flags, m2, plen = struct.unpack('!BBH', body[:4])
        if not (flags & MAP_CMD_FLAG) or m2 != mux:
            continue
        cname, ctype_res, _rsvd, ctxn = struct.unpack('<BBHI', body[4:12])
        if cname == name and (ctype_res & 0x03) == CMD_ACK and ctxn == txn:
            return {'name': cname, 'type': ctype_res & 0x03, 'txn': hex(ctxn),
                    'pkt_len': plen}
    return None


def mapcmd_roundtrip(mux):
    res = {'mode': 'mapcmd', 'acks': [], 'bad': [], 'ok': False,
           'blocked': None, 'flushed': None, 'no_ack_without_flag': None}
    rdev = 'qmapc' + str(mux)
    fd = tun_open(TUN_NAME)
    try:
        run('ip link set ' + TUN_NAME + ' up', check=True)
        run('ip link del ' + rdev)
        run('ip link add link ' + TUN_NAME + ' name ' + rdev +
            ' type rmnet mux_id %d ingress-commands on' % mux, check=True)
        run('ip addr add 10.89.%d.1/24 dev %s' % (mux, rdev), check=True)
        run('ip link set ' + rdev + ' up', check=True)
        res['link_flags'] = 'INGRESS_MAP_COMMANDS' in run('ip -d link show ' + rdev)[1]

        # 1) FLOW_DISABLE must be acknowledged
        txn_dis = 0x11223344
        os.write(fd, struct.pack('!HH', 0, ETH_P_MAP) +
                 map_cmd_frame(mux, CMD_FLOW_DISABLE, txn_dis))
        ack = find_ack(read_frames(fd, 1.5), mux, CMD_FLOW_DISABLE, txn_dis)
        res['acks'].append({'cmd': 'flow-disable', 'acked': bool(ack), 'ack': ack})

        # 2) the TX queue is stopped now: a reply must not come back
        marker = b'kebab-mapcmd-' + str(mux).encode()
        req = ipv4('10.89.%d.2' % mux, '10.89.%d.1' % mux, 1,
                   icmp_echo(0x5a5a, 11, marker), 0x3333)
        os.write(fd, struct.pack('!HH', 0, ETH_P_MAP) + map_hdr(mux, len(req)) + req)
        frames = read_frames(fd, 1.5)
        res['blocked'] = not any(p == ETH_P_MAP and not (b[0] & MAP_CMD_FLAG)
                                 for p, b in frames if len(b) >= 4)

        # 3) FLOW_ENABLE must be acknowledged AND flush the held reply
        txn_ena = 0x11223345
        os.write(fd, struct.pack('!HH', 0, ETH_P_MAP) +
                 map_cmd_frame(mux, CMD_FLOW_ENABLE, txn_ena))
        frames = read_frames(fd, 2.5)
        ack = find_ack(frames, mux, CMD_FLOW_ENABLE, txn_ena)
        res['acks'].append({'cmd': 'flow-enable', 'acked': bool(ack), 'ack': ack})
        res['flushed'] = any(p == ETH_P_MAP and not (b[0] & MAP_CMD_FLAG) and marker in b
                             for p, b in frames if len(b) >= 4)

        # 4) negative control: the same command on a link without the flag is
        #    dropped, and the TX queue must stay usable
        run('ip link del ' + rdev)
        run('ip link add link ' + TUN_NAME + ' name ' + rdev +
            ' type rmnet mux_id %d' % mux, check=True)
        run('ip addr add 10.89.%d.1/24 dev %s' % (mux, rdev), check=True)
        run('ip link set ' + rdev + ' up', check=True)
        txn = 0x11223346
        os.write(fd, struct.pack('!HH', 0, ETH_P_MAP) +
                 map_cmd_frame(mux, CMD_FLOW_DISABLE, txn))
        frames = read_frames(fd, 1.5)
        res['no_ack_without_flag'] = find_ack(frames, mux, CMD_FLOW_DISABLE, txn) is None
        req2 = ipv4('10.89.%d.2' % mux, '10.89.%d.1' % mux, 1,
                    icmp_echo(0x5b5b, 12, marker), 0x3334)
        os.write(fd, struct.pack('!HH', 0, ETH_P_MAP) + map_hdr(mux, len(req2)) + req2)
        frames = read_frames(fd, 2.0)
        res['still_flows_without_flag'] = any(
            p == ETH_P_MAP and not (b[0] & MAP_CMD_FLAG) and marker in b
            for p, b in frames if len(b) >= 4)
    finally:
        run('ip link del ' + rdev)
        os.close(fd)
    res['ok'] = (bool(res['link_flags'])
                 and all(a['acked'] for a in res['acks'])
                 and bool(res['blocked'])
                 and bool(res['flushed'])
                 and bool(res['no_ack_without_flag'])
                 and bool(res['still_flows_without_flag']))
    return res


# --------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="both",
                    help="veth | tun | both (=tun,veth) | mapcmd (EXPERIMENTAL, panicked once) | all | comma list")
    ap.add_argument("--mux", default="7", help="mux id (tun) or comma list (veth)")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if os.geteuid() != 0:
        print("FAIL: needs root", file=sys.stderr)
        return 1
    if not os.path.exists(RMNET_KO):
        print("FAIL: " + RMNET_KO + " missing", file=sys.stderr)
        return 1
    if not shutil.which("ip"):
        print("FAIL: iproute2 not found", file=sys.stderr)
        return 1

    was_loaded = module_loaded()
    if not was_loaded:
        rc, _, err = run(["insmod", RMNET_KO])
        if rc != 0:
            print("FAIL: cannot load rmnet: " + err, file=sys.stderr)
            return 1

    mux_ids = [int(x) for x in args.mux.split(",") if x.strip()]
    modes = []
    for m in args.mode.split(","):
        m = m.strip()
        if m == "both":
            modes += ["tun", "veth"]
        elif m == "all":
            modes += ["tun", "veth", "mapcmd"]
        elif m:
            modes.append(m)
    bad = [m for m in modes if m not in ("tun", "veth", "mapcmd")]
    if bad:
        print("FAIL: unknown mode(s): " + ", ".join(bad), file=sys.stderr)
        return 2
    result = {"requested": args.mode, "modes": modes, "results": []}
    try:
        if "tun" in modes:
            result["results"].append(tun_roundtrip(mux_ids[0]))
        if "veth" in modes:
            result["results"].append(veth_roundtrip(mux_ids))
        if "mapcmd" in modes:
            result["results"].append(mapcmd_roundtrip(mux_ids[0]))
    finally:
        if not args.keep and module_loaded():
            rc, _, err = run(["rmmod", "rmnet"])
            result["rmmod"] = {"rc": rc, "err": err, "still_loaded": module_loaded()}
        result["leftover_links"] = run("ip -o link show")[1].count("qmap")

    result["verdict"] = "PASS" if all(r["ok"] for r in result["results"]) else "FAIL"
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for r in result["results"]:
            if r["mode"] == "tun":
                for link in r.get("links", []):
                    print("tun: " + link["iface"] + " over " + link["real"] + " (ARPHRD type " +
                          link["real_type"] + ") mux " + str(link["mux"]) + " mtu " + str(link["mtu"]) +
                          (" (deagg on)" if link.get("deagg") else ""))
                for f in r.get("frames", []):
                    print("  reply via MAP: mux " + str(f["map"]["mux_id"]) +
                          " proto " + f["pi_proto"] + " inner " + f["src"] + " -> " + f["dst"] +
                          " icmp_type " + str(f["icmp_type"]) + " id " + str(f["icmp_id"]) +
                          " seq " + str(f["icmp_seq"]) + " marker " + str(f["marker"]))
            else:
                for link in r.get("links", []):
                    print("veth: " + link["iface"] + " mux " + str(link["mux"]) +
                          " mtu " + str(link["mtu"]) + (" (deagg on)" if link.get("deagg") else ""))
                print("  frames per mux: " + str(r.get("frames")))
            if r["mode"] == "mapcmd":
                print("mapcmd: link flags carry INGRESS_MAP_COMMANDS: " + str(r.get("link_flags")))
                for a in r.get("acks", []):
                    print("  " + a["cmd"] + ": " + ("ACK " + str(a["ack"]) if a["acked"] else "NO ACK"))
                print("  TX queue held the reply while disabled: " + str(r.get("blocked")))
                print("  FLOW_ENABLE flushed the held reply: " + str(r.get("flushed")))
                print("  without the flag the command is dropped: " + str(r.get("no_ack_without_flag")))
                print("  without the flag data still flows: " + str(r.get("still_flows_without_flag")))
            for b in r.get("bad", []):
                print("  BAD: " + str(b))
            print("  " + r["mode"] + ": " + ("PASS" if r["ok"] else "FAIL"))
        print("leftover qmap links: " + str(result["leftover_links"]))
        print("VERDICT: " + result["verdict"])
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
