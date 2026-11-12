#!/bin/sh
# Kebab SDX55 "SIM day" pre-flight: check everything that must be true BEFORE
# you insert the card and reboot. Read-only; changes nothing.
#
# Run it any time. A FAIL means a prerequisite is missing; a WARN is something
# to know about before you reboot.
set -u
export LC_ALL=C LANG=C   # every parser below matches English tool output
PASS=0; FAIL=0; WARN=0
ok()   { printf "PASS  %s\n" "$1"; PASS=$((PASS + 1)); }
bad()  { printf "FAIL  %s\n" "$1"; FAIL=$((FAIL + 1)); }
warn() { printf "WARN  %s\n" "$1"; WARN=$((WARN + 1)); }

echo "== kebab SDX55 SIM-day pre-flight =="
echo

# 1. boot chain
if systemctl is-enabled --quiet kebab-sdx55-modem.service; then ok "kebab-sdx55-modem.service enabled at boot"; else bad "kebab-sdx55-modem.service NOT enabled (modem will not come up after reboot)"; fi
for u in pm-service-native tqftpserv-sdx55 mhi-efs-sync; do
  if [ -f "/etc/systemd/system/$u.service" ]; then ok "$u.service present"; else bad "$u.service missing (bring-up needs it)"; fi
done
if [ -f /etc/default/kebab-sdx55 ] && grep -q "KEBAB_SDX55_ONLINE=auto" /etc/default/kebab-sdx55; then
  ok "KEBAB_SDX55_ONLINE=auto (modem goes online even before registration)"
else
  warn "KEBAB_SDX55_ONLINE is not 'auto' - GNSS/LOC stays dead without online"
fi

# 2. UIM provisioning helper
if [ -x /usr/local/lib/kebab-modem/sim-provision.sh ]; then ok "sim-provision.sh installed"; else bad "sim-provision.sh missing"; fi
if [ -e /etc/kebab-sim-pin ]; then
  if [ "$(stat -c %a /etc/kebab-sim-pin)" = 600 ]; then warn "PIN file present (0600): PIN1 will be verified once if retries==3"; else bad "/etc/kebab-sim-pin exists but mode is $(stat -c %a /etc/kebab-sim-pin), must be 600"; fi
else
  ok "no PIN file (card must be PIN-free, or create /etc/kebab-sim-pin 0600 before boot)"
fi

# 3. data path tooling
for f in /usr/local/lib/kebab-modem/kebab-qmi-wds.py /usr/local/lib/kebab-modem/selftest.sh /usr/local/sbin/kebab-modem-status; do
  if [ -e "$f" ]; then ok "$(basename "$f") installed"; else bad "$(basename "$f") missing"; fi
done
# 3b. the tools the runbook actually types on SIM day (docs drifted before: see
#     MM doc 4.31 - a page can reference a tool that was never installed)
for f in /usr/local/sbin/kebab-sim-day \
         /usr/local/sbin/kebab-modem-mhi-diag \
         /usr/local/sbin/kebab-modem-qmap \
         /usr/local/lib/kebab-modem/tx-probe.sh \
         /usr/local/lib/kebab-modem/dns-probe.py \
         /usr/local/lib/kebab-modem/tcp-probe.py \
         /usr/local/lib/kebab-modem/bearer-rehearse.sh \
         /usr/local/lib/kebab-modem/runbook-lint.py \
         /usr/local/lib/kebab-modem/test-sim-day.sh; do
  if [ -e "$f" ]; then ok "$(basename "$f") present"; else bad "$(basename "$f") missing (the runbook calls it)"; fi
done

# 3c. QMAP fallback readiness: the module and the tun pre-check must be there
#     before the card arrives, because arming QMAP is gated on both
[ -e /usr/local/lib/kebab-modem/rmnet/rmnet.ko ] && ok "rmnet.ko staged (QMAP fallback available)" \
  || warn "rmnet.ko missing: raw-IP only, no QMAP fallback if the bearer wedges"
[ -e /usr/local/lib/kebab-modem/qmap-local-test.py ] && ok "qmap-local-test.py present (QMAP pre-check)" \
  || warn "qmap-local-test.py missing: --qmap would refuse to arm"

# 3d. the patched ModemManager (only matters if MM is enabled, but cheap)
[ -x /usr/local/lib/kebab-mm/ModemManager-patched ] && ok "patched ModemManager binary present" \
  || warn "patched ModemManager missing (packaged MM cannot do mhi_net bearers)"

if systemctl is-enabled --quiet kebab-sdx55-data.service 2>/dev/null; then
  warn "kebab-sdx55-data.service is enabled; it will fight a manual 'data-up' (systemctl disable it)"
else
  ok "data.service not enabled (manual 'data-up --profile 3' is the plan)"
fi

# 4. offline ModemManager packages
D=/srv/modem-20261112/mm-debs
want_modem=d144b8fb9d5abad7a43b967d3db57f4f24dcc980c4457b9bd0eeddcfab86b12c
if [ -d "$D" ]; then
  n=$(ls -1 "$D"/*.deb 2>/dev/null | wc -l)
  [ "$n" -ge 4 ] && ok "ModemManager debs staged ($n files in $D)" || warn "only $n debs in $D (need 4)"
  if [ -f "$D/modemmanager_1.24.0-1+deb13u1_arm64.deb" ]; then
    got=$(sha256sum "$D/modemmanager_1.24.0-1+deb13u1_arm64.deb" | cut -d' ' -f1)
    [ "$got" = "$want_modem" ] && ok "modemmanager .deb checksum matches" || bad "modemmanager .deb checksum mismatch"
  else
    warn "modemmanager .deb not found by name"
  fi
else
  warn "$D missing (ModemManager is optional, data does not need it)"
fi

# 5. state that forces a decision
NODE=$(qrtr-lookup 2>/dev/null | awk '$1==16 && $4>1 {print $4; exit}')
if [ -n "$NODE" ]; then
  SIM=$(qmicli -d qrtr://"$NODE" --uim-get-slot-status 2>/dev/null | grep -c "Card status: present")
  [ -n "$SIM" ] || SIM=0
else
  SIM=0
fi
if [ "$SIM" -gt 0 ]; then
  warn "a card is ALREADY visible in this boot: if it was inserted after power-on, reboot now (MCFG cannot be re-triggered)"
else
  ok "no card visible (expected before SIM day)"
fi

# 6. SIM-day card-wait flag
if [ -e /etc/kebab-modem-sim-expected ]; then
  ok "SIM-day wait flag present (sim-provision waits up to 12 s for a slow card)"
else
  echo "INFO  no /etc/kebab-modem-sim-expected: on SIM day run 'sudo touch' on it before booting,"
  echo "      otherwise a slow-initialising card can miss MCFG's 15 s/24 s window"
fi

echo
echo "RESULT: $PASS passed, $WARN warnings, $FAIL failed"
echo "Next: sudo touch /etc/kebab-modem-sim-expected; insert the card; reboot; run kebab-modem-selftest.sh"
[ "$FAIL" -eq 0 ] || exit 1
