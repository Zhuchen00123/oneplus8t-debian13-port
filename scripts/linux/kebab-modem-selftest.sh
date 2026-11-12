#!/bin/sh
# Kebab SDX55 self-test: verify the modem stack from userspace, right now.
#
# Two modes, auto-detected from the SIM slot status:
#   no card -> the "known good without a SIM" baseline: unit up, QMI answering,
#              online, bands present, WDS bind OK, start reaching the call layer
#   card    -> stricter: card present, start must actually succeed
#
# Exit status: 0 when every check passes, 1 otherwise. Read-only apart from a
# WDS bind/start attempt that never configures an interface or a route.
set -u
export LC_ALL=C LANG=C   # every parser below matches English tool output
WDS=/usr/local/lib/kebab-modem/kebab-qmi-wds.py
NODE=$(qrtr-lookup 2>/dev/null | awk '$1==16 && $4>1 {print $4; exit}')
PASS=0
FAIL=0
ok()  { printf "PASS  %s\n" "$1"; PASS=$((PASS + 1)); }
bad() { printf "FAIL  %s\n" "$1"; FAIL=$((FAIL + 1)); }

echo "== kebab modem self-test =="
echo

if systemctl is-active --quiet kebab-sdx55-modem; then ok "kebab-sdx55-modem.service active"; else bad "kebab-sdx55-modem.service not active"; fi
if [ -d /sys/bus/mhi/devices/mhi1 ]; then ok "mhi1 present"; else bad "mhi1 missing (firmware ERRFATAL? needs a reboot)"; fi
if [ -n "$NODE" ]; then ok "QRTR node $NODE advertises a Location service"; else bad "no QRTR node; modem stack not up"; fi
[ -n "$NODE" ] || { echo; echo "RESULT: $PASS passed, $FAIL failed"; exit 1; }
Q="qmicli -d qrtr://$NODE"

OP=$($Q --dms-get-operating-mode 2>/dev/null | sed -n "s/.*Mode: '\([^']*\)'.*/\1/p")
if [ "$OP" = online ]; then ok "operating mode online"; else bad "operating mode is '$OP' (GNSS needs online)"; fi

BANDS=$($Q --dms-get-band-capabilities 2>/dev/null | sed -n "s/.*LTE bands: '\([^']*\)'.*/\1/p")
if [ -n "$BANDS" ]; then ok "LTE bands: $(echo "$BANDS" | cut -d, -f1-3)..."; else bad "LTE bands empty (MCFG/EFS problem)"; fi

MAN=$($Q --dms-get-manufacturer 2>/dev/null | sed -n "s/.*[Mm]anufacturer: '\(.*\)'/\1/p")
if [ -n "$MAN" ]; then ok "DMS answers (manufacturer readable)"; else bad "DMS get-manufacturer returned nothing"; fi

SIM=$($Q --uim-get-slot-status 2>/dev/null | grep -c "Card status: present")
[ -n "$SIM" ] || SIM=0
echo "INFO  sim_present=$SIM"

OUT=$(python3 "$WDS" up --profile 3 --timeout 20 2>&1)
if echo "$OUT" | grep -qE "bind +: result=0"; then ok "WDS Bind Mux Data Port (pcie/4/0)"; else bad "WDS bind failed"; fi
if [ "$SIM" -gt 0 ]; then
  if echo "$OUT" | grep -q "start   : result=0"; then ok "WDS Start Network succeeded"; else bad "WDS start failed with a card present"; fi
else
  if echo "$OUT" | grep -q "error=70"; then bad "WDS start returned INVALID_OPERATION (bind did not take)"; else ok "WDS start reached the call layer (error 14 / no-service without a SIM)"; fi
fi

if python3 "$WDS" selftest >/dev/null 2>&1; then ok "TLV/prefix/interface selftest"; else bad "selftest failed"; fi

# Community cross-check (their TECHNICAL_HANDOFF section 9 calls this the one
# success test): a properly provisioned modem lists its RATs.  If MCFG never
# ran (card inserted late) this comes back empty even though QMI answers.
CAPS=$($Q --dms-get-capabilities 2>/dev/null | sed -n "s/.*Networks: '\([^']*\)'.*/\1/p")
case "$CAPS" in
  *lte*) ok "DMS capabilities list RATs (networks: $CAPS)" ;;
  *) bad "DMS capabilities do not list LTE ('$CAPS') - MCFG/NV not provisioned?" ;;
esac

if [ -e /sys/class/net/mhi_hwip0 ]; then
  ok "mhi_hwip0 (IP_HW0 data path) present"
else
  bad "mhi_hwip0 missing (data path netdev is gone)"
fi

# ModemManager's port map matters on SIM day: if it picks mhi_swip0 the call
# comes up but no packet ever moves.  Only checked when MM has a modem.
if command -v mmcli >/dev/null 2>&1 && mmcli -L 2>/dev/null | grep -q Modem; then
  MIDX=$(mmcli -L 2>/dev/null | sed -n 's#.*/Modem/\([0-9]*\).*#\1#p' | head -1)
  PORTS=$(mmcli -m "${MIDX:-0}" --output-keyvalue 2>/dev/null | grep 'ports.value')
  if echo "$PORTS" | grep -q 'mhi_swip0 (ignored)'; then
    ok "MM ignores mhi_swip0 (will use the right data port)"
  else
    bad "MM does not ignore mhi_swip0 (it may pick the idle SW port)"
  fi
else
  echo "INFO  ModemManager has no modem yet; port map not checked"
fi

H=/run/kebab-sdx55-health.json
if [ -r "$H" ] && grep -q '"opmode":"online"' "$H" 2>/dev/null; then ok "health JSON reports online"; else bad "health JSON missing or not online"; fi

echo
echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
