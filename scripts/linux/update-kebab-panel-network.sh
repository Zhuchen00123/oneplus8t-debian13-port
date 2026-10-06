#!/bin/bash
# Update only the panel and its new read-only snapshot publisher on the phone.
set -euo pipefail
[[ $EUID == 0 && $(uname -m) == aarch64 ]]
[[ $# == 1 && $1 =~ ^[0-9a-f]{64}$ ]]
cd "$(dirname "$0")"
sha256sum -c SHA256SUMS
[[ $(sha256sum /usr/local/bin/kebab-status-panel | cut -d ' ' -f1) == "$1" ]]
systemctl is-active --quiet kebab-status-panel
systemctl is-active --quiet kebab-powerkey-screen
for path in /usr/local/libexec/kebab-panel-network /etc/systemd/system/kebab-panel-network.{service,timer}; do
  [[ ! -e $path && ! -L $path ]] || { echo "Existing file needs review: $path" >&2; exit 1; }
done
python3 test-kebab-status-panel.py
systemd-analyze verify ./kebab-panel-network.service ./kebab-panel-network.timer
backup=/var/backups/kebab-panel-network-$(date +%Y%m%d-%H%M%S)
install -d -m 0700 "$backup"
cp -a /usr/local/bin/kebab-status-panel "$backup/kebab-status-panel"
sha256sum "$backup/kebab-status-panel" > "$backup/SHA256SUMS"
rollback() {
  systemctl disable --now kebab-panel-network.timer || true
  systemctl stop kebab-panel-network.service || true
  cp -a "$backup/kebab-status-panel" /usr/local/bin/kebab-status-panel.restore
  mv /usr/local/bin/kebab-status-panel.restore /usr/local/bin/kebab-status-panel
  systemctl restart kebab-status-panel || true
  echo "Panel restored from $backup; new snapshot files retained for inspection" >&2
}
trap rollback ERR
install -m 0755 kebab-panel-network.py /usr/local/libexec/kebab-panel-network
install -m 0644 kebab-panel-network.service kebab-panel-network.timer /etc/systemd/system/
systemctl daemon-reload
systemctl start kebab-panel-network.service
sudo -u debian python3 -c 'import json; from pathlib import Path; d=json.loads(Path("/run/kebab-panel-network/status.json").read_text()); assert d["campus"] and d["services"]'
install -m 0755 kebab-status-panel.py /usr/local/bin/kebab-status-panel.new
mv /usr/local/bin/kebab-status-panel.new /usr/local/bin/kebab-status-panel
systemctl enable --now kebab-panel-network.timer
systemctl restart kebab-status-panel
systemctl is-active --quiet kebab-status-panel
trap - ERR
printf 'Backup: %s\n' "$backup"
sha256sum /usr/local/bin/kebab-status-panel /usr/local/libexec/kebab-panel-network
