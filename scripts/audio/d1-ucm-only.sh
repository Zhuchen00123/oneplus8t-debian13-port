#!/bin/bash
# Run 4: UCM only. No playback, no capture -- just prove the staged UCM2 profile
# drives exactly the audited controls and can be disabled again.
set -u
set -o pipefail
RUN="$1"
PREV="$2"
S=/srv/kebab-audio-$RUN
R=/run/kebab-audio-$RUN
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"
AG=/usr/local/lib/kebab-audio/kebab-audio-guard
rm -rf "$S"; mkdir -p "$S/ucm2" "$R"; chmod 700 "$S" "$R"

run() {
  echo "### $1"
  $KA run --run "$RUN" --action "$1" 2>&1 | tail -7
  echo "   rc=$?"
}

show() {
  printf '  %-42s ' "$1"
  amixer -c 0 cget name="$1" 2>&1 | tail -1
}

{
  echo "kernel=$(uname -r)"
  echo "boot_id=$(cat /proc/sys/kernel/random/boot_id)"
  echo "uptime=$(cut -d" " -f1 /proc/uptime)"
  echo "cards=$(cat /proc/asound/cards | tr "\n" " ")"
  echo "blacklist_lines=$(grep -c blacklist /etc/modprobe.d/90-kebab-server.conf)"
} > "$S/pre-state.txt"
cat "$S/pre-state.txt"

echo "== private UCM tree (copy the contents, not the directory) =="
cp -r /usr/share/alsa/ucm2/. "$S/ucm2/"
mkdir -p "$S/ucm2/O8T"
cp /tmp/kebab-audio-selftest/ucm/O8T/O8T.conf /tmp/kebab-audio-selftest/ucm/O8T/HiFi.conf "$S/ucm2/O8T/"
chmod 600 "$S/ucm2/O8T"/*.conf
test -f "$S/ucm2/ucm.conf" && echo "ucm.conf present"
du -sh "$S/ucm2"

echo "== freeze manifest =="
python3 /tmp/kebab-audio-selftest/make-manifests4.py "$RUN" "$S" 0 hw:0,1 hw:0,0 "$S/ucm2" "$S/../kebab-audio-$PREV/control-map-live.json" || exit 1
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

echo "== the staged UCM must exist and parse =="
ALSA_CONFIG_UCM2="$S/ucm2" alsaucm -c O8T list _verbs 2>&1 | head -10
ALSA_CONFIG_UCM2="$S/ucm2" alsaucm -c O8T list _devices 2>&1 | head -12

echo "== baseline (everything muted by the driver defaults) =="
show 'TERT_MI2S_RX Audio Mixer MultiMedia1'
show 'MultiMedia2 Mixer VA_CODEC_DMA_TX_0'
show 'VA_AIF1_CAP Mixer DEC0'
show 'VA DMIC MUX0'

echo "== _enadev Speaker =="
run ucm-spk-on
show 'TERT_MI2S_RX Audio Mixer MultiMedia1'
echo "== _disdev Speaker =="
run ucm-spk-off
show 'TERT_MI2S_RX Audio Mixer MultiMedia1'

echo "== _enadev Mic =="
run ucm-mic-on
show 'MultiMedia2 Mixer VA_CODEC_DMA_TX_0'
show 'VA_AIF1_CAP Mixer DEC0'
show 'VA DMIC MUX0'
echo "== _disdev Mic =="
run ucm-mic-off
show 'MultiMedia2 Mixer VA_CODEC_DMA_TX_0'
show 'VA_AIF1_CAP Mixer DEC0'
show 'VA DMIC MUX0'

echo "== teardown + verdicts =="
run route-off
run idle-safety
run transport
run server-regression
run pipewire

echo "== guardian stop =="
kill -TERM $GPID 2>/dev/null
wait $GPID 2>/dev/null
echo "guardian rc=$?"
kill $BPID 2>/dev/null
tail -1 "$S/guard-events.jsonl" | cut -c1-520

echo "== rollback (verify-only) =="
$KA rollback --run "$RUN" --out "$S/rollback-report.json" --verify-only 2>&1 | tail -6

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
