#!/bin/bash
# Phase 2: hand over the guard, then run the reviewed route + microphone +
# bounded acoustic probe sequence. Every action goes through the executor.
set -u
RUN="$1"
S=/srv/kebab-audio-$RUN
R=/run/kebab-audio-$RUN
CARD=${2:-0}
CAP=${3:-hw:0,1}
PLAY=${4:-hw:0,0}
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"

echo "== state before handover =="
cat /proc/asound/cards
lsmod | grep tfa2 || echo "tfa2 not loaded"
python3 -c "import json;d=json.load(open('$R/guard.json'));print('guard',d['state'],'max_seconds',d['max_seconds'])"

echo "== build control-map + manifest from the LIVE card =="
python3 /tmp/kebab-audio-selftest/make-manifests.py "$RUN" "$S" "$CARD" "$CAP" "$PLAY" || exit 1
SHA=$(sha256sum "$S/trial-manifest.json" | cut -d' ' -f1)
echo "manifest_sha=$SHA"

echo "== retire phase-1 guard (SIGKILL: nothing was ever routed or played) =="
P1=$(python3 -c "import json;print(json.load(open('$R/guard.json'))['pid'])")
kill -9 "$P1" 2>/dev/null || true
systemctl stop "kebab-audio-guard-$RUN" 2>/dev/null || true
sleep 0.6
python3 - "$S" "$P1" <<'PY'
import json, sys, time
s, pid = sys.argv[1], int(sys.argv[2])
with open(s + "/guard-events.jsonl", "a") as fh:
    fh.write(json.dumps({"event": "handover", "retired_pid": pid,
                         "reason": "phase-1 guard retired by operator; no PCM was "
                                   "ever opened and no route was ever enabled",
                         "wall_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) + "\n")
PY
echo "phase1_pid=$P1 alive=$(kill -0 $P1 2>/dev/null && echo yes || echo no)"
cat /proc/asound/cards
cat "$S/guard-events.jsonl" | tail -2

echo "== arm phase-2 guard with the full mixer stop set =="
systemctl reset-failed "kebab-audio-guard-$RUN" 2>/dev/null || true
systemd-run --unit="kebab-audio-guard-$RUN" --collect --quiet \
  --description="kebab audio silence guard phase 2 ($RUN)" \
  /usr/bin/python3 /usr/local/lib/kebab-audio/kebab_audio.py guard \
  --run "$RUN" --manifest-sha "$SHA" --max-seconds 420
for i in $(seq 1 60); do
  st=$(python3 -c "import json;print((json.load(open('$R/guard.json')) or {}).get('state',''))" 2>/dev/null)
  [ "$st" = "armed" ] && break
  sleep 0.25
done
echo "guard2_state=$st"
cat "$R/guard.json"
tail -2 "$S/guard-events.jsonl"
