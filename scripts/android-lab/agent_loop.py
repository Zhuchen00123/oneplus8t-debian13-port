#!/usr/bin/env python3
"""Repeatable adb UI loop, restricted to our own offline test APK."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET

PACKAGE = 'org.oneplus8t.agentlab'
COMPONENT = PACKAGE + '/.MainActivity'

def resolve_serial(value, state=Path('/srv/waydroid-r4-lab-20261112/current-android.json'), boot_file=Path('/proc/sys/kernel/random/boot_id')):
    if value != 'auto':
        return value
    if not state.is_file():
        raise RuntimeError('no verified Android/ADB state; start must actually pass first')
    data=json.loads(state.read_text())
    if data.get('boot_id') != boot_file.read_text().strip() or data.get('adb_authenticated') is not True:
        raise RuntimeError('stale or unverified Android state; do not reuse across host boot')
    return data['serial']

def find_control(xml, name):
    if len(xml) > 2_000_000 or '<!DOCTYPE' in xml.upper():
        raise ValueError('invalid or oversized UI XML')
    nodes = [n for n in ET.fromstring(xml).iter('node')
             if n.get('resource-id') == PACKAGE + ':id/' + name and n.get('package') == PACKAGE]
    if len(nodes) != 1:
        raise ValueError(f'expected exactly one owned {name}; got {len(nodes)}')
    node = nodes[0]
    if node.get('enabled') != 'true':
        raise ValueError('disabled control')
    match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', node.get('bounds', ''))
    if not match:
        raise ValueError('invalid control bounds')
    x1, y1, x2, y2 = map(int, match.groups())
    if not (0 <= x1 < x2 <= 8192 and 0 <= y1 < y2 <= 8192):
        raise ValueError('empty or unreasonable bounds')
    return node, ((x1+x2)//2, (y1+y2)//2)

class Agent:
    def __init__(self, serial, out, adb='adb', port=5038):
        if not re.fullmatch(r'192\.168\.240\.\d{1,3}:5555', serial):
            raise ValueError('only private lab subnet ADB endpoints are permitted')
        self.prefix = [adb, '-P', str(port), '-s', serial]
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.commands = []

    def call(self, argv, binary=False, timeout=30, check=True):
        start = time.monotonic()
        p = subprocess.run(self.prefix + argv, capture_output=True, timeout=timeout)
        self.commands.append({'argv': self.prefix + argv, 'utc': datetime.now(timezone.utc).isoformat(),
                              'elapsed_s': time.monotonic()-start, 'rc': p.returncode,
                              'stdout': '<binary>' if binary else p.stdout.decode(errors='replace'),
                              'stderr': p.stderr.decode(errors='replace')})
        (self.out/'commands.json').write_text(json.dumps(self.commands, indent=2))
        if check and p.returncode:
            raise RuntimeError(f'{argv!r} rc={p.returncode}: {p.stderr.decode(errors="replace")}')
        return p.stdout if binary else p.stdout.decode(errors='replace')

    def install(self, apk):
        response = self.call(['install', '-r', '--no-streaming', str(Path(apk).resolve())], timeout=90)
        if 'Success' not in response:
            raise RuntimeError('APK installation was not confirmed')
        listed = self.call(['shell', 'pm', 'path', PACKAGE])
        if not listed.startswith('package:'):
            raise RuntimeError('installed APK missing from package manager')

    def launch(self):
        self.call(['shell', 'am', 'force-stop', PACKAGE])
        self.call(['shell', 'am', 'start', '-W', '-n', COMPONENT])
        self.assert_foreground()

    def assert_foreground(self):
        focus = self.call(['shell', 'dumpsys', 'activity', 'activities'])
        resumed = [x for x in focus.splitlines() if 'ResumedActivity' in x or 'topResumedActivity' in x]
        if not any(PACKAGE in x for x in resumed):
            raise RuntimeError('owned test app is not resumed')

    def dump(self, name):
        remote = '/data/local/tmp/o8t-agent-' + uuid.uuid4().hex + '.xml'
        try:
            self.call(['shell', 'uiautomator', 'dump', remote], timeout=40)
            text = self.call(['exec-out', 'cat', remote])
            ET.fromstring(text)
            (self.out/(name+'.xml')).write_text(text)
            return text
        finally:
            # Only the exact freshly generated temporary path.
            self.call(['shell', 'rm', '-f', remote], check=False)

    def operate(self, token=None):
        token = token or ('o8t_' + uuid.uuid4().hex)
        if not re.fullmatch(r'o8t_[0-9a-f]{32}', token):
            raise ValueError('unsafe/nonunique test token')
        started = time.time()
        self.assert_foreground()
        before = self.dump('before')
        _, xy = find_control(before, 'input_text')
        self.call(['shell', 'input', 'tap', *map(str, xy)])
        self.call(['shell', 'input', 'text', token])
        self.call(['shell', 'input', 'keyevent', '111']) # dismiss IME, not the app
        typed = self.dump('typed')
        input_node, _ = find_control(typed, 'input_text')
        if input_node.get('text') != token:
            raise AssertionError('input text not actually present')
        _, xy = find_control(typed, 'apply_button')
        self.call(['shell', 'input', 'tap', *map(str, xy)])
        after = self.dump('after')
        result, _ = find_control(after, 'result_text')
        expected = 'OK:' + token
        if result.get('text') != expected:
            raise AssertionError(f'actual UI result {result.get("text")!r} != {expected!r}')
        png = self.call(['exec-out', 'screencap', '-p'], binary=True)
        if not png.startswith(bytes.fromhex('89504e470d0a1a0a')) or len(png) < 100:
            raise AssertionError('invalid screenshot')
        (self.out/'screen.png').write_bytes(png)
        answer = {'passed': True, 'token': token, 'expected': expected, 'actual': result.get('text'),
                  'started_epoch': started, 'finished_epoch': time.time(),
                  'screenshot_sha256': hashlib.sha256(png).hexdigest(),
                  'locator': 'exact resource-id + package; tap uses current XML bounds, never fixed coordinates'}
        (self.out/'result.json').write_text(json.dumps(answer, indent=2))
        return answer

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--serial', default='auto')
    p.add_argument('--apk', type=Path, default=Path('/srv/waydroid-r4-downloads-20261112/o8t-agent-lab-build2.apk'))
    p.add_argument('--out', type=Path, default=None)
    p.add_argument('--rounds', type=int, default=3, choices=range(1,11))
    p.add_argument('--adb', default='adb')
    p.add_argument('--adb-port', type=int, default=5038)
    args = p.parse_args()
    args.serial = resolve_serial(args.serial)
    args.out = args.out or Path('/srv/waydroid-r4-lab-20261112/runs')/('agent_'+uuid.uuid4().hex)
    args.out.mkdir(parents=True, exist_ok=False, mode=0o700)
    results = []
    for n in range(args.rounds):
        agent = Agent(args.serial, args.out/f'round-{n+1}', args.adb, args.adb_port)
        try:
            if n == 0:
                agent.install(args.apk)
            agent.launch()
            result = agent.operate()
        except Exception as exc:
            result = {'passed': False, 'error': str(exc), 'category': 'agent/application/ADB: inspect commands.json'}
        results.append(result)
        (args.out/'summary.json').write_text(json.dumps({'attempts':len(results), 'successes':sum(x['passed'] for x in results),'rounds':results},indent=2))
    print(json.dumps({'attempts':len(results),'successes':sum(x['passed'] for x in results)}))
    return 0 if all(x['passed'] for x in results) else 1

if __name__ == '__main__':
    raise SystemExit(main())
