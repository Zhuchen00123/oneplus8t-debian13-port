#!/usr/bin/env python3
"""Failure-path checks for read-only dashboard metrics; no graphical server needed."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('panel', Path(__file__).with_name('kebab-status-panel.py'))
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)
network_spec = importlib.util.spec_from_file_location('network', Path(__file__).with_name('kebab-panel-network.py'))
network = importlib.util.module_from_spec(network_spec)
network_spec.loader.exec_module(network)


class MetricFailures(unittest.TestCase):
    def test_address_filtering_and_bad_input(self):
        entries = [{'local': address, 'scope': scope} for address, scope in
                   (('10.1.2.3', 'global'), ('fe80::1', 'link'), ('2001:db8::1', 'global'))]
        entries.append({'local': '2001:db8::2', 'scope': 'global', 'flags': ['tentative']})
        entries.append({'local': '2001:db8::3', 'scope': 'global', 'preferred_life_time': 0})
        raw = json.dumps([{'ifname': 'wlan0', 'addr_info': entries},
                          {'ifname': 'usb0', 'addr_info': [{'local': '192.0.2.1', 'scope': 'global'}]}])
        self.assertEqual(network.addresses(raw), {'ipv4': ['10.1.2.3'], 'ipv6': ['2001:db8::1']})
        for raw in ('invalid', '{}', '[null]'):
            self.assertEqual(network.addresses(raw), {'ipv4': [], 'ipv6': []})

    def test_relay_expiry_and_no_secret_export(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'status.json'
            value = {'checked_at': 1000, 'ready': True, 'https_url': 'https://demo-9003.cnb.run/',
                     'token': 'fixture-secret', 'workspace_sn': 'fixture-id'}
            path.write_text(json.dumps(value))
            self.assertEqual(network.relay_status(path, 1100),
                             {'checked_at': 1000, 'https_url': value['https_url']})
            for now in (999, 1181):
                self.assertIsNone(network.relay_status(path, now))
            for url in ('https://demo-9003.cnb.run/?token=secret', 'https://other.example/',
                        'https://user:secret@demo-9003.cnb.run/'):
                value['https_url'] = url
                path.write_text(json.dumps(value))
                self.assertIsNone(network.relay_status(path, 1100))
            path.write_text('[]')
            self.assertIsNone(network.relay_status(path, 1100))

    def test_mc_config_only_publishes_host_port(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mc.env'
            path.write_text('MCB_HOST=demo-9000.cnb.run\nMCB_PORT=443\nMCB_SECRET=fixture-secret\n')
            self.assertEqual(network.mc_address(path), 'demo-9000.cnb.run:443')
            for host in ('demo-9000.cnb.run/fixture-secret', '$(touch /tmp/fixture)', 'other.example'):
                path.write_text('MCB_HOST=' + host)
                self.assertIsNone(network.mc_address(path))
            path.write_text('MCB_HOST=demo.cnb.run\nMCB_PORT=65536')
            self.assertIsNone(network.mc_address(path))

    def test_network_snapshot_expiry_corruption_and_relay_expiry(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(panel.time, 'time', return_value=1000):
            path = Path(directory) / 'network.json'
            value = {'checked_at': 950, 'campus': {'ipv4': ['10.1.2.3'], 'ipv6': []},
                     'services': {}, 'sillytavern': {'checked_at': 900, 'https_url': 'https://demo-9003.cnb.run/'}}
            path.write_text(json.dumps(value))
            self.assertIsNotNone(panel.network_snapshot(path)['sillytavern'])
            value['services'] = {'kebab-strelay-http.service': []}
            value['mc_address'] = 'demo.cnb.run:65536'
            path.write_text(json.dumps(value))
            self.assertEqual(panel.network_snapshot(path)['services'], {})
            self.assertIsNone(panel.network_snapshot(path)['mc_address'])
            value['campus']['ipv4'] = [123]
            path.write_text(json.dumps(value))
            self.assertIsNone(panel.network_snapshot(path))
            value['campus']['ipv4'] = ['10.1.2.3']
            value['sillytavern']['checked_at'] = 800
            path.write_text(json.dumps(value))
            self.assertIsNone(panel.network_snapshot(path)['sillytavern'])
            for stamp in (800, 1001):
                value['checked_at'] = stamp
                path.write_text(json.dumps(value))
                self.assertIsNone(panel.network_snapshot(path))
            for raw in ('{broken', '[]', '{"checked_at": "now"}'):
                path.write_text(raw)
                self.assertIsNone(panel.network_snapshot(path))

    def test_services_unknown_and_bounded_command_failure(self):
        self.assertEqual(network.service_states('Id=kebab-strelay-http.service\nActiveState=active\n\n'
                                               'Id=ssh.service\nActiveState=active'),
                         {'kebab-strelay-http.service': 'active'})
        with patch.object(network.subprocess, 'run', side_effect=network.subprocess.TimeoutExpired('ip', 3)):
            self.assertEqual(network.command(['ip']), '')

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

    @unittest.skipUnless(hasattr(os, 'statvfs'), 'Linux filesystem metrics require statvfs')
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
