#!/bin/bash
# A0: read-only baseline + source freeze for RUN. Writes only under /srv/kebab-audio-RUN.
set -u
RUN="$1"
S=/srv/kebab-audio-$RUN
echo "== inventory =="
kebab-audio inventory --out "$S/baseline.json" > /dev/null && echo "baseline_ok"
echo "== source hashes (device side) =="
{
  echo "collected_wall_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "boot_id=$(cat /proc/sys/kernel/random/boot_id)"
  echo "kernel_release=$(uname -r)"
  for f in /usr/lib/modules/$(uname -r)/kernel/sound/soc/codecs/snd-soc-tfa2.ko.zst \
           /usr/local/lib/kebab-audio/kebab_audio.py \
           /usr/local/lib/kebab-audio/kebab_audio_core.py \
           /usr/local/lib/kebab-audio/kebab_audio_verify.py \
           /usr/local/lib/kebab-audio/audio-agent-policy.json ; do
    printf '%s  %s\n' "$(sha256sum "$f" | cut -d' ' -f1)" "$f"
  done
} > "$S/source-hashes-device.txt"
cat "$S/source-hashes-device.txt"
echo "== existing audio state snapshot =="
{
  echo "--- /proc/asound/cards"; cat /proc/asound/cards
  echo "--- blacklist"; cat /etc/modprobe.d/90-kebab-server.conf
  echo "--- lsmod snd/tfa"; lsmod | grep -E '^(snd|tfa)' || echo "(none)"
  echo "--- deferred"; cat /sys/kernel/debug/devices_deferred
  echo "--- amp bind state"
  for d in 15-0034 15-0035; do printf '%s driver=' "$d"; readlink /sys/bus/i2c/devices/$d/driver || echo NONE; done
} > "$S/pre-state.txt"
cat "$S/pre-state.txt"
echo "== sizes =="
ls -l "$S"
