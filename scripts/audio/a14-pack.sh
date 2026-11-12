#!/bin/bash
set -u
RUN="$1"
S=/srv/kebab-audio-$RUN
G=/srv/kebab-audio-guardcheck-$RUN
echo "== refresh source-manifest with the guardian binary =="
python3 - "$S" "$RUN" <<'PY'
import hashlib, json, os, sys
s, run = sys.argv[1], sys.argv[2]
inst = "/usr/local/lib/kebab-audio"
files = {}
for name in sorted(os.listdir(inst)):
    p = os.path.join(inst, name)
    if os.path.isfile(p):
        files[p] = hashlib.sha256(open(p, "rb").read()).hexdigest()
wrapper = "/usr/local/sbin/kebab-audio"
if os.path.exists(wrapper):
    files[wrapper] = hashlib.sha256(open(wrapper, "rb").read()).hexdigest()
man = {
    "schema": 1, "run_id": run, "installed_files": files,
    "components": {
        "guardian": {"path": inst + "/kebab-audio-guard", "language": "rust",
                     "note": "stateful, long-running, root: implemented in Rust per project rule"},
        "guardian_python": {"path": inst + "/kebab_audio.py",
                            "note": "existing executor/orchestrator kept in Python; it is the caller, not the long-running guard"},
        "executor": {"path": inst + "/kebab_audio.py", "language": "python"},
        "verify": {"path": inst + "/kebab_audio_verify.py", "language": "python"},
        "core": {"path": inst + "/kebab_audio_core.py", "language": "python"},
    },
    "rollback_procedure": [
        "systemctl stop kebab-audio-guard-<run>   # Rust guardian: SIGTERM triggers the mute set",
        "/usr/local/sbin/kebab-audio rollback --run <run>",
        "for d in 15-0034 15-0035: echo $d > /sys/bus/i2c/drivers/tfa2/unbind",
        "rmmod snd_soc_tfa2   # plain, never --force",
        "verify /proc/asound/cards is empty and the amps are unbound",
    ],
}
json.dump(man, open(os.path.join(s, "source-manifest.json"), "w"), indent=2, sort_keys=True)
print("installed files hashed:", len(files))
for k, v in sorted(files.items()):
    print("  %-52s %s" % (k, v[:16]))
PY
echo "== rust guardian verification bundle =="
cd "$G" && sha256sum guard-selftest-rust.json guard-events.jsonl guard.json plan-real.txt plan-fixture.txt > SHA256SUMS
cat SHA256SUMS
cd "$S" && sha256sum baseline.json control-map.json trial-manifest.json guard-selftest-device.json guard-events.jsonl journal.jsonl probe-evidence.json result.json rollback-report.json source-manifest.json pre-state.txt source-hashes-device.txt > SHA256SUMS
echo "== pack both =="
sudo -n tar -cz -C /srv -f /tmp/ka-$RUN.tgz kebab-audio-$RUN
sudo -n tar -cz -C /srv -f /tmp/kag-$RUN.tgz kebab-audio-guardcheck-$RUN
sudo -n chmod 644 /tmp/ka-$RUN.tgz /tmp/kag-$RUN.tgz
ls -l /tmp/ka-$RUN.tgz /tmp/kag-$RUN.tgz
