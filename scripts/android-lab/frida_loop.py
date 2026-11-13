#!/usr/bin/env python3
"""Own APK only: launch -> matched Frida hook -> real UI -> assert -> cleanup.
Requires root on the experimental Debian/Android lab, not a production app.
frida-tools is not required: match the Python frida library to frida-server.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import threading
import time

from agent_loop import Agent, PACKAGE, resolve_serial

VERSION = '16.7.19'
LXC = ['lxc-attach', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '--']
SERVER = '/data/local/tmp/o8t-frida-server'
HOOK = '''
Java.perform(function () {
  const App = Java.use('org.oneplus8t.agentlab.MainActivity');
  const original = App.transform.overload('java.lang.String');
  original.implementation = function (value) {
    const start = Date.now();
    const result = original.call(this, value);
    send({kind: 'call', method: 'MainActivity.transform(String)',
          input: String(value), result: String(result), utc_ms: Date.now(), elapsed_ms: Date.now()-start});
    return result;
  };
  send({kind: 'ready'});
});
'''

def root(argv, check=True):
    p = subprocess.run(LXC+argv, capture_output=True, text=True, timeout=15)
    if check and p.returncode:
        raise RuntimeError(f'root argv={argv!r}: {p.stderr}')
    return p.stdout.strip()

def server_pids():
    text = root(['/system/bin/pidof', 'o8t-frida-server'], check=False)
    pids = [int(x) for x in text.split()]
    for pid in pids:
        if root(['/system/bin/readlink', f'/proc/{pid}/exe']) != SERVER:
            raise RuntimeError('foreign server PID: do not kill')
    return pids

def main():
    import frida
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--serial', default='auto')
    p.add_argument('--apk', type=Path, default=Path('/srv/waydroid-r4-downloads-20261112/o8t-agent-lab-build2.apk'))
    p.add_argument('--server', type=Path, default=Path('/srv/waydroid-r4-lab-20261112/tools/frida-server'))
    p.add_argument('--out', type=Path, default=None)
    p.add_argument('--adb-port', type=int, default=5038)
    args = p.parse_args()
    args.serial = resolve_serial(args.serial)
    args.out = args.out or Path('/srv/waydroid-r4-lab-20261112/runs')/('frida_'+str(time.time_ns()))
    if os.geteuid() != 0 or frida.__version__ != VERSION:
        raise SystemExit('experimental root + Python frida=='+VERSION+' required')
    agent = Agent(args.serial, args.out, port=args.adb_port)
    session = script = process = None
    forward = None
    events = []
    ready = threading.Event()
    verdict = {'passed':False,'client_version':frida.__version__}
    cleanup = {}
    log = (agent.out/'server.log').open('wb')
    try:
        agent.install(args.apk)
        agent.call(['push', str(args.server.resolve()), SERVER])
        root(['/system/bin/chmod', '700', SERVER])
        actual_version = root([SERVER, '--version'])
        if actual_version != VERSION:
            raise RuntimeError('Frida client library/server version mismatch: '+actual_version)
        verdict['server_version'] = actual_version
        if server_pids():
            raise RuntimeError('preexisting Frida server: refusing duplicate/reuse')
        agent.launch()
        pid_text = agent.call(['shell','pidof', PACKAGE]).strip()
        if not pid_text.isdecimal():
            raise RuntimeError('ambiguous app PID')
        app_pid = int(pid_text)
        process = subprocess.Popen(LXC+[SERVER, '--listen=127.0.0.1:27042'], stdout=log, stderr=log)
        forward_text = agent.call(['forward','tcp:0','tcp:27042']).strip()
        if not forward_text.isdecimal():
            raise RuntimeError('invalid allocated loopback ADB forward port')
        forward = int(forward_text)
        device = frida.get_device_manager().add_remote_device('127.0.0.1:'+str(forward))
        deadline = time.monotonic()+15
        while True:
            try:
                session = device.attach(app_pid)
                break
            except frida.ServerNotRunningError:
                if process.poll() is not None or time.monotonic()>deadline:
                    raise
                time.sleep(0.2)
        script = session.create_script(HOOK)
        def message(msg, data):
            events.append({'received_epoch':time.time(),'message':msg})
            if msg.get('type') == 'send' and msg.get('payload',{}).get('kind') == 'ready':
                ready.set()
        script.on('message',message)
        script.load()
        if not ready.wait(15):
            raise RuntimeError('Java hook never became ready')
        result = agent.operate()
        deadline = time.monotonic()+5
        while time.monotonic()<deadline and not any(x['message'].get('payload',{}).get('input')==result['token'] for x in events):
            time.sleep(0.1)
        calls = [x['message']['payload'] for x in events if x['message'].get('type')=='send' and x['message'].get('payload',{}).get('kind')=='call' and x['message']['payload'].get('input')==result['token']]
        if len(calls)!=1 or calls[0]['result']!=result['actual']:
            raise AssertionError('UI result not correlated with exactly one actual method call')
        if not result['started_epoch']-1 <= calls[0]['utc_ms']/1000 <= result['finished_epoch']+1:
            raise AssertionError('hook time outside UI action window')
        verdict.update(passed=True,ui=result,observed=calls[0])
    except Exception as exc:
        verdict.update(error=str(exc),category='tool version / permission / app: see event and server logs')
    finally:
        for name, action in [('unload',lambda:script.unload() if script else None),('detach',lambda:session.detach() if session else None)]:
            try:action();cleanup[name]=True
            except Exception as exc:cleanup[name]=str(exc)
        try:
            pids=server_pids()
            for pid in pids:root(['/system/bin/kill','-TERM',str(pid)])
            if process:
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate();process.wait(timeout=5)
            cleanup['server_absent']=not server_pids()
            agent.call(['shell','am','force-stop',PACKAGE])
            cleanup['app_absent']=not agent.call(['shell','pidof',PACKAGE],check=False).strip()
            if forward is not None:
                agent.call(['forward','--remove','tcp:'+str(forward)])
            cleanup['owned_forward_removed']=True
        except Exception as exc:cleanup['error']=str(exc)
        log.close()
        verdict['cleanup']=cleanup
        if cleanup.get('server_absent') is not True or cleanup.get('app_absent') is not True or cleanup.get('owned_forward_removed') is not True:
            verdict['passed']=False
        (agent.out/'frida-events.json').write_text(json.dumps(events,indent=2))
        (agent.out/'frida-result.json').write_text(json.dumps(verdict,indent=2))
        print(json.dumps(verdict,indent=2))
    return 0 if verdict['passed'] else 1

if __name__=='__main__':
    raise SystemExit(main())
