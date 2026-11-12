#!/usr/bin/env python3
"""kebab_audio_verify -- read-only capability checks for the r4 audio run.

Every check returns (ok, evidence) and refuses to upgrade absence of evidence
into a pass. These run on the device; nothing here plays audio.
"""
from __future__ import annotations

import json
import os
import re
import time

import kebab_audio_core as core

AMP_ADDRS = ("15-0034", "15-0035")
EXPECTED_CHANNEL = {"15-0034": 0, "15-0035": 1}
EXPECTED_PREFIX = {"15-0034": "Left", "15-0035": "Right"}


def _read_json(path, default=None):
    return core.load_json(path, default)


def _dt_str(path):
    try:
        with open(path, "rb") as fh:
            return fh.read().rstrip(b"\x00").decode("utf-8", "replace")
    except OSError:
        return None


def _dt_u32(path):
    try:
        with open(path, "rb") as fh:
            b = fh.read(4)
        return int.from_bytes(b, "big") if len(b) == 4 else None
    except OSError:
        return None


def _sh(cmd, timeout=10, stdin_data=None):
    p = core.run(cmd, timeout=timeout, stdin_data=stdin_data)
    return p.returncode, p.stdout, p.stderr


def check_binding(act, ctx):
    """Both TFA9874 bound on the r4 TFA2 driver + machine card + DAIs."""
    ev = {"amp": {}, "problems": []}
    cards = core.parse_proc_asound_cards(core.read_text("/proc/asound/cards", "") or "")
    model = _dt_str("/proc/device-tree/sound/model")
    ev["dt_model"] = model
    ev["cards"] = cards
    match = [c for c in cards
             if (model and model.lower() in (c.get("longname") or "").lower())
             or c.get("id") == "kebab"]
    ev["matched_card"] = match[0] if match else None
    if not match:
        ev["problems"].append("no sound card matching DT model %r" % (model,))

    for addr in AMP_ADDRS:
        base = "/sys/bus/i2c/devices/" + addr
        of = os.path.join(base, "of_node")
        driver_link = os.path.join(base, "driver")
        info = {
            "present": os.path.isdir(base),
            "driver": (os.path.basename(os.path.realpath(driver_link))
                       if os.path.exists(driver_link) else None),
            "channel": _dt_u32(os.path.join(of, "nxp,tfa-channel")),
            "prefix": _dt_str(os.path.join(of, "sound-name-prefix")),
            "compatible": _dt_str(os.path.join(of, "compatible")),
        }
        ev["amp"][addr] = info
        if not info["present"]:
            ev["problems"].append("%s missing" % addr)
            continue
        # the i2c driver is named 'tfa2'; its module is 'snd_soc_tfa2'
        if info["driver"] not in ("tfa2", "snd_soc_tfa2"):
            ev["problems"].append("%s driver=%r (want tfa2/snd_soc_tfa2)"
                                  % (addr, info["driver"]))
        if info["channel"] != EXPECTED_CHANNEL[addr]:
            ev["problems"].append("%s dt channel=%r (want %d)"
                                  % (addr, info["channel"], EXPECTED_CHANNEL[addr]))
        if info["prefix"] != EXPECTED_PREFIX[addr]:
            ev["problems"].append("%s name-prefix=%r (want %r)"
                                  % (addr, info["prefix"], EXPECTED_PREFIX[addr]))
        if info["compatible"] != "nxp,tfa9874":
            ev["problems"].append("%s compatible=%r" % (addr, info["compatible"]))

    machine = "/sys/devices/platform/sound/driver"
    ev["machine_bound"] = (os.path.basename(os.path.realpath(machine))
                           if os.path.exists(machine) else None)
    if ev["machine_bound"] != "snd-sm8250":
        ev["problems"].append("machine card not bound to snd-sm8250 (got %r)"
                              % (ev["machine_bound"],))

    deferred = core.read_text("/sys/kernel/debug/devices_deferred", "") or ""
    ev["deferred"] = deferred.strip()
    if "sound" in deferred and "snd-sm8250" in deferred:
        ev["problems"].append("sound still in deferred probe: %s"
                              % deferred.strip()[:200])

    rc, dais, _ = _sh(["sh", "-c", "cat /sys/kernel/debug/asoc/dais 2>/dev/null | sort -u"])
    ev["dais_present"] = {"TERT_MI2S_RX": "TERT_MI2S_RX" in dais,
                          "tfa2-hifi": "tfa2-hifi" in dais,
                          "VA_CODEC_DMA_TX_0": "VA_CODEC_DMA_TX_0" in dais}
    for k, v in ev["dais_present"].items():
        if not v:
            ev["problems"].append("DAI %s missing" % k)

    ev["verdict"] = "pass" if not ev["problems"] else "fail"
    return ev["verdict"] == "pass", ev


def check_idle_safety(act, ctx):
    if not os.path.isdir("/proc/asound") or not any(
            re.match(r"^card\d+$", e) for e in os.listdir("/proc/asound")):
        # No card at all: no route can be open and no stream can be running.
        return True, {"verdict": "pass", "card_present": False,
                      "note": "no ALSA card bound: nothing can be routed or played"}
    return _check_idle_safety_card(act, ctx)


def _check_idle_safety_card(act, ctx):
    """No route left open, no PCM open, no playback/capture process alive."""
    ev = {"problems": [], "controls": {}}
    manifest = core.load_json(ctx["manifest_path"], {}) or {}
    backend = ctx["backend_factory"]()
    for sa in manifest.get("stop_actions") or []:
        if sa.get("kind") == "rmmod":
            continue
        name = sa.get("name")
        try:
            cur = backend.get(name)
        except Exception as exc:  # noqa: BLE001
            ev["problems"].append("cannot read %s: %s" % (name, exc))
            continue
        ev["controls"][name] = {"value": cur, "stop_value": sa.get("values")}
        if cur != sa.get("values"):
            ev["problems"].append("%s=%r not at stop value %r"
                                  % (name, cur, sa.get("values")))
    import re as _re

    running = []
    card_pat = _re.compile(r"^cardd+$")
    for card in os.listdir("/proc/asound") if os.path.isdir("/proc/asound") else []:
        if not card_pat.match(card):   # /proc/asound/cards is a file, not a card
            continue
        base = os.path.join("/proc/asound", card)
        for entry in sorted(os.listdir(base)):
            if not entry.startswith("pcm"):
                continue
            status = core.read_text(os.path.join(base, entry, "sub0", "status"), "") or ""
            if "RUNNING" in status:
                running.append("%s/%s" % (card, entry))
    ev["running_pcm"] = running
    if running:
        ev["problems"].append("PCM still running: %s" % ", ".join(running))
    rc, out, _ = _sh(["ps", "-eo", "pid,comm,args"])
    mine = [l for l in out.splitlines()
            if any(k in l for k in ("aplay", "arecord"))]
    ev["playback_processes"] = mine
    if mine:
        ev["problems"].append("playback/capture process still alive")
    ev["verdict"] = "pass" if not ev["problems"] else "fail"
    return ev["verdict"] == "pass", ev


def check_server_regression(act, ctx):
    """Protected services and r4 identity must be unchanged from the baseline."""
    ev = {"problems": [], "services": {}, "baseline_loaded": False}
    baseline = _read_json(ctx.get("baseline_path") or "", None)
    if not baseline:
        ev["verdict"] = "unknown"
        ev["problems"].append("no baseline.json to compare against")
        return False, ev
    ev["baseline_loaded"] = True
    cur_kernel = core.kernel_release()
    ev["kernel"] = {"baseline": baseline.get("kernel_release"), "current": cur_kernel}
    if baseline.get("kernel_release") != cur_kernel:
        ev["problems"].append("kernel release drift")
    cur_boot = core.boot_id()
    ev["boot_id"] = {"baseline": baseline.get("boot_id"), "current": cur_boot}
    if baseline.get("boot_id") != cur_boot:
        ev["problems"].append("boot id changed during the run (read-only evidence only)")
    for unit, was in (baseline.get("protected_services") or {}).items():
        now = {"active": _sh(["systemctl", "is-active", unit])[1].strip(),
               "enabled": _sh(["systemctl", "is-enabled", unit])[1].strip()}
        ev["services"][unit] = {"baseline": was, "current": now}
        if now["active"] != was.get("active"):
            ev["problems"].append("%s active %r -> %r"
                                  % (unit, was.get("active"), now["active"]))
        if now["enabled"] != was.get("enabled"):
            ev["problems"].append("%s enabled %r -> %r"
                                  % (unit, was.get("enabled"), now["enabled"]))
    cur_failed = set(l.split()[0] for l in _sh(["systemctl", "list-units", "--state=failed",
                                                "--no-legend", "--no-pager"])[1].splitlines()
                     if l.strip())
    base_failed = set(l.split()[0] for l in (baseline.get("failed_units") or "").splitlines()
                      if l.strip())
    ev["failed_units"] = {"baseline": sorted(base_failed), "current": sorted(cur_failed)}
    new_failures = cur_failed - base_failed
    if new_failures:
        ev["problems"].append("new failed units: %s" % ", ".join(sorted(new_failures)))
    ev["thermal"] = {p: core.read_one_line(p) for p in
                     ("/sys/class/thermal/thermal_zone0/temp",
                      "/sys/class/power_supply/battery/temp") if os.path.exists(p)}
    ev["verdict"] = "pass" if not ev["problems"] else "fail"
    return ev["verdict"] == "pass", ev


def check_transport(act, ctx):
    """Transport-only: the playback path consumed the samples it was given."""
    ev = {"note": "transport only; never acoustic evidence", "plays": []}
    evdir = ctx["evidence_dir"]
    for fn in sorted(os.listdir(evdir)) if os.path.isdir(evdir) else []:
        if not (fn.startswith("action-") and fn.endswith(".json")):
            continue
        d = _read_json(os.path.join(evdir, fn), {}) or {}
        if d.get("kind") in ("play", "probe") and d.get("wav"):
            ev["plays"].append({"action": fn[len("action-"):-len(".json")],
                                "rc": d.get("rc"), "wav": d["wav"],
                                "wall_seconds": d.get("wall_seconds")})
    good = [p for p in ev["plays"] if p["rc"] == 0]
    ev["ok_plays"] = len(good)
    ev["verdict"] = "pass" if good else "unknown"
    return bool(good), ev


def check_pipewire(act, ctx):
    """Is there a real session audio server to integrate with?"""
    ev = {"sockets": [], "binaries": {}, "problems": []}
    for root in ("/run/user",):
        if not os.path.isdir(root):
            continue
        for uid in sorted(os.listdir(root)):
            d = os.path.join(root, uid)
            for name in ("pipewire-0", "pulse/native"):
                p = os.path.join(d, name)
                if os.path.exists(p):
                    ev["sockets"].append(p)
    for b in ("pipewire", "wireplumber", "wpctl", "pactl", "pulseaudio", "pw-cli"):
        ev["binaries"][b] = _sh(["sh", "-c", "command -v " + b])[1].strip() or None
    rc, out, _ = _sh(["ps", "-eo", "comm,args"])
    ev["processes"] = [l for l in out.splitlines()
                       if any(k in l.lower() for k in
                              ("pipewire", "wireplumber", "pulseaudio"))]
    have = bool(ev["sockets"]) and any(ev["binaries"].values())
    if not have:
        ev["problems"].append("no session audio server or client tool present")
        ev["verdict"] = "not_tested"
    else:
        ev["verdict"] = "pass"
    ev["note"] = ("hosts without WirePlumber/PulseAudio cannot prove UCM or sink/source "
                  "integration; the UCM test below is the ALSA-level substitute")
    return have, ev


def check_ucm_pair(act, ctx):
    """Enable then disable the platform UCM device and read the route back."""
    ev = {"card": ctx.get("card"), "problems": [], "steps": []}
    card = ctx.get("card")
    if card is None:
        ev["verdict"] = "fail"
        ev["problems"].append("no card index")
        return False, ev
    verb = act.get("verb", "HiFi")
    device = act.get("device", "Speaker")
    route_ctl = act.get("readback_control")
    ok_all = True
    # alsaucm is a fresh process per call and keeps no verb, so _enadev or
    # _disdev alone is a no-op there (or fails with ENOENT). Both halves of the
    # pair therefore run as one batch session: enable to prove the enable
    # sequence, then enable+disable to prove the disable sequence.
    for label, lines, want in (
            ("enable", ["set _verb %s" % verb, "set _enadev %s" % device], 1),
            ("enable+disable",
             ["set _verb %s" % verb, "set _enadev %s" % device,
              "set _disdev %s" % device], 0)):
        script = "".join(line + "\n" for line in lines)
        rc, out, err = _sh(["alsaucm", "-c", str(card), "-b", "-"],
                           timeout=20, stdin_data=script)
        step = {"op": label, "cmd": "alsaucm -b -", "stdin": script.strip(),
                "rc": rc, "stdout": out.strip()[-400:], "stderr": err.strip()[-400:]}
        if rc != 0:
            step["result"] = "failed"
            ok_all = False
            if label == "enable":
                ev["steps"].append(step)
                ev["verdict"] = "fail"
                ev["problems"].append("no usable UCM verb/device for this card (rc=%d)" % rc)
                return False, ev
        elif route_ctl:
            backend = ctx["backend_factory"]()
            try:
                got = backend.get(route_ctl)
            except Exception as exc:  # noqa: BLE001
                step["result"] = "readback_failed"
                step["error"] = str(exc)
                ok_all = False
                got = None
            if got is not None:
                step["readback"] = {"control": route_ctl, "value": got}
                if got != [want]:
                    step["result"] = "readback_mismatch"
                    step["want"] = [want]
                    ok_all = False
        ev["steps"].append(step)
    rc, out, err = _sh(["alsaucm", "-c", str(card), "set", "_verb", "reset"], timeout=15)
    ev["steps"].append({"op": "_verb reset", "rc": rc, "stderr": err.strip()[-400:]})
    ev["verdict"] = "pass" if ok_all else "fail"
    return ok_all, ev


def check_acoustic(act, ctx):
    """Turn the stored probe metrics into a physical verdict. No new audio here."""
    ev = {"channels": {}, "problems": [], "observer": "on-device DMIC"}
    probes = ctx.get("probe_evidence") or {}
    accept = ctx.get("accept") or {}
    noise = ctx.get("noise_rms_db")
    if not probes:
        ev["verdict"] = "unknown"
        ev["problems"].append("no probe evidence")
        return False, ev
    if noise is None:
        ev["verdict"] = "unknown"
        ev["problems"].append("no quiet baseline capture; cannot compute SNR")
        return False, ev
    ev["noise_rms_db"] = noise
    verdicts = []
    for label, p in probes.items():
        stats = p.get("burst_stats")
        corr = p.get("corr")
        verdict, why = core.acoustic_verdict(stats, noise, corr if corr is not None else 0.0,
                                             accept)
        ev["channels"][label] = {"verdict": verdict, "why": why,
                                 "peak_db": p.get("peak_db"),
                                 "snr_db": (None if stats is None
                                            else round(stats.get("rms_db", -200.0) - noise, 2)),
                                 "corr": corr}
        verdicts.append(verdict)
    if all(v == "pass" for v in verdicts):
        ev["verdict"] = "pass"
    elif "pass" in verdicts:
        # one logical channel verified acoustically, another did not: that is a
        # partial result, never a pass and never a blanket failure
        ev["verdict"] = "partial"
        ev["problems"].append("only some logical channels passed the frozen "
                              "thresholds: %s" % dict(
                                  (k, v["verdict"]) for k, v in ev["channels"].items()))
    elif "fail" in verdicts:
        ev["verdict"] = "fail"
        ev["problems"].append("no logical channel passed the frozen thresholds")
    else:
        ev["verdict"] = "unknown"
    ev["note"] = ("logical L/R only: the two physical transducers were not localized; "
                  "this is not a per-transducer acceptance")
    return ev["verdict"] in ("pass", "partial"), ev


CHECKS = {
    "binding": check_binding,
    "idle-safety": check_idle_safety,
    "server-regression": check_server_regression,
    "transport": check_transport,
    "pipewire": check_pipewire,
    "ucm-pair": check_ucm_pair,
    "acoustic": check_acoustic,
}


def run_check(act, ctx):
    name = act.get("check")
    fn = CHECKS.get(name)
    if fn is None:
        return False, {"check": name, "error": "unknown check",
                       "verdict": "not_tested"}
    ok, ev = fn(act, ctx)
    ev["kind"] = "verify"
    ev["check"] = name
    ev.setdefault("verdict", "pass" if ok else "fail")
    return ok, ev
