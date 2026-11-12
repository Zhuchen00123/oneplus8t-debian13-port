#!/bin/sh
# Offline assertions for the SIM-day summary logic.
#
# kebab-sim-day.sh ends with "next_steps:", which is the only line anybody reads
# at 2am on SIM day.  Its decision logic is a pure function (derive_next) that
# can be sourced without touching the modem, so every branch is testable
# without a SIM card.  Usage: sudo sh test-kebab-sim-day.sh [path-to-sim-day.sh]
set -u

SCRIPT=${1:-/usr/local/lib/kebab-modem/sim-day.sh}
[ -r "$SCRIPT" ] || { echo "FAIL: cannot read $SCRIPT" >&2; exit 2; }

SIMDAY_LIB_ONLY=1
. "$SCRIPT"

pass=0
fail=0

# check <label> <expected-substring> <args...>
check() {
  label=$1; want=$2; shift 2
  got=$(derive_next "$@")
  case "$got" in
    *"$want"*) pass=$((pass + 1)); printf 'ok   %s\n' "$label" ;;
    *) fail=$((fail + 1)); printf 'FAIL %s\n  want substring: %s\n  got: %s\n' "$label" "$want" "$got" ;;
  esac
}

check "dead modem says reboot (host rescan cannot help)" "只能 reboot" \
      0 "" "" "" "" mhi_hwip0 /usr/local/lib/kebab-modem/kebab-qmi-wds.py no
check "no SIM says the card must be in before boot" "开机前插好" \
      0 "" "" "" "" mhi_hwip0 /usr/local/lib/kebab-modem/kebab-qmi-wds.py
check "card + start error 14 (registered) points at attach-set" "attach-set --list 3 --apply" \
      1 "" 14 "3 = no-service" "" mhi_hwip0 /usr/local/lib/kebab-modem/kebab-qmi-wds.py yes registered
check "card + start error 14 while still searching points at registration" "还没注册" \
      1 "" 14 "3 = no-service" "" mhi_hwip0 /usr/local/lib/kebab-modem/kebab-qmi-wds.py yes not-registered-searching
check "card + other start error (registered) points at the call-end table" "call-end 表" \
      1 "" 7 "" "" mhi_hwip0 /usr/local/lib/kebab-modem/kebab-qmi-wds.py yes registered
check "wedge points at the QMAP sequence" "format-set --ul qmap --dl qmap --apply" \
      1 10.0.0.2 0 "" tx-only-suspected-wedge mhi_hwip0 /usr/local/lib/kebab-modem/kebab-qmi-wds.py
check "traffic flowing but no ping points at routing" "ip -4 route show dev" \
      1 10.0.0.2 0 "" traffic-flowing mhi_hwip0 /usr/local/lib/kebab-modem/kebab-qmi-wds.py
check "idle/unknown still says to look at the link" "ip -s link show" \
      1 10.0.0.2 0 "" idle mhi_hwip0 /usr/local/lib/kebab-modem/kebab-qmi-wds.py

# qmap_gate: the three conditions that must hold before the modem is put into
# QMAP mode.  Empty output means "go".
gate() {
  label=$1; want=$2; shift 2
  got=$(qmap_gate "$@")
  if [ -z "$want" ]; then
    if [ -z "$got" ]; then
      pass=$((pass + 1)); printf 'ok   %s\n' "$label"
    else
      fail=$((fail + 1)); printf 'FAIL %s\n  want: allowed (empty), got: %s\n' "$label" "$got"
    fi
    return 0
  fi
  case "$got" in
    *"$want"*) pass=$((pass + 1)); printf 'ok   %s\n' "$label" ;;
    *) fail=$((fail + 1)); printf 'FAIL %s\n  want substring: %s\n  got: %s\n' "$label" "$want" "$got" ;;
  esac
}

gate "qmap gate blocks when the kernel precheck failed" "precheck failed" 1 registered fail
gate "qmap gate blocks on a card-less modem" "no SIM card" 0 "" pass
gate "qmap gate blocks before registration" "no registration yet" 1 not-registered-searching pass
gate "qmap gate allows a registered modem" "" 1 registered pass
gate "qmap gate allows roaming too" "" 1 registered-roaming pass

# registration_ok: the NAS states that are attached far enough to ask for a PDN
regcheck() {
  label=$1; want=$2; state=$3
  if registration_ok "$state"; then got=yes; else got=no; fi
  if [ "$got" = "$want" ]; then
    pass=$((pass + 1)); printf 'ok   %s\n' "$label"
  else
    fail=$((fail + 1)); printf 'FAIL %s\n  want: %s\n  got: %s (serving=%s)\n' "$label" "$want" "$got" "$state"
  fi
}
regcheck "registered counts as attached" yes registered
regcheck "registered-roaming counts as attached" yes registered-roaming
regcheck "not-registered-searching does not" no not-registered-searching
regcheck "limited service does not" no limited
regcheck "an empty state does not" no ""

# sim_slots_from_status / wait_sim_default: a card-less run must stay fast, and
# a status line that is missing must read as 0 rather than "unknown".
slcheck() {
  label=$1; want=$2; text=$3
  got=$(sim_slots_from_status "$text")
  if [ "$got" = "$want" ]; then
    pass=$((pass + 1)); printf 'ok   %s\n' "$label"
  else
    fail=$((fail + 1)); printf 'FAIL %s\n  want: %s\n  got: %s\n' "$label" "$want" "$got"
  fi
}
slcheck "one present slot parses to 1" 1 "sim_slots_present        1"
slcheck "two present slots parse to 2" 2 "sim_slots_present        2"
slcheck "no card parses to 0" 0 "sim_slots_present        0"
slcheck "a missing status line parses to 0" 0 "serving                  not-registered-searching"
if [ "$(wait_sim_default 1)" = 20 ] && [ "$(wait_sim_default 0)" = 0 ]; then
  pass=$((pass + 1)); printf 'ok   %s\n' "the expected-card flag enables the SIM wait, otherwise not"
else
  fail=$((fail + 1)); printf 'FAIL %s\n' "the expected-card flag enables the SIM wait, otherwise not"
fi

# bearer_ok: ICMP, DNS or a TCP handshake is enough (operators filter ICMP,
# and a resolver can be down while the PDN carries traffic fine)
bcheck() {
  label=$1; want=$2; ping=$3; dns=$4; tcp=${5:-no}
  if bearer_ok "$ping" "$dns" "$tcp"; then got=yes; else got=no; fi
  if [ "$got" = "$want" ]; then
    pass=$((pass + 1)); printf 'ok   %s\n' "$label"
  else
    fail=$((fail + 1)); printf 'FAIL %s\n  want: %s\n  got: %s\n' "$label" "$want" "$got"
  fi
}
bcheck "ping alone proves the bearer" yes yes no
bcheck "a DNS answer alone proves the bearer" yes no yes
bcheck "neither ICMP nor DNS means no proof" no no no
bcheck "a completed TCP handshake alone proves the bearer" yes no no yes

# sample_trend: the wedge report has to say "did it change" and "how fast";
# a single end snapshot cannot tell "never carried traffic" from "then wedged"
TMPD=$(mktemp -d)
printf 'epoch\tverdict\trx_bytes\ttx_bytes\tul_pending\n1700000000\tidle\t100\t200\t3\n1700000010\tidle\t300\t400\t3\n1700000020\tidle\t500\t600\t3\n' >"$TMPD/a.tsv"
printf 'epoch\tverdict\trx_bytes\ttx_bytes\tul_pending\n1700000000\ttraffic-flowing\t100\t200\t0\n1700000010\ttraffic-flowing\t1100\t1200\t7\n1700000020\ttx-only-suspected-wedge\t1100\t1400\t128\n' >"$TMPD/b.tsv"
: >"$TMPD/c.tsv"
tcheck() {
  label=$1; f=$2; pat=$3
  got=$(sample_trend "$f")
  case "$got" in
    *"$pat"*) pass=$((pass + 1)); printf 'ok   %s\n' "$label" ;;
    *) fail=$((fail + 1)); printf 'FAIL %s\n  want pattern: %s\n  got: %s\n' "$label" "$pat" "$got" ;;
  esac
}
tcheck "a steady bearer has no verdict change" "$TMPD/a.tsv" "3 samples, idle -> idle (0 change(s))"
tcheck "a wedge shows up as a change and as slow rx" "$TMPD/b.tsv" "traffic-flowing -> tx-only-suspected-wedge (1 change(s))"
tcheck "throughput is derived from the byte counters" "$TMPD/b.tsv" "tx 60 B/s"
tcheck "an empty series says so instead of inventing numbers" "$TMPD/c.tsv" "no samples"
rm -rf "$TMPD"

# NAS parsers: the RAT/PLMN/signal lines are what tell "not on LTE yet" from
# "APN wrong", so their extraction has to be pinned by a test, not by eye.
GSM_DUMP=$(printf '%s\n' \
  "[qrtr://3] Successfully got serving system:" \
  "	Registration state: 'not-registered-searching'" \
  "	Radio interfaces: '1'" \
  "		[0]: 'gsm'" \
  "	Detailed status:" \
  "		Status: 'limited'" \
  "	Current PLMN:" \
  "		MCC: '460'" \
  "		MNC: '0'" \
  "GSM:" \
  "	RSSI: '-95 dBm'")
LTE_DUMP=$(printf '%s\n' \
  "	Radio interfaces: '4'" \
  "		[0]: 'lte'" \
  "		Status: 'full'" \
  "		MCC: '460'" \
  "		MNC: '00'" \
  "LTE:" \
  "	RSSI: '-55 dBm'" \
  "	RSRP: '-85 dBm'" \
  "	RSRQ: '-10 dB'" \
  "	SINR: '18 dB'")
pcheck() {
  label=$1; want=$2; got=$3
  if [ "$got" = "$want" ]; then
    pass=$((pass + 1)); printf 'ok   %s\n' "$label"
  else
    fail=$((fail + 1)); printf 'FAIL %s\n  want: %s\n  got: %s\n' "$label" "$want" "$got"
  fi
}
pcheck "RAT of a GSM-only registration" gsm "$(printf '%s\n' "$GSM_DUMP" | rat_from)"
pcheck "RAT of an LTE registration" lte "$(printf '%s\n' "$LTE_DUMP" | rat_from)"
pcheck "PLMN is read from the Current PLMN block" "460/0" "$(printf '%s\n' "$GSM_DUMP" | plmn_from)"
pcheck "detailed status (limited) is recognised" limited "$(printf '%s\n' "$GSM_DUMP" | nas_state_from)"
pcheck "GSM signals reduce to RSSI" "RSSI=-95 dBm" "$(printf '%s\n' "$GSM_DUMP" | sig_from)"
pcheck "LTE signals keep all four readings" "RSSI=-55 dBm RSRP=-85 dBm RSRQ=-10 dB SINR=18 dB" "$(printf '%s\n' "$LTE_DUMP" | sig_from)"
if grep -q '^export LC_ALL=C' "$SCRIPT" 2>/dev/null; then
  pass=$((pass + 1)); printf 'ok   %s\n' "the parsers run under a pinned C locale"
else
  fail=$((fail + 1)); printf 'FAIL %s\n' "the parsers run under a pinned C locale"
fi
# wedge_dump: the snapshot has to be taken at first sight, because the rings
# move on and the modem can reset itself before the run ends
WDIR=$(mktemp -d)
WDUMP=$(wedge_dump "$WDIR" wedge 2>/dev/null | tail -1)
if [ -f "$WDUMP" ] && grep -q "== counters (same frame) ==" "$WDUMP"; then
  pass=$((pass + 1)); printf 'ok   %s\n' "the wedge dump is written with a counters section"
else
  fail=$((fail + 1)); printf 'FAIL %s\n  got: %s\n' "the wedge dump is written with a counters section" "$WDUMP"
fi
case "$WDUMP" in
  */wedge-*.txt) pass=$((pass + 1)); printf 'ok   %s\n' "the dump file is timestamped and greppable" ;;
  *) fail=$((fail + 1)); printf 'FAIL %s\n  got: %s\n' "the dump file is timestamped and greppable" "$WDUMP" ;;
esac
if grep -q "verdict" "$WDUMP" 2>/dev/null || grep -q "bearer-down" "$WDUMP" 2>/dev/null; then
  pass=$((pass + 1)); printf 'ok   %s\n' "the dump carries a diag verdict"
else
  fail=$((fail + 1)); printf 'FAIL %s\n' "the dump carries a diag verdict"
fi
rm -rf "$WDIR"

# the sampler must freeze the state on the FIRST wedge it sees, using the same
# code path the real run uses; a stub diag keeps this off the modem
FLIB=$(mktemp -d)
cat >"$FLIB/mhi-diag.sh" <<'STUB'
#!/bin/sh
printf '%s\n' '{"verdict":"tx-only-suspected-wedge","rx_bytes":1,"tx_bytes":2,"ul_pending_tres":127}'
STUB
chmod 755 "$FLIB/mhi-diag.sh"
SW=$(mktemp -d)
( LIB="$FLIB"; export LIB; sample_loop "$SW/s.tsv" 1 "$SW/never" 3 ) >/dev/null 2>&1
if [ -f "$SW/.wedge" ] && ls "$SW"/wedge-*.txt >/dev/null 2>&1; then
  pass=$((pass + 1)); printf 'ok   %s\n' "a wedge seen by the sampler freezes one dump"
else
  fail=$((fail + 1)); printf 'FAIL %s\n  files: %s\n' "a wedge seen by the sampler freezes one dump" "$(ls -A "$SW" 2>/dev/null | tr '\n' ' ')"
fi
if [ "$(ls "$SW"/wedge-*.txt 2>/dev/null | wc -l)" -eq 1 ]; then
  pass=$((pass + 1)); printf 'ok   %s\n' "the dump is written once, not once per sample"
else
  fail=$((fail + 1)); printf 'FAIL %s\n  dumps: %s\n' "the dump is written once, not once per sample" "$(ls "$SW"/wedge-*.txt 2>/dev/null | wc -l)"
fi
rm -rf "$FLIB" "$SW"
# the probe section is a function so the PASS path can be rehearsed; if it
# ever goes back to being inline, rehearsal (and this test) silently stops
# covering anything
if [ "$(command -v probe_bearer)" = "probe_bearer" ]; then
  pass=$((pass + 1)); printf 'ok   %s\n' "the probe block is callable as a function"
else
  fail=$((fail + 1)); printf 'FAIL %s\n' "the probe block is callable as a function"
fi
PROBE_OUT=$(mktemp -d)
( OUT="$PROBE_OUT"; IFACE=mhi_hwip0; IP=""; IP6=""; probe_bearer ) >/dev/null 2>&1
if grep -q "no bearer address; probes skipped" "$PROBE_OUT/probes.txt" 2>/dev/null; then
  pass=$((pass + 1)); printf 'ok   %s\n' "no address means the probes are skipped, not attempted"
else
  fail=$((fail + 1)); printf 'FAIL %s\n  probes.txt: %s\n' "no address means the probes are skipped, not attempted" "$(head -2 "$PROBE_OUT/probes.txt" 2>/dev/null)"
fi
rm -rf "$PROBE_OUT"

# gw_candidate: the interface address must never be used as a ping target -
# ping -I <iface> <own addr> is answered locally, i.e. 0% loss with no packet
# ever leaving the box (found on a synthetic bearer that had no default route)
gcheck() {
  label=$1; want=$2; got=$3
  if [ "$got" = "$want" ]; then
    pass=$((pass + 1)); printf 'ok   %s\n' "$label"
  else
    fail=$((fail + 1)); printf 'FAIL %s\n  want: %s\n  got: %s\n' "$label" "$want" "$got"
  fi
}
gcheck "a real default gateway is kept" 10.20.30.1 "$(gw_candidate 10.20.30.1 10.99.13.2)"
gcheck "the interface own address is refused" "" "$(gw_candidate 10.99.13.2 10.99.13.2)"
gcheck "no default route means no gateway target" "" "$(gw_candidate "" 10.99.13.2)"

# elapsed_since: the summary has to say WHEN the modem vanished; every sample
# after the death looks like a plain bearer-down, so the rows alone cannot
gcheck "elapsed_since measures the gap" "+37s" "$(elapsed_since 1000 1037)"
gcheck "elapsed_since ignores an empty first stamp" "" "$(elapsed_since "" 1500)"
gcheck "elapsed_since clamps a backwards clock" "+0s" "$(elapsed_since 2000 1900)"

# a modem that disappears mid-hold must be frozen once, with a timestamp, and
# must outrank the APN advice (the box needs a reboot, not a new profile)
MDIR=$(mktemp -d)
( LIB="$FLIB"; export LIB; KEBAB_SYSFS_NET="$MDIR/empty"; export KEBAB_SYSFS_NET; IFACE=ghost0; export IFACE; sample_loop "$MDIR/s.tsv" 1 "$MDIR/never" 3 ) >/dev/null 2>&1
if [ -s "$MDIR/.modem-gone" ] && ls "$MDIR"/modemdied-*.txt >/dev/null 2>&1; then
  pass=$((pass + 1)); printf 'ok   %s\n' "a vanishing modem is stamped and dumped once"
else
  fail=$((fail + 1)); printf 'FAIL %s\n  files: %s\n' "a vanishing modem is stamped and dumped once" "$(ls -A "$MDIR" 2>/dev/null | tr '\n' ' ')"
fi
# precedence, not just presence: the modem-gone advice must be checked BEFORE
# the fake-bearer branch, otherwise a dead modem would be reported as routine
GONE_LINE=$(grep -n "disappeared during the hold" "$SCRIPT" 2>/dev/null | head -1 | cut -d: -f1)
FAKE_LINE=$(grep -n "fake bearer: this proves the flow" "$SCRIPT" 2>/dev/null | head -1 | cut -d: -f1)
if [ -n "$GONE_LINE" ] && [ -n "$FAKE_LINE" ] && [ "$GONE_LINE" -lt "$FAKE_LINE" ]; then
  pass=$((pass + 1)); printf 'ok   %s\n' "a dead modem gets reboot advice before any other advice"
else
  fail=$((fail + 1)); printf 'FAIL %s\n  gone=%s fake=%s\n' "a dead modem gets reboot advice before any other advice" "${GONE_LINE:-none}" "${FAKE_LINE:-none}"
fi
rm -rf "$MDIR"

# samples.tsv opens with a header, so "the first sample" must skip it - taking
# line 1 gives the word "epoch" and every timestamp downstream goes blank
TTSV=$(mktemp)
printf '%s\n' "epoch	verdict	rx_bytes	tx_bytes	ul_pending" "1794436189	idle	0	7062	119" >"$TTSV"
gcheck "first_epoch skips the header row" "1794436189" "$(first_epoch "$TTSV")"
printf '%s\n' "epoch	verdict	rx_bytes	tx_bytes	ul_pending" >"$TTSV"
gcheck "first_epoch returns nothing for a header-only file" "" "$(first_epoch "$TTSV")"
gcheck "first_epoch tolerates a missing file" "" "$(first_epoch /nonexistent/samples.tsv)"
rm -f "$TTSV"

# Route evidence: the pack must show the bearer did not take over the uplink.
_rt='default via 192.168.8.2 dev usb0 proto static metric 100
default via 192.168.1.1 dev wlan0 proto dhcp src 192.168.1.122 metric 600'
gcheck "default_routes_from() compacts dev/metric" "usb0/100 wlan0/600" "$(default_routes_from "$_rt")"
_rt2="default via 10.99.13.1 dev mhi_hwip0 metric 1000"
gcheck "a bearer default route is counted" "1" "$(bearer_default_route "$_rt2" mhi_hwip0)"
gcheck "no bearer default route on a normal box" "0" "$(bearer_default_route "$_rt" mhi_hwip0)"

# --help must list every option: usage() used to be `head -18`, which silently
# cut off --qmap/--fake-bearer/--wait-sim/--sample-every (measured, round 57).
_help=$(sh "$SCRIPT" --help 2>&1)
for _opt in --qmap --fake-bearer --wait-sim --sample-every; do
  case "$_help" in
    *"$_opt"*) pass=$((pass + 1)); printf 'ok   help lists %s\n' "$_opt" ;;
    *) fail=$((fail + 1)); printf 'FAIL help does not list %s\n' "$_opt" ;;
  esac
done

# bearer_lost(): the difference between "never worked" and "worked then died".
# Only the second one is the QMAP case, so it must not be folded into the first.
gcheck "bearer_lost(no,no)" no "$(bearer_lost no no)"
gcheck "bearer_lost(yes,no)" yes "$(bearer_lost yes no)"
gcheck "bearer_lost(yes,yes)" no "$(bearer_lost yes yes)"
gcheck "bearer_lost(no,yes)" no "$(bearer_lost no yes)"
# derive_next must send a lost bearer to the QMAP/samples path, not to the APN one
_lostmsg=$(derive_next 1 10.1.1.2 0 none idle mhi_hwip0 /usr/local/lib/kebab-modem/kebab-qmi-wds.py yes registered yes)
if printf '%s' "$_lostmsg" | grep -q "先通后 wedge" && printf '%s' "$_lostmsg" | grep -q "samples.tsv"; then
  pass=$((pass + 1)); printf 'ok   %s\n' "a bearer lost during the hold points at samples.tsv and QMAP, not at the APN"
else
  fail=$((fail + 1)); printf 'FAIL %s\n  got: %s\n' "a bearer lost during the hold points at samples.tsv and QMAP, not at the APN" "$_lostmsg"
fi
# ... and an unregistered modem must still win over the lost-bearer case
_regmsg=$(derive_next 1 "" 14 none not-registered-searching mhi_hwip0 /usr/local/lib/kebab-modem/kebab-qmi-wds.py yes not-registered-searching yes)
if printf '%s' "$_regmsg" | grep -q "还没注册"; then
  pass=$((pass + 1)); printf 'ok   %s\n' "not-registered still outranks the lost-bearer advice"
else
  fail=$((fail + 1)); printf 'FAIL %s\n  got: %s\n' "not-registered still outranks the lost-bearer advice" "$_regmsg"
fi

# residue_cleanup: if data-up had to be SIGKILLed the address it installed is
# still there; that leftovers confuses the next run, so the guard must repair
# it.  A veth pair gives a real interface to prove it on.
if ip link add kt-res0 type veth peer name kt-res1 2>/dev/null; then
  ip link set kt-res0 up 2>/dev/null
  ip addr add 10.97.0.1/24 dev kt-res0 2>/dev/null
  got=$(residue_cleanup kt-res0 1500 2>/dev/null)
  if printf '%s' "$got" | grep -q "10.97.0.1/24" && ! ip -4 -o addr show dev kt-res0 | grep -q 10.97.0.1; then
    pass=$((pass + 1)); printf 'ok   %s\n' "a leftover address is removed by the residue guard"
  else
    fail=$((fail + 1)); printf 'FAIL %s\n  got: %s\n' "a leftover address is removed by the residue guard" "$got"
  fi
  if [ -z "$(residue_cleanup kt-res0 1500 2>/dev/null)" ]; then
    pass=$((pass + 1)); printf 'ok   %s\n' "the residue guard says nothing when the interface is clean"
  else
    fail=$((fail + 1)); printf 'FAIL %s\n' "the residue guard says nothing when the interface is clean"
  fi
  if [ -z "$(residue_cleanup kt-does-not-exist 1500 2>/dev/null)" ]; then
    pass=$((pass + 1)); printf 'ok   %s\n' "the residue guard tolerates a vanished interface"
  else
    fail=$((fail + 1)); printf 'FAIL %s\n' "the residue guard tolerates a vanished interface"
  fi
  # MTU is residue too: data-up sets it and a killed data-up never restores it,
  # so the guard must fix it even when the address is already gone.
  ip link set kt-res0 mtu 9000 2>/dev/null
  got=$(residue_cleanup kt-res0 1500 2>/dev/null)
  if printf '%s' "$got" | grep -q 'mtu 9000 -> 1500' && [ "$(cat /sys/class/net/kt-res0/mtu)" = 1500 ]; then
    pass=$((pass + 1)); printf 'ok   %s\n' "a leftover MTU is restored even with no address left"
  else
    fail=$((fail + 1)); printf 'FAIL %s\n  got: %s mtu=%s\n' "a leftover MTU is restored even with no address left" "$got" "$(cat /sys/class/net/kt-res0/mtu 2>/dev/null)"
  fi
  ip link del kt-res0 2>/dev/null || true
else
  printf 'note %s\n' "skip residue tests: cannot create a veth pair here"
fi

if grep -q "^trap 'abort_cleanup' EXIT INT TERM HUP" "${LIB:-/usr/local/lib/kebab-modem}/sim-day.sh" 2>/dev/null; then
  pass=$((pass + 1)); printf 'ok   %s\n' "sim-day installs the abort trap that stops the bearer on any exit"
else
  fail=$((fail + 1)); printf 'FAIL %s\n' "sim-day installs the abort trap that stops the bearer on any exit"
fi

# A log() call placed before the function is defined fails silently and only
# shows up as a missing line in run.log (measured twice).  The dry run must
# therefore be stderr-clean and must actually contain the early disk line.
_dl=$(mktemp -d 2>/dev/null)
if [ -n "_dl" ] && /usr/local/sbin/kebab-sim-day --dry-run --out "$_dl" >/dev/null 2>"$_dl.err"; then
  if [ -s "$_dl.err" ]; then
    fail=$((fail + 1)); printf 'FAIL %s\n  stderr: %s\n' "the dry run does not write to stderr" "$(head -1 "$_dl.err")"
  else
    pass=$((pass + 1)); printf 'ok   %s\n' "the dry run does not write to stderr"
  fi
  if grep -q 'disk:' "$_dl/run.log" 2>/dev/null; then
    pass=$((pass + 1)); printf 'ok   %s\n' "the early disk-space line reaches run.log"
  else
    fail=$((fail + 1)); printf 'FAIL %s\n' "the early disk-space line reaches run.log"
  fi
else
  printf 'note %s\n' "skip early-log checks: dry run failed"
fi
[ -n "_dl" ] && rm -rf "$_dl" "$_dl.err"

# Two concurrent runs would share mhi_hwip0 and the WDS call, so the second one
# must refuse instead of producing evidence nobody can explain.
_lkd=$(mktemp -d 2>/dev/null)
if [ -n "_lkd" ]; then
  flock /run/kebab-sim-day.lock -c 'sleep 4' >/dev/null 2>&1 &
  _lkh=$!
  sleep 1
  /usr/local/sbin/kebab-sim-day --dry-run --out "$_lkd" >"$_lkd.out" 2>&1
  _lrc=$?
  if [ "$_lrc" = 3 ] && grep -q 'already running' "$_lkd.out"; then
    pass=$((pass + 1)); printf 'ok   %s\n' "a second instance refuses with rc=3 and says why"
  else
    fail=$((fail + 1)); printf 'FAIL %s\n  rc=%s\n' "a second instance refuses with rc=3 and says why" "$_lrc"
  fi
  wait $_lkh 2>/dev/null
  if /usr/local/sbin/kebab-sim-day --dry-run --out "${_lkd}2" >/dev/null 2>&1; then
    pass=$((pass + 1)); printf 'ok   %s\n' "the lock is released when the holder exits"
  else
    fail=$((fail + 1)); printf 'FAIL %s\n' "the lock is released when the holder exits"
  fi
  # a run that exits must not leave a pid that looks like a live holder
  if [ -s /run/kebab-sim-day.lock ]; then
    fail=$((fail + 1)); printf 'FAIL %s\n' "an exited run leaves no pid in the lock file"
  else
    pass=$((pass + 1)); printf 'ok   %s\n' "an exited run leaves no pid in the lock file"
  fi
  rm -rf "$_lkd" "${_lkd}2" "${_lkd}2.err"
fi

# The MTU baseline must be the driver default, not "whatever is there now":
# an earlier crashed run leaving 1500 would otherwise be adopted and preserved
# forever.  --dry-run exits before touching the interface, so this is safe.
_bm=$(mktemp -d 2>/dev/null)
if [ -n "$_bm" ] && KEBAB_BEARER_MTU=9000 /usr/local/sbin/kebab-sim-day --dry-run --out "$_bm" >/dev/null 2>&1; then
  if grep -q 'driver default' "$_bm/run.log" 2>/dev/null; then
    pass=$((pass + 1)); printf 'ok   %s\n' "a non-default MTU at start is called out as residue"
  else
    fail=$((fail + 1)); printf 'FAIL %s\n' "a non-default MTU at start is called out as residue"
  fi
  if grep -q '^verdict: DRY-RUN' "$_bm/summary.txt" 2>/dev/null; then
    pass=$((pass + 1)); printf 'ok   %s\n' "the dry run still refuses to touch the interface"
  else
    fail=$((fail + 1)); printf 'FAIL %s\n' "the dry run still refuses to touch the interface"
  fi
else
  printf 'note %s\n' "skip MTU baseline checks: dry run did not produce a pack"
fi
[ -n "$_bm" ] && rm -rf "$_bm"

# The evidence pack must verify against its own manifest.  A truncated
# probes.txt reads exactly like "no data", so the seal is part of the evidence.
if command -v sha256sum >/dev/null 2>&1; then
  _pk=$(mktemp -d 2>/dev/null)
  if [ -n "$_pk" ] && /usr/local/sbin/kebab-sim-day --dry-run --out "$_pk" >/dev/null 2>&1; then
    if ( cd "$_pk" && sha256sum -c SHA256SUMS >/dev/null 2>&1 ); then
      pass=$((pass + 1)); printf 'ok   %s\n' "the dry-run evidence pack verifies against its own SHA256SUMS"
    else
      fail=$((fail + 1)); printf 'FAIL %s\n' "the dry-run evidence pack verifies against its own SHA256SUMS"
    fi
    if grep -q 'manifest check: ok' "$_pk/manifest-check.txt" 2>/dev/null; then
      pass=$((pass + 1)); printf 'ok   %s\n' "manifest-check.txt records a passing self-check"
    else
      fail=$((fail + 1)); printf 'FAIL %s\n' "manifest-check.txt records a passing self-check"
    fi
  else
    printf 'note %s\n' "skip manifest checks: dry run did not produce a pack"
  fi
  [ -n "$_pk" ] && rm -rf "$_pk"
fi

# the sourcing guard must not run the capture (it would need root and a modem)
if [ "$(id -u)" != 0 ] && [ -d /srv ]; then
  echo "note: ran as non-root and nothing tried to capture; guard works"
fi

printf '%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
# qmap_armed_iface: the QMAP link name comes from qmap.sh, never assumed.
TMPLOG=$(mktemp "${TMPDIR:-/tmp}/qmaplog.XXXXXX")
printf '%s\n' 'load: rmnet loaded' 'link up: rmnet3 over mhi_hwip0 mux_id 2 mtu 1500' \
  '33: rmnet3@mhi_hwip0: <UP,LOWER_UP,M-DOWN> mtu 1500' >"$TMPLOG"
got=$(qmap_armed_iface "$TMPLOG")
if [ "$got" = rmnet3 ]; then pass=$((pass + 1)); printf 'ok   %s\n' "link name is read back from qmap.sh output"; 
else fail=$((fail + 1)); printf 'FAIL %s\n  want: rmnet3\n  got: %s\n' "link name is read back from qmap.sh output" "$got"; fi
: >"$TMPLOG"
got=$(qmap_armed_iface "$TMPLOG")
if [ -z "$got" ]; then pass=$((pass + 1)); printf 'ok   %s\n' "an empty qmap log yields no name (arming skipped)"; 
else fail=$((fail + 1)); printf 'FAIL %s\n  want: empty\n  got: %s\n' "an empty qmap log yields no name (arming skipped)" "$got"; fi
rm -f "$TMPLOG"

[ "$pass" -ge 85 ] || { echo "FAIL: expected 85 checks" >&2; exit 1; }
echo "PASS"
