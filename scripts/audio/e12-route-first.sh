#!/bin/bash
# Enable the UCM route, then let the plain ALSA profile hand PipeWire a sink.
set -uo pipefail
RUN="$1"
S=/srv/kebab-audio-$RUN
R=/run/kebab-audio-$RUN
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"
AG=/usr/local/lib/kebab-audio/kebab-audio-guard
U="env XDG_RUNTIME_DIR=/run/user/1000"
rm -rf "$S"; mkdir -p "$S" "$R"; chmod 700 "$S" "$R"
run() { echo "### $1"; $KA run --run "$RUN" --action "$1" 2>&1 | tail -6; echo "   rc=$?"; }

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
sleep 2
cat /proc/asound/cards
echo "== enable the UCM route (budgeted action) =="
run ucm-enable-speaker
echo "TERT now: $(amixer -c 0 sget "TERT_MI2S_RX Audio Mixer MultiMedia1" 2>/dev/null | tail -1)"
echo "== can the PCM be opened now? =="
timeout 5 aplay -D hw:0,0 --dump-hw-params /usr/share/sounds/alsa/Noise.wav 2>&1 | head -8

echo "== give WirePlumber a profile =="
DEV=$(sudo -n -u debian $U wpctl status 2>/dev/null | sed -n "/Devices:/,/Sinks:/p" | grep -oE "[0-9]+\. " | head -1 | tr -dc "0-9")
echo "device=$DEV"
sudo -n -u debian $U wpctl set-profile "$DEV" 1 2>&1 | grep -v -i "rtkit\\|mod.rt" | tail -2
sleep 2
sudo -n -u debian $U wpctl status 2>/dev/null | sed -n "/Audio/,/Video/p" | head -16
SINK=$(sudo -n -u debian $U wpctl status 2>/dev/null | sed -n "/Sinks:/,/Sources:/p" | grep -oE "[0-9]+\. " | head -1 | tr -dc "0-9")
echo "sink=[]$SINK"
if [ -n "$SINK" ]; then
  sudo -n -u debian $U wpctl set-default "$SINK" 2>&1 | tail -1
  echo "== PipeWire probe =="
  run probe-pipewire
  python3 -c "
import json
d=json.load(open('$S/evidence/action-probe-pipewire.json'))
print('  play_rc=%s corr=%s peak=%s' % (d.get('play_rc'), d.get('corr'), d.get('peak_db')))
print('  stderr=%s' % ((d.get('play_stderr') or '')[:300]))
" 2>&1
else
  echo "still no sink"
  sudo -n -u debian $U journalctl --user -u pipewire --since "-3 min" --no-pager 2>&1 | tail -8
fi

kill -TERM $GPID 2>/dev/null; wait $GPID 2>/dev/null; echo "guardian rc=$?"
kill $BPID 2>/dev/null
cat /proc/asound/cards
