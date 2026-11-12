#!/usr/bin/env python3
"""Show the FULL ALSA control names we care about, parsed from amixer contents."""
import subprocess
import sys

sys.path.insert(0, "/usr/local/lib/kebab-audio")
import kebab_audio_core as core

WANT = ["TERT_MI2S_RX Audio Mixer MultiMedia1",
        "MultiMedia2 Mixer VA_CODEC_DMA_TX_0",
        "VA_AIF1_CAP Mixer DEC0", "VA DMIC MUX0", "VA DEC0 MUX",
        "VA_DEC0 Volume", "VA_DEC0", "VA DMIC MUX1"]

p = subprocess.run(["amixer", "-c", "0", "contents"],
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
blocks = {}
cur = []
for line in p.stdout.splitlines():
    if line.startswith("numid=") and cur:
        info = core.parse_amixer_cget("\n".join(cur))
        if info.get("name"):
            blocks[info["name"]] = info
        cur = []
    cur.append(line)
if cur:
    info = core.parse_amixer_cget("\n".join(cur))
    if info.get("name"):
        blocks[info["name"]] = info
print("total controls parsed:", len(blocks))
for name in WANT:
    got = blocks.get(name)
    if got is None:
        near = [k for k in blocks if name.split()[0].lower() in k.lower()][:6]
        print("MISSING %-40s near=%s" % (name, near))
    else:
        print("OK      %-40s type=%-10s count=%s value=%s min=%s max=%s items=%s"
              % (name, got.get("type"), got.get("count"), got.get("values"),
                 got.get("min"), got.get("max"), got.get("items")))
