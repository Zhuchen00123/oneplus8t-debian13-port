"""Reuse CNB discovery without changing the original controller, repo, or its processes."""
import base64
import hashlib
import json
import os
import pathlib
import re
import shlex
import signal
import subprocess
import time
import urllib.request

ROOT = pathlib.Path('/srv/kebab-dshrelay')
PRIVATE = ROOT / 'private'
STATUS = pathlib.Path('/run/kebab-dshrelay/status.json')
CONFIG = json.loads((PRIVATE / 'config.json').read_text())
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
alive = True

def stop(*_):
    global alive
    alive = False

for signum in [signal.SIGTERM, signal.SIGINT]:
    signal.signal(signum, stop)

def atomic(path, text, mode=0o600):
    temporary = path.with_suffix('.new')
    temporary.write_text(text)
    temporary.chmod(mode)
    temporary.replace(path)

def api(path):
    request = urllib.request.Request('https://api.cnb.cool' + path,
                                    headers={'Authorization': 'Bearer ' + CONFIG['cnb_token']})
    with OPENER.open(request, timeout=15) as response:
        return json.load(response)

def cycle():
    original = json.loads(pathlib.Path('/srv/kebab-strelay/private/status.json').read_text())
    if not original.get('ready') or not 0 <= time.time() - original.get('checked_at', 0) < 180:
        raise RuntimeError('Existing environment is not ready')
    match = re.fullmatch(r'https://([A-Za-z0-9-]+)-9003\.cnb\.run/', original['https_url'])
    if not match:
        raise ValueError('Unexpected existing endpoint')
    prefix = match.group(1)
    detail = api('/' + CONFIG['repo'] + '/-/workspace/detail/' + original['workspace_sn'])
    parts = shlex.split(detail['ssh'])
    if not parts or parts[0] != 'ssh':
        raise ValueError('Unexpected SSH metadata')
    port = int(parts[parts.index('-p') + 1]) if '-p' in parts else 22
    destination = next(p for p in parts[1:] if '@' in p and not p.startswith('-'))
    if not re.fullmatch(r'[A-Za-z0-9_.@-]+', destination):
        raise ValueError('Invalid SSH destination')
    ssh = ['ssh', '-F', '/dev/null', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
           '-o', 'HostKeyAlias=kebab-cnb-relay', '-o', 'UserKnownHostsFile=' + str(PRIVATE / 'known_hosts'),
           '-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=15', '-p', str(port), destination]
    files = {}
    for name in ['cnb_bootstrap.py', 'cnb_supervisor.py']:
        raw = (ROOT / name).read_bytes()
        files[name] = {'content': base64.b64encode(raw).decode(), 'sha256': hashlib.sha256(raw).hexdigest()}
    # The fixed receiver writes only our two files in our private namespace, then invokes our bootstrap.
    receiver = (
        "import sys,json,pathlib,base64,hashlib,subprocess,os; p=json.load(sys.stdin); "
        "d=pathlib.Path('/tmp/kebab-dshrelay'); d.mkdir(mode=0o700,exist_ok=True); "
        "os.chmod(d,0o700); "
        "exec(\"for name,row in p['files'].items():\\n assert name in ['cnb_bootstrap.py','cnb_supervisor.py']\\n "
        "raw=base64.b64decode(row['content']); assert hashlib.sha256(raw).hexdigest()==row['sha256']\\n "
        "target=d/name; target.write_bytes(raw); target.chmod(0o600)\"); "
        "r=subprocess.run([sys.executable,str(d/'cnb_bootstrap.py')],input=json.dumps(p['credentials']),"
        "text=True,capture_output=True,timeout=20); sys.stdout.write(r.stdout); sys.exit(r.returncode)"
    )
    payload = {'files': files, 'credentials': {'secret': CONFIG['secret'], 'token': CONFIG['token']}}
    result = subprocess.run(ssh + ['python3 -c ' + shlex.quote(receiver)], input=json.dumps(payload),
                            capture_output=True, text=True, timeout=45)
    if result.returncode != 0 or not json.loads(result.stdout).get('ready'):
        raise RuntimeError('DSH relay provisioning failed')
    env = ('MCB_HOST=' + prefix + '-9011.cnb.run\nMCB_PORT=443\nMCB_SECRET=' + CONFIG['secret'] +
           '\nMCB_SESSION=kebab-dsh-http\nMCB_MC=127.0.0.1:3082\n')
    path = PRIVATE / 'client.env'
    changed = not path.exists() or path.read_text() != env
    if changed:
        atomic(path, env)
        subprocess.run(['systemctl', 'restart', 'kebab-dshrelay-client'], check=True, capture_output=True)
    elif subprocess.run(['systemctl', 'is-active', '--quiet', 'kebab-dshrelay-client']).returncode:
        subprocess.run(['systemctl', 'start', 'kebab-dshrelay-client'], check=True, capture_output=True)
    url = 'https://' + prefix + '-9013.cnb.run/'
    update = subprocess.run(['/usr/sbin/runuser', '-u', 'dsh', '--', 'python3', str(ROOT / 'update_public_url.py'), url],
                            check=True, capture_output=True, text=True)
    if json.loads(update.stdout)['changed']:
        subprocess.run(['systemctl', 'restart', 'kebab-dsh'], check=True, capture_output=True)
    atomic(STATUS, json.dumps({'ready': True, 'checked_at': int(time.time()), 'https_url': url,
                              'address_changed': changed}), 0o644)

previous = None
while alive:
    try:
        cycle()
        state = 'connected'
    except Exception as error:
        state = 'waiting:' + type(error).__name__
        atomic(STATUS, json.dumps({'ready': False, 'checked_at': int(time.time()), 'error_type': type(error).__name__}), 0o644)
    if state != previous:
        print(state, flush=True)
        previous = state
    for _ in range(60):
        if not alive:
            break
        time.sleep(1)
