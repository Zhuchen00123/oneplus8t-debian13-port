#!/bin/bash
# Install userspace only; activation requires an opt-in lease kernel already booted.
set -euo pipefail
bundle=$(realpath -e -- "${1:?Usage: install-charge-policy.sh bundle-directory install|activate|deactivate}")
mode=${2:?Select install, activate or deactivate}
[[ $EUID == 0 && $(uname -m) == aarch64 ]]
[[ $mode == install || $mode == activate || $mode == deactivate ]]
exec 9>/run/lock/kebab-charge-policy-install.lock
flock -n 9
python3 - "$bundle" <<'PY'
import hashlib,json,pathlib,sys
p=pathlib.Path(sys.argv[1])
m=json.loads((p/'bundle-manifest.json').read_text())
for name in ('kebab-charge-policy.py','kebab-charge-policy.json','kebab-charge-policy.service','kebab-health.py'):
    expected=m['files_sha256'][name]
    assert hashlib.file_digest((p/name).open('rb'),'sha256').hexdigest()==expected
PY
if [[ $mode == install ]]; then
    if systemctl is-active --quiet kebab-charge-policy.service; then
        echo 'Active policy exists; stop it before replacing its files.' >&2
        exit 1
    fi
    current_health=$(sha256sum /usr/local/sbin/kebab-health | awk '{print $1}')
    wanted_health=$(sha256sum "$bundle/kebab-health.py" | awk '{print $1}')
    [[ $current_health == 06f36c4d32a1638e2df356959871fefdf55df813c0317a880c1011ee66762de3 || $current_health == "$wanted_health" ]]
    backup=/var/backups/kebab-charge-policy/$(date +%Y%m%d-%H%M%S)
    mkdir -p "$backup" /etc/kebab
    for old in /usr/local/sbin/kebab-charge-policy /etc/kebab/charge-policy.json /etc/systemd/system/kebab-charge-policy.service /usr/local/sbin/kebab-health; do
        if [[ -e $old ]]; then cp -a --parents "$old" "$backup"; fi
    done
    install -m 755 "$bundle/kebab-charge-policy.py" /usr/local/sbin/kebab-charge-policy
    install -m 644 "$bundle/kebab-charge-policy.json" /etc/kebab/charge-policy.json
    install -m 644 "$bundle/kebab-charge-policy.service" /etc/systemd/system/kebab-charge-policy.service
    install -m 755 "$bundle/kebab-health.py" /usr/local/sbin/kebab-health
    systemd-analyze verify /etc/systemd/system/kebab-charge-policy.service
    systemctl daemon-reload
    systemctl disable kebab-charge-policy.service
    /usr/bin/python3 /usr/local/sbin/kebab-charge-policy --once
    echo 'INSTALLED_DISABLED; kernel and charging registers were not modified'
elif [[ $mode == activate ]]; then
    # Do not enable a service with mismatching or locally changed installed files.
    cmp "$bundle/kebab-charge-policy.py" /usr/local/sbin/kebab-charge-policy
    cmp "$bundle/kebab-charge-policy.json" /etc/kebab/charge-policy.json
    cmp "$bundle/kebab-charge-policy.service" /etc/systemd/system/kebab-charge-policy.service
    snapshot=$(cat /sys/bus/i2c/devices/5-005c/charge_test_snapshot)
    [[ $snapshot == *'allow_control=1 lease_seconds=90 recovering=0'* ]]
    [[ $snapshot == *'allow_test=0'* && $snapshot == *'last_error=0'* ]]
    systemctl enable --now kebab-charge-policy.service
    systemctl is-active --quiet kebab-charge-policy.service
    echo 'ACTIVE; check /run/kebab-charge-policy/status.json for verified state'
else
    systemctl disable --now kebab-charge-policy.service
    # SIGTERM best-effort restore is independent of the kernel expiry deadline.
    cat /sys/bus/i2c/devices/5-005c/charge_test_snapshot
    echo 'DISABLED; verify owned=0 before returning to the read-only boot image'
fi
