#!/usr/bin/env python3
"""Fresh status alone must not claim hardware charge inhibition."""
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest

p = Path(__file__).resolve().parents[1] / 'scripts/linux/kebab-health.py'
spec = importlib.util.spec_from_file_location('health', p)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.device = self.root / 'sys/bus/i2c/devices/5-005c'
        self.device.mkdir(parents=True)
        self.runtime = self.root / 'run/kebab-charge-policy'
        self.runtime.mkdir(parents=True)
        self.state = {'version': 1, 'monotonic_seconds': time.monotonic(), 'control_enabled': True,
                      'inhibit_readback_verified': True, 'stop_percent': 80, 'resume_percent': 75}
        self.snapshot = 'reg08=0x06 last_error=0 owned=1 allow_control=1 recovering=0'
        self.lease = '85'

    def sample(self):
        (self.runtime / 'status.json').write_text(json.dumps(self.state))
        (self.device / 'charge_test_snapshot').write_text(self.snapshot)
        (self.device / 'charge_control_lease').write_text(self.lease)
        return mod.charge_control_status(self.root / 'sys', self.root / 'run')

    def test_verified_current_inhibit(self):
        status = self.sample()
        self.assertTrue(status['inhibit_readback_verified'])
        self.assertFalse(status['full_charge_cycle_validated'])

    def test_stale_or_future_status_rejected(self):
        for offset in (-60, 10):
            self.state['monotonic_seconds'] = time.monotonic() + offset
            self.assertFalse(self.sample()['inhibit_readback_verified'])

    def test_status_claim_rejected_by_actual_readback(self):
        for replacement in ['reg08=0x16', 'owned=0', 'allow_control=0', 'recovering=1', 'last_error=-5']:
            key = replacement.split('=')[0]
            original = self.snapshot
            self.snapshot = ' '.join(replacement if x.startswith(key + '=') else x for x in original.split())
            self.assertFalse(self.sample()['inhibit_readback_verified'])
            self.snapshot = original

    def test_expired_or_invalid_lease_not_verified(self):
        for value in ('0', 'NaN', '91'):
            self.lease = value
            self.assertFalse(self.sample()['inhibit_readback_verified'])

    def test_error_or_stopped_controller_not_active(self):
        self.state['error'] = 'I2C failed'
        self.assertFalse(self.sample()['policy_active'])
        self.state.pop('error')
        self.state['control_enabled'] = False
        self.assertFalse(self.sample()['policy_active'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
