#!/bin/bash
# End to end through the desktop stack: pw-play out, pw-record in, same window.
set -uo pipefail
RUN="$1"
S=/srv/kebab-audio-$RUN
R=/run/kebab-audio-$RUN
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"
AG=/usr/local/lib/kebab-audio/kebab-audio-guard
U="env XDG_RUNTIME_DIR=/run/user/1000"
rm -rf "$S"; mkdir -p "$S" "$R"; chmod 700 "$S" "$R"
run() { echo "### $1"; $KA run --run "$RUN" --action "$1" 2>&1 | tail -3; echo "   rc=$?"; }

echo "== bring the path up (this also enables the UCM route) =="
sudo -n /usr/local/sbin/kebab-audio-on 2>&1 | tail -12

python3 /tmp/kebab-audio-selftest/make-manifests17.py "$RUN" "$S" 0 hw:0,1 hw:0,0 \
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

echo "== graph before the probe =="
sudo -n -u debian $U wpctl status 2>/dev/null | sed -n "/Audio/,/Video/p" | head -16
echo "== streams seen while playing =="
sudo -n -u debian $U timeout 5 pw-top -b -n 3 > "$S/pw-top.txt" 2>&1 &
TPID=$!
sleep 0.5

run load-tfa2
run ucm-enable-speaker
run probe-pipewire
wait $TPID 2>/dev/null || true
sed -n "1,12p" "$S/pw-top.txt"

echo "== evidence =="
python3 - <<PY
import json
d = json.load(open('$S/evidence/action-probe-pipewire.json'))
for k in ('play_rc','capture_rc','corr','peak_db','play_wall_seconds','capture_cmd','play_cmd'):
    print('  %-16s %s' % (k, d.get(k)))
PY
echo "== capture spectrum (1 kHz should sit where the tone landed) =="
sudo -n python3 /tmp/analyze-capture.py "/run/kebab-audio-$RUN/cap-pw.wav" 2>&1 | sed -n "1,3p;10,26p"

kill -TERM $GPID 2>/dev/null; wait $GPID 2>/dev/null; echo "guardian rc=$?"
kill $BPID 2>/dev/null
echo "== bring it back down =="
sudo -n /usr/local/sbin/kebab-audio-off 2>&1 | tail -10