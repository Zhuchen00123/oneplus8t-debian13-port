#!/usr/bin/env python3
"""Metrics-only analysis of the captured WAVs. Raw PCM never leaves the device."""
import json
import math
import os
import sys

sys.path.insert(0, "/usr/local/lib/kebab-audio")
import kebab_audio_core as core


def goertzel(samples, rate, freq):
    n = len(samples)
    if n == 0:
        return 0.0
    k = int(0.5 + n * freq / rate)
    w = 2.0 * math.pi * k / n
    c = 2.0 * math.cos(w)
    s1 = s2 = 0.0
    for v in samples:
        s0 = v + c * s1 - s2
        s2, s1 = s1, s0
    power = s1 * s1 + s2 * s2 - c * s1 * s2
    return math.sqrt(max(power, 0.0)) / n


def summary(path, label):
    if not os.path.exists(path):
        return {"label": label, "missing": True}
    s, rate, nch = core.wav_mono_s16(path)
    env = core.block_rms_db(s, nch, rate, 50, 0.0)
    quiet = core.noise_floor_db(s, nch, rate)
    after = core.block_rms_db(s, nch, rate, 50, 0.2)
    start, end = core.find_burst(s, nch, rate, skip_s=0.15)
    out = {"label": label, "duration_s": round(len(s) / float(nch) / rate, 3),
           "noise_floor_db": quiet, "first_block_db": round(env[0], 1) if env else None,
           "max_block_after_0.2s_db": round(max(after), 1) if after else None,
           "max_block_time_s": (round(0.2 + after.index(max(after)) * 0.05, 2)
                                if after else None)}
    if start is not None:
        win = core.slice_mono(s, nch, start, end)
        st = core.channel_stats(s, nch, start, end)[0]
        out["burst"] = {"start_s": round(start / float(rate), 3),
                        "rms_db": st["rms_db"], "peak_db": st["peak_db"],
                        "goertzel": {str(f): round(goertzel(win, rate, f), 1)
                                     for f in (200, 500, 800, 1000, 1200, 1500, 2000)},
                        "snr_vs_floor_db": round(st["rms_db"] - (quiet or -200), 1)}
    return out


def main():
    run = sys.argv[1]
    d = "/run/kebab-audio-" + run
    out = {}
    for label, fn in (("noise", "noise.wav"), ("mic", "mic.wav"),
                      ("cap-left", "cap-l.wav"), ("cap-right", "cap-r.wav")):
        out[label] = summary(os.path.join(d, fn), label)
    for side, capf, reff in (("left", "cap-l.wav", "ref-l.wav"),
                             ("right", "cap-r.wav", "ref-r.wav")):
        cp, rp = os.path.join(d, capf), os.path.join(d, reff)
        if not (os.path.exists(cp) and os.path.exists(rp)):
            continue
        cs, cr, cn = core.wav_mono_s16(cp)
        rs, rr, rn = core.wav_mono_s16(rp)
        ref = core.slice_mono(rs, rn, 0, len(rs) // rn)
        start, end = core.find_burst(cs, cn, cr, skip_s=0.15)
        if start is None:
            out.setdefault("corr", {})[side] = {"error": "no burst after 0.15s"}
            continue
        cap = core.slice_mono(cs, cn, start, end)
        best, lag = core.best_lag_correlation(cap, ref, int(cr * 0.05))
        # decimated copy of the reference vs capture, 1 kHz is 48 samples/cycle
        out.setdefault("corr", {})[side] = {
            "window_frames": len(cap), "ref_frames": len(ref),
            "best_corr": round(best, 4), "lag_frames": lag,
            "zero_lag_corr": round(core.normalized_correlation(cap, ref), 4)}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
