#!/bin/bash
# Why did WirePlumber create the device but no sink?
set -uo pipefail
RUN="$1"
S=/srv/kebab-audio-$RUN
R=/run/kebab-audio-$RUN
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"
AG=/usr/local/lib/kebab-audio/kebab-audio-guard
U="env XDG_RUNTIME_DIR=/run/user/1000"
rm -rf "$S"; mkdir -p "$S" "$R"; chmod 700 "$S" "$R"

run() { echo "### $1"; $KA run --run "$RUN" --action "$1" 2>&1 | tail -3; echo "   rc=$?"; }

python3 /tmp/kebab-audio-selftest/make-manifests11.py "$RUN" "$S" 0 hw:0,1 hw:0,0 \
  "/srv/kebab-audio-20261112t1225z/control-map-live.json" || exit 1
python3 /tmp/kebab-audio-selftest/make-guard-plan.py "$S" "$RUN" "$S/guard-plan.txt" 0 > /dev/null
"$AG" arm --plan "$S/guard-plan.txt" --run-dir "$R" --srv-dir "$S" > "$S/guard-arm.log" 2>&1 &
GPID=$!
for i in $(seq 1 40); do
  st=$("$AG" status --run-dir "$R" 2>/dev/null | grep -o '"state": "[a-z]*"' | head -1)
  [ "$st" = '"state": "armed"' ] && break
  sleep 0.1
done
cat "$S/guard-arm.log"
"$AG" beat --run-dir "$R" --interval-ms 200 --parent-pid $$ > /dev/null 2>&1 &
BPID=$!
trap 'kill $BPID 2>/dev/null' EXIT

echo "== load =="
run load-tfa2
sleep 4
cat /proc/asound/cards

DEV=$(sudo -n -u debian $U wpctl status 2>/dev/null | sed -n "/Devices:/,/Sinks:/p" | grep -E "^ *[0-9]+\." | head -1 | sed -n "s/^[^0-9]*\([0-9]\+\).*/\1/p")
echo "device id: $DEV"
echo "== inspect device =="
sudo -n -u debian $U wpctl inspect "$DEV" > "$S/device-inspect.txt" 2>&1
cat "$S/device-inspect.txt" | head -40

echo "== profiles seen by wireplumber =="
sudo -n -u debian $U pw-dump "$DEV" > "$S/pw-dump-device.json" 2>&1
python3 - "$S/pw-dump-device.json" <<'PY'
import json,sys
try:
    d=json.load(open(sys.argv[1]))
except Exception as e:
    print("dump parse failed:", e); raise SystemExit
for obj in d:
    if obj.get("type") != "PipeWire:Interface:Device":
        continue
    p = obj.get("info",{}).get("props",{}) or {}
    params = obj.get("info",{}).get("params",{}) or {}
    print("  device.name:", p.get("device.name"), "api:", p.get("device.api"))
    print("  profile:", p.get("device.profile.name"), "cards:", p.get("api.alsa.card.name"), p.get("api.alsa.card.id"))
    for k,v in params.items():
        if k in ("Profile","EnumProfile","Route","EnumRoute"):
            print("  param", k, "=", json.dumps(v)[:600])
PY

echo "== wireplumber log =="
journalctl --user-unit=wireplumber --since "-3 min" --no-pager 2>/dev/null | tail -25 || \
  sudo -n -u debian $U journalctl --user-unit=wireplumber --since "-3 min" --no-pager 2>&1 | tail -25

echo "== restart wireplumber with the card present =="
sudo -n -u debian $U systemctl --user restart wireplumber 2>&1 | tail -2
sleep 4
sudo -n -u debian $U wpctl status 2>/dev/null | sed -n "/Audio/,/Video/p" | head -16

echo "== stop =="
kill -TERM $GPID 2>/dev/null; wait $GPID 2>/dev/null; echo "guardian rc=$?"
kill $BPID 2>/dev/null
cat /proc/asound/cards
