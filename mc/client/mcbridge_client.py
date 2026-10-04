#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mcbridge 手机侧客户端（纯标准库，零依赖）。

角色：等价于原方案里的「魔搭 DSW 侧 client」——主动出站连到 CNB 中继，
把中继送来的玩家连接转发到本机 MC 服务端（默认 127.0.0.1:25565）。

为什么用 Python 重写而不是交叉编译 Rust：
  主机上没有 gcc/clang/Go 且 Docker 守护进程未运行，Rust 到 aarch64 需要
  ring/OpenSSL 的 C 工具链，走不通；Python 用系统 OpenSSL，零编译、零依赖。

协议（与原实现逐字节对齐，见 mc/mcbridge-rs/src/tunnel.rs、ws.rs）：
  * 两条 WebSocket 流：/t/<secret>/<session>/up（只写）、/t/<secret>/<session>/down（只读）
  * 隧道帧：[type:1][id:4 BE][payload]
      type: 1=OPEN  2=DATA  3=CLOSE  4=PING  5=PONG
  * 中继把玩家的 OPEN/DATA/CLOSE 从 DOWN 送来；本端把 MC 的字节用 DATA 从 UP 送回
  * 每 20 秒在 UP 上发一个 PING；收到 PING 回 PONG
  * 配对靠帧里的 id，不靠 session 名

用法：
  mcbridge_client.py --host <中继主机> --port 443 --secret <隧道密钥>
                     --mc 127.0.0.1:25565 [--session kebab] [--plain]

环境变量回退：MCB_HOST / MCB_PORT / MCB_SECRET / MCB_SESSION / MCB_MC
"""

import argparse
import base64
import os
import signal
import socket
import ssl
import struct
import sys
import threading
import time

T_OPEN, T_DATA, T_CLOSE, T_PING, T_PONG = 1, 2, 3, 4, 5
MAX_MSG = 64 * 1024 * 1024
PING_INTERVAL = 20.0
RECONNECT_DELAY = 5.0
RECV_CHUNK = 16384

# ---------------------------------------------------------------- 日志

_log_lock = threading.Lock()


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    with _log_lock:
        try:
            print(line, flush=True)
        except Exception:
            pass


# ---------------------------------------------------------------- WebSocket

class WsError(Exception):
    pass


class WsStream(object):
    """最小 WebSocket 客户端：只实现本协议需要的东西。"""

    def __init__(self, sock, is_client=True):
        self.sock = sock
        self.is_client = is_client
        self.buf = b""
        self.wlock = threading.Lock()

    # ---- 建连 ----
    @staticmethod
    def connect(host, port, path, use_tls=True, timeout=15.0, debug=False):
        raw = socket.create_connection((host, port), timeout=timeout)
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if use_tls:
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(raw, server_hostname=host)
        else:
            sock = raw
        sock.settimeout(None)

        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            "GET %s HTTP/1.1\r\n"
            "Host: %s\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n" % (path, host, key)
        )
        sock.sendall(req.encode())

        # 逐字节读到头部结束，避免多读走后面的帧数据
        hdr = b""
        while b"\r\n\r\n" not in hdr:
            b = sock.recv(1)
            if not b:
                raise WsError("握手期间连接被关闭")
            hdr += b
            if len(hdr) > 8192:
                raise WsError("握手响应头过长")

        first = hdr.split(b"\r\n", 1)[0].decode("latin-1")
        if " 101" not in first:
            raise WsError("WS 升级失败: %s" % first)
        if debug:
            log("  WS 升级 OK: %s" % first)
        return WsStream(sock, is_client=True)

    # ---- 精确读 ----
    def _read_exact(self, n):
        while len(self.buf) < n:
            d = self.sock.recv(max(65536, n - len(self.buf)))
            if not d:
                raise WsError("连接已关闭")
            self.buf += d
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    # ---- 读一条完整消息 ----
    def recv_message(self):
        """返回一条完整消息；None = 对端正常关闭。"""
        acc = None
        while True:
            h = self._read_exact(2)
            fin = (h[0] & 0x80) != 0
            opcode = h[0] & 0x0F
            masked = (h[1] & 0x80) != 0
            ln = h[1] & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._read_exact(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._read_exact(8))[0]
            if ln > MAX_MSG:
                raise WsError("帧过大: %d" % ln)
            mk = self._read_exact(4) if masked else None
            payload = self._read_exact(ln) if ln else b""
            if mk:
                payload = bytes(b ^ mk[i & 3] for i, b in enumerate(payload))

            if opcode == 0x8:            # close
                return None
            if opcode == 0x9:            # ping → pong
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:            # pong
                continue
            if opcode in (0x1, 0x2):     # text / binary
                if fin:
                    return payload
                acc = bytearray(payload)
                continue
            if opcode == 0x0:            # continuation
                if acc is None:
                    raise WsError("意外的续帧")
                acc += payload
                if fin:
                    return bytes(acc)
                continue
            raise WsError("不支持的 opcode 0x%x" % opcode)

    # ---- 写 ----
    def send(self, payload):
        self._send_frame(0x2, payload)

    def _send_frame(self, opcode, payload):
        n = len(payload)
        head = bytearray()
        head.append(0x80 | opcode)
        mask_bit = 0x80 if self.is_client else 0x00
        if n < 126:
            head.append(mask_bit | n)
        elif n <= 0xFFFF:
            head.append(mask_bit | 126)
            head += struct.pack(">H", n)
        else:
            head.append(mask_bit | 127)
            head += struct.pack(">Q", n)
        if self.is_client:
            mk = os.urandom(4)
            head += mk
            body = bytes(b ^ mk[i & 3] for i, b in enumerate(payload))
        else:
            body = payload
        with self.wlock:
            self.sock.sendall(bytes(head) + body)

    def close(self):
        try:
            self._send_frame(0x8, b"")
        except Exception:
            pass
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


# ---------------------------------------------------------------- 隧道帧

def pack_frame(t, fid, payload=b""):
    return struct.pack(">BI", t, fid) + payload


def parse_frame(msg):
    if len(msg) < 5:
        return None
    t = msg[0]
    fid = struct.unpack(">I", msg[1:5])[0]
    return t, fid, msg[5:]


# ---------------------------------------------------------------- 客户端

class PhoneClient(object):
    def __init__(self, host, port, secret, session, mc_host, mc_port,
                 use_tls=True, debug=False, once=False):
        self.host = host
        self.port = port
        self.secret = secret
        self.session = session
        self.mc = (mc_host, mc_port)
        self.use_tls = use_tls
        self.debug = debug
        self.once = once

        self.up = None
        self.alive = threading.Event()
        self.conns = {}                 # fid -> socket
        self.conns_lock = threading.Lock()
        self.stopping = False

    # ---- 路径 ----
    def _paths(self):
        sec = self.secret if self.secret else "_"
        base = "/t/%s/%s/" % (sec, self.session)
        return base + "up", base + "down"

    # ---- 发帧 ----
    def send_frame(self, t, fid, payload=b""):
        up = self.up
        if up is None or not self.alive.is_set():
            return False
        try:
            up.send(pack_frame(t, fid, payload))
            return True
        except Exception as e:
            if self.debug:
                log("  发帧失败: %s" % e)
            self.alive.clear()
            return False

    # ---- 心跳 ----
    def _ping_loop(self, up):
        while self.alive.is_set():
            for _ in range(int(PING_INTERVAL * 2)):
                if not self.alive.is_set():
                    return
                time.sleep(0.5)
            if not self.send_frame(T_PING, 0):
                return

    # ---- 单次会话 ----
    def run_once(self):
        up_path, down_path = self._paths()
        up = WsStream.connect(self.host, self.port, up_path,
                              use_tls=self.use_tls, debug=self.debug)
        try:
            down = WsStream.connect(self.host, self.port, down_path,
                                    use_tls=self.use_tls, debug=self.debug)
        except Exception:
            up.close()
            raise

        self.up = up
        self.alive.set()
        log("已连上中继 %s:%d（session=%s），转发到 %s:%d"
            % (self.host, self.port, self.session, self.mc[0], self.mc[1]))

        threading.Thread(target=self._ping_loop, args=(up,), daemon=True).start()

        try:
            while self.alive.is_set():
                msg = down.recv_message()
                if msg is None:
                    break
                f = parse_frame(msg)
                if f is None:
                    continue
                t, fid, payload = f
                if t == T_OPEN:
                    self._on_open(fid)
                elif t == T_DATA:
                    self._on_data(fid, payload)
                elif t == T_CLOSE:
                    self._on_close(fid)
                elif t == T_PING:
                    self.send_frame(T_PONG, 0)
        finally:
            self.alive.clear()
            self._close_all()
            try:
                up.close()
            except Exception:
                pass
            try:
                down.close()
            except Exception:
                pass
            self.up = None

    # ---- 玩家连接进来 ----
    def _on_open(self, fid):
        if self.debug:
            log("  OPEN id=%d → 连 %s:%d" % (fid, self.mc[0], self.mc[1]))
        try:
            s = socket.create_connection(self.mc, timeout=10.0)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.settimeout(None)
        except Exception as e:
            log("连本机 MC %s:%d 失败: %s" % (self.mc[0], self.mc[1], e))
            self.send_frame(T_CLOSE, fid)
            return
        with self.conns_lock:
            self.conns[fid] = s
        threading.Thread(target=self._mc_reader, args=(fid, s), daemon=True).start()

    def _mc_reader(self, fid, s):
        try:
            while self.alive.is_set():
                d = s.recv(RECV_CHUNK)
                if not d:
                    break
                if self.debug:
                    log("  MC→隧道 id=%d %d 字节" % (fid, len(d)))
                if not self.send_frame(T_DATA, fid, d):
                    break
        except Exception:
            pass
        with self.conns_lock:
            self.conns.pop(fid, None)
        self.send_frame(T_CLOSE, fid)
        try:
            s.close()
        except Exception:
            pass

    def _on_data(self, fid, payload):
        with self.conns_lock:
            s = self.conns.get(fid)
        if s is None:
            if self.debug:
                log("  DATA id=%d 找不到连接，丢弃 %d 字节" % (fid, len(payload)))
            return
        if self.debug:
            log("  隧道→MC id=%d %d 字节" % (fid, len(payload)))
        try:
            s.sendall(payload)
        except Exception:
            with self.conns_lock:
                self.conns.pop(fid, None)
            self.send_frame(T_CLOSE, fid)
            try:
                s.close()
            except Exception:
                pass

    def _on_close(self, fid):
        with self.conns_lock:
            s = self.conns.pop(fid, None)
        if s is not None:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass
        if self.debug:
            log("  CLOSE id=%d" % fid)

    def _close_all(self):
        with self.conns_lock:
            items = list(self.conns.items())
            self.conns.clear()
        for _, s in items:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass
        if items:
            log("回收了 %d 条玩家连接" % len(items))

    # ---- 主循环 ----
    def run_forever(self):
        while not self.stopping:
            try:
                self.run_once()
                log("隧道已断开")
            except Exception as e:
                log("隧道错误: %s: %s" % (type(e).__name__, e))
            if self.once or self.stopping:
                break
            log("%.0f 秒后重连…" % RECONNECT_DELAY)
            # 分片 sleep，便于 Ctrl-C / SIGTERM 快速退出
            slept = 0.0
            while slept < RECONNECT_DELAY and not self.stopping:
                time.sleep(0.25)
                slept += 0.25

    def stop(self):
        self.stopping = True
        self.alive.clear()
        try:
            if self.up is not None:
                self.up.close()
        except Exception:
            pass


# ---------------------------------------------------------------- 入口

def env(name, default=""):
    return os.environ.get(name, default) or default


def main():
    ap = argparse.ArgumentParser(description="mcbridge 手机侧客户端（CNB 中继反向隧道）")
    ap.add_argument("--host", default=env("MCB_HOST"))
    ap.add_argument("--port", type=int, default=int(env("MCB_PORT", "443")))
    ap.add_argument("--secret", default=env("MCB_SECRET"))
    ap.add_argument("--session", default=env("MCB_SESSION", "kebab"))
    ap.add_argument("--mc", default=env("MCB_MC", "127.0.0.1:25565"))
    ap.add_argument("--plain", action="store_true", help="不用 TLS（排障用）")
    ap.add_argument("--once", action="store_true", help="只跑一次，不自动重连")
    ap.add_argument("--debug", action="store_true", help="打印每帧收发")
    args = ap.parse_args()

    if not args.host:
        ap.error("缺少 --host（或环境变量 MCB_HOST）")
    if ":" in args.mc:
        mc_host, mc_port = args.mc.rsplit(":", 1)
        mc_port = int(mc_port)
    else:
        mc_host, mc_port = args.mc, 25565

    cli = PhoneClient(
        host=args.host,
        port=args.port,
        secret=args.secret,
        session=args.session,
        mc_host=mc_host,
        mc_port=mc_port,
        use_tls=not args.plain,
        debug=args.debug,
        once=args.once,
    )

    def _term(signum, frame):
        log("收到信号 %d，正在退出" % signum)
        cli.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _term)
        except Exception:
            pass

    log("启动：中继 %s:%d  session=%s  →  本机 %s:%d  TLS=%s"
        % (args.host, args.port, args.session, mc_host, mc_port, not args.plain))
    cli.run_forever()
    log("已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
