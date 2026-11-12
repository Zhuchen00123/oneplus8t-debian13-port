#!/bin/bash
# End-to-end: the same burst through ALSA and through PipeWire/WirePlumber.
set -uo pipefail
RUN="$1"
PREV="$2"
S=/srv/kebab-audio-$RUN
R=/run/kebab-audio-$RUN
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"
AG=/usr/local/lib/kebab-audio/kebab-audio-guard
U="env XDG_RUNTIME_DIR=/run/user/1000"
rm -rf "$S"; mkdir -p "$S" "$R"; chmod 700 "$S" "$R"

run() { echo "### $1"; $KA run --run "$RUN" --action "$1" 2>&1 | tail -4; echo "   rc=$?"; }

{
  echo "kernel=$(uname -r)"; echo "boot_id=$(cat /proc/sys/kernel/random/boot_id)"
  echo "uptime=$(cut -d" " -f1 /proc/uptime)"
} > "$S/pre-state.txt"; cat "$S/pre-state.txt"

echo "== pipewire state before =="
systemctl --user --machine=debian@.host is-active pipewire 2>/dev/null || true
sudo -n -u debian $U systemctl --user is-active pipewire wireplumber 2>&1 | tr "\n" " "; echo

python3 /tmp/kebab-audio-selftest/make-manifests10.py "$RUN" "$S" 0 hw:0,1 hw:0,0 \
  "/srv/kebab-audio-$PREV/control-map-live.json" || exit 1

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

echo "== load and let WirePlumber see the card =="
run load-tfa2
sleep 4
cat /proc/asound/cards
sudo -n -u debian $U wpctl status > "$S/wpctl-after-load.txt" 2>&1
sed -n "/Audio/,/Video/p" "$S/wpctl-after-load.txt" | head -18

echo "== pick the default sink =="
SINK=$(sudo -n -u debian $U wpctl status 2>/dev/null | sed -n "/Sinks:/,/Sources:/p" | grep -E "^ *[0-9]+\." | head -1)
echo "sink line: $SINK"
SINK_ID=$(echo "$SINK" | sed -n "s/^[^0-9]*\([0-9]\+\).*/\1/p")
if [ -n "$SINK_ID" ]; then sudo -n -u debian $U wpctl set-default "$SINK_ID" 2>&1; fi
sudo -n -u debian $U wpctl inspect @DEFAULT_AUDIO_SINK@ 2>/dev/null | head -14 > "$S/sink-inspect.txt"
cat "$S/sink-inspect.txt"

echo "== ALSA probe =="
run probe-alsa
echo "== PipeWire probe =="
run probe-pipewire

echo "== evidence =="
python3 -c "
import json
d=json.load(open('$S/probe-evidence.json'))
for k,v in sorted(d['probes'].items()):
    b=v.get('burst_stats') or {}
    print('  %-9s corr=%-9s peak=%-8s rms=%-8s play_cmd=%s' % (
        k, v.get('corr'), v.get('peak_db'), b.get('rms_db'), v.get('play_cmd')))
" 2>&1
for a in probe-alsa probe-pipewire; do
  echo "-- $a evidence --"
  python3 -c "
import json
d=json.load(open('$S/evidence/action-$a.json'))
print('  play_rc=%s play_wall=%s stderr=%s' % (d.get('play_rc'), d.get('play_wall_seconds'), (d.get('play_stderr') or '')[:200]))
" 2>&1
done

echo "== stop =="
kill -TERM $GPID 2>/dev/null; wait $GPID 2>/dev/null; echo "guardian rc=$?"
kill $BPID 2>/dev/null
cat /proc/asound/cards
