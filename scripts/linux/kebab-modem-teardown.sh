#!/bin/sh
# Reverse of bringup.sh: stop the remote-EFS daemon and unload the MHI stack.
# The Wi-Fi MHI controller (mhi0 / ath11k) is a different PCI function and is
# never referenced here.
#
# NOTE (measured 2026-11-12): after this runs the modem no longer has a QRTR
# node until the next reboot — qcom_mhi_qrtr stays bound to the removed
# mhi1_IPCR and does not re-register when mhi_pci_generic is loaded again.
# That is why kebab-sdx55-modem.service deliberately does NOT call this on
# stop/restart; use it only when you really want the modem powered down.
set -u
TAG=kebab-sdx55
log() { logger -t "$TAG" "$*"; echo "[$(date -u +%FT%TZ)] $*"; }
systemctl stop mhi-efs-sync.service 2>/dev/null || true
for m in mhi_pci_generic mhi_wwan_ctrl mhi_sahara_modem; do
  modprobe -r "$m" 2>/dev/null && log "removed $m" || log "WARN: could not remove $m"
done
systemctl stop tqftpserv-sdx55.service pm-service-native.service 2>/dev/null || true
log "torn down"

