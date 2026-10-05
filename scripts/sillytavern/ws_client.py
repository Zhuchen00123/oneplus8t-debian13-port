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
import hashlib
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
WRITE_TIMEOUT = 10.0
IDLE_TIMEOUT = 75.0

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
        deadline = time.monotonic() + timeout
        raw = socket.create_connection((host, port), timeout=timeout)
        sock = raw
        def remaining():
            left = deadline - time.monotonic()
            if left <= 0:
                raise socket.timeout("WebSocket 握手超过截止时间")
            return left
        try:
            raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            raw.settimeout(remaining())
            if use_tls:
                sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
            key = base64.b64encode(os.urandom(16)).decode()
            req = (
                "GET %s HTTP/1.1\r\nHost: %s:%d\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
                % (path, host, port, key)
            )
            sock.settimeout(remaining())
            sock.sendall(req.encode())
            hdr = bytearray()
            while b"\r\n\r\n" not in hdr:
                sock.settimeout(remaining())
                b = sock.recv(1)
                if not b:
                    raise WsError("握手期间连接被关闭")
                hdr.extend(b)
                if len(hdr) > 8192:
                    raise WsError("握手响应头过长")
            lines = bytes(hdr).decode("latin-1").split("\r\n")
            status = lines[0].split()
            if len(status) < 2 or status[1] != "101":
                raise WsError("WS 升级失败: %s" % lines[0])
            headers = dict((k.strip().lower(), v.strip()) for line in lines[1:]
                           if ":" in line for k, v in [line.split(":", 1)])
            accept = base64.b64encode(hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            if headers.get("sec-websocket-accept") != accept:
                raise WsError("WebSocket Accept 校验失败")
            sock.settimeout(IDLE_TIMEOUT)
            if debug:
                log("  WS 升级 OK: %s" % lines[0])
            return WsStream(sock, is_client=True)
        except BaseException:
            sock.close()
            raw.close()
            raise

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
                if len(acc) > MAX_MSG:
                    raise WsError("消息过大")
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
        # 不先抢写锁发 Close：另一个线程可能正堵在 sendall 上。
        # shutdown 会同时唤醒读写，保证故障恢复与停止不会卡住。
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

class LinkState:
    def __init__(self, up, down):
        self.up, self.down = up, down
        self.alive = threading.Event()
        self.alive.set()
        self.last_received = time.monotonic()
        self.conns = {}
        self.lock = threading.Lock()


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
        self.down = None
        self.link = None
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
    def send_frame(self, t, fid, payload=b"", link=None):
        link = self.link if link is None else link
        if link is None or link is not self.link or not link.alive.is_set():
            return False
        try:
            link.up.send(pack_frame(t, fid, payload))
            return True
        except Exception as e:
            self._abort_link(link, "写入失败: %s" % type(e).__name__)
            return False

    def _abort_link(self, link, reason=None):
        was_alive = link.alive.is_set()
        link.alive.clear()
        if was_alive and reason:
            log("隧道错误: %s" % reason)
        # 使用捕获的 link，旧线程不能关闭下一次重连的 socket。
        for stream in (link.up, link.down):
            stream.close()

    # ---- 心跳 ----
    def _ping_loop(self, link):
        next_ping = time.monotonic() + PING_INTERVAL
        while link.alive.is_set() and link is self.link:
            now = time.monotonic()
            if now - link.last_received >= IDLE_TIMEOUT:
                self._abort_link(link, "超过接收心跳截止时间")
                return
            if now >= next_ping:
                if not self.send_frame(T_PING, 0, link=link):
                    return
                next_ping = time.monotonic() + PING_INTERVAL
            time.sleep(0.25)

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

        up.sock.settimeout(WRITE_TIMEOUT)
        link = LinkState(up, down)
        self.link = link
        self.up, self.down = up, down
        self.alive = link.alive
        self.conns, self.conns_lock = link.conns, link.lock
        log("已连上中继 %s:%d（session=%s），转发到 %s:%d"
            % (self.host, self.port, self.session, self.mc[0], self.mc[1]))

        threading.Thread(target=self._ping_loop, args=(link,), daemon=True).start()

        try:
            while link.alive.is_set():
                msg = down.recv_message()
                if msg is None:
                    break
                link.last_received = time.monotonic()
                f = parse_frame(msg)
                if f is None:
                    continue
                t, fid, payload = f
                if t == T_OPEN:
                    self._on_open(fid, link)
                elif t == T_DATA:
                    self._on_data(fid, payload, link)
                elif t == T_CLOSE:
                    self._on_close(fid, link)
                elif t == T_PING:
                    self.send_frame(T_PONG, 0, link=link)
        finally:
            self._abort_link(link)
            self._close_all(link)
            if self.link is link:
                self.link = None
                self.up = self.down = None

    # ---- 玩家连接进来 ----
    def _on_open(self, fid, link):
        if self.debug:
            log("  OPEN id=%d → 连 %s:%d" % (fid, self.mc[0], self.mc[1]))
        try:
            s = socket.create_connection(self.mc, timeout=10.0)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.settimeout(WRITE_TIMEOUT)
        except Exception as e:
            log("连本机 MC %s:%d 失败: %s" % (self.mc[0], self.mc[1], e))
            self.send_frame(T_CLOSE, fid, link=link)
            return
        with link.lock:
            if not link.alive.is_set() or fid in link.conns:
                s.close()
                return
            link.conns[fid] = s
        threading.Thread(target=self._mc_reader, args=(fid, s, link), daemon=True).start()

    def _mc_reader(self, fid, s, link):
        try:
            while link.alive.is_set():
                try:
                    d = s.recv(RECV_CHUNK)
                except socket.timeout:
                    continue
                if not d:
                    break
                if self.debug:
                    log("  MC→隧道 id=%d %d 字节" % (fid, len(d)))
                if not self.send_frame(T_DATA, fid, d, link):
                    break
        except Exception:
            pass
        with link.lock:
            if link.conns.get(fid) is s:
                link.conns.pop(fid, None)
        self.send_frame(T_CLOSE, fid, link=link)
        try:
            s.close()
        except Exception:
            pass

    def _on_data(self, fid, payload, link):
        with link.lock:
            s = link.conns.get(fid)
        if s is None:
            if self.debug:
                log("  DATA id=%d 找不到连接，丢弃 %d 字节" % (fid, len(payload)))
            return
        if self.debug:
            log("  隧道→MC id=%d %d 字节" % (fid, len(payload)))
        try:
            s.sendall(payload)
        except Exception:
            with link.lock:
                if link.conns.get(fid) is s:
                    link.conns.pop(fid, None)
            self.send_frame(T_CLOSE, fid, link=link)
            try:
                s.close()
            except Exception:
                pass

    def _on_close(self, fid, link):
        with link.lock:
            s = link.conns.pop(fid, None)
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

    def _close_all(self, link):
        with link.lock:
            items = list(link.conns.items())
            link.conns.clear()
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
        link = self.link
        if link is not None:
            self._abort_link(link)


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
        # ⚠️ 这一行是必须的，只设标志位不够。
        #
        # 隧道活着时主线程阻塞在 socket.recv() 上（连接是 settimeout(None)）。
        # PEP 475 规定：信号处理器**正常返回**时，被中断的系统调用会自动重启。
        # 于是 recv 继续阻塞，run_forever 里那句 `while not self.stopping` 永远轮不到。
        # 而且 stop() 关的是 UP 连接，主线程读的却是 DOWN 连接，关错了一根。
        #
        # 实测症状：日志明明打了「收到信号 15」，进程仍要等满 TimeoutStopSec
        # （systemd 默认 90 秒）再被 SIGKILL；期间隧道一直是断的。
        # 抛 SystemExit 会让被中断的 recv 当场抛出，进程立即退出。
        # SystemExit 继承 BaseException，不会被 run_forever 的 `except Exception` 吞掉。
        raise SystemExit(0)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _term)
        except Exception:
            pass

    log("启动：中继 %s:%d  session=%s  →  本机 %s:%d  TLS=%s"
        % (args.host, args.port, args.session, mc_host, mc_port, not args.plain))
    try:
        cli.run_forever()
    except SystemExit:
        pass          # 来自 _term 的正常退出请求，不当错误
    log("已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
