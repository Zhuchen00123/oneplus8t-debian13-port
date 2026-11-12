#!/bin/bash
# Did PipeWire's playback actually reach hw:0,0? Watch the PCM state.
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
sleep 1
DEV=$(sudo -n -u debian $U wpctl status 2>/dev/null | sed -n "/Devices:/,/Sinks:/p" | grep -oE "[0-9]+\. " | head -1 | tr -dc "0-9")
sudo -n -u debian $U wpctl set-profile "$DEV" 1 > /dev/null 2>&1
sleep 2
SINK=$(sudo -n -u debian $U wpctl status 2>/dev/null | sed -n "/Sinks:/,/Sources:/p" | grep -oE "[0-9]+\. " | head -1 | tr -dc "0-9")
echo "device=$DEV sink=$SINK"
sudo -n -u debian $U wpctl set-default "$SINK" > /dev/null 2>&1
echo "== sink node props =="
sudo -n -u debian $U wpctl inspect "$SINK" 2>/dev/null | grep -E "node.name|api.alsa|audio.channels|node.state|audio.format|audio.rate" | head -8
sudo -n -u debian $U pw-dump "$SINK" 2>/dev/null > "$S/sink.json"

echo "== pcm state before =="
cat /proc/asound/card0/pcm0p/sub0/status 2>/dev/null | head -3

# observer: sample the playback PCM state while the budgeted probe runs
( for i in $(seq 1 90); do
    printf "%s pcm0p=%s pcm1p=%s\n" "$(date +%H:%M:%S.%N | cut -c1-12)" \
      "$(sed -n "s/^state: //p" /proc/asound/card0/pcm0p/sub0/status 2>/dev/null)" \
      "$(sed -n "s/^state: //p" /proc/asound/card0/pcm1p/sub0/status 2>/dev/null)"
    sleep 0.05
  done ) > "$S/pcm-watch.txt" 2>&1 &
WPID=$!

run probe-pipewire
sleep 0.5
kill $WPID 2>/dev/null
echo "== pcm state during playback (unique) =="
sed "s/^[0-9:.]* //" "$S/pcm-watch.txt" | sort | uniq -c | sort -rn | head -6
echo "== hw_params seen while running =="
grep -c "pcm0p=RUNNING" "$S/pcm-watch.txt" || true

echo "== probe evidence =="
sudo -n cat "$S/evidence/action-probe-pipewire.json" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('  play_rc=%s corr=%s peak=%s burst=%s' % (d.get('play_rc'), d.get('corr'), d.get('peak_db'), d.get('burst')))
"
echo "== capture spectrum (where is the energy) =="
python3 /tmp/analyze-capture.py "/run/kebab-audio-$RUN/cap-pw.wav" 2>&1 | sed -n "1,6p;18,22p"

kill -TERM $GPID 2>/dev/null; wait $GPID 2>/dev/null; echo "guardian rc=$?"
kill $BPID 2>/dev/null
cat /proc/asound/cards
