#!/usr/bin/env python3
"""kebab_audio_core -- deterministic primitives for the r4 host-audio executor.

Design rules enforced here (see docs/audio-agent-workflow-20261112.md):
  * every hardware action is write-ahead journalled and budget-reserved BEFORE it happens
  * the budget is charged on RESERVE, never on success, so a crash cannot refund it
  * the device-side guard owns silence; the runner only requests actions
  * no pure function in this file touches hardware -- they are all unit-testable
  * a control write is refused unless the control is in the frozen control map

Only the Python standard library is used: the device ships python3 3.13 and has no pip.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

SCHEMA = 1

# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------


def sha256_bytes(data):
    import hashlib

    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def boot_time():
    """Device monotonic clock bound to the current boot (seconds, fractional).

    /proc/uptime is CLOCK_BOOTTIME-ish (includes suspend) and resets at boot,
    which is exactly what the lease contract needs. Wall clock is only logged.
    """
    with open("/proc/uptime", "r", encoding="ascii") as fh:
        return float(fh.read().split()[0])


def boot_id():
    with open("/proc/sys/kernel/random/boot_id", "r", encoding="ascii") as fh:
        return fh.read().strip()


def kernel_release():
    return os.uname().release


def run(cmd, timeout=None, check=False, stdin_data=None, env=None):
    """Run a command, capture text, never raise on non-zero unless check=True.

    argv is always a list; nothing here goes through a shell.
    """
    proc = subprocess.run(
        cmd,
        input=stdin_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        text=True,
        env=env,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            "command failed rc=%d: %s\n%s" % (proc.returncode, cmd, proc.stderr.strip())
        )
    return proc


def read_text(path, default=None):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return default


def read_one_line(path, default=None):
    txt = read_text(path)
    if txt is None:
        return default
    return txt.strip()


def mkdir_private(path, mode=0o700):
    if not os.path.isdir(path):
        os.makedirs(path, mode=mode)
    os.chmod(path, mode)


def atomic_write(path, data, mode=0o600):
    """Write via tmp+rename and fsync, so a crash leaves either old or new bytes."""
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "wb") as fh:
        if isinstance(data, str):
            data = data.encode("utf-8")
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    try:
        dfd = os.open(os.path.dirname(path) or ".", os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def dump_json(path, obj, mode=0o600):
    atomic_write(path, json.dumps(obj, indent=2, sort_keys=True) + "\n", mode=mode)


def load_json(path, default=None):
    txt = read_text(path)
    if txt is None:
        return default
    try:
        return json.loads(txt)
    except ValueError:
        return default


def jsonl_append(path, obj, mode=0o600):
    """Append one durable line. fsync per record: the journal must survive a crash."""
    line = json.dumps(obj, sort_keys=True) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, mode)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def jsonl_read(path):
    out = []
    txt = read_text(path)
    if not txt:
        return out
    for line in txt.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            out.append({"corrupt": line})
    return out


# --------------------------------------------------------------------------
# policy / budget -- pure logic, unit tested offline
# --------------------------------------------------------------------------

COST_CLASSES = ("playback_nonzero_ms", "playback_attempt", "capture_seconds",
                "capture_count", "module_load", "route_variant", "rollback",
                "trial_install", "replay", "audio_service_restart")


def budget_caps(policy):
    b = policy.get("experiment_budget", {})
    a = policy.get("acoustic_probe", {})
    c = policy.get("capture_probe", {})
    return {
        "playback_nonzero_ms": int(a.get("max_nonzero_playback_seconds_total", 3)) * 1000,
        "playback_attempt": int(a.get("max_playback_attempts", 6)),
        "capture_seconds": int(c.get("max_capture_seconds_total", 40)),
        "capture_count": int(c.get("max_captures", 8)),
        "module_load": int(b.get("max_module_load_cycles", 2)),
        "route_variant": int(b.get("max_reviewed_route_variants", 6)),
        "rollback": int(b.get("max_rollback_transactions_total", 2)),
        "trial_install": int(b.get("max_trial_install_transactions", 1)),
        "replay": int(b.get("max_same_manifest_commit_replays", 1)),
        "audio_service_restart": int(b.get("max_validation_audio_session_restarts", 1)),
    }


def budget_check(used, caps, requests):
    """Return (ok, reasons). requests is a {class: amount} dict.

    A class that is not declared in caps is refused outright: an unknown cost
    must never be silently free.
    """
    reasons = []
    if not isinstance(requests, dict) or not requests:
        return False, ["empty cost request"]
    for cls, amount in requests.items():
        if cls not in COST_CLASSES:
            reasons.append("unknown cost class %r" % (cls,))
            continue
        if cls not in caps:
            reasons.append("no cap declared for %r" % (cls,))
            continue
        spent = int(used.get(cls, 0))
        if spent + int(amount) > int(caps[cls]):
            reasons.append(
                "%s: used %d + %d > cap %d" % (cls, spent, int(amount), int(caps[cls]))
            )
        if int(amount) <= 0:
            reasons.append("%s: non-positive amount %d" % (cls, int(amount)))
    return (not reasons), reasons


# --------------------------------------------------------------------------
# lease -- the contract between runner and guard
# --------------------------------------------------------------------------


def lease_expired(now_bt, heartbeat_bt, expiry_ms):
    if heartbeat_bt is None:
        return True
    return (now_bt - float(heartbeat_bt)) * 1000.0 > float(expiry_ms)


def lease_identity_ok(lease, run_id, expect_boot_id, manifest_sha):
    """Every field that the silence guarantee depends on must match exactly."""
    problems = []
    if not isinstance(lease, dict):
        return False, ["lease missing"]
    if lease.get("run_id") != run_id:
        problems.append("run_id %r != %r" % (lease.get("run_id"), run_id))
    if lease.get("boot_id") != expect_boot_id:
        problems.append("boot_id drift: lease %r != current %r"
                        % (lease.get("boot_id"), expect_boot_id))
    if manifest_sha and lease.get("manifest_sha") != manifest_sha:
        problems.append("manifest drift")
    if not lease.get("card"):
        problems.append("lease has no card binding")
    if not lease.get("route"):
        problems.append("lease has no route binding")
    return (not problems), problems


# --------------------------------------------------------------------------
# control map -- refusals are the whole point
# --------------------------------------------------------------------------


def control_entry(control_map, name):
    ctrls = (control_map or {}).get("controls") or {}
    return ctrls.get(name)


def control_write_allowed(control_map, name, values):
    """A write is allowed only for a frozen control with a known type/count/range.

    Returns (ok, reason, original_values). Unknown fields are never written.
    """
    entry = control_entry(control_map, name)
    if entry is None:
        return False, "control %r not in frozen control-map" % (name,), None
    ctype = entry.get("type")
    count = entry.get("count")
    if ctype not in ("INT", "ENUM", "BOOL"):
        return False, "control %r has unsupported type %r" % (name, ctype), None
    if not isinstance(count, int) or count <= 0:
        return False, "control %r has no audited count" % (name,), None
    if not isinstance(values, list) or len(values) != count:
        return False, "control %r expects %d values, got %r" % (name, count, values), None
    for v in values:
        if not isinstance(v, int):
            return False, "control %r value %r is not an int" % (name, v), None
    if ctype == "INT":
        lo, hi = entry.get("min"), entry.get("max")
        if not isinstance(lo, int) or not isinstance(hi, int):
            return False, "control %r has no audited min/max" % (name,), None
        for v in values:
            if v < lo or v > hi:
                return False, "control %r value %d outside audited range %d..%d" % (
                    name, v, lo, hi), None
    elif ctype == "ENUM":
        items = entry.get("items") or []
        if not items:
            return False, "control %r has no audited enum items" % (name,), None
        for v in values:
            if v < 0 or v >= len(items):
                return False, "control %r enum index %d outside 0..%d" % (
                    name, v, len(items) - 1), None
    elif ctype == "BOOL":
        for v in values:
            if v not in (0, 1):
                return False, "control %r bool value %r not 0/1" % (name, v), None
    return True, "ok", entry.get("value")


def control_write_is_change(entry_original, values):
    return list(entry_original or []) != list(values or [])


# --------------------------------------------------------------------------
# rollback decision -- never overwrite somebody else's drift
# --------------------------------------------------------------------------


def rollback_decision(current, candidate_applied, original):
    """Decide what to do for one owned object.

    current == candidate  -> restore original (we still own it)
    current == original   -> already restored / never changed -> noop
    otherwise             -> drift, owned by somebody else -> skip, report
    """
    if candidate_applied is None or original is None or current is None:
        return "skip_unknown"
    if list(current or []) == list(candidate_applied or []):
        if list(original or []) == list(candidate_applied or []):
            return "noop"
        return "restore"
    if list(current or []) == list(original or []):
        return "noop"
    return "skip_drift"


# --------------------------------------------------------------------------
# capture / acoustic metrics -- pure, testable, pessimistic by default
# --------------------------------------------------------------------------


def wav_mono_s16(path):
    """Read a 16-bit WAV into (samples, rate, channels)."""
    import wave

    with wave.open(path, "rb") as wf:
        if wf.getsampwidth() != 2:
            raise ValueError("only S16_LE supported")
        nch = wf.getnchannels()
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    import array

    a = array.array("h")
    a.frombytes(raw)
    if sys.byteorder == "big":
        a.byteswap()
    return a, rate, nch


def wav_write_mono_s16(path, samples, rate, channels=1):
    """Write interleaved S16_LE. channels=1 as before; channels=2 for stereo."""
    import array
    import wave

    a = array.array("h", samples)
    if sys.byteorder == "big":
        a.byteswap()
    with wave.open(path, "wb") as wf:
        wf.setnchannels(int(channels))
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(a.tobytes())


INT16_FULL_SCALE = 32768.0


def db_from_rms(rms):
    """Linear amplitude -> dBFS. -48 dBFS source must read about -48, not +42."""
    import math

    if rms <= 0:
        return -200.0
    return 20.0 * math.log10(rms / INT16_FULL_SCALE)


def channel_stats(samples, nch, from_index=0, to_index=None):
    """Per-channel RMS/peak dBFS, clip and flatline counters for a window."""
    import math

    if to_index is None:
        to_index = len(samples) // nch
    out = []
    for ch in range(nch):
        seg = samples[from_index * nch + ch: to_index * nch: nch]
        n = len(seg)
        if n == 0:
            out.append({"rms_db": -200.0, "peak_db": -200.0, "clipped": 0,
                        "flat": True, "nonzero": 0, "total_samples": 0})
            continue
        peak = max(abs(min(seg)), abs(max(seg)))
        ssum = sum(float(v) * float(v) for v in seg)
        rms = math.sqrt(ssum / n)
        clipped = sum(1 for v in seg if v >= 32767 or v <= -32768)
        out.append({
            "rms_db": round(db_from_rms(rms), 2),
            "peak_db": round(db_from_rms(peak), 2),
            "clipped": int(clipped),
            "flat": peak == 0,
            "nonzero": int(sum(1 for v in seg if v != 0)),
            "total_samples": n,
        })
    return out


def normalized_correlation(a, b):
    """Zero-lag normalised cross-correlation in [-1, 1]; 0 when either is silent.

    Uses map(operator.mul, ...) rather than a Python-level loop: the probe path
    has a hard deadline, and a slow correlator already cost one attempt its
    deadline in the 20261112t1115z run.
    """
    import math
    import operator

    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    mul = operator.mul
    sa = sum(map(mul, a[:n], a[:n]))
    if sa == 0:
        return 0.0
    sb = sum(map(mul, b[:n], b[:n]))
    if sb == 0:
        return 0.0
    dot = sum(map(mul, a[:n], b[:n]))
    return dot / math.sqrt(float(sa) * float(sb))


def _corr_at(a, b, lag):
    if lag < 0:
        x, y = a[-lag:], b[:len(b) + lag]
    elif lag > 0:
        x, y = a[:len(a) - lag], b[lag:]
    else:
        x, y = a, b
    return normalized_correlation(x, y)


def best_lag_correlation(a, b, max_lag, stride=8):
    """Max |normalised correlation| over +/-max_lag, coarse-to-fine.

    Two passes: a strided sweep to find the bearing, then a +-stride refinement.
    The answer is the same as an exhaustive sweep whenever the correlation lobe is
    wider than the stride, which it is for a 1 kHz burst at 48 kHz.
    """
    best = 0.0
    best_lag = 0
    for lag in range(-max_lag, max_lag + 1, max(1, stride)):
        c = _corr_at(a, b, lag)
        if abs(c) > abs(best):
            best, best_lag = c, lag
    coarse = best_lag
    for lag in range(coarse - stride + 1, coarse + stride):
        if abs(lag) > max_lag:
            continue
        c = _corr_at(a, b, lag)
        if abs(c) > abs(best):
            best, best_lag = c, lag
    return best, best_lag


def block_rms_db(samples, nch, rate, block_ms=50, skip_s=0.0):
    """RMS of each short block, in dBFS. Used for envelopes and noise floors."""
    import math

    blk = max(1, int(rate * block_ms / 1000.0))
    mono = list(samples[0::nch])
    skip = int(skip_s * rate)
    out = []
    i = skip
    while i + blk <= len(mono):
        seg = mono[i:i + blk]
        rms = math.sqrt(sum(float(v) * v for v in seg) / len(seg))
        out.append(db_from_rms(rms))
        i += blk
    return out


def noise_floor_db(samples, nch, rate, block_ms=50, skip_s=0.2, percentile=0.25):
    """Robust quiet level: the low quartile of block RMS.

    A capture that starts with an amplifier/DMIC pop would otherwise report a
    'noise floor' tens of dB too high, which both hides real signal and makes
    every SNR meaningless. The pop is excluded by skip_s and by using a low
    percentile rather than the mean.
    """
    vals = block_rms_db(samples, nch, rate, block_ms, skip_s)
    if not vals:
        return None
    vals = sorted(vals)
    idx = min(len(vals) - 1, max(0, int(len(vals) * percentile)))
    return round(vals[idx], 2)


def find_burst(samples, nch, rate, win_ms=20, skip_s=0.0):
    """Locate the loudest short window and return a burst region around it.

    Used to align the captured speaker output with the generated reference
    without assuming the capture started exactly when playback did.
    """
    win = max(1, int(rate * win_ms / 1000.0))
    mono = list(samples[0::nch])
    if len(mono) < win * 2:
        return None, None
    best_i, best_e = 0, -1.0
    i = int(skip_s * rate)
    while i + win <= len(mono):
        e = 0.0
        for v in mono[i:i + win]:
            e += float(v) * float(v)
        if e > best_e:
            best_e, best_i = e, i
        i += max(1, win // 2)
    if best_e <= 0:
        return None, None
    start = max(0, best_i - 2 * win)
    end = min(len(mono), best_i + 5 * win)
    return start, end


def slice_mono(samples, nch, start, end, ch=0):
    return list(samples[start * nch + ch: end * nch: nch])


# Accept thresholds are frozen in the trial manifest BEFORE any probe runs.
DEFAULT_ACCEPT = {
    "min_snr_db": 10.0,
    "min_abs_corr": 0.6,
    "max_clipped_fraction": 0.0,
    "require_nonzero": True,
}


def acoustic_verdict(burst_stats, noise_rms_db, corr, accept=None):
    """Verdict for one logical channel: (verdict, reasons).

    verdict is 'pass' | 'fail' | 'unknown'. 'unknown' means we could not
    observe (digitally silent capture / no observer); never a quiet pass.
    """
    acc = dict(DEFAULT_ACCEPT)
    if accept:
        acc.update(accept)
    reasons = []
    if burst_stats is None:
        return "unknown", ["no capture window"]
    peak = burst_stats.get("peak_db", -200.0)
    if burst_stats.get("flat") or peak <= -120.0:
        return "unknown", ["capture window is digitally silent (no observer)"]
    snr = float(burst_stats.get("rms_db", -200.0)) - float(noise_rms_db)
    if burst_stats.get("nonzero", 0) == 0 and acc["require_nonzero"]:
        reasons.append("window has no non-zero samples")
    if snr < acc["min_snr_db"]:
        reasons.append("snr %.1f dB < %.1f dB" % (snr, acc["min_snr_db"]))
    if abs(corr) < acc["min_abs_corr"]:
        reasons.append("abs(corr) %.2f < %.2f" % (abs(corr), acc["min_abs_corr"]))
    total = burst_stats.get("total_samples") or 0
    clipped = burst_stats.get("clipped", 0)
    frac = (float(clipped) / total) if total else 1.0
    if frac > acc["max_clipped_fraction"]:
        reasons.append("clipped fraction %.4f > %.4f" % (frac, acc["max_clipped_fraction"]))
    if reasons:
        return "fail", reasons
    return "pass", []


def capture_continuity_ok(duration_s, errors, min_seconds, eio_seen):
    """A capture only counts if long enough and it did not die on EIO."""
    problems = []
    if eio_seen:
        problems.append("EIO during capture")
    if duration_s < min_seconds:
        problems.append("continuous %.2fs < %.2fs" % (duration_s, min_seconds))
    for e in errors or []:
        problems.append("capture error: %s" % (e,))
    return (not problems), problems


def is_hardware_pcm(pcm_name):
    """Null / plugin / loopback / monitor PCMs are not hardware evidence."""
    if not pcm_name:
        return False, "no pcm name"
    bad = ("null", "default", "sysdefault", "loopback", "aloop", "dmix", "dsnoop",
           "pulse", "pipewire", "monitor", "surround")
    low = pcm_name.lower()
    head = low.split("/", 1)[0]
    segments = [s for s in low.replace(":", "/").split("/") if s]
    for b in bad:
        if head == b or head.startswith(b + ":") or b in segments:
            return False, "pcm %r is not a hardware PCM (%s)" % (pcm_name, b)
    if not (low.startswith("hw:") or "card=" in low or "sysdefault" in low):
        if "hdmi" in low:
            return False, "pcm %r is not a hardware PCM (hdmi)" % (pcm_name,)
        return False, "pcm %r does not pin a hardware card" % (pcm_name,)
    if "hdmi" in low:
        return False, "pcm %r is not a hardware PCM (hdmi)" % (pcm_name,)
    return True, "ok"


# --------------------------------------------------------------------------
# amixer parsing -- deterministic, no locale dependence
# --------------------------------------------------------------------------


def parse_amixer_cget(text):
    """Parse amixer cget output into a control descriptor."""
    info = {"values": [], "type": None, "count": None, "min": None, "max": None,
            "items": [], "name": None}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("; name="):
            info["name"] = line.split("=", 1)[1]
        elif line.startswith("numid="):
            for part in line.split(","):
                if part.startswith("name="):
                    info["name"] = part.split("=", 1)[1].strip().strip("'")
                elif part.startswith("numid="):
                    info["numid"] = int(part.split("=", 1)[1])
        elif line.startswith("; type="):
            meta = line.strip()[1:].strip()
            fields = {}
            for part in meta.split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    fields[k.strip()] = v.strip()
            info["type"] = fields.get("type")
            # 'items=9' on the type line is the enum COUNT; the item names come
            # from the '; Item #N' lines. Keeping them in separate fields stops
            # one from silently clobbering the other.
            for key in ("values", "min", "max", "items"):
                if key in fields:
                    try:
                        info["item_count" if key == "items" else
                             ("count" if key == "values" else key)] = int(fields[key])
                    except ValueError:
                        pass
            info["access"] = fields.get("access")
            info["step"] = fields.get("step")
        elif line.startswith(": values=") or line.startswith(": value="):
            # amixer prints BOOLEANs as 'on'/'off' and ENUMs as a number or a
            # quoted name, so a digits-only parser silently drops every switch.
            raw = line.split("=", 1)[1].strip()
            toks = [t.strip() for t in raw.split(",") if t.strip()]
            vals = []
            for t in toks:
                tl = t.lower()
                if tl == "on":
                    vals.append(1)
                elif tl == "off":
                    vals.append(0)
                elif t.lstrip("-").isdigit():
                    vals.append(int(t))
                else:
                    vals = []
                    info["enum_value"] = t.strip("'")
                    break
            info["values"] = vals
        elif line.startswith("Limits:"):
            parts = line.split(":", 1)[1].strip().split(" - ")
            if len(parts) == 2:
                try:
                    info["min"], info["max"] = int(parts[0]), int(parts[1])
                except ValueError:
                    pass
        elif line.startswith("; Item"):
            body = line.split("'", 2)
            if len(body) >= 2:
                info.setdefault("items", []).append(body[1])
        elif line.startswith("Items:"):
            body = line.split(":", 1)[1].strip()
            parts = []
            cur = ""
            inside = False
            for ch in body:
                if ch == "'":
                    if inside:
                        parts.append(cur)
                        cur = ""
                    inside = not inside
                elif inside:
                    cur += ch
            info["items"] = parts
    return info


def parse_amixer_scontrols(text):
    """Parse amixer scontrols output -> list of {iface, control}."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("Simple mixer control"):
            continue
        body = line.split("'", 2)
        if len(body) < 2:
            continue
        full = body[1]
        if "," in full:
            iface, name = full.split(",", 1)
        else:
            iface, name = "?", full
        out.append({"iface": iface.strip(), "control": name.strip()})
    return out


def parse_proc_asound_cards(text):
    """Parse /proc/asound/cards into [{index, id, driver, longname}].

    The real format is ' 0 [kebab          ]: kebab - Kebab-Sound-Card', i.e. a
    leading space, then the index, then the id in brackets.
    """
    import re

    cards = []
    pat = re.compile(r"^\s*(\d+)\s*\[([^\]]*)\]\s*:\s*(.*)$")
    for line in text.splitlines():
        if not line or line.strip().startswith("---"):
            continue
        m = pat.match(line)
        if not m:
            continue
        idx = int(m.group(1))
        cid = m.group(2).strip()
        after = m.group(3).strip()
        driver, _, longname = after.partition(" - ")
        if not longname:
            driver, longname = "", after
        cards.append({"index": idx, "id": cid, "driver": driver.strip(),
                      "longname": longname.strip(), "raw": line.rstrip()})
    return cards


# --------------------------------------------------------------------------
# journal -- write-ahead intent: RESERVED->ARMED->APPLIED->VERIFIED->RELEASED
# --------------------------------------------------------------------------

STATES = ("RESERVED", "ARMED", "APPLIED", "VERIFIED", "RELEASED")


def journal_new_entry(exp_id, action, cost, run_id, b_id, seq):
    return {
        "schema": SCHEMA,
        "exp_id": exp_id,
        "seq": seq,
        "run_id": run_id,
        "boot_id": b_id,
        "action": action,
        "cost": cost or {},
        "state": "RESERVED",
        "wall_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "boot_time": boot_time(),
    }


def journal_charge(entries):
    """Recompute spend from the journal, once per experiment id.

    One action writes several state transitions for the same exp_id
    (RESERVED, APPLIED, VERIFIED). The reservation is what costs budget, so
    each exp_id is charged exactly once, at its largest declared cost.

    A terminal state never refunds: budget is spent when it is reserved, not
    when the action succeeds. That is precisely what stops a crash from
    handing the operator a fresh budget.
    """
    per_exp = {}
    for e in entries:
        eid = e.get("exp_id")
        if not eid:
            continue
        if e.get("state") == "REFUNDED":
            continue
        cost = e.get("cost") or {}
        prev = per_exp.get(eid)
        if prev is None or sum(int(v) for v in cost.values()) >                 sum(int(v) for v in prev.values()):
            per_exp[eid] = cost
    total = {}
    for cost in per_exp.values():
        for cls, amount in cost.items():
            total[cls] = total.get(cls, 0) + int(amount)
    return total


def journal_state_of(entries, exp_id):
    st = None
    for e in entries:
        if e.get("exp_id") == exp_id:
            st = e.get("state")
    return st


def journal_uncertain_hardware(entries):
    """Actions reserved/applied but never VERIFIED/RELEASED.

    After an agent/SSH restart these keep their full charge and are never
    replayed. Returns sorted [(exp_id, action)].
    """
    pending = {}
    for e in entries:
        eid = e.get("exp_id")
        if not eid:
            continue
        if e.get("state") in ("RESERVED", "ARMED", "APPLIED"):
            pending[eid] = e.get("action")
        elif e.get("state") in ("VERIFIED", "RELEASED"):
            pending.pop(eid, None)
    return sorted(pending.items())
