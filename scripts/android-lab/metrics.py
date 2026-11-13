#!/usr/bin/env python3
"""Bounded coexistence sampler; safety guard stops only this lab when required."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import time

def call(argv):
    p=subprocess.run(argv,capture_output=True,text=True,timeout=8)
    return {'rc':p.returncode,'out':p.stdout.strip()}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seconds',type=int,default=180);p.add_argument('--interval',type=int,default=5);p.add_argument('--out',type=Path,required=True);p.add_argument('--require-android',action='store_true');a=p.parse_args()
    if not 10<=a.seconds<=1800 or not 1<=a.interval<=30:raise SystemExit('invalid bounded duration/interval')
    a.out.mkdir(parents=True,exist_ok=False,mode=0o700)
    boot=Path('/proc/sys/kernel/random/boot_id').read_text();begin=time.monotonic();end=begin+a.seconds;rows=[];previous=None
    names=['kebab-dsh','kebab-sillytavern','kebab-qqbot','kebab-charge-policy','kebab-sdx55-modem','kebab-gps','kebab-ssc-listener','kebab-venus']
    while True:
        now=time.monotonic();cpu=list(map(int,Path('/proc/stat').read_text().splitlines()[0].split()[1:9]));idle=cpu[3]+cpu[4];total=sum(cpu)
        mem={x.split(':')[0]:int(x.split()[1])*1024 for x in Path('/proc/meminfo').read_text().splitlines()}
        charge=json.loads(Path('/run/kebab-charge-policy/status.json').read_text())
        thermal=[]
        for t in Path('/sys/class/thermal').glob('thermal_zone*/temp'):
            try:thermal.append(int(t.read_text()))
            except OSError:pass
        row={'utc':datetime.now(timezone.utc).isoformat(),'elapsed_s':now-begin,'boot_same':Path('/proc/sys/kernel/random/boot_id').read_text()==boot,'mem_available':mem['MemAvailable'],'thermal_max_mC':max(thermal),
             'charge_temperature_C':charge['temperature_c'],'charge_owned':charge['snapshot']['owned'],'charge_error':charge['snapshot']['last_error'],'inhibit_verified':charge['inhibit_readback_verified'],
             'services':call(['systemctl','show',*names,'-p','Id','-p','ActiveState','-p','MainPID','-p','NRestarts']),
             'container_cgroup':call(['systemctl','show','kebab-waydroid-r4-payload.scope','-p','MemoryCurrent','-p','MemoryPeak','-p','TasksCurrent','-p','CPUUsageNSec','-p','ControlGroup']),
             'android_boot':call(['lxc-attach','-P','/var/lib/waydroid/lxc','-n','waydroid','--','/system/bin/getprop','sys.boot_completed'])}
        row['cpu_busy_percent']=None if previous is None else round(100*(1-(idle-previous[1])/(total-previous[0])),2)
        previous=(total,idle);rows.append(row)
        with (a.out/'samples.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
        if a.require_android and row['android_boot']['out']!='1':raise SystemExit('framework not ready during required-Android soak')
        if row['charge_temperature_C']>=42 or row['mem_available']<3*1024**3:
            if a.require_android:
                stopped=subprocess.run(['python3','/srv/waydroid-r4-lab-20261112/bin/lab_control.py','stop'],capture_output=True,text=True,timeout=90)
                (a.out/'guard-stop.json').write_text(json.dumps({'reason':'headroom/temperature','stop_rc':stopped.returncode,'stdout':stopped.stdout,'stderr':stopped.stderr},indent=2))
            raise SystemExit('headroom/temperature guard triggered')
        if time.monotonic()>=end:break
        time.sleep(min(a.interval,max(0,end-time.monotonic())))
    summary={'requested_seconds':a.seconds,'actual_span_seconds':rows[-1]['elapsed_s'],'samples':len(rows),'boot_unchanged':all(r['boot_same'] for r in rows),'framework_ready_samples':sum(r['android_boot']['out']=='1' for r in rows),'mem_available_min':min(r['mem_available'] for r in rows),'battery_temperature_max_C':max(r['charge_temperature_C'] for r in rows),'thermal_max_mC':max(r['thermal_max_mC'] for r in rows),'charge_all_owned_noerror':all(r['charge_owned']==1 and r['charge_error']==0 and r['inhibit_verified'] for r in rows),'cpu_busy_max_percent':max(r['cpu_busy_percent'] or 0 for r in rows)}
    (a.out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
