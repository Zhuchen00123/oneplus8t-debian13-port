#!/bin/bash
set -euo pipefail
[[ $EUID == 0 && $(uname -r) == 7.2.0-kebab-debian13-r2-charge-policy+ ]]
cd "$(dirname "$0")"
sha256sum -c SHA256SUMS
systemd-analyze verify kebab-three-mode.service kebab-three-mode-fallback.service kebab-terminal-backend.service
systemctl is-active --quiet kebab-three-mode.service
systemctl is-active --quiet kebab-terminal-backend.service
python3 - <<'PY'
import json, time
from pathlib import Path
state=json.loads(Path('/run/user/1000/kebab-three-mode.json').read_text())
assert 0 <= time.monotonic()-state['monotonic_seconds'] < 2
assert state['mode'] in ('terminal','panel','desktop')
assert Path(f'/proc/{state["pid"]}').exists()
PY
backup=/var/backups/kebab-three-mode-activation-$(date +%Y%m%d-%H%M%S)
install -d -m 0700 "$backup"
cp -a /usr/local/sbin/kebab-display "$backup/kebab-display"
systemctl is-enabled kebab-status-panel.service > "$backup/panel-enabled.txt" || true
systemctl is-enabled kebab-three-mode.service > "$backup/mobile-enabled.txt" || true
install -m 0755 kebab-display.sh /usr/local/sbin/kebab-display
systemctl enable kebab-terminal-backend.service kebab-three-mode.service
systemctl enable --now bluetooth.service
systemctl disable kebab-status-panel.service
echo 'Three-mode autostart configured; old panel retained; no reboot performed'
