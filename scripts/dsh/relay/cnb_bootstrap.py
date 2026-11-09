"""Provision only the DSH relay processes inside the already-running CNB environment."""
import fcntl
import hashlib
import json
import os
import pathlib
import socket
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
STATE = pathlib.Path.home() / '.kebab-dshrelay'
STATE.mkdir(mode=0o700, exist_ok=True)
os.chmod(STATE, 0o700)
with (STATE / 'lock').open('w') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    config = json.load(sys.stdin)
    assert set(config) == {'secret', 'token'}
    assert all(len(v) == 64 and set(v) <= set('0123456789abcdef') for v in config.values())
    raw = json.dumps(config, sort_keys=True).encode()
    target = STATE / 'config.json'
    if not target.exists() or target.read_bytes() != raw:
        temporary = STATE / 'config.new'
        temporary.write_bytes(raw)
        temporary.chmod(0o600)
        temporary.replace(target)
    pidfile = STATE / 'supervisor.pid'
    running = False
    if pidfile.exists():
        try:
            pid = int(pidfile.read_text())
            running = str(HERE / 'cnb_supervisor.py').encode() in (pathlib.Path('/proc') / str(pid) / 'cmdline').read_bytes()
        except (ValueError, OSError):
            pass
    if not running:
        for port in [9011, 9013]:
            with socket.socket() as probe:
                probe.bind(('0.0.0.0', port))
        process = subprocess.Popen([sys.executable, str(HERE / 'cnb_supervisor.py')],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        # PID publication belongs to the supervisor that owns the runtime lock.
    ready = False
    for _ in range(40):
        try:
            for port in [9011, 9013]:
                socket.create_connection(('127.0.0.1', port), timeout=0.3).close()
            ready = True
            break
        except OSError:
            time.sleep(0.25)
    print(json.dumps({'ready': ready, 'new_supervisor': not running}))
