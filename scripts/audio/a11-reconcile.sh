#!/bin/bash
# Reconcile the final state read-only and record it truthfully.
set -u
RUN="$1"
S=/srv/kebab-audio-$RUN
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"

echo "== observed end state =="
lsmod | grep tfa2 || echo "module: not loaded"
cat /proc/asound/cards
for d in 15-0034 15-0035; do printf '%s driver: ' "$d"; readlink /sys/bus/i2c/devices/$d/driver 2>/dev/null || echo UNBOUND; done
echo "blacklist lines: $(grep -c blacklist /etc/modprobe.d/90-kebab-server.conf)"
echo "kernel=$(uname -r) boot=$(cat /proc/sys/kernel/random/boot_id)"

echo "== read-only reconciliation (no charge, no writes) =="
$KA rollback --run "$RUN" --verify-only --out "$S/rollback-report.json" 2>&1 | head -8
python3 - "$S" <<'PY'
import json, os, sys
s = sys.argv[1]
r = json.load(open(os.path.join(s, "rollback-report.json")))
print("mode:", r.get("mode"))
print("verdict:", r.get("verdict"), "failed:", r.get("failed"),
      "not_applicable:", r.get("not_applicable"))
for st in r.get("steps", []):
    print("  %-8s %-10s %-42s %s" % (st.get("kind"), st.get("module") or "",
                                     st.get("name") or "", st.get("decision")))
PY

echo "== regenerate result.json + checksums =="
$KA verify --run "$RUN" --out "$S/result.json" >/dev/null 2>&1
python3 - "$S" <<'PY'
import json, os, sys
s = sys.argv[1]
r = json.load(open(os.path.join(s, "result.json")))
print("overall:", r["capabilities"]["overall"])
for k, v in sorted(r["capabilities"].items()):
    if k == "overall":
        continue
    print("  %-18s %-10s" % (k, v["verdict"]))
print("budget:", json.dumps(r["budget_charged"]))
PY
cd "$S" && sha256sum baseline.json control-map.json trial-manifest.json \
  guard-selftest-device.json guard-events.jsonl journal.jsonl \
  probe-evidence.json result.json rollback-report.json source-manifest.json \
  pre-state.txt source-hashes-device.txt > SHA256SUMS
echo "== checksums =="
cat SHA256SUMS
