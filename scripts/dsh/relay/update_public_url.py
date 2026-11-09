"""Runs as dsh, never as root: update the bridge's external-origin registration."""
import json
import pathlib
import re
import sys

url = sys.argv[1]
if not re.fullmatch(r'https://[A-Za-z0-9-]+-9013\.cnb\.run/', url):
    raise SystemExit('Invalid relay URL')
path = pathlib.Path('/srv/dsh/home/dsh-bridge/config.json')
config = json.loads(path.read_text())
changed = config.get('externalTunnel') != {'url': url}
if changed:
    config['externalTunnel'] = {'url': url}
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(config, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)
print(json.dumps({'changed': changed}))
