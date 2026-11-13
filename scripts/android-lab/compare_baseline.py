#!/usr/bin/env python3
"""Compare private device baseline with a new capture; export necessary facts only."""
import argparse
import json
from pathlib import Path
import re

def output(d,key):return d.get(key,{}).get('stdout','')
def routes(d,key):
    rows=json.loads(output(d,key) or '[]')
    return sorted(json.dumps({k:v for k,v in r.items() if k not in ['expires','cache','metrics']},sort_keys=True) for r in rows)
def units(d):
    answer={}
    for block in output(d,'units').split(chr(10)*2):
        row=dict(x.split('=',1) for x in block.splitlines() if '=' in x)
        if 'Id' in row:answer[row['Id']]=row
    return answer
def linknames(d):return sorted(x['ifname'] for x in json.loads(output(d,'links') or '[]'))
def listeners(d):
    # Port + service only; do not export host addressing/credentials.
    answer=[]
    for line in output(d,'listeners').splitlines()[1:]:
        cols=line.split()
        if len(cols)<5:continue
        port=cols[4].rsplit(':',1)[-1]
        comms=sorted(set(re.findall(r'[(][(]"([^"]+)"',line)))
        answer.append((cols[0],port,comms))
    return sorted(answer,key=str)
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--before',type=Path,required=True);p.add_argument('--after',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    before=json.loads(a.before.read_text());after=json.loads(a.after.read_text());u1=units(before);u2=units(after)
    services={n:{'before':v,'after':u2.get(n),'unchanged':v==u2.get(n)} for n,v in u1.items()}
    oldlog=output(before,'kmsg');newlog=output(after,'kmsg');oldlines=set(oldlog.splitlines());added=[x for x in newlog.splitlines() if x not in oldlines]
    fatal=re.compile(r'Kernel panic|BUG:|Oops:|out of memory|oom-kill|ERR_FATAL|Device died|GPU fault|SMMU.*fault',re.I)
    result={'before_utc':before['utc'],'after_utc':after['utc'],'same_boot':before['boot_id']==after['boot_id'],'kernel_unchanged':before['kernel']==after['kernel'],
            'route4_equal':routes(before,'routes')==routes(after,'routes'),'route6_equal':routes(before,'routes6')==routes(after,'routes6'),'dns_equal':before['dns']==after['dns'],
            'interfaces_equal':linknames(before)==linknames(after),'nft_equal':output(before,'nft')==output(after,'nft'),'iptables_equal':(output(before,'iptables')==output(after,'iptables')) if before.get('iptables',{}).get('rc')==0 and after.get('iptables',{}).get('rc')==0 else None,
            'listeners_equal':listeners(before)==listeners(after),'listeners_before':listeners(before),'listeners_after':listeners(after),'services':services,
            'new_kernel_warning_error_lines':len(added),'new_fatal_signature_count':sum(bool(fatal.search(x)) for x in added),
            'memory_before':output(before,'memory'),'memory_after':output(after,'memory'),'disk_before':output(before,'disk'),'disk_after':output(after,'disk'),
            'binder_before':before['binder'],'binder_after':after['binder'],'binder_equal':before['binder']==after['binder'],
            'failed_before':output(before,'failed'),'failed_after':output(after,'failed')}
    result['protected_baseline_pass']=all(result[k] for k in ['same_boot','kernel_unchanged','route4_equal','route6_equal','dns_equal','interfaces_equal','nft_equal','iptables_equal','listeners_equal','binder_equal']) and all(x['unchanged'] for x in services.values()) and result['new_fatal_signature_count']==0
    if a.out.exists():raise SystemExit('refuse overwrite comparison')
    a.out.write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    return 0 if result['protected_baseline_pass'] else 1

if __name__=='__main__':raise SystemExit(main())
