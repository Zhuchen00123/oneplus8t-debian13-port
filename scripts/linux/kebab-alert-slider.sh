#!/bin/bash
# Runtime module on r2; startup health gate for the r3/r4 builtin.
set -euo pipefail
[[ $EUID == 0 && $(uname -m) == aarch64 ]]
snapshot=/sys/module/kebab_alert_slider/parameters/snapshot
kernel_release=$(uname -r)
if [[ $kernel_release == 7.2.0-kebab-debian13-r3-slider+ ||
      $kernel_release == 7.2.0-kebab-debian13-r4-venus+ ]]; then
  case "${1:-}" in
    start)
      # The I2C core probes builtin drivers once bus and vdd are available.
      # This gate never loads a second driver or overwrites its calibration.
      python3 - "$snapshot" <<'PY'
import json, sys, time
for _ in range(50):
    try:
        with open(sys.argv[1]) as stream:
            s = json.load(stream)
        if (s['active'] and s['owned'] and s['count'] >= 3 and
                s['error'] == 0 and s['age_ms'] <= 300 and
                s['reported'] in (0, 1, 2)):
            print('Builtin alert slider recognised, profile=' + str(s['reported']))
            break
    except (OSError, ValueError, KeyError):
        pass
    time.sleep(0.2)
else:
    raise SystemExit('Builtin alert slider has not produced a healthy stable position')
PY
      ;;
    stop)
      echo 'Builtin recognition remains kernel-owned; this service is a health gate.'
      ;;
    status) cat "$snapshot" ;;
    *) echo 'Usage: kebab-alert-slider start|stop|status' >&2; exit 2 ;;
  esac
  exit 0
fi
[[ $(uname -r) == 7.2.0-kebab-debian13-r2-charge-policy+ ]]
case "${1:-}" in
  start)
    [[ ! -d /sys/module/kebab_alert_slider && ! -d /sys/module/kebab_alert_slider_raw ]]
    sha256sum --status -c /etc/kebab/alert-slider-module.sha256
    insmod /lib/modules/7.2.0-kebab-debian13-r2-charge-policy+/extra/kebab-alert-slider.ko \
      persistent=1 seconds=0 calibration=55,148,93,102,147,55 tolerance=12
    trap 'rmmod kebab_alert_slider' ERR
    python3 - "$snapshot" <<'PY'
import json, sys, time
time.sleep(0.5)
s = json.load(open(sys.argv[1]))
assert s['active'] and s['owned'] and s['count'] >= 3
assert s['error'] == 0 and s['age_ms'] <= 300 and s['reported'] in (0, 1, 2)
print('Alert slider recognised, profile=' + str(s['reported']))
PY
    trap - ERR
    ;;
  stop)
    if [[ -d /sys/module/kebab_alert_slider ]]; then
      rmmod kebab_alert_slider
      [[ ! -d /sys/module/kebab_alert_slider ]]
    fi
    ;;
  status)
    [[ -f $snapshot ]]
    cat "$snapshot"
    ;;
  *) echo 'Usage: kebab-alert-slider start|stop|status' >&2; exit 2 ;;
esac
