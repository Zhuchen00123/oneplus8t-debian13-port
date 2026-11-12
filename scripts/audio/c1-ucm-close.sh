#!/bin/bash
# Run 3: close UCM, and replicate the right-channel result in a run that also has
# a quiet baseline, so the acoustic verdict can be recomputed honestly.
set -u
set -o pipefail
RUN="$1"
PREV="$2"
S=/srv/kebab-audio-$RUN
R=/run/kebab-audio-$RUN
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"
AG=/usr/local/lib/kebab-audio/kebab-audio-guard
mkdir -p "$S" "$R" "$S/ucm2/O8T"; chmod 700 "$S" "$R"

run() {
  echo "### $1"
  $KA run --run "$RUN" --action "$1" 2>&1 | tail -8
  echo "   rc=$?"
}

{
  echo "kernel=$(uname -r)"
  echo "boot_id=$(cat /proc/sys/kernel/random/boot_id)"
  echo "uptime=$(cut -d" " -f1 /proc/uptime)"
  echo "cards=$(cat /proc/asound/cards | tr "\n" " ")"
  echo "blacklist_lines=$(grep -c blacklist /etc/modprobe.d/90-kebab-server.conf)"
} > "$S/pre-state.txt"
cat "$S/pre-state.txt"

echo "== private UCM tree =="
cp -r /usr/share/alsa/ucm2 "$S/ucm2-full"
mv "$S/ucm2-full" "$S/ucm2"
mkdir -p "$S/ucm2/O8T"
cp /tmp/kebab-audio-selftest/ucm/O8T/O8T.conf /tmp/kebab-audio-selftest/ucm/O8T/HiFi.conf "$S/ucm2/O8T/"
chmod 600 "$S/ucm2/O8T"/*.conf
grep -E 'PCM|Device' "$S/ucm2/O8T/HiFi.conf"

echo "== freeze manifest (map seeded from the live read of the previous run) =="
python3 /tmp/kebab-audio-selftest/make-manifests3.py "$RUN" "$S" 0 hw:0,1 hw:0,0 "$S/ucm2" "$S/../kebab-audio-$PREV/control-map-live.json" || exit 1
echo "manifest sha=$(sha256sum "$S/trial-manifest.json" | cut -d" " -f1)"

echo "== arm the guardian =="
python3 /tmp/kebab-audio-selftest/make-guard-plan.py "$S" "$RUN" "$S/guard-plan.txt" 0 > /dev/null
grep -E '^(stop|unbind|rmmod)' "$S/guard-plan.txt"
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
run binding

echo "== quiet baseline, then the right-channel replication =="
run noise-cap
run probe-right-48

echo "== UCM =="
echo "  before:"
for n in 'TERT_MI2S_RX Audio Mixer MultiMedia1' 'MultiMedia2 Mixer VA_CODEC_DMA_TX_0' 'VA_AIF1_CAP Mixer DEC0' 'VA DMIC MUX0'; do
  printf '    %-42s ' "$n"; amixer -c 0 cget name="$n" 2>&1 | tail -1
done
run ucm-verb
run ucm-spk-on
echo "  after _enadev Speaker:"
amixer -c 0 cget name='TERT_MI2S_RX Audio Mixer MultiMedia1' 2>&1 | tail -1
run ucm-spk-off
echo "  after _disdev Speaker:"
amixer -c 0 cget name='TERT_MI2S_RX Audio Mixer MultiMedia1' 2>&1 | tail -1
run ucm-mic-on
echo "  after _enadev Mic:"
for n in 'MultiMedia2 Mixer VA_CODEC_DMA_TX_0' 'VA_AIF1_CAP Mixer DEC0' 'VA DMIC MUX0'; do
  printf '    %-42s ' "$n"; amixer -c 0 cget name="$n" 2>&1 | tail -1
done
run ucm-mic-off
echo "  after _disdev Mic:"
for n in 'MultiMedia2 Mixer VA_CODEC_DMA_TX_0' 'VA_AIF1_CAP Mixer DEC0' 'VA DMIC MUX0'; do
  printf '    %-42s ' "$n"; amixer -c 0 cget name="$n" 2>&1 | tail -1
done

echo "== teardown + verdicts =="
run route-off
run acoustic
run idle-safety
run transport
run server-regression
run pipewire

echo "== guardian stop (unbind, rmmod) =="
kill -TERM $GPID 2>/dev/null
wait $GPID 2>/dev/null
echo "guardian rc=$?"
kill $BPID 2>/dev/null
tail -1 "$S/guard-events.jsonl" | cut -c1-520

echo "== rollback (verify-only) =="
$KA rollback --run "$RUN" --out "$S/rollback-report.json" --verify-only 2>&1 | tail -8

echo "== result =="
$KA verify --run "$RUN" --out "$S/result.json" > /dev/null 2>&1
python3 -c "
import json
r=json.load(open('$S/result.json'))
print('overall:', r['capabilities']['overall'])
for k,v in sorted(r['capabilities'].items()):
    if k=='overall': continue
    print('  %-18s %-11s %s' % (k, v['verdict'], json.dumps(v['detail'],default=str)[:220]))
print('budget:', json.dumps(r['budget_charged']))
"

echo "== final state =="
lsmod | grep tfa2 || echo "tfa2 unloaded"
cat /proc/asound/cards
for d in 15-0034 15-0035; do printf '%s ' "$d"; readlink /sys/bus/i2c/devices/$d/driver 2>/dev/null || echo UNBOUND; done
