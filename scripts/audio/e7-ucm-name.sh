#!/bin/bash
# Which UCM name does this card answer to, and does that give WirePlumber a sink?
set -uo pipefail
RUN="$1"
S=/srv/kebab-audio-$RUN
R=/run/kebab-audio-$RUN
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"
AG=/usr/local/lib/kebab-audio/kebab-audio-guard
U="env XDG_RUNTIME_DIR=/run/user/1000"
rm -rf "$S"; mkdir -p "$S" "$R"; chmod 700 "$S" "$R"

run() { echo "### $1"; $KA run --run "$RUN" --action "$1" 2>&1 | tail -3; echo "   rc=$?"; }

python3 /tmp/kebab-audio-selftest/make-manifests12.py "$RUN" "$S" 0 hw:0,1 hw:0,0 \
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
sleep 3
cat /proc/asound/cards
echo "== how does alsa name the card =="
cat /sys/class/sound/card0/id 2>/dev/null; echo

echo "== alsaucm by card id =="
env -u ALSA_CONFIG_UCM2 alsaucm -c O8T list _verbs 2>&1 | head -4
echo "== alsaucm by longname =="
env -u ALSA_CONFIG_UCM2 alsaucm -c "OnePlus 8T" list _verbs 2>&1 | head -4

echo "== the longname directory that ACP looks for =="
ls -l "/usr/share/alsa/ucm2/OnePlus 8T/" 2>&1 | head -4
env -u ALSA_CONFIG_UCM2 alsaucm -c "OnePlus 8T" list _verbs 2>&1 | head -4

echo "== restart wireplumber so it re-reads the card =="
sudo -n -u debian $U systemctl --user restart wireplumber 2>&1 | tail -2
sleep 4
sudo -n -u debian $U wpctl status 2>/dev/null | sed -n "/Audio/,/Video/p" | head -16

echo "== profiles now =="
sudo -n -u debian $U pw-dump > "$S/pw-dump-all.json" 2>&1
python3 - "$S/pw-dump-all.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
for obj in d:
    if obj.get("type") != "PipeWire:Interface:Device":
        continue
    p=obj.get("info",{}).get("props",{}) or {}
    if p.get("device.api") != "alsa":
        continue
    print("  card:", p.get("api.alsa.card.name"), "id:", p.get("api.alsa.card.id"),
          "profile:", p.get("device.profile.name"), "ucm:", p.get("api.alsa.ucm.verb"))
    for k,v in (obj.get("info",{}).get("params",{}) or {}).items():
        if k == "EnumProfile":
            print("   profiles:", json.dumps(v)[:700])
PY

SINK=$(sudo -n -u debian $U wpctl status 2>/dev/null | sed -n "/Sinks:/,/Sources:/p" | grep -E "^ *[0-9]+\." | head -1)
echo "sink: [$SINK]"
if [ -n "$SINK" ]; then
  SINK_ID=$(echo "$SINK" | sed -n "s/^[^0-9]*\([0-9]\+\).*/\1/p")
  sudo -n -u debian $U wpctl set-default "$SINK_ID" 2>&1
  echo "== PipeWire probe with a real sink =="
  run probe-pipewire
  python3 -c "
import json
d=json.load(open('$S/evidence/action-probe-pipewire.json'))
print('  play_rc=%s corr=%s peak=%s stderr=%s' % (d.get('play_rc'), d.get('corr'), d.get('peak_db'), (d.get('play_stderr') or '')[:160]))
" 2>&1
else
  echo "no sink: skipping the PipeWire probe"
fi

kill -TERM $GPID 2>/dev/null; wait $GPID 2>/dev/null; echo "guardian rc=$?"
kill $BPID 2>/dev/null
cat /proc/asound/cards
