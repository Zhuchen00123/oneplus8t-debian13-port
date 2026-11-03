#!/usr/bin/env bash
# Link test: campus -> HK VPS (ICMP + upload + download)
# usage: bash hk-vps-linktest.sh [rounds] [interval_sec]
set -u
ROUNDS=${1:-3}
GAP=${2:-30}
HOST_V6=${HOST_V6:-2602:f506:81::6440:1333}
HOST_V4=${HOST_V4:-198.44.170.21}
KEY=${KEY:-/mnt/c/Users/15185/.ssh/dsh_tunnel}
RECV=/mnt/c/Users/15185/AppData/Local/Temp/http_recv.py
TMPK=/tmp/hklink_key
cp "$KEY" "$TMPK" 2>/dev/null && chmod 600 "$TMPK"
SSHOPT="-n -i $TMPK -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o BatchMode=yes -o ConnectTimeout=15"

echo "# setup servers on $HOST_V6"
scp -q -i "$TMPK" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$RECV" "root@[$HOST_V6]:/tmp/http_recv.py" || { echo 'scp failed'; exit 1; }
timeout 90 ssh $SSHOPT "root@$HOST_V6" 'mkdir -p /tmp/srv; [ -f /tmp/srv/t50.bin ] || head -c 50000000 /dev/urandom > /tmp/srv/t50.bin; systemd-run --unit=speedget --collect /usr/bin/python3 -m http.server 18080 --bind :: --directory /tmp/srv >/dev/null 2>&1; systemd-run --unit=speedrecv --collect /usr/bin/python3 /tmp/http_recv.py >/dev/null 2>&1; sleep 2; ss -lnt | grep -cE ":1808[05]"'

head -c 10000000 /dev/zero > /tmp/hk_u10.bin
echo
echo 'time      loss   rtt(ms)  up(KB/s)  down(KB/s)'
for i in $(seq 1 "$ROUNDS"); do
  T=$(TZ=Asia/Shanghai date '+%H:%M:%S')
  P=$(ping -6 -c 30 -i 0.1 -W 2 -s 1200 "$HOST_V6" 2>&1 | tail -2 | tr '\n' ' ')
  LOSS=$(printf '%s' "$P" | sed -n 's/.*, \([0-9.]*\)% packet loss.*/\1/p' | awk '{printf "%.1f", $1}')
  RTT=$(printf '%s' "$P" | awk -F'/' '/rtt/{print $5}' | cut -d' ' -f1)
  UP=$(timeout 90 curl -6 -s -m 85 -X POST --data-binary @/tmp/hk_u10.bin -o /dev/null -w '%{speed_upload}' "http://[$HOST_V6]:18085/" 2>/dev/null)
  DN=$(timeout 90 curl -6 -s -m 85 -r 0-19999999 -o /dev/null -w '%{speed_download}' "http://[$HOST_V6]:18080/t50.bin" 2>/dev/null)
  printf '%-9s %-6s %-8s %-9s %s\n' "$T" "${LOSS:-?}%" "${RTT:-?}" "$(( ${UP:-0} / 1024 ))" "$(( ${DN:-0} / 1024 ))"
  [ "$i" -lt "$ROUNDS" ] && sleep "$GAP"
done

echo
echo '# cleanup'
timeout 60 ssh $SSHOPT "root@$HOST_V6" 'systemctl stop speedget speedrecv 2>/dev/null; systemctl reset-failed speedget speedrecv 2>/dev/null; rm -rf /tmp/srv /tmp/http_recv.py' >/dev/null 2>&1
rm -f /tmp/hk_u10.bin "$TMPK"
echo done