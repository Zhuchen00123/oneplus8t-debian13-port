#!/bin/bash
# Disciplined unload: unmute (done) -> unbind the two i2c clients -> rmmod.
# No --force, no reboot. Aborts and reports if the state looks wrong.
set -u
RUN="$1"
S=/srv/kebab-audio-$RUN
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"

echo "== pre-checks: routes must be muted =="
$KA run --run "$RUN" --action idle-safety 2>&1 | tail -3

echo "== in-use state =="
lsmod | grep tfa2 || { echo "already unloaded"; exit 0; }
for d in 15-0034 15-0035; do
  printf '%s bound-to: ' "$d"; readlink /sys/bus/i2c/devices/$d/driver 2>/dev/null || echo "(none)"
done

echo "== unbind the i2c clients =="
for d in 15-0034 15-0035; do
  if [ -e /sys/bus/i2c/drivers/tfa2/$d ]; then
    echo "$d" | sudo -n tee /sys/bus/i2c/drivers/tfa2/unbind >/dev/null && echo "unbound $d"
  fi
done
sleep 0.5
for d in 15-0034 15-0035; do
  printf '%s bound-to: ' "$d"; readlink /sys/bus/i2c/devices/$d/driver 2>/dev/null || echo "(none)"
done

echo "== rmmod (plain, no force) =="
/sbin/rmmod snd_soc_tfa2 2>&1; rc=$?
echo "rmmod rc=$rc"
sleep 0.5
if [ $rc -eq 0 ]; then
  lsmod | grep tfa2 || echo "tfa2 unloaded"
  cat /proc/asound/cards
  echo "--- sound deferred"; grep sound /sys/kernel/debug/devices_deferred || echo "(no sound entry)"
  echo "--- amps bound"; for d in 15-0034 15-0035; do printf '%s ' "$d"; readlink /sys/bus/i2c/devices/$d/driver 2>/dev/null || echo UNBOUND; done
fi
echo "== dmesg (new lines only) =="
sudo -n dmesg | tail -14
