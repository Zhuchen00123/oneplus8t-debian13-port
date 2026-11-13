#!/usr/bin/env python3
"""Owned, reversible IPv4-only network for this Waydroid laboratory.
No main/default-route edits, no host DNS edits, no global forwarding or flush.
Only wlan0's per-interface forwarding is leased; its previous value is restored.
"""
import ipaddress
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

BASE = Path('/run/waydroid-r4-lab-network')
STATE = BASE / 'owner.json'
BRIDGE = 'waydroid0'
TABLE = 'o8t_wd_lab_20261112'
SUBNET = ipaddress.ip_network('192.168.240.0/24')
UPLINK = 'wlan0'
POLICY = '5240'

def run(argv, check=True, **kw):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=15, **kw)
    if check and p.returncode:
        raise RuntimeError(f'{argv!r}: rc={p.returncode}: {p.stderr}')
    return p

def policy_rule_argv(action, ingress_only=True):
    if action not in {'add','del'}:
        raise ValueError('only exact add/del of the owned policy rule is allowed')
    argv=['ip','rule',action,'pref',POLICY]
    if ingress_only:
        argv.extend(['iif',BRIDGE])
    return argv+['from',str(SUBNET),'lookup',POLICY]

def record(s):
    STATE.write_text(json.dumps(s, indent=2))
    STATE.chmod(0o600)

def stop():
    if not STATE.exists():
        return
    s = json.loads(STATE.read_text())
    if s.get('owner') != TABLE:
        raise RuntimeError('foreign owner')
    pidfile = BASE / 'dnsmasq.pid'
    if pidfile.exists():
        pid = int(pidfile.read_text())
        proc = Path(f'/proc/{pid}/cmdline')
        if proc.exists():
            argv = proc.read_bytes().split(bytes([0]))
            if ('--pid-file=' + str(pidfile)).encode() not in argv:
                raise RuntimeError('dnsmasq PID ownership mismatch')
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 5
            while proc.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            if proc.exists():
                raise RuntimeError('owned dnsmasq did not stop')
    if s.get('nft'):
        run(['nft', 'delete', 'table', 'inet', TABLE])
    if s.get('rule'):
        run(policy_rule_argv('del',s.get('policy_iif',False)))
    if s.get('route'):
        # Exact route only, never flush a routing table.
        run(['ip', 'route', 'del', 'table', POLICY, 'default', 'via', s['gateway'], 'dev', UPLINK])
    knob = Path(f'/proc/sys/net/ipv4/conf/{UPLINK}/forwarding')
    if s.get('forwarding'):
        if knob.read_text().strip() != '1':
            raise RuntimeError('uplink forwarding drift: refusing blind restore')
        knob.write_text(s['old_forwarding'])
    if s.get('bridge'):
        idx = Path('/sys/class/net') / BRIDGE / 'ifindex'
        if idx.exists():
            if int(idx.read_text()) != s['ifindex']:
                raise RuntimeError('bridge identity changed')
            run(['ip', 'link', 'del', BRIDGE])
    s['stopped_utc_epoch'] = time.time()
    record(s)
    # Keep immutable evidence; only the active ownership marker is removed.
    archive = BASE / ('stopped-' + str(time.time_ns()) + '.json')
    archive.write_text(STATE.read_text())
    assert STATE.resolve() == BASE / 'owner.json'
    STATE.unlink()

def start():
    if STATE.exists():
        raise RuntimeError('network is already owned; stop explicitly first')
    if Path('/sys/class/net', BRIDGE).exists():
        raise RuntimeError('existing bridge belongs to someone else')
    if run(['nft', 'list', 'table', 'inet', TABLE], check=False).returncode == 0:
        raise RuntimeError('existing nft table')
    for route in json.loads(run(['ip', '-j', '-4', 'route', 'show', 'table', 'all']).stdout):
        dst = route.get('dst', 'default')
        if dst != 'default' and ipaddress.ip_network(dst, strict=False).overlaps(SUBNET):
            raise RuntimeError('container subnet conflicts with host route: ' + dst)
    if any(str(x.get('priority')) == POLICY or str(x.get('table')) == POLICY
           for x in json.loads(run(['ip', '-j', 'rule']).stdout)):
        raise RuntimeError('policy rule collision')
    if run(['ip', 'route', 'show', 'table', POLICY], check=False).stdout.strip():
        raise RuntimeError('routing table collision')
    routes = json.loads(run(['ip', '-j', '-4', 'route', 'show', 'default', 'dev', UPLINK]).stdout)
    if len(routes) != 1:
        raise RuntimeError('ambiguous uplink gateway')
    gateway = str(ipaddress.IPv4Address(routes[0]['gateway']))
    BASE.mkdir(mode=0o755, exist_ok=True)
    knob = Path(f'/proc/sys/net/ipv4/conf/{UPLINK}/forwarding')
    s = {'owner': TABLE, 'gateway': gateway, 'old_forwarding': knob.read_text(), 'started_epoch': time.time()}
    record(s)
    try:
        run(['ip', 'link', 'add', BRIDGE, 'type', 'bridge'])
        s.update(bridge=True, ifindex=int(Path('/sys/class/net', BRIDGE, 'ifindex').read_text())); record(s)
        run(['ip', 'address', 'add', '192.168.240.1/24', 'dev', BRIDGE])
        Path(f'/proc/sys/net/ipv6/conf/{BRIDGE}/disable_ipv6').write_text('1')
        Path(f'/proc/sys/net/ipv4/conf/{BRIDGE}/forwarding').write_text('1')
        run(['ip', 'link', 'set', BRIDGE, 'up'])
        run(['ip', 'route', 'add', 'table', POLICY, 'default', 'via', gateway, 'dev', UPLINK, 'onlink'])
        s['route'] = True; record(s)
        # Source-only routing also captures host-origin ADB after source selection.
        # Scope this policy to packets that actually ENTER from the owned bridge.
        run(policy_rule_argv('add'))
        s['rule'] = True; s['policy_iif'] = True; record(s)
        if knob.read_text().strip() != '1':
            knob.write_text('1'); s['forwarding'] = True; record(s)
        rules = f'''table inet {TABLE} {{
 chain inbound {{ type filter hook input priority -110; policy accept;
  iifname "{BRIDGE}" udp dport {{ 53, 67 }} accept
  iifname "{BRIDGE}" tcp dport 53 accept
  iifname "{BRIDGE}" ct state established,related accept
  iifname "{BRIDGE}" drop
 }}
 chain forwarding {{ type filter hook forward priority -110; policy accept;
  ip daddr {SUBNET} iifname != "{BRIDGE}" ct state established,related accept
  ip daddr {SUBNET} iifname != "{BRIDGE}" drop
  iifname "{BRIDGE}" ip daddr {{ 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16 }} drop
  iifname "{BRIDGE}" oifname "{UPLINK}" accept
  iifname "{BRIDGE}" drop
 }}
 chain nat {{ type nat hook postrouting priority srcnat; policy accept;
  ip saddr {SUBNET} oifname "{UPLINK}" masquerade
 }}
}}
'''
        run(['nft', '-f', '-'], input=rules); s['nft'] = True; record(s)
        argv = ['dnsmasq', '--conf-file=/dev/null', '--no-hosts', '--no-resolv',
                '--bind-interfaces', '--interface=' + BRIDGE, '--except-interface=lo',
                '--listen-address=192.168.240.1', '--dhcp-range=192.168.240.2,192.168.240.254,12h',
                '--dhcp-option=3,192.168.240.1', '--dhcp-option=6,192.168.240.1',
                '--server=223.5.5.5@wlan0', '--server=1.1.1.1@wlan0',
                '--pid-file=' + str(BASE / 'dnsmasq.pid'),
                '--dhcp-leasefile=' + str(BASE / 'leases'), '--user=nobody']
        run(argv)
        print('NETWORK_READY', BRIDGE, str(SUBNET), flush=True)
    except BaseException:
        stop()
        raise

if __name__ == '__main__':
    if os.geteuid() != 0:
        raise SystemExit('requires root')
    if sys.argv[1:] == ['start']:
        start()
    elif sys.argv[1:] == ['stop']:
        stop()
    else:
        raise SystemExit('usage: waydroid_lab_network.py start|stop')
