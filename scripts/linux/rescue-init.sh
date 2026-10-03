#!/bin/sh
# RAM rescue only. Do not mount, unlock, repartition or write phone storage.
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
mkdir -p /dev /proc /sys /run /tmp
mount -t devtmpfs devtmpfs /dev
mkdir -p /dev/pts
mount -t devpts -o gid=5,mode=0620,ptmxmode=0666 devpts /dev/pts
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t tmpfs -o mode=0755 tmpfs /run
mount -t tmpfs -o mode=1777 tmpfs /tmp
exec </dev/console >/dev/console 2>&1
echo 'KB2000 Debian 13 ARM64 rescue initramfs; filesystems remain unmounted'
uname -a
echo 7 > /proc/sys/kernel/printk
if /sbin/kebab-usb-gadget rndis > /run/usb-gadget.log 2>&1; then
  echo 'USB configured; set PC adapter to 192.168.8.2/24, then SSH to root@192.168.8.1'
else
  echo 'USB setup failed; inspect /run/usb-gadget.log on the local console'
fi
# Each boot gets a fresh SSH host key. No private SSH key is embedded.
mkdir -p /etc/dropbear
if /usr/bin/dropbearkey -t ed25519 -f /etc/dropbear/dropbear_ed25519_host_key > /run/dropbear-keygen.log 2>&1; then
  /usr/sbin/dropbear -E -s -j -k -r /etc/dropbear/dropbear_ed25519_host_key -p 192.168.8.1:22 > /run/dropbear.log 2>&1
fi
echo 'Block-device names (no mounts or data reads):'
cat /proc/partitions
echo 'Collect dmesg, /proc/cmdline, /sys/class/udc and /run/*.log; use reboot to return to stock boot'
# Serial fallback and display console are physical USB/local access only.
if [ -c /dev/ttyGS0 ]; then
  setsid sh -c 'exec </dev/ttyGS0 >/dev/ttyGS0 2>&1; echo "KB2000 RAM rescue serial console"; exec /bin/sh -i' &
fi
while :; do
  setsid sh -c 'exec </dev/console >/dev/console 2>&1; exec /bin/sh -i'
  sleep 1
done
