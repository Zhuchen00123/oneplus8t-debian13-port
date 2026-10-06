#!/bin/bash
set -euo pipefail
[[ $EUID == 0 && $(uname -r) == 7.2.0-kebab-debian13-r2-charge-policy+ ]]
cd "$(dirname "$0")"
sha256sum -c SHA256SUMS
python3 -m py_compile kebab-three-mode.py kebab-ui-state.py kebab-ui-mode.py
python3 test-kebab-ui-state.py
bash -n kebab-mobile-session.sh kebab-mobile-compositor.sh kebab-terminal-backend.sh
targets=(/usr/local/libexec/kebab-three-mode /usr/local/libexec/kebab-ui-state.py /usr/local/libexec/kebab-panel-renderer.py /usr/local/libexec/kebab-mobile-session /usr/local/libexec/kebab-terminal-backend /usr/local/bin/kebab-ui-mode /etc/kebab/phoc.ini /etc/kebab/xkb/rules/kebab /etc/kebab/xkb/symbols/kebab /etc/systemd/system/kebab-terminal-backend.service /usr/local/share/applications/kebab-console.desktop /usr/local/share/applications/kebab-panel-view.desktop)
for path in "${targets[@]}"; do
  [[ ! -e $path && ! -L $path ]] || { echo "Existing candidate file requires review: $path" >&2; exit 1; }
done
for path in /usr/local/libexec/kebab-mobile-compositor /etc/systemd/system/kebab-three-mode.service /etc/systemd/system/kebab-three-mode-fallback.service /usr/local/libexec/test-kebab-xkb.py; do
  [[ ! -e $path && ! -L $path ]] || { echo "Existing candidate file requires review: $path" >&2; exit 1; }
done
files=(kebab-three-mode.py kebab-ui-state.py kebab-status-panel.py kebab-mobile-session.sh kebab-terminal-backend.sh kebab-ui-mode.py kebab-phoc.ini kebab-xkb-rules kebab-xkb-symbols kebab-terminal-backend.service kebab-console.desktop kebab-panel-view.desktop)
for i in "${!files[@]}"; do
  install -D -m 0644 "${files[$i]}" "${targets[$i]}"
done
chmod 0755 /usr/local/libexec/kebab-mobile-session /usr/local/libexec/kebab-terminal-backend /usr/local/bin/kebab-ui-mode
install -d -m 0700 -o debian -g debian /srv/kebab-three-mode/profile
install -D -m 0644 test-kebab-xkb.py /usr/local/libexec/test-kebab-xkb.py
install -D -m 0755 kebab-mobile-compositor.sh /usr/local/libexec/kebab-mobile-compositor
install -D -m 0644 kebab-three-mode.service /etc/systemd/system/kebab-three-mode.service
install -D -m 0644 kebab-three-mode-fallback.service /etc/systemd/system/kebab-three-mode-fallback.service
XKB_DEFAULT_RULES=kebab XKB_CONFIG_EXTRA_PATH=/etc/kebab/xkb python3 test-kebab-xkb.py
systemctl daemon-reload
systemd-analyze verify kebab-terminal-backend.service kebab-three-mode.service kebab-three-mode-fallback.service
systemctl start kebab-terminal-backend.service
echo 'Candidate installed; old graphical session unchanged, no autostart enabled'
