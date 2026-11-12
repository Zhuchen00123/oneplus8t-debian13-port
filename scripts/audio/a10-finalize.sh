#!/bin/bash
# Finalize: clean rollback report, verdicts, checksums, source manifest.
set -u
RUN="$1"
S=/srv/kebab-audio-$RUN
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"

echo "== rollback report (state already at original) =="
$KA rollback --run "$RUN" --out "$S/rollback-report.json" 2>&1 | head -6
python3 - "$S" <<'PY'
import json, os, sys
s = sys.argv[1]
r = json.load(open(os.path.join(s, "rollback-report.json")))
print("rollback verdict:", r.get("verdict"))
for st in r.get("steps", []):
    print("  %-9s %-8s %-42s decision=%s" % (
        st.get("kind"), st.get("module") or "", st.get("name") or "",
        st.get("decision")))
PY

echo "== read-only verdict refresh =="
for a in transport acoustic idle-safety server-regression pipewire; do
  $KA run --run "$RUN" --action "$a" >/dev/null 2>&1; echo "  $a rc=$?"
done

echo "== result.json =="
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
                                json.dumps(v["detail"], default=str)[:200]))
print("budget:", json.dumps(r["budget_charged"]))
PY

echo "== source-manifest.json =="
python3 - "$S" "$RUN" <<'PY'
import hashlib, json, os, sys
s, run = sys.argv[1], sys.argv[2]
INST = "/usr/local/lib/kebab-audio"
files = {}
for name in sorted(os.listdir(INST)):
    p = os.path.join(INST, name)
    if os.path.isfile(p):
        files[p] = hashlib.sha256(open(p, "rb").read()).hexdigest()
for extra in ("/usr/local/sbin/kebab-audio",):
    if os.path.exists(extra):
        files[extra] = hashlib.sha256(open(extra, "rb").read()).hexdigest()
man = {"schema": 1, "run_id": run, "installed_files": files,
       "rollback_procedure": [
           "systemctl stop kebab-audio-guard-<run>",
           "/usr/local/sbin/kebab-audio rollback --run <run>",
           "for d in 15-0034 15-0035: echo $d > /sys/bus/i2c/drivers/tfa2/unbind",
           "rmmod snd_soc_tfa2   # plain, never --force",
           "verify /proc/asound/cards is empty and the amps are unbound",
       ]}
json.dump(man, open(os.path.join(s, "source-manifest.json"), "w"),
          indent=2, sort_keys=True)
print("installed files hashed:", len(files))
PY

echo "== SHA256SUMS =="
cd "$S" && sha256sum baseline.json control-map.json trial-manifest.json \
  guard-selftest-device.json guard-events.jsonl journal.jsonl \
  probe-evidence.json result.json rollback-report.json source-manifest.json \
  pre-state.txt source-hashes-device.txt > SHA256SUMS
cat SHA256SUMS
echo "== evidence files =="
ls -1 "$S/evidence" | head -30
