#!/usr/bin/env python3
"""Minimal raw QMI client for the kebab SDX55 over QRTR.

Why this exists: over QRTR a QMI "client" is the socket, not a numeric CID, so
two separate qmicli runs cannot share a WDS client.  BIND_MUX_DATA_PORT and
START_NETWORK must therefore be issued on the *same* socket.  libqmi does this
in-process (that is what ModemManager does); from the shell there is no way to
reuse the client, which is exactly why Apollo shipped a wds_test binary.  This
script is the same idea in Python, using AF_QIPCRTR directly.

Framing (no QMUX header over QRTR, but the QMI header keeps its TLV length):
    u8 flags (0 request, 2 response, 4 indication), u16 txn, u16 msgid,
    u16 tlv_length, then TLVs of u8 type / u16 length / value.
The tlv_length field is NOT optional: a message without it is silently dropped
by the modem (verified 2026-11-12: 5-byte header -> no reply, 7-byte -> reply).

Ports come from qrtr-lookup: the modem registers QMI services as ordinary QRTR
services on its node (WDS -> "Wireless Data Service", DMS -> "Device Management
Service").

Usage:
    kebab-qmi-wds.py info                     # show node/port discovery + state
    kebab-qmi-wds.py dms-manufacturer         # framing sanity check
    kebab-qmi-wds.py bind                     # bind pcie/iface 4/mux 0
    kebab-qmi-wds.py start --apn cmnet        # start network (needs a SIM)
    kebab-qmi-wds.py up --profile 3           # this modem: profile 3 = CMNET (preferred)
    kebab-qmi-wds.py settings                 # get current IP settings
    kebab-qmi-wds.py status                   # packet service status
    kebab-qmi-wds.py up --profile 3           # bind + start + settings (profile 3 = CMNET)
    kebab-qmi-wds.py stop --handle N          # stop network
    kebab-qmi-wds.py release                  # stop + (socket close releases)

Nothing is written to disk and no credentials are involved.
"""

import argparse
import ctypes
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import time

AF_QIPCRTR = 42
LOCAL_NODE = 1          # qrtr_local_nid default; bind node must match it
QRTR_CTRL_PORT = 0xFFFFFFFE

SVC_WDS = 1
SVC_DMS = 2
SVC_WDA = 26            # Wireless Data Administrative Service (QMAP lives here)
SVC_DESCRIPTIONS = {
    SVC_WDS: "Wireless Data Service",
    SVC_DMS: "Device Management Service",
    SVC_WDA: "Wireless data administrative service",
}

MSG_DMS_GET_MANUFACTURER = 0x0021
MSG_WDS_BIND_MUX_DATA_PORT = 0x00A2
MSG_WDS_START_NETWORK = 0x0020
MSG_WDS_STOP_NETWORK = 0x0021
MSG_WDS_GET_PACKET_SERVICE_STATUS = 0x0022
MSG_WDS_GET_CURRENT_SETTINGS = 0x002D
MSG_GET_SUPPORTED_MESSAGES = 0x001E   # same id in every QMI service
MSG_WDS_GET_PROFILE_SETTINGS = 0x002B
MSG_WDS_GET_DEFAULT_PROFILE_NUMBER = 0x0049
MSG_WDS_GET_MAX_LTE_ATTACH_PDN_NUMBER = 0x0092
MSG_WDS_GET_LTE_ATTACH_PARAMETERS = 0x0085
MSG_WDS_SET_LTE_ATTACH_PDN_LIST = 0x0093
MSG_WDS_GET_LTE_ATTACH_PDN_LIST = 0x0094
MSG_WDS_GET_CHANNEL_RATES = 0x0023
MSG_WDA_SET_DATA_FORMAT = 0x0020
MSG_WDA_GET_DATA_FORMAT = 0x0021
MSG_WDS_BIND_SUBSCRIPTION = 0x00AF
MSG_WDS_GET_BIND_SUBSCRIPTION = 0x00B0

EP_TYPE = {"undefined": 0xFF, "hsic": 1, "hsusb": 2, "pcie": 3, "embedded": 4, "bam-dmux": 5}
IP_FAMILY = {"4": 4, "6": 6, "any": 8}
# QmiSubscriptionType: ModemManager binds the WDS client to a subscription
# before starting a call on a modem that can see more than one.
SUBSCRIPTION_TYPE = {"default": 0x00, "primary": 0x01, "secondary": 0x02, "tertiary": 0x03, "any": 0xFF}
# libqmi QmiWdsProfileType / QmiWdsProfileFamily / QmiWdsIpSupportType
PROFILE_TYPE = {"3gpp": 0, "3gpp2": 1, "epc": 2, "all": 0xFF}
PROFILE_FAMILY = {"embedded": 0, "tethered": 1}
IP_SUPPORT = {0: "ipv4", 1: "ipv6", 2: "ipv4v6"}
# libqmi QmiWdaLinkLayerProtocol / QmiWdaDataAggregationProtocol
LINK_LAYER = {0: "unknown", 1: "802.3", 2: "raw-ip", 3: "ethernet-pdu"}
AGGREGATION = {0: "disabled", 1: "tlp", 2: "qc-ncm", 3: "mbim", 4: "rndis",
               5: "qmap", 6: "qmapv2", 7: "qmapv3", 8: "qmapv4", 9: "qmapv5"}
LL_ID = {v: k for k, v in LINK_LAYER.items()}
AGG_ID = {v: k for k, v in AGGREGATION.items()}
# WDA Get Data Format output TLV ids -> internal key.  Note that Get's 0x1B is
# "flow control" while Set's 0x1B is "UL max datagrams": same number, different
# meaning per direction, so they are never mixed in one dict.
FORMAT_TLV = {0x11: "llp", 0x12: "ul", 0x13: "dl", 0x14: "ndp", 0x15: "dl_max_dgram",
              0x16: "dl_max_size", 0x17: "ul_max_dgram", 0x18: "ul_max_size",
              0x1A: "dl_min_pad", 0x1B: "flow"}
SUBSCRIPTION_NAME = {v: k for k, v in SUBSCRIPTION_TYPE.items()}
CONN_NAMES = {0: "unknown", 1: "disconnected", 2: "connected", 3: "suspended", 4: "authenticating"}
REQ_SETTINGS_ALL = 0x03FF   # bits 0..9: profile id/name, pdp type, apn, dns, username, auth, ip, gateway
REQ_SETTINGS_ALL |= 0x2000 | 0x8000   # + MTU + IP family (libqmi QmiWdsRequestedSettings bits 13/15)

# Only the errors this path actually produces; unknown values are printed raw.
QMI_ERRORS = {
    0: "none", 1: "internal", 2: "malformed-message", 3: "not-supported",
    5: "out-of-call", 9: "call-failure", 14: "call-failed", 15: "out-of-call",
    46: "general-error", 52: "device-not-ready", 69: "disabled",
    70: "invalid-operation", 71: "invalid-qmi-command", 94: "not-supported",
}


CALL_END_GENERIC = {
    1: "unspecified", 2: "client-end", 3: "no-service", 4: "fade",
    5: "release-normal", 6: "access-attempt-in-progress", 7: "access-failure",
    8: "redirection-or-handoff", 9: "close-in-progress",
    10: "authentication-failed", 11: "internal-error",
}


REASON_DB = None


def reason_db():
    """Names harvested from libqmi's qmi-enums-wds.h (see gen-wds-call-end-reasons.py).

    A number alone cannot tell "APN wrong" from "subscription blocked" from
    "radio never attached"; with the table the failure line does.
    """
    global REASON_DB
    if REASON_DB is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "wds-call-end-reasons.json")
        try:
            with open(path, encoding="utf-8") as fh:
                REASON_DB = json.load(fh)
        except (OSError, ValueError):
            REASON_DB = {}
    return REASON_DB


def call_end_name(reason):
    if reason is None:
        return "?"
    db = reason_db().get("call_end", {})
    for group in ("generic", "cdma", "gsm-wcdma", "evdo"):
        name = db.get(group, {}).get(str(reason))
        if name:
            return f"{name} ({group})"
    if reason in CALL_END_GENERIC:
        return CALL_END_GENERIC[reason] + " (generic)"
    return f"reason-{reason}"


def verbose_call_end_name(vtype, vreason):
    """(type name, reason name or None) for the verbose call-end TLV."""
    db = reason_db()
    tname = db.get("verbose_type", {}).get(str(vtype)) or f"type-{vtype}"
    rname = db.get("verbose", {}).get(tname, {}).get(str(vreason))
    return tname, rname


def explain_start(data):
    """Decode the call-end-reason TLVs a failed START_NETWORK returns."""
    for t, v in parse_tlvs(data):
        if t == 0x10 and len(v) >= 2:
            r = struct.unpack("<H", v[:2])[0]
            print(f"  call-end-reason: {r} = {call_end_name(r)}")
        elif t == 0x11 and len(v) >= 4:
            vtype, vreason = struct.unpack("<Hh", v[:4])
            tname, rname = verbose_call_end_name(vtype, vreason)
            print(f"  verbose-call-end: type={vtype} ({tname}) reason={vreason}"
                  + (f" ({rname})" if rname else ""))


SETTINGS_TLV = {
    0x10: "profile_name", 0x11: "pdp_type", 0x14: "apn",
    0x15: "dns1", 0x16: "dns2",
    0x1E: "ip", 0x20: "gw", 0x21: "mask", 0x29: "mtu",
    0x25: "ipv6", 0x26: "gw6", 0x27: "dns1_6", 0x28: "dns2_6",
    0x2B: "ip_family",
}

# libqmi QmiWdsPdpType / QmiWdsIpFamily - the numbers, not the order, are what
# the firmware sends (verified against libqmi data/qmi-service-wds.json).
PDP_NAMES = {0: "ipv4", 1: "ppp", 2: "ipv6", 3: "ipv4-or-ipv6", 4: "non-ip"}
IP_FAMILY_NAMES = {0: "unknown", 4: "ipv4", 6: "ipv6", 8: "unspecified", 12: "ethernet"}


def ip4(v):
    return socket.inet_ntoa(struct.pack("<I", struct.unpack("<I", v)[0]))


def prefix_from_mask(mask):
    return bin(mask).count("1")


def parse_settings(data):
    """Decode the interesting Get-Current-Settings TLVs into a dict."""
    out = {}
    for t, v in parse_tlvs(data):
        name = SETTINGS_TLV.get(t)
        if not name:
            continue
        if name in ("ip", "gw", "mask", "dns1", "dns2", "mtu") and len(v) >= 4:
            out[name] = struct.unpack("<I", v[:4])[0]
        elif name == "pdp_type" and len(v) >= 1:
            out[name] = PDP_NAMES.get(v[0], f"pdp-{v[0]}")
        elif name == "ip_family" and len(v) >= 1:
            out[name] = IP_FAMILY_NAMES.get(v[0], f"family-{v[0]}")
        elif name in ("apn", "profile_name"):
            out[name] = v.rstrip(b"\x00").decode("utf-8", "replace")
        elif name in ("ipv6", "gw6") and len(v) >= 17:
            # libqmi "Get Current Settings" 0x002D: 0x25/0x26 are
            # sequence { 16-byte address in network order, u8 prefix length }
            out[name + "_str"] = socket.inet_ntop(socket.AF_INET6, v[:16])
            out[name + "_prefix"] = v[16]
        elif name in ("dns1_6", "dns2_6") and len(v) >= 16:
            out[name + "_str"] = socket.inet_ntop(socket.AF_INET6, v[:16])
    for k in ("ip", "gw", "mask", "dns1", "dns2"):
        if k in out:
            out[k + "_str"] = ip4(struct.pack("<I", out[k]))
    if "mask" in out:
        out["prefix"] = prefix_from_mask(out["mask"])
    return out


def ip_run(args):
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


# Default-route helpers.  A metric-less "ip route replace default" ties with the
# server's real uplink (both metric 0), and then the kernel picks by FIB order -
# which is how a modem day silently takes the box off the network.  The bearer
# therefore gets a high metric and stays a fallback.
def route_cmds(iface, gw, metric, v6=None):
    """argv list for the default routes we install (pure: no side effects)."""
    out = []
    if gw:
        out.append(["ip", "route", "replace", "default", "via", gw, "dev", iface,
                    "metric", str(metric)])
    if v6 and v6[2]:
        out.append(["ip", "-6", "route", "replace", "default", "via", v6[2],
                    "dev", iface, "metric", str(metric)])
    return out


def route_del_cmds(iface, gw, v6=None):
    """argv list to remove what route_cmds installed (pure)."""
    out = []
    if gw:
        out.append(["ip", "route", "del", "default", "via", gw, "dev", iface])
    if v6 and v6[2]:
        out.append(["ip", "-6", "route", "del", "default", "via", v6[2], "dev", iface])
    return out


def configure_iface(iface, ip, prefix, gw, mtu, add_route=False, v6=None,
                    route_metric=1000):
    # v6 = (address, prefix, gateway) or None.  IPv6 is configured whenever the
    # bearer hands one out: profile 3 is "ipv4-or-ipv6", and a v6-only bearer
    # would otherwise look like "no address" even though the call is fine.
    # Bring the link up last: an address and the MTU while down cannot leak a
    # half-configured bearer (mhi_net's own default MTU is 16 KiB).
    if ip:
        ip_run(["ip", "addr", "replace", f"{ip}/{prefix}", "dev", iface])
    if v6:
        addr6, prefix6 = v6[0], v6[1]
        # nodad: a point-to-point bearer has nobody to resolve the address
        # against, and DAD would leave it "tentative" for a second or two.
        ip_run(["ip", "-6", "addr", "replace", f"{addr6}/{prefix6}",
                "dev", iface, "nodad"])
    if mtu:
        ip_run(["ip", "link", "set", iface, "mtu", str(mtu)])
    ip_run(["ip", "link", "set", iface, "up"])
    if add_route:
        # Off by default: this box is a server whose default route is the
        # Wi-Fi/USB uplink.  When it IS asked for, the metric keeps the bearer a
        # fallback instead of stealing the uplink (see route_cmds).
        for argv in route_cmds(iface, gw, route_metric, v6):
            print(f"route   : {' '.join(argv)}")
            ip_run(argv)


def iface_mtu(iface):
    """Current MTU, or None when it cannot be read.  Cleanup restores it."""
    try:
        with open(f"/sys/class/net/{iface}/mtu") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def cleanup_iface(iface, ip, prefix, gw, add_route=False, v6=None, restore_mtu=None):
    if add_route:
        for argv in route_del_cmds(iface, gw, v6):
            subprocess.run(argv, capture_output=True)
    if v6 and v6[0]:
        subprocess.run(["ip", "-6", "addr", "del", f"{v6[0]}/{v6[1]}", "dev", iface], capture_output=True)
    if ip:
        subprocess.run(["ip", "addr", "del", f"{ip}/{prefix}", "dev", iface], capture_output=True)
    # Leave the bearer down: it has no address any more and the next data-up
    # brings it back up itself.
    subprocess.run(["ip", "link", "set", iface, "down"], capture_output=True)
    # Put the driver default back as well: mhi_net hands out 16384, and a
    # leftover 1500 makes every later "is the interface untouched?" check lie.
    if restore_mtu:
        subprocess.run(["ip", "link", "set", iface, "mtu", str(restore_mtu)], capture_output=True)


def packet_state_from(data):
    for t, v in parse_tlvs(data):
        if t == 0x01 and len(v) >= 1:
            return v[0]
    return None


def packet_state(c):
    data = c.request(MSG_WDS_GET_PACKET_SERVICE_STATUS)
    c.result(data)
    return packet_state_from(data)


def wait_connected(c, timeout):
    """Poll Get Packet Service Status until 'connected' or the deadline.

    Returns the last code seen (2 == connected).  A brand new call is often
    still 'authenticating' for a second or two, and asking for settings too
    early yields QMI error 15/out-of-call even on a perfectly good SIM.
    """
    deadline = time.time() + max(0, timeout)
    state = None
    while True:
        try:
            state = packet_state(c)
        except Exception:
            state = None
        if state == 2 or time.time() >= deadline:
            return state
        time.sleep(1)


ERRORS_DB = None


def errors_db():
    """Protocol error names from libqmi's qmi-errors.h (gen-qmi-error-names.py).

    125 values, so "error 74" reads as "information-unavailable" instead of
    sending the next person down the wrong road.
    """
    global ERRORS_DB
    if ERRORS_DB is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qmi-errors.json")
        try:
            with open(path, encoding="utf-8") as fh:
                ERRORS_DB = json.load(fh).get("errors", {})
        except (OSError, ValueError):
            ERRORS_DB = {}
    return ERRORS_DB


def err_name(err):
    if err is None:
        return "?"
    name = errors_db().get(str(err))
    if name:
        return name
    return QMI_ERRORS.get(err, f"qmi-error-{err}")

_libc = ctypes.CDLL("libc.so.6", use_errno=True)
_libc.sendto.restype = ctypes.c_ssize_t
_libc.sendto.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
_libc.bind.restype = ctypes.c_int
_libc.bind.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]


def qrtr_lookup():
    """Return [(service, node, port, description)] from qrtr-lookup."""
    out = subprocess.run(["qrtr-lookup"], capture_output=True, text=True, timeout=10).stdout
    rows = []
    for line in out.splitlines()[1:]:
        f = line.split(None, 5)
        if len(f) < 6:
            continue
        try:
            rows.append((int(f[0]), int(f[3]), int(f[4]), f[5].strip()))
        except ValueError:
            continue
    return rows


def find_service(rows, service_id):
    cands = [(node, port) for (svc, node, port, _d) in rows if svc == service_id]
    if not cands:
        raise SystemExit(f"service {service_id} not published in qrtr-lookup")
    return cands[0]


def tlv(t, value):
    return struct.pack("<BH", t, len(value)) + value


def tlv_u8(t, v):
    return tlv(t, struct.pack("<B", v))


def tlv_u16(t, v):
    return tlv(t, struct.pack("<H", v))


def tlv_u32(t, v):
    return tlv(t, struct.pack("<I", v))


def tlv_str(t, s):
    return tlv(t, s.encode())


def tlv_seq_u32_u32(t, a, b):
    return tlv(t, struct.pack("<II", a, b))


def tlv_seq_u8_u8(t, a, b):
    return tlv(t, struct.pack("<BB", a, b))


def parse_tlvs(buf, off=7):
    out = []
    while off + 3 <= len(buf):
        t = buf[off]
        length = struct.unpack("<H", buf[off + 1:off + 3])[0]
        out.append((t, buf[off + 3:off + 3 + length]))
        off += 3 + length
    return out


class Client:
    def __init__(self, node, port, timeout=8.0):
        self.node = node
        self.port = port
        self.timeout = timeout
        self.sock = socket.socket(AF_QIPCRTR, socket.SOCK_DGRAM)
        self.sock.settimeout(timeout)
        self.txn = 0
        addr = struct.pack("<H2xII", AF_QIPCRTR, LOCAL_NODE, 0)
        rc = _libc.bind(self.sock.fileno(), addr, len(addr))
        # Autobind in sendmsg would also work; a failure here is not fatal.

    def close(self):
        self.sock.close()

    def _send(self, msgid, tlvs=b""):
        self.txn = (self.txn + 1) & 0xFFFF
        payload = struct.pack("<BHHH", 0x00, self.txn, msgid, len(tlvs)) + tlvs
        addr = struct.pack("<H2xII", AF_QIPCRTR, self.node, self.port)
        n = _libc.sendto(self.sock.fileno(), payload, len(payload), 0, addr, len(addr))
        if n < 0:
            raise SystemExit(f"sendto failed: errno {ctypes.get_errno()}")
        return self.txn

    def request(self, msgid, tlvs=b""):
        txn = self._send(msgid, tlvs)
        deadline_sock = self.sock
        while True:
            try:
                data, _peer = deadline_sock.recvfrom(65536)
            except socket.timeout:
                raise SystemExit(f"timeout waiting for msgid 0x{msgid:04x} (txn {txn})")
            if len(data) < 7:
                continue
            mtype = data[0]
            if mtype != 0x02:
                continue                      # indication; ignore
            rtxn = struct.unpack("<H", data[1:3])[0]
            rmsg = struct.unpack("<H", data[3:5])[0]
            if rtxn != txn or rmsg != msgid:
                continue
            return data

    def result(self, data):
        for t, v in parse_tlvs(data):
            if t == 0x02 and len(v) >= 4:
                return struct.unpack("<HH", v[:4])
        return (None, None)


def dump(data, skip_result=True):
    for t, v in parse_tlvs(data):
        if skip_result and t == 0x02:
            continue
        printable = v.rstrip(b"\x00").decode("utf-8", "replace")
        shown = printable if all(32 <= ord(c) < 127 for c in printable) and printable else v.hex()
        print(f"  tlv 0x{t:02x} len={len(v)} {shown}")


def cmd_dms_manufacturer(args, rows):
    node, port = find_service(rows, SVC_DMS)
    c = Client(node, port, args.timeout)
    data = c.request(MSG_DMS_GET_MANUFACTURER)
    res, err = c.result(data)
    print(f"DMS Get Manufacturer: result={res} error={err}")
    for t, v in parse_tlvs(data):
        if t == 0x01:
            print(f"  manufacturer: {v.rstrip(b'\x00').decode('utf-8', 'replace')}")
    c.close()


def cmd_bind(args, rows):
    node, port = find_service(rows, SVC_WDS)
    c = Client(node, port, args.timeout)
    tlvs = tlv_seq_u32_u32(0x10, EP_TYPE[args.ep], args.iface) + tlv_u8(0x11, args.mux)
    data = c.request(MSG_WDS_BIND_MUX_DATA_PORT, tlvs)
    res, err = c.result(data)
    print(f"WDS Bind Mux Data Port (ep={args.ep}/{args.iface}, mux={args.mux}): result={res} error={err} ({err_name(err)})")
    dump(data)
    c.close()
    return res


def cmd_status(args, rows):
    node, port = find_service(rows, SVC_WDS)
    c = Client(node, port, args.timeout)
    data = c.request(MSG_WDS_GET_PACKET_SERVICE_STATUS)
    res, err = c.result(data)
    state = CONN_NAMES.get(packet_state_from(data), "no status TLV")
    print(f"WDS Packet Service Status: result={res} error={err} ({err_name(err)}) state={state}")
    c.close()


def cmd_start(args, rows):
    node, port = find_service(rows, SVC_WDS)
    c = Client(node, port, args.timeout)
    tlvs = (tlv_u8(0x31, args.profile) if args.profile else tlv_str(0x14, args.apn)) + tlv_u8(0x19, IP_FAMILY[args.ip])
    if args.auth_user or args.auth_pass:
        tlvs += tlv_u8(0x16, 0x03 if args.auth == "both" else (1 if args.auth == "pap" else 2))
        tlvs += tlv_str(0x17, args.auth_user) + tlv_str(0x18, args.auth_pass)
    data = c.request(MSG_WDS_START_NETWORK, tlvs)
    res, err = c.result(data)
    what = f"profile={args.profile}" if args.profile else f"apn={args.apn}"
    print(f"WDS Start Network ({what}, ip={args.ip}): result={res} error={err} ({err_name(err)})")
    explain_start(data)
    dump(data)
    c.close()


def cmd_settings(args, rows):
    node, port = find_service(rows, SVC_WDS)
    c = Client(node, port, args.timeout)
    data = c.request(MSG_WDS_GET_CURRENT_SETTINGS, tlv_u32(0x10, REQ_SETTINGS_ALL))
    res, err = c.result(data)
    print(f"WDS Get Current Settings: result={res} error={err} ({err_name(err)})")
    dump(data)
    c.close()


MESSAGES_DB = None


def messages_db():
    """Message id -> name per service, generated from libqmi data/*.json.

    Lets a "Get Supported Messages" bitmap be read as names, and lets us ask
    whether this firmware supports the messages a feature needs (QMAP needs
    WDA Set Data Format; QMAP multiplexing needs WDS Bind Mux Data Port).
    """
    global MESSAGES_DB
    if MESSAGES_DB is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qmi-messages.json")
        try:
            with open(path, encoding="utf-8") as fh:
                MESSAGES_DB = json.load(fh)
        except (OSError, ValueError):
            MESSAGES_DB = {}
    return MESSAGES_DB


def msg_name(service, msgid):
    db = messages_db().get(service) or {}
    return db.get(str(msgid), f"msg-0x{msgid:04x}")


def supported_ids(data, tlv_id=0x10):
    """Decode a Get Supported Messages bitmap (2-byte size prefix, then bits).

    Bit N of the payload means message id N, LSB first.
    """
    for t, v in parse_tlvs(data):
        if t != tlv_id or len(v) < 2:
            continue
        count = struct.unpack("<H", v[:2])[0]
        out = set()
        for i, byte in enumerate(v[2:2 + count]):
            for bit in range(8):
                if byte & (1 << bit):
                    out.add(i * 8 + bit)
        return out
    return set()


def cmd_supported(args, rows):
    service = {"wds": SVC_WDS, "dms": SVC_DMS, "wda": SVC_WDA}[args.service]
    node, port = find_service(rows, service)
    c = Client(node, port, args.timeout)
    data = c.request(MSG_GET_SUPPORTED_MESSAGES)
    res, err = c.result(data)
    ids = supported_ids(data)
    print(f"{args.service.upper()} Get Supported Messages: result={res} error={err} ({err_name(err)}) count={len(ids)}")
    if err == 71:
        print("  (this firmware does not implement Get Supported Messages: feature gating stays empirical)")
    if not ids:
        c.close()
        return
    for mid in sorted(ids):
        print(f"  0x{mid:04x} {msg_name(args.service.upper(), mid)}")
    c.close()


def cmd_format(args, rows):
    """Read WDA data format: raw-IP vs QMAP, and whether QMAP can be enabled.

    With no kernel QMAP support, raw-IP is the only format that can work, so
    this is the check that says whether our unframed mhi_net path is the one
    the firmware is actually configured for.
    """
    node, port = find_service(rows, SVC_WDA)
    c = Client(node, port, args.timeout)
    print("WDA data format (read-only)")
    # WDA Get Data Format is per-endpoint: without Endpoint Info this box
    # answers error 48 InvalidArgument.  Note the asymmetry in libqmi: Get
    # takes Endpoint Info as TLV 0x10, while Set takes the same sequence as
    # TLV 0x17.  Payload is two u32 (QmiDataEndpointType + interface number).
    req = tlv_seq_u32_u32(0x10, EP_TYPE[args.ep], args.iface)
    data = c.request(MSG_WDA_GET_DATA_FORMAT, req)
    res, err = c.result(data)
    print(f"  endpoint        : {args.ep}/{args.iface} (tlv 0x10)")
    print(f"  get-data-format : result={res} error={err} ({err_name(err)})")
    got = {}
    for t, v in parse_tlvs(data):
        if t == 0x10 and len(v) >= 1:
            got["qos"] = "on" if v[0] else "off"
        elif t in (0x11, 0x12, 0x13) and len(v) >= 4:
            n = struct.unpack("<I", v[:4])[0]
            key = {0x11: "link-layer", 0x12: "ul-aggregation", 0x13: "dl-aggregation"}[t]
            got[key] = (LINK_LAYER if t == 0x11 else AGGREGATION).get(n, f"id-{n}")
        elif t == 0x1B and len(v) >= 1:
            # libqmi "Get Data Format" output: Flow Control is guint8, not
            # guint32.  Parsing it as u32 meant a 1-byte TLV was dropped and
            # we could not see whether the firmware has flow control enabled.
            got["flow-control"] = v[0]
        elif t in (0x14, 0x15, 0x16, 0x17, 0x18, 0x1A) and len(v) >= 4:
            n = struct.unpack("<I", v[:4])[0]
            key = {0x14: "ndp-signature", 0x15: "dl-max-datagrams", 0x16: "dl-max-size",
                   0x17: "ul-max-datagrams", 0x18: "ul-max-size", 0x1A: "dl-min-padding"}[t]
            got[key] = f"0x{n:08x}" if t == 0x14 else n
    for key in ("link-layer", "qos", "ul-aggregation", "dl-aggregation",
                "dl-max-datagrams", "dl-max-size", "ul-max-datagrams", "ul-max-size",
                "ndp-signature", "flow-control"):
        if key in got:
            val = got[key]
            if key == "flow-control" and val in (0, 1):
                val = f"{val} ({'enabled' if val else 'disabled'})"
            print(f"  {key:<16}: {val}")
    llp = got.get("link-layer")
    if llp == "raw-ip" and got.get("dl-aggregation") in ("disabled", None):
        print("  => raw-IP and no aggregation: matches the unframed mhi_net path")
    elif llp:
        print(f"  => link-layer is {llp}: the kernel side must match this (raw-IP expected)")
    sdata = c.request(MSG_GET_SUPPORTED_MESSAGES)
    sres, serr = c.result(sdata)
    ids = supported_ids(sdata)
    if ids:
        can_set = 0x0020 in ids
        print(f"  WDA messages   : {len(ids)} supported, Set Data Format (0x0020): "
              f"{'yes' if can_set else 'NO'} -> QMAP switch is "
              f"{'possible from the host' if can_set else 'not possible from the host'}")
    c.close()


def wda_format(c, ep, iface):
    """Read WDA Get Data Format into raw values (no name mapping)."""
    data = c.request(MSG_WDA_GET_DATA_FORMAT, tlv_seq_u32_u32(0x10, EP_TYPE[ep], iface))
    res, err = c.result(data)
    raw = {}
    for t, v in parse_tlvs(data):
        if t == 0x10 and len(v) >= 1:
            raw["qos"] = v[0]
        elif t in FORMAT_TLV and len(v) >= 4:
            raw[FORMAT_TLV[t]] = struct.unpack("<I", v[:4])[0]
    # the modem omits TLVs it considers default; Set needs every field present
    for key in ("qos", "llp", "ul", "dl", "ndp", "dl_max_dgram", "dl_max_size",
                "ul_max_dgram", "ul_max_size"):
        raw.setdefault(key, 0)
    return raw, res, err


def cmd_format_set(args, rows):
    """Write WDA Set Data Format (0x0020).

    Default is a dry run, and with no explicit changes it mirrors whatever Get
    returns -- that is a safe way to prove the Set path works without altering
    the data format the modem is currently using.
    """
    node, port = find_service(rows, SVC_WDA)
    c = Client(node, port, args.timeout)
    before, res, err = wda_format(c, args.ep, args.iface)
    print(f"WDA set data format (endpoint {args.ep}/{args.iface})")
    print(f"  current        : result={res} error={err} ({err_name(err)})")
    if err:
        c.close()
        sys.exit("cannot read the current format; refusing to write")

    plan = dict(before)
    if args.link_layer:
        plan["llp"] = LL_ID[args.link_layer]
    if args.ul:
        plan["ul"] = AGG_ID[args.ul]
    if args.dl:
        plan["dl"] = AGG_ID[args.dl]
    for key, value in (("dl_max_dgram", args.dl_max_datagrams),
                       ("dl_max_size", args.dl_max_size),
                       ("ul_max_dgram", args.ul_max_datagrams),
                       ("ul_max_size", args.ul_max_size),
                       ("ndp", args.ndp), ("qos", args.qos)):
        if value is not None:
            plan[key] = value
    for key in ("llp", "ul", "dl", "qos"):
        plan.setdefault(key, 2 if key == "llp" else 0)
    for key in ("ndp", "dl_max_dgram", "dl_max_size", "ul_max_dgram", "ul_max_size"):
        plan.setdefault(key, 0)

    def show(label, vals):
        print(f"  {label:<15}: link-layer={LINK_LAYER.get(vals['llp'], vals['llp'])}"
              f" qos={'on' if vals['qos'] else 'off'}"
              f" ul={AGGREGATION.get(vals['ul'], vals['ul'])}"
              f" dl={AGGREGATION.get(vals['dl'], vals['dl'])}"
              f" ndp=0x{vals['ndp']:08x}"
              f" dl-max={vals['dl_max_dgram']}/{vals['dl_max_size']}"
              f" ul-max={vals['ul_max_dgram']}/{vals['ul_max_size']}")

    show("current", before)
    show("planned", plan)
    if not args.apply:
        print("  dry run        : pass --apply to send Set Data Format")
        c.close()
        return

    tlvs = (tlv_u8(0x10, plan["qos"]) + tlv_u32(0x11, plan["llp"]) +
            tlv_u32(0x12, plan["ul"]) + tlv_u32(0x13, plan["dl"]) +
            tlv_u32(0x14, plan["ndp"]) + tlv_u32(0x15, plan["dl_max_dgram"]) +
            tlv_u32(0x16, plan["dl_max_size"]) +
            tlv_seq_u32_u32(0x17, EP_TYPE[args.ep], args.iface) +
            tlv_u32(0x1B, plan["ul_max_dgram"]) + tlv_u32(0x1C, plan["ul_max_size"]))
    data = c.request(MSG_WDA_SET_DATA_FORMAT, tlvs)
    res2, err2 = c.result(data)
    print(f"  set            : result={res2} error={err2} ({err_name(err2)})")
    after, res3, err3 = wda_format(c, args.ep, args.iface)
    if err3:
        print(f"  read back      : failed result={res3} error={err3} ({err_name(err3)})")
    else:
        diff = {k: (before.get(k), after.get(k)) for k in set(before) | set(after)
                if before.get(k) != after.get(k)}
        if diff:
            print(f"  read back      : CHANGED {diff}")
        else:
            print("  read back      : identical to what was there before")
    c.close()


def cmd_profile(args, rows):
    """Read the 3GPP profile store: which APN each profile index really carries.

    The profile *names* from the profile list ("fota,ia", "CMNET", ...) do not
    say what APN the firmware will dial, and the LTE attach PDN list points at
    profile indices - so this is where that indirection is resolved.
    """
    node, port = find_service(rows, SVC_WDS)
    c = Client(node, port, args.timeout)
    wanted = []
    for chunk in args.index.split(","):
        chunk = chunk.strip()
        if chunk:
            wanted.append(int(chunk))
    for idx in wanted:
        data = c.request(MSG_WDS_GET_PROFILE_SETTINGS,
                         tlv_seq_u8_u8(0x01, PROFILE_TYPE["3gpp"], idx))
        res, err = c.result(data)
        if err:
            print(f"profile {idx:<2}: result={res} error={err} ({err_name(err)})")
            continue
        s = parse_settings(data)
        parts = []
        if "profile_name" in s:
            parts.append(f"name={s['profile_name']!r}")
        if "apn" in s:
            parts.append(f"apn={s['apn']!r}")
        if "pdp_type" in s:
            parts.append(f"pdp={s['pdp_type']}")
        if "ip_family" in s:
            parts.append(f"family={s['ip_family']}")
        if "dns1_str" in s:
            parts.append(f"dns1={s['dns1_str']}")
        if "dns2_str" in s:
            parts.append(f"dns2={s['dns2_str']}")
        body = " ".join(parts) if parts else "(no known TLVs)"
        print(f"profile {idx:<2}: result={res} error={err} ({err_name(err)}) {body}")
    c.close()


ATTACH_ACTION = {1: "none", 2: "detach-or-pdn-disconnect"}


def attach_lists(c):
    """Read the LTE attach PDN list (current, pending) from WDS 0x0094."""
    data = c.request(MSG_WDS_GET_LTE_ATTACH_PDN_LIST)
    res, err = c.result(data)
    lists = {}
    for t, v in parse_tlvs(data):
        if t in (0x10, 0x11) and len(v) >= 1:
            n = min(v[0], (len(v) - 1) // 2)
            lists[t] = [struct.unpack("<H", v[1 + 2 * i:3 + 2 * i])[0] for i in range(n)]
    return lists.get(0x10, []), lists.get(0x11, []), res, err


def fmt_ids(ids):
    return "[" + ", ".join(str(i) for i in ids) + "]"


def attach_array(tlv, ids):
    """TLV array with a guint8 size prefix, elements guint16 (see the IDL)."""
    body = bytes([len(ids)]) + b"".join(struct.pack("<H", i) for i in ids)
    return struct.pack("<BH", tlv, len(body)) + body


def cmd_attach_set(args, rows):
    """Write the LTE attach PDN list (WDS 0x0093).

    The list decides which profiles the modem attaches with.  This box reports
    [1] while profile 1 has an empty APN, so on a live network the default
    bearer may not come up; the lever is setting the list to [3] (CMNET).
    Default is a dry run, and with no --list it writes back what is already
    there, which proves the write path without changing anything.
    """
    node, port = find_service(rows, SVC_WDS)
    c = Client(node, port, args.timeout)
    cur, pend, res, err = attach_lists(c)
    print(f"WDS set LTE attach PDN list")
    print(f"  current       : result={res} error={err} ({err_name(err)}) current={fmt_ids(cur)} pending={fmt_ids(pend)}")
    if err:
        c.close()
        sys.exit("cannot read the current list; refusing to write")

    target = cur
    if args.list:
        target = [int(x) for x in args.list.split(",") if x.strip()]
    action = args.action if args.action is not None else 1
    print(f"  planned       : {fmt_ids(target)} action={action} ({ATTACH_ACTION.get(action, '?')})")
    if not args.apply:
        print("  dry run       : pass --apply to send Set LTE Attach PDN List")
        c.close()
        return

    tlvs = attach_array(0x01, target) + tlv_u32(0x10, action)
    data = c.request(MSG_WDS_SET_LTE_ATTACH_PDN_LIST, tlvs)
    res2, err2 = c.result(data)
    print(f"  set           : result={res2} error={err2} ({err_name(err2)})")
    after_cur, after_pend, res3, err3 = attach_lists(c)
    if err3:
        print(f"  read back     : failed result={res3} error={err3} ({err_name(err3)})")
    else:
        print(f"  read back     : current={fmt_ids(after_cur)} pending={fmt_ids(after_pend)}"
              + ("  (matches plan)" if after_cur == target else "  (plan NOT applied yet)"))
    c.close()


def cmd_params(args, rows):
    """Read-only: what the modem would attach with, and how fast it can go.

    All five answers are configuration, not call state, so they work with no
    SIM in the slot - which makes this the last piece of the data path that can
    be checked before the card arrives.
    """
    node, port = find_service(rows, SVC_WDS)
    c = Client(node, port, args.timeout)

    def ask(msgid, tlvs=b""):
        data = c.request(msgid, tlvs)
        res, err = c.result(data)
        return res, err, data

    def tag(res, err):
        return f"result={res} error={err} ({err_name(err)})"

    print("WDS parameters (read-only)")

    res, err, data = ask(MSG_WDS_GET_DEFAULT_PROFILE_NUMBER,
                         tlv_seq_u8_u8(0x01, PROFILE_TYPE["3gpp"], PROFILE_FAMILY["embedded"]))
    idx = None
    for t, v in parse_tlvs(data):
        if t == 0x01 and len(v) >= 1:
            idx = v[0]
    print(f"  default-profile : {tag(res, err)}" +
          (f" 3gpp/embedded -> {idx}" if idx is not None else " (no index TLV)"))

    res, err, data = ask(MSG_WDS_GET_MAX_LTE_ATTACH_PDN_NUMBER)
    mx = None
    for t, v in parse_tlvs(data):
        if t == 0x10 and len(v) >= 1:
            mx = v[0]
    print(f"  max-attach-pdns : {tag(res, err)}" + (f" {mx}" if mx is not None else " (no TLV)"))

    res, err, data = ask(MSG_WDS_GET_LTE_ATTACH_PARAMETERS)
    apn = support = ota = None
    for t, v in parse_tlvs(data):
        if t == 0x10:
            apn = v.rstrip(b"\x00").decode("utf-8", "replace")
        elif t == 0x11 and len(v) >= 1:
            support = IP_SUPPORT.get(v[0], f"type-{v[0]}")
        elif t == 0x12 and len(v) >= 1:
            ota = "yes" if v[0] else "no"
    print(f"  attach-params   : {tag(res, err)}" +
          (f" apn={apn!r} ip={support} ota-attach={ota}" if (apn or support) else " (no TLVs)"))

    res, err, data = ask(MSG_WDS_GET_LTE_ATTACH_PDN_LIST)
    lists = {}
    for t, v in parse_tlvs(data):
        if t in (0x10, 0x11) and len(v) >= 1:
            n = min(v[0], (len(v) - 1) // 2)
            lists[t] = [struct.unpack("<H", v[1 + 2 * i:3 + 2 * i])[0] for i in range(n)]

    def fmt(ids):
        return "[" + ", ".join(str(i) for i in ids) + "]"

    print(f"  attach-pdn-list : {tag(res, err)}" +
          (f" current={fmt(lists.get(0x10, []))} pending={fmt(lists.get(0x11, []))}"
           if lists else " (no list TLVs)"))

    res, err, data = ask(MSG_WDS_GET_CHANNEL_RATES)
    rates = None
    for t, v in parse_tlvs(data):
        if t == 0x01 and len(v) >= 16:
            rates = struct.unpack("<IIII", v[:16])
    if rates:
        tx, rx, mtx, mrx = rates
        print(f"  channel-rates   : {tag(res, err)} "
              f"tx={tx / 1e6:.1f}Mbps rx={rx / 1e6:.1f}Mbps "
              f"max_tx={mtx / 1e6:.1f}Mbps max_rx={mrx / 1e6:.1f}Mbps")
    else:
        print(f"  channel-rates   : {tag(res, err)} (no sequence TLV)")
    c.close()


def bind_subscription(c, stype):
    """WDS Bind Subscription (0x00AF): TLV 0x01 = QmiSubscriptionType as u32.

    Unsupported just means the firmware does not care, so that is not fatal;
    any other error is worth stopping for, because Start Network would only
    fail later with the same cause.
    """
    data = c.request(MSG_WDS_BIND_SUBSCRIPTION, tlv_u32(0x01, SUBSCRIPTION_TYPE[stype]))
    res, err = c.result(data)
    print(f"subscr : result={res} error={err} ({err_name(err)}) type={stype}")
    return err


def cmd_subscription(args, rows):
    node, port = find_service(rows, SVC_WDS)
    c = Client(node, port, args.timeout)
    if args.type != "none":
        bind_subscription(c, args.type)
    data = c.request(MSG_WDS_GET_BIND_SUBSCRIPTION)
    res, err = c.result(data)
    bound = None
    for t, v in parse_tlvs(data):
        if t == 0x10 and len(v) >= 4:
            bound = struct.unpack("<I", v[:4])[0]
    if bound is None:
        shown = "no TLV"
    else:
        shown = SUBSCRIPTION_NAME.get(bound, f"id-{bound}")
    print(f"get    : result={res} error={err} ({err_name(err)}) bound={shown}")
    c.close()


def cmd_up(args, rows):
    """bind + start + settings on one client, the way ModemManager does it."""
    node, port = find_service(rows, SVC_WDS)
    c = Client(node, port, args.timeout)
    if args.bind_subscription != "off":
        sub_err = bind_subscription(c, args.bind_subscription)
        if sub_err and sub_err not in (3, 94):
            c.close()
            raise SystemExit(2)
    tlvs = tlv_seq_u32_u32(0x10, EP_TYPE[args.ep], args.iface) + tlv_u8(0x11, args.mux)
    data = c.request(MSG_WDS_BIND_MUX_DATA_PORT, tlvs)
    res, err = c.result(data)
    print(f"bind   : result={res} error={err} ({err_name(err)})")
    if err:
        c.close()
        raise SystemExit(2)
    tlvs = (tlv_u8(0x31, args.profile) if args.profile else tlv_str(0x14, args.apn)) + tlv_u8(0x19, IP_FAMILY[args.ip])
    data = c.request(MSG_WDS_START_NETWORK, tlvs)
    res, err = c.result(data)
    what = f"profile={args.profile}" if args.profile else f"apn={args.apn}"
    print(f"start  : result={res} error={err} ({err_name(err)}) {what}")
    if err:
        explain_start(data)
    handle = None
    for t, v in parse_tlvs(data):
        if t == 0x01 and len(v) >= 4:
            handle = struct.unpack("<I", v[:4])[0]
    print(f"handle : {handle}")
    if err:
        c.close()
        raise SystemExit(3)
    data = c.request(MSG_WDS_GET_CURRENT_SETTINGS, tlv_u32(0x10, REQ_SETTINGS_ALL))
    res, err = c.result(data)
    print(f"settings: result={res} error={err} ({err_name(err)})")
    dump(data)
    c.close()


def fake_bearer(args):
    """Hold a SYNTHETIC bearer: no QMI at all.

    A card-less box can never get past WDS Start (error 14 / cm acl-failure),
    so the configure -> hold -> SIGTERM -> cleanup path of a real bearer is
    never reached before SIM day.  This mode installs a made-up address on
    the same interface and runs exactly that path, so the process lifetime,
    the signal handler and the cleanup are proven ahead of time.
    """
    ip, prefix, gw, mtu = "10.99.13.2", 30, "10.99.13.1", 1500
    v6 = None
    print("fake    : synthetic bearer - no QMI was sent")
    print(f"  ip_str: {ip}  gw_str: {gw}  prefix: {prefix}  mtu: {mtu}")
    if getattr(args, "dry_run", False):
        # A dry run that installs a default route is not a dry run.  This was
        # measured for real: --fake --dry-run --route configured the interface
        # (round 54).
        for argv in (route_cmds(args.ifname, gw, args.route_metric, v6)
                     if args.route else []):
            print("route   : would run " + " ".join(argv))
        print(f"dry-run : would configure {args.ifname} {ip}/{prefix} gw={gw} mtu={mtu}"
              + ("  + default route metric=%d" % args.route_metric
                 if args.route and gw else "  (no default route)"))
        print("dry-run : nothing was touched (no QMI, no interface change)")
        return

    stop = {"flag": False}

    def _sig(_s, _f):
        stop["flag"] = True

    signal.signal(signal.SIGPIPE, signal.SIG_IGN)  # closed pipe -> exception, not death
    signal.signal(signal.SIGTERM, _sig)
    orig_mtu = iface_mtu(args.ifname)
    configure_iface(args.ifname, ip, prefix, gw, mtu, args.route, v6,
                             args.route_metric)
    print(f"configured: {args.ifname} {ip}/{prefix} gw={gw} mtu={mtu}"
          + ("  default-route=via-gw metric=%d" % args.route_metric if args.route and gw
                 else "  default-route=untouched"))
    started = time.time()
    try:
        while not stop["flag"]:
            if args.hold_seconds and time.time() - started >= args.hold_seconds:
                break
            time.sleep(1)
            print(f"  holding: {int(time.time() - started)}s  {args.ifname} {ip}/{prefix}", flush=True)
    finally:
        cleanup_iface(args.ifname, ip, prefix, gw, args.route, v6, orig_mtu)
        print("stop    : skipped (fake bearer, nothing was called)")


def cmd_data_up(args, rows):
    """bind -> start -> settings -> configure the raw-IP interface, then hold.

    The QMI client lives in this process, and over QRTR the client is the
    socket, so the call is torn down when the script exits.  That is why this
    command holds the call (printing a periodic status line) until SIGINT or
    --hold-seconds elapses, then stops the call and removes what it configured.
    """
    if getattr(args, "fake", False):
        return fake_bearer(args)

    node, port = find_service(rows, SVC_WDS)
    c = Client(node, port, args.timeout)

    def die(code, msg):
        print(f"FAIL: {msg}")
        c.close()
        raise SystemExit(code)

    if args.bind_subscription != "off":
        sub_err = bind_subscription(c, args.bind_subscription)
        if sub_err and sub_err not in (3, 94):
            die(2, "bind subscription failed")
    tlvs = tlv_seq_u32_u32(0x10, EP_TYPE[args.ep], args.iface) + tlv_u8(0x11, args.mux)
    data = c.request(MSG_WDS_BIND_MUX_DATA_PORT, tlvs)
    res, err = c.result(data)
    print(f"bind    : result={res} error={err} ({err_name(err)})")
    if err:
        die(2, "bind failed")
    what = f"profile={args.profile}" if args.profile else f"apn={args.apn}"
    tlvs = (tlv_u8(0x31, args.profile) if args.profile else tlv_str(0x14, args.apn)) + tlv_u8(0x19, IP_FAMILY[args.ip])
    data = c.request(MSG_WDS_START_NETWORK, tlvs)
    res, err = c.result(data)
    print(f"start   : result={res} error={err} ({err_name(err)}) {what}")
    if err:
        explain_start(data)
        die(3, "start failed")
    handle = 0
    for t, v in parse_tlvs(data):
        if t == 0x01 and len(v) >= 4:
            handle = struct.unpack("<I", v[:4])[0]
    print(f"handle  : {handle}")

    st = wait_connected(c, args.connect_timeout)
    print(f"packet  : {CONN_NAMES.get(st, 'no-response')}" +
          ("" if st == 2 else f" (waited {args.connect_timeout}s)"))
    if st == 4:
        # authenticating: the call is accepted, settings just need a moment
        time.sleep(2)

    data = None
    for attempt in range(1, max(1, args.settings_retries) + 1):
        data = c.request(MSG_WDS_GET_CURRENT_SETTINGS, tlv_u32(0x10, REQ_SETTINGS_ALL))
        res, err = c.result(data)
        print(f"settings: result={res} error={err} ({err_name(err)})" +
              ("" if attempt == 1 else f" [attempt {attempt}]"))
        if not err:
            break
        if attempt < args.settings_retries and err in (5, 15, 46):
            time.sleep(2)
            continue
        c.request(MSG_WDS_STOP_NETWORK, tlv_u32(0x01, handle))
        die(4, "settings failed")
    s = parse_settings(data)
    for k, v in s.items():
        if not k.endswith("_str"):
            print(f"  {k}: {v}")

    mtu = s.get("mtu") or args.mtu   # mhi_net defaults to a 16 KiB MTU; 1500 is what an LTE bearer expects
    ip = s.get("ip_str"); gw = s.get("gw_str"); prefix = s.get("prefix")
    # IPv6 is optional: profile 3 is "ipv4-or-ipv6", and a v6-only bearer is a
    # perfectly good call - failing it as "no IPv4 address" would be wrong.
    v6 = None
    if s.get("ipv6_str") and s.get("ipv6_prefix"):
        v6 = (s["ipv6_str"], s["ipv6_prefix"], s.get("gw6_str"))
        print(f"ipv6    : {v6[0]}/{v6[1]} gw={v6[2]} dns={s.get('dns1_6_str')}")
    if (not ip or not prefix) and not v6:
        c.request(MSG_WDS_STOP_NETWORK, tlv_u32(0x01, handle))
        family = s.get("ip_family")
        die(5, "settings returned neither an IPv4 nor an IPv6 address"
               + (f" (ip_family={family})" if family else ""))
    if not ip:
        print("note    : bearer is IPv6-only; IPv4 targets will not work")
    if args.dry_run:
        what = f"{ip}/{prefix}" if ip else "no-ipv4"
        if v6:
            what += f" + {v6[0]}/{v6[1]}"
        print(f"dry-run: would configure {args.ifname} {what} gw={gw} mtu={mtu}"
              + ("  + default route metric=%d" % args.route_metric
                 if args.route and (gw or (v6 and v6[2])) else "  (no default route)"))
        c.close()
        return

    # Install the handlers BEFORE touching the interface: from here on a
    # SIGTERM (systemd stop, Ctrl-C) must reach the cleanup path below rather
    # than killing us with an address left on the bearer.
    stop = {"flag": False}
    def _sig(_s, _f):
        stop["flag"] = True
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)  # closed pipe -> exception, not death
    signal.signal(signal.SIGTERM, _sig)

    configure_iface(args.ifname, ip, prefix, gw, mtu, args.route, v6,
                             args.route_metric)
    desc = f"{ip}/{prefix}" if ip else "(no ipv4)"
    if v6:
        desc += f" + {v6[0]}/{v6[1]}"
    print(f"configured: {args.ifname} {desc} gw={gw} mtu={mtu}"
          + ("  default-route=via-gw" if args.route and gw else "  default-route=untouched"))

    started = time.time()
    try:
        while not stop["flag"]:
            if args.hold_seconds and time.time() - started >= args.hold_seconds:
                break
            time.sleep(5)
            print(f"  holding: {int(time.time() - started)}s  {args.ifname} {desc}", flush=True)
    finally:
        cleanup_iface(args.ifname, ip, prefix, gw, args.route, v6, orig_mtu)
        data = c.request(MSG_WDS_STOP_NETWORK, tlv_u32(0x01, handle))
        res, err = c.result(data)
        print(f"stop    : result={res} error={err} ({err_name(err)})")
        c.close()


def cmd_data_down(args, rows):
    node, port = find_service(rows, SVC_WDS)
    c = Client(node, port, args.timeout)
    data = c.request(MSG_WDS_STOP_NETWORK, tlv_u32(0x01, args.handle))
    res, err = c.result(data)
    print(f"WDS Stop Network (handle {args.handle}): result={res} error={err} ({err_name(err)})")
    c.close()


def cmd_selftest(args):
    """Offline-ish check: TLV parsing, prefix math and interface plumbing.

    Creates a dummy interface, configures it exactly like a real data call
    would, verifies the result, then removes it.  Needs root, touches nothing
    else.
    """
    def ip6(addr):
        return socket.inet_pton(socket.AF_INET6, addr)

    blob = (
        tlv(0x10, b"selftest") +
        tlv_u8(0x11, 3) +                # PDP type ipv4-or-ipv6 (what profile 3 says)
        tlv_str(0x14, "selftest") +
        tlv_u32(0x15, 0x01010101) +      # 1.1.1.1
        tlv_u32(0x16, 0x08080808) +      # 8.8.8.8
        tlv_u32(0x1E, 0x0201630A) +      # 10.99.1.2
        tlv_u32(0x20, 0x0101630A) +      # 10.99.1.1
        tlv_u32(0x21, 0xFFFFFFFC) +      # 255.255.255.252 -> /30
        tlv_u32(0x29, 1430) +            # carrier MTU, not the 1500 default
        tlv(0x25, ip6("2001:db8::2") + bytes([64])) +   # IPv6 addr + prefix
        tlv(0x26, ip6("fe80::1") + bytes([64])) +       # IPv6 gateway
        tlv(0x27, ip6("2001:4860:4860::8888")) +        # IPv6 primary DNS
        tlv_u8(0x2B, 4)                  # IP family ipv4
    )
    frame = struct.pack("<BHHH", 0x02, 1, MSG_WDS_GET_CURRENT_SETTINGS, len(blob)) + blob
    s = parse_settings(frame)
    ok = True
    checks = [
        ("ip_str", "10.99.1.2"), ("gw_str", "10.99.1.1"),
        ("dns1_str", "1.1.1.1"), ("dns2_str", "8.8.8.8"),
        ("prefix", 30), ("mtu", 1430),
        ("pdp_type", "ipv4-or-ipv6"), ("ip_family", "ipv4"),
        ("ipv6_str", "2001:db8::2"), ("ipv6_prefix", 64),
        ("gw6_str", "fe80::1"), ("dns1_6_str", "2001:4860:4860::8888"),
    ]
    for k, want in checks:
        got = s.get(k)
        good = got == want
        ok = ok and good
        print(f"  parse {k}: {got!r} {'ok' if good else 'MISMATCH want ' + repr(want)}")
    # v6-only bearer: same call, no IPv4 TLVs at all.  data-up must treat this
    # as a usable bearer rather than "settings did not return an IPv4 address".
    v6blob = (tlv(0x10, b"selftest") + tlv_u8(0x11, 2) +
              tlv(0x25, ip6("2001:db8::9") + bytes([64])) +
              tlv_u8(0x2B, 6))
    v6frame = struct.pack("<BHHH", 0x02, 3, MSG_WDS_GET_CURRENT_SETTINGS, len(v6blob)) + v6blob
    s6 = parse_settings(v6frame)
    for label, got, want in (("v6-only ipv6_str", s6.get("ipv6_str"), "2001:db8::9"),
                             ("v6-only has no ipv4", "ip_str" in s6, False),
                             ("v6-only ip_family", s6.get("ip_family"), "ipv6")):
        good = got == want
        ok = ok and good
        print(f"  parse {label}: {got!r} {'ok' if good else 'MISMATCH want ' + repr(want)}")

    # The call-end tables are a data file; if it is missing the only diagnostic
    # the modem gives us on a failed call degrades back to bare numbers.
    db_checks = [
        ("verbose_type[3]", reason_db().get("verbose_type", {}).get("3"), "cm"),
        ("cm[1042]", reason_db().get("verbose", {}).get("cm", {}).get("1042"), "acl-failure"),
        ("generic[3]", reason_db().get("call_end", {}).get("generic", {}).get("3"), "no-service"),
        ("call_end_name(3)", call_end_name(3).split(" ")[0], "no-service"),
    ]
    for label, got, want in db_checks:
        good = got == want
        ok = ok and good
        print(f"  reasons {label}: {got!r} {'ok' if good else 'MISMATCH want ' + repr(want)}")
    # Default routes: the argv we build must carry a metric, because a
    # metric-less default route ties with the server uplink (both 0) and the
    # kernel then picks by FIB order.  Pure, so no interface is touched.
    rc = route_cmds("mhi_hwip0", "10.99.1.1", 1000)
    rc6 = route_cmds("mhi_hwip0", None, 1000, v6=("2001:db8::2", 64, "fe80::1"))
    rd = route_del_cmds("mhi_hwip0", "10.99.1.1", v6=("2001:db8::2", 64, "fe80::1"))
    route_checks = [
        ("v4 route has a metric", " ".join(rc[0]) if rc else "",
         "ip route replace default via 10.99.1.1 dev mhi_hwip0 metric 1000"),
        ("v6 route has a metric", " ".join(rc6[0]) if rc6 else "",
         "ip -6 route replace default via fe80::1 dev mhi_hwip0 metric 1000"),
        ("delete matches the v4 route", len(rd) >= 1 and "-6" not in rd[0], True),
        ("delete covers both families", len(rd), 2),
        ("a zero metric is not accepted silently by default",
         "metric 1000" in " ".join(route_cmds("x", "1.2.3.4", 1000)[0]), True),
    ]
    for label, got, want in route_checks:
        good = got == want
        ok = ok and good
        print(f"  route {label}: {got!r} {'ok' if good else 'MISMATCH want ' + repr(want)}")

    dummy = "kebab-qmi-selftest"
    subprocess.run(["ip", "link", "del", dummy], capture_output=True)
    subprocess.run(["modprobe", "dummy"], capture_output=True)   # CONFIG_DUMMY=m here
    target = dummy
    try:
        ip_run(["ip", "link", "add", dummy, "type", "dummy"])
    except RuntimeError as exc:
        # Some kernels reject the dummy link kind.  Fall back to the real
        # raw-IP interface when it is provably idle (down, no IPv4 address),
        # which is exactly the state data-up expects to find it in.
        print(f"  dummy link unavailable ({exc}); trying {args.ifname} instead")
        target = args.ifname
        st = subprocess.run(["ip", "-4", "addr", "show", "dev", target],
                            capture_output=True, text=True)
        link = subprocess.run(["ip", "link", "show", target], capture_output=True, text=True)
        if st.returncode != 0 or "inet " in st.stdout or "state DOWN" not in link.stdout:
            print(f"  iface test skipped ({target} is not idle)")
            print("SELFTEST " + ("PASS" if ok else "FAIL"))
            if not ok:
                raise SystemExit(1)
            return
    orig_mtu = None
    if target != dummy:
        # Remember the real interface's MTU so this test leaves no trace.
        try:
            orig_mtu = ip_run(["ip", "link", "show", target]).split("mtu ", 1)[1].split()[0]
        except Exception:
            orig_mtu = None
    v6cfg = (s["ipv6_str"], s["ipv6_prefix"], None)
    try:
        configure_iface(target, s["ip_str"], s["prefix"], None, s["mtu"], v6=v6cfg)
        out = ip_run(["ip", "-4", "addr", "show", "dev", target])
        good = f"{s['ip_str']}/{s['prefix']}" in out
        ok = ok and good
        print(f"  iface addr on {target}: {'ok' if good else 'MISMATCH'}")
        out6 = ip_run(["ip", "-6", "addr", "show", "dev", target])
        good6 = f"{v6cfg[0]}/{v6cfg[1]}" in out6
        ok = ok and good6
        print(f"  iface addr6 on {target}: {'ok' if good6 else 'MISMATCH'}")
        mtu = ip_run(["ip", "link", "show", target])
        good = f"mtu {s['mtu']}" in mtu
        ok = ok and good
        print(f"  iface mtu on {target}: {'ok' if good else 'MISMATCH'}")
    finally:
        if target == dummy:
            subprocess.run(["ip", "link", "del", dummy], capture_output=True)
        else:
            cleanup_iface(target, s["ip_str"], s["prefix"], None, False, v6cfg)
            left6 = ip_run(["ip", "-6", "addr", "show", "dev", target])
            good6 = v6cfg[0] not in left6
            ok = ok and good6
            print(f"  iface addr6 removed from {target}: {'ok' if good6 else 'MISMATCH'}")
            if orig_mtu:
                subprocess.run(["ip", "link", "set", target, "mtu", orig_mtu], capture_output=True)
    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    if not ok:
        raise SystemExit(1)


def main():
    ap = argparse.ArgumentParser(description="raw QMI WDS client for the kebab SDX55")
    ap.add_argument("command", choices=["info", "dms-manufacturer", "bind", "subscription", "params", "profile", "format", "format-set", "attach-set", "supported",
                                        "start", "settings",
                                        "status", "up", "stop", "data-up", "data-down", "selftest"])
    ap.add_argument("--ep", default="pcie", choices=list(EP_TYPE))
    ap.add_argument("--iface", type=int, default=4, help="endpoint interface number (kebab: 4)")
    ap.add_argument("--mux", type=int, default=0)
    ap.add_argument("--fake", action="store_true",
                    help="data-up: hold a SYNTHETIC bearer (no QMI) to exercise configure/hold/cleanup")
    ap.add_argument("--list", default="",
                    help="attach-set: comma separated profile ids (default: keep the current list)")
    ap.add_argument("--action", type=int, default=None,
                    help="attach-set: 1=none (default), 2=detach or PDN disconnect")
    ap.add_argument("--apply", action="store_true",
                    help="format-set: actually send Set Data Format")
    ap.add_argument("--link-layer", choices=sorted(LL_ID), default=None,
                    help="format-set: link layer protocol to set")
    ap.add_argument("--ul", choices=sorted(AGG_ID), default=None,
                    help="format-set: uplink aggregation protocol to set")
    ap.add_argument("--dl-max-datagrams", type=int, default=None,
                    help="format-set: downlink aggregation datagram limit")
    ap.add_argument("--dl-max-size", type=int, default=None,
                    help="format-set: downlink aggregation byte limit")
    ap.add_argument("--ul-max-datagrams", type=int, default=None,
                    help="format-set: uplink aggregation datagram limit")
    ap.add_argument("--ul-max-size", type=int, default=None,
                    help="format-set: uplink aggregation byte limit")
    ap.add_argument("--ndp", type=int, default=None,
                    help="format-set: NDP signature (0 keeps the firmware default)")
    ap.add_argument("--qos", type=int, choices=[0, 1], default=None,
                    help="format-set: QoS flow header (0 off, 1 on)")
    ap.add_argument("--dl", choices=sorted(AGG_ID), default=None,
                    help="format-set: downlink aggregation protocol to set")
    ap.add_argument("--service", default="wds", choices=["wds", "dms", "wda"],
                    help="supported: which service to ask for its message bitmap")
    ap.add_argument("--index", default="1,2,3,4,5", help="profile: comma-separated 3GPP profile indices to read")
    ap.add_argument("--type", default="none", choices=["none"] + list(SUBSCRIPTION_TYPE),
                    help="subscription: bind this WDS client to a subscription (none = only read it back)")
    ap.add_argument("--bind-subscription", default="off", choices=["off"] + list(SUBSCRIPTION_TYPE),
                    help="up/data-up: bind the client to a subscription before the call (default: off)")
    ap.add_argument("--apn", default="cmnet")
    ap.add_argument("--profile", type=int, default=0, help="use 3GPP profile index instead of APN (this modem: 3=CMNET)")
    ap.add_argument("--ip", default="4", choices=list(IP_FAMILY))
    ap.add_argument("--auth", default="none", choices=["none", "pap", "chap", "both"])
    ap.add_argument("--auth-user", default="")
    ap.add_argument("--auth-pass", default="")
    ap.add_argument("--handle", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--ifname", default="mhi_hwip0", help="raw-IP interface to configure for data-up")
    ap.add_argument("--hold-seconds", type=int, default=0, help="data-up: auto-exit after N seconds (0 = until signal)")
    ap.add_argument("--connect-timeout", type=int, default=15,
                    help="data-up: seconds to wait for the call to report 'connected' before asking for settings")
    ap.add_argument("--settings-retries", type=int, default=3,
                    help="data-up: Get Current Settings attempts while the call settles")
    ap.add_argument("--dry-run", action="store_true", help="data-up: print the config instead of applying it")
    ap.add_argument("--mtu", type=int, default=1500,
                    help="data-up: fallback MTU if the bearer does not report one (mhi_net's own default is 16384)")
    ap.add_argument("--route", action="store_true",
                    help="data-up: also install a default route via the bearer gateway (default: untouched)")
    ap.add_argument("--route-metric", type=int, default=1000,
                    help="data-up: metric for that default route. A metric-less route ties "
                         "with the server uplink (both 0) and can steal it; the bearer is a "
                         "fallback, so this defaults to 1000")
    args = ap.parse_args()
    # metric 0 is exactly what ties with the server uplink; refuse it outright
    if getattr(args, "command", "") == "data-up" and getattr(args, "route_metric", 1) < 1:
        ap.error("--route-metric must be >= 1 (0 would tie with the server uplink)")

    rows = qrtr_lookup()
    if args.command == "info":
        for svc in (SVC_WDS, SVC_DMS):
            node, port = find_service(rows, svc)
            print(f"service {svc} ({SVC_DESCRIPTIONS[svc]}): node {node} port {port}")
        cmd_status(args, rows)
    elif args.command == "dms-manufacturer":
        cmd_dms_manufacturer(args, rows)
    elif args.command == "bind":
        cmd_bind(args, rows)
    elif args.command == "subscription":
        cmd_subscription(args, rows)
    elif args.command == "params":
        cmd_params(args, rows)
    elif args.command == "profile":
        cmd_profile(args, rows)
    elif args.command == "format":
        cmd_format(args, rows)
    elif args.command == "format-set":
        cmd_format_set(args, rows)
    elif args.command == "attach-set":
        cmd_attach_set(args, rows)
    elif args.command == "supported":
        cmd_supported(args, rows)
    elif args.command == "start":
        cmd_start(args, rows)
    elif args.command == "settings":
        cmd_settings(args, rows)
    elif args.command == "status":
        cmd_status(args, rows)
    elif args.command == "up":
        cmd_up(args, rows)
    elif args.command == "stop" or args.command == "data-down":
        cmd_data_down(args, rows)
    elif args.command == "data-up":
        cmd_data_up(args, rows)
    elif args.command == "selftest":
        cmd_selftest(args)


if __name__ == "__main__":
    main()
