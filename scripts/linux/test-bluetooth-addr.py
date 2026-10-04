#!/usr/bin/env python3
"""Host-only regression of the address helper with fake btmgmt; no radio/NV I/O."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HELPER = Path(__file__).with_name('kebab-bluetooth-addr.sh')
MACHINE_ID = '0123456789abcdef0123456789abcdef'
MOCK = r'''#!/usr/bin/env python3
import json, os, stat, sys
from pathlib import Path
assert stat.S_ISFIFO(os.fstat(0).st_mode), 'stdin must be a pipe'
root = Path(os.environ['MOCK_ROOT'])
mode = os.environ['MOCK_MODE']
args = sys.argv[1:]
with (root/'calls').open('a') as f:
    f.write(json.dumps(args)+'\n')
state = root/'state'
if args == ['info']:
    if mode == 'configured':
        print('hci0: Primary controller')
    elif state.exists():
        n = int(state.read_text())
        state.write_text(str(n+1))
        if n >= 1:
            print('hci0: Primary controller')
elif args == ['config']:
    if not state.exists() and mode != 'absent':
        print('hci1: Unconfigured controller')
        print('    missing options: external-configuration')
        print('hci0: Unconfigured controller')
        missing = 'external-configuration' if mode == 'other' else 'public-address'
        print('    missing options: '+missing+'  ')
        print('hci2: Unconfigured controller')
        print('    missing options: external-configuration')
elif len(args) == 4 and args[:3] == ['--index', '0', 'public-addr']:
    if mode == 'reject':
        sys.exit(1)
    state.write_text('0')
else:
    sys.exit(2)
'''


class AddressHelperTests(unittest.TestCase):
    def exercise(self, mode, expected, dev='hci0', machine_id=MACHINE_ID):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mock = root/'btmgmt'
            mock.write_text(MOCK)
            mock.chmod(0o755)
            identity = root/'machine-id'
            identity.write_text(machine_id+'\n')
            # Keep production algorithm intact; substitute fixture paths and a
            # short poll window. The production command timeout stays bounded.
            text = HELPER.read_text().replace('/usr/bin/btmgmt', str(mock))
            text = text.replace('/etc/machine-id', str(identity)).replace('-lt 60', '-lt 3')
            script = root/'helper.sh'
            script.write_text(text)
            env = dict(os.environ, MOCK_ROOT=str(root), MOCK_MODE=mode)
            result = subprocess.run(['sh', str(script), dev], env=env,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, expected, result.stderr)
            calls_file = root/'calls'
            import json
            calls = [json.loads(line) for line in calls_file.read_text().splitlines()] if calls_file.exists() else []
            return calls

    def test_already_configured_preserved(self):
        self.assertEqual(self.exercise('configured', 0), [['info']])

    def test_async_reenumeration_and_correct_target_block(self):
        calls = self.exercise('async', 0)
        digest = hashlib.sha256((MACHINE_ID+'-kebab-bt').encode()).hexdigest()[:10]
        address = '02:'+':'.join(digest[i:i+2] for i in range(0, 10, 2))
        setters = [c for c in calls if c[0] == '--index']
        self.assertEqual(setters, [['--index', '0', 'public-addr', address]])
        self.assertGreaterEqual(calls.count(['info']), 3)
        self.assertTrue(all(c in (['info'], ['config']) or c in setters for c in calls))

    def test_other_missing_option_refused(self):
        self.assertEqual(self.exercise('other', 1), [['info'], ['config']])

    def test_invalid_identity_refused(self):
        self.assertEqual(self.exercise('async', 1, machine_id='invalid'), [['info'], ['config']])

    def test_controller_rejection_fails(self):
        self.assertEqual(len(self.exercise('reject', 1)), 3)

    def test_missing_controller_bounded(self):
        self.assertTrue(self.exercise('absent', 1))

    def test_invalid_device_refused_before_command(self):
        for dev in ('hci0x', 'hci', 'other', 'hci-1'):
            self.assertEqual(self.exercise('async', 2, dev=dev), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
