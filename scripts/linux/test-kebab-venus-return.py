"""Offline identity guards; never execute a reboot or read a device."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import json

spec = importlib.util.spec_from_file_location('venus_return', Path(__file__).with_name('kebab-venus-return.py'))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class ReturnIdentity(unittest.TestCase):
    def setUp(self):
        self.manifest = dict(kernelrelease=guard.RELEASE,
                             current_boot_backup_sha256=guard.ORIGINAL_BOOT_SHA256,
                             hardware_boot_verified=False, boot_partition_writes=False)

    def allowed(self, **changes):
        values = dict(kernel=guard.RELEASE, root_uuid=guard.ROOT_UUID,
                      boot_sha=guard.ORIGINAL_BOOT_SHA256, manifest=self.manifest)
        values.update(changes)
        return guard.permitted(**values)

    def test_identified_temporary_boot(self):
        self.assertTrue(self.allowed())

    def test_running_original_is_never_rebooted(self):
        self.assertFalse(self.allowed(kernel='7.2.0-kebab-debian13-r3-slider+'))

    def test_wrong_root(self):
        self.assertFalse(self.allowed(root_uuid='another-root'))

    def test_changed_boot_even_if_manifest_changed(self):
        self.manifest['current_boot_backup_sha256'] = '1'*64
        self.assertFalse(self.allowed(boot_sha='1'*64))

    def test_manifest_wrong_release(self):
        self.manifest['kernelrelease'] = 'another-candidate'
        self.assertFalse(self.allowed())

    def test_missing_or_wrong_scope(self):
        for key in ('hardware_boot_verified', 'boot_partition_writes'):
            with self.subTest(key=key):
                m = dict(self.manifest)
                m.pop(key)
                self.assertFalse(self.allowed(manifest=m))
                m[key] = True
                self.assertFalse(self.allowed(manifest=m))


class ReturnCLI(unittest.TestCase):
    def invoke(self, explicit=False, correct=True):
        manifest = dict(kernelrelease=guard.RELEASE,
                        current_boot_backup_sha256=guard.ORIGINAL_BOOT_SHA256,
                        hardware_boot_verified=False, boot_partition_writes=False)
        digest = Mock()
        digest.hexdigest.return_value = guard.ORIGINAL_BOOT_SHA256 if correct else '0'*64
        argv = ['kebab-venus-return', 'manifest.json'] + (['--reboot'] if explicit else [])
        with patch('sys.argv', argv), patch.object(guard.Path, 'read_text', return_value=json.dumps(manifest)), \
                patch('builtins.open'), patch.object(guard.hashlib, 'file_digest', return_value=digest), \
                patch.object(guard.subprocess, 'check_output', side_effect=[guard.RELEASE, guard.ROOT_UUID]), \
                patch.object(guard.subprocess, 'run') as reboot:
            if correct:
                guard.main()
            else:
                with self.assertRaises(SystemExit):
                    guard.main()
            return reboot.call_args_list

    def test_cli_default_does_not_reboot(self):
        self.assertEqual(self.invoke(), [])

    def test_cli_mismatch_never_reboots_even_when_requested(self):
        self.assertEqual(self.invoke(explicit=True, correct=False), [])

    def test_cli_explicit_identified_candidate_uses_normal_reboot(self):
        calls = self.invoke(explicit=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[0], ['systemctl', '--no-wall', 'reboot'])


if __name__ == '__main__':
    unittest.main()
