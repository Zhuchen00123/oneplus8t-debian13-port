#!/bin/bash
# Arm the phase-2 guard and prove the OLD guard is gone (new pid + new manifest sha).
set -u
RUN="$1"
S=/srv/kebab-audio-$RUN
R=/run/kebab-audio-$RUN
SHA=$(sha256sum "$S/trial-manifest.json" | cut -d' ' -f1)
echo "want_sha=$SHA"
systemctl reset-failed "kebab-audio-guard-$RUN" 2>/dev/null || true
systemctl stop "kebab-audio-guard-$RUN" 2>/dev/null || true
sleep 0.5
rm -f "$R/guard.json"
systemd-run --unit="kebab-audio-guard-$RUN" --collect \
  --description="kebab audio silence guard phase 2 ($RUN)" \
  /usr/bin/python3 /usr/local/lib/kebab-audio/kebab_audio.py guard \
  --run "$RUN" --manifest-sha "$SHA" --max-seconds 420 2>&1 | tail -5
for i in $(seq 1 80); do
  read -r ST PID MSHA <<EOF
$(python3 - "$R/guard.json" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("none 0 none"); raise SystemExit
print(d.get("state", "?"), d.get("pid", 0), d.get("manifest_sha", "none"))
PY
)
EOF
  [ "$ST" = "armed" ] && [ "$MSHA" = "$SHA" ] && break
  sleep 0.25
done
echo "guard2 state=$ST pid=$PID sha_match=$([ "$MSHA" = "$SHA" ] && echo yes || echo no)"
systemctl is-active "kebab-audio-guard-$RUN"
tail -3 "$S/guard-events.jsonl"
