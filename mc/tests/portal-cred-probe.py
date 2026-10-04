#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性反证：门户到底校不校验账号？

必须用**全新未被占用**的 wlan_user_ip，否则会撞在"已经在线"上验不到凭据。

依次：
  1. 全新 IP + 不存在的账号 + 假密码   → 期望失败（证明会校验账号）
  2. 全新 IP + 真账号 + 真密码         → 期望成功（证明我们的凭据是对的）
  3. 全新 IP + 真账号 + 假密码         → 期望失败（证明会校验密码）

凭据从环境变量读，输出里真实密码不出现。

用法：
  sudo sh -c 'set -a; . /etc/kebab-portal-keeper.env; set +a; exec python3 credprobe.py'
"""

import http.client
import os
import sys
import urllib.parse

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/141.0.0.0 Safari/537.36"
HOST = os.environ.get("PK_PORTAL_HOST", "10.0.3.2")
PORT = int(os.environ.get("PK_API_PORT", "801"))
USER = os.environ.get("PK_USER", "")
PASS = os.environ.get("PK_PASS", "")
AC_IP = os.environ.get("PK_AC_IP", "")
MAC = os.environ.get("PK_WLAN_MAC", "000000000000")

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def call(wlan_ip, user, password):
    q = [
        ("user_account", ",0," + user),
        ("user_password", password),
        ("wlan_user_ip", wlan_ip),
        ("wlan_user_ipv6", ""),
        ("wlan_user_mac", MAC),
        ("wlan_ac_ip", AC_IP),
        ("wlan_ac_name", ""),
        ("login_method", "1"),
        ("terminal_type", "2"),
        ("callback", "dr1"),
        ("jsVersion", "4.1"),
        ("v", "1"),
        ("lang", "zh-cn"),
    ]
    url = "/eportal/portal/login?" + urllib.parse.urlencode(q)
    c = http.client.HTTPConnection(HOST, PORT, timeout=12)
    try:
        c.request("GET", url, headers={"User-Agent": UA, "Connection": "close"})
        r = c.getresponse()
        return r.status, r.read(8192).decode("utf-8", "replace").strip()
    finally:
        c.close()


# 三个各不相同的、未被使用过的校园网 IP（必须新鲜，否则会撞"已经在线"）
CASES = [
    ("① 全新IP + 假账号 + 假密码", "10.44.188.188", "9999999999", "definitely-not-the-password"),
    ("② 全新IP + 真账号 + 真密码", "10.44.177.177", USER, PASS),
    ("③ 全新IP + 真账号 + 假密码", "10.44.166.166", USER, "definitely-not-the-password"),
]

print("门户 %s:%d  账号长度 %d  密码长度 %d  wlan_ac_ip=%s\n"
      % (HOST, PORT, len(USER), len(PASS), AC_IP or "<空>"))
for tag, ip, u, p in CASES:
    try:
        st, body = call(ip, u, p)
    except Exception as e:
        print("  %-28s 异常 %s: %s" % (tag, type(e).__name__, e))
        continue
    body = body.replace(PASS, "<真密码>").replace("definitely-not-the-password", "<假密码>")
    print("  %-28s %-16s HTTP %s  %s" % (tag, ip, st, body[:200]))
