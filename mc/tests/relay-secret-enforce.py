import socket, ssl, base64, os, time

HOST = "zzsh2enrz7-9000.cnb.run"
PORT = 443

def connect():
    raw = socket.create_connection((HOST, PORT), timeout=15)
    ctx = ssl.create_default_context()
    return ctx.wrap_socket(raw, server_hostname=HOST)

def upgrade(path):
    ss = connect()
    key = base64.b64encode(os.urandom(16)).decode()
    req = ("GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, HOST, key))
    ss.sendall(req.encode())
    ss.recv(4096)
    return ss

def status_line():
    ss = connect()
    ss.sendall(("GET /status HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n" % HOST).encode())
    buf = b""
    while True:
        try:
            d = ss.recv(4096)
        except Exception:
            break
        if not d:
            break
        buf += d
    ss.close()
    for ln in buf.decode("utf-8", errors="replace").splitlines():
        if "DSW" in ln:
            return ln.strip()
    return "(未找到)"

def label(s):
    if "\u274c" in s: return "未连接"
    if "\u26a0" in s: return "只有一半(已注册)"
    if "\u2705" in s: return "已连接"
    return "?"

print("基线                  :", label(status_line()))

print()
print("--- 打开【正确】密钥 的 /up，保持 5 秒 ---")
a = upgrade("/t/wst_9f3a7c2e5b18d460/kebab/up")
time.sleep(1.5)
s1 = status_line(); print("  期间状态            :", label(s1))
a.close(); time.sleep(1.5)
print("  关闭后              :", label(status_line()))

print()
print("--- 打开【错误】密钥 的 /up，保持 5 秒 ---")
b = upgrade("/t/THIS_IS_WRONG_XXXX/kebab/up")
time.sleep(1.5)
s2 = status_line(); print("  期间状态            :", label(s2))
b.close(); time.sleep(1.5)
print("  关闭后              :", label(status_line()))

print()
print("--- 打开【正确】密钥 的 /up 和 /down 两条 ---")
u = upgrade("/t/wst_9f3a7c2e5b18d460/kebab/up")
d = upgrade("/t/wst_9f3a7c2e5b18d460/kebab/down")
time.sleep(1.5)
print("  期间状态            :", label(status_line()))
u.close(); d.close()