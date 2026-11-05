#!/usr/bin/env python3
"""Exercise the real 80/75 policy with stateful backend and sensor faults."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

path = Path(__file__).resolve().parents[1] / 'scripts/linux/kebab-charge-policy.py'
spec = importlib.util.spec_from_file_location('policy', path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class FakeBackend:
    def __init__(self):
        self.capacity = 79
        self.temp = 38
        self.online = 1
        self.calls = []
        self.snap = {'reg08': 0x16, 'reg09': 0x4c, 'reg13': 0x0f,
                     'owned': 0, 'last_error': 0, 'allow_control': 1,
                     'lease_seconds': 90, 'recovering': 0}
        self.fail = False
        self.bad_readback = False

    def sample(self):
        return {'capacity': self.capacity, 'temperature_c': self.temp,
                'online': self.online, 'snapshot': self.snapshot()}

    def snapshot(self):
        return copy.deepcopy(self.snap)

    def action(self, action):
        self.calls.append(action)
        if self.fail:
            raise OSError('I2C fault')
        if action == 'inhibit':
            self.snap['owned'] = 1
            if not self.bad_readback:
                self.snap['reg08'] &= ~0x10
        elif action == 'restore':
            self.snap['owned'] = 0
            self.snap['reg08'] |= 0x10


class Tests(unittest.TestCase):
    def setUp(self):
        self.b = FakeBackend()
        self.p = mod.Policy(mod.DEFAULT_CONFIG.copy())

    def step(self, count=1, control=True):
        for _ in range(count):
            result = mod.step(self.p, self.b, control)
        return result

    def test_real_hysteresis_and_no_toggle_on_renewal(self):
        self.b.capacity = 80
        self.step(2)
        self.assertEqual(self.b.calls, [])
        self.assertTrue(self.step()['inhibit_readback_verified'])
        self.b.capacity = 78
        self.step(240)  # Simulated one hour; only renewal actions.
        self.assertEqual(self.b.calls, ['inhibit'] + ['renew'] * 240)
        self.b.capacity = 75
        self.step(2)
        self.assertEqual(self.b.snap['owned'], 1)
        self.step()
        self.assertEqual(self.b.calls[-1], 'restore')
        self.assertEqual(self.b.snap['owned'], 0)

    def test_jitter_requires_consecutive_confirmation(self):
        for cap in [80, 79, 80, 81, 79, 80, 81]:
            self.b.capacity = cap
            self.step()
        self.assertEqual(self.b.calls, [])
        self.b.capacity = 81
        self.step()
        self.assertEqual(self.b.calls, ['inhibit'])

    def test_firmware_inhibit_not_enabled(self):
        self.b.snap['reg08'] = 0x06
        self.b.capacity = 10
        self.step(10)
        self.assertEqual(self.b.calls, [])

    def test_tcpm_offline_with_power_good_can_inhibit_and_renew(self):
        self.b.online = 0
        self.b.capacity = 80
        status = self.step(3)
        self.assertEqual(self.b.calls, ['inhibit'])
        self.assertEqual(status['requested_action'], 'inhibit')
        self.assertEqual(status['online'], 0)
        self.assertEqual(status['tcpm_online'], 0)
        self.assertTrue(status['mp2762_power_good'])
        self.assertEqual(status['input_evidence'], 'mp2762_reg13_power_good_bit1')

        self.b.capacity = 78
        status = self.step()
        self.assertEqual(self.b.calls[-1], 'renew')
        self.assertEqual(status['requested_action'], 'renew')
        self.assertEqual(status['tcpm_online'], 0)
        self.assertTrue(status['mp2762_power_good'])

    def test_missing_power_good_rejects_online_and_offline_sources(self):
        self.b.capacity = 90
        self.b.snap['reg13'] = 0x0d  # POWER_GOOD bit 1 is clear; no fault bits.
        for online in (0, 1):
            self.b.online = online
            status = self.step(3)
            self.assertEqual(status['requested_action'], 'wait')
            self.assertEqual(status['tcpm_online'], online)
            self.assertFalse(status['mp2762_power_good'])
            self.assertEqual(self.b.calls, [])

    def test_invalid_conditions_relinquish_owned_lease(self):
        changes = [('reg08', 0x26), ('reg09', 0x7c), ('reg13', 0xc3),
                   ('reg13', 0x0d),
                   ('last_error', -5), ('recovering', 1)]
        for key, value in changes:
            self.setUp()
            self.b.capacity = 90
            self.step(3)
            self.b.snap[key] = value
            self.step()
            self.assertEqual(self.b.calls[-1], 'restore')
        for temp in [4.9, 43.1]:
            self.setUp()
            self.b.capacity = 90
            self.step(3)
            self.b.temp = temp
            self.step()
            self.assertEqual(self.b.calls[-1], 'restore')

    def test_disconnect_restore(self):
        self.b.capacity = 90
        self.step(3)
        self.b.online = 0
        self.b.snap['reg13'] &= ~0x02  # No MP2762 POWER_GOOD evidence after disconnect.
        self.step()
        self.assertEqual(self.b.calls[-1], 'restore')

    def test_disabled_backend_and_dry_run_never_write(self):
        self.b.capacity = 100
        self.b.snap['allow_control'] = 0
        self.step(5, control=False)
        self.assertEqual(self.b.calls, [])
        with self.assertRaises(RuntimeError):
            self.step()
        self.assertEqual(self.b.calls, [])

    def test_write_and_readback_failures_surface(self):
        for failure in ['fail', 'bad_readback']:
            self.setUp()
            self.b.capacity = 90
            self.step(2)
            setattr(self.b, failure, True)
            with self.assertRaises((OSError, RuntimeError)):
                self.step()

    def test_real_sysfs_read_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            b = mod.Backend(Path(temp))
            b.battery.mkdir(parents=True)
            for invalid in ['-1', '101', 'NaN', '']:
                (b.battery / 'capacity').write_text(invalid)
                with self.assertRaises(ValueError):
                    mod.integer(b.battery / 'capacity', 0, 100)

    def test_strict_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'config.json'
            p.write_text(json.dumps(mod.DEFAULT_CONFIG))
            self.assertEqual(mod.config_from(p), mod.DEFAULT_CONFIG)
            for key, value in [('stop_percent', 74), ('interval_seconds', 60),
                               ('confirm_samples', True), ('valid_temperature_max_c', 60)]:
                cfg = mod.DEFAULT_CONFIG.copy()
                cfg[key] = value
                p.write_text(json.dumps(cfg))
                with self.assertRaises(ValueError):
                    mod.config_from(p)


if __name__ == '__main__':
    unittest.main(verbosity=2)
