#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kebab 校园网门户自动登录保活（Dr.COM / 城市热点 eportal，广东工业大学）。

由 systemd timer 每 60 秒拉起一次（Type=oneshot）。纯标准库，零依赖。

================ 接口是怎么确定的（2026-10-04 实机探测）================
一开始从门户的 a40.js 逆出的是 `page.path + 'login'` = `http://10.0.3.2/drcom/login`，
**那是错的**：实测只要带上 DDDDD 参数它就返回 `404 URL no found`。

真正活着的是 `page.portal_api + 'login'`（loadConfig 返回 login_method=1）：

    GET http://10.0.3.2:801/eportal/portal/login
        ?user_account=,0,<账号>
        &user_password=<密码>
        &wlan_user_ip=<本机 wlan0 的 IPv4>
        &wlan_user_ipv6=
        &wlan_user_mac=<MAC 或 000000000000>
        &wlan_ac_ip=<网关 302 给的 wlanacip>
        &wlan_ac_name=
        &login_method=1&terminal_type=2&jsVersion=4.1
        &callback=drNNNN&v=<rand>&lang=zh-cn

实测响应（2026-10-04，本机已在线）：
    dr1({"result":0,"msg":"IP: 10.44.0.100 已经在线！","ret_code":2})
  ⇒ **ret_code=2 表示该 IP 已经在线**，等同于"不需要再登"。
  字段名必须是 user_account/user_password；只给 DDDDD/upass 会被回
  `{"result":0,"msg":"无法获取用户认证账号！"}`。

loadConfig 确认：`en_md5=0`（密码明文，不用 MD5）、`account_suffix=""`（普通校园网，无 @dx/@lt）。

================ 三条必须遵守的规则 ================
  1. **在线判断必须用 HTTPS，不能用 HTTP。** 未认证时网关会把任意 HTTP 请求 302
     劫持到门户（返回 200/302 的假页面），HTTP 探测会误判成"在线"。
     用 HTTPS 时网关伪造不了证书，TLS 校验失败 ⇒ 确定离线。
     门户自己的 /drcom/chkstatus 也不可信：实测网络明明是通的，它却返回 result:0。
  2. **登录前先让网关 302 一次**（可选但有益）：网关需要先为这个源 IP 建会话，
     那个 302 的 Location 里带着 wlanuserip / wlanacip。
     （经验来源：xiaomai1011/GZIST-NetKeeper，同为 2026-09 升级后的 Dr.COM。）
  3. **注销接口是坏的**：`/drcom/logout`、`:801/eportal/?c=ACSetting&a=Logout`、
     `/eportal/portal/logout` 三种都试过，分别返回
     `Logout Error(-1)` / `Logout failed.` / `Radius注销失败！`。
     所以"踢自己下线做测试"这条路走不通，只能等真实掉线。

凭据只从 /etc/kebab-portal-keeper.env 读，任何日志都不打印密码，账号打码。

退出码永远是 0：这是 timer 的 oneshot，非零退出会把单元标成 failed，
污染"无 failed unit"这条基线。真实状态看日志和 /run 里的状态文件。

手动排障：
  python3 kebab-portal-keeper.py --check     # 只探测在线状态
  python3 kebab-portal-keeper.py --force     # 跳过探测，强制登一次
  python3 kebab-portal-keeper.py --dry-run   # 打印将要发出的 URL（密码打码）
"""

import http.client
import json
import os
import random
import socket
import ssl
import sys
import time
import urllib.parse

# ------------------------------------------------------------------ 配置

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

# 在线探测（按顺序试，任一个通过就认为在线）。必须都是 HTTPS。
PROBE_TARGETS = (
    ("www.baidu.com", "/"),
    ("connect.rom.miui.com", "/generate_204"),
)

# 用来触发网关 302 的地址（普通 HTTP，未认证时必定被劫持）
HIJACK_HOST = os.environ.get("PK_HIJACK_HOST") or "connect.rom.miui.com"
HIJACK_PATH = os.environ.get("PK_HIJACK_PATH") or "/generate_204"
HIJACK_PORT = int(os.environ.get("PK_HIJACK_PORT") or "80")

# 门户登录接口（实测可用）
LOGIN_PATH = "/eportal/portal/login"

STATE_FILE = os.environ.get("PK_STATE_FILE") or "/run/kebab-portal-keeper.state"
COUNT_FILE = os.environ.get("PK_COUNT_FILE") or "/run/kebab-portal-keeper.count"
GW_FILE = os.environ.get("PK_GW_FILE") or "/run/kebab-portal-keeper.gw"

PROBE_TIMEOUT = 6.0
LOGIN_TIMEOUT = 10.0


def env(name, default=""):
    return os.environ.get(name, default) or default


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        # 控制台编码不是 UTF-8 时（比如 Windows GBK）别让日志把脚本炸掉
        enc = sys.stdout.encoding or "utf-8"
        print(line.encode(enc, "replace").decode(enc, "replace"), flush=True)


def mask_account(acct):
    """账号打码：只留前 2 位和后 2 位。"""
    if not acct:
        return "<空>"
    if len(acct) <= 6:
        return acct[:1] + "***"
    return acct[:2] + "***" + acct[-2:]


# ------------------------------------------------------------------ 探测


def https_probe():
    """返回 (在线?, 说明)。HTTPS + 证书校验，网关伪造不了。"""
    last = "无可用探测目标"
    for host, path in PROBE_TARGETS:
        try:
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(host, 443, timeout=PROBE_TIMEOUT, context=ctx)
            try:
                conn.request("GET", path, headers={
                    "User-Agent": UA,
                    "Connection": "close",
                })
                resp = conn.getresponse()
                resp.read(2048)
                code = resp.status
            finally:
                conn.close()
            if code in (200, 204, 301, 302, 403):
                return True, "%s → HTTP %d" % (host, code)
            last = "%s → HTTP %d" % (host, code)
        except ssl.SSLError as e:
            last = "%s: TLS 校验失败（%s）" % (host, type(e).__name__)
        except socket.timeout:
            last = "%s: 连接超时" % host
        except OSError as e:
            last = "%s: %s" % (host, e)
        except Exception as e:
            last = "%s: %s: %s" % (host, type(e).__name__, e)
    return False, last


def cache_gw_params(got):
    try:
        with open(GW_FILE, "w") as f:
            json.dump(got, f)
    except Exception:
        pass


def load_cached_gw_params():
    try:
        with open(GW_FILE, "r") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def capture_gw_params():
    """发一个会被劫持的 HTTP 请求，从 302 的 Location 里取网关登记的权威参数。

    在线时不会被劫持（返回 204），那就用上次缓存下来的；再没有就用 env 的兜底。
    """
    loc = ""
    try:
        conn = http.client.HTTPConnection(HIJACK_HOST, HIJACK_PORT, timeout=PROBE_TIMEOUT)
        try:
            conn.request("GET", HIJACK_PATH, headers={
                "User-Agent": UA,
                "Connection": "close",
            })
            resp = conn.getresponse()
            resp.read(4096)
            loc = resp.getheader("Location") or ""
        finally:
            conn.close()
    except Exception as e:
        log("  [WARN] 取网关参数失败：%s: %s" % (type(e).__name__, e))

    if loc:
        got = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(loc).query))
        keep = {}
        for k in ("wlanuserip", "wlanacip", "wlanacname", "wlanusermac"):
            v = got.get(k)
            if v:
                keep[k] = v
        log("  网关 302 → %s" % urllib.parse.urlparse(loc).netloc)
        if keep:
            cache_gw_params(keep)
        return keep

    cached = load_cached_gw_params()
    if cached:
        log("  未被劫持（可能已在线），沿用缓存参数：%s" % ", ".join(sorted(cached.keys())))
    else:
        log("  未被劫持（可能已在线），没有缓存参数")
    return cached


def local_ipv4(portal_host):
    """取本机 wlan0 的 IPv4（UDP connect 不实际发包）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((portal_host, 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return ""


# ------------------------------------------------------------------ 登录


def build_login_query(account, password, wlan_ip, wlan_mac, gw):
    """拼出 /eportal/portal/login 的查询串（字段名与实测一致）。

    wlan_ac_ip 是**必填**的：2026-10-04 实测，同一组参数下
      wlan_ac_ip=172.16.254.2  → {"result":0,"msg":"IP: ... 已经在线！","ret_code":2}
      wlan_ac_ip=(空)          → {"result":0,"ret_code":4,"msg":"AC认证失败"}
    所以拿不到劫持参数时必须用 PK_AC_IP 兜底，不能留空。
    """
    acct_field = env("PK_ACCOUNT_FIELD", "user_account")
    pass_field = env("PK_PASSWORD_FIELD", "user_password")
    prefix = env("PK_ACCOUNT_PREFIX", ",0,")

    ac_ip = gw.get("wlanacip") or env("PK_AC_IP")
    ac_name = gw.get("wlanacname") or env("PK_AC_NAME")

    q = [
        (acct_field, prefix + account),
        (pass_field, password),
        ("wlan_user_ip", wlan_ip),
        ("wlan_user_ipv6", gw.get("wlan_user_ipv6", "")),
        ("wlan_user_mac", wlan_mac),
        ("wlan_ac_ip", ac_ip),
        ("wlan_ac_name", ac_name),
        ("login_method", env("PK_LOGIN_METHOD", "1")),
        ("terminal_type", env("PK_TERMINAL_TYPE", "2")),
        ("callback", "dr%d" % random.randint(1000, 9999)),
        ("jsVersion", env("PK_JS_VERSION", "4.1")),
        ("v", str(random.randint(100, 9999))),
        ("lang", "zh-cn"),
    ]
    return q


def parse_jsonp(body):
    """dr1234({...}) → dict；不是 JSONP 就返回 None。"""
    s = body.strip()
    i = s.find("(")
    j = s.rfind(")")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(s[i + 1:j])
    except Exception:
        return None


def classify(data):
    """把门户响应归类成 (是否达标, 说明)。

    达标 = 登录成功(result=1) 或 该 IP 本来就在线(ret_code=2)。
    """
    if data is None:
        return False, "（非 JSONP 响应）"
    res = data.get("result")
    ret = data.get("ret_code")
    msg = data.get("msg", "")
    if res in (1, "1", "ok"):
        return True, "result=1 登录成功 %s" % msg
    if ret in (2, "2"):
        return True, "ret_code=2 该 IP 已在线（等价于不需要再登）%s" % msg
    bits = []
    for k in ("result", "ret_code", "msg", "error", "error_msg"):
        if k in data:
            bits.append("%s=%s" % (k, data[k]))
    return False, " ".join(bits) or json.dumps(data, ensure_ascii=False)[:200]


def do_login(url):
    parsed = urllib.parse.urlsplit(url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=LOGIN_TIMEOUT)
    try:
        conn.request("GET", parsed.path + "?" + parsed.query, headers={
            "User-Agent": UA,
            "Referer": "http://%s/a79.htm" % parsed.hostname,
            "Accept": "*/*",
            "Connection": "close",
        })
        resp = conn.getresponse()
        body = resp.read(65536).decode("utf-8", "replace")
        return resp.status, body
    finally:
        conn.close()


# ------------------------------------------------------------------ 限流 / 状态


def bump_counter():
    """返回本小时内的累计尝试次数。"""
    hour = int(time.time() // 3600)
    cnt = 0
    try:
        with open(COUNT_FILE, "r") as f:
            parts = f.read().split()
        if len(parts) == 2 and int(parts[0]) == hour:
            cnt = int(parts[1])
    except Exception:
        pass
    cnt += 1
    try:
        with open(COUNT_FILE, "w") as f:
            f.write("%d %d\n" % (hour, cnt))
    except Exception:
        pass
    return cnt


def read_state():
    try:
        with open(STATE_FILE, "r") as f:
            return f.read().strip()
    except Exception:
        return ""


def write_state(s):
    try:
        with open(STATE_FILE, "w") as f:
            f.write(s + "\n")
    except Exception:
        pass


# ------------------------------------------------------------------ 主流程


def check_only():
    ok, detail = https_probe()
    log(("在线 ✅ " if ok else "离线 ❌ ") + detail)
    return 0


def main(argv):
    account = env("PK_USER")
    password = env("PK_PASS")
    portal_host = env("PK_PORTAL_HOST", "10.0.3.2").split(":")[0]
    api_port = env("PK_API_PORT", "801")
    wlan_mac = env("PK_WLAN_MAC", "000000000000")
    max_per_hour = int(env("PK_MAX_PER_HOUR", "12"))

    dry = "--dry-run" in argv
    # dry-run 必须在任何探测之前就退出，否则在线时会走"在线，无需登录"分支，
    # 根本打不出要发的 URL。
    force = ("--force" in argv) or dry

    if "--check" in argv:
        return check_only()

    if not account or not password:
        # 只在状态变化时喊一次，别每 60 秒刷屏
        if read_state() != "unconfigured":
            write_state("unconfigured")
            log("[ERR] 未配置 PK_USER / PK_PASS，跳过。"
                "填好 /etc/kebab-portal-keeper.env 即可自动生效。")
        return 0

    if not force:
        ok, detail = https_probe()
        if ok:
            if read_state() != "online":
                write_state("online")
                log("[OK] 在线（%s），无需登录" % detail)
            return 0
        log("[WARN] 离线：%s" % detail)
        write_state("offline")

    n = bump_counter()
    if n > max_per_hour:
        if read_state() != "throttled":
            write_state("throttled")
            log("[ERR] 本小时已尝试 %d 次（上限 %d），暂停。"
                "反复失败通常是密码错、账号在别处在线或门户地址变了。"
                % (n, max_per_hour))
        return 0

    wlan_ip = local_ipv4(portal_host)
    log("[..] 第 %d 次尝试登录：账号 %s，门户 %s:%s，本机 IP %s"
        % (n, mask_account(account), portal_host, api_port, wlan_ip or "<未知>"))

    gw = capture_gw_params()
    if gw:
        log("  使用的网关参数：%s" % ", ".join(sorted(gw.keys())))

    q = build_login_query(account, password, wlan_ip, wlan_mac, gw)
    url = urllib.parse.urlunsplit((
        "http", "%s:%s" % (portal_host, api_port), LOGIN_PATH,
        urllib.parse.urlencode(q), ""))

    if dry:
        # 只把密码打码，其他字段照打——这样能直接和实测字段逐个比对
        pass_field = env("PK_PASSWORD_FIELD", "user_password")
        safe = [(k, "<password>" if k == pass_field else v) for k, v in q]
        log("  [DRY-RUN] %s" % urllib.parse.urlunsplit((
            "http", "%s:%s" % (portal_host, api_port), LOGIN_PATH,
            urllib.parse.urlencode(safe), "")))
        return 0

    try:
        status, body = do_login(url)
    except Exception as e:
        log("[ERR] 登录请求本身失败：%s: %s" % (type(e).__name__, e))
        return 0

    data = parse_jsonp(body)
    ok, detail = classify(data)
    log("  响应 HTTP %d：%s" % (status, detail))
    if data is None:
        log("  原始响应前 200 字节：%s" % body[:200].replace("\n", " "))

    time.sleep(2)
    probe_ok, probe_detail = https_probe()
    if probe_ok:
        write_state("online")
        log("[OK] 复测在线 ✅ %s" % probe_detail)
    elif ok:
        # 门户说成功/已在线，但实际还是不通：多半是 AC 侧会话残留，
        # 这种情况注销接口又是坏的，只能记清楚等人看。
        write_state("stale")
        log("[WARN] 门户应答达标（%s），但复测仍离线 ❌ %s —— AC 侧可能有残留会话"
            % (detail, probe_detail))
    else:
        write_state("offline")
        log("[ERR] 复测仍离线 ❌ %s" % probe_detail)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
