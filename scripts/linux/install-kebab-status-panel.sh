#!/bin/bash
# Run only on the existing Debian phone, from an explicitly transferred staging directory.
set -euo pipefail
[[ $EUID == 0 && $(uname -m) == aarch64 ]]
[[ $(uname -r) == 7.2.0-kebab-debian13-r2-charge-policy+ ]]
[[ $(systemctl get-default) == multi-user.target ]]
for unit in lightdm kebab-xfce-test kebab-status-panel kebab-maintenance-desktop; do
  ! systemctl is-active --quiet "$unit"
done
for unit in kebab-charge-policy kebab-powerkey-screen; do
  systemctl is-active --quiet "$unit"
done
cd "$(dirname "$0")"
sha256sum -c SHA256SUMS
python3 -c 'import gi, cairo; gi.require_version("Gtk", "3.0"); gi.require_version("PangoCairo", "1.0"); from gi.repository import Gtk, PangoCairo'
python3 test-kebab-status-panel.py
bash -n kebab-display.sh
sh -n kebab-panel-session.sh
systemd-analyze verify ./kebab-status-panel.service ./kebab-maintenance-desktop.service
sources=(kebab-status-panel.py kebab-panel-session.sh kebab-display.sh kebab-panel.desktop kebab-panel-lightdm.conf kebab-maintenance-lightdm.conf kebab-status-panel.service kebab-maintenance-desktop.service)
targets=(/usr/local/bin/kebab-status-panel /usr/local/libexec/kebab-panel-session /usr/local/sbin/kebab-display /usr/share/xsessions/kebab-panel.desktop /etc/kebab/panel-lightdm.conf /etc/kebab/maintenance-lightdm.conf /etc/systemd/system/kebab-status-panel.service /etc/systemd/system/kebab-maintenance-desktop.service)
for path in "${targets[@]}"; do
  [[ ! -e $path && ! -L $path ]] || { echo "Existing file requires review: $path" >&2; exit 1; }
done
backup=/var/backups/kebab-status-panel-$(date +%Y%m%d-%H%M%S)
install -d -m 0700 "$backup"
systemctl get-default >"$backup/default-target.txt"
systemctl is-enabled lightdm >"$backup/lightdm-enabled.txt" || true
systemctl show kebab-charge-policy kebab-powerkey-screen kebab-health.timer >"$backup/services-before.txt"
cat /sys/class/backlight/ae94000.dsi.0/brightness >"$backup/brightness-before.txt"
tar -czf "$backup/graphical-config-before.tar.gz" --ignore-failed-read /etc/lightdm /home/debian/.dmrc /var/cache/lightdm/dmrc/debian.dmrc
sha256sum "$backup/graphical-config-before.tar.gz" >"$backup/SHA256SUMS"
for i in "${!sources[@]}"; do
  mode=0644
  [[ $i -gt 2 ]] || mode=0755
  install -D -m "$mode" "${sources[$i]}" "${targets[$i]}"
done
systemctl daemon-reload
systemd-analyze verify kebab-status-panel.service kebab-maintenance-desktop.service
# Leave services disabled until the bounded graphical validation passes.
printf '%s\n' "$backup" > /var/lib/kebab-status-panel-backup-path
printf 'Installed; baseline backup: %s\n' "$backup"
