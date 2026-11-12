#!/bin/bash
# Noise floor + both channels at one drive level, analysed on the device.
set -u
set -o pipefail
RUN="$1"
PREV="$2"
S=/srv/kebab-audio-$RUN
R=/run/kebab-audio-$RUN
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"
AG=/usr/local/lib/kebab-audio/kebab-audio-guard
rm -rf "$S"; mkdir -p "$S" "$R"; chmod 700 "$S" "$R"

run() { echo "### $1"; $KA run --run "$RUN" --action "$1" 2>&1 | tail -4; echo "   rc=$?"; }

{
  echo "kernel=$(uname -r)"; echo "boot_id=$(cat /proc/sys/kernel/random/boot_id)"
  echo "uptime=$(cut -d" " -f1 /proc/uptime)"
} > "$S/pre-state.txt"; cat "$S/pre-state.txt"

python3 /tmp/kebab-audio-selftest/make-manifests7.py "$RUN" "$S" 0 hw:0,1 hw:0,0 \
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

echo "== load =="
run load-tfa2
sleep 1
dmesg | grep -iE "tfa9874|tfa2" > "$S/dmesg-probe.txt" 2>&1
cat "$S/dmesg-probe.txt"
cat /proc/asound/cards
if [ -w /sys/kernel/debug/dynamic_debug/control ]; then
  echo "file tfa2.c +p" > /sys/kernel/debug/dynamic_debug/control
fi

echo "== noise floor =="
run noise-cap
python3 /tmp/kebab-audio-selftest/probe-spectrum.py /run/kebab-audio-$RUN/noise.wav noise

for a in probe-left-1 probe-left-2 probe-right-1; do
  echo "== $a =="
  run "$a"
  case "$a" in
    probe-left-1) w=left1;; probe-left-2) w=left2;; *) w=right1;;
  esac
  python3 /tmp/kebab-audio-selftest/probe-spectrum.py /run/kebab-audio-$RUN/cap-$w.wav "$w"
done

echo "== executor evidence =="
python3 -c "
import json
d=json.load(open('$S/probe-evidence.json'))
for k,v in sorted(d['probes'].items()):
    b=v.get('burst_stats') or {}
    print('  %-6s corr=%-9s peak=%-8s rms=%-8s clipped=%s' % (
        k, v.get('corr'), v.get('peak_db'), b.get('rms_db'), b.get('clipped')))
"

echo "== stop =="
kill -TERM $GPID 2>/dev/null; wait $GPID 2>/dev/null; echo "guardian rc=$?"
kill $BPID 2>/dev/null
cat /proc/asound/cards
