#!/usr/bin/env python3
"""Insert the load action so the reload is journaled like any other action."""
import json, os, sys
sys.path.insert(0, "/usr/local/lib/kebab-audio")
import kebab_audio_core as core

RUN = sys.argv[1]
S = "/srv/kebab-audio-" + RUN
p = os.path.join(S, "trial-manifest.json")
m = json.load(open(p))
if any(a["id"] == "load-tfa2" for a in m["actions"]):
    print("already present"); raise SystemExit
m["actions"].insert(0, {
    "id": "load-tfa2", "kind": "modprobe", "module": "snd_soc_tfa2",
    "cost": {"module_load": 1}, "timeout_s": 20,
    "note": "replay cycle: prove the card can be brought up a second time "
            "after a clean unload, from the same manifest lineage",
})
m.setdefault("notes", []).append("load-tfa2 added for the A6 replay cycle")
core.dump_json(p, m, 0o600)
print("actions:", [a["id"] for a in m["actions"]])
