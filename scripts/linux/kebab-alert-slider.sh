#!/bin/bash
# Runtime driver only for the verified kernel and Hall sensor calibration.
set -euo pipefail
[[ $EUID == 0 && $(uname -m) == aarch64 ]]
[[ $(uname -r) == 7.2.0-kebab-debian13-r2-charge-policy+ ]]
snapshot=/sys/module/kebab_alert_slider/parameters/snapshot
case "${1:-}" in
  start)
    [[ ! -d /sys/module/kebab_alert_slider && ! -d /sys/module/kebab_alert_slider_raw ]]
    sha256sum --status -c /etc/kebab/alert-slider-module.sha256
    centres=$(python3 - /etc/kebab/alert-slider-calibration.json <<'PY'
import json, sys
v = json.load(open(sys.argv[1]))
assert isinstance(v, list) and len(v) == 6
assert all(type(x) is int and -32768 <= x <= 32767 for x in v)
print(','.join(map(str, v)))
PY
    )
    insmod /lib/modules/7.2.0-kebab-debian13-r2-charge-policy+/extra/kebab-alert-slider.ko \
      persistent=1 seconds=0 calibration="$centres" tolerance=12
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
