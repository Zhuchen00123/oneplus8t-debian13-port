"""Provision only the additional MC and existing MCP-placeholder/CF processes."""
import fcntl, hashlib, json, os, pathlib, socket, subprocess, sys, time, urllib.request

HERE = pathlib.Path(__file__).resolve().parent
STATE = pathlib.Path.home() / '.kebab-shared-relay'
STATE.mkdir(mode=0o700, exist_ok=True)
os.chmod(STATE, 0o700)
with (STATE / 'bootstrap.lock').open('w') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    c = json.load(sys.stdin)
    assert set(c) == {'mc_secret', 'mc_token', 'cf_token', 'cf_hostname', 'mcp_binary_sha256', 'cf_binary_sha256'}
    assert all(isinstance(v, str) and 0 < len(v) < 4096 for v in c.values())
    assert all(len(c[k]) == 64 and all(x in '0123456789abcdef' for x in c[k])
               for k in ['mcp_binary_sha256', 'cf_binary_sha256'])
    cf = HERE / 'cloudflared-linux-amd64'
    if not cf.exists():
        # Fixed release, and verify against the existing connector's exact checksum.
        tmp = HERE / 'cloudflared-download.tmp'
        with urllib.request.urlopen('https://github.com/cloudflare/cloudflared/releases/download/2026.9.3/cloudflared-linux-amd64', timeout=45) as response:
            tmp.write_bytes(response.read())
        assert hashlib.sha256(tmp.read_bytes()).hexdigest() == c['cf_binary_sha256']
        tmp.replace(cf)
    raw = json.dumps(c, sort_keys=True).encode()
    target = STATE / 'config.json'
    if not target.exists() or target.read_bytes() != raw:
        tmp = STATE / 'config.new'
        tmp.write_bytes(raw)
        os.chmod(tmp, 0o600)
        tmp.replace(target)
    running = False
    try:
        pid = int((STATE / 'supervisor.pid').read_text())
        running = str(HERE / 'supervisor.py').encode() in (pathlib.Path('/proc') / str(pid) / 'cmdline').read_bytes()
    except (OSError, ValueError):
        pass
    if not running:
        for port in [9000, 9010]:
            with socket.socket() as s:
                s.bind(('0.0.0.0', port))
        subprocess.Popen([sys.executable, str(HERE / 'supervisor.py')], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    ready = False
    for _ in range(60):
        try:
            for port in [9000, 9010]:
                socket.create_connection(('127.0.0.1', port), timeout=.3).close()
            ready = True
            break
        except OSError:
            time.sleep(.25)
    print(json.dumps({'ready': ready, 'new_supervisor': not running}))
