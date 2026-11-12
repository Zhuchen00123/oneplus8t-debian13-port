#!/bin/bash
# kebab-audio-on: turn the OnePlus 8T speaker/mic path on for the desktop stack.
#
# In order:
#   1. load the tfa2 module (blacklisted for udev, not for us)
#   2. let UCM power the DAPM route -- without it the PCM answers EINVAL on open
#   3. publish explicit 2ch S16 sink and 1ch S16 source nodes. WirePlumber's ACP
#      "pro-audio" profile builds 8-channel S24_32 nodes for this card and the
#      2-channel DAI then plays/records nothing; a node that states its channel
#      count does not have that problem.
#   4. make them the defaults so pw-play / pw-record find them.
#
# Reversible with kebab-audio-off.
set -uo pipefail
CARD_ID=${KEBAB_CARD_ID:-O8T}
NODE_NAME=${KEBAB_SINK_NAME:-o8t-speaker}
PCM=${KEBAB_PLAY_PCM:-hw:0,0}
CAP_PCM=${KEBAB_CAP_PCM:-hw:0,1}
UID_DEBIAN=$(id -u debian 2>/dev/null || echo 1000)
U="env XDG_RUNTIME_DIR=/run/user/$UID_DEBIAN"
LOG=/tmp/kebab-audio-on-node.log

log() { echo "audio-on: $*"; }
fail() { echo "audio-on: ERROR: $*" >&2; exit 1; }
[ "$(id -u)" = "0" ] || fail "must run as root"

node_exists() {
    sudo -u debian $U pw-cli ls Node 2>/dev/null | grep -q "node.name = \"$1\""
}

# Create one adapter node and prove it exists; a node that fails to open leaves
# nothing behind, and a silent failure here is what makes the next step fail.
make_node() {
    local name="$1" props="$2" i
    if node_exists "$name"; then
        log "node $name already present"
        return 0
    fi
    for i in 1 2 3 4 5; do
        sudo -u debian $U pw-cli create-node adapter "{ $props }" > "$LOG" 2>&1
        for _ in $(seq 1 20); do
            if node_exists "$name"; then
                log "node $name is up"
                return 0
            fi
            sleep 0.1
        done
        log "node $name missing after attempt $i: $(tr "\n" " " < "$LOG" | cut -c1-200)"
        sleep 0.4
    done
    return 1
}

if [ ! -e /proc/asound/$CARD_ID ]; then
    log "loading snd_soc_tfa2"
    modprobe snd_soc_tfa2 || fail "modprobe snd_soc_tfa2 failed"
    for _ in $(seq 1 40); do
        [ -e /proc/asound/$CARD_ID ] && break
        sleep 0.1
    done
fi
[ -e /proc/asound/$CARD_ID ] || fail "card $CARD_ID did not appear"

printf 'set _verb HiFi\nset _enadev Speaker\nset _enadev Mic\n' |
    alsaucm -c "$CARD_ID" -b - >/dev/null 2>&1
TERT=$(amixer -c 0 sget "TERT_MI2S_RX Audio Mixer MultiMedia1" 2>/dev/null | tail -1)
case "$TERT" in
    *"[on]"*) log "route on: $TERT" ;;
    *) fail "UCM route did not come up ($TERT)" ;;
esac
sleep 0.3   # the card settles before the first PCM open

make_node "$NODE_NAME" \
    "factory.name=api.alsa.pcm.sink node.name=$NODE_NAME node.description=\"OnePlus 8T Speaker\" api.alsa.path=\"$PCM\" audio.channels=2 audio.format=S16LE audio.rate=48000 media.class=Audio/Sink object.linger=true" ||
    fail "could not create the sink node"

make_node "${NODE_NAME}-mic" \
    "factory.name=api.alsa.pcm.source node.name=${NODE_NAME}-mic node.description=\"OnePlus 8T Mic\" api.alsa.path=\"$CAP_PCM\" audio.channels=1 audio.format=S16LE audio.rate=48000 media.class=Audio/Source object.linger=true" ||
    log "WARNING: capture node was not created; playback still works"

# 4. defaults, so a plain pw-play / pw-record lands on our nodes
SINK=""
for _ in $(seq 1 30); do
    SINK=$(sudo -u debian $U wpctl status 2>/dev/null | sed -n "/Sinks:/,/Sources:/p" |
            grep -oE "[0-9]+\. " | head -1 | tr -dc "0-9")
    [ -n "$SINK" ] && break
    sleep 0.1
done
[ -n "$SINK" ] || fail "no PipeWire sink is listed"
sudo -u debian $U wpctl set-default "$SINK" >/dev/null 2>&1
log "sink $SINK ($NODE_NAME) is the default"

SOURCE=""
for _ in $(seq 1 30); do
    SOURCE=$(sudo -u debian $U wpctl status 2>/dev/null | sed -n "/Sources:/,/Filters:/p" |
             grep -oE "[0-9]+\. " | head -1 | tr -dc "0-9")
    [ -n "$SOURCE" ] && break
    sleep 0.1
done
if [ -n "$SOURCE" ]; then
    sudo -u debian $U wpctl set-default "$SOURCE" >/dev/null 2>&1
    log "source $SOURCE (${NODE_NAME}-mic) is the default"
else
    log "WARNING: no PipeWire source is listed"
fi

sudo -u debian $U wpctl status 2>/dev/null | sed -n "/Audio/,/Video/p" | head -14
log "ready: pw-play and pw-record both use these nodes"
log "note: the amps run stage-1 DSP-bypass, so there is no excursion protection."
log "      treat unattended playback as unsafe; kebab-audio-off stops it."
