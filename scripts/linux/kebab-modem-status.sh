#!/bin/sh
# Compact read-only SDX55 status for humans and scripts.
set -u
export LC_ALL=C LANG=C   # every parser below matches English tool output
Q="timeout 12 qmicli -d qrtr://3"
p() { printf "%-24s %s\n" "$1" "$2"; }
p boot_id "$(cat /proc/sys/kernel/random/boot_id)"
p uptime "$(cut -d' ' -f1 /proc/uptime)s"
for m in mhi_pci_generic mhi_sahara_modem mhi_wwan_ctrl; do
  if lsmod | grep -q "^$m "; then p "module:$m" loaded; else p "module:$m" missing; fi
done
if [ -d /sys/bus/mhi/devices/mhi1 ]; then p mhi1 present; else p mhi1 absent; fi
p wwan_ports "$(ls /dev/wwan* 2>/dev/null | tr '\n' ' ')"
p netdev "$(ip -br link 2>/dev/null | awk '/mhi_|wwan/ {printf "%s(%s) ", $1, $2}')"
p qrtr_loc_services "$(qrtr-lookup 2>/dev/null | awk '$1==16' | wc -l)"
for u in pm-service-native tqftpserv-sdx55 mhi-efs-sync kebab-gps; do
  p "unit:$u" "$(systemctl is-active $u 2>&1)"
done
if [ -d /sys/bus/mhi/devices/mhi1 ]; then
  p op_mode "$($Q --dms-get-operating-mode 2>/dev/null | grep -m1 Mode | cut -d\' -f2)"
  BANDS=$($Q --dms-get-band-capabilities 2>/dev/null | grep -m1 "LTE bands:" | cut -d\' -f2)
  p band_lte "$(echo "$BANDS" | cut -c1-70)"
  p sim_slots_present "$($Q --uim-get-slot-status 2>/dev/null | grep -c 'Card status: present')"
  p serving "$($Q --nas-get-serving-system 2>/dev/null | grep -m1 "Registration state" | cut -d\' -f2)"
  p detail_status "$($Q --nas-get-serving-system 2>/dev/null | grep -m1 "Status:" | cut -d\' -f2)"
  p signal "$($Q --nas-get-signal-info 2>/dev/null | grep -E 'RSSI|RSRP' | head -2 | tr -d '\t' | tr '\n' ' ')"
  p pdc_platform "$($Q --pdc-list-configs=platform 2>/dev/null | grep -c 'Config')"
fi
p gps_fix "$(sed -n 's/.*"fix": \(true\|false\).*/\1/p' /run/kebab-gps.json 2>/dev/null)"
p gps_sats "$(sed -n 's/.*"sats_visible": \([0-9]*\).*/\1/p' /run/kebab-gps.json 2>/dev/null)"
p efs_sessions "$(sudo -n journalctl -u mhi-efs-sync -b --no-pager 2>/dev/null | grep -c 'session complete')"

# Data plane: what data-up configured on the raw-IP interface (empty until a bearer exists)
if [ -e /sys/class/net/mhi_hwip0 ]; then
  p data_addr "$(ip -4 -o addr show dev mhi_hwip0 2>/dev/null | awk '{print $4}')"
  p data_link "$(cat /sys/class/net/mhi_hwip0/operstate 2>/dev/null)"
  p data_mtu "$(cat /sys/class/net/mhi_hwip0/mtu 2>/dev/null)"
  p data_bytes "rx=$(cat /sys/class/net/mhi_hwip0/statistics/rx_bytes 2>/dev/null) tx=$(cat /sys/class/net/mhi_hwip0/statistics/tx_bytes 2>/dev/null)"
fi
p data_service "$(systemctl is-active kebab-sdx55-data.service 2>&1)"
# QMAP/rmnet availability decides whether muxed bearers are even possible (see doc §8.1)
if zcat /proc/config.gz 2>/dev/null | grep -q "^CONFIG_RMNET="; then p rmnet_kernel yes; else p rmnet_kernel no; fi
p sim_expected_flag "$([ -e /etc/kebab-modem-sim-expected ] && echo set || echo unset)"
# Crash evidence.  ramoops IS reserved on this board (4 MiB at 0xb0000000,
# CONFIG_PSTORE_RAM=y, console backend on), so an unexpected reset that came
# from the kernel should leave a record here -- check it after any surprise
# reboot before touching the modem again (MM doc 4.20).
if [ -r /sys/fs/pstore ]; then
  _n=$(ls -1 /sys/fs/pstore 2>/dev/null | wc -l)
  p pstore "records=$_n$([ "$_n" -gt 0 ] && echo " (read: sudo cat /sys/fs/pstore/*)")"
else
  p pstore "unreadable (run as root)"
fi
