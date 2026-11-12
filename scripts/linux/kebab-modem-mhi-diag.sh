#!/bin/sh
# MHI ring + raw-IP data-plane diagnostic for the kebab SDX55.
#
# Answers the one question that is hard to answer from `ip -s link` alone when
# a bearer "looks up but no traffic flows": are the MHI transfer rings still
# moving?  A wedged link shows TX growing, RX flat, and IP_HW0 rp/wp frozen.
#
# Read-only. Needs root only for /sys/kernel/debug/mhi (debugfs mode 0700).
set -u
export LC_ALL=C LANG=C   # every parser below matches English tool output
IF=${KEBAB_DATA_IF:-mhi_hwip0}
DBG=${KEBAB_MHI_DBG:-/sys/kernel/debug/mhi/mhi1}
SAMPLES=2
INTERVAL=2
JSON=0

usage() {
  cat <<EOF
usage: kebab-modem-mhi-diag [--samples N] [--interval SEC] [--json]

Samples the MHI IP_HW0 transfer rings and the raw-IP interface counters, then
prints the delta + a verdict:
  traffic-flowing         RX bytes or the DL ring moved
  tx-only-suspected-wedge TX moved but RX and the DL ring did not
  idle                    nothing moved
  bearer-down             the interface is not up (no address yet, etc.)

env: KEBAB_DATA_IF (default $IF), KEBAB_MHI_DBG (default $DBG)
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --samples)  SAMPLES=${2:-2}; shift 2 ;;
    --interval) INTERVAL=${2:-2}; shift 2 ;;
    --json)     JSON=1; shift ;;
    -h|--help)  usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[ -r "$DBG/channels" ] || { echo "FAIL: $DBG/channels not readable (need root / debugfs mounted)" >&2; exit 1; }

# chan <channel-number> -> "rp wp state" for IP_HW0(<n>)
chan() {
  awk -v want="IP_HW0(""$1"") " 'index($0, want) == 1 {
    rp=""; wp=""; st=""; prev=""
    for (i = 1; i <= NF; i++) {
      if ($i == "rp:" && prev != "local") rp = $(i+1)
      if ($i == "wp:" && prev != "local") wp = $(i+1)
      if ($i == "state:") st = $(i+1)
      prev = $i
    }
    print rp, wp, st
    exit
  }' "$DBG/channels"
}

counter() { cat "/sys/class/net/$IF/statistics/$1" 2>/dev/null || echo 0; }
link_state() {
  if [ ! -e "/sys/class/net/$IF" ]; then echo gone; return; fi
  cat "/sys/class/net/$IF/operstate" 2>/dev/null || echo unknown
}
pm_state() {
  sed -n 's/^PM state: \(.*\)/\1/p' "$DBG/states" 2>/dev/null | head -1
}

read_state() {
  set -- $(chan 100)      # UL ring (host -> modem)
  UL_RP=$1; UL_WP=$2; UL_ST=$3
  set -- $(chan 101)      # DL ring (modem -> host)
  DL_RP=$1; DL_WP=$2; DL_ST=$3
  RX=$(counter rx_bytes)
  TX=$(counter tx_bytes)
  RX_PKT=$(counter rx_packets)
  TX_PKT=$(counter tx_packets)
  LINK=$(link_state)
  PM=$(pm_state)
}

read_state
FIRST_UL_RP=$UL_RP; FIRST_DL_RP=$DL_RP; FIRST_DL_WP=$DL_WP
FIRST_RX=$RX; FIRST_TX=$TX
i=1
while [ "$i" -lt "$SAMPLES" ]; do
  sleep "$INTERVAL"
  read_state
  i=$((i + 1))
done

# Ring occupancy: rp/wp move in 16-byte TRE units, the ring itself is 2048
# bytes (128 TREs).  With no bearer the modem never consumes, so every packet
# ever sent still sits there - which is also what "TX stalled" looks like on a
# live bearer.  Measured on the tx-probe: 7 packets -> 7 TREs -> +112 bytes.
RD_LEN=2048
UL_PEND=0
UL_RP_D=0
if [ -n "$UL_WP" ] && [ -n "$UL_RP" ]; then
  UL_PEND=$(( (UL_WP - UL_RP) / 16 ))
  [ "$UL_PEND" -lt 0 ] && UL_PEND=$(( (UL_WP - UL_RP + RD_LEN) / 16 ))
fi
[ -n "$FIRST_UL_RP" ] && [ -n "$UL_RP" ] && UL_RP_D=$(( (UL_RP - FIRST_UL_RP) / 16 ))

RX_D=$((RX - FIRST_RX))
TX_D=$((TX - FIRST_TX))
DL_MOVED=0
[ "$DL_RP" != "$FIRST_DL_RP" ] && DL_MOVED=1
[ "$DL_WP" != "$FIRST_DL_WP" ] && DL_MOVED=1
UL_MOVED=0
[ "$UL_RP" != "$FIRST_UL_RP" ] && UL_MOVED=1

if [ "$LINK" != "up" ]; then
  VERDICT=bearer-down
elif [ "$RX_D" -gt 0 ] || [ "$DL_MOVED" = 1 ]; then
  VERDICT=traffic-flowing
elif [ "$TX_D" -gt 0 ] || [ "$UL_MOVED" = 1 ]; then
  VERDICT=tx-only-suspected-wedge
else
  VERDICT=idle
fi

if [ "$JSON" = 1 ]; then
  printf '{"if":"%s","link":"%s","pm":"%s","rx_bytes":%s,"tx_bytes":%s,"rx_packets":%s,"tx_packets":%s,"rx_delta":%s,"tx_delta":%s,"ul_rp":"%s","ul_wp":"%s","ul_pending_tres":%s,"ul_rp_tres_delta":%s,"dl_rp":"%s","dl_wp":"%s","dl_rp_before":"%s","dl_moved":%s,"samples":%s,"interval":%s,"verdict":"%s"}\n' \
    "$IF" "$LINK" "$PM" "$RX" "$TX" "$RX_PKT" "$TX_PKT" "$RX_D" "$TX_D" \
    "$UL_RP" "$UL_WP" "$UL_PEND" "$UL_RP_D" "$DL_RP" "$DL_WP" "$FIRST_DL_RP" "$DL_MOVED" \
    "$SAMPLES" "$INTERVAL" "$VERDICT"
  exit 0
fi

printf '%-22s %s\n' interface "$IF"
printf '%-22s %s\n' link_state "$LINK"
printf '%-22s %s\n' pm_state "${PM:-unknown}"
printf '%-22s rx=%s (pkts %s)  tx=%s (pkts %s)\n' counters "$RX" "$RX_PKT" "$TX" "$TX_PKT"
printf '%-22s rx+%s  tx+%s over %ss\n' delta "$RX_D" "$TX_D" "$((INTERVAL * (SAMPLES - 1)))"
printf '%-22s state=%s rp=%s wp=%s\n' "ul_ring(100)" "$UL_ST" "$UL_RP" "$UL_WP"
printf '%-22s state=%s rp=%s wp=%s\n' "dl_ring(101)" "$DL_ST" "$DL_RP" "$DL_WP"
printf '%-22s dl_rp_moved=%s\n' ring_motion "$DL_MOVED"
printf '%-22s %s of 128 TREs (%.0f%%)  device_consumed=%s TREs\n' ul_pending "$UL_PEND" \
  "$(awk -v a="$UL_PEND" 'BEGIN{print a*100/128}')" "$UL_RP_D"
printf '%-22s %s\n' verdict "$VERDICT"

case "$VERDICT" in
  tx-only-suspected-wedge)
    echo
    echo "TX is growing but nothing is coming back. Check: 'ping -c3 <gw>' from the bearer's"
    echo "address, then re-run with --samples 5. If it stays wedged, restart the call (SIGTERM"
    echo "the data-up process, or systemctl restart kebab-sdx55-data) instead of rebooting."
    echo "If ul_pending is near 128 TREs the UL ring is full: the modem is not draining it." ;;
  bearer-down)
    echo
    echo "The interface has no link. A bearer only exists while a data-up process holds the call."
    echo "ul_pending > 0 here is normal: with no bearer nothing consumes the UL ring." ;;
esac
