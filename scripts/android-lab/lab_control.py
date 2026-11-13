#!/usr/bin/env python3
"""Manual r4 lab lifecycle. No flashing, reboot, global DNS or main route edits.
Installation/initialization are separate; start requires the audited transient unit.
"""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import subprocess
import time
import uuid

ROOT=Path('/srv/waydroid-r4-lab-20261112')
USER=Path('/srv/waydroid-r4-user-20261112')
KERNEL='7.2.0-kebab-debian13-r4-venus+'
LXC=['lxc-attach','-P','/var/lib/waydroid/lxc','-n','waydroid','--']
SERVICE='kebab-waydroid-r4-lab.service'
SESSION='kebab-waydroid-r4-session.service'
PAYLOAD='kebab-waydroid-r4-payload.scope'

def run(argv,check=True,timeout=45,**kw):
    p=subprocess.run(argv,capture_output=True,text=True,timeout=timeout,**kw)
    if check and p.returncode:
        raise RuntimeError(f'{argv!r}: rc={p.returncode}: {p.stderr}')
    return p

def root(argv,check=True):
    return run(LXC+argv,check=check,timeout=15).stdout.strip()

def resource_guard():
    mem={x.split(':')[0]:int(x.split()[1])*1024 for x in Path('/proc/meminfo').read_text().splitlines()}
    if mem['MemAvailable']<3*1024**3:
        raise RuntimeError('server memory headroom below 3 GiB')
    for p in Path('/sys/class/power_supply').glob('bq*/temp'):
        if int(p.read_text())>=420:
            raise RuntimeError('battery temperature at/above 42 C: stop lab')

def normalized_adb_keys(text):
    return {line.split()[0] for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')}

def snapshot_nodes():
    marker=ROOT/'active-attempt.json'
    if marker.exists():
        previous=json.loads(marker.read_text())
        if previous.get('state')!='restored':
            raise RuntimeError('unfinished previous node lease: stop it before taking a new snapshot')
    names=list(json.loads((ROOT/'host-node-baseline.json').read_text()))
    nodes={}
    for name in names:
        p=Path(name)
        if not p.exists():
            continue
        s=p.stat()
        if not stat.S_ISCHR(s.st_mode):
            raise RuntimeError('not a real character device: '+name)
        acl=base64.b64encode(os.getxattr(p,'system.posix_acl_access')).decode() if 'system.posix_acl_access' in os.listxattr(p) else None
        nodes[name]={'mode':stat.S_IMODE(s.st_mode),'uid':s.st_uid,'gid':s.st_gid,'rdev':s.st_rdev,'acl':acl}
    attempt=ROOT/'runs'/('attempt_'+uuid.uuid4().hex)
    attempt.mkdir(parents=True,exist_ok=False,mode=0o700)
    data={'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'nodes':nodes}
    (attempt/'node-baseline.json').write_text(json.dumps(data,indent=2))
    marker.write_text(json.dumps({'path':str(attempt.relative_to(ROOT)),'state':'leased'},indent=2))
    return attempt

def restore_nodes():
    marker=ROOT/'active-attempt.json'
    lease=json.loads(marker.read_text()) if marker.exists() else None
    if lease and lease.get('state')=='restored':
        return
    if lease:
        attempt=(ROOT/lease['path']).resolve()
        if not attempt.is_relative_to((ROOT/'runs').resolve()):
            raise RuntimeError('node lease path outside experiment run directory')
        data=json.loads((attempt/'node-baseline.json').read_text())
        rows=data['nodes'];old_boot=data['boot_id']
    else:
        rows=json.loads((ROOT/'host-node-baseline.json').read_text())
        old_boot=json.loads((ROOT/'before.json').read_text())['boot_id']
    same_boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()==old_boot
    if not same_boot:
        for name,d in rows.items():
            p=Path(name)
            if stat.S_IMODE(p.stat().st_mode)!=d['mode']:
                raise RuntimeError('new boot device mode differs: do not apply stale restoration '+name)
        return # Kernel recreated nodes; never replay old ACL snapshots into a different boot.
    for name,d in rows.items():
        p=Path(name);s=p.stat()
        if s.st_rdev!=d['rdev'] or s.st_uid!=d['uid'] or s.st_gid!=d['gid']:
            raise RuntimeError('host node ownership changed; refusing blind restore: '+name)
        os.chmod(p,d['mode'])
        if d['acl'] is not None:
            os.setxattr(p,'system.posix_acl_access',base64.b64decode(d['acl']))
        assert stat.S_IMODE(p.stat().st_mode)==d['mode']
    if lease:
        proof=attempt/'nodes-restored.json'
        with proof.open('x') as f:json.dump({'utc':datetime.now(timezone.utc).isoformat(),'node_count':len(rows),'verified':True},f,indent=2)
        lease['state']='restored';marker.write_text(json.dumps(lease,indent=2))

def stop():
    run(['systemctl','stop',SESSION],check=False)
    run(['systemctl','stop',SERVICE],check=False)
    run(['systemctl','stop',PAYLOAD],check=False)
    state=run(['lxc-info','-P','/var/lib/waydroid/lxc','-n','waydroid','-sH'],check=False).stdout.strip()
    if state not in ['STOPPED','']:
        raise RuntimeError('container not stopped; refusing permission restoration')
    run(['python3',str(ROOT/'bin/waydroid_lab_network.py'),'stop'])
    restore_nodes()
    run(['adb','-P','5038','kill-server'],check=False)
    current=ROOT/'current-android.json'
    if current.exists():
        cached=json.loads(current.read_text())
        cached.update(framework_boot_completed=False,adb_authenticated=False,stopped_utc=datetime.now(timezone.utc).isoformat())
        current.write_text(json.dumps(cached,indent=2))
    if Path('/sys/class/net/waydroid0').exists():
        raise RuntimeError('bridge residue after stop')
    print('STOP_VERIFIED: container stopped, owned network absent, host device modes restored')

def start():
    if (ROOT/'HOLD_UNEXPECTED_RESET').exists():
        raise RuntimeError('unexpected host reset evidence preserved: further Android runs require a reviewed maintenance window')
    if os.uname().release!=KERNEL or not os.path.ismount('/var/lib/waydroid') or not os.path.samefile('/var/lib/waydroid',ROOT/'state'):
        raise RuntimeError('wrong kernel or missing experimental data mount')
    resource_guard()
    if not Path('/srv/waydroid-r4-user-20261112/runtime/waydroid-lab').is_socket():
        raise RuntimeError('start the separate bounded Wayland compositor first')
    if not Path('/var/lib/waydroid/waydroid.cfg').exists():
        raise RuntimeError('verified images must be initialized first')
    if run(['systemctl','is-active',SERVICE],check=False).stdout.strip()=='active':
        raise RuntimeError('already active: do not snapshot experiment permissions as a new baseline')
    attempt=snapshot_nodes()
    try:
        run(['systemctl','start',SERVICE])
        session=['systemd-run','--collect','--unit='+SESSION,'--uid=1000','--property=CPUQuota=100%',
                 '--property=MemoryMax=512M','--property=RuntimeMaxSec=1800',
                 '--setenv=XDG_RUNTIME_DIR=/srv/waydroid-r4-user-20261112/runtime','--setenv=WAYLAND_DISPLAY=waydroid-lab',
                 '--setenv=XDG_DATA_HOME='+str(USER/'data'),
                 '--setenv=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus',
                 '--setenv=HOME='+str(USER),'/usr/bin/waydroid','-l',str(USER/'logs/session.log'),'session','start']
        run(session)
        began=time.monotonic()
        deadline=began+300
        boot=''
        seen_running=False
        containment_verified=False
        while time.monotonic()<deadline:
            resource_guard()
            state=run(['lxc-info','-P','/var/lib/waydroid/lxc','-n','waydroid','-sH'],check=False).stdout.strip()
            if state=='RUNNING':
                seen_running=True
                if not containment_verified:
                    pid=run(['lxc-info','-P','/var/lib/waydroid/lxc','-n','waydroid','-pH']).stdout.strip()
                    if not pid.isdecimal():
                        raise RuntimeError('no valid init PID for resource containment proof')
                    cgroup=Path('/proc',pid,'cgroup').read_text().strip().split('::',1)[1]
                    if '/'+PAYLOAD+'/' not in cgroup:
                        raise RuntimeError('Android payload escaped its resource-limited parent: '+cgroup)
                    base=Path('/sys/fs/cgroup')/cgroup.lstrip('/')
                    limits={name:(base/name).read_text().strip() for name in ['memory.current','memory.max','cpu.max','pids.current','pids.max']}
                    if limits['memory.max']=='max' or int(limits['memory.max'])>3*1024**3:
                        raise RuntimeError('Android memory limit not applied')
                    (attempt/'resource-containment.json').write_text(json.dumps({'cgroup':cgroup,'limits':limits},indent=2))
                    containment_verified=True
                probe=run(LXC+['/system/bin/getprop','sys.boot_completed'],check=False,timeout=15)
                boot=probe.stdout.strip()
                with (attempt/'boot-poll.jsonl').open('a') as f:f.write(json.dumps({'elapsed_s':time.monotonic()-began,'rc':probe.returncode,'out':probe.stdout,'err':probe.stderr})+'\n')
                if boot=='1':break
            elif seen_running:
                raise RuntimeError('container init exited before framework boot; inspect this attempt LXC/trace logs')
            if time.monotonic()-began>20 and state=='STOPPED':
                raise RuntimeError('LXC stopped during startup; inspect trace/exit evidence, do not wait out the framework deadline')
            if time.monotonic()-began>15 and run(['systemctl','is-active',SESSION],check=False).stdout.strip()!='active':
                raise RuntimeError('session exited before Android boot; inspect state/waydroid.log')
            time.sleep(2)
        if boot!='1':raise RuntimeError('Android framework did not reach sys.boot_completed=1')
        (attempt/'framework-boot.json').write_text(json.dumps({'utc':datetime.now(timezone.utc).isoformat(),'kernel':os.uname().release,'sys.boot_completed':boot,'containment_verified':containment_verified},indent=2))
        print('ANDROID_BOOT_COMPLETED=1',flush=True)
        keydir=ROOT/'adb-home';keydir.mkdir(mode=0o700,exist_ok=True)
        key=keydir/'adbkey'
        if not key.exists():run(['adb','-P','5038','keygen',str(key)])
        public=key.with_suffix('.pub').read_text()
        data=USER/'data/waydroid/data/misc/adb';data.mkdir(mode=0o750,parents=True,exist_ok=True)
        # Only a newly generated lab ADB public key enters Android, never user credentials.
        keys=data/'adb_keys'
        if keys.exists():
            existing=normalized_adb_keys(keys.read_text())
            if existing and existing!=normalized_adb_keys(public):
                raise RuntimeError('ADB authorized key MATERIAL drifted; refusing overwrite')
        keys.write_text(public.rstrip()+'\n');os.chown(keys,1000,2000);keys.chmod(0o640)
        root(['/system/bin/setprop','service.adb.tcp.port','5555'])
        root(['/system/bin/setprop','ctl.restart','adbd'])
        ip=root(['/system/bin/ip','-o','-4','addr','show','dev','eth0'])
        import re
        match=re.search(r'inet (192\.168\.240\.\d+)/',ip)
        if not match:raise RuntimeError('no lab DHCP address inside Android')
        serial=match[1]+':5555'
        env={**os.environ,'ADB_VENDOR_KEYS':str(key),'ANDROID_USER_HOME':str(keydir)}
        run(['adb','-P','5038','start-server'],env=env)
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            try:
                connected=run(['adb','-P','5038','connect',serial],check=False,env=env,timeout=6)
                state=run(['adb','-P','5038','-s',serial,'get-state'],check=False,env=env,timeout=5)
                with (attempt/'adb-connect.jsonl').open('a') as f:f.write(json.dumps({'connect_rc':connected.returncode,'connect_out':connected.stdout,'connect_err':connected.stderr,'state':state.stdout.strip()})+'\n')
                if state.stdout.strip()=='device':break
            except subprocess.TimeoutExpired:
                with (attempt/'adb-connect.jsonl').open('a') as f:f.write(json.dumps({'transport_timeout':True})+'\n')
            time.sleep(1)
        else:raise RuntimeError('ADB never became authenticated')
        report={'utc':datetime.now(timezone.utc).isoformat(),'kernel':os.uname().release,
                'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'framework_boot_completed':True,'adb_authenticated':True,'serial':serial,
                'abi':root(['/system/bin/getprop','ro.product.cpu.abi']),
                'release':root(['/system/bin/getprop','ro.build.version.release']),
                'network_ip':ip,'dns':root(['/system/bin/getprop','net.dns1']),
                'secure_adb':root(['/system/bin/getprop','ro.adb.secure'])}
        (attempt/'adb-boot.json').write_text(json.dumps(report,indent=2))
        (ROOT/'current-android.json').write_text(json.dumps(report,indent=2))
        print(json.dumps({key:value for key,value in report.items() if key!='boot_id'},indent=2))
    except BaseException:
        stop()
        raise

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['start','stop'])
    args=parser.parse_args()
    if os.geteuid()!=0:raise SystemExit('requires experimental host root')
    {'start':start,'stop':stop}[args.action]()
