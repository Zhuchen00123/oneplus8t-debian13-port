#!/bin/bash
# deploy-selftest.sh stages the tree in /tmp; the executor actually runs from
# /usr/local/lib/kebab-audio, so the two have to be kept in step explicitly.
set -euo pipefail
cd /f/codexprojects/oneplus8t/scripts/audio
bash deploy-selftest.sh >/dev/null
ssh -F /f/codexprojects/oneplus8t/local/ssh/kebab-server.conf kebab \
  'sudo -n install -m 0700 -o root -g root /tmp/kebab-audio-selftest/kebab_audio.py /tmp/kebab-audio-selftest/kebab_audio_core.py /tmp/kebab-audio-selftest/kebab_audio_verify.py /usr/local/lib/kebab-audio/ && sudo -n ls -l /usr/local/lib/kebab-audio/'
