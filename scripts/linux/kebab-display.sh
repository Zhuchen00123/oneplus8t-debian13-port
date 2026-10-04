#!/bin/bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Use sudo kebab-display panel|desktop|off|status' >&2; exit 1; }
exec 9>/run/lock/kebab-display.lock
flock -n 9 || { echo 'Another display transition is running' >&2; exit 1; }
case ${1:-status} in
  status) systemctl --no-pager status kebab-status-panel.service kebab-maintenance-desktop.service; exit $? ;;
  panel|desktop|off) ;;
  *) echo 'Usage: kebab-display panel|desktop|off|status' >&2; exit 2 ;;
esac
for other in lightdm.service kebab-xfce-test.service; do
  if systemctl is-active --quiet "$other"; then
    echo "$other is running; stop that session explicitly before switching" >&2
    exit 1
  fi
done
systemctl stop kebab-status-panel.service kebab-maintenance-desktop.service
for unit in kebab-status-panel.service kebab-maintenance-desktop.service; do
  if systemctl is-failed --quiet "$unit"; then
    systemctl reset-failed "$unit"
  fi
done
case $1 in
  panel) systemctl start kebab-status-panel.service ;;
  desktop) systemctl start kebab-maintenance-desktop.service ;;
esac
# off stops graphical sessions; brightness remains owned by the power-key service.
