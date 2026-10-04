#!/bin/sh
set -eu
export GDK_BACKEND=x11
export NO_AT_BRIDGE=1
# Existing power-key service owns brightness. Don't let X blank independently.
xset s off
xset -dpms
exec /usr/bin/python3 /usr/local/bin/kebab-status-panel
