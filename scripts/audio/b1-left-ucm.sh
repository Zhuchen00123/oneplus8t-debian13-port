#!/bin/bash
# Run 2: close out the left channel and stage UCM. Same frozen thresholds as
# run 1; a fresh experiment budget because the caps are per experiment.
set -u
set -o pipefail
RUN="$1"
PREV="$2"
S=/srv/kebab-audio-$RUN
R=/run/kebab-audio-$RUN
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"
AG=/usr/local/lib/kebab-audio/kebab-audio-guard
mkdir -p "$S" "$R" "$S/ucm2/O8T"
chmod 700 "$S" "$R"

run() {
  echo "### $1"
  $KA run --run "$RUN" --action "$1" 2>&1 | tail -6
  echo "   rc=$?"
}

echo "== pre-state =="
cat /proc/asound/cards
lsmod | grep tfa2 || echo "tfa2 not loaded"
{
  echo "kernel=$(uname -r)"
  echo "boot_id=$(cat /proc/sys/kernel/random/boot_id)"
  echo "uptime=$(cut -d" " -f1 /proc/uptime)"
  echo "cards=$(cat /proc/asound/cards | tr "\n" " ")"
  echo "blacklist_lines=$(grep -c blacklist /etc/modprobe.d/90-kebab-server.conf)"
} > "$S/pre-state.txt"
cat "$S/pre-state.txt"
$KA inventory --out "$S/baseline.json" > /dev/null 2>&1
echo "baseline rc=$?"

echo "== stage UCM privately (ALSA_CONFIG_UCM2, no system file touched) =="
cp /tmp/kebab-audio-selftest/ucm/O8T/O8T.conf /tmp/kebab-audio-selftest/ucm/O8T/HiFi.conf "$S/ucm2/O8T/"
chmod 600 "$S/ucm2/O8T"/*.conf
ls -l "$S/ucm2/O8T"

echo "== freeze manifest (control map seeded from the audited previous run) =="
cp "/srv/kebab-audio-$PREV/control-map.json" "$S/control-map-seed.json"
python3 /tmp/kebab-audio-selftest/make-manifests2.py "$RUN" "$S" 0 hw:0,1 hw:0,0 "$S/ucm2" "$S/control-map-seed.json" || exit 1
echo "manifest sha=$(sha256sum "$S/trial-manifest.json" | cut -d" " -f1)"

echo "== arm the Rust guardian before the codec exists =="
python3 /tmp/kebab-audio-selftest/make-guard-plan.py "$S" "$RUN" "$S/guard-plan.txt" 0 > /dev/null
cat "$S/guard-plan.txt"
"$AG" arm --plan "$S/guard-plan.txt" --run-dir "$R" --srv-dir "$S" > "$S/guard-arm.log" 2>&1 &
GPID=$!
for i in $(seq 1 40); do
  st=$("$AG" status --run-dir "$R" 2>/dev/null | grep -o '"state": "[a-z]*"' | head -1)
  [ "$st" = '"state": "armed"' ] && break
  sleep 0.1
done
cat "$S/guard-arm.log"
"$AG" status --run-dir "$R" | head -10

"$AG" beat --run-dir "$R" --interval-ms 200 --parent-pid $$ > /dev/null 2>&1 &
BPID=$!
trap 'kill $BPID 2>/dev/null' EXIT

echo "== load the codec under the guardian =="
run load-tfa2
sleep 1
cat /proc/asound/cards

echo "== the seeded map must match the live card before we write anything =="
python3 /tmp/kebab-audio-selftest/check-controls.py "$S" 0
if [ $? -ne 0 ]; then echo "ABORT: live card disagrees with the frozen map"; kill -TERM $GPID; exit 2; fi

run binding

echo "== left channel: -42 dBFS (attempt 1 of 2) =="
run probe-left-42
CORR=$(python3 -c "
import json
d=json.load(open('$S/probe-evidence.json'))
print(d['probes'].get('left',{}).get('corr'))
" 2>/dev/null)
echo "left corr after -42: $CORR"
NEED2=1
if [ -n "$CORR" ] && [ "$CORR" != "None" ]; then
  NEED2=$(python3 -c "print(1 if abs(float('$CORR')) < 0.6 else 0)")
fi
if [ "$NEED2" = "1" ]; then
  echo "== left channel: -36 dBFS (attempt 2 of 2, the last allowed) =="
  run probe-left-36
  python3 -c "
import json
d=json.load(open('$S/probe-evidence.json'))
print('left corr after -36:', d['probes'].get('left',{}).get('corr'))
"
else
  echo "left detected at -42 dBFS; the -36 step stays unused"
fi

echo "== UCM through alsaucm, private tree =="
export ALSA_CONFIG_UCM2="$S/ucm2"
alsaucm -c O8T list _verbs 2>&1 | head -6
run ucm-verb
run ucm-spk-on
echo "  speaker route after _enadev Speaker:"
amixer -c 0 cget name='TERT_MI2S_RX Audio Mixer MultiMedia1' 2>&1 | tail -1
run ucm-spk-off
echo "  speaker route after _disdev Speaker:"
amixer -c 0 cget name='TERT_MI2S_RX Audio Mixer MultiMedia1' 2>&1 | tail -1
run ucm-mic-on
echo "  mic route after _enadev Mic:"
for n in 'MultiMedia2 Mixer VA_CODEC_DMA_TX_0' 'VA_AIF1_CAP Mixer DEC0' 'VA DMIC MUX0'; do
  printf '    %-42s ' "$n"
  amixer -c 0 cget name="$n" 2>&1 | tail -1
done
run ucm-mic-off
echo "  mic route after _disdev Mic:"
for n in 'MultiMedia2 Mixer VA_CODEC_DMA_TX_0' 'VA_AIF1_CAP Mixer DEC0' 'VA DMIC MUX0'; do
  printf '    %-42s ' "$n"
  amixer -c 0 cget name="$n" 2>&1 | tail -1
done

echo "== tear down routes, then the remaining verdicts =="
run route-off
run transport
run acoustic
run idle-safety
run server-regression
run pipewire

echo "== stop the guardian: this is the real-card mute path =="
kill -TERM $GPID 2>/dev/null
wait $GPID 2>/dev/null
echo "guardian rc=$?"
kill $BPID 2>/dev/null
tail -1 "$S/guard-events.jsonl" | cut -c1-420

echo "== rollback =="
$KA rollback --run "$RUN" --out "$S/rollback-report.json" 2>&1 | tail -14

echo "== result =="
$KA verify --run "$RUN" --out "$S/result.json" > /dev/null 2>&1
python3 -c "
import json
r=json.load(open('$S/result.json'))
print('overall:', r['capabilities']['overall'])
for k,v in sorted(r['capabilities'].items()):
    if k=='overall': continue
    print('  %-18s %-11s %s' % (k, v['verdict'], json.dumps(v['detail'],default=str)[:200]))
print('budget:', json.dumps(r['budget_charged']))
"

echo "== final state =="
lsmod | grep tfa2 || echo "tfa2 unloaded"
cat /proc/asound/cards
for d in 15-0034 15-0035; do printf '%s ' "$d"; readlink /sys/bus/i2c/devices/$d/driver 2>/dev/null || echo UNBOUND; done
grep -c blacklist /etc/modprobe.d/90-kebab-server.conf
ls -l "$S"
