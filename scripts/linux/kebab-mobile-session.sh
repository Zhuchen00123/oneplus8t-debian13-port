#!/bin/bash
# Run inside a dedicated DBus/Wayland session, as the ordinary desktop user.
set -euo pipefail
[[ $EUID != 0 ]]
export GDK_BACKEND=wayland
export XDG_CURRENT_DESKTOP=Phosh
export NO_AT_BRIDGE=1
export GTK_A11Y=none
export GSETTINGS_BACKEND=keyfile
export XDG_CONFIG_HOME=${KEBAB_PROFILE_DIR:-/srv/kebab-three-mode/profile}
mkdir -p "$XDG_CONFIG_HOME"
python3 - <<'PY'
from gi.repository import Gio, GLib
values = {
 'org.gnome.desktop.session': {'idle-delay': ('u', 0)},
 'org.gnome.desktop.screensaver': {'lock-enabled': ('b', False)},
 'org.gnome.desktop.background': {'picture-uri': ('s', ''),
   'picture-uri-dark': ('s', ''), 'primary-color': ('s', '#07100d'),
   'color-shading-type': ('s', 'solid')},
 'sm.puri.phosh': {'enable-suspend': ('b', False),
   'favorites': ('as', ['kebab-console.desktop', 'kebab-panel-view.desktop', 'kebab-firefox.desktop', 'kebab-qq.desktop', 'org.gnome.TextEditor.desktop', 'thunar.desktop']),
   'force-adaptive': ('as', ['kebab-console.desktop', 'kebab-panel-view.desktop', 'thunar.desktop', 'xfce4-terminal.desktop', 'kebab-firefox.desktop', 'firefox-esr.desktop', 'kebab-qq.desktop', 'qq.desktop'])},
 'sm.puri.phosh.notifications': {'wakeup-screen-triggers': ('as', [])},
 # Squeekboard interprets false as suppressing automatic display, while its
 # explicit DBus ForceVisible still works. Focus changes otherwise reset hides.
 'org.gnome.desktop.a11y.applications': {'screen-keyboard-enabled': ('b', False)},
 'org.gnome.desktop.input-sources': {'sources': ('a(ss)', [('xkb', 'us')])},
}
for schema, keys in values.items():
 settings = Gio.Settings.new(schema)
 for key, (kind, value) in keys.items():
  if not settings.set_value(key, GLib.Variant(kind, value)):
   raise RuntimeError(f'Could not configure {schema} {key}')
Gio.Settings.sync()
PY
/usr/libexec/phosh --unlocked &
shell_pid=$!
/usr/bin/squeekboard &
keyboard_pid=$!
ui_pid=
cleanup() {
  kill ${ui_pid:+"$ui_pid"} "$keyboard_pid" "$shell_pid" 2>/dev/null || true
  wait ${ui_pid:+"$ui_pid"} "$keyboard_pid" "$shell_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
sleep 1
kill -0 "$shell_pid" "$keyboard_pid"
/usr/bin/python3 /usr/local/libexec/kebab-three-mode &
ui_pid=$!
# An unexpected exit must stop the complete graphical session, allowing rollback.
wait -n "$shell_pid" "$keyboard_pid" "$ui_pid"
exit 1
