#!/usr/bin/env python3
"""Append one reviewed action to the frozen phase-2 manifest (new sha required)."""
import json
import os
import sys

sys.path.insert(0, "/usr/local/lib/kebab-audio")
import kebab_audio_core as core

S = "/srv/kebab-audio-" + sys.argv[1]
RUN = sys.argv[1]
path = os.path.join(S, "trial-manifest.json")
m = json.load(open(path))
ids = [a["id"] for a in m["actions"]]
if "probe-left-2" in ids:
    print("already present")
    raise SystemExit
base = [a for a in m["actions"] if a["id"] == "probe-left"][0]
new = json.loads(json.dumps(base))
new.update({
    "id": "probe-left-2",
    "peak_dbfs": -42.0,
    "ref_path": "/run/kebab-audio-%s/ref-l2.wav" % RUN,
    "capture_path": "/run/kebab-audio-%s/cap-l2.wav" % RUN,
    "note": ("one step up the frozen amplitude ladder (-48 -> -42 dBFS) to "
             "separate 'too quiet at this DMIC position' from 'slot not driven'"),
})
# insert after probe-right, i.e. before the route-off teardown
out = []
for a in m["actions"]:
    if a["id"] == "route-off":
        out.append(new)
    out.append(a)
m["actions"] = out
m.setdefault("notes", []).append(
    "probe-left-2 added after the -48 dBFS left probe produced no detectable tone; "
    "thresholds unchanged, amplitude ladder step only")
core.dump_json(path, m, 0o600)
print("actions:", [a["id"] for a in m["actions"]])
