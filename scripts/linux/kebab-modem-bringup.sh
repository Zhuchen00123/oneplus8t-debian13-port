#!/bin/sh
# Kebab SDX55 (external PCIe modem) bring-up.
#
# Order matters:
#   1. pm-service-native + tqftpserv-sdx55 must be publishing before the MHI
#      modules load, otherwise the modem cannot fetch its RFS/NV and stalls.
#   2. The remote-EFS daemon must be serving before the modem asks for NV.
#   3. /etc/modprobe.d/90-kebab-server.conf only *blacklists* mhi_pci_generic
#      (no udev auto-load); an explicit modprobe still works, so the server
#      baseline stays untouched until this unit asks for the modem.
#
# The modem is discovered, never assumed: its QRTR node id can change, so the
# node is resolved from the LOC service (16) on every start.
#
# Operating-mode note (measured 2026-11-12, this device):
#   * DMS "offline" / the firmware's post-mission-mode "shutting-down" state both
#     leave the LOC engine running but with ZERO satellites; switching the modem
#     ONLINE makes the GNSS engine see the sky (37 sats, live GSM RSSI).
#   * Forcing "offline" once also made the modem reject later ONLINE transitions
#     with QMI InvalidTransition (error 60) until the next boot.
# So the default policy drives the modem ONLINE even without a SIM: GNSS is a
# delivered feature and it needs the RF front-end powered. "offline"/"none"
# remain available for experiments but are known to starve LOC.
set -u
TAG=kebab-sdx55
log() { logger -t "$TAG" "$*"; echo "[$(date -u +%FT%TZ)] $*"; }

PCI=/sys/bus/pci/devices/0002:01:00.0
if [ ! -e "$PCI" ]; then
  log "no SDX55 PCI function (0002:01:00.0); nothing to do"
  exit 0
fi

log "bringing up SDX55"
systemctl start pm-service-native.service tqftpserv-sdx55.service || log "WARN: RFS/PM start failed"

for m in mhi mhi_sahara_modem mhi_wwan_ctrl mhi_pci_generic; do
  if modprobe "$m"; then log "modprobe $m ok"; else log "modprobe $m FAILED"; fi
done

systemctl start mhi-efs-sync.service || log "WARN: mhi-efs-sync start failed"

# Readiness = the modem answers a real QMI request. mhi1 and the wwan ports
# appear at probe time, long before the firmware is running, so they are not a
# sufficient signal.
NODE=""
i=0
while [ $i -lt 70 ]; do
  NODE=$(qrtr-lookup 2>/dev/null | awk '$1==16 && $4>1 {print $4; exit}')
  if [ -n "$NODE" ] && timeout 10 qmicli -d "qrtr://$NODE" --dms-get-manufacturer >/dev/null 2>&1; then
    log "modem QMI ready after ${i}s (qrtr node $NODE)"
    break
  fi
  NODE=""
  i=$((i+1)); sleep 1
done
if [ -z "$NODE" ]; then
  log "ERROR: modem did not answer QMI within 70s; leaving the stack loaded for inspection"
  exit 1
fi

# Let UIM/NAS publish their services, then read the state before touching it.
sleep 3

# SIM first, carrier config second: MCFG picks and activates its profile
# roughly 15 s and 24 s after mission mode, and it cannot be retried at
# runtime. A PIN-locked or unbound card at that moment means no config is ever
# activated (empty band/RAT caps, DMS online refused). The helper exits in
# well under a second when no card is present, so it costs nothing today.
if [ -x /usr/local/lib/kebab-modem/sim-provision.sh ]; then
  timeout 45 /usr/local/lib/kebab-modem/sim-provision.sh || log "WARN: SIM provisioning step failed"
fi

MODE_NOW=$(timeout 20 qmicli -d "qrtr://$NODE" --dms-get-operating-mode 2>/dev/null | sed -n "s/.*Mode: '\([^']*\)'.*/\1/p")
CARD=$(timeout 20 qmicli -d "qrtr://$NODE" --uim-get-slot-status 2>/dev/null | grep -c "Card status: present")
[ -n "$CARD" ] || CARD=0
log "state: opmode=${MODE_NOW:-unknown} sim_cards_present=$CARD node=$NODE"

# Operating-mode policy (see header):
#   auto    -> always ONLINE (GNSS needs the RF front-end powered; a SIM only
#              decides whether registration can go past limited service)
#   online  -> force ONLINE
#   offline -> force OFFLINE (kept for experiments; breaks GNSS and later ONLINE)
#   none    -> leave untouched
POLICY="${KEBAB_SDX55_ONLINE:-auto}"
case "$POLICY" in
  auto)
    # Always ONLINE: the LOC/GNSS engine only receives satellites when the
    # modem's RF path is powered (see the header note). A SIM only changes
    # whether registration can go past limited service.
    TARGET=online ;;
  online|offline) TARGET="$POLICY" ;;
  *) TARGET="" ;;
esac
if [ -n "$TARGET" ]; then
  if [ "$TARGET" = "$MODE_NOW" ]; then
    log "operating mode already $TARGET (policy=$POLICY)"
  elif timeout 25 qmicli -d "qrtr://$NODE" --dms-set-operating-mode="$TARGET" >/dev/null 2>&1; then
    log "operating mode set to $TARGET (policy=$POLICY, sim_cards_present=$CARD)"
  else
    log "WARN: failed to set operating mode $TARGET (policy=$POLICY, sim_cards_present=$CARD)"
  fi
else
  log "operating mode left untouched (policy=$POLICY, sim_cards_present=$CARD)"
fi

# Second SIM pass: a card that only initialised after the first pass still gets
# one chance to bind/verify before MCFG's later pass. Also cheap when idle.
if [ -x /usr/local/lib/kebab-modem/sim-provision.sh ]; then
  timeout 45 /usr/local/lib/kebab-modem/sim-provision.sh >/dev/null 2>&1 || true
fi
log "done (qrtr node $NODE)"
