#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校园网门户接口探针（一次性诊断工具，不是保活组件）。

用途：把候选的注销 / 登录接口依次打一遍，打印每个的真实响应，
从中找出这套 Dr.COM 门户真正在接受哪一种。

为什么需要它：从门户自己的 a40.js 里逆出来的路径是 /drcom/login（端口 80），
但 2026-10-04 实测在**已认证**状态下它返回 404 —— 无法判断是
  (a) 路径不对，还是
  (b) 预认证花园只在未认证时才存在。
所以先找到能用的注销接口，把网踢下线，再在离线状态下试登录。

凭据从环境变量 PK_USER / PK_PASS 读（由调用方 source 那个 600 的 env 文件），
打印时密码一律打码。

用法：
  sudo sh -c 'set -a; . /etc/kebab-portal-keeper.env; set +a; exec python3 portal-api-probe.py'
"""

import http.client
import json
import os
import random
import ssl
import sys
import urllib.parse

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

HOST = os.environ.get("PK_PORTAL_HOST", "10.0.3.2")
USER = os.environ.get("PK_USER", "")
PASS = os.environ.get("PK_PASS", "")

WLAN_USER_IP = None          # 探测时动态填
AC_IP = "172.16.254.2"       # 门户页 302 里给的 wlanacip
AC_NAME = ""


def rid():
    return random.randint(100000, 999999)


def jsonp_tail():
    return "callback=dr%d&jsVersion=4.X&v=%d&lang=zh" % (rid(), rid())


def http_get(url, timeout=10):
    u = urllib.parse.urlsplit(url)
    conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
    try:
        conn.request("GET", (u.path or "/") + ("?" + u.query if u.query else ""),
                     headers={"User-Agent": UA,
                              "Referer": "http://%s/a79.htm" % HOST,
                              "Connection": "close"})
        resp = conn.getresponse()
        return resp.status, resp.read(65536).decode("utf-8", "replace")
    finally:
        conn.close()


def online():
    """HTTPS 证书校验式在线判断（和保活脚本同一套逻辑）。"""
    try:
        ctx = ssl.create_default_context()
        c = http.client.HTTPSConnection("www.baidu.com", 443, timeout=6, context=ctx)
        c.request("GET", "/", headers={"User-Agent": UA, "Connection": "close"})
        r = c.getresponse()
        r.read(512)
        c.close()
        return True
    except Exception:
        return False


def mask(s):
    if not s:
        return s
    out = s.replace(PASS, "<PASS>")
    out = out.replace(urllib.parse.quote(PASS, safe=""), "<PASS>")
    return out


def show(tag, status, body):
    body = mask(body).strip().replace("\n", " ")
    print("  %-46s HTTP %s  %s" % (tag, status, body[:220]))


def cur_ip():
    """拿当前 wlan0 的 IPv4。"""
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.0.3.2", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""


def main():
    global WLAN_USER_IP
    WLAN_USER_IP = cur_ip() or "0.0.0.0"
    print("门户 %s   本机 wlan IP %s   账号长度 %d" % (HOST, WLAN_USER_IP, len(USER)))
    print("起始在线状态: %s\n" % online())

    # ---------------- 注销候选 ----------------
    print("======== 注销候选 ========")
    logout_urls = [
        ("L1 /drcom/logout (裸)",
         "http://%s/drcom/logout?%s" % (HOST, jsonp_tail())),
        ("L2 /drcom/logout + 网关参数",
         "http://%s/drcom/logout?wlanuserip=%s&wlanacip=%s&wlanacname=%s&%s"
         % (HOST, WLAN_USER_IP, AC_IP, AC_NAME, jsonp_tail())),
        ("L3 :801 ACSetting&a=Logout",
         "http://%s:801/eportal/?c=ACSetting&a=Logout&ver=1.0&url=drappal&%s"
         % (HOST, jsonp_tail())),
        ("L4 :801 /eportal/portal/logout",
         "http://%s:801/eportal/portal/logout?%s" % (HOST, jsonp_tail())),
    ]
    for tag, url in logout_urls:
        try:
            st, body = http_get(url)
        except Exception as e:
            print("  %-46s 异常 %s: %s" % (tag, type(e).__name__, e))
            continue
        show(tag, st, body)
        if 'result":1' in body or "result':1" in body or "��¼�ɹ�" in body or "注销成功" in body:
            print("  ^^^ 这个看起来成功了，停止尝试注销")
            break

    import time
    time.sleep(4)
    is_on = online()
    print("\n注销后在线状态: %s" % is_on)
    if is_on:
        print(">>> 仍然是『在线』，说明注销没生效；下面的登录测试结果不可信。")
    else:
        print(">>> 已离线，进入登录测试。")

    # ---------------- 登录候选 ----------------
    print("\n======== 登录候选 ========")
    gw = "wlanuserip=%s&wlanacip=%s&wlanacname=%s" % (WLAN_USER_IP, AC_IP, AC_NAME)
    base_fields = ("DDDDD=%s&upass=%s&0MKKey=123456&R1=&R2=&R3=&R6=0&para=&v6ip="
                   % (urllib.parse.quote(USER, safe=""), urllib.parse.quote(PASS, safe="")))

    login_urls = [
        ("G1 /drcom/login (a40.js 原生)",
         "http://%s/drcom/login?%s&%s&terminal_type=2&lang=zh&%s"
         % (HOST, base_fields, gw, jsonp_tail())),
        ("G2 :801 ACSetting&a=Login",
         "http://%s:801/eportal/?c=ACSetting&a=Login&url=drappal&%s&%s&%s"
         % (HOST, base_fields, gw, jsonp_tail())),
        ("G3 :801 Portal&a=login (哆点新版)",
         "http://%s:801/eportal/?c=Portal&a=login&login_method=1&user_account=,0,%s"
         "&user_password=%s&%s&jsVersion=4.1&terminal_type=1&lang=zh-cn&v=%d"
         % (HOST, urllib.parse.quote(USER, safe=""), urllib.parse.quote(PASS, safe=""),
            gw, rid())),
        ("G4 :801 /eportal/portal/login",
         "http://%s:801/eportal/portal/login?%s&%s&terminal_type=2&lang=zh&%s"
         % (HOST, base_fields, gw, jsonp_tail())),
    ]

    for tag, url in login_urls:
        try:
            st, body = http_get(url)
        except Exception as e:
            print("  %-46s 异常 %s: %s" % (tag, type(e).__name__, e))
            continue
        show(tag, st, body)
        import time
        time.sleep(2)
        if online():
            print("  ^^^ 这个**真的把网打通了**，就是它！")
            break

    print("\n最终在线状态: %s" % online())
    return 0


if __name__ == "__main__":
    sys.exit(main())
