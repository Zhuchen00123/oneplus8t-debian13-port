#!/usr/bin/env python3
"""Facts about the retained probe evidence and the escalation capture."""
import json, math, os, sys
sys.path.insert(0, "/usr/local/lib/kebab-audio")
import kebab_audio_core as core

RUN = sys.argv[1]; D = "/run/kebab-audio-" + RUN; S = "/srv/kebab-audio-" + RUN
pe = core.load_json(os.path.join(S, "probe-evidence.json"), {}) or {}
print("noise_rms_db:", pe.get("noise_rms_db"))
for k, v in (pe.get("probes") or {}).items():
    print("probe %-6s corr=%s burst_start=%s peak=%s rms=%s" %
          (k, v.get("corr"), v.get("burst_start_s"), v.get("peak_db"),
           (v.get("burst_stats") or {}).get("rms_db")))

def g1k(samples, rate):
    n = len(samples)
    if not n: return 0.0
    k = int(0.5 + n * 1000.0 / rate)
    w = 2.0 * math.pi * k / n
    c = 2.0 * math.cos(w); s1 = s2 = 0.0
    for v in samples:
        s0 = v + c * s1 - s2; s2, s1 = s1, s0
    p = s1*s1 + s2*s2 - c*s1*s2
    return math.sqrt(max(p, 0.0)) / n

for label, fn in (("cap-l2", "cap-l2.wav"),):
    p = os.path.join(D, fn)
    if not os.path.exists(p):
        print(label, "missing"); continue
    s, rate, nch = core.wav_mono_s16(p)
    quiet = core.noise_floor_db(s, nch, rate)
    env = core.block_rms_db(s, nch, rate, 20, 0.2)
    best_i = max(range(len(env)), key=lambda i: env[i])
    print("%s noise_floor=%.1f loudest_block_after0.2s=%.1f at %.2fs" %
          (label, quiet, env[best_i], 0.2 + best_i * 0.02))
    blk = int(rate * 0.02)
    seg = core.slice_mono(s, nch, int((0.2 + best_i*0.02)*rate),
                          int((0.2 + best_i*0.02)*rate) + blk)
    print("   1kHz goertzel in that block: %.1f  (5 samples/period check: %d)" %
          (g1k(seg, rate), len(seg)))
    st, en = core.find_burst(s, nch, rate, skip_s=0.2)
    if st:
        win = core.slice_mono(s, nch, st, en)
        print("   burst %.2f..%.2fs  rms=%.1f  peak=%.1f  g1k=%.2f" % (
            st/rate, en/rate, core.channel_stats(s, nch, st, en)[0]["rms_db"],
            core.channel_stats(s, nch, st, en)[0]["peak_db"], g1k(win, rate)))
