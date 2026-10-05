#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试：隧道客户端收到 SIGTERM 必须**立刻**退出。

## 为什么要有这个测试（真实事故）

2026-10-04/05 线上观察：`systemctl stop/restart kebab-mcbridge-client` 每次都卡满
**90 秒**（systemd 的 `TimeoutStopSec` 默认值）才被 SIGKILL，而这 90 秒里隧道一直是断的。
四次重启四次如此。日志里客户端**明明已经打印了「收到信号 15，正在退出」**：

    [15:41:22] 收到信号 15，正在退出
    [15:41:22] systemd: Stopping kebab-mcbridge-client.service...
    [15:42:52] systemd: State 'stop-sigterm' timed out. Killing.     ← 90 秒后

根因不是"信号没收到"，而是收到了也没用：

1. 隧道活着时主线程阻塞在 `socket.recv()` 上（连接是 `settimeout(None)`）；
2. `_term` 里只调了 `cli.stop()` 设标志位，然后**正常返回**；
3. PEP 475 规定——信号处理器正常返回时，被中断的系统调用会**自动重启**；
4. 于是 `recv` 继续阻塞，`run_forever` 里的 `while not self.stopping` 永远轮不到；
5. 雪上加霜：`stop()` 关的是 UP 连接，主线程读的却是 DOWN 连接，关错了一根。

修法：让 `_term` 抛 `SystemExit`，被中断的 `recv` 当场抛出，进程立即退出
（`SystemExit` 继承 `BaseException`，不会被 `run_forever` 的 `except Exception` 吞掉）。

隧道**断开**时主线程在 `time.sleep(0.25)` 分片睡眠里，标志位能被看到，
所以那种情况一直是干净退出的——这正是 bug 间歇出现、容易漏掉的原因。

## 测试怎么复现

不需要联网、不需要真中继、不需要 TLS：起一个"接受连接后装死"的 TCP 服务端，
客户端会在 **WS 握手阶段阻塞在 `recv(1)` 上**——和线上是同一个阻塞点。

用法：
    python3 test-client-sigterm.py
    KEBAB_MCB_CLIENT=/usr/local/sbin/kebab-mcbridge-client.py python3 test-client-sigterm.py

退出码：0 = 全过，1 = 有失败，2 = 环境不满足（非 POSIX 或找不到客户端）。
"""

import os
import signal
import socket
import subprocess
import sys
import threading
import time

if os.name != "posix":
    print("本测试依赖 POSIX 信号语义，只能在 Linux/macOS/WSL 下运行。", file=sys.stderr)
    sys.exit(2)

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT = os.environ.get("KEBAB_MCB_CLIENT") or os.path.join(HERE, "..", "client", "mcbridge_client.py")
CLIENT = os.path.abspath(CLIENT)
PY = sys.executable or "python3"

if not os.path.isfile(CLIENT):
    print("找不到客户端脚本：%s" % CLIENT, file=sys.stderr)
    print("可用 KEBAB_MCB_CLIENT=/path/to/mcbridge_client.py 指定", file=sys.stderr)
    sys.exit(2)

# 修好之后应当 <1 秒退出；给 4 秒余量，避免繁忙机器上误报。
# 未修复时这里会稳定撞上 90 秒的 TimeoutStopSec，所以判据不会两边都通过。
BUDGET = float(os.environ.get("SIGTERM_BUDGET", "4"))

_passed = 0
_failed = 0


def ok(label, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print("  PASS  %s" % label)
    else:
        _failed += 1
        print("  FAIL  %s%s" % (label, ("    " + detail) if detail else ""))


def eq(label, got, want):
    ok(label, got == want, "期望 [%s] 实际 [%s]" % (want, got))


class FakePeer(threading.Thread):
    """接受连接后什么都不发（复现卡住的 WS 握手）；或立刻关闭（复现重连循环）。"""

    def __init__(self, close_immediately=False):
        threading.Thread.__init__(self)
        self.daemon = True
        self.close_immediately = close_immediately
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.port = self.srv.getsockname()[1]
        self.accepted = threading.Event()
        self.nconn = 0
        self._held = []
        self._stop = False

    def run(self):
        self.srv.settimeout(0.3)
        while not self._stop:
            try:
                c, _ = self.srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self.nconn += 1
            self.accepted.set()
            if self.close_immediately:
                try:
                    c.close()
                except OSError:
                    pass
            else:
                self._held.append(c)   # 攥着不放，一个字节都不发

    def shutdown(self):
        self._stop = True
        for c in self._held:
            try:
                c.close()
            except OSError:
                pass
        try:
            self.srv.close()
        except OSError:
            pass


def run_case(name, close_immediately, once, extra_wait=0.0):
    """起服务端 -> 起客户端 -> 等它进入阻塞 -> 发 SIGTERM -> 量退出耗时。"""
    print()
    print("[%s]" % name)
    peer = FakePeer(close_immediately=close_immediately)
    peer.start()
    proc = None
    try:
        cmd = [PY, "-u", CLIENT, "--plain", "--host", "127.0.0.1",
               "--port", str(peer.port), "--secret", "test-secret",
               "--session", "sigterm-test", "--mc", "127.0.0.1:1"]
        if once:
            cmd.append("--once")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)

        # 等到服务端确实接受了连接，说明客户端已经进到阻塞点
        got_conn = peer.accepted.wait(timeout=15)
        ok("客户端已建立连接（进入阻塞点）", got_conn,
           "15 秒内没收到连接，环境可能有问题")
        if extra_wait:
            time.sleep(extra_wait)

        t0 = time.time()
        proc.send_signal(signal.SIGTERM)
        try:
            rc = proc.wait(timeout=BUDGET + 30)   # 外层宽限只为拿到证据
            elapsed = time.time() - t0
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
            elapsed = time.time() - t0
            rc = None

        out = ""
        try:
            out = proc.stdout.read() or ""
        except Exception:
            pass

        ok("收到了 SIGTERM 并打了日志", "收到信号" in out,
           "客户端没有记录到信号，处理器可能没装上")
        ok("在 %.0f 秒内退出" % BUDGET, elapsed < BUDGET,
           "实际耗时 %.1f 秒（未修复时是 90 秒的 TimeoutStopSec）" % elapsed)
        eq("退出码 0（不是被信号打死）", rc, 0)
        print("        （实测退出耗时 %.2f 秒）" % elapsed)
        return out
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
        peer.shutdown()


def main():
    print("被测客户端：%s" % CLIENT)
    print("退出预算  ：%.0f 秒" % BUDGET)

    # 场景 1：阻塞在 WS 握手的 recv(1) 上 —— 这就是线上卡 90 秒的那个场景
    run_case("阻塞在握手的 recv 上时收到 SIGTERM", close_immediately=False, once=False)

    # 场景 2：服务端立刻关闭 -> 客户端报错 -> 进入 5 秒重连分片睡眠
    #         这条路径修之前也是好的，列在这里防止回归
    run_case("重连等待期间收到 SIGTERM", close_immediately=True, once=False,
             extra_wait=0.5)

    # 场景 3：--once 单次模式
    run_case("单次模式（--once）下收到 SIGTERM", close_immediately=False, once=True)

    print()
    print("================ 结果 ================")
    if _failed == 0:
        print("全部通过 ✅（%d 项）" % _passed)
        return 0
    print("失败 %d 项，通过 %d 项" % (_failed, _passed))
    return 1


if __name__ == "__main__":
    sys.exit(main())
