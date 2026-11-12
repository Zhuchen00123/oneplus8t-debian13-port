#!/bin/sh
# SDX55 SIM bring-up: activate the provisioning session and verify PIN1 early.
#
# Adapted from jfbobier/Oneplus-8T-kebab-pmos-bringup
# device/oneplus-kebab/kebab-uim-provision (same board family, same modem).
# Two hard-won facts from that project:
#
#  1. This modem firmware has no automatic UIM provisioning items
#     (/nv/item_files/modem/uim/mmgsdi/slot_automatic_provisioning etc. may be
#     absent), so the card can be detected but never bound to a subscription.
#     Without that binding ModemManager ends up at "GW primary session index
#     unknown" and reports sim-missing.
#  2. MCFG (carrier config) selects and activates in one pass roughly 15 s and
#     24 s after mission mode. If the card is still PIN-locked in that window,
#     no config is ever activated: band/RAT capability goes empty, DMS online
#     returns DeviceNotReady, and there is no data and no GNSS fix. MCFG cannot
#     be retriggered at runtime, so the card must be ready BEFORE it runs.
#
# Safety: PIN1 allows three attempts before PUK. This script verifies at most
# once per invocation and refuses unless all three retries are still available.
# The PIN is read from a root-only file and never written to the log.
set -u

PINFILE=/etc/kebab-sim-pin
WAIT=${KEBAB_SIM_WAIT:-0}      # extra seconds to wait for a slow UIM init
# On SIM day the card can take a few seconds to become visible after mission
# mode, and the provisioning session has to exist before MCFG's 15 s / 24 s
# passes (see header).  Opting in with a flag keeps normal no-card boots fast:
#   sudo touch /etc/kebab-modem-sim-expected
if [ -e /etc/kebab-modem-sim-expected ]; then
  WAIT=${KEBAB_SIM_WAIT:-12}
fi
NODE=$(qrtr-lookup 2>/dev/null | awk '$1==16 && $4>1 {print $4; exit}')
NODE=${NODE:-}
log() { logger -t kebab-sim "$*"; echo "[sim] $*"; }

if [ -z "$NODE" ]; then
  log "no Location node yet; modem not ready, nothing to do"
  exit 0
fi
Q="qmicli -d qrtr://$NODE"

slots() { timeout 15 $Q --uim-get-slot-status 2>/dev/null; }

# ---- 0. is there a card at all? (fast path: no card is the normal state) ----
S=$(slots)
i=0
while [ "$i" -lt "$WAIT" ]; do
  echo "$S" | grep -q "Card status: present" && break
  sleep 2; i=$((i + 2)); S=$(slots)
done
if ! echo "$S" | grep -q "Card status: present"; then
  log "no SIM card present in either slot; skipping"
  exit 0
fi
SLOT=$(echo "$S" | awk '/Physical slot/{s=$3} /Card status: present/{gsub(/:/,"",s); print s; exit}')
SLOT=${SLOT:-1}
log "card present in physical slot $SLOT"

# ---- 1. provisioning session (bind the card to a subscription) ----
C=$(timeout 20 $Q --uim-get-card-status 2>/dev/null)
if echo "$C" | grep -qE "Primary GW:[[:space:]]+slot"; then
  log "primary-gw session already present"
else
  AID=$(echo "$C" | grep -A1 'Application ID' | sed -n '2p' | tr -d ' \t:')
  if [ -z "$AID" ]; then
    log "card present but no application id reported; cannot activate session"
  else
    log "activating primary-gw provisioning session (slot $SLOT, aid ${#AID} hex chars)"
    timeout 30 $Q --uim-change-provisioning-session="activate=yes,session-type=primary-gw-provisioning,slot=$SLOT,aid=$AID" 2>&1 | sed -e 's/[0-9A-Fa-f]\{24,\}/<aid>/g' | head -3
    C=$(timeout 20 $Q --uim-get-card-status 2>/dev/null)
  fi
fi

# ---- 2. PIN1 verification, guarded ----
if [ ! -r "$PINFILE" ]; then
  log "no $PINFILE; skipping PIN auto-verify (a PIN-locked card will stay offline)"
elif ! echo "$C" | grep -q "PIN1 state: 'enabled-not-verified'"; then
  log "PIN1 not awaiting verification; skipping"
else
  RET=$(echo "$C" | sed -n "s/.*PIN1 retries: '\([0-9]*\)'.*/\1/p" | head -1)
  RET=${RET:-0}
  if [ "$RET" -lt 3 ]; then
    log "REFUSING to auto-verify: PIN1 retries=$RET (need 3, PUK guard). Unlock by hand once."
  else
    PIN=$(head -n1 "$PINFILE" | tr -d ' \t\r\n')
    if [ -z "$PIN" ]; then
      log "$PINFILE is empty; skipping"
    else
      log "verifying PIN1 (retries=$RET, one attempt only)"
      OUT=$(timeout 30 $Q --uim-verify-pin="PIN1,$PIN,session-type=primary-gw-provisioning" 2>&1)
      echo "$OUT" | sed -e "s/$PIN/<pin>/g" | head -3
      PIN=""; unset PIN
    fi
  fi
fi

# ---- 3. report (no sensitive values) ----
C=$(timeout 20 $Q --uim-get-card-status 2>/dev/null)
echo "$C" | grep -E "Card state:|Application state:|PIN1 state:|Primary GW:" | head -6 | sed -E 's/[0-9]{9,}/<masked>/g'
CAP=$(timeout 20 $Q --dms-get-capabilities 2>/dev/null | grep -E "Networks")
log "capabilities: ${CAP:-<none>}"
exit 0
