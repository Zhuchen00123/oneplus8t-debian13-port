#!/bin/bash
# Host only: extend a verified Debian base in a new Linux-filesystem directory.
set -euo pipefail
[[ $# == 2 ]] || { echo 'Usage: prepare-server-rootfs.sh BASE_EXPORT BUILD_DIR' >&2; exit 2; }
[[ $(id -u) == 0 ]] || { echo 'Run as root on the build host.' >&2; exit 1; }
base=$(realpath "$1")
build=$(realpath -m "$2")
[[ "$build" == /home/*/oneplus8t-server-* && ! -e "$build" ]] || { echo 'Choose a fresh /home/.../oneplus8t-server-* directory.' >&2; exit 1; }
python3 - "$base" <<'PY'
import hashlib,json,pathlib,sys
b=pathlib.Path(sys.argv[1]); m=json.loads((b/'rootfs-export-manifest.json').read_text())
p=b/'debian13-arm64-base.tar.gz'
assert m['architecture']=='arm64' and m['kernelrelease']=='7.2.0-kebab-debian13+'
assert hashlib.file_digest(p.open('rb'),'sha256').hexdigest()==m['files_sha256'][p.name]
PY
mkdir -p "$build/rootfs" "$build/packages"
exec > >(tee "$build/prepare.log") 2>&1
rootfs="$build/rootfs"
tar --numeric-owner -xpf "$base/debian13-arm64-base.tar.gz" -C "$rootfs"
printf '#!/bin/sh\nexit 101\n' > "$rootfs/usr/sbin/policy-rc.d"
chmod 755 "$rootfs/usr/sbin/policy-rc.d"
# Explicit emulation remains available for reproducible chroot commands.
cp /usr/bin/qemu-aarch64-static "$rootfs/usr/bin/qemu-aarch64-static"
cat > "$rootfs/etc/apt/sources.list" <<'EOF'
deb https://deb.debian.org/debian trixie main non-free-firmware
deb https://deb.debian.org/debian trixie-updates main non-free-firmware
deb https://deb.debian.org/debian-security trixie-security main non-free-firmware
EOF
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
export DEBIAN_FRONTEND=noninteractive LC_ALL=C
apt_args=(-o Acquire::https::Proxy=DIRECT -o Acquire::http::Proxy=DIRECT -o Dpkg::Use-Pty=0)
chroot "$rootfs" /usr/bin/env DEBIAN_FRONTEND=noninteractive apt-get "${apt_args[@]}" update
chroot "$rootfs" /usr/bin/env DEBIAN_FRONTEND=noninteractive apt-get "${apt_args[@]}" \
  -o APT::Keep-Downloaded-Packages=true --no-install-recommends -y install \
  firmware-atheros firmware-qcom-soc wpasupplicant iw rfkill bluez \
  alsa-ucm-conf alsa-utils qrtr-tools iputils-ping nftables curl rsync ethtool wireless-regdb systemd-resolved e2fsprogs \
  systemd-timesyncd libegl-mesa0 libgl1-mesa-dri mesa-utils-bin mesa-vulkan-drivers vulkan-tools \
  v4l-utils libcamera-tools libcamera-ipa python3
# Community kernel trusts upstream regdb keys, not Debian's signing key.
# Select the paired signed database; do not disable signature verification.
chroot "$rootfs" update-alternatives --set regulatory.db /lib/firmware/regulatory.db-upstream
# Unprivileged headless GPU diagnostics need both DRM node groups.
chroot "$rootfs" usermod -a -G video,render debian
find "$rootfs/var/cache/apt/archives" -maxdepth 1 -type f -name '*.deb' -exec cp -- '{}' "$build/packages/" \;
chroot "$rootfs" dpkg --audit
chroot "$rootfs" dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' > "$build/packages.tsv"
chroot "$rootfs" apt-cache policy firmware-atheros firmware-qcom-soc qrtr-tools > "$build/firmware-package-policy.txt"
# Bluetooth/audio are optional for a server. Keep packages, do not auto-start them.
chroot "$rootfs" systemctl disable bluetooth.service alsa-restore.service alsa-state.service || true
rm "$rootfs/usr/bin/qemu-aarch64-static" "$rootfs/usr/sbin/policy-rc.d"
echo 'Prepared server packages; no phone commands executed.'
