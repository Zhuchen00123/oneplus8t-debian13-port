#!/bin/bash
# A6 replay: load the module a second time from the same manifest lineage.
set -u
RUN="$1"
S=/srv/kebab-audio-$RUN
R=/run/kebab-audio-$RUN
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"

echo "== state before replay =="
lsmod | grep tfa2 || echo "tfa2 not loaded"
cat /proc/asound/cards

python3 /tmp/kebab-audio-selftest/a9-addload.py "$RUN" || exit 1
SHA=$(sha256sum "$S/trial-manifest.json" | cut -d' ' -f1)
echo "manifest sha=$SHA"

systemctl stop "kebab-audio-guard-$RUN" 2>/dev/null || true
systemctl reset-failed "kebab-audio-guard-$RUN" 2>/dev/null || true
rm -f "$R/guard.json"
systemd-run --unit="kebab-audio-guard-$RUN" --collect --quiet \
  --description="kebab audio silence guard replay ($RUN)" \
  /usr/bin/python3 /usr/local/lib/kebab-audio/kebab_audio.py guard \
  --run "$RUN" --manifest-sha "$SHA" --max-seconds 240
for i in $(seq 1 60); do
  st=$(python3 -c "import json;d=json.load(open('$R/guard.json'));print(d.get('state'),d.get('manifest_sha')=='''$SHA''')" 2>/dev/null)
  [ "$st" = "armed True" ] && break
  sleep 0.25
done
echo "guard4: $st"

echo "== journaled reload =="
$KA run --run "$RUN" --action load-tfa2 2>&1 | tail -6
sleep 1
echo "== card present? =="
cat /proc/asound/cards
echo "== binding check =="
$KA run --run "$RUN" --action binding 2>&1 | tail -3
sudo -n dmesg | tail -4

echo "== stop guard, unload again =="
systemctl stop "kebab-audio-guard-$RUN" 2>/dev/null
sleep 1
for d in 15-0034 15-0035; do
  [ -e /sys/bus/i2c/drivers/tfa2/$d ] && echo "$d" | sudo -n tee /sys/bus/i2c/drivers/tfa2/unbind >/dev/null
done
sleep 0.4
/sbin/rmmod snd_soc_tfa2 2>&1; echo "rmmod rc=$?"
sleep 0.4
lsmod | grep tfa2 || echo "tfa2 unloaded"
cat /proc/asound/cards
echo "== blacklist / boot identity unchanged =="
grep -c blacklist /etc/modprobe.d/90-kebab-server.conf
echo "kernel=$(uname -r) boot=$(cat /proc/sys/kernel/random/boot_id) uptime=$(cut -d' ' -f1 /proc/uptime)"
