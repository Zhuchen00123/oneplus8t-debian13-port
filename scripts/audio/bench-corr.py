import math, random, sys, time
sys.path.insert(0, "/usr/local/lib/kebab-audio")
import kebab_audio_core as core

rate = 48000
ref_n = int(rate * 0.25)
rnd = random.Random(11)

def tone(n, freq, amp):
    return [int(amp * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)]

ref = tone(ref_n, 1000.0, 130)

def capture(freq=None, amp=100, noise=3, at=3000, total=None):
    n = total or (ref_n + 6000)
    cap = [rnd.randint(-noise, noise) for _ in range(n)]
    if freq is not None:
        body = tone(ref_n, freq, amp)
        for i, v in enumerate(body):
            if at + i < n:
                cap[at + i] += v
    return cap

cases = []
# 1. the tone is there
t0 = time.time()
c, _ = core.best_lag_correlation(capture(1000.0), ref, 2400)
dt = time.time() - t0
cases.append(("tone-present-detected", abs(c) > 0.9, "corr=%.4f" % c))
cases.append(("analysis-within-2s", dt < 2.0, "%.3fs" % dt))

# 2. silence / noise only
c, _ = core.best_lag_correlation(capture(None), ref, 2400)
cases.append(("noise-not-detected", abs(c) < 0.3, "corr=%.4f" % c))

# 3. a different frequency must not pass: this is what makes it a detector
#    rather than "any loud thing scores high"
for f in (800.0, 1200.0, 2000.0):
    c, _ = core.best_lag_correlation(capture(f), ref, 2400)
    cases.append(("freq-%d-rejected" % f, abs(c) < 0.6, "corr=%.4f" % c))

# 4. level independence: a quieter copy still detects, i.e. the metric is normalised
c, _ = core.best_lag_correlation(capture(1000.0, amp=8, noise=1), ref, 2400)
cases.append(("quiet-tone-detected", abs(c) > 0.9, "corr=%.4f" % c))

fails = 0
for name, ok, note in cases:
    print("%-24s %-5s %s" % (name, "ok" if ok else "FAIL", note))
    fails += 0 if ok else 1
print("VERDICT", "pass" if fails == 0 else "fail")
