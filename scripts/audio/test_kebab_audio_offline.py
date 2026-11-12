#!/usr/bin/env python3
"""Offline negative tests for kebab-audio.

These exercise the guard, the budget, the write-ahead journal, the control-map
gate and the rollback drift rules with a JSON fixture backend. No ALSA device is
opened, no PCM is played, no microphone is captured, and nothing is installed.

Run on the device (Linux, /proc, flock, process groups) or anywhere for the
pure-function subset. Output is a JSON report suitable for guard-selftest.json.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time

RESULTS = []


def record(name, ok, detail=None):
    RESULTS.append({"case": name, "status": "pass" if ok else "fail",
                    "detail": detail or {}})
    print(("PASS " if ok else "FAIL ") + name + (
        "" if ok else "  <- " + json.dumps(detail, default=str)[:600]))


def boot_id():
    with open("/proc/sys/kernel/random/boot_id") as fh:
        return fh.read().strip()


def kernel_release():
    return os.uname().release


def boot_time():
    with open("/proc/uptime") as fh:
        return float(fh.read().split()[0])


class Env(object):
    def __init__(self, workdir, tool, policy_path):
        self.workdir = os.path.abspath(workdir)
        self.tool = os.path.abspath(tool)
        self.policy = os.path.abspath(policy_path)
        os.makedirs(self.workdir, exist_ok=True)
        self.py = sys.executable

    def case_dir(self, name):
        d = os.path.join(self.workdir, name)
        if os.path.isdir(d):
            shutil.rmtree(d)
        os.makedirs(d)
        return d

    def run(self, args, timeout=30, expect=None):
        cmd = [self.py, self.tool]
        if self.policy:
            cmd += ["--policy", self.policy]
        cmd += args
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, timeout=timeout)
        if expect is not None and p.returncode != expect:
            raise AssertionError(
                "expected rc=%s got %s for %s\nstdout=%s\nstderr=%s"
                % (expect, p.returncode, args, p.stdout[-1500:], p.stderr[-1500:]))
        return p


def write_fixtures(case, controls, control_map, manifest_actions, stop_actions,
                   pcm=None, faults=None):
    os.makedirs(case, exist_ok=True)
    fake = os.path.join(case, "fake")
    srv = os.path.join(case, "srv")
    run = os.path.join(case, "run")
    for d in (fake, srv, run):
        os.makedirs(d, exist_ok=True)
    with open(os.path.join(fake, "controls.json"), "w") as fh:
        json.dump(controls, fh)
    if pcm is not None:
        with open(os.path.join(fake, "pcm.json"), "w") as fh:
            json.dump(pcm, fh)
    if faults is not None:
        with open(os.path.join(fake, "faults.json"), "w") as fh:
            json.dump(faults, fh)
    with open(os.path.join(fake, "card.json"), "w") as fh:
        json.dump("card0:kebab-fake", fh)
    with open(os.path.join(srv, "control-map.json"), "w") as fh:
        json.dump(control_map, fh)
    manifest = {
        "schema": 1,
        "run_id": os.path.basename(case),
        "boot_id": boot_id(),
        "kernel_release": kernel_release(),
        "stop_actions": stop_actions,
        "accept": {"min_snr_db": 10.0, "min_abs_corr": 0.6},
        "actions": manifest_actions,
    }
    with open(os.path.join(srv, "trial-manifest.json"), "w") as fh:
        json.dump(manifest, fh)
    return fake, srv, run


def common_args(run_id, srv, run, fake):
    return ["--run", run_id, "--run-dir", run, "--srv-dir", srv,
            "--fake-root", fake]


DEFAULT_CONTROLS = {
    "TERT_MI2S_RX Audio Mixer MultiMedia1": {"type": "BOOL", "count": 1, "value": [0]},
    "VA_DEC0 Volume": {"type": "INT", "count": 2, "min": 0, "max": 255,
                       "value": [100, 100]},
    "Speaker Amp Switch": {"type": "BOOL", "count": 1, "value": [0]},
}

DEFAULT_MAP = {
    "schema": 1, "card": "card0:kebab-fake",
    "controls": {
        "TERT_MI2S_RX Audio Mixer MultiMedia1":
            {"numid": 12, "type": "BOOL", "count": 1, "value": [0]},
        "VA_DEC0 Volume": {"numid": 30, "type": "INT", "count": 2,
                           "min": 0, "max": 255, "value": [100, 100]},
        "Speaker Amp Switch": {"numid": 40, "type": "BOOL", "count": 1, "value": [0]},
    },
}

DEFAULT_STOP = [
    {"name": "TERT_MI2S_RX Audio Mixer MultiMedia1", "values": [0]},
    {"name": "Speaker Amp Switch", "values": [0]},
    {"name": "VA_DEC0 Volume", "values": [0, 0]},
]


def default_actions(case):
    wav = os.path.join(case, "cap.wav")
    return [
        {"id": "route-on", "kind": "control", "requires": ["silence"],
         "route": "speaker-tert-mi2s", "cost": {"route_variant": 1}, "timeout_s": 5,
         "writes": [{"name": "TERT_MI2S_RX Audio Mixer MultiMedia1", "values": [1]}]},
        {"id": "vol-set", "kind": "control", "cost": {"route_variant": 1},
         "timeout_s": 5,
         "writes": [{"name": "VA_DEC0 Volume", "values": [80, 80]}]},
        {"id": "unknown-control", "kind": "control", "cost": {"route_variant": 1},
         "timeout_s": 5,
         "writes": [{"name": "Nonexistent Volume", "values": [50, 50]}]},
        {"id": "out-of-range", "kind": "control", "cost": {"route_variant": 1},
         "timeout_s": 5,
         "writes": [{"name": "VA_DEC0 Volume", "values": [400, 400]}]},
        {"id": "capture-1", "kind": "capture", "requires": ["silence"],
         "cost": {"capture_seconds": 3, "capture_count": 1}, "timeout_s": 8,
         "seconds": 3, "min_continuous_s": 3.0, "path": wav},
        {"id": "slow-capture", "kind": "capture", "cost": {"capture_seconds": 2},
         "timeout_s": 0.5, "seconds": 2, "scenario": "slow",
         "min_continuous_s": 3.0, "path": wav},
        {"id": "play-1", "kind": "play", "requires": ["silence"],
         "cost": {"playback_nonzero_ms": 250, "playback_attempt": 1},
         "timeout_s": 8, "ms": 250, "channel": "left", "peak_dbfs": -48},
        {"id": "play-2", "kind": "play", "requires": ["silence"],
         "cost": {"playback_nonzero_ms": 250, "playback_attempt": 1},
         "timeout_s": 8, "ms": 250, "channel": "right", "peak_dbfs": -48},
        {"id": "play-3", "kind": "play", "requires": ["silence"],
         "cost": {"playback_nonzero_ms": 250, "playback_attempt": 1},
         "timeout_s": 8, "ms": 250, "channel": "both", "peak_dbfs": -48},
    ]


PCM_FIXTURE = {
    "default": {"mode": "noise", "amp": 0.01, "delay_s": 0.0},
    "slow": {"mode": "noise", "amp": 0.01, "delay_s": 1.6},
}


def start_guard(env, run_id, srv, run, fake, extra=None, timeout=40):
    args = ["guard"] + common_args(run_id, srv, run, fake)
    args += ["--manifest-sha", sha_manifest(srv)]
    args += (extra or [])
    cmd = [env.py, env.tool, "--policy", env.policy] + args
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)


def sha_manifest(srv):
    import hashlib

    h = hashlib.sha256()
    with open(os.path.join(srv, "trial-manifest.json"), "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def wait_armed(case, srv, run, seconds=6.0):
    p = os.path.join(run, "guard.json")
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with open(p) as fh:
                st = json.load(fh)
            if st.get("state") == "armed":
                return st
        except (OSError, ValueError):
            pass
        time.sleep(0.05)
    return None


def wait_file(path, seconds=8.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if os.path.exists(path):
            return True
        time.sleep(0.05)
    return False


def read_json(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def read_jsonl(path):
    out = []
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    except OSError:
        pass
    return out


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------


def t_selftest(env):
    case = env.case_dir("t01-selftest")
    p = env.run(["selftest"], expect=0)
    doc = json.loads(p.stdout)
    record("selftest.pure_functions", doc.get("verdict") == "pass",
           {"failed": doc.get("failed"), "total": doc.get("total")})


def t_guard_refuses_without_stop_actions(env):
    case = env.case_dir("t02-guard-no-stop")
    fake, srv, run = write_fixtures(case, DEFAULT_CONTROLS, DEFAULT_MAP,
                                    default_actions(case), [])
    p = env.run(["guard"] + common_args(os.path.basename(case), srv, run, fake)
                + ["--max-seconds", "3"], expect=2)
    st = read_json(os.path.join(run, "guard.json"), {})
    record("guard.refuses_without_stop_actions",
           st.get("state") == "refused" and "stop" in (st.get("reason") or ""),
           {"stderr": p.stderr.strip()[:300], "state": st.get("state")})


def t_guard_refuses_boot_id_drift(env):
    case = env.case_dir("t03-guard-bootid")
    fake, srv, run = write_fixtures(case, DEFAULT_CONTROLS, DEFAULT_MAP,
                                    default_actions(case), DEFAULT_STOP)
    p = env.run(["guard"] + common_args(os.path.basename(case), srv, run, fake)
                + ["--boot-id", "00000000-dead-beef-0000-000000000000",
                   "--max-seconds", "3"], expect=2)
    st = read_json(os.path.join(run, "guard.json"), {})
    record("guard.refuses_boot_id_drift",
           st.get("state") == "refused" and "boot" in (st.get("reason") or ""),
           {"stderr": p.stderr.strip()[:300]})


def t_guard_single_owner(env):
    case = env.case_dir("t04-guard-flock")
    fake, srv, run = write_fixtures(case, DEFAULT_CONTROLS, DEFAULT_MAP,
                                    default_actions(case), DEFAULT_STOP)
    run_id = os.path.basename(case)
    g1 = start_guard(env, run_id, srv, run, fake, ["--max-seconds", "6"])
    try:
        st = wait_armed(case, srv, run)
        if not st:
            record("guard.single_owner", False, {"why": "first guard never armed"})
            return
        p = env.run(["guard"] + common_args(run_id, srv, run, fake)
                    + ["--max-seconds", "3"])
        after = read_json(os.path.join(run, "guard.json"), {})
        record("guard.single_owner",
               p.returncode == 2 and after.get("state") == "refused",
               {"rc": p.returncode, "state": after.get("state"),
                "stderr": p.stderr.strip()[:200]})
    finally:
        g1.send_signal(signal.SIGTERM)
        try:
            g1.wait(timeout=8)
        except subprocess.TimeoutExpired:
            g1.kill()


def t_guard_lease_expiry_stops(env):
    case = env.case_dir("t05-guard-expiry")
    controls = json.loads(json.dumps(DEFAULT_CONTROLS))
    controls["TERT_MI2S_RX Audio Mixer MultiMedia1"]["value"] = [1]
    controls["Speaker Amp Switch"]["value"] = [1]
    controls["VA_DEC0 Volume"]["value"] = [200, 210]
    fake, srv, run = write_fixtures(case, controls, DEFAULT_MAP,
                                    default_actions(case), DEFAULT_STOP)
    run_id = os.path.basename(case)
    g = start_guard(env, run_id, srv, run, fake, ["--max-seconds", "8"])
    try:
        if not wait_armed(case, srv, run):
            record("guard.lease_expiry_stops", False, {"why": "never armed"})
            return
        # One heartbeat, then die: this is what a killed agent looks like.
        lease = {"run_id": run_id, "boot_id": boot_id(), "manifest_sha": sha_manifest(srv),
                 "card": "card0:kebab-fake", "route": "speaker-tert-mi2s",
                 "pgid": None, "heartbeat_boot_time": boot_time()}
        with open(os.path.join(run, "lease.json"), "w") as fh:
            json.dump(lease, fh)
        rc = g.wait(timeout=12)
        events = read_jsonl(os.path.join(srv, "guard-events.jsonl"))
        stop = [e for e in events if e.get("event") == "stop"]
        after = read_json(os.path.join(fake, "controls.json"), {})
        ok = (rc == 0 and stop and stop[-1].get("reason") == "lease_expired"
              and stop[-1].get("stop_ok") is True
              and after.get("TERT_MI2S_RX Audio Mixer MultiMedia1", {})
              .get("value") == [0]
              and after.get("VA_DEC0 Volume", {}).get("value") == [0, 0])
        record("guard.lease_expiry_stops", ok,
               {"rc": rc, "reason": stop[-1].get("reason") if stop else None,
                "stop_ok": stop[-1].get("stop_ok") if stop else None,
                "after": {k: v.get("value") for k, v in after.items()}})
    finally:
        if g.poll() is None:
            g.kill()


def t_guard_kills_owned_pgid(env):
    case = env.case_dir("t06-guard-killpg")
    fake, srv, run = write_fixtures(case, DEFAULT_CONTROLS, DEFAULT_MAP,
                                    default_actions(case), DEFAULT_STOP)
    run_id = os.path.basename(case)
    child = subprocess.Popen(["sleep", "120"], start_new_session=True)
    g = start_guard(env, run_id, srv, run, fake, ["--max-seconds", "8"])
    try:
        if not wait_armed(case, srv, run):
            record("guard.kills_owned_pgid", False, {"why": "never armed"})
            return
        lease = {"run_id": run_id, "boot_id": boot_id(), "manifest_sha": sha_manifest(srv),
                 "card": "card0:kebab-fake", "route": "speaker-tert-mi2s",
                 "pgid": child.pid, "heartbeat_boot_time": boot_time()}
        with open(os.path.join(run, "lease.json"), "w") as fh:
            json.dump(lease, fh)
        rc = g.wait(timeout=12)
        time.sleep(0.2)
        dead = child.poll() is not None
        events = read_jsonl(os.path.join(srv, "guard-events.jsonl"))
        stop = [e for e in events if e.get("event") == "stop"][-1:]
        record("guard.kills_owned_pgid", rc == 0 and dead,
               {"rc": rc, "child_alive": child.poll() is None,
                "kill": stop[0].get("kill") if stop else None})
    finally:
        if child.poll() is None:
            child.kill()
        if g.poll() is None:
            g.kill()


def t_guard_stop_not_proven(env):
    case = env.case_dir("t07-guard-mute-fail")
    controls = json.loads(json.dumps(DEFAULT_CONTROLS))
    controls["VA_DEC0 Volume"]["value"] = [200, 210]
    fake, srv, run = write_fixtures(case, controls, DEFAULT_MAP,
                                    default_actions(case), DEFAULT_STOP,
                                    faults={"set_fail": ["VA_DEC0 Volume"]})
    run_id = os.path.basename(case)
    g = start_guard(env, run_id, srv, run, fake, ["--max-seconds", "8"])
    try:
        if not wait_armed(case, srv, run):
            record("guard.stop_not_proven", False, {"why": "never armed"})
            return
        lease = {"run_id": run_id, "boot_id": boot_id(), "manifest_sha": sha_manifest(srv),
                 "card": "card0:kebab-fake", "route": "speaker-tert-mi2s",
                 "pgid": None, "heartbeat_boot_time": boot_time()}
        with open(os.path.join(run, "lease.json"), "w") as fh:
            json.dump(lease, fh)
        rc = g.wait(timeout=12)
        events = read_jsonl(os.path.join(srv, "guard-events.jsonl"))
        stop = [e for e in events if e.get("event") == "stop"][-1:]
        record("guard.stop_not_proven",
               rc == 3 and stop and stop[0].get("stop_ok") is False,
               {"rc": rc, "stop_ok": stop[0].get("stop_ok") if stop else None})
    finally:
        if g.poll() is None:
            g.kill()


def t_guard_run_deadline(env):
    case = env.case_dir("t08-guard-deadline")
    fake, srv, run = write_fixtures(case, DEFAULT_CONTROLS, DEFAULT_MAP,
                                    default_actions(case), DEFAULT_STOP)
    run_id = os.path.basename(case)
    g = start_guard(env, run_id, srv, run, fake, ["--max-seconds", "1.0"])
    try:
        if not wait_armed(case, srv, run):
            record("guard.run_deadline", False, {"why": "never armed"})
            return
        rc = g.wait(timeout=10)
        events = read_jsonl(os.path.join(srv, "guard-events.jsonl"))
        stop = [e for e in events if e.get("event") == "stop"][-1:]
        record("guard.run_deadline",
               rc == 0 and stop and stop[0].get("reason") == "run_deadline",
               {"rc": rc, "reason": stop[0].get("reason") if stop else None})
    finally:
        if g.poll() is None:
            g.kill()


def t_runner_refuses_without_guard(env):
    case = env.case_dir("t09-run-no-guard")
    fake, srv, run = write_fixtures(case, DEFAULT_CONTROLS, DEFAULT_MAP,
                                    default_actions(case), DEFAULT_STOP)
    p = env.run(["run"] + common_args(os.path.basename(case), srv, run, fake)
                + ["--action", "route-on"], expect=2)
    record("runner.refuses_without_armed_guard",
           "guard not armed" in p.stderr, {"stderr": p.stderr.strip()[:300]})


def t_runner_refuses_unknown_action(env):
    case = env.case_dir("t10-run-unknown-action")
    fake, srv, run = write_fixtures(case, DEFAULT_CONTROLS, DEFAULT_MAP,
                                    default_actions(case), DEFAULT_STOP)
    p = env.run(["run"] + common_args(os.path.basename(case), srv, run, fake)
                + ["--action", "does-not-exist"], expect=2)
    record("runner.refuses_unknown_action",
           "not in manifest" in p.stderr, {"stderr": p.stderr.strip()[:200]})


def t_runner_control_gate(env):
    case = env.case_dir("t11-run-control-gate")
    fake, srv, run = write_fixtures(case, DEFAULT_CONTROLS, DEFAULT_MAP,
                                    default_actions(case), DEFAULT_STOP)
    args = common_args(os.path.basename(case), srv, run, fake)
    p1 = env.run(["run"] + args + ["--action", "unknown-control"], expect=2)
    p2 = env.run(["run"] + args + ["--action", "out-of-range"], expect=2)
    after = read_json(os.path.join(fake, "controls.json"), {})
    untouched = after.get("VA_DEC0 Volume", {}).get("value") == [100, 100]
    journal = read_jsonl(os.path.join(srv, "journal.jsonl"))
    refusals = [j for j in journal if j.get("event") == "control_write_refused"]
    record("runner.control_gate",
           "not in frozen control-map" in p1.stderr
           and "outside audited range" in p2.stderr
           and untouched and len(refusals) == 2,
           {"p1": p1.stderr.strip()[:160], "p2": p2.stderr.strip()[:160],
            "untouched": untouched, "refusals": len(refusals)})


def t_runner_budget_and_deadline(env):
    case = env.case_dir("t12-run-budget")
    fake, srv, run = write_fixtures(case, DEFAULT_CONTROLS, DEFAULT_MAP,
                                    default_actions(case), DEFAULT_STOP,
                                    pcm=PCM_FIXTURE)
    run_id = os.path.basename(case)
    g = start_guard(env, run_id, srv, run, fake, ["--max-seconds", "40"])
    try:
        if not wait_armed(case, srv, run):
            record("runner.budget_and_deadline", False, {"why": "never armed"})
            return
        args = common_args(run_id, srv, run, fake)
        r1 = env.run(["run"] + args + ["--action", "play-1"], expect=0)
        r2 = env.run(["run"] + args + ["--action", "play-2"], expect=0)
        r3 = env.run(["run"] + args + ["--action", "play-3"], expect=2)
        slow = env.run(["run"] + args + ["--action", "slow-capture"], expect=4)
        slow_doc = json.loads(slow.stdout) if slow.stdout.strip() else {}
        ev = read_json(os.path.join(srv, "evidence", "action-slow-capture.json"), {})
        journal = read_jsonl(os.path.join(srv, "journal.jsonl"))
        charged = {}
        for j in journal:
            if j.get("state") in ("RESERVED", "APPLIED", "VERIFIED"):
                for k, v in (j.get("cost") or {}).items():
                    charged[k] = charged.get(k, 0) + v
        record("runner.budget_refuses_over_cap",
               r3.returncode == 2 and "playback_attempt" in r3.stderr,
               {"stderr": r3.stderr.strip()[:200], "charged": charged})
        record("runner.hard_deadline_aborts",
               slow.returncode == 4 and ev.get("hard_deadline_hit") is True,
               {"rc": slow.returncode, "elapsed": ev.get("elapsed_s"),
                "deadline": ev.get("hard_deadline_s")})
        status = env.run(["status"] + common_args(run_id, srv, run, fake), expect=0)
        st = json.loads(status.stdout)
        uncertain_names = [a for a, _ in st.get("uncertain_hardware", [])]
        record("runner.write_ahead_charges_failures",
               any(n.startswith("slow-capture") for n in uncertain_names)
               and st.get("charged", {}).get("playback_attempt", 0) >= 2,
               {"uncertain": uncertain_names[:5], "charged": st.get("charged")})
    finally:
        g.send_signal(signal.SIGTERM)
        try:
            g.wait(timeout=10)
        except subprocess.TimeoutExpired:
            g.kill()


def t_rollback_restore_and_drift(env):
    case = env.case_dir("t13-rollback")
    fake, srv, run = write_fixtures(case, DEFAULT_CONTROLS, DEFAULT_MAP,
                                    default_actions(case), DEFAULT_STOP)
    run_id = os.path.basename(case)
    g = start_guard(env, run_id, srv, run, fake, ["--max-seconds", "40"])
    try:
        if not wait_armed(case, srv, run):
            record("rollback.restore_and_drift", False, {"why": "never armed"})
            return
        args = common_args(run_id, srv, run, fake)
        env.run(["run"] + args + ["--action", "route-on"], expect=0)
        env.run(["run"] + args + ["--action", "vol-set"], expect=0)
        after_run = read_json(os.path.join(fake, "controls.json"), {})
        # somebody else moves the volume afterwards: rollback must not clobber it
        state = json.loads(json.dumps(after_run))
        state["VA_DEC0 Volume"]["value"] = [50, 50]
        with open(os.path.join(fake, "controls.json"), "w") as fh:
            json.dump(state, fh)
        p = env.run(["rollback"] + args, expect=0)
        rep = json.loads(p.stdout)
        final = read_json(os.path.join(fake, "controls.json"), {})
        steps = {s.get("name"): s for s in rep.get("steps", []) if s.get("kind") == "control"}
        ok_route = (steps.get("TERT_MI2S_RX Audio Mixer MultiMedia1", {})
                    .get("decision") == "restore"
                    and final.get("TERT_MI2S_RX Audio Mixer MultiMedia1", {})
                    .get("value") == [0])
        ok_drift = (steps.get("VA_DEC0 Volume", {}).get("decision") == "skip_drift"
                    and final.get("VA_DEC0 Volume", {}).get("value") == [50, 50])
        record("rollback.restores_owned_change", ok_route,
               {"decision": steps.get("TERT_MI2S_RX Audio Mixer MultiMedia1", {})
                .get("decision"), "final": final.get(
                    "TERT_MI2S_RX Audio Mixer MultiMedia1", {}).get("value")})
        record("rollback.skips_drift", ok_drift,
               {"decision": steps.get("VA_DEC0 Volume", {}).get("decision"),
                "final": final.get("VA_DEC0 Volume", {}).get("value")})
        record("rollback.report_verdict", rep.get("verdict") == "pass",
               {"verdict": rep.get("verdict"), "failed": rep.get("failed")})
    finally:
        g.send_signal(signal.SIGTERM)
        try:
            g.wait(timeout=10)
        except subprocess.TimeoutExpired:
            g.kill()


def t_rollback_budget(env):
    case = env.case_dir("t14-rollback-budget")
    fake, srv, run = write_fixtures(case, DEFAULT_CONTROLS, DEFAULT_MAP,
                                    default_actions(case), DEFAULT_STOP)
    run_id = os.path.basename(case)
    args = common_args(run_id, srv, run, fake)
    env.run(["rollback"] + args, expect=0)
    env.run(["rollback"] + args, expect=0)
    third = env.run(["rollback"] + args, expect=2)
    record("rollback.refuses_beyond_transaction_cap",
           "rollback" in third.stderr and "cap" in third.stderr,
           {"stderr": third.stderr.strip()[:200]})


def t_acoustic_from_capture(env):
    """A digitally silent capture must not become a physical pass."""
    case = env.case_dir("t15-acoustic")
    pcm = {"default": {"mode": "silence"}, "tone": {"mode": "tone", "amp": 0.2}}
    fake, srv, run = write_fixtures(case, DEFAULT_CONTROLS, DEFAULT_MAP,
                                    default_actions(case), DEFAULT_STOP, pcm=pcm)
    run_id = os.path.basename(case)
    g = start_guard(env, run_id, srv, run, fake, ["--max-seconds", "30"])
    try:
        if not wait_armed(case, srv, run):
            record("acoustic.silent_is_unknown", False, {"why": "never armed"})
            return
        args = common_args(run_id, srv, run, fake)
        env.run(["run"] + args + ["--action", "capture-1"], expect=0)
        ev = read_json(os.path.join(srv, "evidence", "action-capture-1.json"), {})
        peak = ev.get("peak_db")
        sys.path.insert(0, os.path.dirname(env.tool))
        import kebab_audio_core as core

        stats = (ev.get("stats") or [{}])[0]
        verdict, why = core.acoustic_verdict(stats, -90.0, 0.0)
        record("acoustic.silent_is_unknown",
               peak is not None and peak <= -120.0 and verdict == "unknown",
               {"peak_db": peak, "verdict": verdict, "why": why})
        # and a real tone would pass, so the test above is not vacuous
        stats2 = dict(stats, rms_db=-40.0, peak_db=-30.0, flat=False,
                      nonzero=1000, total_samples=1000, clipped=0)
        v2, _ = core.acoustic_verdict(stats2, -90.0, 0.9)
        record("acoustic.tone_passes_when_present", v2 == "pass", {"verdict": v2})
    finally:
        g.send_signal(signal.SIGTERM)
        try:
            g.wait(timeout=10)
        except subprocess.TimeoutExpired:
            g.kill()


def t_probe_and_acoustic(env):
    """A probe must leave correlatable evidence, and only then can physical pass."""
    case = env.case_dir("t16-probe")
    pcm = {"default": {"mode": "noise", "amp": 0.004},
           "loud": {"mode": "tone", "amp": 0.30}}
    actions = default_actions(case) + [
        {"id": "noise-cap", "kind": "capture", "role": "noise",
         "cost": {"capture_seconds": 2, "capture_count": 1}, "timeout_s": 8,
         "seconds": 2, "min_continuous_s": 1.0,
         "path": os.path.join(case, "noise.wav")},
        {"id": "probe-left", "kind": "probe", "label": "left",
         "scenario": "loud", "channel": "left", "ms": 250, "peak_dbfs": -48.0,
         "capture_seconds": 2.0,
         "cost": {"playback_nonzero_ms": 250, "playback_attempt": 1,
                  "capture_seconds": 2},
         "timeout_s": 8,
         "ref_path": os.path.join(case, "ref-l.wav"),
         "capture_path": os.path.join(case, "cap-l.wav")},
        {"id": "acoustic", "kind": "verify", "check": "acoustic",
         "cost": {"route_variant": 0} , "timeout_s": 8,
         "accept": {"min_snr_db": 10.0, "min_abs_corr": 0.6}},
    ]
    actions[-1]["cost"] = {"route_variant": 1}
    fake, srv, run = write_fixtures(case, DEFAULT_CONTROLS, DEFAULT_MAP,
                                    actions, DEFAULT_STOP, pcm=pcm)
    run_id = os.path.basename(case)
    g = start_guard(env, run_id, srv, run, fake, ["--max-seconds", "40"])
    try:
        if not wait_armed(case, srv, run):
            record("probe.leaves_evidence", False, {"why": "never armed"})
            return
        args = common_args(run_id, srv, run, fake)
        env.run(["run"] + args + ["--action", "noise-cap"], expect=0)
        p = env.run(["run"] + args + ["--action", "probe-left"], expect=0)
        pev = read_json(os.path.join(srv, "evidence", "action-probe-left.json"), {})
        pe = read_json(os.path.join(srv, "probe-evidence.json"), {})
        record("probe.leaves_evidence",
               pev.get("corr") is not None and abs(pev.get("corr", 0)) > 0.6
               and pev.get("burst_stats", {}).get("peak_db", -200) > -60
               and pe.get("noise_rms_db") is not None
               and "left" in (pe.get("probes") or {}),
               {"corr": pev.get("corr"), "peak_db": pev.get("peak_db"),
                "noise": pe.get("noise_rms_db"),
                "probe_labels": sorted((pe.get("probes") or {}).keys())})
        v = env.run(["run"] + args + ["--action", "acoustic"], expect=0)
        aev = read_json(os.path.join(srv, "evidence", "action-acoustic.json"), {})
        record("acoustic.passes_with_observable_tone",
               aev.get("verdict") == "pass",
               {"verdict": aev.get("verdict"), "channels": aev.get("channels"),
                "problems": aev.get("problems")})
    finally:
        g.send_signal(signal.SIGTERM)
        try:
            g.wait(timeout=10)
        except subprocess.TimeoutExpired:
            g.kill()


def t_acoustic_unknown_without_noise(env):
    case = env.case_dir("t17-acoustic-no-noise")
    actions = default_actions(case) + [
        {"id": "acoustic", "kind": "verify", "check": "acoustic",
         "cost": {"route_variant": 1}, "timeout_s": 8}]
    fake, srv, run = write_fixtures(case, DEFAULT_CONTROLS, DEFAULT_MAP,
                                    actions, DEFAULT_STOP)
    # probes exist but no quiet baseline was ever captured
    with open(os.path.join(srv, "probe-evidence.json"), "w") as fh:
        json.dump({"probes": {"left": {"burst_stats": {"rms_db": -40.0, "peak_db": -30.0,
                                                       "clipped": 0, "flat": False,
                                                       "nonzero": 100, "total_samples": 100},
                               "corr": 0.9, "peak_db": -30.0, "channel": "left"}}}, fh)
    run_id = os.path.basename(case)
    g = start_guard(env, run_id, srv, run, fake, ["--max-seconds", "30"])
    try:
        if not wait_armed(case, srv, run):
            record("acoustic.unknown_without_noise_baseline", False,
                   {"why": "never armed"})
            return
        args = common_args(run_id, srv, run, fake)
        env.run(["run"] + args + ["--action", "acoustic"], expect=0)
        aev = read_json(os.path.join(srv, "evidence", "action-acoustic.json"), {})
        record("acoustic.unknown_without_noise_baseline",
               aev.get("verdict") == "unknown", {"verdict": aev.get("verdict")})
    finally:
        g.send_signal(signal.SIGTERM)
        try:
            g.wait(timeout=10)
        except subprocess.TimeoutExpired:
            g.kill()


TESTS = [
    t_selftest,
    t_guard_refuses_without_stop_actions,
    t_guard_refuses_boot_id_drift,
    t_guard_single_owner,
    t_guard_lease_expiry_stops,
    t_guard_kills_owned_pgid,
    t_guard_stop_not_proven,
    t_guard_run_deadline,
    t_runner_refuses_without_guard,
    t_runner_refuses_unknown_action,
    t_runner_control_gate,
    t_runner_budget_and_deadline,
    t_rollback_restore_and_drift,
    t_rollback_budget,
    t_acoustic_from_capture,
    t_probe_and_acoustic,
    t_acoustic_unknown_without_noise,
]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool", default=os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "kebab_audio.py"))
    ap.add_argument("--policy", required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--only", default=None)
    args = ap.parse_args(argv)
    env = Env(args.workdir, args.tool, args.policy)
    only = set(args.only.split(",")) if args.only else None
    for fn in TESTS:
        if only and fn.__name__ not in only:
            continue
        try:
            fn(env)
        except AssertionError as exc:
            record(fn.__name__, False, {"assertion": str(exc)[:800]})
        except Exception as exc:  # noqa: BLE001
            record(fn.__name__, False, {"exception": repr(exc)[:800]})
    failed = [r for r in RESULTS if r["status"] != "pass"]
    doc = {
        "schema": 1,
        "tool": "kebab-audio offline negative tests",
        "boot_id": boot_id(),
        "kernel_release": kernel_release(),
        "python": sys.version.split()[0],
        "workdir": env.workdir,
        "wall_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cases": RESULTS,
        "total": len(RESULTS),
        "failed": len(failed),
        "verdict": "pass" if not failed else "fail",
        "note": "fixture backend only; no ALSA device, no PCM, no microphone",
    }
    text = json.dumps(doc, indent=2, sort_keys=True)
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
    print(text)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
