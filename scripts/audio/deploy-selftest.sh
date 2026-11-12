#!/bin/bash
# Deploy the whole audio toolchain directory to a private tmpdir on the device.
set -u
DEST="${1:-/tmp/kebab-audio-selftest}"
SRC=/f/codexprojects/oneplus8t/scripts/audio
SSH="ssh -F /f/codexprojects/oneplus8t/local/ssh/kebab-server.conf"
$SSH kebab "sudo -n rm -rf $DEST && sudo -n mkdir -p $DEST && sudo -n chown debian:debian $DEST && chmod 700 $DEST" || exit 1
tar -cz -C "$SRC" --exclude='__pycache__' --exclude='*.pyc' . \
  | $SSH kebab "tar -xz -C $DEST" || exit 1
$SSH kebab "cd $DEST && chmod 700 *.py *.sh 2>/dev/null; ls"
