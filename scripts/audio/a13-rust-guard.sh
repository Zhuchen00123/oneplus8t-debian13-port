#!/bin/bash
# Verify the Rust guardian on the device: selftest, fixture end-to-end, and the
# real (card-absent) arm/stop path. No codec load, no PCM, no audio.
set -u
RUN="$1"
S=/srv/kebab-audio-guardcheck-$RUN
BIN=/usr/local/lib/kebab-audio/kebab-audio-guard
R=/run/kebab-audio-guardcheck-$RUN
R2=/run/kebab-audio-guardcheck2-$RUN
mkdir -p "$S" "$R" "$R2"
chmod 700 "$S" "$R" "$R2"

echo "== binary =="
"$BIN" version
sha256sum "$BIN"

echo "== in-process negative tests =="
"$BIN" selftest --json > "$S/guard-selftest-rust.json" 2>&1
echo "selftest rc=$?"
python3 -c "
import json
d=json.load(open('$S/guard-selftest-rust.json'))
print('verdict',d['verdict'],'passed',d['passed'],'of',d['total'])
for c in d['cases']:
    if not c['ok']: print('  FAIL',c['case'],c['note'])
"

echo "== fixture end-to-end: owner dies, guardian must mute =="
F=/tmp/kag-fake
rm -rf "$F"; mkdir -p "$F/ctl"; echo 1 > "$F/card_present"
echo 1 > "$F/ctl/TERT_MI2S_RX_Audio_Mixer_MultiMedia1"
echo 2 > "$F/ctl/VA_DMIC_MUX0"
BOOT=$(cat /proc/sys/kernel/random/boot_id)
printf 'run_id fixture\nboot_id %s\nmanifest_sha 0000000000000000000000000000000000000000000000000000000000000000\ncard 0\nheartbeat_ms 100\nexpiry_ms 600\nmax_seconds 10\nfake_root %s\nstop TERT_MI2S_RX Audio Mixer MultiMedia1|BOOL|1|0\nstop VA DMIC MUX0|ENUM|1|0\n' "$BOOT" "$F" > "$S/plan-fixture.txt"
"$BIN" arm --plan "$S/plan-fixture.txt" --run-dir "$R" --srv-dir "$S" --apply-stop-at-arm > "$S/fixture-arm.log" 2>&1 &
GPID=$!
sleep 0.4
echo "  after apply-stop: TERT=$(cat "$F/ctl/TERT_MI2S_RX_Audio_Mixer_MultiMedia1") MUX0=$(cat "$F/ctl/VA_DMIC_MUX0")"
for i in 1 2 3 4 5 6; do touch "$R/guard.lease"; sleep 0.2; done
echo "  owner stopped beating at uptime $(cut -d' ' -f1 /proc/uptime); waiting for expiry"
sleep 1.4
if kill -0 $GPID 2>/dev/null; then echo "  guardian STILL RUNNING (bad)"; kill -TERM $GPID; else echo "  guardian exited"; fi
wait $GPID 2>/dev/null; echo "  arm rc=$?"
echo "  last event:"; tail -2 "$S/guard-events.jsonl" 2>/dev/null | cut -c1-240

echo "== fixture end-to-end: SIGTERM must stop =="
echo 1 > "$F/ctl/TERT_MI2S_RX_Audio_Mixer_MultiMedia1"
"$BIN" arm --plan "$S/plan-fixture.txt" --run-dir "$R2" --srv-dir "$S" --apply-stop-at-arm > /dev/null 2>&1 &
GPID=$!
sleep 0.5
kill -TERM $GPID
wait $GPID 2>/dev/null; echo "  sigterm rc=$?"
echo "  after sigterm: TERT=$(cat "$F/ctl/TERT_MI2S_RX_Audio_Mixer_MultiMedia1")"
echo "  last event:"; tail -1 "$S/guard-events.jsonl" | cut -c1-200

echo "== real path: card absent (codec not loaded), arm + stop =="
python3 /tmp/kebab-audio-selftest/make-guard-plan.py /srv/kebab-audio-$RUN "$RUN" "$S/plan-real.txt" 0 | head -3
echo "  fixture-arm.log:"; cat "$S/fixture-arm.log" 2>/dev/null
"$BIN" arm --plan "$S/plan-real.txt" --run-dir "$S" --srv-dir "$S" > "$S/real-arm.log" 2>&1 &
GPID=$!
sleep 0.6
echo "  guard.json while armed:"; cat "$S/guard.json" 2>/dev/null
kill -TERM $GPID
wait $GPID 2>/dev/null; echo "  stop rc=$?"
echo "  guard.json after stop:"; cat "$S/guard.json" 2>/dev/null
echo "  last event:"; tail -1 "$S/guard-events.jsonl" | cut -c1-280
echo "== inventory =="
ls -l "$S"
