#!/usr/bin/env python3
"""Recompute probe evidence from the RETAINED captures with the reference taken
from the channel that actually carries the tone.

This fixes a measurement bug (the reference was always read from channel 0, so a
right-channel probe was compared against digital silence). The accept thresholds
frozen in the manifest are NOT touched.
"""
import json
import os
import sys

sys.path.insert(0, "/usr/local/lib/kebab-audio")
import kebab_audio_core as core

RUN = sys.argv[1]
D = "/run/kebab-audio-" + RUN
S = "/srv/kebab-audio-" + RUN

PAIRS = [("left", "cap-l.wav", "ref-l.wav", 0),
         ("right", "cap-r.wav", "ref-r.wav", 1)]

ev = core.load_json(os.path.join(S, "probe-evidence.json"), {}) or {}
ev.setdefault("probes", {})
report = []
for label, capf, reff, ch in PAIRS:
    cp, rp = os.path.join(D, capf), os.path.join(D, reff)
    if not (os.path.exists(cp) and os.path.exists(rp)):
        report.append({"label": label, "error": "capture or reference missing"})
        continue
    cs, cr, cn = core.wav_mono_s16(cp)
    rs, rr, rn = core.wav_mono_s16(rp)
    ref = core.slice_mono(rs, rn, 0, len(rs) // rn, ch=ch)
    ref_energy = sum(float(v) * v for v in ref)
    start, end = core.find_burst(cs, cn, cr, skip_s=0.15)
    if start is None:
        report.append({"label": label, "error": "no burst after the start pop"})
        continue
    win = core.slice_mono(cs, cn, start, end)
    stats = core.channel_stats(cs, cn, start, end)[0]
    corr, lag = core.best_lag_correlation(ref, win, int(cr * 0.05))
    entry = {
        "burst_stats": stats,
        "corr": round(corr, 4),
        "peak_db": stats.get("peak_db"),
        "channel": label,
        "ref_channel": ch,
        "lag_frames": lag,
        "burst_start_s": round(start / float(cr), 3),
        "source_action": "probe-%s" % label,
        "reanalysed": True,
        "ref_energy_zero": ref_energy == 0,
    }
    ev["probes"][label] = entry
    report.append({"label": label, "corr": entry["corr"],
                   "peak_db": entry["peak_db"], "burst_start_s": entry["burst_start_s"],
                   "ref_energy_zero": entry["ref_energy_zero"]})
core.dump_json(os.path.join(S, "probe-evidence.json"), ev, 0o600)
print(json.dumps(report, indent=1))
