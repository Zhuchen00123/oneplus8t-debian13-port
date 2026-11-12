#!/bin/sh
# Prove the AP half of the data path without a SIM.
#
# With no bearer the modem drops whatever we send, but the kernel still has to
# hand our packets to mhi_net and bump the interface counters.  If that half is
# broken, every later "no data" report is unreadable - so measure it once, now,
# and leave the interface exactly as it was found.
#
# What it does (only with --apply):
#   1. sample  ip -s link  counters and the IP_HW0(100) ring line
#   2. put a private /30 on the raw-IP interface and bring it up
#   3. ping the on-link address -- 100% loss is EXPECTED and is not a failure
#   4. sample again, then restore: link down, address removed
#
# usage: kebab-modem-tx-probe [--iface IF] [--count N] [--addr A/B] [--apply]
set -u

IFACE=mhi_hwip0
COUNT=5
CIDR=10.99.13.2/30
PEER=10.99.13.1
APPLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --iface) IFACE=$2; shift 2 ;;
    --count) COUNT=$2; shift 2 ;;
    --addr) CIDR=$2; PEER=${2%/*}; PEER=${PEER%.*}.1; shift 2 ;;
    --apply) APPLY=1; shift ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 2; }

counters() {
  ip -s link show dev "$IFACE" 2>/dev/null | awk '
    /RX:/ {getline; rx_b=$1; rx_p=$2}
    /TX:/ {getline; tx_b=$1; tx_p=$2}
    END {printf "%s %s %s %s", rx_b, rx_p, tx_b, tx_p}'
}

ring() {
  grep -h "IP_HW0(100)" /sys/kernel/debug/mhi/mhi1/channels 2>/dev/null | head -1
}

echo "iface : $IFACE"
echo "before: $(counters)"
BEFORE=$(counters)
BRING=$(ring)
echo "ring  : ${BRING:-<debugfs unavailable>}"

if [ "$APPLY" = 0 ]; then
  echo "dry-run: would add $CIDR, bring $IFACE up, ping $COUNT x $PEER, then restore"
  exit 0
fi

MTU=$(ip link show dev "$IFACE" | sed -n 's/.*mtu \([0-9]*\).*/\1/p')
restore() {
  ip link set "$IFACE" down 2>/dev/null
  ip addr del "$CIDR" dev "$IFACE" 2>/dev/null
  [ -n "${MTU:-}" ] && ip link set "$IFACE" mtu "$MTU" 2>/dev/null
  echo "restored: $(counters)"
  echo "addr    : $(ip -4 -o addr show dev "$IFACE" | awk '{print $4}' | head -1 || true)"
  echo "link    : $(ip link show dev "$IFACE" | sed -n 's/.*state \([A-Z]*\).*/\1/p')"
  echo "mtu     : $(ip link show dev "$IFACE" | sed -n 's/.*mtu \([0-9]*\).*/\1/p')"
}
trap 'restore; exit 130' INT TERM

ip addr replace "$CIDR" dev "$IFACE" || { echo "FAIL: cannot add address"; trap - INT TERM; restore; exit 3; }
ip link set "$IFACE" up || { echo "FAIL: cannot bring link up"; trap - INT TERM; restore; exit 3; }
echo "up    : $(ip -4 -o addr show dev "$IFACE" | awk '{print $4}')"

# A raw-IP bearer has no ARP: the packets go straight at the interface, which
# is the point.  Loss is expected; what matters is that TX moved.
ping -c "$COUNT" -W 1 -I "$IFACE" "$PEER" >/dev/null 2>&1
echo "ping  : rc=$? (loss expected: no bearer)"
sleep 1
AFTER=$(counters)
echo "after : $AFTER"
echo "ring  : $(ring)"

set -- $BEFORE; b_rx_b=$1; b_rx_p=$2; b_tx_b=$3; b_tx_p=$4
set -- $AFTER;  a_rx_b=$1; a_rx_p=$2; a_tx_b=$3; a_tx_p=$4
d_tx_p=$((a_tx_p - b_tx_p))
d_tx_b=$((a_tx_b - b_tx_b))
echo "delta : tx_packets=$d_tx_p tx_bytes=$d_tx_b"

trap - INT TERM
restore

if [ "$d_tx_p" -ge "$COUNT" ]; then
  echo "VERDICT: tx-path-ok  ($d_tx_p packets handed to mhi_net)"
  exit 0
fi
echo "VERDICT: tx-path-suspect  (only $d_tx_p of $COUNT packets counted)"
exit 1
