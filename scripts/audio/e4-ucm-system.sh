#!/bin/bash
# Final UCM closure: install the profile into the system tree, then verify that
# alsaucm works with no ALSA_CONFIG_UCM2 override at all.
set -uo pipefail
RUN="$1"
PREV="$2"
S=/srv/kebab-audio-$RUN
R=/run/kebab-audio-$RUN
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"
AG=/usr/local/lib/kebab-audio/kebab-audio-guard
UCMD=/usr/share/alsa/ucm2/O8T
rm -rf "$S"; mkdir -p "$S" "$R"; chmod 700 "$S" "$R"

{
  echo "kernel=$(uname -r)"; echo "boot_id=$(cat /proc/sys/kernel/random/boot_id)"
  echo "uptime=$(cut -d" " -f1 /proc/uptime)"
} > "$S/pre-state.txt"; cat "$S/pre-state.txt"

echo "== install the profile into the system UCM tree =="
echo "before: $(ls /usr/share/alsa/ucm2 | grep -c O8T || true) O8T entries"
install -d -m 0755 "$UCMD"
install -m 0644 /tmp/kebab-audio-selftest/ucm/O8T/O8T.conf "$UCMD/O8T.conf"
install -m 0644 /tmp/kebab-audio-selftest/ucm/O8T/HiFi.conf "$UCMD/HiFi.conf"
sha256sum "$UCMD"/* | tee "$S/system-ucm-sha.txt"

echo "== alsaucm with no ALSA_CONFIG_UCM2 in the environment =="
env -u ALSA_CONFIG_UCM2 alsaucm -c O8T list _verbs 2>&1 | head -6

# the generator still takes the tree path as an argument; manifests8 no longer
# puts it into the manifest, because the profile now lives in the system tree.
python3 /tmp/kebab-audio-selftest/make-manifests8.py "$RUN" "$S" 0 hw:0,1 hw:0,0 \
  /usr/share/alsa/ucm2 "/srv/kebab-audio-$PREV/control-map-live.json" || exit 1
echo "manifest sha=$(sha256sum "$S/trial-manifest.json" | cut -d" " -f1)"

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

# route-off is deliberately not run: the four UCM transitions plus the pair
# verification already charge the whole route_variant budget, and the guardian's
# own stop actions mute the same four controls when the lease ends.
for a in load-tfa2 binding ucm-spk-on ucm-spk-off ucm-mic-on ucm-mic-off ucm-pair \
         idle-safety transport server-regression pipewire; do
  echo "### $a"
  $KA run --run "$RUN" --action "$a" 2>&1 | tail -4
  echo "   rc=$?"
done

echo "== controls after everything =="
for c in "TERT_MI2S_RX Audio Mixer MultiMedia1" "MultiMedia2 Mixer VA_CODEC_DMA_TX_0" \
         "VA_AIF1_CAP Mixer DEC0" "VA DMIC MUX0"; do
  printf "  %-42s " "$c"
  amixer -c 0 sget "$c" 2>/dev/null | tail -1 | sed "s/^ *//"
done

echo "== capabilities =="
python3 -c "
import json
r=json.load(open('$S/result.json')); c=r['capabilities']
for k,v in c.items():
    if isinstance(v,dict) and 'verdict' in v:
        print('  %-14s %-12s %s' % (k, v['verdict'], str(v.get('reason'))[:70]))
" 2>&1

echo "== ucm-pair evidence =="
cat "$S/evidence/action-ucm-pair.json" 2>/dev/null | head -40

echo "== stop =="
kill -TERM $GPID 2>/dev/null; wait $GPID 2>/dev/null; echo "guardian rc=$?"
kill $BPID 2>/dev/null
cat /proc/asound/cards
