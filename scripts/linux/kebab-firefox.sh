#!/bin/bash
set -euo pipefail
[[ $EUID != 0 ]]
[[ -d /srv/kebab-apps/firefox && -O /srv/kebab-apps/firefox ]]
export MOZ_ENABLE_WAYLAND=1
export GDK_BACKEND=wayland
exec /usr/bin/firefox-esr --profile /srv/kebab-apps/firefox "$@"
