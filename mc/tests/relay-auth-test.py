import socket, ssl, base64, os, time, sys

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
    head = ss.recv(4096)
    return ss, head.split(b"\r\n")[0].decode(errors="replace")

def http_get(path):
    ss = connect()
    ss.sendall(("GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n" % (path, HOST)).encode())
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
    return buf.decode("utf-8", errors="replace")

def tunnel_state():
    try:
        t = http_get("/status")
    except Exception as e:
        return "STATUS-ERR %s" % e
    for ln in t.splitlines():
        if "DSW" in ln or "tunnel" in ln.lower():
            return ln.strip()
    return "(未找到隧道行)"

def live(ss, seconds=4):
    """101 之后连接是否被服务端保持（还是立刻关掉）。"""
    ss.settimeout(seconds)
    try:
        d = ss.recv(4096)
        if d == b"":
            return "服务端立刻关闭了连接（鉴权失败）"
        return "服务端保持连接并有数据 %d 字节（鉴权通过）" % len(d)
    except socket.timeout:
        return "连接保持打开、无数据（鉴权通过）"
    except Exception as e:
        return "读取异常: %s" % e

print("=== 基线 ===")
print("  /status :", tunnel_state())
print()
print("=== 测试 A：正确密钥 ===")
try:
    ss, line = upgrade("/t/wst_9f3a7c2e5b18d460/kebab/up")
    print("  升级响应:", line)
    time.sleep(1)
    st_during = tunnel_state()
    print("  保持期间 /status :", st_during)
    print("  连接活性:", live(ss))
    ss.close()
except Exception as e:
    print("  异常:", type(e).__name__, e)
print()
print("=== 测试 B：错误密钥（负对照）===")
try:
    ss2, line2 = upgrade("/t/wrongsecret123/kebab/up")
    print("  升级响应:", line2)
    time.sleep(0.5)
    print("  连接活性:", live(ss2, 3))
    ss2.close()
except Exception as e:
    print("  异常:", type(e).__name__, e)
print()
time.sleep(1)
print("=== 事后 /status（应恢复未连接）===")
print(" ", tunnel_state())