#!/usr/bin/env python3
"""Characterise what a probe actually recorded.

Correlation with the reference answers "is the tone there"; this answers "what
is there instead", which is what a broken slot or a mis-decoded TDM frame looks
like. Reads a WAV from tmpfs and prints numbers only -- the PCM never leaves the
device.
"""
import math
import struct
import sys


def read_wav_mono(path):
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise SystemExit("not a wav: %s" % path)
    pos = 12
    fmt = None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])  # (format, channels, rate, byterate, align, bits)
        elif cid == b"data":
            _afmt, ch, rate, _brate, _align, bits = fmt
            width = bits // 8
            frames = len(body) // (width * ch)
            out = []
            for i in range(frames):
                off = i * width * ch
                v = struct.unpack("<h", body[off:off + 2])[0]
                out.append(v)
            return out, rate, ch
        pos += 8 + size + (size & 1)
    raise SystemExit("no data chunk")


def goertzel(samples, rate, freq):
    n = len(samples)
    if n == 0:
        return 0.0
    w = 2.0 * math.pi * freq / rate
    coeff = 2.0 * math.cos(w)
    s0 = s1 = s2 = 0.0
    for x in samples:
        s0 = x + coeff * s1 - s2
        s2 = s1
        s1 = s0
    real = s1 - s2 * math.cos(w)
    imag = s2 * math.sin(w)
    return math.sqrt(real * real + imag * imag) * 2.0 / n


def db(v, full=32768.0):
    if v <= 0:
        return -999.0
    return 20.0 * math.log10(v / full)


def main():
    path = sys.argv[1]
    label = sys.argv[2] if len(sys.argv) > 2 else path
    samples, rate, ch = read_wav_mono(path)
    if ch > 1:
        samples = samples[0::ch]
    skip = int(rate * 0.2)
    body = samples[skip:]
    rms = math.sqrt(sum(float(v) * v for v in body) / max(1, len(body)))
    peak = max((abs(v) for v in body), default=0)
    print("[%s] rate=%d n=%d rms=%s peak=%s" % (label, rate, len(body),
                                                round(db(rms), 2), round(db(float(peak)), 2)))

    # Where is the burst? Work in 25 ms blocks and take the loudest run.
    blk = int(rate * 0.025)
    blocks = [body[i:i + blk] for i in range(0, len(body) - blk, blk)]
    energies = [sum(float(v) * v for v in b) / len(b) for b in blocks]
    if not energies:
        print("  no blocks")
        return
    k = max(range(len(energies)), key=lambda i: energies[i])
    lo = max(0, k - 2)
    hi = min(len(blocks), k + 6)
    window = [v for b in blocks[lo:hi] for v in b]
    print("  loudest block at %.2fs, analysing %.0f ms" % (
        lo * 0.025, 1000.0 * len(window) / rate))

    ref = goertzel(window, rate, 1000.0)
    print("  goertzel 1000Hz: %s dBFS" % round(db(ref), 2))

    scan = []
    f = 200.0
    while f <= 8000.0:
        scan.append((f, goertzel(window, rate, f)))
        f += 25.0
    scan.sort(key=lambda kv: -kv[1])
    print("  top peaks:")
    for f, m in scan[:6]:
        print("    %7.1f Hz  %8s dBFS" % (f, round(db(m), 2)))

    near1k = [m for f, m in scan if 900.0 <= f <= 1100.0]
    best1k = max(near1k) if near1k else 0.0
    other = [m for f, m in scan if f < 900.0 or f > 1100.0]
    best_other = max(other) if other else 0.0
    print("  1kHz band %s dBFS, best non-1kHz %s dBFS, ratio %s dB" % (
        round(db(best1k), 2), round(db(best_other), 2),
        round(db(best1k) - db(best_other), 2)))


if __name__ == "__main__":
    main()
