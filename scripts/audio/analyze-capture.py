#!/usr/bin/env python3
"""Where is the energy in a capture, and is 1 kHz in it?"""
import math, struct, sys, wave

def read(path):
    w = wave.open(path, "rb")
    n, ch, rate, bits = w.getnframes(), w.getnchannels(), w.getframerate(), w.getsampwidth()
    raw = w.readframes(n)
    w.close()
    if bits != 2:
        raise SystemExit("expected 16-bit")
    vals = struct.unpack("<%dh" % (len(raw) // 2), raw)
    return [vals[i] for i in range(0, len(vals), ch)], rate, ch

def rms_db(x):
    if not x:
        return -120.0
    s = sum(float(v) * v for v in x) / len(x)
    return 20.0 * math.log10(math.sqrt(s) / 32768.0 + 1e-12)

def goertzel(x, rate, freq):
    n = len(x)
    if n == 0:
        return 0.0
    w = 2.0 * math.pi * freq / rate
    c = 2.0 * math.cos(w)
    s1 = s2 = 0.0
    for v in x:
        s0 = v + c * s1 - s2
        s2, s1 = s1, s0
    p = s1 * s1 + s2 * s2 - c * s1 * s2
    return math.sqrt(max(p, 0.0)) / n

mono, rate, ch = read(sys.argv[1])
block = int(rate * 0.05)
print("rate=%d frames=%d dur=%.2fs" % (rate, len(mono), len(mono) / float(rate)))
print(" t(s)   rms_dB   1k_amp  1k_dB")
for i in range(0, len(mono) - block + 1, block):
    seg = mono[i:i + block]
    a = goertzel(seg, rate, 1000.0)
    print("%5.2f  %7.1f  %7.1f  %6.1f" % (i / float(rate), rms_db(seg), a,
                                          20 * math.log10(a / 32768.0 + 1e-12)))
