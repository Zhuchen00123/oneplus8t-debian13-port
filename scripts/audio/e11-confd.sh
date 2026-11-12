#!/bin/bash
# Install the UCM in the conf.d path and see whether WirePlumber now uses it.
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
sleep 2
cat /proc/asound/cards
DRV=$(cat /sys/class/sound/card0/device/driver 2>/dev/null | sed "s#.*/##")
[ -n "$DRV" ] || DRV=sm8250
echo "sysfs driver: [$DRV]"

echo "== install the conf.d entry =="
DEST=/usr/share/alsa/ucm2/conf.d/$DRV
install -d -m 0755 "$DEST"
install -m 0644 "/tmp/kebab-audio-selftest/ucm/conf.d/sm8250/OnePlus 8T.conf" "$DEST/OnePlus 8T.conf"
install -m 0644 "/tmp/kebab-audio-selftest/ucm/O8T/HiFi.conf" "$DEST/HiFi.conf"
sed -i "s#/conf.d/sm8250/HiFi.conf#/conf.d/$DRV/HiFi.conf#" "$DEST/OnePlus 8T.conf"
ls -l "$DEST"
sha256sum "$DEST"/* | tee "$S/confd-sha.txt"

echo "== does alsa-lib find it through the hw path? =="
env -u ALSA_CONFIG_UCM2 alsaucm -c hw:0 list _verbs 2>&1 | head -4

echo "== restart wireplumber =="
sudo -n -u debian $U systemctl --user restart wireplumber 2>&1 | tail -1
sleep 4
sudo -n -u debian $U wpctl status 2>/dev/null | sed -n "/Audio/,/Video/p" | head -16

echo "== device profile =="
sudo -n -u debian $U pw-dump > "$S/pw-dump3.json" 2>&1
python3 - "$S/pw-dump3.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
for o in d:
    if o.get("type") != "PipeWire:Interface:Device":
        continue
    p=o.get("info",{}).get("props",{}) or {}
    if p.get("device.api") != "alsa":
        continue
    print("  card:", p.get("api.alsa.card.name"), "profile:", p.get("device.profile.name"),
          "ucm.verb:", p.get("api.alsa.ucm.verb"))
    for k,v in (o.get("info",{}).get("params",{}) or {}).items():
        if k == "EnumProfile":
            print("   profiles:", json.dumps(v)[:500])
PY

SINK=$(sudo -n -u debian $U wpctl status 2>/dev/null | sed -n "/Sinks:/,/Sources:/p" | grep -oE "[0-9]+\. " | head -1 | tr -dc "0-9")
echo "sink id: [$SINK]"
if [ -n "$SINK" ]; then
  sudo -n -u debian $U wpctl set-default "$SINK" 2>&1 | tail -1
  run probe-pipewire
  python3 -c "
import json
d=json.load(open('$S/evidence/action-probe-pipewire.json'))
print('  play_rc=%s corr=%s peak=%s stderr=%s' % (d.get('play_rc'), d.get('corr'), d.get('peak_db'), (d.get('play_stderr') or '')[:200]))
" 2>&1
else
  echo "no sink yet"
fi

kill -TERM $GPID 2>/dev/null; wait $GPID 2>/dev/null; echo "guardian rc=$?"
kill $BPID 2>/dev/null
cat /proc/asound/cards
