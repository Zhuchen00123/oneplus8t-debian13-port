#!/usr/bin/env python3
"""Exercise persistent Venus deployment gates without touching a device."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

path = Path(__file__).resolve().parents[1] / 'scripts/linux/deploy-venus-kernel.py'
spec = importlib.util.spec_from_file_location('venus_deploy', path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
TEST_ROOT = Path(__file__).resolve().parent


def minimal_bundle_manifest():
    return {
        'kernelrelease': mod.R4,
        'root_uuid': mod.ROOT_UUID,
        'current_boot_backup_sha256': mod.ORIGINAL_BOOT_SHA256,
        'boot_policy_unchanged': True,
        'boot_partition_limit_bytes': mod.BOOT_BYTES,
        'modules_count': mod.MODULE_COUNT,
        'modules_sha256_verified': True,
        'modules_vermagic_verified': True,
        'rootfs_prep_payloads_deployed': False,
        'files_sha256': dict(mod.REQUIRED_BUNDLE_FILES),
    }


def minimal_oem_manifest():
    files = {}
    for name in mod.OEM_NAMES:
        files[name] = {
            'archive_member': 'vendor/firmware_mnt/image/' + name,
            'type': 'regular', 'bytes': 1, 'sha256': '0' * 64, 'output': name,
        }
    return {
        'schema_version': 1,
        'source': {'device_build_tag': mod.OEM_BUILD_TAG,
                   'archive_sha256': mod.OEM_ARCHIVE_SHA256},
        'extraction': {'member_count': 13, 'selected_only': True,
                       'byte_preserving': True, 'firmware_repack_or_signature_change': False},
        'files': files,
    }


def make_module_archive(path, relative='kernel/test.ko.zst', payload=b'candidate-module'):
    full_name = 'lib/modules/' + mod.R4 + '/' + relative
    with tarfile.open(path, 'w:gz') as archive:
        info = tarfile.TarInfo(full_name)
        info.size = len(payload)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(payload))
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Tests(unittest.TestCase):
    def test_default_cli_is_read_only(self):
        plan = {'module_count': mod.MODULE_COUNT}
        with patch.object(mod, 'preflight', return_value=plan), \
                patch.object(mod, 'apply_deployment') as apply, \
                patch.object(mod, 'acquire_update_lock') as lock, \
                patch('sys.stdout', new_callable=io.StringIO) as stdout:
            result = mod.main(['/bundle'])
        self.assertEqual(result, 0)
        apply.assert_not_called()
        lock.assert_not_called()
        self.assertIn('READ ONLY', stdout.getvalue())

    def test_explicit_apply_rechecks_under_lock_before_write(self):
        plans = [{'module_count': mod.MODULE_COUNT,
                  'boot_before_sha256': mod.ORIGINAL_BOOT_SHA256},
                 {'module_count': mod.MODULE_COUNT,
                  'boot_before_sha256': mod.ORIGINAL_BOOT_SHA256}]
        lock_context = Mock()
        lock_context.__enter__ = Mock(return_value=object())
        lock_context.__exit__ = Mock(return_value=False)
        with patch.object(mod, 'preflight', side_effect=plans) as preflight, \
                patch.object(mod, 'acquire_update_lock', return_value=lock_context) as lock, \
                patch.object(mod, 'apply_deployment') as apply, \
                patch('sys.stdout', new_callable=io.StringIO):
            result = mod.main(['/bundle', '--apply'])
        self.assertEqual(result, 0)
        self.assertEqual(preflight.call_count, 2)
        lock.assert_called_once_with()
        apply.assert_called_once_with(plans[1])

    def test_r3_or_changed_root_identity_rejected(self):
        for field, invalid in [('kernelrelease', mod.R3),
                               ('root_uuid', '00000000-0000-0000-0000-000000000000'),
                               ('current_boot_backup_sha256', '0' * 64),
                               ('boot_policy_unchanged', False)]:
            manifest = minimal_bundle_manifest()
            manifest[field] = invalid
            with self.subTest(field=field), self.assertRaises(ValueError):
                mod.validate_bundle_metadata(manifest)

    def test_oem_manifest_must_match_device_build_and_all_13_files(self):
        good = minimal_oem_manifest()
        self.assertEqual(set(mod.validate_firmware_metadata(good)), mod.OEM_NAMES)
        bad_build = minimal_oem_manifest()
        bad_build['source']['device_build_tag'] = 'different-device-build'
        with self.assertRaisesRegex(ValueError, 'OEM manifest'):
            mod.validate_firmware_metadata(bad_build)
        missing_shard = minimal_oem_manifest()
        missing_shard['files'].pop('venus.b19')
        with self.assertRaisesRegex(ValueError, 'OEM manifest'):
            mod.validate_firmware_metadata(missing_shard)

    def test_wrong_firmware_manifest_digest_is_rejected_before_alias_checks(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            manifest = Path(temp) / 'manifest.json'
            manifest.write_text('{}', encoding='utf-8')
            with patch.object(mod, 'require_plain_root_input', return_value=manifest):
                with self.assertRaisesRegex(ValueError, 'manifest SHA-256 mismatch'):
                    mod.validate_installed_firmware(manifest)

    def test_module_archive_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            archive_path = Path(temp) / 'bad.tar.gz'
            with tarfile.open(archive_path, 'w:gz') as archive:
                payload = b'x'
                item = tarfile.TarInfo('lib/modules/' + mod.R4 + '/../escape.ko.zst')
                item.size = len(payload)
                archive.addfile(item, io.BytesIO(payload))
            with patch.object(mod, 'MODULE_COUNT', 1):
                with self.assertRaisesRegex(ValueError, 'Unsafe module archive member'):
                    mod.module_archive_members(archive_path)

    def test_installed_module_hash_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            root = Path(temp)
            archive = root / 'modules.tar.gz'
            archive_sha = make_module_archive(archive)
            module_root = root / 'installed'
            (module_root / 'kernel').mkdir(parents=True)
            (module_root / 'kernel/test.ko.zst').write_bytes(b'different-module')
            with patch.object(mod, 'MODULE_COUNT', 1), \
                    patch.object(mod, 'MODULE_ARCHIVE_SHA256', archive_sha), \
                    patch.object(mod, 'require_plain_root_input', side_effect=lambda p, **kw: Path(p)):
                with self.assertRaisesRegex(ValueError, 'module SHA-256 mismatch'):
                    mod.verify_installed_modules(archive, module_root)

    def test_matching_installed_module_and_vermagic_pass(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            root = Path(temp)
            archive = root / 'modules.tar.gz'
            archive_sha = make_module_archive(archive)
            module_root = root / 'installed'
            (module_root / 'kernel').mkdir(parents=True)
            (module_root / 'kernel/test.ko.zst').write_bytes(b'candidate-module')
            result = Mock(stdout=mod.R4 + ' SMP preempt mod_unload\n', stderr='')
            with patch.object(mod, 'MODULE_COUNT', 1), \
                    patch.object(mod, 'MODULE_ARCHIVE_SHA256', archive_sha), \
                    patch.object(mod, 'require_plain_root_input', side_effect=lambda p, **kw: Path(p)), \
                    patch.object(mod.subprocess, 'run', return_value=result) as modinfo:
                verified = mod.verify_installed_modules(archive, module_root)
            self.assertEqual(verified['count'], 1)
            self.assertTrue(verified['vermagic_verified'])
            self.assertEqual(modinfo.call_args.args[0][:3], ['modinfo', '-F', 'vermagic'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
