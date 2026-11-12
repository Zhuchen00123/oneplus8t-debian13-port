#!/bin/sh
# kebab: make sure ModemManager actually notices the SDX55 after boot.
#
# Two failure modes are known from the Apollo project (same modem/firmware):
#   1. MM can start before the modem publishes its QMI services. It marks the
#      QRTR port invalid after repeated timeouts and never retries, which looks
#      exactly like "no mobile network".
#   2. Even with the modem present, the first registration attempt can sit in
#      "searching" indefinitely; taking the radio down and back up makes it
#      register within seconds (root cause unknown upstream).
#
# This helper only acts when a modem exists and a SIM is present, so a
# card-less board is never poked.
set -u
WAIT=${KEBAB_MM_WAIT:-30}
TRIES=${KEBAB_MM_TRIES:-8}
EVERY=${KEBAB_MM_EVERY:-30}
REG_WAIT=${KEBAB_MM_REG_WAIT:-90}
REG_TRIES=${KEBAB_MM_REG_TRIES:-2}

log() { logger -t kebab-mm "$*"; echo "[$(date -u +%FT%TZ)] $*"; }
modem() { mmcli -L 2>/dev/null | grep -oE '/Modem/[0-9]+' | head -1 | sed 's|.*/||'; }
field() { mmcli -m "$1" 2>/dev/null | sed -n "s/.*$2: *//p" | head -1; }

sleep "$WAIT"

i=0
while [ -z "$(modem)" ] && [ "$i" -lt "$TRIES" ]; do
  i=$((i + 1))
  log "no modem yet, restarting ModemManager ($i/$TRIES)"
  systemctl restart ModemManager || exit 0
  sleep "$EVERY"
done
m=$(modem)
if [ -z "$m" ]; then
  log "still no modem after $TRIES restarts, giving up"
  exit 0
fi
log "modem $m present"

case "$(field "$m" registration)" in
  home|roaming) log "modem $m already registered"; exit 0 ;;
esac
if [ "$(field "$m" sim)" = "none" ] || [ -z "$(field "$m" sim)" ]; then
  log "modem $m has no usable SIM, leaving the radio alone"
  exit 0
fi

j=0
while [ "$j" -lt "$REG_TRIES" ]; do
  n=0
  while [ "$n" -lt "$REG_WAIT" ]; do
    case "$(field "$m" registration)" in
      home|roaming) log "modem $m registered"; exit 0 ;;
    esac
    n=$((n + 10)); sleep 10
  done
  j=$((j + 1))
  log "not registered after ${REG_WAIT}s, cycling the radio ($j/$REG_TRIES)"
  mmcli -m "$m" --set-power-state-low >/dev/null 2>&1
  sleep 5
  mmcli -m "$m" --set-power-state-on >/dev/null 2>&1
  sleep 5
done
log "modem $m still not registered"
exit 0
