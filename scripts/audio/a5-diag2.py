#!/usr/bin/env python3
"""Sliding-window correlation: is the detected 1 kHz tone coherent with the reference?"""
import json
import math
import os
import sys

sys.path.insert(0, "/usr/local/lib/kebab-audio")
import kebab_audio_core as core

RUN = sys.argv[1]
D = "/run/kebab-audio-" + RUN


def load(fn):
    s, r, n = core.wav_mono_s16(os.path.join(D, fn))
    return core.slice_mono(s, n, 0, len(s) // n), r


def band_env(mono, rate, freq, block_ms=20):
    blk = max(1, int(rate * block_ms / 1000.0))
    out = []
    for i in range(0, len(mono) - blk, blk):
        seg = mono[i:i + blk]
        n = len(seg)
        k = int(0.5 + n * freq / rate)
        w = 2.0 * math.pi * k / n
        c = 2.0 * math.cos(w)
        s1 = s2 = 0.0
        for v in seg:
            s0 = v + c * s1 - s2
            s2, s1 = s1, s0
        p = s1 * s1 + s2 * s2 - c * s1 * s2
        out.append(math.sqrt(max(p, 0.0)) / n)
    return out


def analyse(side, capf, reff):
    cap, cr = load(capf)
    ref, rr = load(reff)
    L = len(ref)
    best = (-2.0, 0, 0.0, 0.0)
    for start in range(0, len(cap) - L, 240):      # 5 ms hop
        win = cap[start:start + L]
        c0 = core.normalized_correlation(win, ref)
        if abs(c0) > abs(best[0]):
            best = (c0, start, 0, 0)
    env_cap = band_env(cap, cr, 1000.0)
    env_ref = band_env(ref, rr, 1000.0)
    peak_idx = max(range(len(env_cap)), key=lambda i: env_cap[i])
    return {
        "side": side,
        "capture_frames": len(cap),
        "best_zero_lag_corr": round(best[0], 4),
        "best_window_start_s": round(best[1] / float(cr), 3),
        "band1k_max_env_capture": round(env_cap[peak_idx], 2),
        "band1k_max_at_s": round(peak_idx * 0.02, 2),
        "band1k_ref_max": round(max(env_ref), 2),
        "band1k_capture_blocks_above_half_ref": sum(
            1 for v in env_cap if v > 0.5 * max(env_ref)),
        "band1k_ratio_db": round(20 * math.log10(
            (env_cap[peak_idx] + 1e-9) / (max(env_ref) + 1e-9)), 2),
    }


out = []
for side, capf, reff in (("left", "cap-l.wav", "ref-l.wav"),
                         ("right", "cap-r.wav", "ref-r.wav")):
    try:
        out.append(analyse(side, capf, reff))
    except Exception as exc:
        out.append({"side": side, "error": repr(exc)})
print(json.dumps(out, indent=1))
