#!/bin/bash
# A2: silent load of the verified r4 TFA2 module under the armed guard, then
# prove the binding. No PCM is opened and no sound is requested here.
set -u
RUN="$1"
S=/srv/kebab-audio-$RUN
run() { echo "### $1"; kebab-audio run --run "$RUN" --action "$1" 2>&1 | tail -25; echo "rc=$?"; }

echo "== pre =="
cat /proc/asound/cards; lsmod | grep -c tfa2 || true
run load-tfa2
echo "== post-load module state =="
lsmod | grep tfa2 || echo "tfa2 NOT loaded"
echo "== dmesg tail =="
sudo -n dmesg | tail -25
run binding
run idle-safety
run transport
run server-regression
echo "== cards =="
cat /proc/asound/cards
echo "== evidence =="
ls -l "$S/evidence/" 2>/dev/null
echo "== binding evidence =="
python3 -c "import json;d=json.load(open('$S/evidence/action-binding.json'));print(json.dumps({k:d.get(k) for k in ('verdict','matched_card','machine_bound','amp','dais_present','problems')},indent=1))" 2>&1
