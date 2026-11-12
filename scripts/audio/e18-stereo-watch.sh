#!/bin/bash
# Stereo tone through PipeWire while pw-top watches the graph.
set -uo pipefail
RUN="$1"
S=/srv/kebab-audio-$RUN
R=/run/kebab-audio-$RUN
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"
AG=/usr/local/lib/kebab-audio/kebab-audio-guard
U="env XDG_RUNTIME_DIR=/run/user/1000"
rm -rf "$S"; mkdir -p "$S" "$R"; chmod 700 "$S" "$R"
run() { echo "### $1"; $KA run --run "$RUN" --action "$1" 2>&1 | tail -3; echo "   rc=$?"; }

python3 /tmp/kebab-audio-selftest/make-manifests15.py "$RUN" "$S" 0 hw:0,1 hw:0,0 \
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
sleep 1
DEV=$(sudo -n -u debian $U wpctl status 2>/dev/null | sed -n "/Devices:/,/Sinks:/p" | grep -oE "[0-9]+\. " | head -1 | tr -dc "0-9")
sudo -n -u debian $U wpctl set-profile "$DEV" 1 > /dev/null 2>&1
sleep 2
SINK=$(sudo -n -u debian $U wpctl status 2>/dev/null | sed -n "/Sinks:/,/Sources:/p" | grep -oE "[0-9]+\. " | head -1 | tr -dc "0-9")
sudo -n -u debian $U wpctl set-default "$SINK" > /dev/null 2>&1
echo "sink=$SINK"
sudo -n -u debian $U pw-dump "$SINK" 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
p=d[0]["info"]["props"]
for k in ("audio.channels","audio.rate","audio.format","node.mute","node.volume","api.alsa.period-size","api.alsa.period-num","resample.disable"):
    if k in p: print("   %s = %s" % (k, p[k]))
"
echo "== pw-top during playback =="
sudo -n -u debian $U timeout 6 pw-top -b -n 3 > "$S/pw-top.txt" 2>&1 &
TPID=$!
sleep 1
run probe-pipewire
wait $TPID 2>/dev/null
sed -n "1,14p" "$S/pw-top.txt"

python3 -c "import json;d=json.load(open('$S/evidence/action-probe-pipewire.json'));print('corr=%s peak=%s burst=%s' % (d.get('corr'), d.get('peak_db'), d.get('burst')))"
python3 /tmp/analyze-capture.py "/run/kebab-audio-$RUN/cap-pw.wav" 2>&1 | sed -n "1,8p"

kill -TERM $GPID 2>/dev/null; wait $GPID 2>/dev/null; echo "guardian rc=$?"
kill $BPID 2>/dev/null
cat /proc/asound/cards