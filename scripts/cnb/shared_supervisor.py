"""Additional services in the existing ST/SSH workspace; credentials arrive by SSH."""
import fcntl, hashlib, json, os, pathlib, signal, subprocess, time

STATE = pathlib.Path.home() / '.kebab-shared-relay'
HERE = pathlib.Path(__file__).resolve().parent
STATE.mkdir(mode=0o700, exist_ok=True)
os.chmod(STATE, 0o700)
lock = (STATE / 'runtime.lock').open('w')
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(0)
(STATE / 'supervisor.pid').write_text(str(os.getpid()))
os.chmod(STATE / 'supervisor.pid', 0o600)
alive = True
children = {}

def stop(*_):
    global alive
    alive = False

def terminate():
    for child in children.values():
        if child.poll() is None:
            child.terminate()
    for child in children.values():
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    children.clear()

signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
fingerprint = None
try:
    while alive:
        config = STATE / 'config.json'
        if not config.exists():
            time.sleep(2)
            continue
        raw = config.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != fingerprint:
            terminate()
            fingerprint = digest
            c = json.loads(raw)
            binary = HERE.parent / 'bin/mcbridge-linux-amd64'
            mcp = HERE / 'mcp-placeholder-amd64'
            cf = HERE / 'cloudflared-linux-amd64'
            for path, expected in [(binary, '72e099e6aa0195ee9795bd1b60159f30aefbfe18687211a927b4dedd1d4b8873'),
                                   (mcp, c['mcp_binary_sha256']), (cf, c['cf_binary_sha256'])]:
                if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise RuntimeError('Binary checksum mismatch')
                os.chmod(path, 0o755)
        c = json.loads(raw)
        jobs = {
            'mc': ([str(binary), 'serve', '--port', '9010', '--tun-secret', c['mc_secret'], '--fwd-token', c['mc_token']], None),
            'mcp-placeholder': ([str(mcp), 'serve', '--port', '9000'], None),
            'cloudflare': ([str(cf), 'tunnel', '--no-autoupdate', '--protocol', 'http2', 'run'],
                           dict(os.environ, TUNNEL_TOKEN=c['cf_token']))
        }
        for name, (args, env) in jobs.items():
            if name not in children or children[name].poll() is not None:
                children[name] = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL,
                                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
finally:
    terminate()
