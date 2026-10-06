#!/bin/bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Use sudo kebab-display mobile|panel|desktop|off|status' >&2; exit 1; }
exec 9>/run/lock/kebab-display.lock
flock -n 9 || { echo 'Another display transition is running' >&2; exit 1; }
case ${1:-status} in
  status) systemctl --no-pager status kebab-three-mode.service kebab-status-panel.service kebab-maintenance-desktop.service; exit $? ;;
  mobile) systemctl start kebab-three-mode.service; exit $? ;;
  panel|desktop|off) ;;
  *) echo 'Usage: kebab-display mobile|panel|desktop|off|status' >&2; exit 2 ;;
esac
for other in lightdm.service kebab-xfce-test.service; do
  if systemctl is-active --quiet "$other"; then
    echo "$other is running; stop that session explicitly before switching" >&2
    exit 1
  fi
done
systemctl stop kebab-three-mode.service kebab-status-panel.service kebab-maintenance-desktop.service
for unit in kebab-status-panel.service kebab-maintenance-desktop.service; do
  if systemctl is-failed --quiet "$unit"; then
    systemctl reset-failed "$unit"
  fi
done
case $1 in
  panel) systemctl start kebab-status-panel.service ;;
  desktop) systemctl start kebab-maintenance-desktop.service ;;
esac
# Maintenance transitions close graphical windows; tmux remains independent.
# Do not bind this command to the slider. off leaves brightness to the power key.
