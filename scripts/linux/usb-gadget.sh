#!/bin/sh
# Runs inside the Linux candidate, after explicit authorization to boot it.
# Creates only configfs USB interfaces, never a mass-storage function.
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
network=${1:-rndis}
case "$network" in rndis|ecm|ncm) ;; *) echo 'Expected rndis, ecm or ncm' >&2; exit 1 ;; esac
modprobe phy_qcom_qusb2
modprobe libcomposite
modprobe usb_f_acm
modprobe "usb_f_$network"
mountpoint -q /sys/kernel/config || mount -t configfs configfs /sys/kernel/config
g=/sys/kernel/config/usb_gadget/kebab
if [ -d "$g" ]; then echo 'Gadget already exists; preserve it for inspection' >&2; exit 1; fi
mkdir "$g"
printf '0x1d6b\n' > "$g/idVendor"
printf '0x0104\n' > "$g/idProduct"
printf '0x0200\n' > "$g/bcdUSB"
printf '0x0102\n' > "$g/bcdDevice"
printf '0xef\n' > "$g/bDeviceClass"
printf '0x02\n' > "$g/bDeviceSubClass"
printf '0x01\n' > "$g/bDeviceProtocol"
mkdir "$g/strings/0x409"
printf 'kebab-linux-rescue\n' > "$g/strings/0x409/serialnumber"
printf 'Linux\n' > "$g/strings/0x409/manufacturer"
printf 'KB2000 Linux rescue\n' > "$g/strings/0x409/product"
mkdir "$g/configs/c.1" "$g/configs/c.1/strings/0x409"
printf 'USB rescue network and serial\n' > "$g/configs/c.1/strings/0x409/configuration"
printf '250\n' > "$g/configs/c.1/MaxPower"
mkdir "$g/functions/$network.usb0" "$g/functions/acm.usb0"
printf '02:00:00:00:08:01\n' > "$g/functions/$network.usb0/dev_addr"
printf '02:00:00:00:08:02\n' > "$g/functions/$network.usb0/host_addr"
if [ "$network" = rndis ]; then
  # Windows 11 rndiscmp.inf matches EF/04/01 without a custom host INF.
  # Set these before linking the function; linked attributes are busy.
  printf '0xef\n' > "$g/functions/rndis.usb0/class"
  printf '0x04\n' > "$g/functions/rndis.usb0/subclass"
  printf '0x01\n' > "$g/functions/rndis.usb0/protocol"
  printf '1\n' > "$g/os_desc/use"
  printf '0xcd\n' > "$g/os_desc/b_vendor_code"
  printf 'MSFT100\n' > "$g/os_desc/qw_sign"
  printf 'RNDIS\n' > "$g/functions/rndis.usb0/os_desc/interface.rndis/compatible_id"
  printf '5162001\n' > "$g/functions/rndis.usb0/os_desc/interface.rndis/sub_compatible_id"
  ln -s "$g/configs/c.1" "$g/os_desc/c.1"
fi
# Keep RNDIS before ACM: Windows failed to start it when ACM was first.
ln -s "$g/functions/$network.usb0" "$g/configs/c.1/01-network"
ln -s "$g/functions/acm.usb0" "$g/configs/c.1/02-serial"
udc=
i=0
while [ "$i" -lt 60 ]; do
  for controller in /sys/class/udc/*; do
    [ -e "$controller" ] || continue
    udc=${controller##*/}
    break
  done
  [ -z "$udc" ] || break
  i=$((i + 1))
  sleep 1
done
[ -n "$udc" ] || { echo 'No UDC found; gadget left unbound for inspection' >&2; exit 1; }
printf '%s\n' "$udc" > "$g/UDC"
ip link set usb0 up
ip address add 192.168.8.1/24 dev usb0
echo "USB $network + ACM ready on $udc; device address 192.168.8.1/24"
