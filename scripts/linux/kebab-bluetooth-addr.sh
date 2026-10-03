#!/bin/sh
# SPDX-License-Identifier: MIT
# Debian adaptation of jfbobier's kebab-bluetooth-addr at 2e5e065.
# Preserve upstream machine-id derivation; never read/write Qualcomm NV.
set -eu
DEV=${1:-hci0}
case "$DEV" in hci[0-9]*) INDEX=${DEV#hci};; *) exit 2;; esac
case "$INDEX" in ''|*[!0-9]*) exit 2;; esac
BTMGMT=/usr/bin/btmgmt
[ -x "$BTMGMT" ] && command -v timeout >/dev/null || exit 1
log() { printf '%s\n' "kebab-bluetooth-addr: $*" >&2; }
# BlueZ 5.82 needs attachable stdin, not systemd's /dev/null.
# info/config must be global: indexed config misprints an unconfigured index.
mgmt() { printf '' | timeout -k 1 5 "$BTMGMT" "$@"; }
uptime_s() { read -r up rest < /proc/uptime; printf '%s\n' "${up%%.*}"; }
start=$(uptime_s)
while [ "$(( $(uptime_s) - start ))" -lt 60 ]; do
    if info=$(mgmt info 2>&1) && printf '%s\n' "$info" | grep -q "^$DEV:"; then
        log "$DEV already configured; preserving its address"
        exit 0
    fi
    if config=$(mgmt config 2>&1) && printf '%s\n' "$config" | grep -q "^$DEV:[[:space:]]*Unconfigured controller"; then
        missing=$(printf '%s\n' "$config" | awk -v dev="$DEV:" '$1 ~ /^hci[0-9]+:$/ { selected=($1==dev) } selected && /missing options:/ { sub(/^.*missing options:[[:space:]]*/, ""); sub(/[[:space:]]*$/, ""); print; exit }')
        if [ "$missing" != public-address ]; then
            log "$DEV needs something other than public-address; refusing"
            exit 1
        fi
        [ -r /etc/machine-id ] || exit 1
        machine_id=$(tr -d '\n' < /etc/machine-id)
        case "$machine_id" in ''|*[!0-9a-fA-F]*) log 'Invalid machine-id'; exit 1;; esac
        [ "${#machine_id}" -eq 32 ] || exit 1
        digest=$(printf '%s' "$machine_id-kebab-bt" | sha256sum | cut -c1-10)
        addr=02
        n=1
        while [ "$n" -lt 10 ]; do
            addr="$addr:$(printf '%s' "$digest" | cut -c"$n"-"$((n+1))")"
            n=$((n+2))
        done
        mgmt --index "$INDEX" public-addr "$addr" || { log 'Controller rejected public-address'; exit 1; }
        # Set Public Address removes/re-adds the management index asynchronously.
        # Re-enter the bounded global readiness poll, rather than treating the
        # immediate empty index list as a failed address assignment.
        log 'Address accepted; waiting for configured controller to reappear'
    fi
    sleep 1
done
log "$DEV not ready within bounded wait"
exit 1
