#!/bin/bash
set -euo pipefail
[[ $EUID != 0 ]]
case "${1:-}" in
  start)
    ! tmux -L kebab-terminal has-session -t console 2>/dev/null
    tmux -L kebab-terminal new-session -d -s console -c "$HOME" \; set-option -g exit-empty off
    tmux -L kebab-terminal display-message -p '#{pid}' > /run/kebab-terminal/tmux.pid
    ;;
  stop) tmux -L kebab-terminal kill-server ;;
  *) exit 2 ;;
esac
