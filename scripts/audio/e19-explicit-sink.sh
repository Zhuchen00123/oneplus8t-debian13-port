#!/bin/bash
# Give PipeWire a 2-channel S16 sink on hw:0,0 instead of ACP's 8-channel one.
set -uo pipefail
RUN="$1"
S=/srv/kebab-audio-$RUN
R=/run/kebab-audio-$RUN
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"
AG=/usr/local/lib/kebab-audio/kebab-audio-guard
U="env XDG_RUNTIME_DIR=/run/user/1000"
rm -rf "$S"; mkdir -p "$S" "$R"; chmod 700 "$S" "$R"
run() { echo "### $1"; $KA run --run "$RUN" --action "$1" 2>&1 | tail -3; echo "   rc=$?"; }

python3 /tmp/kebab-audio-selftest/make-manifests16.py "$RUN" "$S" 0 hw:0,1 hw:0,0 \
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
echo "== create a 2-channel S16 sink on hw:0,0 =="
sudo -n -u debian $U pw-cli create-node adapter '{ factory.name=api.alsa.pcm.sink node.name=o8t-speaker api.alsa.path="hw:0,0" audio.channels=2 audio.format=S16LE audio.rate=48000 media.class=Audio/Sink object.linger=true }' > "$S/create-node.txt" 2>&1
cat "$S/create-node.txt"
sleep 2
sudo -n -u debian $U wpctl status 2>/dev/null | sed -n "/Audio/,/Video/p" | head -14
echo "== the new node =="
sudo -n -u debian $U wpctl status 2>/dev/null | grep -i "o8t\|Speaker" | head -5

echo "== probe through the new sink =="
run probe-pipewire
python3 -c "import json;d=json.load(open('$S/evidence/action-probe-pipewire.json'));print('corr=%s peak=%s stderr=%s' % (d.get('corr'), d.get('peak_db'), (d.get('play_stderr') or '')[:200]))"
echo "-- capture spectrum --"
python3 /tmp/analyze-capture.py "/run/kebab-audio-$RUN/cap-pw.wav" 2>&1 | sed -n "1,10p"

kill -TERM $GPID 2>/dev/null; wait $GPID 2>/dev/null; echo "guardian rc=$?"
kill $BPID 2>/dev/null
cat /proc/asound/cards