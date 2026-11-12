#!/bin/bash
# Install the kebab-audio executor on the device (root-owned, 0700) and create the
# private run directories for RUN. No audio is touched here.
set -eu
RUN="$1"
DEST=/usr/local/lib/kebab-audio
SRC=/tmp/kebab-audio-selftest
install -d -m 0700 -o root -g root "$DEST"
for f in kebab_audio.py kebab_audio_core.py kebab_audio_verify.py; do
  install -m 0700 -o root -g root "$SRC/$f" "$DEST/$f"
done
install -m 0600 -o root -g root "$SRC/audio-agent-policy.json" "$DEST/audio-agent-policy.json"
cat > /usr/local/sbin/kebab-audio <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /usr/local/lib/kebab-audio/kebab_audio.py "$@"
EOF
chmod 0755 /usr/local/sbin/kebab-audio
install -d -m 0700 -o root -g root "/srv/kebab-audio-$RUN"
install -d -m 0700 -o root -g root "/run/kebab-audio-$RUN"
echo "INSTALL_OK"
ls -l "$DEST"
python3 "$DEST/kebab_audio.py" selftest --out "/srv/kebab-audio-$RUN/guard-selftest-device.json" >/dev/null && \
  echo "DEVICE_SELFTEST_PASS" || echo "DEVICE_SELFTEST_FAIL"
