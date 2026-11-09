"""Independent DSH byte relay; never stores DSH application credentials or payloads."""
import hashlib
import fcntl
import json
import os
import pathlib
import signal
import subprocess
import time

STATE = pathlib.Path.home() / '.kebab-dshrelay'
STATE.mkdir(mode=0o700, exist_ok=True)
lock = (STATE / 'runtime.lock').open('w')
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(0)
(STATE / 'supervisor.pid').write_text(str(os.getpid()))
(STATE / 'supervisor.pid').chmod(0o600)
BIN = pathlib.Path('/workspace/bin/mcbridge-linux-amd64')
EXPECTED_BINARY = '72e099e6aa0195ee9795bd1b60159f30aefbfe18687211a927b4dedd1d4b8873'
assert hashlib.sha256(BIN.read_bytes()).hexdigest() == EXPECTED_BINARY
alive = True
children = {}
fingerprint = None

def stop(*_):
    global alive
    alive = False

for signum in [signal.SIGTERM, signal.SIGINT]:
    signal.signal(signum, stop)

def terminate():
    for process in children.values():
        if process.poll() is None:
            process.terminate()
    for process in children.values():
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    children.clear()

try:
    while alive:
        raw = (STATE / 'config.json').read_bytes()
        current = hashlib.sha256(raw).hexdigest()
        if current != fingerprint:
            terminate()
            fingerprint = current
        config = json.loads(raw)
        jobs = {
            'tunnel': [str(BIN), 'serve', '--port', '9011', '--tun-secret', config['secret'], '--fwd-token', config['token']],
            'web': [str(BIN), 'forward', '--host', '127.0.0.1', '--port', '9011', '--token', config['token'],
                    '--listen', '0.0.0.0:9013', '--plain', '--no-gui'],
        }
        for name, args in jobs.items():
            if name not in children or children[name].poll() is not None:
                children[name] = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
finally:
    terminate()
