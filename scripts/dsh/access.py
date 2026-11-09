"""Open DSH through the existing authenticated relay and strict SSH host verification."""
import argparse
import http.cookiejar
import json
import pathlib
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
import webbrowser

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/sillytavern'))
from access import PRIVATE, discover
import wss_forwarder


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='Verify the local forwarded Web endpoint, then exit')
    parser.add_argument('--web-port', type=int, default=3080)
    args = parser.parse_args()
    if not 1 <= args.web_port <= 65535:
        raise SystemExit('Invalid local Web port')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.web_port))
    client_id = 'dsh-access-' + uuid.uuid4().hex[:12]
    original_connect = wss_forwarder.bridge.WsStream.connect
    wss_forwarder.bridge.WsStream.connect = staticmethod(
        lambda host, port, path: original_connect(host, port, path.replace('/st-maintenance/', '/' + client_id + '/'))
    )
    cfg = json.loads((PRIVATE / 'relay-private.json').read_text(encoding='utf8'))
    endpoint = discover()
    forwarder = None
    tunnel = None
    try:
        forwarder = wss_forwarder.Forwarder(endpoint['ssh_wss_host'], cfg['ssh_token'], 0)
        config = ROOT / 'local/ssh/kebab-server.conf'
        resolved = subprocess.check_output(['ssh', '-G', '-F', str(config), 'kebab'], text=True)
        ssh_config = dict(line.split(' ', 1) for line in resolved.splitlines() if ' ' in line)
        key_alias = ssh_config.get('hostkeyalias', ssh_config['hostname'])
        options = ['-F', str(config), '-o', 'HostName=127.0.0.1', '-o', 'BindAddress=127.0.0.1',
                   '-o', 'HostKeyAlias=' + key_alias, '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                   '-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=20', '-o', 'ServerAliveCountMax=3',
                   '-p', str(forwarder.port)]
        result = subprocess.run(['ssh', *options, 'kebab', 'kebab-dsh web-url'],
                                capture_output=True, text=True, timeout=40)
        if result.returncode:
            raise RuntimeError('Unable to obtain DSH launch URL through verified SSH')
        remote_url = result.stdout.strip()
        if not remote_url.startswith('http://127.0.0.1:3080/?token=') or '\n' in remote_url:
            raise RuntimeError('Unexpected launch URL')
        url = remote_url.replace('127.0.0.1:3080', '127.0.0.1:' + str(args.web_port), 1)
        tunnel = subprocess.Popen(['ssh', *options, '-o', 'ExitOnForwardFailure=yes', '-N',
                                   '-L', f'127.0.0.1:{args.web_port}:127.0.0.1:3080', 'kebab'],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(30):
            if tunnel.poll() is not None:
                raise RuntimeError('SSH Web tunnel exited')
            try:
                with socket.create_connection(('127.0.0.1', args.web_port), timeout=1):
                    break
            except OSError:
                time.sleep(0.5)
        else:
            raise RuntimeError('SSH Web tunnel was not ready')
        if args.check:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            )
            with opener.open(url, timeout=20) as response:
                print(json.dumps({'forwarded_web_status': response.status, 'html_bytes': len(response.read())}))
            return
        webbrowser.open(url)
        print('DSH 已在浏览器打开。保持此窗口运行；按 Ctrl+C 关闭本次本地隧道。', flush=True)
        while forwarder.alive.is_set() and tunnel.poll() is None:
            time.sleep(1)
        raise RuntimeError('Access tunnel disconnected; run the command again')
    finally:
        if tunnel is not None and tunnel.poll() is None:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel.kill()
                tunnel.wait(timeout=5)
        if forwarder is not None:
            forwarder.close()


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as error:
        raise SystemExit('DSH access failed (' + type(error).__name__ + '); check the relay and DSH service.')
