#!/bin/bash
# Run the reviewed phase-2 action sequence under the armed guard.
set -u
RUN="$1"
S=/srv/kebab-audio-$RUN
run() {
  echo "### $1"
  python3 /usr/local/lib/kebab-audio/kebab_audio.py run --run "$RUN" --action "$1" 2>&1 | tail -8
  echo "   rc=${PIPESTATUS[0]}"
}
run binding
run route-check
run noise-cap
run mic-3s
run probe-left
echo "   inter-probe quiet 5s (policy)"; sleep 5
run probe-right
echo "   inter-probe quiet 5s (policy)"; sleep 5
run route-off
run transport
run acoustic
run idle-safety
run server-regression
run pipewire
echo "== summary =="
python3 /usr/local/lib/kebab-audio/kebab_audio.py verify --run "$RUN" --out "$S/result.json" > /dev/null 2>&1
python3 - "$S" <<'PY'
import json, sys, os
s = sys.argv[1]
r = json.load(open(os.path.join(s, "result.json")))
print("overall:", r["capabilities"]["overall"])
for k, v in sorted(r["capabilities"].items()):
    if k == "overall":
        continue
    print("  %-18s %-10s %s" % (k, v["verdict"],
                                json.dumps(v["detail"], default=str)[:150]))
print("budget_charged:", r["budget_charged"])
print("uncertain:", r["uncertain_hardware_actions"])
PY
echo "== pcm files left in tmpfs =="
ls -l /run/kebab-audio-$RUN/*.wav 2>/dev/null
echo "== dmesg tail =="
sudo -n dmesg | tail -12
