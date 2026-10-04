#!/usr/bin/env python3
"""Failure-path checks for read-only dashboard metrics; no graphical server needed."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

spec = importlib.util.spec_from_file_location('panel', Path(__file__).with_name('kebab-status-panel.py'))
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)


class MetricFailures(unittest.TestCase):
    def test_cpu_delta_not_lifetime_load(self):
        self.assertEqual(panel.cpu_percent((1000, 800), (1100, 850)), 50)
        self.assertIsNone(panel.cpu_percent(None, (1100, 850)))

    def test_cpu_invalid_counters_unknown(self):
        for before, after in (((100, 90), (90, 80)), ((100, 90), (110, 120)), ((100, 90), (100, 90))):
            self.assertIsNone(panel.cpu_percent(before, after))

    def test_network_reset_not_negative_bandwidth(self):
        self.assertEqual(panel.rates((100, 200), (160, 320), 3), [20, 40])
        self.assertIsNone(panel.rates((100, 200), (10, 30), 3))
        self.assertIsNone(panel.rates(None, (10, 30), 3))
        self.assertIsNone(panel.rates((10, 20), (30, 40), 0))

    def test_stale_or_corrupt_charge_snapshot_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'health.json'
            path.write_text(json.dumps({'charge_control': {'policy_active': True, 'inhibit_readback_verified': True,
                                                         'stop_percent': 80, 'resume_percent': 75}}))
            self.assertTrue(panel.health_snapshot(path)[0]['charge_control']['inhibit_readback_verified'])
            os.utime(path, (time.time() - 120, time.time() - 120))
            self.assertIsNone(panel.health_snapshot(path)[0])
            os.utime(path, (time.time() + 120, time.time() + 120))
            self.assertIsNone(panel.health_snapshot(path)[0])
            path.write_text('{broken')
            self.assertIsNone(panel.health_snapshot(path)[0])
            path.write_text('[]')
            self.assertIsNone(panel.health_snapshot(path)[0])
            path.write_text('{"charge_control": []}')
            self.assertIsNone(panel.health_snapshot(path)[0])
            path.write_text('{"charge_control": {"policy_active": "true"}}')
            self.assertIsNone(panel.health_snapshot(path)[0])
            path.write_text('{"charge_control": {"policy_active": true, "inhibit_readback_verified": true}}')
            self.assertIsNone(panel.health_snapshot(path)[0])

    def test_missing_data_mount_not_root_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(panel.disk(directory, require_mount=True))
            self.assertIsNotNone(panel.disk(directory))

    def test_invalid_sensor_not_normal_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sensor'
            for value in ('nan', 'inf', '-9999', '', 'unreadable'):
                path.write_text(value)
                self.assertIsNone(panel.number(path, low=-50, high=150))


if __name__ == '__main__':
    unittest.main()
