#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kebab-portal-keeper 的离线自测（起一个假门户，跑真脚本，断言收到的字段）。

不联网、不碰真门户、不写系统目录。手机/主机都能跑：

    python3 mc/tests/test-portal-keeper.py

假门户模拟这套 Dr.COM 的三个行为：
    /hijack                  → 302 到门户页并带上 wlanuserip/wlanacip/wlanacname
    /eportal/portal/login    → 校验 user_account/user_password，返回 JSONP
    (账号密码不对)            → {"result":0,...}
    (密码 == ONLINE_PASS)     → {"result":0,"msg":"...已经在线！","ret_code":2}
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
KEEPER = os.path.join(HERE, "..", "portal", "kebab-portal-keeper.py")

# 让本测试自己的中文/emoji 输出在 GBK 控制台上也不炸
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

GOOD_USER = "3120001234"
GOOD_PASS = "S3cret-Pa55w0rd!"
BAD_PASS = "wrong-password"
ONLINE_PASS = "already-online-x"          # 这个密码让假门户回 ret_code=2

WLAN_USER_IP = "10.44.0.100"
WLAN_AC_IP = "172.16.254.2"
WLAN_AC_NAME = "GDUT-AC-1"
WLAN_MAC = "00037f12e8e8"

RECEIVED = []          # 假门户收到的登录查询参数

EXPECTED_FIELDS = [
    "user_account", "user_password", "wlan_user_ip", "wlan_user_ipv6",
    "wlan_user_mac", "wlan_ac_ip", "wlan_ac_name", "login_method",
    "terminal_type", "callback", "jsVersion", "v", "lang",
]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        raw = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))

        if parsed.path == "/hijack":
            loc = ("http://127.0.0.1:%d/a79.htm?wlanuserip=%s&wlanacname=%s&wlanacip=%s"
                   % (self.server.server_address[1], WLAN_USER_IP, WLAN_AC_NAME, WLAN_AC_IP))
            return self._send(302, "", extra={"Location": loc})

        if parsed.path == "/nohijack":
            # 模拟"已经在线、没被劫持"：普通 204，没有 Location
            return self._send(204, "")

        if parsed.path == "/eportal/portal/login":
            RECEIVED.append(query)
            cb = query.get("callback", "dr1")
            acct = query.get("user_account", "")
            pwd = query.get("user_password", "")
            if pwd == ONLINE_PASS:
                payload = {"result": 0, "msg": "IP: %s 已经在线！" % WLAN_USER_IP, "ret_code": 2}
            elif acct == ",0," + GOOD_USER and pwd == GOOD_PASS:
                payload = {"result": 1, "uid": GOOD_USER, "UL": ""}
            else:
                payload = {"result": 0, "msg": "账号或密码错误", "ret_code": ""}
            return self._send(200, "%s(%s)" % (cb, json.dumps(payload)),
                              ctype="application/javascript; charset=utf-8")

        return self._send(404, "not found")


def start_fake_portal():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def run_keeper(port, password, extra_env=None, args=("--force",)):
    workdir = tempfile.mkdtemp(prefix="pk-test-")
    env = dict(os.environ)
    env.update({
        "PK_USER": GOOD_USER,
        "PK_PASS": password,
        "PK_PORTAL_HOST": "127.0.0.1",
        "PK_API_PORT": str(port),
        "PK_HIJACK_HOST": "127.0.0.1",
        "PK_HIJACK_PATH": "/hijack",
        "PK_HIJACK_PORT": str(port),
        "PK_WLAN_MAC": WLAN_MAC,
        "PK_STATE_FILE": os.path.join(workdir, "state"),
        "PK_COUNT_FILE": os.path.join(workdir, "count"),
        "PK_GW_FILE": os.path.join(workdir, "gw"),
        "PK_MAX_PER_HOUR": "12",
        # 子进程输出强制 UTF-8，父进程也按 UTF-8 解码，避免 Windows GBK 扯皮
        "PYTHONIOENCODING": "utf-8",
    })
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, KEEPER, *args],
        env=env, capture_output=True, text=True, timeout=90,
        encoding="utf-8", errors="replace",
    )


FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   %s" % (name, detail))
        FAILS.append(name)


def dump(proc):
    print("--- keeper stdout ---")
    print(proc.stdout.rstrip())
    if proc.stderr.strip():
        print("--- keeper stderr ---")
        print(proc.stderr.rstrip())
    print("--------------------")


def main():
    srv = start_fake_portal()
    port = srv.server_address[1]
    print("假门户已启动于 127.0.0.1:%d" % port)

    # ---------- 1. 正确密码 ----------
    print("\n[1] 正确密码，--force 强制登录")
    RECEIVED.clear()
    proc = run_keeper(port, GOOD_PASS)
    dump(proc)

    check("退出码为 0", proc.returncode == 0, "rc=%d" % proc.returncode)
    check("确实收到了登录请求", len(RECEIVED) == 1, "收到 %d 条" % len(RECEIVED))
    if RECEIVED:
        got = RECEIVED[0]
        missing = [f for f in EXPECTED_FIELDS if f not in got]
        check("实测字段一个不少", not missing, "缺: %s" % missing)
        check("user_account = ',0,'+账号", got.get("user_account") == ",0," + GOOD_USER,
              got.get("user_account"))
        check("user_password 正确", got.get("user_password") == GOOD_PASS, "<不匹配>")
        check("login_method=1", got.get("login_method") == "1", got.get("login_method"))
        check("jsVersion=4.1", got.get("jsVersion") == "4.1", got.get("jsVersion"))
        check("网关参数 wlan_ac_ip 已带上", got.get("wlan_ac_ip") == WLAN_AC_IP, got.get("wlan_ac_ip"))
        check("网关参数 wlan_ac_name 已带上", got.get("wlan_ac_name") == WLAN_AC_NAME,
              got.get("wlan_ac_name"))
        check("wlan_user_mac 用了配置值", got.get("wlan_user_mac") == WLAN_MAC, got.get("wlan_user_mac"))
        check("callback 形如 drNNNN", re.fullmatch(r"dr\d+", got.get("callback", "") or "") is not None,
              got.get("callback"))
    check("日志报告登录成功", "result=1 登录成功" in proc.stdout, "未见到 result=1")
    check("密码没有出现在 stdout", GOOD_PASS not in proc.stdout, "密码泄露到日志！")
    check("密码没有出现在 stderr", GOOD_PASS not in proc.stderr, "密码泄露到日志！")

    # ---------- 2. ret_code=2（该 IP 已在线）应算达标 ----------
    print("\n[2] ret_code=2『已经在线』应被视为达标，不报登录失败")
    RECEIVED.clear()
    proc2 = run_keeper(port, ONLINE_PASS)
    dump(proc2)
    check("退出码 0", proc2.returncode == 0, "rc=%d" % proc2.returncode)
    check("识别出 ret_code=2", "ret_code=2" in proc2.stdout, proc2.stdout.strip()[-200:])
    check("没有把它当成失败", "[ERR]" not in proc2.stdout, proc2.stdout.strip()[-200:])

    # ---------- 3. 错误密码 ----------
    print("\n[3] 错误密码，应如实报失败")
    RECEIVED.clear()
    proc3 = run_keeper(port, BAD_PASS)
    dump(proc3)
    check("退出码仍为 0", proc3.returncode == 0, "rc=%d" % proc3.returncode)
    check("收到了登录请求", len(RECEIVED) == 1)
    check("日志显示 result=0 且带 msg", "result=0" in proc3.stdout and "账号或密码错误" in proc3.stdout,
          proc3.stdout.strip()[-200:])
    check("错误密码没有出现在 stdout", BAD_PASS not in proc3.stdout)

    # ---------- 4. --dry-run ----------
    print("\n[4] --dry-run 只打印、不发送")
    RECEIVED.clear()
    proc4 = run_keeper(port, GOOD_PASS, args=("--dry-run",))
    check("没有发出登录请求", len(RECEIVED) == 0, "却收到 %d 条" % len(RECEIVED))
    check("打印了 DRY-RUN", "[DRY-RUN]" in proc4.stdout)
    check("dry-run 里密码被打码", GOOD_PASS not in proc4.stdout, "密码泄露！")
    check("dry-run 路径是 /eportal/portal/login", "/eportal/portal/login" in proc4.stdout,
          proc4.stdout.strip()[-200:])

    # ---------- 5. 未配置凭据 ----------
    print("\n[5] 未配置凭据时安静跳过")
    proc5 = run_keeper(port, "", extra_env={"PK_USER": "", "PK_PASS": ""})
    check("退出码 0", proc5.returncode == 0)
    check("提示未配置", "未配置 PK_USER" in proc5.stdout, proc5.stdout.strip()[-200:])
    check("没有发出登录请求", len(RECEIVED) == 0)

    # ---------- 6. 限流 ----------
    print("\n[6] 每小时上限生效")
    wd = tempfile.mkdtemp(prefix="pk-ratelimit-")
    with open(os.path.join(wd, "count"), "w") as f:
        f.write("%d 99\n" % int(time.time() // 3600))
    RECEIVED.clear()
    proc6 = run_keeper(port, GOOD_PASS, extra_env={
        "PK_STATE_FILE": os.path.join(wd, "state"),
        "PK_COUNT_FILE": os.path.join(wd, "count"),
        "PK_GW_FILE": os.path.join(wd, "gw"),
        "PK_MAX_PER_HOUR": "5",
    })
    check("被限流拦下", "已尝试" in proc6.stdout and "暂停" in proc6.stdout, proc6.stdout.strip()[-200:])
    check("限流时确实没发登录请求", len(RECEIVED) == 0)

    # ---------- 7. 抓不到 302 时，必须用 PK_AC_IP 兜底 ----------
    print("\n[7] 未被劫持时 wlan_ac_ip 必须用 PK_AC_IP 兜底（留空会得 ret_code=4）")
    wd7 = tempfile.mkdtemp(prefix="pk-acip-")
    RECEIVED.clear()
    proc7 = run_keeper(port, GOOD_PASS, extra_env={
        "PK_HIJACK_PATH": "/nohijack",
        "PK_AC_IP": WLAN_AC_IP,
        "PK_STATE_FILE": os.path.join(wd7, "state"),
        "PK_COUNT_FILE": os.path.join(wd7, "count"),
        "PK_GW_FILE": os.path.join(wd7, "gw"),
    })
    dump(proc7)
    check("发出了登录请求", len(RECEIVED) == 1, "收到 %d 条" % len(RECEIVED))
    if RECEIVED:
        check("wlan_ac_ip 用了 PK_AC_IP", RECEIVED[0].get("wlan_ac_ip") == WLAN_AC_IP,
              repr(RECEIVED[0].get("wlan_ac_ip")))
    check("没有发成空值", "AC认证失败" not in proc7.stdout)

    srv.shutdown()

    print("\n================ 结果 ================")
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
