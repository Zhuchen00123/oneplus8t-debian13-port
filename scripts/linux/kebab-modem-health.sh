#!/bin/sh
# Periodic health check for the external SDX55.
#
# Two things are worth watching, both learned on this board:
#   * GNSS silently stops producing satellites when the modem is not ONLINE
#     (the LOC engine still answers, so nothing looks broken).
#   * A firmware ERRFATAL removes mhi1 and the whole stack; a module reload does
#     NOT bring the QRTR node back, so that case needs a reboot and should be
#     reported rather than "fixed" by restarting anything.
set -u
STATE=/run/kebab-sdx55-health.json
MHI1=0
[ -d /sys/bus/mhi/devices/mhi1 ] && MHI1=1
NODE=$(qrtr-lookup 2>/dev/null | awk '$1==16 && $4>1 {print $4; exit}')
NODE=${NODE:-}
OP=unknown
if [ "$MHI1" = 1 ] && [ -n "$NODE" ]; then
  OP=$(timeout 10 qmicli -d "qrtr://$NODE" --dms-get-operating-mode 2>/dev/null | sed -n "s/.*Mode: '\([^']*\)'.*/\1/p")
  OP=${OP:-unknown}
fi
SATS=$(sed -n 's/.*"sats_visible": \([0-9]*\).*/\1/p' /run/kebab-gps.json 2>/dev/null)

# SIM presence (the only reliable signal; card state, not "sim_slots_present")
SIM=0
if [ "$MHI1" = 1 ] && [ -n "$NODE" ]; then
  SIM=$(timeout 15 qmicli -d "qrtr://$NODE" --uim-get-slot-status 2>/dev/null | grep -c "Card status: present")
  SIM=${SIM:-0}
fi
# Data bearer: whatever data-up configured on the raw-IP interface
DATA_ADDR=$(ip -4 -o addr show dev mhi_hwip0 2>/dev/null | awk '{print $4}' | head -1)
DATA_ADDR=${DATA_ADDR:-}
DATA_LINK=$(cat /sys/class/net/mhi_hwip0/operstate 2>/dev/null)
DATA_LINK=${DATA_LINK:-gone}
FIX=$(sed -n 's/.*"fix": \(true\|false\).*/\1/p' /run/kebab-gps.json 2>/dev/null)

ACTION=none
if [ "$MHI1" = 0 ]; then
  ACTION=modem-gone
  logger -t kebab-sdx55-health "mhi1 is gone (firmware ERRFATAL?); a reboot is required to restore the QRTR node"
elif [ "$OP" != online ] && [ -n "$NODE" ]; then
  OUT=$(timeout 25 qmicli -d "qrtr://$NODE" --dms-set-operating-mode=online 2>&1)
  RC=$?
  if [ "$RC" -eq 0 ]; then
    ACTION=reassert-online
    logger -t kebab-sdx55-health "operating mode was '$OP'; set back to online (GNSS needs it)"
  else
    ACTION=online-failed
    # One line, so a stuck transition (QMI error 60 InvalidTransition) is
    # visible instead of just "failed".
    logger -t kebab-sdx55-health "operating mode is '$OP' and could not be set online: $(echo "$OUT" | tr '\n' ' ' | cut -c1-160)"
  fi
fi

TMP="$STATE.tmp.$$"
printf '{"ts":"%s","mhi1":%s,"qrtr_node":"%s","opmode":"%s","sats_visible":"%s","fix":"%s","sim_present":%s,"data_addr":"%s","data_link":"%s","action":"%s"}\n' \
  "$(date -u +%FT%TZ)" "$MHI1" "$NODE" "$OP" "${SATS:-}" "${FIX:-}" "$SIM" "$DATA_ADDR" "$DATA_LINK" "$ACTION" > "$TMP" && mv "$TMP" "$STATE"
cat "$STATE"
