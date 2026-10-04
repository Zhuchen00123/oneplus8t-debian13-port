#!/usr/bin/env python3
"""假 MC 服务端：给链路联调用的最小 TCP 服务（不是真的 Minecraft）。

用途：在手机侧客户端就绪之前，先把「中继链路」本身验通。
行为：连上先发一行 BANNER，之后原样回显（前缀 ECHO:）。
"""
import socket
import threading
import sys

BANNER = b"FAKE-MC-BANNER\n"


def handle(conn, addr):
    try:
        conn.settimeout(60)
        conn.sendall(BANNER)
        while True:
            data = conn.recv(4096)
            if not data:
                break
            conn.sendall(b"ECHO:" + data)
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 25566
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(16)
    print("FAKE-MC listening on %s:%d" % (host, port), flush=True)
    while True:
        conn, addr = srv.accept()
        print("FAKE-MC conn from %s:%d" % addr, flush=True)
        threading.Thread(target=handle, args=(conn, addr), daemon=True).start()


if __name__ == "__main__":
    main()
