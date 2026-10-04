#!/bin/bash
# Run only under a private mount namespace on the build host.
set -euo pipefail
root=$(realpath "${1:?Rootfs required}")
shift
[[ "$root" == /home/*/oneplus8t-server-*/rootfs && -f "$root/etc/debian_version" ]] || exit 1
[[ $(readlink /proc/self/ns/mnt) != $(readlink /proc/1/ns/mnt) ]] || { echo 'Private mount namespace required.' >&2; exit 1; }
mount -t proc proc "$root/proc"
mount -t sysfs -o ro sysfs "$root/sys"
exec chroot "$root" "$@"
