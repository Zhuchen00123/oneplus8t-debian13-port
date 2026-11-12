#!/bin/bash
# What PCMs does the card actually expose, and why is there no sink?
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

run load-tfa2
sleep 2
cat /proc/asound/cards
echo "== playback PCMs =="
aplay -l 2>&1 | head -8
echo "== capture PCMs =="
arecord -l 2>&1 | head -8
echo "== /proc/asound/devices =="
cat /proc/asound/devices | head -12

DEV=$(sudo -n -u debian $U wpctl status 2>/dev/null | sed -n "/Devices:/,/Sinks:/p" | grep -oE "[0-9]+\. " | head -1 | tr -dc "0-9")
echo "device=$DEV"
sudo -n -u debian $U wpctl set-profile "$DEV" 1 2>&1 | grep -v rtkit | tail -2
sleep 2
sudo -n -u debian $U wpctl status 2>/dev/null | sed -n "/Audio/,/Video/p" | head -18

echo "== pipewire / wireplumber log =="
sudo -n -u debian $U journalctl --user -u pipewire -u wireplumber --since "-2 min" --no-pager 2>&1 | grep -iE "error|fail|alsa|sink|profile|ucm" | tail -20

echo "== devices in the alsa node list =="
sudo -n -u debian $U pw-dump 2>/dev/null > "$S/pw-dump2.json"
python3 - "$S/pw-dump2.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
for o in d:
    t=o.get("type","")
    if not t.endswith(("Device","Node")):
        continue
    p=o.get("info",{}).get("props",{}) or {}
    if p.get("media.class") or p.get("device.api")=="alsa":
        print("  %-8s %-28s class=%-18s api=%s name=%s" % (
            o.get("id"), (p.get("node.name") or p.get("device.name") or "")[:28],
            p.get("media.class"), p.get("device.api"), p.get("api.alsa.pcm.subdevice","")))
PY

kill -TERM $GPID 2>/dev/null; wait $GPID 2>/dev/null; echo "guardian rc=$?"
kill $BPID 2>/dev/null
cat /proc/asound/cards
