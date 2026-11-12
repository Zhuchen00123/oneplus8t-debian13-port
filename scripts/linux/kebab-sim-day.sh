#!/bin/sh
# SIM-day evidence capture for the kebab SDX55 data plane.
#
# One command does what the runbook spells out, and leaves a self-contained
# evidence directory behind:
#   preflight -> the read-only checks
#   status    -> kebab-modem-status before/after, health JSON, QRTR table
#   data-up   -> held for --hold seconds, log captured
#   probes    -> ping via the bearer, MHI ring diagnostic, link counters
#   summary   -> PASS/FAIL with the facts that decided it
#
# --qmap additionally runs the QMAP path the runbook describes: WDA
# Set Data Format (qmap/qmap) -> rmnet0 over the physical netdev with a mux id
# -> data-up on that link.  Everything is restored on exit (format back to
# raw-ip/disabled, link removed, module unloaded), including on SIGINT/SIGTERM.
#
# It never touches the default route or DNS.  Safe to run with no SIM: it
# records the start failure and exits non-zero instead of pretending.
#
# # A card can take 12-20 s to be reported by UIM, so when the first snapshot has
# # no card it waits --wait-sim seconds (default 20 if /etc/kebab-modem-sim-expected
# # exists, else 0) before concluding that the slot is empty.
# # It waits up to --wait-reg seconds (default 60) for NAS registration before
# starting the call, because a start on a searching modem fails with the same
# error 14 as a real APN problem.
#
# usage: kebab-sim-day [--profile N | --apn APN] [--hold SEC] [--out DIR]
#                      [--iface IF] [--wait-reg SEC] [--wait-sim SEC] [--sample-every SEC]
#                      [--fake-bearer]
#                      [--dry-run] [--qmap [--mux N]]
set -u
export LC_ALL=C LANG=C   # every parser below matches English tool output

# derive_next() turns the evidence into the next concrete action.  It is pure:
# no I/O, no state, so it can be exercised without a SIM card:
#   SIMDAY_LIB_ONLY=1 . sim-day.sh; derive_next 0 "" "" "" "" mhi_hwip0 tool
derive_next() {
  _sim=$1; _ip=$2; _rc=$3; _end=$4; _diag=$5; _if=$6; _tool=$7; _alive=${8:-}; _serv=${9:-}; _lost=${10:-}
  if [ "$_alive" = no ]; then
    printf '%s' "modem 已死（PCIe 链路训练失败，mhi_hwip0 不在）：只能 reboot。host 侧 PCIe remove/rescan 与控制器 unbind 都救不回（实测 2026-11-13），别在那里耗时间。"
  elif [ "$_sim" = 0 ]; then
    printf '%s' "sim=absent: 卡必须在开机前插好（MCFG 只在 mission mode 后约 15 s/24 s 各跑一次，之后无法重触发）。插卡后重启，开机前先 sudo touch /etc/kebab-modem-sim-expected。"
  elif [ -n "$_ip" ] && [ "${_lost:-no}" = yes ]; then
    printf '%s' "承载起来时是通的（probes_at_start），按住期间坏掉了 —— 这是「先通后 wedge」，不是 APN/路由问题：先看 samples.tsv 与 wedge_dump（\$out/），再按 MM 文档 4.15 切 QMAP。别去改 APN。"
  elif [ -z "$_ip" ] && ! registration_ok "$_serv"; then
    printf '%s' "卡在位但还没注册（serving=${_serv:-unknown}）：这个状态下 WDS Start 必然 error ${_rc:-?}，先等注册再重跑本脚本（qmicli -d qrtr://3 --nas-get-serving-system 看到 registered 为止），不要先去改 APN 或 attach-set——那会把「没注册」误诊成「APN 不对」。"
  elif [ -z "$_ip" ]; then
    case "$_rc" in
      14) printf '%s' "start error 14 且卡在位：先看 call-end-reason (${_end:-none})；若是 no-pdp-context / acl-failure 一类，试 sudo python3 $_tool attach-set --list 3 --apply（附着改走 CMNET profile 3），再 sudo systemctl restart kebab-sdx55-modem 后重跑本脚本。" ;;
      *)  printf '%s' "start error ${_rc:-unknown} 且卡在位：按 MM 文档 4.4 的 call-end 表分流；先留基线 python3 $_tool params / subscription。" ;;
    esac
  elif [ "$_diag" = tx-only-suspected-wedge ]; then
    printf '%s' "bearer 在但 wedge（UL 环不再被设备取）：切 QMAP —— sudo python3 $_tool format-set --ul qmap --dl qmap --apply；sudo kebab-modem-qmap load；sudo kebab-modem-qmap up --mux 1；再 python3 $_tool data-up --mux 1 --ifname rmnet0。"
  elif [ "$_diag" = traffic-flowing ]; then
    printf '%s' "bearer 在、环也在动但 ping 不通：查路由与 ICMP —— ip -4 route show dev $_if；ip -s link show $_if；确认没有别的默认路由抢占。"
  else
    printf '%s' "bearer 在但 ping 不通（diag=${_diag:-n/a}）：先 ip -4 route show dev $_if 看网关/路由，再 ip -s link show $_if 看 RX 是否在涨。"
  fi
}

# qmap_gate() decides whether it is safe to put the modem into QMAP mode.
# Pure (no I/O) so it is unit-testable without a modem:
#   qmap_gate <sim_present 0|1> <nas serving state> <precheck pass|fail|not-run>
# Prints nothing when arming is allowed, otherwise the reason to skip.
# Rationale: a card-less attempt with qmap format + a WDS start that never
# completed killed the firmware (MM doc 4.15); an unregistered modem can hit
# the same failed-start path, so registration is required too.
qmap_gate() {
  _sim=$1; _serv=$2; _st=$3
  if [ "${_st:-not-run}" = fail ]; then
    printf '%s' "kernel QMAP precheck failed (see qmap-selftest.json); the modem was not touched"
  elif [ "${_sim:-0}" != 1 ]; then
    printf '%s' "no SIM card: QMAP arming needs a registered modem (a card-less attempt killed the firmware once, MM doc 4.15)"
  else
    case "${_serv:-}" in
      registered*) : ;;
      *) printf '%s' "no registration yet (serving=${_serv:-unknown}): QMAP arming needs a registered modem (a failed start in qmap format once killed the firmware, MM doc 4.15)" ;;
    esac
  fi
}

# qmap_armed_iface() reads back the interface name qmap.sh created.
# qmap.sh must not reuse rmnet0 when that name already belongs to somebody
# else (a ModemManager bearer looks exactly the same in `ip link`), so it
# picks a free rmnetN and reports "link up: <name> over <parent> ..."; the name
# cannot be assumed.  Pure enough to unit-test on a log snippet.
qmap_armed_iface() {
	sed -n "s/^link up: \([^ ]*\) .*/\1/p" "${1:-/dev/null}" 2>/dev/null | tail -1 || true
}

# registration_ok() is pure: the NAS serving states that are attached far
# enough to ask for a PDN.  A WDS Start issued while the modem still searches
# fails with error 14 no-service and looks exactly like a data problem.
registration_ok() {
  case "${1:-}" in
    registered*) return 0 ;;
    *) return 1 ;;
  esac
}

# serving_now() one read-only NAS query.  Empty on any failure (modem dead,
# no QRTR node), which the caller treats as "not registered".
serving_now() {
  qmicli -d qrtr://3 --nas-get-serving-system 2>/dev/null \
    | sed -n "s/.*Registration state: '\\([^']*\\)'.*/\\1/p" | head -1
}

# wait_registration() polls NAS until registered or the deadline expires.
# Prints the last state seen; returns 0 only when registered.  Bails out early
# when the modem itself is gone (a dead PCIe link would otherwise burn the
# whole timeout).
wait_registration() {
  _deadline=$(( $(date +%s) + ${1:-60} ))
  _last=""
  while [ "$(date +%s)" -lt "$_deadline" ]; do
    if [ ! -e /sys/class/net/mhi_hwip0 ]; then
      printf '%s' "${_last:-modem-gone}"
      return 1
    fi
    _last=$(serving_now)
    registration_ok "$_last" && { printf '%s' "$_last"; return 0; }
    sleep 3
  done
  printf '%s' "${_last:-unknown}"
  return 1
}

# sim_slots_from_status() is pure: read the "sim_slots_present N" line that
# kebab-modem-status prints, or 0 when it is missing.  A card can take 12-20 s
# to be reported by UIM after boot (community kebab-uim-provision waits up to
# 120 s), so a single snapshot is not evidence that the slot is empty.
sim_slots_from_status() {
  _n=$(printf '%s\n' "${1:-}" | sed -n 's/^sim_slots_present *\([0-9]*\).*/\1/p' | head -1)
  printf '%s' "${_n:-0}"
}

# sim_now() one read-only UIM query, same semantics as the status line.
sim_now() {
  qmicli -d qrtr://3 --uim-get-slot-status 2>/dev/null \
    | grep -c 'Card status: present' || true
}

# wait_sim_default() is pure: only wait when the operator announced a card
# (/etc/kebab-modem-sim-expected, the same flag sim-provision.sh honours).
# A card-less run must stay fast, otherwise every offline test pays 20 s.
wait_sim_default() {
  if [ "${1:-0}" = 1 ]; then printf '20'; else printf '0'; fi
}

# wait_sim() polls UIM until a slot reports present or the deadline passes.
# Prints the last slot count; returns 0 when a card is present.  Bails out
# immediately when the modem itself is gone.
wait_sim() {
  _deadline=$(( $(date +%s) + ${1:-0} ))
  _last=0
  while :; do
    if [ ! -e /sys/class/net/mhi_hwip0 ]; then
      printf '%s' "$_last"
      return 1
    fi
    _last=$(sim_now)
    [ "${_last:-0}" -gt 0 ] 2>/dev/null && { printf '%s' "$_last"; return 0; }
    [ "$(date +%s)" -ge "$_deadline" ] && break
    sleep 2
  done
  printf '%s' "${_last:-0}"
  return 1
}

# bearer_ok() is pure: ICMP is the cheapest probe and the first thing an
# operator may filter, so a DNS answer through the bearer counts as proof of
# real IP traffic too.
# bearer_ok(ping, dns, tcp): any one proof is enough.  ICMP gets filtered by
# operators, a resolver can be unreachable while the PDN is fine, and a
# completed TCP handshake is the strongest of the three because it is
# stateful - it needs the operator session, not just one datagram.
# bearer_lost() is the one distinction a single end-of-hold probe cannot make:
# a bearer that carried traffic at t=0 and does not at the end is NOT the same
# failure as one that never worked, and the operator must be sent down a
# different path (QMAP/wedge) than the APN/route one.
#   bearer_lost <start_ok yes|no> <end_ok yes|no>  -> prints yes|no
bearer_lost() {
  if [ "${1:-no}" = yes ] && [ "${2:-no}" != yes ]; then printf '%s' yes; else printf '%s' no; fi
}

# default_routes_from() compacts `ip route show default` into "dev/metric" pairs.
# The evidence pack has to show that the bearer did not take over the server's
# uplink: a modem default route at metric 0 ties with it and wins by FIB order.
default_routes_from() {
  printf '%s\n' "${1:-}" | awk '{dev=""; met="0"; for (i=1;i<=NF;i++) { if ($i=="dev") dev=$(i+1); else if ($i=="metric") met=$(i+1) } if (dev!="") printf "%s/%s ", dev, met}' | sed 's/ *$//'
}

# bearer_default_route: how many default routes point at our bearer interface.
# 0 is what a healthy run looks like (sim-day never installs one).
bearer_default_route() {
  printf '%s\n' "${1:-}" | grep -c "dev ${2:-mhi_hwip0}" 2>/dev/null || true
}

bearer_ok() {
  [ "${1:-}" = yes ] && return 0
  [ "${2:-}" = yes ] && return 0
  [ "${3:-}" = yes ] && return 0
  return 1
}

# sample_loop() writes one TSV row per interval while the bearer is held.
# A single snapshot at the end cannot tell "never carried traffic" from
# "carried traffic, then wedged" - and the wedge is the failure this rig is
# meant to catch (MM doc 4.7/4.15).  Stopped by touching $3.stop.
sample_loop() {
  _out=$1; _every=$2; _stop=$3; _budget=${4:-3600}
  _until=$(( $(date +%s) + _budget ))
  printf 'epoch\tverdict\trx_bytes\ttx_bytes\tul_pending\n' >"$_out"
  while [ ! -e "$_stop" ]; do
    # sample first, then sleep: a short hold still gets a t=0 reading to
    # compare against the last one
    _j=$("$LIB/mhi-diag.sh" --samples 1 --interval 0 --json 2>/dev/null || true)
    if [ -z "$_j" ]; then _j='{}'; _v=error; else
      _v=$(printf '%s' "$_j" | sed -n 's/.*"verdict":"\([^"]*\)".*/\1/p')
      _v=${_v:-error}
    fi
    _rx=$(printf '%s' "$_j" | sed -n 's/.*"rx_bytes":\([0-9]*\).*/\1/p')
    _tx=$(printf '%s' "$_j" | sed -n 's/.*"tx_bytes":\([0-9]*\).*/\1/p')
    _up=$(printf '%s' "$_j" | sed -n 's/.*"ul_pending_tres":\([0-9]*\).*/\1/p')
    printf '%s\t%s\t%s\t%s\t%s\n' "$(date +%s)" "${_v:-error}" "${_rx:-0}" "${_tx:-0}" "${_up:-0}" >>"$_out"
    # the modem itself vanishing is a different failure from a wedge: freeze it
    # too, once, and stamp the epoch so the summary can say when it happened
    if [ ! -e "${KEBAB_SYSFS_NET:-/sys/class/net}/${IFACE:-mhi_hwip0}" ] && [ ! -e "${_out%/*}/.modem-gone" ]; then
      date +%s >"${_out%/*}/.modem-gone" 2>/dev/null || true
      wedge_dump "${_out%/*}" modemdied >/dev/null 2>&1 || true
    fi
    # first wedge seen: freeze the modem state now, not at the end of the run
    if [ "$_v" = "tx-only-suspected-wedge" ] && [ ! -e "${_out%/*}/.wedge" ]; then
      : >"${_out%/*}/.wedge"
      wedge_dump "${_out%/*}" wedge >"${_out%/*}/.wedge-path" 2>/dev/null || true
    fi
    [ "$(date +%s)" -ge "$_until" ] && break
    sleep "$_every"
  done
}

# sample_trend() is a pure summariser over the TSV above: it answers "did the
# verdict ever change" and "how fast did bytes move", which are the two things
# a wedge report has to state.
sample_trend() {
  _f=${1:-}
  [ -s "$_f" ] || { printf 'no samples'; return 0; }
  awk -F'\t' '
    NR==1 { next }
    {
      n++
      if (first=="") { first=$2; e0=$1; r0=$3; t0=$4 }
      if (prev!="" && $2!=prev) ch++
      prev=$2; last=$2; e1=$1; r1=$3; t1=$4
    }
    END {
      if (n==0) { printf "0 samples"; exit }
      d=e1-e0; if (d<1) d=1
      printf "%d samples, %s -> %s (%d change(s)), rx %.0f B/s tx %.0f B/s", n, first, last, ch, (r1-r0)/d, (t1-t0)/d
    }' "$_f"
}

# NAS parsers read a qmicli dump on stdin.  A "did data fail because there is no
# service" question cannot be answered from the WDS error alone: the RAT and the
# signal are what separate "not on LTE yet" from "APN/bearer problem".
rat_from() {
  sed -n "s/^[[:space:]]*\[[0-9]*\]: '\([^']*\)'.*/\1/p" | head -1
}

plmn_from() {
  awk '/Current PLMN:/{f=1; next}
       f && /MCC:/{l=$0; gsub(/[^0-9]/, "", l); m=l}
       f && /MNC:/{l=$0; gsub(/[^0-9]/, "", l); printf "%s/%s\n", m, l; exit}'
}

nas_state_from() {
  sed -n "s/^[[:space:]]*Status: '\([^']*\)'.*/\1/p" | head -1
}

sig_from() {
  sed -n "s/^[[:space:]]*\(RSSI\|RSRP\|RSRQ\|SINR\): '\([^']*\)'.*/\1=\2/p" | tr '\n' ' ' | sed 's/ *$//'
}

# wedge_dump() freezes the modem state at the moment the wedge is first seen.
# The failure we care about is "carried traffic, then stopped"; by the time a
# run ends the rings may have moved again or the modem may have reset itself,
# so the only useful snapshot is the first one.  Written next to the samples.
wedge_dump() {
  _dir=${1:-.}; _tag=${2:-wedge}
  _file="$_dir/${_tag}-$(date -u +%Y%m%dT%H%M%SZ).txt"
  {
    echo "== when =="
    date -u
    echo "== mhi states =="
    cat /sys/kernel/debug/mhi/mhi1/states 2>&1
    echo "== channels (data path) =="
    grep -E "IP_HW0|IP_SW0|DIAG" /sys/kernel/debug/mhi/mhi1/channels 2>&1
    echo "== counters (same frame) =="
    "${LIB:-/usr/local/lib/kebab-modem}/mhi-diag.sh" --samples 1 --interval 0 2>&1
    echo "== ip -s link =="
    ip -s link show mhi_hwip0 2>&1
    echo "== dmesg tail =="
    dmesg 2>&1 | tail -30
  } >"$_file" 2>&1
  printf "%s\n" "$_file"
}

# probe_bearer() is the whole probe section as one function: ping v4/v6, DNS,
# TCP and PMTU, all bound to $IFACE.  Extracted so the PASS path can be
# rehearsed against a non-modem interface (kebab-bearer-rehearse.sh) instead
# of being proven for the first time on SIM day.
# gw_candidate(default_gw, own_addr): the address to ping first.
#   - a real default gateway is a fine target;
#   - the interface address itself is NOT: ping -I <iface> <own addr> is
#     answered by the local stack, so it would report 0% loss with zero
#     packets ever leaving the box (seen on a synthetic bearer with no
#     default route, where the old fallback picked the interface address).
gw_candidate() {
  _dg=${1:-}; _own=${2:-}
  [ -n "$_dg" ] || return 0
  [ "$_dg" = "$_own" ] && return 0
  printf "%s" "$_dg"
}

# elapsed_since(first_epoch, later_epoch): "+37s" or "" when either is unknown.
# Used to say WHEN the modem vanished, which the samples alone cannot tell you
# (every row after the death looks like a plain bearer-down).
# first_epoch(samples.tsv): the first DATA row.  The file starts with a header
# ("epoch  verdict  ..."), so NR==1 is the word "epoch" and silently yields an
# empty timestamp - which then makes every "when did it happen" line blank.
first_epoch() {
  awk '$1 ~ /^[0-9]+$/ {print $1; exit}' "${1:-/dev/null}" 2>/dev/null
}

elapsed_since() {
  case "${1:-}" in ""|*[!0-9]*) return 0 ;; esac
  case "${2:-}" in ""|*[!0-9]*) return 0 ;; esac
  _d=$(( $2 - $1 ))
  [ "$_d" -lt 0 ] && _d=0
  printf "+%ss" "$_d"
}

# residue_cleanup(iface, orig_mtu): last line of defence.
# The normal path is that data-up removes what it configured.  If it had to be
# SIGKILLed (blocked in a QMI read, wedged), cleanup never ran and the address
# stays on the bearer - which then confuses the NEXT run and any bearer
# ModemManager tries to set up.  So after the stop we look and repair.
residue_cleanup() {
  _rif=${1:-}; _rmtu=${2:-}
  [ -n "$_rif" ] || return 0
  [ -e "/sys/class/net/$_rif" ] || return 0
  _msg=""
  _left=$(ip -4 -o addr show dev "$_rif" 2>/dev/null | awk '{print $4}')
  if [ -n "$_left" ]; then
    for _a in $_left; do ip addr del "$_a" dev "$_rif" 2>/dev/null || true; done
    ip link set "$_rif" down 2>/dev/null || true
    _msg="removed $(printf "%s" "$_left" | tr "\n" " " | sed "s/ *$//")"
  fi
  # The MTU is residue too: data-up sets the bearer MTU and a killed data-up
  # never puts it back, so an interface with no address can still be wrong.
  _cur=$(cat "/sys/class/net/$_rif/mtu" 2>/dev/null || echo "")
  if [ -n "$_rmtu" ] && [ -n "$_cur" ] && [ "$_cur" != "$_rmtu" ]; then
    ip link set "$_rif" mtu "$_rmtu" 2>/dev/null || true
    _msg="${_msg:+$_msg; }mtu $_cur -> $_rmtu"
  fi
  printf "%s" "$_msg"
}
probe_bearer() {
  _if=$IFACE; _out=$OUT
  _label=${1:-}
  if [ -z "$_label" ]; then
    : >"$_out/probes.txt"
  else
    printf '\n### probes %s ###\n' "$_label" >>"$_out/probes.txt"
  fi
  # Count ICMP successes around this probe so PING_OK describes THIS phase and
  # not "did anything ever work": a bearer that works at t=0 and is dead at the
  # end must not read as PASS.
  _ping_before=$(grep -c ' 0% packet loss' "$_out/probes.txt" 2>/dev/null || true)
  if [ -n "$IP$IP6" ]; then
    if [ -n "$IP" ]; then
      _own=$(ip -4 -o addr show dev "$_if" 2>/dev/null | awk '{print $4}' | sed 's#/[0-9]*##' | head -1)
      GW=$(gw_candidate "$(ip -4 route show dev "$_if" 2>/dev/null | sed -n 's/^default via \([0-9.]*\).*/\1/p' | head -1)" "$_own")
      [ -n "$GW" ] || { type log >/dev/null 2>&1 && log "no default route on $_if; only the public resolvers are pinged"; }
      for pair in "gw $GW" "dns 223.5.5.5" "dns 8.8.8.8"; do
        kind=${pair%% *}; host=${pair#* }
        [ -n "$host" ] || continue
        {
          echo "== ping $kind $host =="
          ping -c 3 -W 2 -I "$_if" "$host" 2>&1
          echo "rc=$?"
        } >>"$_out/probes.txt"
      done
    fi
    if [ -n "$IP6" ]; then
      GW6=$(ip -6 route show dev "$_if" 2>/dev/null | sed -n 's/^default via \([0-9a-fA-F:]*\).*/\1/p' | head -1)
      for pair in "gw6 $GW6" "dns6 2400:3200::1" "dns6 2001:4860:4860::8888"; do
        kind=${pair%% *}; host=${pair#* }
        [ -n "$host" ] || continue
        {
          echo "== ping $kind $host =="
          ping -6 -c 3 -W 2 -I "$_if" "$host" 2>&1
          echo "rc=$?"
        } >>"$_out/probes.txt"
      done
    fi
    # ICMP may be filtered by the operator (common on LTE).  A DNS answer is
    # stronger evidence that the bearer carries IP traffic, and it uses the
    # resolver the network handed us in WDS settings when we have one.
    # Bearer DNS first (it is what the network handed us), then a public one:
    # some carriers hand out a resolver that is only reachable after the PDN is
    # fully up, and a working public resolver is still proof that data flows.
    DNS_SRV=$(sed -n 's/^  dns1: //p' "$_out/dataup.log" 2>/dev/null | head -1)
    DNS_OK=no
    for _srv in ${DNS_SRV:-223.5.5.5} 223.5.5.5; do
      echo "== dns $_srv via $_if ==" >>"$_out/probes.txt"
      python3 "$LIB/dns-probe.py" --iface "$_if" --server "$_srv" \
        --name example.com --timeout 5 --json >>"$_out/dns.json" 2>>"$_out/probes.txt"
      _rc=$?
      echo "dns $_srv rc=$_rc" >>"$_out/probes.txt"
      DNS_SRV=$_srv
      if [ "$_rc" = 0 ]; then DNS_OK=yes; break; fi
    done

    # TCP handshake: ICMP and UDP DNS can both be answered by something in front
    # of the real network; a SYN/SYN-ACK round trip needs the operator session.
    TCP_OK=no
    TCP_TARGET=""
    for _tgt in "${DNS_SRV:-223.5.5.5}:443" "1.1.1.1:443" "223.5.5.5:443"; do
      _host=${_tgt%:*}
      _port=${_tgt##*:}
      echo "== tcp $_host:$_port ==" >>"$_out/probes.txt"
      python3 "$LIB/tcp-probe.py" --iface "$_if" --host "$_host" --port "$_port" \
        --timeout 6 --json >>"$_out/tcp.json" 2>>"$_out/probes.txt"
      _trc=$?
      echo "tcp $_host:$_port rc=$_trc" >>"$_out/probes.txt"
      if [ "$_trc" = 0 ]; then TCP_OK=yes; TCP_TARGET="$_host:$_port"; break; fi
    done
    # PMTU: DF-sized ping at the MTU we configured.  A bearer whose MTU is
    # advertised wrong bends here (big packets vanish, small ones work), which
    # is the classic "DNS resolves but pages hang" symptom.
    PMTU=unknown
    _mtu=$(cat "/sys/class/net/$_if/mtu" 2>/dev/null || echo 1500)
    if [ -n "$IP" ] && [ -n "${GW:-}" ]; then
      _size=$(( _mtu - 28 ))
      if ping -c 2 -W 3 -M do -s "$_size" -I "$_if" "$GW" >>"$_out/probes.txt" 2>&1 \
         && grep -q " 0% packet loss" "$_out/probes.txt"; then
        PMTU="ok ${_mtu}"
      else
        PMTU="failed ${_mtu} (df ping lost)"
      fi
    fi
  else
    echo "(no bearer address; probes skipped)" >>"$_out/probes.txt"
  fi
  _ping_after=$(grep -c ' 0% packet loss' "$_out/probes.txt" 2>/dev/null || true)
  if [ "${_ping_after:-0}" -gt "${_ping_before:-0}" ] 2>/dev/null; then PING_OK=yes; else PING_OK=no; fi
}
# Sourcing with SIMDAY_LIB_ONLY=1 defines derive_next()/qmap_gate() only; it runs nothing.
if [ "${SIMDAY_LIB_ONLY:-0}" = 1 ]; then
  return 0 2>/dev/null || exit 0
fi
PROFILE=3
APN=""
HOLD=120
WAIT_REG=60
WAIT_SIM=-1
SAMPLE_EVERY=15
FAKE=0
OUT=""
DRY=0
QMAP=0
MUX=1
IFACE=mhi_hwip0
LINK_PARENT=$IFACE        # the physical bearer; IFACE may later become an rmnet link
IFACE_GIVEN=0
QMAP_ARMED=0
QMAP_IF=""
QMAP_RESULT=not-requested
QMAP_SELFTEST=not-run
LIB=/usr/local/lib/kebab-modem
TOOL=$LIB/kebab-qmi-wds.py
STATUS=/usr/local/sbin/kebab-modem-status

usage() {
  head -18 "$0" | cut -c3-
}

while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE=$2; shift 2 ;;
    --apn)     APN=$2; shift 2 ;;
    --hold)    HOLD=$2; shift 2 ;;
    --out)     OUT=$2; shift 2 ;;
    --iface)   IFACE=$2; IFACE_GIVEN=1; shift 2 ;;
    --qmap)    QMAP=1; shift ;;
    --mux)     MUX=$2; shift 2 ;;
    --wait-reg) WAIT_REG=$2; shift 2 ;;
    --wait-sim) WAIT_SIM=$2; shift 2 ;;
    --sample-every) SAMPLE_EVERY=$2; shift 2 ;;
    --fake-bearer) FAKE=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

TS=$(date -u +%Y%m%dT%H%M%SZ)
[ -n "$OUT" ] || OUT=/srv/modem-$(date -u +%Y%m%d)/simday-$TS
mkdir -p "$OUT" || { echo "cannot create $OUT" >&2; exit 2; }
OUT=$(cd "$OUT" && pwd)

# Defined here, before anything that logs: three separate blocks (disk space,
# MTU baseline, the single-instance lock) all log early, and an early `log` call
# before this definition fails silently in a way that only shows up as a missing
# line in run.log (measured twice: rounds 51 and 52).
log() { printf '%s %s\n' "$(date -u +%H:%M:%SZ)" "$*" | tee -a "$OUT/run.log"; }

# A full disk silently truncates the evidence, and a truncated probes.txt reads
# exactly like "no data".  Say how much room there is before we start.
FREE_MB=$(df -Pm "$OUT" 2>/dev/null | awk 'NR==2 {print $4}')
case "${FREE_MB:-}" in ''|*[!0-9]*) FREE_MB=unknown ;; esac
if [ "$FREE_MB" = unknown ]; then
  log "disk: could not read free space for $OUT"
elif [ "$FREE_MB" -lt 20 ]; then
  log "disk: WARNING only ${FREE_MB} MB free under $OUT - the evidence pack may be truncated"
else
  log "disk: ${FREE_MB} MB free under $OUT"
fi


[ "$(id -u)" -eq 0 ] || { echo "must run as root: sudo $0 $*" >&2; exit 2; }
command -v python3 >/dev/null || { echo "python3 missing" >&2; exit 2; }

log "out: $OUT"

# Two concurrent runs fight over the same bearer interface (address, MTU, WDS
# call) and produce evidence neither of them can explain.  One instance at a
# time, and say who is already holding it.
LOCK=/run/kebab-sim-day.lock
if command -v flock >/dev/null 2>&1; then
  # >> not >: opening with > truncates the file, which would erase the running
  # instance's pid before we even learn that it holds the lock (measured).
  exec 9>>"$LOCK" 2>/dev/null || LOCK=""
  if [ -n "$LOCK" ]; then
    if flock -n 9; then
      LOCK_ACQUIRED=1
      : >"$LOCK"
      printf 'pid %s since %s\n' "$$" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$LOCK"
    else
      _held=$(tr -d '\n' <"$LOCK" 2>/dev/null)
      # log() tees to run.log and stdout; a bare pipeline to stderr was silent
      # in practice, and this message is the only thing the operator sees.
      log "refusing to start: another kebab-sim-day is already running (${_held:-pid unknown})"
      log "two runs would fight over mhi_hwip0 and the WDS call; if that pid is stale: sudo rm -f $LOCK"
      exit 3
    fi
  fi
else
  log "flock not available: running without a single-instance lock"
fi

# What the driver has before we touch anything: data-up sets the bearer MTU and
# a killed data-up never restores it, so the guard needs a target.
# The target is the DRIVER DEFAULT, not "whatever it happens to be now": if an
# earlier crashed run left the interface at 1500, adopting 1500 as the baseline
# preserves the wrong value forever (measured in round 52).  mhi_net raises the
# interface with MTU 0x4000 (16384); override with KEBAB_BEARER_MTU.
BASE_MTU=${KEBAB_BEARER_MTU:-16384}
ORIG_MTU=$(cat "/sys/class/net/$IFACE/mtu" 2>/dev/null || echo "$BASE_MTU")
if [ "$ORIG_MTU" != "$BASE_MTU" ]; then
  log "baseline: mtu=$ORIG_MTU but the driver default is $BASE_MTU - looks like residue from an earlier run; it will be restored to $BASE_MTU"
  ORIG_MTU=$BASE_MTU
fi
log "args: profile=$PROFILE apn=${APN:-<profile>} hold=$HOLD dry_run=$DRY iface=$IFACE qmap=$QMAP mux=$MUX"

# ---------- 1. preflight ----------
PF_RC=127
if [ -x "$LIB/sim-preflight.sh" ]; then
  "$LIB/sim-preflight.sh" >"$OUT/preflight.txt" 2>&1
  PF_RC=$?
  grep -E '^(RESULT|Next):' "$OUT/preflight.txt" | sed 's/^/  preflight: /' | tee -a "$OUT/run.log"
else
  log "preflight: missing $LIB/sim-preflight.sh"
fi

# ---------- 2. status before ----------
{
  "$STATUS" 2>&1
  echo "--- health ---"
  cat /run/kebab-sdx55-health.json 2>/dev/null || echo "(no health json)"
  echo "--- qrtr ---"
  qrtr-lookup 2>/dev/null | head -20
} >"$OUT/status-before.txt" 2>&1

SIM=0
grep -q 'sim_slots_present *[1-9]' "$OUT/status-before.txt" && SIM=1
REG=$(sed -n 's/^serving *//p' "$OUT/status-before.txt" | head -1)
log "sim_present=$SIM serving=${REG:-unknown}"

# The pack must be able to prove itself: seal it, read the manifest back, and
# record the result next to it (manifest-check.txt is then part of the final
# seal, so the check file is covered too).  A truncated probes.txt reads exactly
# like "no data", and this is what catches that.
seal_pack() {
  find "$OUT" -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum >"$OUT/SHA256SUMS"
  if ( cd "$OUT" && sha256sum -c --quiet SHA256SUMS >/dev/null 2>&1 ); then _m=ok; else _m=MISMATCH; fi
  { printf 'manifest check: %s (%s files sealed before this note)\n' "$_m" "$(wc -l <"$OUT/SHA256SUMS" | tr -d ' ')"
    printf 'verified at: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } >"$OUT/manifest-check.txt"
  # Log BEFORE the final seal: run.log is part of the pack, so a log line
  # written after sealing makes the pack fail its own check (measured).
  log "manifest: $_m"
  find "$OUT" -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum >"$OUT/SHA256SUMS"
  [ "$_m" = ok ]
}

if [ "$DRY" = 1 ]; then
  log "dry run: stopping after preflight/status"
  printf 'verdict: DRY-RUN\npreflight_rc: %s\nsim_present: %s\nserving: %s\n' "$PF_RC" "$SIM" "${REG:-unknown}" >"$OUT/summary.txt"
  seal_pack || MANIFEST_BAD=1
  # the EXIT trap is installed later, so release our pid here
  [ "${LOCK_ACQUIRED:-0}" = 1 ] && : >"$LOCK"
  cat "$OUT/summary.txt"
  exit 0
fi

# ---------- 2a. a slow card is not an absent card ----------
# UIM can take 12-20 s to report a card after boot (community kebab-uim-
# provision waits up to 120 s).  Taking one snapshot right after boot and
# calling it "no SIM card" is how the whole evidence pack gets the wrong
# verdict on a card that is actually there.
SIM_SLOTS_AT_START=-1
SIM_WAITED=0
FLAG_SIM=0
[ -e /etc/kebab-modem-sim-expected ] && FLAG_SIM=1
if [ "$WAIT_SIM" -lt 0 ]; then WAIT_SIM=$(wait_sim_default "$FLAG_SIM"); fi
if [ "$SIM" = 0 ] && [ "$WAIT_SIM" -gt 0 ]; then
  SIM_WAITED=$WAIT_SIM
  log "no card in the first snapshot; waiting up to ${WAIT_SIM}s for UIM (slow card)"
  _slots=$(wait_sim "$WAIT_SIM") || true
  SIM_SLOTS_AT_START=${_slots:-0}
  if [ "${_slots:-0}" -gt 0 ] 2>/dev/null; then
    SIM=1; REG=$(serving_now)
    log "card appeared after the wait: slots=$_slots serving=${REG:-unknown}"
  else
    log "still no card after ${WAIT_SIM}s (slots=${_slots:-0})"
  fi
else
  SIM_SLOTS_AT_START=$(sim_now)
fi

# ---------- 2c. wait for registration ----------
# WDS Start needs an attached modem.  Right after boot the modem is still
# searching, and a start issued then fails with "error 14 no-service" which
# reads exactly like a data/APN problem.  Waiting here is the one thing the
# evidence pack must not get wrong (MM doc 4.21).
SERVING_AT_START=${REG:-unknown}
if [ "$SIM" = 1 ]; then
  log "waiting up to ${WAIT_REG}s for NAS registration (serving=${SERVING_AT_START})"
  if _serv_now=$(wait_registration "$WAIT_REG"); then
    SERVING_AT_START=$_serv_now; REG=$_serv_now
    log "registered: serving=$SERVING_AT_START"
  else
    SERVING_AT_START=${_serv_now:-unknown}; REG=$SERVING_AT_START
    log "not registered after ${WAIT_REG}s (serving=$SERVING_AT_START); continuing, the summary will say so"
  fi
else
  log "registration wait skipped: no SIM card (serving=${SERVING_AT_START})"
fi

# ---------- 2d. RF context: which RAT, which PLMN, how strong ----------
# Recorded next to the data attempt so a later "start failed" can be read
# against the cell the modem was actually on (GSM fallback vs LTE).
NAS_RAT=unknown NAS_PLMN="" NAS_STATE="" NAS_SIG=""
{
  qmicli -d qrtr://3 --nas-get-serving-system 2>&1
  echo "--- signal ---"
  qmicli -d qrtr://3 --nas-get-signal-info 2>&1
} >"$OUT/nas.txt" 2>&1
NAS_RAT=$(rat_from <"$OUT/nas.txt"); NAS_RAT=${NAS_RAT:-unknown}
NAS_PLMN=$(plmn_from <"$OUT/nas.txt")
NAS_STATE=$(nas_state_from <"$OUT/nas.txt")
NAS_SIG=$(sig_from <"$OUT/nas.txt")
log "nas: rat=$NAS_RAT plmn=${NAS_PLMN:-unknown} state=${NAS_STATE:-unknown} sig=${NAS_SIG:-none}"

# ---------- 2b. optional QMAP preparation ----------
# The runbook's fallback path, made one flag.  qmap_restore() is idempotent and
# is also wired to the EXIT/INT/TERM traps, so a mid-run failure cannot leave
# the modem in qmap mode or rmnet0 behind.
qmap_restore() {
  [ "$QMAP_ARMED" = 1 ] || return 0
  QMAP_ARMED=0
  log "qmap: restoring raw-ip/disabled and removing the link"
  python3 "$TOOL" format-set --ul disabled --dl disabled --apply >>"$OUT/qmap.log" 2>&1 || true
  "$LIB/qmap.sh" down >>"$OUT/qmap.log" 2>&1 || true
  "$LIB/qmap.sh" unload >>"$OUT/qmap.log" 2>&1 || true
  {
    echo "--- format after restore ---"
    python3 "$TOOL" format 2>&1 | grep -E 'link-layer|aggregation' || true
    echo "--- rmnet links on $LINK_PARENT ---"
    ip -o link show 2>&1 | grep -E "@$LINK_PARENT:" || echo "(none)"
    echo "--- module ---"
    lsmod | grep -E '^rmnet ' || echo "(rmnet not loaded)"
  } >"$OUT/qmap-after.txt" 2>&1
}
# A crash in this script used to leave data-up running: the bearer stayed up,
# holding the address and the MTU, invisible to the next run (found in round 50,
# on a run that aborted before its stop section).  One trap now always ends it.
abort_cleanup() {
  if [ -n "${DPID:-}" ] && kill -0 "$DPID" 2>/dev/null; then
    log "abort: stopping data-up (SIGTERM then SIGKILL)"
    kill -TERM "$DPID" 2>/dev/null
    for _ in 1 2 3 4 5; do kill -0 "$DPID" 2>/dev/null || break; sleep 1; done
    kill -0 "$DPID" 2>/dev/null && kill -KILL "$DPID" 2>/dev/null
    wait "$DPID" 2>/dev/null || true
  fi
  if [ -n "${SPID:-}" ]; then
    : >"$OUT/.stop-sampling" 2>/dev/null || true
    wait "$SPID" 2>/dev/null || true
    SPID=""
  fi
  _res=$(residue_cleanup "$IFACE" "${ORIG_MTU:-}")
  if [ -n "$_res" ]; then log "abort: residue $_res"; fi
  qmap_restore
  # an exited run must not leave a pid behind that looks like a live holder
  if [ "${LOCK_ACQUIRED:-0}" = 1 ]; then : >"$LOCK" 2>/dev/null || true; fi
}
trap 'abort_cleanup' EXIT INT TERM HUP

if [ "$QMAP" = 1 ]; then
  : >"$OUT/qmap.log"
  QT_RC=0
  QMAP_SKIP=""
  # Kernel-side precheck first (throw-away tun device, no modem touch): if
  # rmnet cannot round-trip a MAP frame on THIS kernel, switching the modem to
  # QMAP can only make things worse.
  if [ -f "$LIB/qmap-local-test.py" ]; then
    log "qmap: kernel precheck (tun round-trip, no modem touch)"
    if python3 "$LIB/qmap-local-test.py" --mode tun --mux "$MUX" --json >"$OUT/qmap-selftest.json" 2>>"$OUT/qmap.log"; then
      QMAP_SELFTEST=pass
      log "qmap: precheck PASS"
    else
      QMAP_SELFTEST=fail
      QMAP_SKIP="kernel QMAP precheck failed (see qmap-selftest.json); the modem was not touched"
      log "qmap: precheck FAIL, leaving the modem in raw-ip"
    fi
  fi
  # Third gate: SIM + registration.  Measured 2026-11-13: WDA qmap/qmap +
  # rmnet0 + data-up with no SIM left the WDS start unanswered, then the
  # firmware died ("Device died"), the PCIe link failed to retrain and only a
  # reboot brought it back.  See MM doc 4.15.
  [ -n "$QMAP_SKIP" ] || QMAP_SKIP=$(qmap_gate "$SIM" "${REG:-}" "$QMAP_SELFTEST")
  if [ -n "$QMAP_SKIP" ]; then
    QMAP_RESULT="skipped: $QMAP_SKIP; data-up stays on $IFACE without mux"
    log "qmap: skipping arming: $QMAP_SKIP"
  else
    log "qmap: Set Data Format ul=qmap dl=qmap"
    python3 "$TOOL" format-set --ul qmap --dl qmap --apply >>"$OUT/qmap.log" 2>&1 || QT_RC=$?
    if [ "$QT_RC" = 0 ]; then
      log "qmap: load rmnet and create rmnet0 over $IFACE mux $MUX"
      "$LIB/qmap.sh" load >>"$OUT/qmap.log" 2>&1 || QT_RC=$?
    fi
    if [ "$QT_RC" = 0 ]; then
      "$LIB/qmap.sh" up --mux "$MUX" >>"$OUT/qmap.log" 2>&1 || QT_RC=$?
    fi
    # qmap.sh picks a free rmnetN name (it must not reuse rmnet0 when that is
    # already somebody else's link, e.g. a ModemManager bearer), so read the
    # name back instead of assuming it.
    QMAP_IF=$(qmap_armed_iface "$OUT/qmap.log")
    if [ "$QT_RC" = 0 ] && [ -n "$QMAP_IF" ] && ip -o link show dev "$QMAP_IF" >/dev/null 2>&1; then
      QMAP_ARMED=1
      QMAP_RESULT="armed: $QMAP_IF over $IFACE mux $MUX"
      [ "$IFACE_GIVEN" = 1 ] || IFACE=$QMAP_IF
      log "qmap: ready, data-up will use iface=$IFACE mux=$MUX"
    else
      QMAP_RESULT="setup failed rc=$QT_RC (data-up falls back to $IFACE without mux)"
      log "qmap: setup failed rc=$QT_RC; not arming the restore, continuing on $IFACE"
    fi
  fi
  cat "$OUT/qmap.log" | sed 's/^/  qmap: /' | tail -12
fi

# ---------- 3. data-up, held ----------
ARGS="--ifname $IFACE"
if [ -n "$APN" ]; then ARGS="$ARGS --apn $APN"; else ARGS="$ARGS --profile $PROFILE"; fi
[ "${FAKE:-0}" = 1 ] && ARGS="$ARGS --fake"
[ "$QMAP_ARMED" = 1 ] && ARGS="$ARGS --mux $MUX"
log "starting: python3 $TOOL data-up $ARGS"
python3 "$TOOL" data-up $ARGS >"$OUT/dataup.log" 2>&1 &
DPID=$!
sleep 1

# Sample the bearer while it is held: a wedge shows up as a verdict change
# (traffic-flowing -> tx-only-suspected-wedge), which a single end snapshot
# would hide.  --sample-every 0 disables it.
SAMPLES_TSV="$OUT/samples.tsv"
if [ "${SAMPLE_EVERY:-0}" -gt 0 ] 2>/dev/null; then
  rm -f "$OUT/.stop-sampling"
  sample_loop "$SAMPLES_TSV" "$SAMPLE_EVERY" "$OUT/.stop-sampling" "$(( HOLD + 120 ))" &
  SPID=$!
else
  SPID=""
fi

IP=""
IP6=""
PING_OK=no
DNS_OK=no
DNS_SRV=""
TCP_OK=no
TCP_TARGET=""
PMTU=unknown
DATAUP_RC=-1
REAPED=0
DEADLINE=$(( $(date +%s) + HOLD ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  if ! kill -0 "$DPID" 2>/dev/null; then
    wait "$DPID"; DATAUP_RC=$?; REAPED=1
    log "data-up exited early rc=$DATAUP_RC"
    break
  fi
  IP=$(ip -4 -o addr show dev "$IFACE" 2>/dev/null | awk '{print $4}' | head -1)
  IP6=$(ip -6 -o addr show dev "$IFACE" scope global 2>/dev/null | awk '{print $4}' | head -1)
  [ -n "$IP$IP6" ] && { log "bearer address: ${IP:-none} / ${IP6:-none}"; break; }
  sleep 2
done


# ---------- 4. probes, then HOLD the bearer, then probe again ----------
# A single probe answers "does data work right now".  --hold answers the harder
# question an operator actually has: does it KEEP working.  So probe at t=0,
# keep the call up for HOLD seconds (the sampler runs throughout), probe again,
# and let the summary show both.  A bearer that works at t=0 and not at the end
# is the "works then wedges" failure mode, and it must not read as PASS.
PROBE0_PING=no
PROBE0_DNS=no
PROBE0_TCP=no
PROBE0_PMTU=unknown
HELD=0
if [ -n "${IP}${IP6}" ]; then
  probe_bearer "t=0 (bearer just came up)"
  PROBE0_PING=$PING_OK; PROBE0_DNS=$DNS_OK; PROBE0_TCP=$TCP_OK; PROBE0_PMTU=$PMTU
  if [ "${HOLD:-0}" -gt 0 ] 2>/dev/null; then
    HOLD_START=$(date +%s)
    HOLD_END=$(( HOLD_START + HOLD ))
    log "holding the bearer for ${HOLD}s (sampling every ${SAMPLE_EVERY}s)"
    while [ "$(date +%s)" -lt "$HOLD_END" ]; do
      if [ ! -e "${KEBAB_SYSFS_NET:-/sys/class/net}/$IFACE" ]; then
        log "interface ${IFACE} vanished during the hold"
        break
      fi
      if ! kill -0 "$DPID" 2>/dev/null; then
        log "data-up exited during the hold"
        break
      fi
      sleep 2
    done
    HELD=$(( $(date +%s) - HOLD_START ))
    log "held ${HELD}s of ${HOLD}s requested"
  fi
  probe_bearer "t=${HELD}s (end of hold)"
else
  probe_bearer
fi
# Did it carry traffic at the start but not at the end?  That is the wedge
# shape, and it decides both the reason and next_steps.
PROBE0_ANY=no
{ bearer_ok "${PROBE0_PING:-no}" "${PROBE0_DNS:-no}" "${PROBE0_TCP:-no}" && PROBE0_ANY=yes; } 2>/dev/null || PROBE0_ANY=no
END_ANY=no
{ bearer_ok "${PING_OK:-no}" "${DNS_OK:-no}" "${TCP_OK:-no}" && END_ANY=yes; } 2>/dev/null || END_ANY=no
LOST=$(bearer_lost "$PROBE0_ANY" "$END_ANY")
# Always sample-stop and always take one diag reading: with no bearer it says
# "bearer-down", which is exactly what a reader needs to tell "no call" from
# "call up but nothing moving".
if [ -n "${SPID:-}" ]; then
  : >"$OUT/.stop-sampling"
  wait "$SPID" 2>/dev/null || true
  SPID=""
fi
{ echo "== mhi diag =="; "$LIB/mhi-diag.sh" --samples 4 --interval 1; } >>"$OUT/probes.txt" 2>&1
{ echo "== ip -s link =="; ip -s link show "$IFACE"; } >>"$OUT/probes.txt" 2>&1

# ---------- 5. status after + dmesg ----------
{
  "$STATUS" 2>&1
  echo "--- health ---"
  cat /run/kebab-sdx55-health.json 2>/dev/null || echo "(no health json)"
  echo "--- addr ---"
  ip -4 addr show dev "$IFACE" 2>&1
  echo "--- addr6 ---"
  ip -6 addr show dev "$IFACE" 2>&1
} >"$OUT/status-after.txt" 2>&1
dmesg 2>/dev/null | tail -40 >"$OUT/dmesg-tail.txt"
ALIVE=no
[ -e /sys/class/net/mhi_hwip0 ] && ALIVE=yes
log "modem_alive=$ALIVE"

# ModemManager coexistence.  MM is installed and running on this box; once a SIM
# is in it may hold the initial EPS bearer or the data port, and then our bind
# can fail in a way that looks like a modem fault.  Recorded only -- stopping MM
# is an operator decision.
if [ "$(systemctl is-active ModemManager 2>/dev/null)" = active ]; then MM_STATE=active; else MM_STATE=inactive; fi
MM_FACTS=$(mmcli -m 0 --output-keyvalue 2>/dev/null | sed -n -e 's/^modem\.generic\.state[^a-z-]*: *//p' \
  -e 's/^modem\.generic\.state-failed-reason *: *//p' -e 's/^modem\.generic\.bearers *: *//p' | tr '\n' ' ' | sed 's/ *$//' || true)
log "mm: $MM_STATE ${MM_FACTS:-no-modem}"

# ---------- 6. stop the call ----------
if kill -0 "$DPID" 2>/dev/null; then
  log "stopping data-up (SIGTERM)"
  kill -TERM "$DPID" 2>/dev/null
  for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$DPID" 2>/dev/null || break; sleep 1; done
fi
if kill -0 "$DPID" 2>/dev/null; then
  log "data-up ignored SIGTERM, sending SIGKILL"
  kill -KILL "$DPID" 2>/dev/null
fi
if [ "$REAPED" = 0 ]; then
  wait "$DPID" 2>/dev/null
  DATAUP_RC=$?
fi
[ "$DATAUP_RC" -eq 0 ] && log "data-up exited cleanly"

# If data-up had to be killed, the address may still be there: repair it now
# rather than leaving a configured bearer behind for the next run.
RESIDUE=$(residue_cleanup "$IFACE" "$ORIG_MTU")
[ -n "$RESIDUE" ] && log "residue after stop: $RESIDUE"

RC_START=$(sed -n 's/^start *: result=\([0-9-]*\) error=\([0-9]*\).*/\2/p' "$OUT/dataup.log" | head -1)
STOP=$(sed -n 's/^stop *: result=\([0-9-]*\) error=\([0-9]*\).*/\2/p' "$OUT/dataup.log" | head -1)

# restore QMAP state now so the probes/status below describe the real baseline
qmap_restore

# ---------- 6b. what to do next, derived from the evidence ----------
CALLEND=$(sed -n 's/^.*call-end-reason *: *//p' "$OUT/dataup.log" 2>/dev/null | head -1)
# Route snapshot: shows whether anything took over the default route.
ip route show default >"$OUT/routes.txt" 2>&1 || true
BEARER_ROUTES=$(bearer_default_route "$(cat "$OUT/routes.txt" 2>/dev/null)" "$IFACE")
DIAG=$(grep -m1 -oE 'tx-only-suspected-wedge|traffic-flowing|idle|bearer-down' "$OUT/probes.txt" 2>/dev/null)
if [ -e "$OUT/.modem-gone" ]; then
  NEXT="the modem disappeared during the hold: reboot (host side cannot recover it, see MM doc 4.15) then re-run; the dmesg tail is in modemdied-*.txt"
elif [ "${FAKE:-0}" = 1 ]; then
  NEXT="fake bearer: this proves the flow, not the data path; run without --fake-bearer on SIM day"
else
  NEXT=$(derive_next "$SIM" "$IP" "${RC_START:-}" "${CALLEND:-}" "${DIAG:-}" "$IFACE" "$TOOL" "$ALIVE" "$SERVING_AT_START" "${LOST:-no}")
fi

# ---------- 6c. did the bearer stay up while we held it? ----------
SAMPLES_TREND=$(sample_trend "$OUT/samples.tsv")
WEDGE_SEEN=no
grep -q 'tx-only-suspected-wedge' "$OUT/samples.tsv" 2>/dev/null && WEDGE_SEEN=yes

# ---------- 7. verdict ----------

# PING_OK comes from the LAST probe_bearer call (the end of the hold), not from
# the whole file: "worked at t=0, dead now" is not PASS.
PING_OK=${PING_OK:-no}
ADDR=${IP:-$IP6}
VERDICT=FAIL
if [ "${FAKE:-0}" = 1 ]; then
  # A synthetic bearer proves the flow (address detection -> probes -> summary
  # -> cleanup).  It must never be able to print PASS: no modem call happened.
  VERDICT=REHEARSAL
  REASON="fake bearer: flow test only, no QMI and no modem call (ping=$PING_OK dns=$DNS_OK tcp=$TCP_OK)"
elif [ "$SIM" = 0 ]; then
  REASON="no SIM card seen by UIM (physical blocker)"
elif [ -z "$ADDR" ]; then
  REASON="no bearer address (start error ${RC_START:-unknown})"
elif ! bearer_ok "$PING_OK" "$DNS_OK" "$TCP_OK"; then
  REASON="bearer up at $ADDR but neither ICMP nor DNS answered (dns server ${DNS_SRV:-none})"
else
  VERDICT=PASS
  REASON="bearer up at $ADDR; ping=$PING_OK dns=$DNS_OK tcp=$TCP_OK (${TCP_TARGET:-no target})"
  [ "$WEDGE_SEEN" = yes ] && REASON="$REASON; WEDGE SEEN during the hold - see samples.tsv"
  [ "${LOST:-no}" = yes ] && REASON="$REASON; BEARER LOST during the hold (worked at t=0, dead at t=${HELD:-0}s) - see samples.tsv"
fi

{
  printf 'verdict: %s\n' "$VERDICT"
  printf 'reason: %s\n' "$REASON"
  printf 'bearer_source: %s\n' "$([ "${FAKE:-0}" = 1 ] && echo 'fake (no QMI, plumbing test)' || echo real)"
  printf 'sim_present: %s\n' "$SIM"
  printf 'sim_slots_at_start: %s\n' "${SIM_SLOTS_AT_START:-unknown}"
  printf 'sim_wait: %s\n' "${SIM_WAITED:-0}s"
  printf 'serving: %s\n' "${REG:-unknown}"
  printf 'nas: rat=%s plmn=%s state=%s sig=%s\n' "${NAS_RAT:-unknown}" "${NAS_PLMN:-unknown}" "${NAS_STATE:-unknown}" "${NAS_SIG:-none}"
  printf 'serving_before_data: %s\n' "${SERVING_AT_START:-unknown}"
  printf 'addr: %s\n' "${IP:-none}"
  printf 'addr6: %s\n' "${IP6:-none}"
  printf 'baseline_mtu: %s\n' "${ORIG_MTU:-unknown}"
  printf 'held: %s\n' "${HELD:-0}s"
  printf 'bearer_lost: %s\n' "${LOST:-no}"
  printf 'probes_at_start: ping=%s dns=%s tcp=%s pmtu=%s\n' "${PROBE0_PING:-unknown}" "${PROBE0_DNS:-unknown}" "${PROBE0_TCP:-unknown}" "${PROBE0_PMTU:-unknown}"
  printf 'ping: %s\n' "$PING_OK"
  printf 'dns: %s via %s\n' "$DNS_OK" "${DNS_SRV:-none}"
  printf 'tcp: %s %s\n' "$TCP_OK" "${TCP_TARGET:-none}"
  printf 'pmtu: %s\n' "${PMTU:-unknown}"
  printf 'samples_every: %ss\n' "${SAMPLE_EVERY:-0}"
  printf 'sample_trend: %s\n' "$SAMPLES_TREND"
  printf 'wedge_seen: %s\n' "$WEDGE_SEEN"
  printf 'wedge_dump: %s\n' "$(cat "$OUT/.wedge-path" 2>/dev/null || echo none)"
  _first=$(first_epoch "$OUT/samples.tsv")
  _gone=$(cat "$OUT/.modem-gone" 2>/dev/null)
  printf 'residue_after_stop: %s\n' "${RESIDUE:-none}"
  printf 'modem_gone: %s\n' "$([ -n "$_gone" ] && echo yes || echo no)"
  [ -n "$_gone" ] && printf 'modem_gone_at: %s\n' "$(elapsed_since "$_first" "$_gone")"
  printf 'start_error: %s\n' "${RC_START:-unknown}"
  printf 'stop_error: %s\n' "${STOP:-unknown}"
  printf 'dataup_rc: %s\n' "$DATAUP_RC"
  printf 'preflight_rc: %s\n' "$PF_RC"
  printf 'call_end: %s\n' "${CALLEND:-none}"
  printf 'diag: %s\n' "${DIAG:-unknown}"
  printf 'qmap: %s\n' "$QMAP_RESULT"
  printf 'qmap_selftest: %s\n' "$QMAP_SELFTEST"
  printf 'modem_alive: %s\n' "$ALIVE"
  printf 'mm: %s %s\n' "$MM_STATE" "${MM_FACTS:-no-modem}"
  printf 'default_routes: %s\n' "$(default_routes_from "$(cat "$OUT/routes.txt" 2>/dev/null)" | sed 's/^$/none/')"
  printf 'bearer_default_route: %s\n' "$([ "${BEARER_ROUTES:-0}" -gt 0 ] && echo "yes (${BEARER_ROUTES})" || echo no)"
  printf 'out: %s\n' "$OUT"
  printf 'next_steps: %s\n' "$NEXT"
} >"$OUT/summary.txt"

log "verdict=$VERDICT reason=$REASON"
cat "$OUT/summary.txt"
# last: the manifest must be written after every other file is final, and the
# pack must verify against it
MANIFEST_BAD=0
seal_pack || MANIFEST_BAD=1
# not via log(): run.log is sealed, so nothing may append to it now
[ "$MANIFEST_BAD" = 0 ] || printf '%s\n' "WARNING: the evidence pack failed its own manifest check (manifest-check.txt)"
[ "$VERDICT" = PASS ] || exit 1
[ "$MANIFEST_BAD" = 0 ] || exit 1
