#!/bin/bash
# One bounded escalation probe on the left channel, then teardown + verdicts.
set -u
RUN="$1"
S=/srv/kebab-audio-$RUN
R=/run/kebab-audio-$RUN
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"

python3 /tmp/kebab-audio-selftest/a6-addleft2.py "$RUN" || exit 1
SHA=$(sha256sum "$S/trial-manifest.json" | cut -d' ' -f1)
echo "new manifest sha=$SHA"

echo "== stop guard 2 (applies the full mute set) =="
systemctl stop "kebab-audio-guard-$RUN" 2>/dev/null
sleep 1.2
tail -1 "$S/guard-events.jsonl"

echo "== arm guard 3 =="
systemctl reset-failed "kebab-audio-guard-$RUN" 2>/dev/null || true
rm -f "$R/guard.json"
systemd-run --unit="kebab-audio-guard-$RUN" --collect --quiet \
  --description="kebab audio silence guard phase 3 ($RUN)" \
  /usr/bin/python3 /usr/local/lib/kebab-audio/kebab_audio.py guard \
  --run "$RUN" --manifest-sha "$SHA" --max-seconds 300
for i in $(seq 1 60); do
  st=$(python3 -c "import json;d=json.load(open('$R/guard.json'));print(d.get('state'),d.get('manifest_sha')=='''$SHA''')" 2>/dev/null)
  [ "$st" = "armed True" ] && break
  sleep 0.25
done
echo "guard3: $st"

echo "== escalate left probe to -42 dBFS =="
$KA run --run "$RUN" --action probe-left-2 2>&1 | tail -6
echo "== re-analyse with the corrected reference channel =="
python3 /tmp/kebab-audio-selftest/a5-reanalyse.py "$RUN"
echo "== route off + verdicts =="
$KA run --run "$RUN" --action route-off 2>&1 | tail -3
$KA run --run "$RUN" --action acoustic 2>&1 | tail -3
$KA run --run "$RUN" --action transport 2>&1 | tail -3
$KA run --run "$RUN" --action idle-safety 2>&1 | tail -6
$KA run --run "$RUN" --action server-regression 2>&1 | tail -3
$KA run --run "$RUN" --action pipewire 2>&1 | tail -3
