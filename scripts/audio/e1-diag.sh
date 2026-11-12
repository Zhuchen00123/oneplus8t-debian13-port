#!/bin/bash
# Diagnostic: what did the driver actually configure on each amplifier?
# Read-only except for one budgeted 250 ms probe.
set -u
set -o pipefail
RUN="$1"
PREV="$2"
S=/srv/kebab-audio-$RUN
R=/run/kebab-audio-$RUN
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"
AG=/usr/local/lib/kebab-audio/kebab-audio-guard
rm -rf "$S"; mkdir -p "$S" "$R"; chmod 700 "$S" "$R"

run() {
  echo "### $1"
  $KA run --run "$RUN" --action "$1" 2>&1 | tail -6
  echo "   rc=$?"
}

{
  echo "kernel=$(uname -r)"
  echo "boot_id=$(cat /proc/sys/kernel/random/boot_id)"
  echo "uptime=$(cut -d" " -f1 /proc/uptime)"
} > "$S/pre-state.txt"; cat "$S/pre-state.txt"

echo "== DT channel properties (numeric) =="
base=/proc/device-tree/soc@0/geniqup@8c0000/i2c@884000
for a in 34 35; do
  printf "  speaker-amp@%s channel=" "$a"
  od -An -tu4 --endian=big "$base/speaker-amp@$a/nxp,tfa-channel" 2>/dev/null | tr -d "\n"
  printf "  prefix="
  tr -d "\000" < "$base/speaker-amp@$a/sound-name-prefix"; echo
done

echo "== freeze manifest (map seeded from the previous live read) =="
python3 /tmp/kebab-audio-selftest/make-manifests5.py "$RUN" "$S" 0 hw:0,1 hw:0,0 \
  "/srv/kebab-audio-$PREV/control-map-live.json" || exit 1
echo "manifest sha=$(sha256sum "$S/trial-manifest.json" | cut -d" " -f1)"

echo "== arm + beat =="
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

echo "== load =="
run load-tfa2
sleep 1
cat /proc/asound/cards
python3 /tmp/kebab-audio-selftest/check-controls.py "$S" 0 || { echo ABORT; kill -TERM $GPID; exit 2; }

echo "== enable dynamic debug for this driver only =="
if [ -w /sys/kernel/debug/dynamic_debug/control ]; then
  echo "file tfa2.c +p" > /sys/kernel/debug/dynamic_debug/control
  grep -c "^sound/soc/codecs/tfa2.c" /sys/kernel/debug/dynamic_debug/control || true
else
  echo "dynamic_debug not writable"
fi
dmesg -C 2>/dev/null || true
dmesg | grep -iE "tfa9874|tfa2" | tail -10

echo "== ASoC topology before streaming =="
ls /sys/kernel/debug/asoc/ 2>/dev/null
echo "-- dai links --"
find /sys/kernel/debug/asoc/ -maxdepth 3 -name codec -printf "%h -> %l\n" 2>/dev/null | head -20
echo "-- components --"
cat /sys/kernel/debug/asoc/components 2>/dev/null | head -20

echo "== one 250 ms left probe (the only hardware action) =="
run probe-left-42

echo "== what the driver logged =="
dmesg | grep -iE "tfa9874|tfa2|hw_params|MTPEX|TDM|OPERATING" | tail -30

echo "== ASoC widget state for the amps =="
grep -rE "AIFIN|SBSL|POWER" /sys/kernel/debug/asoc/*/dapm/ 2>/dev/null | head -20

echo "== tfa2 debugfs, if any =="
ls -d /sys/kernel/debug/*tfa* /sys/kernel/debug/*TFA* 2>/dev/null || echo none

echo "== stop =="
kill -TERM $GPID 2>/dev/null; wait $GPID 2>/dev/null; echo "guardian rc=$?"
kill $BPID 2>/dev/null
tail -1 "$S/guard-events.jsonl" | cut -c1-300
cat /proc/asound/cards
dmesg | tail -5
