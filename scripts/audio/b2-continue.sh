#!/bin/bash
# Run 2b: continue in the same run (same journal, same budget accounting) after
# fixing three tooling bugs. The codec is already loaded, so nothing is reloaded.
set -u
set -o pipefail
RUN="$1"
S=/srv/kebab-audio-$RUN
R=/run/kebab-audio-$RUN
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"
AG=/usr/local/lib/kebab-audio/kebab-audio-guard
mkdir -p "$R"; chmod 700 "$R"

run() {
  echo "### $1"
  $KA run --run "$RUN" --action "$1" 2>&1 | tail -5
  echo "   rc=$?"
}

echo "== private UCM tree: copy the real one, add our card =="
REAL=/usr/share/alsa/ucm2
if [ ! -f "$S/ucm2/ucm.conf" ]; then
  rm -rf "$S/ucm2"
  cp -r "$REAL" "$S/ucm2"
  chmod -R u+rwX "$S/ucm2"
fi
mkdir -p "$S/ucm2/O8T"
cp /tmp/kebab-audio-selftest/ucm/O8T/O8T.conf /tmp/kebab-audio-selftest/ucm/O8T/HiFi.conf "$S/ucm2/O8T/"
chmod 600 "$S/ucm2/O8T"/*.conf
ls "$S/ucm2" | head -5
du -sh "$S/ucm2"
ALSA_CONFIG_UCM2="$S/ucm2" alsaucm -c O8T list _verbs 2>&1 | head -8

echo "== re-arm the guardian (card is already up) =="
python3 /tmp/kebab-audio-selftest/make-guard-plan.py "$S" "$RUN" "$S/guard-plan.txt" 0 > /dev/null
grep -E '^(stop|unbind|rmmod)' "$S/guard-plan.txt"
"$AG" arm --plan "$S/guard-plan.txt" --run-dir "$R" --srv-dir "$S" > "$S/guard-arm2.log" 2>&1 &
GPID=$!
for i in $(seq 1 40); do
  st=$("$AG" status --run-dir "$R" 2>/dev/null | grep -o '"state": "[a-z]*"' | head -1)
  [ "$st" = '"state": "armed"' ] && break
  sleep 0.1
done
cat "$S/guard-arm2.log"
"$AG" beat --run-dir "$R" --interval-ms 200 --parent-pid $$ > /dev/null 2>&1 &
BPID=$!
trap 'kill $BPID 2>/dev/null' EXIT
sleep 0.5
"$AG" status --run-dir "$R" | grep state

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
  echo "== left channel: -36 dBFS (attempt 2 of 2) =="
  run probe-left-36
  python3 -c "
import json
d=json.load(open('$S/probe-evidence.json'))
print('left corr after -36:', d['probes'].get('left',{}).get('corr'))
"
else
  echo "left detected at -42 dBFS"
fi

echo "== UCM =="
run ucm-verb
run ucm-spk-on
echo "  TERT after _enadev Speaker:"; amixer -c 0 cget name='TERT_MI2S_RX Audio Mixer MultiMedia1' 2>&1 | tail -1
run ucm-spk-off
echo "  TERT after _disdev Speaker:"; amixer -c 0 cget name='TERT_MI2S_RX Audio Mixer MultiMedia1' 2>&1 | tail -1
run ucm-mic-on
echo "  mic after _enadev Mic:"
for n in 'MultiMedia2 Mixer VA_CODEC_DMA_TX_0' 'VA_AIF1_CAP Mixer DEC0' 'VA DMIC MUX0'; do
  printf '    %-42s ' "$n"; amixer -c 0 cget name="$n" 2>&1 | tail -1
done
run ucm-mic-off
echo "  mic after _disdev Mic:"
for n in 'MultiMedia2 Mixer VA_CODEC_DMA_TX_0' 'VA_AIF1_CAP Mixer DEC0' 'VA DMIC MUX0'; do
  printf '    %-42s ' "$n"; amixer -c 0 cget name="$n" 2>&1 | tail -1
done

echo "== teardown + verdicts =="
run route-off
run transport
run acoustic
run idle-safety
run server-regression
run pipewire

echo "== stop the guardian: unbind both amps, then rmmod =="
kill -TERM $GPID 2>/dev/null
wait $GPID 2>/dev/null
echo "guardian rc=$?"
kill $BPID 2>/dev/null
tail -1 "$S/guard-events.jsonl" | cut -c1-500
"$AG" status --run-dir "$R" | grep state

echo "== final state =="
lsmod | grep tfa2 || echo "tfa2 unloaded"
cat /proc/asound/cards
for d in 15-0034 15-0035; do printf '%s ' "$d"; readlink /sys/bus/i2c/devices/$d/driver 2>/dev/null || echo UNBOUND; done

echo "== rollback (verify-only: the guardian already did the unload) =="
$KA rollback --run "$RUN" --out "$S/rollback-report.json" --verify-only 2>&1 | tail -10

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
