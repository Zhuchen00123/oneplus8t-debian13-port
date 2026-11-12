#!/bin/bash
# A6 close: inspect the escalation result, then tear everything back down and
# produce the final verdicts. No reboot, no kernel change.
set -u
RUN="$1"
S=/srv/kebab-audio-$RUN
R=/run/kebab-audio-$RUN
KA="python3 /usr/local/lib/kebab-audio/kebab_audio.py"

echo "== probe-left-2 evidence =="
sudo -n python3 -c "
import json
d=json.load(open('$S/evidence/action-probe-left-2.json'))
for k in ('error','detail','play_rc','capture_rc','play_stderr','capture_stderr','elapsed_s','hard_deadline_s','hard_deadline_hit','verdict'):
    if k in d: print('%-18s %s' % (k, json.dumps(d[k])[:220]))
"
echo "== captured wavs =="
ls -l "$R"/*.wav 2>/dev/null

echo "== idle-safety (card still present) =="
$KA run --run "$RUN" --action idle-safety 2>&1 | tail -3

echo "== stop guard 3: applies the audited mute set =="
systemctl stop "kebab-audio-guard-$RUN" 2>/dev/null
sleep 1.2
tail -1 "$S/guard-events.jsonl"

echo "== rollback: restore only this run's owned writes, then unload =="
$KA rollback --run "$RUN" --out "$S/rollback-report.json" 2>&1 | tail -30

echo "== final control readback =="
sudo -n python3 -c "
import json,subprocess
names=['TERT_MI2S_RX Audio Mixer MultiMedia1','MultiMedia2 Mixer VA_CODEC_DMA_TX_0',
       'VA_AIF1_CAP Mixer DEC0','VA DMIC MUX0','VA DEC0 MUX','VA_DEC0 Volume']
for n in names:
    p=subprocess.run(['amixer','-c','0','cget','name='+n],capture_output=True,text=True)
    v=[l for l in p.stdout.splitlines() if l.strip().startswith(': value')]
    print('%-42s %s' % (n, v[-1].strip() if v else ('ERR ' + p.stderr.strip()[:60])))
" 2>&1

echo "== module / card state after rollback =="
lsmod | grep -E 'tfa2' || echo "tfa2 unloaded"
cat /proc/asound/cards
cat /sys/kernel/debug/devices_deferred
echo "== blacklist untouched =="
cat /etc/modprobe.d/90-kebab-server.conf

echo "== delete raw PCM (statistics already extracted) =="
rm -f "$R"/*.wav
ls -l "$R" 2>/dev/null | head

echo "== final server-regression + r4 identity =="
$KA run --run "$RUN" --action server-regression 2>&1 | tail -3
echo "kernel=$(uname -r) boot_id=$(cat /proc/sys/kernel/random/boot_id)"
echo "uptime=$(cut -d' ' -f1 /proc/uptime)"

echo "== final result.json =="
$KA verify --run "$RUN" --out "$S/result.json" > /dev/null 2>&1
sudo -n python3 -c "
import json
r=json.load(open('$S/result.json'))
print('overall:', r['capabilities']['overall'])
for k,v in sorted(r['capabilities'].items()):
    if k=='overall': continue
    print('  %-18s %-10s %s' % (k, v['verdict'], json.dumps(v['detail'],default=str)[:160]))
print('budget_charged:', r['budget_charged'])
print('uncertain:', [a for a,_ in r['uncertain_hardware_actions']])
"
echo "== guard events tail =="
tail -4 "$S/guard-events.jsonl"
