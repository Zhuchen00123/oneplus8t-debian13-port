#!/bin/bash
# QMAP toolkit for the kebab SDX55 data path.
#
# Raw-IP over mhi_hwip0 is what the modem is configured for today, and it has a
# known failure mode on sibling hardware (~10 MB then wedged) that QMAP
# multiplexing over rmnet fixes.  This script manages the pieces that can be
# set up ahead of a bearer: the rmnet module (built out-of-tree, no image
# change), the rmnet link over mhi_hwip0, and the modem's WDA data format.
#
# usage:
#   kebab-modem-qmap.sh status
#   kebab-modem-qmap.sh load | unload
#   kebab-modem-qmap.sh up [--mux N] [--mtu N] | down
#   kebab-modem-qmap.sh format [show|raw-ip|qmap] [--apply]
#   kebab-modem-qmap.sh linktest [--mux N]
#
# linktest exercises the *libqmi* link-creation path (the one ModemManager's
# multiplex bearer uses: qmi_device_add_link_with_flags -> netlink), not
# iproute2.  It checks that the kernel receives the rmnet flags libqmi intended
# and that the link can be removed again, then restores the module state.
#
# The rmnet link's mux id must equal the mux id passed to the QMI bind, i.e.
#   kebab-qmi-wds.py data-up --mux N --ifname rmnet0
# Apollo's wds_test used mux 0; this script defaults to 1 to make "no mux" and
# "first mux" distinguishable, and prints whatever it used.
export LC_ALL=C LANG=C   # parsers below match English ip/iproute2 output
set -euo pipefail

MODULE=/usr/local/lib/kebab-modem/rmnet/rmnet.ko
PARENT=mhi_hwip0
LINK_PREF=rmnet0
MUX=1
MTU=1500
NAME_GIVEN=0
INGRESS_COMMANDS=off
STATE=/run/kebab-modem-qmap.state
WDS=/usr/local/lib/kebab-modem/kebab-qmi-wds.py
LINKTEST=/usr/local/lib/kebab-modem/rmnet-link-test
LINKTEST_PREFIX=kebt

die() { echo "FAIL: $*" >&2; exit 1; }

module_loaded() { [ -n "$(lsmod | awk '$1=="rmnet"{print $1}')" ]; }
link_exists() { [ -n "$1" ] && ip -o link show "$1" >/dev/null 2>&1; }

# Every rmnet slave of $PARENT, one name per line.  Ours, ModemManager's and a
# leftover from an earlier run are indistinguishable in `ip link`, which is the
# whole reason the state file below exists.
rmnet_links() {
	ip -o link show 2>/dev/null | awk -v p="@$PARENT:" 'index($0, p) {print $2}' | sed 's/@.*//' || true
}

mux_owner() { # $1 = mux id -> name of the link already using it, if any
	local n m
	for n in $(rmnet_links); do
		m=$(ip -d link show "$n" 2>/dev/null | sed -n 's/.*mux_id \([0-9]*\).*/\1/p' | head -1)
		[ "$m" = "$1" ] && { echo "$n"; return 0; }
	done
	return 1
}

pick_link_name() {
	local i n
	link_exists "$LINK_PREF" || { echo "$LINK_PREF"; return 0; }
	for i in 0 1 2 3 4 5 6 7; do
		n="rmnet$i"
		link_exists "$n" || { echo "$n"; return 0; }
	done
	return 1
}

# `|| true`: with set -e + pipefail a missing state file would otherwise abort
# the whole script (sed exits 2), which is exactly the no-state path down needs.
state_get() { sed -n "s/^$1=//p" "$STATE" 2>/dev/null | head -1 || true; }
state_put() { printf 'LINK=%s\nMUX=%s\nPARENT=%s\n' "$1" "$2" "$3" >"$STATE" 2>/dev/null || true; }
state_clear() { rm -f "$STATE" 2>/dev/null || true; }
current_link() { local n; n=$(state_get LINK); [ -n "$n" ] || n=$LINK_PREF; echo "$n"; }

cmd_status() {
	local n
	echo "module   : $(module_loaded && echo 'rmnet loaded' || echo 'rmnet not loaded')"
	n=$(current_link)
	if link_exists "$n"; then
		ip -d link show "$n" | sed -n '1p'
		echo "           $(ip -d link show "$n" | grep -o 'mux_id [0-9]*' || echo 'mux_id ?')"
	else
		echo "link     : $n absent"
	fi
	echo "state    : $STATE ($( [ -s "$STATE" ] && echo "LINK=$(state_get LINK) MUX=$(state_get MUX)" || echo "no state file"))"
	echo "links    : $(rmnet_links | tr '\n' ' ')"
	echo "parent   : $(ip -o link show "$PARENT" 2>/dev/null | sed -n '1p' || echo "$PARENT absent")"
	echo "kernel   : $(uname -r)"
	if [ -r "$MODULE" ]; then
		echo "module   : $MODULE ($(stat -c %s "$MODULE") bytes, sha256 $(sha256sum "$MODULE" | cut -c1-16)…)"
	else
		echo "module   : $MODULE missing"
	fi
	if [ -x /usr/bin/python3 ] && [ -e "$WDS" ]; then
		sudo -n python3 "$WDS" format 2>/dev/null | grep -E 'link-layer|aggregation' | sed 's/^/modem    : /' || true
	fi
}

cmd_load() {
	[ -r "$MODULE" ] || die "module file not found: $MODULE"
	if module_loaded; then
		echo "rmnet already loaded"
	else
		sudo -n insmod "$MODULE"
		sleep 0.3
		module_loaded || die "insmod did not register rmnet"
		echo "rmnet loaded"
	fi
	[ -n "$(ip link help 2>&1 | grep -o rmnet)" ] || true
	echo "link type: $(ip link add help 2>&1 | grep -c rmnet || true) mention(s) in ip help"
}

cmd_unload() {
	local n
	n=$(rmnet_links | tr '\n' ' ')
	[ -z "$n" ] || die "rmnet link(s) still up on $PARENT: $n -- run '$0 down' first"
	if module_loaded; then
		sudo -n rmmod rmnet
		echo "rmnet unloaded"
	else
		echo "rmnet not loaded"
	fi
}

cmd_up() {
	module_loaded || die "rmnet module not loaded; run '$0 load'"
	ip -o link show "$PARENT" >/dev/null 2>&1 || die "parent $PARENT missing"
	local owner name
	if owner=$(mux_owner "$MUX"); then
		die "mux $MUX is already used by '$owner' on $PARENT; refusing to share a link with another owner (a ModemManager bearer looks exactly the same). Use another --mux, or stop ModemManager first."
	fi
	if [ "$NAME_GIVEN" = 1 ] && link_exists "$LINK_PREF"; then
		die "$LINK_PREF already exists; refusing to adopt it (delete it first if it really is ours)"
	fi
	name=$(pick_link_name) || die "no free name in rmnet0..rmnet7"
	# ingress-commands = RMNET_FLAGS_INGRESS_MAP_COMMANDS.  Without it the
	# kernel silently drops MAP control frames (rmnet_handlers.c: a MAP_CMD_FLAG
	# packet without the flag goes straight to kfree_skb), so a flow-control
	# command from the modem would never be ACKed.  The firmware currently
	# reports flow-control = disabled, so the default stays OFF: injecting such
	# a command into this 7.2 kernel rebooted the box (2026-11-13, see MM doc
	# 4.20), and a feature we do not need yet is not worth an unexplained reset.
	sudo -n ip link add link "$PARENT" name "$name" type rmnet mux_id "$MUX" ingress-commands "$INGRESS_COMMANDS"
	sudo -n ip link set dev "$name" mtu "$MTU"
	sudo -n ip link set dev "$name" up
	state_put "$name" "$MUX" "$PARENT"
	echo "link up: $name over $PARENT mux_id $MUX mtu $MTU ingress-commands $INGRESS_COMMANDS"
	ip -d link show "$name" | sed -n '1p'
}

cmd_down() {
	local n found
	n=$(state_get LINK)
	if [ -z "$n" ]; then
		found=$(rmnet_links | tr '\n' ' ')
		echo "no state file ($STATE); rmnet links on $PARENT: ${found:-none}"
		echo "refusing to guess which one is ours (a ModemManager bearer looks identical); use 'down --name <if>'"
		return 0
	fi
	if link_exists "$n"; then
		sudo -n ip link set dev "$n" down 2>/dev/null || true
		sudo -n ip link del "$n"
		echo "$n removed"
	else
		echo "$n absent"
	fi
	state_clear
}

# linktest state: the EXIT trap runs after cmd_linktest has returned, so it
# must only touch globals (a local would be "unbound variable" under set -u,
# which used to make a passing test exit 1 and skip the module unload).
LT_LOADED_HERE=0
LT_CREATED=()

lt_cleanup() {
	local entry
	for entry in "${LT_CREATED[@]:-}"; do
		[ -n "$entry" ] || continue
		sudo -n "$LINKTEST" del "${entry%%:*}" "${entry##*:}" >/dev/null 2>&1 || true
	done
	LT_CREATED=()
	if [ "${LT_LOADED_HERE:-0}" = 1 ]; then
		sudo -n rmmod rmnet 2>/dev/null || true
		LT_LOADED_HERE=0
	fi
}

cmd_linktest() {
	[ -x "$LINKTEST" ] || die "link test binary missing: $LINKTEST"
	ip -o link show "$PARENT" >/dev/null 2>&1 || die "parent $PARENT missing (modem dead?)"

	local rc=0 mux_a mux_b plain cksum entry
	LT_CREATED=()
	LT_LOADED_HERE=0
	if ! module_loaded; then
		cmd_load >/dev/null
		LT_LOADED_HERE=1
	fi
	trap lt_cleanup EXIT INT TERM HUP

	# Each add needs its own mux id: a second link with the same mux id is
	# refused (the kernel returns EEXIST).
	mux_a=$MUX
	mux_b=$((MUX + 1))
	[ "$mux_b" -le 254 ] || mux_b=2

	add_link() { # $1 = mux, $2 = flags -> echoes ifname
		local out
		out=$(sudo -n timeout 20 "$LINKTEST" add "$PARENT" "$1" "$LINKTEST_PREFIX" "$2" 2>&1) || { echo "$out" >&2; return 1; }
		echo "$out" | sed -n 's/.*ifname=\([A-Za-z0-9_.-]*\).*/\1/p'
	}
	kernel_flags() { ip -d link show "$1" 2>/dev/null | sed -n '/rmnet mux_id/s/.*\(<[^>]*>\).*/\1/p'; }

	check() { # $1 label, $2 link, $3 must-contain
		local seen
		seen=$(kernel_flags "$2")
		case "$seen" in
			*"$3"*) echo "PASS  $1: $seen" ;;
			*) echo "FAIL  $1: kernel flags '$seen' (want $3)"; rc=1 ;;
		esac
	}

	if plain=$(add_link "$mux_a" 0); then
		LT_CREATED+=("$plain:$mux_a")
		echo "INFO  libqmi add mux $mux_a flags=0 -> $plain"
		check "mux $mux_a: deaggregation on by default" "$plain" "INGRESS_DEAGGREGATION"
	else
		echo "FAIL  libqmi add mux $mux_a flags=0 failed"; rc=1
	fi
	if cksum=$(add_link "$mux_b" 6); then
		LT_CREATED+=("$cksum:$mux_b")
		echo "INFO  libqmi add mux $mux_b flags=6 -> $cksum"
		check "mux $mux_b: ingress cksumv4 reaches the kernel" "$cksum" "INGRESS_MAP_CKSUMV4"
		check "mux $mux_b: egress cksumv4 reaches the kernel"  "$cksum" "EGRESS_MAP_CKSUMV4"
	else
		echo "FAIL  libqmi add mux $mux_b flags=6 failed"; rc=1
	fi

	for entry in "${LT_CREATED[@]:-}"; do
		[ -n "$entry" ] || continue
		if sudo -n timeout 20 "$LINKTEST" del "${entry%%:*}" "${entry##*:}" >/dev/null 2>&1; then
			echo "PASS  delete ${entry%%:*}"
		else
			echo "FAIL  delete ${entry%%:*}"
			rc=1
		fi
	done
	LT_CREATED=()

	[ "$rc" = 0 ] && echo "result: PASS (libqmi netlink link path works on $(uname -r))" \
	              || echo "result: FAIL"
	[ "$LT_LOADED_HERE" = 1 ] && echo "note  : rmnet was loaded for the test and is being unloaded again"

	trap - EXIT INT TERM HUP
	lt_cleanup
	return "$rc"
}

cmd_format() {
	local action=${1:-show}
	shift || true
	case "$action" in
	show) sudo -n python3 "$WDS" format ;;
	raw-ip) sudo -n python3 "$WDS" format-set --link-layer raw-ip --ul disabled --dl disabled "$@" ;;
	qmap)   sudo -n python3 "$WDS" format-set --link-layer raw-ip --ul qmap --dl qmap "$@" ;;
	*) die "format: unknown action '$action'" ;;
	esac
}

CMD=${1:-status}
shift || true
case "$CMD" in
status) cmd_status ;;
load)   cmd_load ;;
unload) cmd_unload ;;
up)     while [ $# -gt 0 ]; do case "$1" in
            --mux) MUX=$2; shift 2 ;;
            --mtu) MTU=$2; shift 2 ;;
            --name) LINK_PREF=$2; NAME_GIVEN=1; shift 2 ;;
            --no-commands) INGRESS_COMMANDS=off; shift ;;
            --commands) INGRESS_COMMANDS=on; shift ;;
            *) die "up: unknown argument $1" ;; esac; done; cmd_up ;;
down)   while [ $# -gt 0 ]; do case "$1" in
            --name) LINK_PREF=$2; NAME_GIVEN=1; shift 2 ;;
            *) die "down: unknown argument $1" ;; esac; done
        if [ "$NAME_GIVEN" = 1 ]; then
            # Explicit request: delete exactly this link, ignore the state file.
            if link_exists "$LINK_PREF"; then sudo -n ip link set dev "$LINK_PREF" down 2>/dev/null || true; sudo -n ip link del "$LINK_PREF"; echo "$LINK_PREF removed"; else echo "$LINK_PREF absent"; fi
            [ "$(state_get LINK)" = "$LINK_PREF" ] && state_clear
        else
            cmd_down
        fi ;;
linktest) while [ $# -gt 0 ]; do case "$1" in --mux) MUX=$2; shift 2 ;; *) die "linktest: unknown argument $1" ;; esac; done; cmd_linktest ;;
format) cmd_format "$@" ;;
*) echo "usage: $0 {status|load|unload|up|down|format|linktest} [--mux N] [--mtu N] [--name IF]" >&2; exit 2 ;;
esac
