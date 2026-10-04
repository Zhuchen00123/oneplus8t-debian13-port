import socket, ssl, base64, os, sys, time

HOST = "zzsh2enrz7-9000.cnb.run"
PORT = 443

def ws_upgrade(path, timeout=15):
    raw = socket.create_connection((HOST, PORT), timeout=timeout)
    ctx = ssl.create_default_context()
    ss = ctx.wrap_socket(raw, server_hostname=HOST)
    key = base64.b64encode(os.urandom(16)).decode()
    req = ("GET %s HTTP/1.1\r\n"
           "Host: %s\r\n"
           "Upgrade: websocket\r\n"
           "Connection: Upgrade\r\n"
           "Sec-WebSocket-Key: %s\r\n"
           "Sec-WebSocket-Version: 13\r\n\r\n" % (path, HOST, key))
    ss.sendall(req.encode())
    data = ss.recv(4096)
    return ss, data

def http_get(path, timeout=15):
    raw = socket.create_connection((HOST, PORT), timeout=timeout)
    ctx = ssl.create_default_context()
    ss = ctx.wrap_socket(raw, server_hostname=HOST)
    ss.sendall(("GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n" % (path, HOST)).encode())
    buf = b""
    while len(buf) < 4096:
        try:
            d = ss.recv(4096)
        except Exception:
            break
        if not d:
            break
        buf += d
        if len(buf) > 1200:
            break
    return buf

print("=== 1. TLS 握手（Python/OpenSSL）===")
try:
    ss, head = ws_upgrade("/t/wst_9f3a7c2e5b18d460/kebab/up")
    line = head.split(b"\r\n")[0].decode(errors="replace")
    print("  隧道 UP 升级 → %s" % line)
    print("  101 = 密钥正确、隧道可用")
    ss.close()
except Exception as e:
    print("  失败: %s %s" % (type(e).__name__, e))

print()
print("=== 2. /status 页面 ===")
try:
    body = http_get("/status")
    txt = body.decode("utf-8", errors="replace")
    for ln in txt.splitlines()[:14]:
        print("  " + ln)
except Exception as e:
    print("  失败: %s %s" % (type(e).__name__, e))

print()
print("=== 3. 错误密钥应当被拒（负对照）===")
try:
    ss2, head2 = ws_upgrade("/t/wrong_secret_xxx/kebab/up")
    print("  → %s" % head2.split(b"\r\n")[0].decode(errors="replace"))
    ss2.close()
except Exception as e:
    print("  失败: %s %s" % (type(e).__name__, e))