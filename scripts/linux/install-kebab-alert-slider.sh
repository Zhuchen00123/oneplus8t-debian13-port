#!/bin/bash
# Install a reviewed, hashed bundle on the already-running Debian phone.
set -euo pipefail
[[ $EUID == 0 && $(uname -m) == aarch64 ]]
[[ $(uname -r) == 7.2.0-kebab-debian13-r2-charge-policy+ ]]
[[ $(cat /sys/firmware/devicetree/base/compatible | tr '\0' '\n' | head -1) == oneplus,kebab ]]
[[ ! -d /sys/module/kebab_alert_slider && ! -d /sys/module/kebab_alert_slider_raw ]]
cd "$(dirname "$0")"
sha256sum -c SHA256SUMS
[[ -f alert-slider-calibration.json && ! -L alert-slider-calibration.json ]]
python3 - <<'PY'
import json
v = json.load(open('alert-slider-calibration.json'))
assert isinstance(v, list) and len(v) == 6
assert all(type(x) is int and -32768 <= x <= 32767 for x in v)
PY
[[ $(modinfo -F vermagic ./kebab-alert-slider.ko) == '7.2.0-kebab-debian13-r2-charge-policy+ SMP preempt mod_unload aarch64' ]]
bash -n kebab-alert-slider.sh
# Validate the unit before its executable is installed, then validate real paths.
verify_unit=$(mktemp --suffix=.service)
trap 'rm -f -- "$verify_unit"' EXIT
sed -e 's@^ExecStart=.*@ExecStart=/bin/true@' -e 's@^ExecStop=.*@ExecStop=/bin/true@' \
  kebab-alert-slider.service >"$verify_unit"
systemd-analyze verify "$verify_unit"
targets=(/lib/modules/7.2.0-kebab-debian13-r2-charge-policy+/extra/kebab-alert-slider.ko /usr/local/sbin/kebab-alert-slider /etc/systemd/system/kebab-alert-slider.service /etc/kebab/alert-slider-module.sha256 /etc/kebab/alert-slider-calibration.json)
for path in "${targets[@]}"; do
  [[ ! -e $path && ! -L $path ]] || { echo "Existing file requires review: $path" >&2; exit 1; }
done
for unit in kebab-charge-policy kebab-health.timer kebab-powerkey-screen kebab-status-panel kebab-sillytavern; do
  systemctl is-active --quiet "$unit"
done
backup=/var/backups/kebab-alert-slider-$(date +%Y%m%d-%H%M%S)
install -d -m 0700 "$backup"
cat /proc/bus/input/devices >"$backup/input-before.txt"
cat /sys/kernel/debug/regulator/regulator_summary >"$backup/regulators-before.txt"
systemctl show kebab-charge-policy kebab-health.timer kebab-powerkey-screen kebab-status-panel kebab-sillytavern >"$backup/services-before.txt"
install -D -m 0644 kebab-alert-slider.ko "${targets[0]}"
install -D -m 0755 kebab-alert-slider.sh "${targets[1]}"
install -D -m 0644 kebab-alert-slider.service "${targets[2]}"
sha256sum "${targets[0]}" >"$backup/module.sha256"
install -D -m 0644 "$backup/module.sha256" "${targets[3]}"
install -D -m 0600 alert-slider-calibration.json "${targets[4]}"
cp SHA256SUMS "$backup/bundle.sha256"
systemctl daemon-reload
systemd-analyze verify kebab-alert-slider.service
echo "Installed disabled; baseline: $backup"
