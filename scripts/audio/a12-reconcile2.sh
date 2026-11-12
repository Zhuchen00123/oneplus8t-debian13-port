#!/bin/bash
set -u
RUN="$1"
S=/srv/kebab-audio-$RUN
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"
$KA rollback --run "$RUN" --verify-only --out "$S/rollback-report.json" >/dev/null 2>&1
echo "rollback rc=$?"
python3 -c "
import json
r=json.load(open('$S/rollback-report.json'))
print('mode',r.get('mode'),'verdict',r.get('verdict'),'failed',r.get('failed'))
"
$KA verify --run "$RUN" --out "$S/result.json" >/dev/null 2>&1
python3 - "$S" <<'PY'
import json, os, sys
s = sys.argv[1]
r = json.load(open(os.path.join(s, "result.json")))
print("overall:", r["capabilities"]["overall"])
for k, v in sorted(r["capabilities"].items()):
    if k == "overall":
        continue
    print("  %-18s %-10s %s" % (k, v["verdict"],
                                json.dumps(v["detail"], default=str)[:190]))
print("budget:", json.dumps(r["budget_charged"]))
PY
cd "$S" && sha256sum baseline.json control-map.json trial-manifest.json \
  guard-selftest-device.json guard-events.jsonl journal.jsonl \
  probe-evidence.json result.json rollback-report.json source-manifest.json \
  pre-state.txt source-hashes-device.txt > SHA256SUMS
