#!/bin/bash
# Rehearsal for the "bearer works" path of kebab-sim-day.
#
# The success path (address -> ping -> DNS -> TCP -> PMTU -> PASS) has never
# run against a real bearer, and it must not be tried for the first time on
# SIM day.  This runs the EXACT probe block from sim-day.sh against an
# interface that is not the modem (wlan0 by default), so the code path is
# exercised with real packets.  It never touches mhi_hwip0 or the modem.
#
# Deliberately NOT part of sim-day: a rehearsal must never be able to write
# `verdict: PASS` into the modem evidence archive.
#
# usage: kebab-bearer-rehearse.sh [iface]   (default wlan0)
# exit: 0 all probes answered, 1 some probe failed, 2 usage/iface error
set -u
export LC_ALL=C LANG=C

LIB=${LIB:-/usr/local/lib/kebab-modem}
IFACE=${1:-wlan0}

[ -e "/sys/class/net/$IFACE" ] || { echo "no such interface: $IFACE" >&2; exit 2; }
SIMDAY_LIB_ONLY=1 . "$LIB/sim-day.sh" || exit 2

OUT=$(mktemp -d)
trap 'rm -rf "$OUT"' EXIT

IP=$(ip -4 -o addr show dev "$IFACE" scope global 2>/dev/null | awk '{print $4}' | head -1)
IP6=$(ip -6 -o addr show dev "$IFACE" scope global 2>/dev/null | awk '{print $4}' | head -1)
GW=""
DNS_SRV=""
DNS_OK=no
TCP_OK=no
TCP_TARGET=""
PMTU=unknown

echo "iface    : $IFACE"
echo "addr     : ${IP:-none} / ${IP6:-none}"
[ -n "$IP$IP6" ] || { echo "interface has no global address; nothing to rehearse" >&2; exit 2; }

probe_bearer

PING_OK=no
grep -q " 0% packet loss" "$OUT/probes.txt" 2>/dev/null && PING_OK=yes

echo "ping     : $PING_OK"
echo "dns      : $DNS_OK via ${DNS_SRV:-none}"
echo "tcp      : $TCP_OK ${TCP_TARGET:-none}"
echo "pmtu     : $PMTU"
echo "probes   : $(wc -l <"$OUT/probes.txt") lines"

if bearer_ok "$PING_OK" "$DNS_OK" "$TCP_OK"; then
  echo "result   : REHEARSAL PASS (this is $IFACE, not the modem)"
  exit 0
fi
echo "result   : REHEARSAL FAIL (no proof on $IFACE)"
exit 1
