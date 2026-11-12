#!/bin/bash
echo "== sound model =="
tr -d '\0' < /proc/device-tree/sound/model; echo
echo "== tert-mi2s dai-link =="
for f in /proc/device-tree/sound/tert-mi2s-dai-link/*; do echo "- $(basename $f)"; done
echo "== cpu dai =="
for f in /proc/device-tree/sound/tert-mi2s-dai-link/cpu/*; do echo "- $(basename $f)"; done
echo "== va dai-link =="
for f in /proc/device-tree/sound/va-dai-link/*; do echo "- $(basename $f)"; done
echo "== asound state =="
ls -la /var/lib/alsa/ 2>&1 | head -5
echo "== alsa ucm2 dirs for platform =="
ls /usr/share/alsa/ucm2/conf.d 2>&1 | head -20
