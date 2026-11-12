#!/bin/bash
# What hw_params does the amp see when PipeWire plays?
set -uo pipefail
RUN="$1"
S=/srv/kebab-audio-$RUN
R=/run/kebab-audio-$RUN
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"
AG=/usr/local/lib/kebab-audio/kebab-audio-guard
U="env XDG_RUNTIME_DIR=/run/user/1000"
rm -rf "$S"; mkdir -p "$S" "$R"; chmod 700 "$S" "$R"
run() { echo "### $1"; $KA run --run "$RUN" --action "$1" 2>&1 | tail -4; echo "   rc=$?"; }

python3 /tmp/kebab-audio-selftest/make-manifests13.py "$RUN" "$S" 0 hw:0,1 hw:0,0 \
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

run load-tfa2
run ucm-enable-speaker
echo "== enable driver debug =="
echo "file tfa2.c +p" > /sys/kernel/debug/dynamic_debug/control 2>/dev/null
grep -c "file tfa2.c" /sys/kernel/debug/dynamic_debug/control 2>/dev/null || true

DEV=$(sudo -n -u debian $U wpctl status 2>/dev/null | sed -n "/Devices:/,/Sinks:/p" | grep -oE "[0-9]+\. " | head -1 | tr -dc "0-9")
sudo -n -u debian $U wpctl set-profile "$DEV" 1 > /dev/null 2>&1
sleep 2
SINK=$(sudo -n -u debian $U wpctl status 2>/dev/null | sed -n "/Sinks:/,/Sources:/p" | grep -oE "[0-9]+\. " | head -1 | tr -dc "0-9")
sudo -n -u debian $U wpctl set-default "$SINK" > /dev/null 2>&1
echo "device=$DEV sink=$SINK"
MARK=$(dmesg 2>/dev/null | wc -l)
echo "dmesg mark=$MARK"

echo "== PipeWire playback =="
run probe-pipewire
sleep 0.5
echo "-- new dmesg lines --"
dmesg 2>/dev/null | tail -n +$((MARK + 1)) | grep -iE "hw_params|tfa2|slot" | tail -8
echo "-- all new lines (last 12) --"
dmesg 2>/dev/null | tail -n +$((MARK + 1)) | tail -12

echo "== capture spectrum =="
python3 /tmp/analyze-capture.py "/run/kebab-audio-$RUN/cap-pw.wav" 2>&1 | sed -n "1,4p"
python3 -c "import json;d=json.load(open('$S/evidence/action-probe-pipewire.json'));print('corr=%s peak=%s' % (d.get('corr'), d.get('peak_db')))"

kill -TERM $GPID 2>/dev/null; wait $GPID 2>/dev/null; echo "guardian rc=$?"
kill $BPID 2>/dev/null
cat /proc/asound/cards