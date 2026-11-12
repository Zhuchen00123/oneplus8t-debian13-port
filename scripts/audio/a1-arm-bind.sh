#!/bin/bash
# A1/A2 phase-1: freeze the silent-bind manifest, arm the device guard in a
# transient unit (survives SSH loss), and report the arm state. No sound yet.
set -u
RUN="$1"
S=/srv/kebab-audio-$RUN
R=/run/kebab-audio-$RUN
MANIFEST="$S/trial-manifest.json"

python3 - "$RUN" "$S" <<'PY'
import json, os, sys
run, s = sys.argv[1], sys.argv[2]
boot = open('/proc/sys/kernel/random/boot_id').read().strip()
rel = os.uname().release
verify = lambda aid, chk, t: {"id": aid, "kind": "verify", "check": chk,
                              "read_only": True, "timeout_s": t}
manifest = {
    "schema": 1, "run_id": run, "boot_id": boot, "kernel_release": rel,
    "phase": "A2-silent-bind",
    # Before a card exists the only provable silence action is unloading the
    # codec driver: there is nothing to mute yet, and nothing can be routed.
    "stop_actions": [{"kind": "rmmod", "module": "snd_soc_tfa2"}],
    "accept": {"min_snr_db": 10.0, "min_abs_corr": 0.6,
               "max_clipped_fraction": 0.0, "require_nonzero": True},
    "actions": [
        {"id": "load-tfa2", "kind": "modprobe", "module": "snd_soc_tfa2",
         "cost": {"module_load": 1}, "timeout_s": 20, "requires": ["silence"]},
        verify("binding", "binding", 20),
        verify("idle-safety", "idle-safety", 20),
        verify("transport", "transport", 20),
        verify("pipewire", "pipewire", 20),
        verify("server-regression", "server-regression", 30),
    ],
}
with open(os.path.join(s, "trial-manifest.json"), "w") as fh:
    fh.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
print("manifest actions:", [a["id"] for a in manifest["actions"]])
PY

SHA=$(sha256sum "$MANIFEST" | cut -d' ' -f1)
echo "manifest_sha=$SHA"
systemctl reset-failed "kebab-audio-guard-$RUN" 2>/dev/null || true
systemd-run --unit="kebab-audio-guard-$RUN" --collect --quiet \
  --description="kebab audio silence guard ($RUN)" \
  /usr/bin/python3 /usr/local/lib/kebab-audio/kebab_audio.py guard \
  --run "$RUN" --manifest-sha "$SHA" --max-seconds 600
for i in $(seq 1 40); do
  st=$(python3 -c "import json;print((json.load(open('$R/guard.json')) or {}).get('state',''))" 2>/dev/null)
  [ "$st" = "armed" ] && break
  sleep 0.25
done
echo "guard_state=$st"
cat "$R/guard.json" 2>/dev/null | head -30
echo "--- guard events"; cat "$S/guard-events.jsonl" 2>/dev/null
systemctl is-active "kebab-audio-guard-$RUN"
