#!/usr/bin/env python3
"""Select a UI view within the same user session; never inject terminal input."""
import argparse
import json
import os
from pathlib import Path
import time

parser = argparse.ArgumentParser()
parser.add_argument('mode', choices=('terminal', 'panel', 'desktop', 'status'))
args = parser.parse_args()
runtime = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))
if args.mode == 'status':
    print((runtime / 'kebab-three-mode.json').read_text())
else:
    request = runtime / 'kebab-three-mode-request.json'
    temporary = request.with_suffix('.tmp')
    temporary.write_text(json.dumps({'mode': args.mode, 'monotonic_seconds': time.monotonic()}) + '\n')
    os.chmod(temporary, 0o600)
    temporary.replace(request)
