#!/bin/bash
# PAM moves the graphical processes to a login session scope. Explicitly stop
# their process group; stopping the system service cgroup alone is insufficient.
set -euo pipefail
if [[ ${1:-} == --wait-display ]]; then
  for _ in {1..60}; do
    if ! pgrep -x Xorg >/dev/null && ! pgrep -x phoc >/dev/null; then
      exit 0
    fi
    sleep .25
  done
  echo 'Previous display server did not release the seat' >&2
  exit 1
fi
[[ $EUID != 0 && -n ${XDG_RUNTIME_DIR:-} ]]
session_pid=
cleanup() {
  trap - EXIT INT TERM
  if [[ -n $session_pid ]]; then
    kill -TERM -- "-$session_pid" 2>/dev/null || true
    # A frozen client cannot handle TERM. Bound cleanup before restoring DRM.
    for _ in {1..10}; do
      kill -0 -- "-$session_pid" 2>/dev/null || break
      sleep .1
    done
    kill -KILL -- "-$session_pid" 2>/dev/null || true
    wait "$session_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'cleanup; exit 0' INT TERM
/usr/bin/setsid /usr/bin/dbus-run-session /usr/bin/phoc -S \
  -C /etc/kebab/phoc.ini -E /usr/local/libexec/kebab-mobile-session &
session_pid=$!
started=$(date +%s)
while kill -0 "$session_pid" 2>/dev/null; do
  sleep 2
  now=$(date +%s)
  if (( now - started > 20 )); then
    stamp=$(stat -c %Y "$XDG_RUNTIME_DIR/kebab-three-mode.json" 2>/dev/null || echo 0)
    if (( stamp < started || now - stamp > 8 )); then
      echo 'Three-mode heartbeat lost; restoring dashboard' >&2
      exit 1
    fi
  fi
done
wait "$session_pid"
