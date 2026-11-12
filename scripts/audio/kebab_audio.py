#!/usr/bin/env python3
"""kebab-audio -- guarded, budgeted, reversible r4 host-audio executor.

Subcommands (all read-only unless the name says otherwise):

  inventory   read-only discovery -> baseline JSON on stdout
  plan        freeze a trial manifest from a control map + policy (no hardware)
  guard       device-local silence owner; the only thing allowed to stop audio
  run         execute exactly one manifest action under the armed guard
  verify      turn evidence + journal into per-capability verdicts
  rollback    reverse only this run's owned, still-equal audio writes
  status      print run state
  selftest    pure-function self checks (no hardware)

Every write path is gated on: frozen manifest, armed guard, fresh lease,
declared budget class, and a control map entry. Nothing here returns a
success code it did not observe.
"""
from __future__ import annotations

import errno
import json
import os
import signal
import subprocess
import sys
import time

try:  # the guard needs an exclusive lock; refuse rather than pretend on Windows
    import fcntl
except ImportError:  # pragma: no cover - exercised only off-device
    fcntl = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kebab_audio_core as core

DEFAULT_POLICY = os.environ.get(
    "KEBAB_AUDIO_POLICY",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio-agent-policy.json"),
)

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_STOP_FAILED = 3
EXIT_HARD_FAIL = 4


def say(msg):
    sys.stderr.write(msg.rstrip() + "\n")
    sys.stderr.flush()


# --------------------------------------------------------------------------
# backends
# --------------------------------------------------------------------------


class FakeControls(object):
    """JSON-file control store used by the offline negative tests.

    Fault injection lives in faults.json:
      {"set_fail": [...], "get_fail": [...], "ignore_writes": [...]}
    """

    def __init__(self, root):
        self.root = root
        self.path = os.path.join(root, "controls.json")
        self.fault_path = os.path.join(root, "faults.json")

    def _load(self):
        return core.load_json(self.path, {}) or {}

    def _faults(self):
        return core.load_json(self.fault_path, {}) or {}

    def get(self, name):
        if name in (self._faults().get("get_fail") or []):
            raise RuntimeError("injected get failure for %s" % name)
        entry = self._load().get(name)
        if entry is None:
            return None
        return list(entry.get("value") or [])

    def info(self, name):
        return self._load().get(name)

    def set(self, name, values):
        faults = self._faults()
        if name in (faults.get("set_fail") or []):
            raise RuntimeError("injected set failure for %s" % name)
        state = self._load()
        entry = state.get(name)
        if entry is None:
            raise RuntimeError("fake control %s does not exist" % name)
        if name in (faults.get("ignore_writes") or []):
            return  # silently ineffective, like a misnamed control
        entry["value"] = list(values)
        core.dump_json(self.path, state)

    def list_controls(self):
        return sorted(self._load().keys())


class AlsaControls(object):
    def __init__(self, card):
        self.card = card

    def _cget(self, name):
        p = core.run(["amixer", "-c", str(self.card), "cget", "name=" + name], timeout=10)
        if p.returncode != 0:
            raise RuntimeError("amixer cget %s failed: %s" % (name, p.stderr.strip()))
        return p.stdout

    def get(self, name):
        info = core.parse_amixer_cget(self._cget(name))
        if not info["values"]:
            raise RuntimeError("amixer cget %s returned no values" % name)
        return info["values"]

    def info(self, name):
        info = core.parse_amixer_cget(self._cget(name))
        info["name"] = name
        return info

    def set(self, name, values):
        cmd = ["amixer", "-c", str(self.card), "cset", "name=" + name]
        cmd += [str(v) for v in values]
        p = core.run(cmd, timeout=10)
        if p.returncode != 0:
            raise RuntimeError("amixer cset %s failed: %s" % (name, p.stderr.strip()))

    def list_controls(self):
        p = core.run(["amixer", "-c", str(self.card), "scontrols"], timeout=10)
        if p.returncode != 0:
            raise RuntimeError("amixer scontrols failed: %s" % p.stderr.strip())
        return [c["control"] for c in core.parse_amixer_scontrols(p.stdout)]


class NullControls(object):
    """No card is bound yet. Any control access is a refusal, not a no-op.

    Used only while the codec driver is being loaded, where the sole provable
    silence action is unloading that driver. The moment a manifest asks for a
    control write in this state the stop/mute probe fails loudly, which is the
    behaviour the contract wants.
    """

    def get(self, name):
        raise RuntimeError("no ALSA card bound; control %r is unavailable" % name)

    def set(self, name, values):
        raise RuntimeError("no ALSA card bound; refusing to write %r" % name)

    def list_controls(self):
        return []


def open_controls(state, card=None, allow_missing=False):
    if state.get("fake_root"):
        return FakeControls(state["fake_root"])
    if card is None:
        if allow_missing:
            return NullControls()
        raise RuntimeError("real backend needs a card index")
    return AlsaControls(card)


# --------------------------------------------------------------------------
# run directory helpers
# --------------------------------------------------------------------------


class RunDirs(object):
    def __init__(self, run_id, base_run=None, base_srv=None):
        if run_id and ("/" in run_id or ".." in run_id):
            raise SystemExit("run id must not contain slashes")
        self.run_id = run_id
        self.run = base_run or ("/run/kebab-audio-%s" % run_id)
        self.srv = base_srv or ("/srv/kebab-audio-%s" % run_id)

    def ensure(self, private=True):
        for d in (self.run, self.srv):
            core.mkdir_private(d, 0o700 if private else 0o755)
        return self

    @property
    def journal(self):
        return os.path.join(self.srv, "journal.jsonl")

    @property
    def lease(self):
        return os.path.join(self.run, "lease.json")

    @property
    def guard_status(self):
        return os.path.join(self.run, "guard.json")

    @property
    def owner_lock(self):
        return os.path.join(self.run, "owner.lock")

    @property
    def manifest(self):
        return os.path.join(self.srv, "trial-manifest.json")

    @property
    def control_map(self):
        return os.path.join(self.srv, "control-map.json")

    @property
    def guard_events(self):
        return os.path.join(self.srv, "guard-events.jsonl")


# --------------------------------------------------------------------------
# guard
# --------------------------------------------------------------------------


CARD_ABSENT = "no ALSA card bound"


def module_loaded(name):
    """True when the module currently appears in /proc/modules."""
    try:
        with open("/proc/modules") as fh:
            for line in fh:
                if line.split(" ", 1)[0] == name:
                    return True
    except OSError:
        return False
    return False


def apply_stop_actions(backend, stop_actions, manifest, dry_run=False,
                       allow_card_absent=False):
    """Apply the audited mute / route-off set and read it back.

    Returns (ok, detail). A stop action that cannot be verified makes ok False:
    an unproven silence is not silence.
    """
    detail = []
    ok = True
    for act in stop_actions or []:
        if act.get("kind") == "rmmod":
            module = act.get("module")
            loaded = any(l.split(" ")[0].replace("-", "_") == module.replace("-", "_")
                         for l in (core.read_text("/proc/modules", "") or "").splitlines())
            if not loaded:
                detail.append({"kind": "rmmod", "module": module,
                               "result": "already_unloaded"})
                continue
            if dry_run:
                detail.append({"kind": "rmmod", "module": module,
                               "result": "would_rmmod"})
                continue
            p = core.run(["/usr/sbin/rmmod", module], timeout=20)
            detail.append({"kind": "rmmod", "module": module, "rc": p.returncode,
                           "result": "unloaded" if p.returncode == 0 else "failed",
                           "stderr": p.stderr.strip()[:300]})
            if p.returncode != 0:
                ok = False
            continue
        name = act.get("name")
        want = act.get("values")
        try:
            got = backend.get(name)
        except Exception as exc:  # noqa: BLE001 - any backend error is a stop failure
            if allow_card_absent and CARD_ABSENT in str(exc):
                # No card bound yet: there is no route to leave open, so this
                # particular mute cannot be proven -- and is not needed. The
                # moment a card exists the strict path below takes over.
                detail.append({"name": name, "result": "not_applicable_no_card"})
                continue
            detail.append({"name": name, "result": "read_failed", "error": str(exc)})
            ok = False
            continue
        if got == want:
            detail.append({"name": name, "result": "already", "value": got})
            continue
        if dry_run:
            detail.append({"name": name, "result": "would_set", "from": got, "to": want})
            continue
        try:
            backend.set(name, want)
        except Exception as exc:  # noqa: BLE001
            detail.append({"name": name, "result": "write_failed", "error": str(exc)})
            ok = False
            continue
        try:
            after = backend.get(name)
        except Exception as exc:  # noqa: BLE001
            detail.append({"name": name, "result": "readback_failed", "error": str(exc)})
            ok = False
            continue
        if after == want:
            detail.append({"name": name, "result": "set", "from": got, "to": after})
        else:
            detail.append({"name": name, "result": "readback_mismatch",
                           "want": want, "got": after})
            ok = False
    return ok, detail


def kill_owned_group(pgid, dry_run=False):
    """Terminate only the process group the lease recorded for this run."""
    if not pgid:
        return "no_pgid"
    if pgid in (0, 1, os.getpid(), os.getpgid(0)):
        return "refused_self"
    if dry_run:
        return "would_kill"
    try:
        os.killpg(int(pgid), signal.SIGTERM)
    except ProcessLookupError:
        return "gone"
    except OSError as exc:
        return "kill_failed:%s" % exc
    deadline = time.time() + 1.5
    while time.time() < deadline:
        try:
            os.killpg(int(pgid), 0)
        except ProcessLookupError:
            return "terminated"
        except OSError:
            break
        time.sleep(0.05)
    try:
        os.killpg(int(pgid), signal.SIGKILL)
        return "sigkilled"
    except ProcessLookupError:
        return "terminated"
    except OSError as exc:
        return "sigkill_failed:%s" % exc


def cmd_guard(args):
    policy = core.load_json(args.policy, {}) or {}
    dirs = RunDirs(args.run, args.run_dir, args.srv_dir).ensure()
    manifest = core.load_json(dirs.manifest, {}) or {}
    guard_cfg = policy.get("guard", {})
    expiry_ms = int(guard_cfg.get("lease_expiry_ms", 1500))
    hb_tick = 0.05
    max_seconds = float(args.max_seconds or policy.get("experiment_budget", {})
                        .get("run_deadline_seconds", 1800))
    guard_card = args.card
    if guard_card is None and not args.fake_root:
        guard_card = find_card_index()[0]
    backend = open_controls({"fake_root": args.fake_root}, guard_card,
                            allow_missing=True)

    if fcntl is None:
        core.dump_json(dirs.guard_status, {
            "state": "refused", "reason": "fcntl unavailable: cannot guarantee a "
                                          "single audio owner"})
        say("guard: refused, no fcntl on this platform")
        return EXIT_REFUSED
    lock_fh = open(dirs.owner_lock, "a+")
    try:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            core.dump_json(dirs.guard_status, {
                "state": "refused", "reason": "another audio owner holds the flock",
                "run_id": dirs.run_id, "pid": os.getpid()})
            say("guard: refused, another owner holds %s" % dirs.owner_lock)
            return EXIT_REFUSED
        raise

    booted = core.boot_id()
    started_bt = core.boot_time()
    if args.run_id_check and args.run_id_check != dirs.run_id:
        say("guard: run id mismatch")
        return EXIT_REFUSED
    if args.boot_id and args.boot_id != booted:
        core.dump_json(dirs.guard_status, {"state": "refused", "reason": "boot_id drift",
                                           "expected": args.boot_id, "current": booted})
        say("guard: refused, boot id changed")
        return EXIT_REFUSED

    # A guard that cannot prove it can silence must not arm at all.
    stop_set = manifest.get("stop_actions") or []
    if not stop_set and not args.allow_no_stop_actions:
        core.dump_json(dirs.guard_status, {
            "state": "refused",
            "reason": "no audited stop actions in manifest; silence cannot be proven"})
        say("guard: refused, manifest has no stop_actions")
        return EXIT_REFUSED
    if stop_set:
        prologue_ok, prologue = apply_stop_actions(backend, stop_set, manifest,
                                                   dry_run=not args.apply_stop_at_arm,
                                                   allow_card_absent=True)
        core.jsonl_append(dirs.guard_events, {
            "event": "arm_stop_probe", "ok": prologue_ok, "detail": prologue,
            "dry_run": not args.apply_stop_at_arm, "boot_time": core.boot_time()})
        if not prologue_ok and not args.dry_run:
            core.dump_json(dirs.guard_status, {
                "state": "refused",
                "reason": "arm-time stop probe failed; silence cannot be proven",
                "detail": prologue})
            say("guard: refused, arm-time stop probe failed")
            return EXIT_STOP_FAILED

    core.dump_json(dirs.guard_status, {
        "state": "armed", "pid": os.getpid(), "run_id": dirs.run_id,
        "boot_id": booted, "manifest_sha": args.manifest_sha,
        "armed_boot_time": started_bt, "max_seconds": max_seconds,
        "expiry_ms": expiry_ms, "fake_root": args.fake_root,
    })
    core.jsonl_append(dirs.guard_events, {
        "event": "armed", "pid": os.getpid(), "run_id": dirs.run_id,
        "boot_id": booted, "boot_time": started_bt})

    stopping = {"flag": False}

    def _term(signum, _frame):
        stopping["flag"] = True

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, _term)

    stop_reason = None
    while True:
        if stopping["flag"]:
            stop_reason = "signal"
            break
        now = core.boot_time()
        if now - started_bt > max_seconds:
            stop_reason = "run_deadline"
            break
        lease = core.load_json(dirs.lease, None)
        if lease:
            if lease.get("run_id") != dirs.run_id or lease.get("boot_id") != booted:
                stop_reason = "lease_identity_drift"
                break
            if core.lease_expired(now, lease.get("heartbeat_boot_time"), expiry_ms):
                stop_reason = "lease_expired"
                break
        time.sleep(hb_tick)

    lease = core.load_json(dirs.lease, None) or {}
    pgid = lease.get("pgid")
    kill_result = kill_owned_group(pgid, dry_run=args.dry_run)
    ok, detail = apply_stop_actions(backend, stop_set, manifest,
                                    dry_run=args.dry_run, allow_card_absent=True)
    event = {
        "event": "stop", "reason": stop_reason, "kill": kill_result,
        "stop_ok": ok, "detail": detail, "pgid": pgid,
        "boot_time": core.boot_time(), "wall_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                 time.gmtime()),
        "lease_heartbeat_age_ms": (None if lease.get("heartbeat_boot_time") is None
                                   else round((core.boot_time()
                                               - float(lease["heartbeat_boot_time"])) * 1000)),
    }
    core.jsonl_append(dirs.guard_events, event)
    core.dump_json(dirs.guard_status, {
        "state": "stopped", "reason": stop_reason, "stop_ok": ok,
        "pid": os.getpid(), "run_id": dirs.run_id, "boot_id": booted})
    if not ok:
        say("guard: STOP NOT PROVEN (%s)" % stop_reason)
        return EXIT_STOP_FAILED
    say("guard: stopped (%s), silence verified" % stop_reason)
    return EXIT_OK


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------


def guard_ready(dirs, run_id, booted, manifest_sha):
    st = core.load_json(dirs.guard_status, None)
    if not st or st.get("state") != "armed":
        return False, "guard not armed: %r" % (st and st.get("state"))
    if st.get("run_id") != run_id:
        return False, "guard run id mismatch"
    if st.get("boot_id") != booted:
        return False, "guard boot id mismatch"
    if manifest_sha and st.get("manifest_sha") != manifest_sha:
        return False, "guard manifest sha mismatch"
    pid = st.get("pid")
    if not isinstance(pid, int) or pid <= 1:
        return False, "guard pid missing"
    try:
        os.kill(pid, 0)
    except OSError:
        return False, "guard process %d is not alive" % pid
    age = core.boot_time() - float(st.get("armed_boot_time") or 0)
    if age > 24 * 3600 or age < 0:
        return False, "guard armed_boot_time implausible (age %.1fs)" % age
    return True, "ok"


class Lease(object):
    def __init__(self, dirs, run_id, booted, manifest_sha, card, route, pgid):
        self.dirs = dirs
        self.base = {
            "run_id": run_id, "boot_id": booted, "manifest_sha": manifest_sha,
            "card": card, "route": route, "pgid": pgid,
        }

    def beat(self):
        d = dict(self.base)
        d["heartbeat_boot_time"] = core.boot_time()
        d["wall_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        core.dump_json(self.dirs.lease, d)

    def drop(self):
        try:
            os.unlink(self.dirs.lease)
        except OSError:
            pass


def fake_pcm_scenario(fake_root, act):
    scen = core.load_json(os.path.join(fake_root, "pcm.json"), {}) or {}
    merged = dict(scen.get("default") or {})
    merged.update((scen.get(act.get("scenario") or act.get("id")) or {}))
    return merged


def fake_capture(act, ev, fake_root, dry_run):
    """Simulated capture: writes a synthetic WAV so the analysis path is real."""
    scen = fake_pcm_scenario(fake_root, act)
    seconds = int(act.get("seconds", 4))
    rate = int(scen.get("rate", act.get("rate", 48000)))
    channels = int(scen.get("channels", act.get("channels", 1)))
    mode = scen.get("mode", "noise")
    delay = float(scen.get("delay_s", 0.0))
    fail = scen.get("fail")
    if dry_run:
        ev["cmd"] = ["fake-capture", act.get("pcm"), seconds, mode]
        return True, ev
    if delay:
        time.sleep(delay)
    ev.update({"fake": True, "mode": mode, "rate": rate, "channels": channels,
               "rc": 0 if not fail else 1, "stderr": fail or "", "eio": bool(
                   fail and "EIO" in str(fail))})
    if fail:
        ev["seconds_requested"] = seconds
        ev["killed_by"] = "error"
        return False, ev
    n = int(rate * seconds)
    amp = float(scen.get("amp", 0.0))
    freq = float(scen.get("freq_hz", 1000.0))
    import math
    import random
    random.seed(7)
    data = []
    for i in range(n):
        for ch in range(channels):
            v = 0
            if mode == "noise":
                v = int(random.gauss(0, max(1.0, amp * 32767.0)))
            elif mode == "tone":
                v = int(amp * 32767.0 * math.sin(2 * math.pi * freq * i / rate))
            elif mode == "silence":
                v = 0
            data.append(max(-32768, min(32767, v)))
    path = act.get("path")
    if path:
        core.wav_write_mono_s16(path, data, rate)
    stats = core.channel_stats(data, channels)
    ev["stats"] = stats
    ev["duration_s"] = round(seconds, 3)
    ev["frames"] = n
    ev["peak_db"] = max(s["peak_db"] for s in stats)
    ev["rms_db"] = min(s["rms_db"] for s in stats)
    ok, problems = core.capture_continuity_ok(ev["duration_s"], [],
                                              act.get("min_continuous_s", 3.0),
                                              ev["eio"])
    ev["continuity"] = problems
    return ok, ev


def fake_play(act, ev, fake_root, dry_run):
    scen = fake_pcm_scenario(fake_root, act)
    delay = float(scen.get("delay_s", 0.0))
    fail = scen.get("fail")
    if dry_run:
        ev["cmd"] = ["fake-play", act.get("pcm"), act.get("channel")]
        return True, ev
    if delay:
        time.sleep(delay)
    ev.update({"fake": True, "rc": 0 if not fail else 1, "stderr": fail or ""})
    return (not fail), ev


def execute_action(act, backend, dirs, dry_run=False, fake_root=None):
    """Perform one manifest action. Returns (ok, evidence)."""
    kind = act.get("kind")
    ev = {"kind": kind, "action_id": act.get("id")}
    if kind == "noop":
        return True, ev
    if kind == "modprobe":
        mod = act["module"]
        if dry_run:
            ev["cmd"] = ["modprobe", "-n", "-v", mod]
            return True, ev
        p = core.run(["/usr/sbin/modprobe", "-v", mod], timeout=act.get("timeout_s", 20))
        ev["module"] = mod
        ev["rc"] = p.returncode
        ev["stdout"] = p.stdout.strip()
        ev["stderr"] = p.stderr.strip()
        return p.returncode == 0, ev
    if kind == "rmmod":
        mod = act["module"]
        if dry_run:
            ev["cmd"] = ["rmmod", mod]
            return True, ev
        p = core.run(["/usr/sbin/rmmod", mod], timeout=act.get("timeout_s", 20))
        ev["module"] = mod
        ev["rc"] = p.returncode
        ev["stderr"] = p.stderr.strip()
        return p.returncode == 0, ev
    if kind == "control":
        results = []
        ok = True
        for w in act.get("writes") or []:
            name = w["name"]
            want = w["values"]
            try:
                before = backend.get(name)
            except Exception as exc:  # noqa: BLE001
                results.append({"name": name, "result": "read_failed", "error": str(exc)})
                ok = False
                continue
            if dry_run:
                results.append({"name": name, "result": "would_set",
                                "from": before, "to": want})
                continue
            try:
                backend.set(name, want)
            except Exception as exc:  # noqa: BLE001
                results.append({"name": name, "result": "write_failed", "error": str(exc)})
                ok = False
                continue
            try:
                after = backend.get(name)
            except Exception as exc:  # noqa: BLE001
                results.append({"name": name, "result": "readback_failed",
                                "error": str(exc)})
                ok = False
                continue
            match = after == want
            ok = ok and match
            results.append({"name": name, "result": "set" if match else "readback_mismatch",
                            "from": before, "to": after, "want": want})
        ev["writes"] = results
        return ok, ev
    if kind in ("capture", "play", "probe") and act.get("pre_writes"):
        # Audited route writes happen after the runner's mute gate and before
        # the stream; without readback success the action is aborted.
        ok, ev = apply_pre_writes(act, backend, dry_run)
        if not ok:
            return False, ev
    if kind == "capture":
        if fake_root:
            return fake_capture(act, ev, fake_root, dry_run)
        return do_capture(act, ev, dry_run)
    if kind == "play":
        if fake_root:
            return fake_play(act, ev, fake_root, dry_run)
        return do_play(act, ev, dry_run)
    if kind == "probe":
        return do_probe(act, ev, dry_run, fake_root)
    if kind == "verify":
        import kebab_audio_verify as V

        ctx = dict(act.get("ctx") or {})
        ctx.setdefault("card", act.get("card"))
        ctx.setdefault("manifest_path", os.path.join(dirs.srv, "trial-manifest.json"))
        ctx.setdefault("baseline_path", os.path.join(dirs.srv, "baseline.json"))
        ctx.setdefault("evidence_dir", os.path.join(dirs.srv, "evidence"))
        _pe = core.load_json(os.path.join(dirs.srv, "probe-evidence.json"), {}) or {}
        ctx.setdefault("probe_evidence", _pe.get("probes") or {})
        ctx.setdefault("noise_rms_db", _pe.get("noise_rms_db"))
        ctx.setdefault("accept", act.get("accept"))
        ctx.setdefault("backend_factory", lambda: backend)
        check_ok, ev2 = V.run_check(act, ctx)
        # A verify action succeeds when the check actually ran: the verdict
        # (pass/fail/unknown) belongs in evidence and result.json, not in the
        # exit status. A check that could not run at all is a failure.
        ev2["check_passed"] = check_ok
        if ev2.get("error"):
            return False, ev2
        return True, ev2
    if kind == "ucm":
        op = act.get("op")
        card = act.get("card")
        cmd = ["alsaucm", "-c", str(card), "set", op, act.get("arg", "")]
        ev["verb"] = act.get("arg") if op == "_verb" else act.get("verb")
        ev["card"] = card
        if dry_run:
            ev["cmd"] = cmd
            return True, ev
        env = dict(os.environ)
        for k, v in (act.get("env") or {}).items():
            env[str(k)] = str(v)
        if act.get("env"):
            ev["env"] = {k: act["env"][k] for k in act["env"]}
        # alsaucm is stateless per invocation: a separate process has no active
        # verb, so _enadev/_disdev there fails with ENOENT even though the verb
        # sets fine on its own. The verb and the device transition therefore have
        # to be driven as one session, which is what the interactive stdin mode
        # is for. Both belong to one action, hence one budget charge.
        if act.get("seq") or act.get("verb"):
            cmd = ["alsaucm", "-c", str(card), "-b", "-"]
            if act.get("seq"):
                script = "".join("set %s\n" % line for line in act["seq"])
            else:
                script = "set _verb %s\nset %s %s\n" % (act["verb"], op, act.get("arg", ""))
            p = core.run(cmd, timeout=act.get("timeout_s", 10), env=env,
                         stdin_data=script)
            ev["cmd"] = cmd
            ev["stdin"] = script.strip()
            ev["rc"] = p.returncode
            ev["stdout"] = p.stdout.strip()[-2000:]
            ev["stderr"] = p.stderr.strip()[-2000:]
            return p.returncode == 0, ev
        p = core.run(cmd, timeout=act.get("timeout_s", 10), env=env)
        ev["cmd"] = cmd
        ev["rc"] = p.returncode
        ev["stdout"] = p.stdout.strip()[-2000:]
        ev["stderr"] = p.stderr.strip()[-2000:]
        return p.returncode == 0, ev
    if kind == "service":
        unit = act["unit"]
        action = act.get("op", "restart")
        ev["unit"] = unit
        if dry_run:
            ev["cmd"] = ["systemctl", action, unit]
            return True, ev
        p = core.run(["systemctl", action, unit], timeout=act.get("timeout_s", 30))
        ev["rc"] = p.returncode
        ev["stderr"] = p.stderr.strip()
        return p.returncode == 0, ev
    return False, {"error": "unknown action kind %r" % kind}


def apply_pre_writes(act, backend, dry_run):
    """Audited route writes performed after the mute gate and before the stream.

    Ordering matters: the pre-action silence gate mutes everything, so the one
    route this probe legitimately needs must be re-enabled here -- explicitly,
    from the frozen control map, with readback, and reversed by rollback.
    """
    results = []
    ok = True
    for w in act.get("pre_writes") or []:
        name = w["name"]
        want = w["values"]
        try:
            before = backend.get(name)
        except Exception as exc:  # noqa: BLE001
            results.append({"name": name, "result": "read_failed", "error": str(exc)})
            ok = False
            continue
        if dry_run:
            results.append({"name": name, "result": "would_set",
                            "from": before, "to": want})
            continue
        try:
            backend.set(name, want)
            after = backend.get(name)
        except Exception as exc:  # noqa: BLE001
            results.append({"name": name, "result": "write_failed", "error": str(exc)})
            ok = False
            continue
        match = after == want
        ok = ok and match
        results.append({"name": name, "result": "set" if match else "readback_mismatch",
                        "from": before, "to": after, "want": want})
    return ok, {"kind": act.get("kind"), "action_id": act.get("id"),
                "pre_writes": results}


def do_capture_wrapper(act, ev, dry_run, fake_root):
    if fake_root:
        return fake_capture(act, ev, fake_root, dry_run)
    return do_capture(act, ev, dry_run)


def do_capture(act, ev, dry_run):
    seconds = int(act.get("seconds", 4))
    pcm = act.get("pcm")
    path = act.get("path")
    if dry_run:
        ev["cmd"] = ["arecord", "-D", pcm, "-d", str(seconds), path]
        return True, ev
    cmd = ["arecord", "-q", "-D", pcm, "-t", "wav", "-f", "S16_LE",
           "-r", str(act.get("rate", 48000)), "-c", str(act.get("channels", 1)),
           "-d", str(seconds), path]
    started = time.time()
    p = core.run(cmd, timeout=float(act.get("timeout_s", 8)))
    elapsed = time.time() - started
    ev.update({"cmd": cmd, "rc": p.returncode, "stderr": p.stderr.strip()[-2000:],
               "seconds_requested": seconds, "wall_seconds": round(elapsed, 2)})
    eio = ("Input/output error" in p.stderr) or ("EIO" in p.stderr)
    ev["eio"] = eio
    if p.returncode != 0 or not os.path.exists(path):
        ev["killed_by"] = "error"
        return False, ev
    try:
        samples, rate, nch = core.wav_mono_s16(path)
    except Exception as exc:  # noqa: BLE001
        ev["analysis_error"] = str(exc)
        return False, ev
    stats = core.channel_stats(samples, nch)
    ev["rate"] = rate
    ev["channels"] = nch
    ev["frames"] = len(samples) // max(nch, 1)
    ev["duration_s"] = round((len(samples) / float(max(nch, 1))) / rate, 3)
    ev["stats"] = stats
    ev["peak_db"] = max(s["peak_db"] for s in stats)
    ev["rms_db"] = min(s["rms_db"] for s in stats)
    ok, problems = core.capture_continuity_ok(ev["duration_s"], [],
                                              act.get("min_continuous_s", 3.0), eio)
    ev["continuity"] = problems
    ev["verdict"] = "pass" if ok else "fail"
    ev["noise_floor_db"] = core.noise_floor_db(samples, nch, rate)
    ev["verdict_note"] = ("capture continuity only: a passing capture says the mic "
                          "delivered >=min_continuous_s without EIO; it says nothing "
                          "about what was heard")
    return ok, ev


def make_tone_wav(path, rate, ms, peak_dbfs, freq, fade_ms, channel, lead_ms=0):
    import math

    nch = 2
    n = int(rate * ms / 1000.0)
    amp = 32767.0 * (10.0 ** (peak_dbfs / 20.0))
    fade_n = max(1, int(rate * fade_ms / 1000.0))
    data = []
    # A player that has to wake a suspended sink can swallow the first few hundred
    # milliseconds of the stream. Leading silence is not a nonzero burst, so it costs
    # no playback budget, and it moves the tone past the start-up window.
    lead_n = max(0, int(rate * lead_ms / 1000.0))
    for _ in range(lead_n):
        data.extend([0, 0])
    for i in range(n):
        env = 1.0
        if i < fade_n:
            env = i / float(fade_n)
        elif i > n - fade_n:
            env = max(0.0, (n - i) / float(fade_n))
        s = int(round(amp * env * math.sin(2.0 * math.pi * freq * i / rate)))
        if channel == "left":
            data.extend([s, 0])
        elif channel == "right":
            data.extend([0, s])
        else:
            data.extend([s, s])
    core.wav_write_mono_s16(path, data, rate, channels=nch)
    return n, nch


def do_play(act, ev, dry_run):
    pcm = act.get("pcm")
    ms = int(act.get("ms", 250))
    peak = float(act.get("peak_dbfs", -48.0))
    freq = float(act.get("freq_hz", 1000.0))
    fade_ms = int(act.get("fade_ms", 25))
    rate = int(act.get("rate", 48000))
    channel = act.get("channel", "both")
    wav = act.get("path")
    if dry_run:
        ev["cmd"] = ["aplay", "-D", pcm, wav,
                     "ms=%d peak=%g ch=%s" % (ms, peak, channel)]
        return True, ev
    samples, nch = make_tone_wav(wav, rate, ms, peak, freq, fade_ms, channel)
    ev["wav"] = {"path": wav, "samples": samples, "channels": nch,
                 "sha256": core.sha256_file(wav), "ms": ms, "peak_dbfs": peak,
                 "freq_hz": freq, "channel": channel}
    cmd = ["aplay", "-q", "-D", pcm, "-t", "wav", wav]
    started = time.time()
    p = core.run(cmd, timeout=float(act.get("timeout_s", 8)))
    ev.update({"cmd": cmd, "rc": p.returncode, "stderr": p.stderr.strip()[-2000:],
               "wall_seconds": round(time.time() - started, 2)})
    return p.returncode == 0, ev


def pick_pcm(card_index, direction):
    """Return (pcm_name, device_info) for the first playback/capture PCM of a card."""
    import re

    base = "/proc/asound/card%d" % int(card_index)
    pat = re.compile(r"^pcm(\d+)([pc])$")
    found = []
    for entry in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        m = pat.match(entry)
        if not m:
            continue
        if direction == "play" and m.group(2) != "p":
            continue
        if direction == "capture" and m.group(2) != "c":
            continue
        info = core.read_text(os.path.join(base, entry, "info"), "") or ""
        found.append((int(m.group(1)), entry, info.strip()))
    if not found:
        return None, None
    dev, entry, info = found[0]
    return "hw:%d,%d" % (int(card_index), dev), {"device": entry, "info": info}


def find_card_index(expect_model=None, expect_id="kebab"):
    cards = core.parse_proc_asound_cards(core.read_text("/proc/asound/cards", "") or "")
    for c in cards:
        if expect_id and c.get("id") == expect_id:
            return c["index"], c
        if expect_model and expect_model.lower() in (c.get("longname") or "").lower():
            return c["index"], c
    if cards:
        return cards[0]["index"], cards[0]
    return None, None


def do_probe(act, ev, dry_run, fake_root=None):
    """Play a bounded generated tone while capturing it, then correlate.

    This is the only action that both makes sound and records; it is charged in
    both the playback and the capture budget classes.
    """
    import math

    rate = int(act.get("rate", 48000))
    ms = int(act.get("ms", 250))
    peak = float(act.get("peak_dbfs", -48.0))
    freq = float(act.get("freq_hz", 1000.0))
    fade = int(act.get("fade_ms", 25))
    channel = act.get("channel", "left")
    cap_seconds = float(act.get("capture_seconds", 2.0))
    ref_path = act.get("ref_path")
    cap_path = act.get("capture_path")
    accept = act.get("accept") or {}
    if dry_run:
        ev["cmd"] = ["probe", act.get("play_pcm"), act.get("capture_pcm"),
                     "ch=%s ms=%d peak=%g" % (channel, ms, peak)]
        return True, ev

    n_ref, nch_ref = make_tone_wav(ref_path, rate, ms, peak, freq, fade, channel,
                                   int(act.get("lead_ms", 0)))
    ref, _, _ = core.wav_mono_s16(ref_path)
    # The reference must come from the channel that actually carries the tone:
    # reading channel 0 for a right-channel probe compares against silence.
    ref_ch = 1 if channel == "right" else 0
    # Leading silence only exists to outlast the player's start-up window; it is
    # not part of the signal, so the correlation reference starts at the tone.
    lead_n = max(0, int(rate * float(act.get("lead_ms", 0)) / 1000.0))
    ref_mono = core.slice_mono(ref, 2, lead_n, lead_n + n_ref, ch=ref_ch)

    if fake_root:
        scen = fake_pcm_scenario(fake_root, act)
        mode = scen.get("mode", "noise")
        delay = float(scen.get("delay_s", 0.0))
        if delay:
            time.sleep(delay)
        n = int(rate * cap_seconds)
        amp = float(scen.get("amp", 0.01))
        import math
        import random
        random.seed(11)
        cap = []
        burst_at = int(len(ref_mono) * 0.5)
        for i in range(n):
            v = 0
            if mode == "tone" and burst_at <= i < burst_at + len(ref_mono):
                v = int(amp * 32767.0 * math.sin(2 * math.pi * freq * i / rate))
            elif mode == "noise":
                v = int(random.gauss(0, max(1.0, amp * 32767.0)))
            cap.append(max(-32768, min(32767, v)))
        core.wav_write_mono_s16(cap_path, cap, rate, channels=1)
        cap_samples, cap_nch = cap, 1
        ev["fake"] = True
        ev["mode"] = mode
    else:
        cap_pcm = act.get("capture_pcm")
        play_pcm = act.get("play_pcm")
        okh, why = core.is_hardware_pcm(cap_pcm)
        if not okh:
            return False, {"kind": "probe", "error": why, "verdict": "fail"}
        okh, why = core.is_hardware_pcm(play_pcm)
        if not okh:
            return False, {"kind": "probe", "error": why, "verdict": "fail"}
        # A probe may name its own recorder, the same way it may name its own
        # player: exercising the capture side of the desktop stack is still a
        # hardware action this executor has to charge, journal and time-bound.
        recorder = act.get("recorder")
        rec_env = None
        if recorder:
            cap_cmd = list(recorder) + [cap_path]
            if act.get("recorder_env"):
                rec_env = dict(os.environ)
                rec_env.update({str(k): str(v) for k, v in act["recorder_env"].items()})
        else:
            cap_cmd = ["arecord", "-q", "-D", cap_pcm, "-t", "wav", "-f", "S16_LE",
                       "-r", str(rate), "-c", "1", "-d",
                       str(int(math.ceil(cap_seconds))), cap_path]
        cap_proc = subprocess.Popen(cap_cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, env=rec_env)
        cap_started = time.time()
        time.sleep(0.20)  # let the capture stream reach RUNNING before the burst
        play_started = time.time()
        # A probe may name its own player so that the whole stack can be exercised
        # (e.g. pw-play through PipeWire) while still being charged and journalled
        # by this executor: the sound is a hardware action either way.
        player = act.get("player")
        play_env = None
        if player:
            play_cmd = list(player) + [ref_path]
            if act.get("player_env"):
                play_env = dict(os.environ)
                play_env.update({str(k): str(v) for k, v in act["player_env"].items()})
            p = core.run(play_cmd, timeout=float(act.get("timeout_s", 8)),
                         env=play_env)
        else:
            play_cmd = ["aplay", "-q", "-D", play_pcm, "-t", "wav", ref_path]
            p = core.run(play_cmd, timeout=float(act.get("timeout_s", 8)))
        play_wall = time.time() - play_started
        if recorder:
            # pw-record keeps running until told to stop, so the executor has to
            # hold the recording to the budget it charged instead of letting the
            # client decide how long the microphone stays open.
            left = cap_seconds - (time.time() - cap_started)
            if left > 0:
                time.sleep(left)
            cap_proc.terminate()
        try:
            cap_out, cap_err = cap_proc.communicate(timeout=cap_seconds + 4)
        except subprocess.TimeoutExpired:
            cap_proc.kill()
            cap_out, cap_err = cap_proc.communicate()
            return False, {"kind": "probe", "error": "capture did not finish",
                           "verdict": "fail"}
        ev.update({"play_rc": p.returncode, "play_stderr": p.stderr.strip()[-800:],
                   "play_wall_seconds": round(play_wall, 2),
                   "capture_rc": cap_proc.returncode,
                   "capture_stderr": (cap_err or "").strip()[-800:],
                   "capture_cmd": cap_cmd, "play_cmd": play_cmd})
        if p.returncode != 0:
            return False, dict(ev, verdict="fail", error="player failed")
        if not os.path.exists(cap_path):
            return False, dict(ev, verdict="fail", error="no capture file")
        cap_samples, _, cap_nch = core.wav_mono_s16(cap_path)

    ev["rate"] = rate
    ev["capture_channels"] = cap_nch
    # skip the stream-start pop (measured ~-22 dBFS in the first 50 ms)
    start, end = core.find_burst(cap_samples, cap_nch, rate, skip_s=0.15)
    if start is None:
        ev["verdict"] = "unknown"
        ev["burst"] = None
        ev["note"] = "capture is digitally silent: no acoustic observer"
        return False, ev
    win = core.slice_mono(cap_samples, cap_nch, start, end)
    stats = core.channel_stats(cap_samples, cap_nch, start, end)[0]
    max_lag = int(rate * 0.08)
    corr, lag = core.best_lag_correlation(ref_mono, win, max_lag)
    ev["burst"] = {"start_frame": start, "end_frame": end,
                   "start_s": round(start / float(rate), 3)}
    ev["burst_stats"] = stats
    ev["corr"] = round(corr, 4)
    ev["lag_frames"] = lag
    ev["peak_db"] = stats.get("peak_db")
    ev["channel"] = channel
    ev["accept"] = accept
    # Action success means "the probe executed and left measurable evidence".
    # The acoustic pass/fail is decided by the separate acoustic check against
    # the frozen thresholds and the measured quiet baseline.
    ev["ref_channel"] = ref_ch
    ev["wav"] = {"ref_sha256": core.sha256_file(ref_path),
                 "capture_sha256": core.sha256_file(cap_path),
                 "ms": ms, "peak_dbfs": peak, "freq_hz": freq,
                 "channel": channel}
    ev["rc"] = ev.get("play_rc", 0)
    ev["verdict"] = "recorded"
    ev["note"] = ("probe executed; acoustic verdict is decided by check=acoustic, "
                  "not by this action's exit status")
    return True, ev


def cmd_run(args):
    policy = core.load_json(args.policy, {}) or {}
    caps = core.budget_caps(policy)
    dirs = RunDirs(args.run, args.run_dir, args.srv_dir).ensure()
    manifest = core.load_json(dirs.manifest, {}) or {}
    if not manifest:
        say("run: no frozen manifest at %s" % dirs.manifest)
        return EXIT_REFUSED
    manifest_sha = core.sha256_file(dirs.manifest)
    booted = core.boot_id()
    if manifest.get("boot_id") and manifest["boot_id"] != booted:
        say("run: manifest was frozen on a different boot id; refusing")
        return EXIT_REFUSED
    if manifest.get("kernel_release") and manifest["kernel_release"] != core.kernel_release():
        say("run: kernel release drift; refusing")
        return EXIT_REFUSED
    action = None
    for a in manifest.get("actions") or []:
        if a.get("id") == args.action:
            action = a
            break
    if action is None:
        say("run: action %r not in manifest" % args.action)
        return EXIT_REFUSED

    # Every control write must exist in the frozen control map with an audited
    # type/count/range. An unknown field is never written, not even in dry-run.
    if action.get("kind") == "control":
        control_map = core.load_json(dirs.control_map, {}) or {}
        for w in action.get("writes") or []:
            allowed, why, original = core.control_write_allowed(
                control_map, w.get("name"), w.get("values"))
            if not allowed:
                core.jsonl_append(dirs.journal, {
                    "event": "control_write_refused", "action": args.action,
                    "control": w.get("name"), "values": w.get("values"),
                    "reason": why, "boot_time": core.boot_time()})
                say("run: refusing control write: %s" % why)
                return EXIT_REFUSED
            w["_original"] = original

    read_only_action = bool(action.get("read_only"))
    if not args.dry_run and not read_only_action:
        ok, why = guard_ready(dirs, dirs.run_id, booted, manifest_sha)
        if not ok:
            say("run: refusing, %s" % why)
            return EXIT_REFUSED
    elif read_only_action and not args.dry_run:
        # A read-only verification cannot energise anything, so it is allowed to
        # run during teardown/after the guard stops. It still journals.
        gst = core.load_json(dirs.guard_status, {}) or {}
        if gst.get("state") not in ("armed", "stopped"):
            say("run: note - guard state is %r for a read-only check"
                % (gst.get("state"),))

    entries = core.jsonl_read(dirs.journal)
    used = core.journal_charge(entries)
    # Read-only checks consume no hardware budget; they are still journalled so
    # the evidence chain stays complete. Everything that can make noise must
    # declare a cost and be charged before it runs.
    read_only = bool(action.get("read_only"))
    cost = {} if read_only else (action.get("cost") or {})
    ok, reasons = core.budget_check(used, caps, cost) if not read_only else (True, [])
    if not ok:
        core.jsonl_append(dirs.journal, {"event": "budget_refused", "action": args.action,
                                         "cost": cost, "reasons": reasons,
                                         "used": used, "boot_time": core.boot_time()})
        say("run: budget refused: %s" % "; ".join(reasons))
        return EXIT_REFUSED

    run_card = args.card
    if run_card is None and not args.fake_root:
        run_card = find_card_index()[0]
    if args.card is None and run_card is not None:
        args.card = str(run_card)
    backend = open_controls({"fake_root": args.fake_root}, run_card,
                            allow_missing=True)

    requires = action.get("requires") or []
    if "silence" in requires and not args.dry_run:
        stop_set = manifest.get("stop_actions") or []
        sok, sdetail = apply_stop_actions(backend, stop_set, manifest)
        core.jsonl_append(dirs.journal, {"event": "pre_action_silence", "action": args.action,
                                         "ok": sok, "detail": sdetail,
                                         "boot_time": core.boot_time()})
        if not sok:
            say("run: refusing, pre-action silence could not be proven")
            return EXIT_STOP_FAILED

    exp_id = "%s-%d" % (args.action, int(time.time() * 1000) % 1000000)
    seq = len(entries) + 1
    entry = core.journal_new_entry(exp_id, args.action, cost, dirs.run_id, booted, seq)
    core.jsonl_append(dirs.journal, entry)  # RESERVED: charged before the action

    pgid = os.getpgid(0)
    lease = None
    if not args.dry_run:
        lease = Lease(dirs, dirs.run_id, booted, manifest_sha,
                      args.card or issue_card_id(args.fake_root),
                      action.get("route") or "none", pgid)
        lease.beat()

    hb_stop = {"flag": False}

    def heartbeat():
        while not hb_stop["flag"]:
            try:
                lease.beat()
            except Exception:  # noqa: BLE001 - heartbeat failure is not fatal here
                pass
            time.sleep(0.25)

    import threading

    hb_thread = None
    if lease is not None:
        hb_thread = threading.Thread(target=heartbeat, daemon=True)
        hb_thread.start()

    hard_deadline = float(action.get("timeout_s", 8))
    started = time.time()
    try:
        ok, evidence = execute_action(action, backend, dirs,
                                       dry_run=args.dry_run,
                                       fake_root=args.fake_root)
    except subprocess.TimeoutExpired as exc:
        ok, evidence = False, {"kind": action.get("kind"), "error": "timeout",
                               "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001
        ok, evidence = False, {"kind": action.get("kind"), "error": "exception",
                               "detail": str(exc)}
    elapsed = time.time() - started
    hb_stop["flag"] = True
    if hb_thread:
        hb_thread.join(timeout=2.0)
    if lease is not None:
        lease.drop()

    evidence = evidence or {}
    evidence["elapsed_s"] = round(elapsed, 3)
    evidence["hard_deadline_s"] = hard_deadline
    if elapsed > hard_deadline:
        evidence["hard_deadline_hit"] = True
        ok = False

    core.jsonl_append(dirs.journal, dict(entry, state="APPLIED", result=evidence.get("rc"),
                                         evidence=evidence))
    final_state = "VERIFIED" if ok else "APPLIED"
    core.jsonl_append(dirs.journal, dict(entry, state=final_state,
                                         verified=bool(ok),
                                         boot_time=core.boot_time()))
    ev_dir = os.path.join(dirs.srv, "evidence")
    core.mkdir_private(ev_dir)
    core.dump_json(os.path.join(ev_dir, "action-%s.json" % args.action), evidence, 0o600)

    # merge measurable probe/noise results so the acoustic check sees them
    if ok and action.get("kind") in ("probe", "capture"):
        pe_path = os.path.join(dirs.srv, "probe-evidence.json")
        pe = core.load_json(pe_path, {}) or {}
        pe.setdefault("probes", {})
        if action.get("kind") == "probe":
            label = action.get("label") or args.action
            pe["probes"][label] = {
                "burst_stats": evidence.get("burst_stats"),
                "corr": evidence.get("corr"),
                "peak_db": evidence.get("peak_db"),
                "channel": evidence.get("channel"),
                "source_action": args.action,
            }
        elif action.get("role") == "noise":
            # robust low-quartile block level: the capture starts with a loud pop
            pe["noise_rms_db"] = (evidence.get("noise_floor_db")
                                  if evidence.get("noise_floor_db") is not None
                                  else evidence.get("rms_db"))
            pe["noise_action"] = args.action
            pe["noise_note"] = ("low-quartile of 50 ms block RMS, first 200 ms "
                                "excluded (stream-start pop)")
        core.dump_json(pe_path, pe)
    if args.dry_run:
        print(json.dumps({"dry_run": True, "action": args.action,
                          "would_execute": evidence}, indent=2, sort_keys=True))
        return EXIT_OK
    print(json.dumps({"action": args.action, "ok": bool(ok), "exp_id": exp_id,
                      "elapsed_s": evidence.get("elapsed_s")}, indent=2, sort_keys=True))
    return EXIT_OK if ok else EXIT_HARD_FAIL


def issue_card_id(fake_root):
    if fake_root:
        return core.load_json(os.path.join(fake_root, "card.json"), {}) or "fake:0"
    cards = core.parse_proc_asound_cards(
        core.read_text("/proc/asound/cards", "") or "")
    if not cards:
        return None
    return "card%d:%s" % (cards[0]["index"], cards[0]["id"])


# --------------------------------------------------------------------------
# inventory (read-only)
# --------------------------------------------------------------------------


def sh(cmd, timeout=10):
    p = core.run(cmd, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def cmd_inventory(args):
    if args.fake_root:
        print(json.dumps(core.load_json(os.path.join(args.fake_root, "baseline.json"),
                                        {}) or {}, indent=2, sort_keys=True))
        return EXIT_OK
    inv = {}
    inv["schema"] = core.SCHEMA
    inv["collected_wall_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    inv["kernel_release"] = core.kernel_release()
    inv["boot_id"] = core.boot_id()
    inv["boot_time_s"] = round(core.boot_time(), 2)
    inv["uname"] = sh(["uname", "-a"])[1].strip()
    inv["cmdline"] = core.read_one_line("/proc/cmdline", "")
    inv["proc_asound_cards_raw"] = core.read_text("/proc/asound/cards", "")
    inv["cards"] = core.parse_proc_asound_cards(inv["proc_asound_cards_raw"] or "")
    inv["asound_devices"] = core.read_text("/proc/asound/devices", "")
    inv["dev_snd"] = sorted(os.listdir("/dev/snd")) if os.path.isdir("/dev/snd") else []
    inv["snd_modules"] = [l for l in (core.read_text("/proc/modules", "") or "").splitlines()
                          if l.split(" ")[0].startswith("snd")
                          or "tfa" in l.split(" ")[0]]
    inv["blacklist"] = core.read_text("/etc/modprobe.d/90-kebab-server.conf", "")
    amp = {}
    for dev in ("15-0034", "15-0035"):
        base = "/sys/bus/i2c/devices/" + dev
        entry = {"present": os.path.isdir(base)}
        if entry["present"]:
            entry["name"] = core.read_one_line(os.path.join(base, "name"))
            entry["modalias"] = core.read_one_line(os.path.join(base, "modalias"))
            entry["driver"] = (os.path.basename(os.path.realpath(os.path.join(base, "driver")))
                               if os.path.exists(os.path.join(base, "driver")) else None)
            entry["uevent"] = core.read_text(os.path.join(base, "uevent"), "")
            entry["waiting_for_supplier"] = core.read_one_line(
                os.path.join(base, "waiting_for_supplier"))
            of = os.path.join(base, "of_node")
            props = {}
            if os.path.isdir(of):
                for p in ("nxp,tfa-channel", "sound-name-prefix", "compatible"):
                    raw = os.path.join(of, p)
                    if os.path.exists(raw):
                        with open(raw, "rb") as fh:
                            b = fh.read()
                        if p == "nxp,tfa-channel":
                            props[p] = int.from_bytes(b[:4], "big")
                        else:
                            props[p] = b.rstrip(b"\x00").decode("utf-8", "replace")
            entry["dt_props"] = props
        amp[dev] = entry
    inv["amp_sysfs"] = amp
    sn = {}
    for f in ("compatible", "model", "name"):
        raw = "/proc/device-tree/sound/" + f
        if os.path.exists(raw):
            with open(raw, "rb") as fh:
                sn[f] = fh.read().rstrip(b"\x00").decode("utf-8", "replace")
    inv["sound_node"] = sn
    inv["sound_dai_links"] = (sorted(d for d in os.listdir("/proc/device-tree/sound")
                                     if os.path.isdir("/proc/device-tree/sound/" + d))
                              if os.path.isdir("/proc/device-tree/sound") else [])
    inv["deferred_probe"] = core.read_text("/sys/kernel/debug/devices_deferred", "") or ""
    inv["lsmod"] = sh(["lsmod"])[1]
    inv["packages"] = sh(["dpkg-query", "-W", "alsa-utils", "alsa-ucm-conf", "libasound2",
                          "kmod", "pulseaudio", "pipewire", "wireplumber"])[1]
    services = {}
    for unit in ("ssh", "kebab-charge-policy", "kebab-alert-slider", "kebab-sdx55-modem",
                 "kebab-gps", "kebab-ssc-mag-gate", "kebab-venus"):
        services[unit] = {
            "active": sh(["systemctl", "is-active", unit])[1].strip(),
            "enabled": sh(["systemctl", "is-enabled", unit])[1].strip(),
        }
    inv["protected_services"] = services
    inv["failed_units"] = sh(["systemctl", "list-units", "--state=failed", "--no-legend",
                              "--no-pager"])[1].strip()
    ps = sh(["ps", "-eo", "pid,comm,args"])[1]
    inv["audio_processes"] = [l for l in ps.splitlines()
                              if any(k in l.lower() for k in
                                     ("pulse", "pipewire", "wireplumber", "alsactl",
                                      "aplay", "arecord"))]
    inv["audio_units"] = [l for l in sh(["systemctl", "list-units", "--all",
                                         "--no-legend", "--no-pager"])[1].splitlines()
                          if any(k in l.lower() for k in
                                 ("audio", "pulse", "pipewire", "alsa", "sound"))]
    inv["tools"] = {t: sh(["sh", "-c", "command -v " + t])[1].strip()
                    for t in ("amixer", "alsactl", "alsaucm", "aplay", "arecord",
                              "modprobe", "rmmod", "/usr/sbin/modprobe")}
    inv["asound_paths"] = {p: os.path.exists(p) for p in
                           ("/etc/asound.conf", "/etc/alsa/conf.d",
                            "/usr/share/alsa/ucm2", "/var/lib/alsa/asound.state")}
    inv["ucm_roots"] = (sorted(os.listdir("/usr/share/alsa/ucm2"))
                        if os.path.isdir("/usr/share/alsa/ucm2") else [])
    inv["thermal"] = {p: core.read_one_line(p) for p in
                      ("/sys/class/thermal/thermal_zone0/temp",
                       "/sys/class/power_supply/battery/temp",
                       "/sys/class/power_supply/battery/capacity") if os.path.exists(p)}
    inv["uptime"] = core.read_text("/proc/uptime", "").strip()
    inv["loadavg"] = core.read_text("/proc/loadavg", "").strip()
    text = json.dumps(inv, indent=2, sort_keys=True, default=str)
    if args.out:
        core.mkdir_private(os.path.dirname(args.out) or ".")
        core.atomic_write(args.out, text + "\n", 0o600)
    print(text)
    return EXIT_OK


def cmd_status(args):
    dirs = RunDirs(args.run, args.run_dir, args.srv_dir)
    entries = core.jsonl_read(dirs.journal)
    st = {
        "run_id": dirs.run_id,
        "boot_id": core.boot_id(),
        "kernel": core.kernel_release(),
        "guard": core.load_json(dirs.guard_status, None),
        "lease": core.load_json(dirs.lease, None),
        "journal_entries": len(entries),
        "charged": core.journal_charge(entries),
        "uncertain_hardware": core.journal_uncertain_hardware(entries),
    }
    print(json.dumps(st, indent=2, sort_keys=True, default=str))
    return EXIT_OK


# --------------------------------------------------------------------------
# selftest: pure functions only
# --------------------------------------------------------------------------

SELFTEST_CASES = []


def case(name):
    def deco(fn):
        SELFTEST_CASES.append((name, fn))
        return fn

    return deco


@case("lease_expired")
def _t_lease():
    assert core.lease_expired(10.0, 10.0, 1500) is False
    assert core.lease_expired(11.6, 10.0, 1500) is True
    assert core.lease_expired(31.0, 30.0, 1500) is False
    assert core.lease_expired(10.0, None, 1500) is True


@case("lease_identity_ok")
def _t_lease_id():
    good = {"run_id": "r", "boot_id": "b", "manifest_sha": "m",
            "card": "card0:x", "route": "spk"}
    ok, _ = core.lease_identity_ok(good, "r", "b", "m")
    assert ok
    for key in ("boot_id", "run_id", "manifest_sha", "card", "route"):
        mut = dict(good)
        mut[key] = None
        bad, problems = core.lease_identity_ok(mut, "r", "b", "m")
        assert not bad, key
        assert problems


@case("budget_check")
def _t_budget():
    caps = {"playback_attempt": 6, "module_load": 2}
    assert core.budget_check({"playback_attempt": 5}, caps, {"playback_attempt": 1})[0]
    bad, why = core.budget_check({"playback_attempt": 6}, caps, {"playback_attempt": 1})
    assert not bad and "cap" in why[0]
    bad2, why2 = core.budget_check({}, caps, {"secret_class": 1})
    assert not bad2 and "unknown cost class" in why2[0]
    assert not core.budget_check({}, caps, {})[0]


@case("control_write_allowed")
def _t_ctl():
    cmap = {"controls": {
        "Vol": {"type": "INT", "count": 2, "min": 0, "max": 255, "value": [100, 100]},
        "Sw": {"type": "BOOL", "count": 1, "value": [0]},
        "Mode": {"type": "ENUM", "count": 1, "items": ["Off", "On"], "value": [0]},
        "Weird": {"type": "BYTES", "count": 1, "value": [0]},
        "NoRange": {"type": "INT", "count": 1, "value": [1]},
    }}
    assert core.control_write_allowed(cmap, "Vol", [50, 50])[0]
    assert not core.control_write_allowed(cmap, "Vol", [50])[0]
    assert not core.control_write_allowed(cmap, "Vol", [0, 256])[0]
    assert not core.control_write_allowed(cmap, "Vol", [0, -1])[0]
    assert not core.control_write_allowed(cmap, "Nope", [0])[0]
    assert not core.control_write_allowed(cmap, "Weird", [0])[0]
    assert not core.control_write_allowed(cmap, "NoRange", [1])[0]
    assert core.control_write_allowed(cmap, "Sw", [1])[0]
    assert not core.control_write_allowed(cmap, "Sw", [2])[0]
    assert core.control_write_allowed(cmap, "Mode", [1])[0]
    assert not core.control_write_allowed(cmap, "Mode", [2])[0]
    ok, _, original = core.control_write_allowed(cmap, "Vol", [10, 10])
    assert ok and original == [100, 100]


@case("rollback_decision")
def _t_rb():
    assert core.rollback_decision([5], [5], [1]) == "restore"
    assert core.rollback_decision([1], [5], [1]) == "noop"
    assert core.rollback_decision([7], [5], [1]) == "skip_drift"
    assert core.rollback_decision([5], [5], [5]) == "noop"
    assert core.rollback_decision(None, [5], [1]) == "skip_unknown", "unreadable current"
    assert core.rollback_decision([5], None, [1]) == "skip_unknown"


@case("journal_charge_and_uncertain")
def _t_journal():
    entries = [
        {"exp_id": "a", "action": "play", "cost": {"playback_attempt": 1},
         "state": "VERIFIED"},
        {"exp_id": "b", "action": "play", "cost": {"playback_attempt": 1},
         "state": "APPLIED"},
        {"exp_id": "c", "action": "capture", "cost": {"capture_seconds": 4},
         "state": "RELEASED"},
        {"exp_id": "d", "action": "capture", "cost": {"capture_seconds": 4},
         "state": "RESERVED"},
    ]
    charged = core.journal_charge(entries)
    assert charged == {"playback_attempt": 2, "capture_seconds": 8}, charged
    # one action writing several transitions must still cost budget exactly once
    transitions = [
        {"exp_id": "x", "action": "play", "cost": {"playback_attempt": 1},
         "state": "RESERVED"},
        {"exp_id": "x", "action": "play", "cost": {"playback_attempt": 1},
         "state": "APPLIED"},
        {"exp_id": "x", "action": "play", "cost": {"playback_attempt": 1},
         "state": "VERIFIED"},
    ]
    assert core.journal_charge(transitions) == {"playback_attempt": 1},         core.journal_charge(transitions)
    uncertain = core.journal_uncertain_hardware(entries)
    assert ("b", "play") in uncertain and ("d", "capture") in uncertain
    assert ("a", "play") not in uncertain
    assert core.journal_state_of(entries, "a") == "VERIFIED"


@case("acoustic_verdict")
def _t_acoustic():
    loud = {"rms_db": -40.0, "peak_db": -30.0, "clipped": 0, "flat": False,
            "nonzero": 1000, "total_samples": 1000}
    quiet = {"rms_db": -90.0, "peak_db": -80.0, "clipped": 0, "flat": False,
             "nonzero": 1000, "total_samples": 1000}
    flat = {"rms_db": -200.0, "peak_db": -200.0, "clipped": 0, "flat": True,
            "nonzero": 0, "total_samples": 1000}
    assert core.acoustic_verdict(loud, -70.0, 0.9)[0] == "pass"
    assert core.acoustic_verdict(quiet, -70.0, 0.9)[0] == "fail"
    assert core.acoustic_verdict(loud, -70.0, 0.2)[0] == "fail"
    assert core.acoustic_verdict(flat, -70.0, 0.9)[0] == "unknown"
    assert core.acoustic_verdict(None, -70.0, 0.9)[0] == "unknown"
    v, why = core.acoustic_verdict(dict(loud, clipped=500), -70.0, 0.9)
    assert v == "fail" and any("clip" in w for w in why)


@case("capture_continuity_ok")
def _t_capture_cont():
    assert core.capture_continuity_ok(3.4, [], 3.0, False)[0]
    bad, why = core.capture_continuity_ok(1.6, [], 3.0, False)
    assert not bad and "1.60" in why[0]
    bad2, why2 = core.capture_continuity_ok(4.0, [], 3.0, True)
    assert not bad2 and "EIO" in why2[0]


@case("is_hardware_pcm")
def _t_pcm():
    assert core.is_hardware_pcm("hw:0,0")[0]
    assert core.is_hardware_pcm("hw:CARD=kebab,DEV=0")[0]
    for bad in ("null", "default", "pulse", "pipewire", "hw:0,0/monitor",
                "surround51", "dmix:CARD=x", "hdmi:CARD=x"):
        assert not core.is_hardware_pcm(bad)[0], bad
    assert not core.is_hardware_pcm("")[0]


@case("parse_amixer")
def _t_amixer():
    txt = (
        "numid=12,iface=MIXER,name='TERT_MI2S_RX Audio Mixer MultiMedia1'\n"
        "  ; type=BOOLEAN,access=rw------,values=1\n"
        "  : values=0\n"
        "numid=30,iface=MIXER,name='VA_DEC0 Volume'\n"
        "  ; type=INTEGER,access=rw---R--,values=2,min=0,max=255,step=0\n"
        "  : values=100,100\n"
        "  | dBscale-min=-95.25dB,step=0.75dB,mute=0\n"
    )
    e = core.parse_amixer_cget(txt)
    assert e["values"] == [100, 100], e
    assert e["type"] == "INTEGER", e
    assert e["count"] == 2, e
    assert e["min"] == 0 and e["max"] == 255, e
    e2 = core.parse_amixer_cget(
        "  ; type=ENUMERATED,access=rw------,values=1,items=2\n"
        "  ; Item #0 'Off'\n  ; Item #1 'On'\n  : value=1\n")
    assert e2["type"] == "ENUMERATED" and e2["values"] == [1]
    assert e2["items"] == ["Off", "On"] and e2["item_count"] == 2, e2
    # BOOLEANs come back as on/off; a digits-only parser silently drops every
    # switch, which is exactly what made the guard refuse to arm once.
    eb = core.parse_amixer_cget(
        "numid=12,iface=MIXER,name='Some Switch'\n"
        "  ; type=BOOLEAN,access=rw------,values=1\n"
        "  : values=off\n")
    assert eb["values"] == [0], eb
    assert eb["name"] == "Some Switch" and eb["numid"] == 12, eb
    eb2 = core.parse_amixer_cget(
        "  ; type=BOOLEAN,access=rw---R--,values=2\n  : values=on,off\n")
    assert eb2["values"] == [1, 0], eb2


@case("parse_proc_asound_cards")
def _t_cards():
    txt = (" 0 [kebab          ]: kebab - kebab\n"
           "                      Kebab-Sound-Card\n"
           " 1 [HDMI           ]: simple - HDMI\n")
    cards = core.parse_proc_asound_cards(txt)
    assert len(cards) == 2, cards
    assert cards[0]["index"] == 0 and cards[0]["id"] == "kebab", cards
    assert cards[0]["driver"] == "kebab" and cards[0]["longname"] == "kebab", cards
    assert cards[0].get("raw", "").startswith(" 0 [kebab")


@case("correlation")
def _t_corr():
    import math

    a = [int(1000 * math.sin(i / 7.0)) for i in range(500)]
    assert core.normalized_correlation(a, a) > 0.999
    assert abs(core.normalized_correlation(a, [-v for v in a]) + 1.0) < 1e-6
    assert core.normalized_correlation(a, [0] * 500) == 0.0
    shifted = [0] * 5 + a[:-5]
    best, lag = core.best_lag_correlation(shifted, a, 8)
    # sign convention: negative lag means the first argument is delayed
    assert best > 0.99 and abs(lag) == 5, (best, lag)


@case("make_tone_wav_bounds")
def _t_tone():
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "t.wav")
        n, nch = make_tone_wav(p, 48000, 250, -48.0, 1000.0, 25, "left")
        samples, rate, ch = core.wav_mono_s16(p)
        assert rate == 48000 and ch == 2 and n == 12000, (rate, ch, n)
        st = core.channel_stats(samples, 2)
        # -48 dBFS source must read about -48 dBFS after normalisation
        assert -52.0 < st[0]["peak_db"] < -44.0, st
        assert st[1]["peak_db"] <= -200.0, st
        assert st[0]["clipped"] == 0, st
        p2 = os.path.join(td, "t2.wav")
        make_tone_wav(p2, 48000, 250, -36.0, 1000.0, 25, "right")
        s2, _, _ = core.wav_mono_s16(p2)
        st2 = core.channel_stats(s2, 2)
        assert st2[0]["peak_db"] <= -200.0, st2
        assert -40.0 < st2[1]["peak_db"] < -32.0, st2


def cmd_selftest(args):
    results = []
    failures = 0
    for name, fn in SELFTEST_CASES:
        try:
            fn()
            results.append({"case": name, "status": "pass"})
        except AssertionError as exc:
            failures += 1
            results.append({"case": name, "status": "fail", "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            failures += 1
            results.append({"case": name, "status": "error", "error": repr(exc)})
    doc = {
        "schema": core.SCHEMA,
        "tool": "kebab-audio selftest",
        "boot_id": core.boot_id(),
        "kernel_release": core.kernel_release(),
        "wall_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cases": results,
        "total": len(results),
        "failed": failures,
        "verdict": "pass" if failures == 0 else "fail",
    }
    text = json.dumps(doc, indent=2, sort_keys=True)
    if args.out:
        core.mkdir_private(os.path.dirname(args.out) or ".")
        core.atomic_write(args.out, text + "\n", 0o600)
    print(text)
    return EXIT_OK if failures == 0 else EXIT_HARD_FAIL


# --------------------------------------------------------------------------
# verify
# --------------------------------------------------------------------------


def build_result(manifest, entries, evidence, dirs):
    caps = {}
    charged = core.journal_charge(entries)
    uncertain = core.journal_uncertain_hardware(entries)

    def ev(action_id):
        return evidence.get(action_id) or {}

    def cap(name, verdict, detail=None, files=None):
        caps[name] = {"verdict": verdict, "detail": detail or {},
                      "evidence": files or []}

    cap("binding", ev("binding").get("verdict", "unknown"),
        {"card": ev("binding").get("matched_card"),
         "amps": ev("binding").get("amp"),
         "problems": ev("binding").get("problems")})
    cap("transport", ev("transport").get("verdict", "unknown"),
        {"note": "PCM exit=0 or DMA advance is transport only, never sound",
         "ok_plays": ev("transport").get("ok_plays")})
    cap("physical", ev("acoustic").get("verdict", "unknown"),
        {"channels": ev("acoustic").get("channels"),
         "noise_rms_db": ev("acoustic").get("noise_rms_db"),
         "problems": ev("acoustic").get("problems"),
         "note": "physical output correlation requires an independent observer"})
    mic = ev("mic-3s") or ev("capture-mic")
    mic_secs = mic.get("duration_s") or mic.get("seconds") or 0
    mic_min = mic.get("min_continuous_s") or 3.0
    mic_ok = bool(mic) and not mic.get("eio") and mic_secs >= mic_min
    cap("mic_continuity", "pass" if mic_ok else ("fail" if mic else "unknown"),
        {"seconds": mic_secs, "required_s": mic_min, "rate": mic.get("rate"),
         "eio": mic.get("eio"), "continuity": mic.get("continuity"),
         "peak_db": mic.get("peak_db"),
         "note": "continuity of the capture only; says nothing about content"})
    ucm_ev = ev("ucm-pair")
    if ucm_ev:
        cap("ucm", ucm_ev.get("verdict", "unknown"), ucm_ev)
    else:
        cap("ucm", "not_tested", {
            "reason": "no UCM2 config exists for this card, and exercising a "
                      "staged one needs the codec loaded, which would be a third "
                      "module load cycle - over the frozen cap of 2",
            "staged_file": "ucm/O8T/HiFi.conf in the replayable package",
            "substitute_used": "the audited mixer writes in control-map.json"})
    cap("pipewire", ev("pipewire").get("verdict", "not_tested"),
        {"note": "no session audio server on this host"})
    cap("idle_safety", ev("idle-safety").get("verdict", "unknown"))
    cap("server_regression", ev("server-regression").get("verdict", "unknown"))
    rb = core.load_json(os.path.join(dirs.srv, "rollback-report.json"), {}) or {}
    cap("rollback", rb.get("verdict") or "unknown",
        {"mode": rb.get("mode", "perform"),
         "failed": rb.get("failed"), "restored": rb.get("restored"),
         "not_applicable": rb.get("not_applicable"),
         "note": "verify-only mode reconciles the end state without changing it"})
    cap("unattended_ready", "fail",
        {"reason": "TFA2 is DSP-bypass with no verified speaker excursion/thermal "
                   "protection (policy: dsp_bypass_speaker_protection_unresolved)"})

    hard = [v["verdict"] for k, v in caps.items() if v["verdict"] == "fail"
            and k != "unattended_ready"]
    passes = [v["verdict"] for k, v in caps.items() if v["verdict"] == "pass"
              and k != "unattended_ready"]
    if hard and passes:
        overall = "partial"
    elif hard:
        overall = "partial" if passes else "fail"
    elif all(v["verdict"] == "pass" for k, v in caps.items()
             if k != "unattended_ready"):
        overall = "pass"
    else:
        overall = "partial" if passes else "not_tested"
    caps["overall"] = overall
    return {
        "schema": core.SCHEMA,
        "run_id": dirs.run_id,
        "kernel_release": core.kernel_release(),
        "boot_id": core.boot_id(),
        "manifest_sha256": (core.sha256_file(dirs.manifest)
                            if os.path.exists(dirs.manifest) else None),
        "capabilities": caps,
        "budget_charged": charged,
        "uncertain_hardware_actions": uncertain,
        "journal_entries": len(entries),
        "generated_wall_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def cmd_verify(args):
    dirs = RunDirs(args.run, args.run_dir, args.srv_dir)
    manifest = core.load_json(dirs.manifest, {}) or {}
    entries = core.jsonl_read(dirs.journal)
    ev_dir = os.path.join(dirs.srv, "evidence")
    evidence = {}
    if os.path.isdir(ev_dir):
        for fn in sorted(os.listdir(ev_dir)):
            if fn.startswith("action-") and fn.endswith(".json"):
                evidence[fn[len("action-"):-len(".json")]] = \
                    core.load_json(os.path.join(ev_dir, fn), {}) or {}
    result = build_result(manifest, entries, evidence, dirs)
    text = json.dumps(result, indent=2, sort_keys=True)
    out = args.out or os.path.join(dirs.srv, "result.json")
    core.mkdir_private(os.path.dirname(out) or ".")
    core.atomic_write(out, text + "\n", 0o600)
    print(text)
    return EXIT_OK


# --------------------------------------------------------------------------
# rollback
# --------------------------------------------------------------------------


def cmd_rollback(args):
    dirs = RunDirs(args.run, args.run_dir, args.srv_dir)
    entries = core.jsonl_read(dirs.journal)
    caps = core.budget_caps(core.load_json(args.policy, {}) or {})
    used = core.journal_charge(entries)
    observe = bool(getattr(args, "verify_only", False))
    if not observe:
        ok, reasons = core.budget_check(used, caps, {"rollback": 1})
        if not ok:
            say("rollback: refused: %s" % "; ".join(reasons))
            return EXIT_REFUSED
    rb_card = args.card
    if rb_card is None and not args.fake_root:
        rb_card = find_card_index()[0]
    backend = open_controls({"fake_root": args.fake_root}, rb_card,
                            allow_missing=True)
    manifest = core.load_json(dirs.manifest, {}) or {}
    report = {"schema": core.SCHEMA, "run_id": dirs.run_id, "steps": [],
              "restored": 0, "skipped_drift": 0, "failed": 0,
              "wall_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if observe:
        # Reconciliation only: read the world, change nothing, charge nothing.
        report["mode"] = "verify_only"
    else:
        exp = "rollback-%d" % int(time.time() * 1000)
        entry = core.journal_new_entry(exp, "rollback", {"rollback": 1},
                                       dirs.run_id, core.boot_id(),
                                       len(entries) + 1)
        core.jsonl_append(dirs.journal, entry)

    candidates = [e for e in reversed(entries)
                  if e.get("state") in ("APPLIED", "VERIFIED") and e.get("evidence")]
    seen = set()
    for e in candidates:
        evd = e.get("evidence") or {}
        aid = e.get("action")
        if aid in seen:
            continue
        seen.add(aid)
        kind = evd.get("kind")
        if kind == "control":
            for w in evd.get("writes") or []:
                name = w.get("name")
                applied = w.get("to")
                original = w.get("from")
                try:
                    current = backend.get(name)
                except Exception as exc:  # noqa: BLE001
                    if CARD_ABSENT in str(exc):
                        # No card bound: this control does not exist, so there is
                        # nothing left to restore and nothing that could sound.
                        report["steps"].append({
                            "kind": "control", "name": name,
                            "decision": "not_applicable_no_card",
                            "applied": applied, "original": original})
                        report["not_applicable"] = report.get("not_applicable", 0) + 1
                        continue
                    report["steps"].append({"kind": "control", "name": name,
                                            "decision": "read_failed", "error": str(exc)})
                    report["failed"] += 1
                    continue
                decision = core.rollback_decision(current, applied, original)
                if observe:
                    decision = ("already_original" if current == original
                                else ("would_restore" if current == applied
                                      else "skip_drift"))
                step = {"kind": "control", "name": name, "decision": decision,
                        "current": current, "applied": applied, "original": original}
                if decision == "would_restore":
                    report["failed"] += 1
                if decision == "restore":
                    try:
                        backend.set(name, original)
                        after = backend.get(name)
                        step["after"] = after
                        if after == original:
                            report["restored"] += 1
                        else:
                            step["decision"] = "restore_readback_mismatch"
                            report["failed"] += 1
                    except Exception as exc:  # noqa: BLE001
                        step["decision"] = "restore_failed"
                        step["error"] = str(exc)
                        report["failed"] += 1
                elif decision == "skip_drift":
                    report["skipped_drift"] += 1
                report["steps"].append(step)
        elif kind in ("modprobe", "rmmod"):
            module = evd.get("module") or manifest_module_for(manifest, aid)
            if not module:
                report["steps"].append({"kind": kind, "action": aid,
                                        "decision": "skip_unknown_module"})
                continue
            if kind == "modprobe":
                if args.dry_run or observe:
                    report["steps"].append({
                        "kind": "rmmod", "module": module,
                        "decision": ("already_unloaded" if not module_loaded(module)
                                     else "would_rmmod")})
                    continue
                if not module_loaded(module):
                    report["steps"].append({"kind": "rmmod", "module": module,
                                            "decision": "already_unloaded"})
                    continue
                p = core.run(["/usr/sbin/rmmod", module], timeout=20)
                step = {"kind": "rmmod", "module": module, "rc": p.returncode,
                        "stderr": p.stderr.strip()}
                if p.returncode == 0:
                    step["decision"] = "restored"
                elif "in use" in p.stderr:
                    # ASoC holds the codec while any i2c client is still bound.
                    # The disciplined path is unbind-then-rmmod, never --force.
                    step["decision"] = "in_use_requires_unbind"
                    step["hint"] = ("echo 15-0034 > /sys/bus/i2c/drivers/tfa2/unbind; "
                                    "same for 15-0035; then rmmod (no --force)")
                else:
                    step["decision"] = "failed"
                if p.returncode == 0:
                    report["restored"] += 1
                else:
                    report["failed"] += 1
                report["steps"].append(step)
        elif kind == "ucm":
            card = evd.get("card") or args.card
            cmd = ["alsaucm", "-c", str(card), "set", "_verb", "reset"]
            if args.dry_run:
                report["steps"].append({"kind": "ucm", "decision": "would_reset",
                                        "cmd": cmd})
                continue
            p = core.run(cmd, timeout=15)
            report["steps"].append({"kind": "ucm", "cmd": cmd, "rc": p.returncode,
                                    "decision": "restored" if p.returncode == 0
                                    else "failed"})
        elif kind == "service":
            report["steps"].append({"kind": "service", "unit": evd.get("unit"),
                                    "decision": "manual_review",
                                    "note": "service state is restored from the "
                                            "journal by the operator, not guessed"})
    report["verdict"] = ("pass" if report["failed"] == 0 else "fail")
    out = args.out or os.path.join(dirs.srv, "rollback-report.json")
    core.dump_json(out, report)
    if not observe:
        core.jsonl_append(dirs.journal, dict(entry,
                                             state="VERIFIED" if report[
                                                 "failed"] == 0 else "APPLIED",
                                             evidence=report))
    print(json.dumps(report, indent=2, sort_keys=True))
    return EXIT_OK if report["failed"] == 0 else EXIT_STOP_FAILED


def manifest_module_for(manifest, action_id):
    for a in manifest.get("actions") or []:
        if a.get("id") == action_id:
            return a.get("module")
    return None


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def build_parser():
    import argparse

    p = argparse.ArgumentParser(prog="kebab-audio", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--policy", default=DEFAULT_POLICY)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--run", required=True)
        sp.add_argument("--run-dir", default=None)
        sp.add_argument("--srv-dir", default=None)
        sp.add_argument("--fake-root", default=None,
                        help="use a JSON fixture backend instead of real ALSA")
        sp.add_argument("--card", default=None, help="ALSA card index")

    sp = sub.add_parser("inventory")
    sp.add_argument("--out", default=None)
    sp.add_argument("--fake-root", default=None)
    sp.set_defaults(func=cmd_inventory)

    sp = sub.add_parser("guard")
    add_common(sp)
    sp.add_argument("--manifest-sha", default=None)
    sp.add_argument("--boot-id", default=None)
    sp.add_argument("--run-id-check", default=None)
    sp.add_argument("--max-seconds", type=float, default=None)
    sp.add_argument("--allow-no-stop-actions", action="store_true")
    sp.add_argument("--apply-stop-at-arm", action="store_true",
                    help="actually apply stop actions during the arm probe "
                         "(default is a read-only probe)")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_guard)

    sp = sub.add_parser("run")
    add_common(sp)
    sp.add_argument("--action", required=True)
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("verify")
    add_common(sp)
    sp.add_argument("--out", default=None)
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("rollback")
    add_common(sp)
    sp.add_argument("--out", default=None)
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--verify-only", dest="verify_only", action="store_true",
                    help="reconcile the end state read-only: change nothing, "
                         "charge no rollback budget")
    sp.set_defaults(func=cmd_rollback)

    sp = sub.add_parser("status")
    add_common(sp)
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("selftest")
    sp.add_argument("--out", default=None)
    sp.add_argument("--fake-root", default=None)
    sp.set_defaults(func=cmd_selftest)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
