#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性诊断：门户判定"已经在线"用的是 wlan_user_ip 参数，还是请求来源 IP？

如果是参数，就可以用一个未占用的 IP 绕过 already-online 检查，
从而在不真正下线的前提下，逼门户走完整的账号认证分支
（这样就能验证 账号格式 + 密码 + 参数集 是否真的正确）。

凭据从环境变量读，输出里密码一律不出现。

用法：
  sudo sh -c 'set -a; . /etc/kebab-portal-keeper.env; set +a; exec python3 ipprobe.py'
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

# 依次试：真实 IP / 明显未占用的 IP / 空
REAL_IP = os.environ.get("PK_REAL_IP", "")
CANDIDATES = [REAL_IP, "10.99.99.99", "10.44.199.199", ""]

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def call(wlan_ip, user=None, password=None):
    q = [
        ("user_account", ",0," + (USER if user is None else user)),
        ("user_password", PASS if password is None else password),
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


print("门户 %s:%d   账号长度 %d   密码长度 %d   wlan_ac_ip=%s"
      % (HOST, PORT, len(USER), len(PASS), AC_IP or "<空>"))
print("\n--- A. 换 wlan_user_ip，凭据用真的 ---")
for ip in CANDIDATES:
    label = ip if ip else "<空>"
    try:
        st, body = call(ip)
    except Exception as e:
        print("  wlan_user_ip=%-16s 异常 %s: %s" % (label, type(e).__name__, e))
        continue
    body = body.replace(PASS, "<PASS>")
    print("  wlan_user_ip=%-16s HTTP %s  %s" % (label, st, body[:230]))

print("\n--- B. 反证：固定用同一个未在线 IP，换成假凭据 ---")
for tag, u, p in [
    ("真账号+假密码", None, "definitely-not-the-password"),
    ("假账号+真密码", "0000000000", None),
    ("假账号+假密码", "0000000000", "definitely-not-the-password"),
]:
    try:
        st, body = call("10.44.199.199", user=u, password=p)
    except Exception as e:
        print("  %-16s 异常 %s: %s" % (tag, type(e).__name__, e))
        continue
    body = body.replace(PASS, "<PASS>").replace("definitely-not-the-password", "<FAKE>")
    print("  %-16s HTTP %s  %s" % (tag, st, body[:230]))
