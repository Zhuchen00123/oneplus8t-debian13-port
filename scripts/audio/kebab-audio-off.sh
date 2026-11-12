#!/bin/bash
# kebab-audio-off: undo exactly what kebab-audio-on did, in reverse order, and
# verify the result instead of assuming it.
set -uo pipefail
CARD_ID=${KEBAB_CARD_ID:-O8T}
NODE_PREFIX=${KEBAB_NODE_PREFIX:-o8t-}
UID_DEBIAN=$(id -u debian 2>/dev/null || echo 1000)
U="env XDG_RUNTIME_DIR=/run/user/$UID_DEBIAN"
log() { echo "audio-off: $*"; }
[ "$(id -u)" = "0" ] || { echo "audio-off: ERROR: must run as root" >&2; exit 1; }

# 1. drop our sink node(s); WirePlumber keeps no handle for us to reuse
# nodes we create all share the o8t- prefix (sink + source)
IDS=$(sudo -u debian $U pw-cli ls Node 2>/dev/null | awk -v n="$NODE_PREFIX" '
    /^[ \t]*id [0-9]+,/ { cur = $2; sub(",", "", cur) }
    index($0, "node.name = \"" n) { print cur }')
for id in $IDS; do
    sudo -u debian $U pw-cli destroy "$id" >/dev/null 2>&1 && log "destroyed node $id"
done

# 2. switch the ACP device profile off so nothing holds the PCM open, otherwise
#    rmmod fails with EBUSY and the card stays behind
DEV=$(sudo -u debian $U wpctl status 2>/dev/null |
      sed -n "/Devices:/,/Sinks:/p" | grep -oE "[0-9]+\. " | head -1 | tr -dc "0-9")
if [ -n "$DEV" ]; then
    sudo -u debian $U wpctl set-profile "$DEV" 0 >/dev/null 2>&1 && log "device $DEV profile off"
fi

# 3. power the route down through UCM. alsaucm has no memory between processes,
#    so the device has to be enabled in the same session before it can be disabled.
if [ -e /proc/asound/$CARD_ID ]; then
    printf 'set _verb HiFi\nset _enadev Speaker\nset _disdev Speaker\nset _enadev Mic\nset _disdev Mic\n' |
        alsaucm -c "$CARD_ID" -b - >/dev/null 2>&1
    log "route: $(amixer -c 0 sget "TERT_MI2S_RX Audio Mixer MultiMedia1" 2>/dev/null | tail -1)"
fi

# 4. unbind the two amps first: the module refcount includes the codec
#    bindings, so rmmod alone fails with EBUSY and leaves the card behind
for dev in 15-0034 15-0035; do
    if [ -e "/sys/bus/i2c/devices/$dev/driver" ]; then
        echo "$dev" > /sys/bus/i2c/drivers/tfa2/unbind 2>/dev/null && log "unbound $dev"
    fi
done

# 5. unload the module, then prove the card is gone
if lsmod | grep -q "^snd_soc_tfa2"; then
    for _ in $(seq 1 20); do
        rmmod snd_soc_tfa2 2>/dev/null || /usr/sbin/rmmod snd_soc_tfa2 2>/dev/null
        lsmod | grep -q "^snd_soc_tfa2" || break
        sleep 0.2
    done
    if lsmod | grep -q "^snd_soc_tfa2"; then
        log "WARNING: snd_soc_tfa2 still loaded (something still holds the PCM)"
    else
        log "snd_soc_tfa2 unloaded"
    fi
fi
if [ -e /proc/asound/$CARD_ID ]; then
    log "WARNING: card $CARD_ID still present"
else
    log "card $CARD_ID gone"
fi
cat /proc/asound/cards 2>/dev/null | head -3
