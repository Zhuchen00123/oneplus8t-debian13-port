#!/bin/bash
set -euo pipefail
[[ $EUID != 0 ]]
[[ -d /srv/kebab-apps/qq-config && -O /srv/kebab-apps/qq-config ]]
export XDG_CONFIG_HOME=/srv/kebab-apps/qq-config
export XDG_CACHE_HOME=/srv/kebab-apps/cache
export ELECTRON_OZONE_PLATFORM_HINT=wayland
exec /opt/QQ/qq --disable-gpu --ozone-platform=wayland --enable-wayland-ime \
  --wayland-text-input-version=3 "$@"
