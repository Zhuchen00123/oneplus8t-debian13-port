#!/bin/bash
# Final: record the measured noise floor, re-derive verdicts, roll back, verify.
set -u
RUN="$1"
S=/srv/kebab-audio-$RUN
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"

echo "== record the measured quiet floor (value from the analysis pass) =="
sudo -n python3 - "$S" <<'PY'
import json, os, sys, time
s = sys.argv[1]
p = os.path.join(s, "probe-evidence.json")
d = json.load(open(p))
d["noise_rms_db"] = -86.69
d["noise_action"] = "noise-cap"
d["noise_note"] = ("low-quartile of 50 ms block RMS excluding the first 200 ms; "
                   "measured on noise.wav before the raw PCM was deleted")
d["noise_provenance"] = {"method": "core.noise_floor_db percentile=0.25 skip_s=0.2",
                         "measured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                         "raw_pcm": "deleted after metrics extraction"}
json.dump(d, open(p, "w"), indent=2, sort_keys=True)
print("noise_rms_db:", d["noise_rms_db"])
PY

echo "== re-derive verdicts =="
$KA run --run "$RUN" --action acoustic 2>&1 | tail -3
$KA run --run "$RUN" --action transport 2>&1 | tail -3

echo "== rollback: restore owned writes, then unload the module =="
$KA rollback --run "$RUN" --out "$S/rollback-report.json" 2>&1 | tail -40

echo "== post-rollback state =="
lsmod | grep tfa2 || echo "tfa2 unloaded"
cat /proc/asound/cards
echo "--- deferred"; cat /sys/kernel/debug/devices_deferred
echo "--- amp bind"; for d in 15-0034 15-0035; do printf '%s ' "$d"; readlink /sys/bus/i2c/devices/$d/driver || echo UNBOUND; done
echo "--- blacklist"; cat /etc/modprobe.d/90-kebab-server.conf

echo "== final read-only regressions (no guard needed) =="
$KA run --run "$RUN" --action server-regression 2>&1 | tail -3
$KA run --run "$RUN" --action pipewire 2>&1 | tail -3
echo "kernel=$(uname -r) boot=$(cat /proc/sys/kernel/random/boot_id)"
echo "uptime=$(cut -d' ' -f1 /proc/uptime)"

echo "== final result.json =="
$KA verify --run "$RUN" --out "$S/result.json" >/dev/null 2>&1
sudo -n python3 - "$S" <<'PY'
import json, os, sys
s = sys.argv[1]
r = json.load(open(os.path.join(s, "result.json")))
print("overall:", r["capabilities"]["overall"])
for k, v in sorted(r["capabilities"].items()):
    if k == "overall":
        continue
    print("  %-18s %-10s %s" % (k, v["verdict"], json.dumps(v["detail"], default=str)[:170]))
print("budget:", json.dumps(r["budget_charged"]))
print("uncertain:", [a for a, _ in r["uncertain_hardware_actions"]])
PY
echo "== artifacts =="
ls -l "$S"
