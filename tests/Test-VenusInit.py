#!/usr/bin/env python3
"""Test the firmware gate and explicit-load requirement without hardware."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('venus_init', Path(__file__).resolve().parents[1] / 'scripts/linux/kebab-venus-init.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class Tests(unittest.TestCase):
    def test_default_check_never_loads_modules(self):
        with patch('sys.argv', ['venus-init']), patch.object(mod.os, 'geteuid', return_value=0), \
             patch.object(mod.subprocess, 'check_output', return_value=mod.RELEASE), \
             patch.object(mod, 'validate_firmware', return_value=13), \
             patch.object(mod.subprocess, 'run') as run:
            mod.main()
            run.assert_not_called()

    def test_wrong_kernel_never_loads(self):
        with patch('sys.argv', ['venus-init', '--load']), patch.object(mod.os, 'geteuid', return_value=0), \
             patch.object(mod.subprocess, 'check_output', return_value='wrong'), \
             patch.object(mod.subprocess, 'run') as run, self.assertRaises(SystemExit):
            mod.main()
        run.assert_not_called()

    def test_failed_firmware_gate_never_loads(self):
        with patch('sys.argv', ['venus-init', '--load']), patch.object(mod.os, 'geteuid', return_value=0), \
             patch.object(mod.subprocess, 'check_output', return_value=mod.RELEASE), \
             patch.object(mod, 'validate_firmware', side_effect=ValueError('mismatch')), \
             patch.object(mod.subprocess, 'run') as run, self.assertRaises(ValueError):
            mod.main()
        run.assert_not_called()

    def test_explicit_load_requests_only_core_and_decoder(self):
        with patch('sys.argv', ['venus-init', '--load']), patch.object(mod.os, 'geteuid', return_value=0), \
             patch.object(mod.subprocess, 'check_output', return_value=mod.RELEASE), \
             patch.object(mod, 'validate_firmware', return_value=13), \
             patch.object(mod, 'decoder_nodes', return_value=['/dev/video14']), \
             patch.object(mod.Path, 'is_char_device', return_value=True), \
             patch.object(mod.subprocess, 'run') as run:
            mod.main()
        self.assertEqual([c.args[0] for c in run.call_args_list],
                         [['modprobe', 'venus_core'], ['modprobe', 'venus_dec']])

    def test_sysfs_binding_requires_venus_parent_and_module(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); root = base / 'class'; root.mkdir()
            node = root / 'video14'; node.mkdir(); (node / 'name').write_text('qcom-venus-decoder')
            parent = base / 'platform/codec'; child = parent / 'decoder'; child.mkdir(parents=True)
            driver = base / 'drivers/qcom-venus'; driver.mkdir(parents=True)
            module = base / 'modules/venus_core'; module.mkdir(parents=True)
            (node / 'device').symlink_to(child, target_is_directory=True)
            (parent / 'driver').symlink_to(driver, target_is_directory=True)
            (driver / 'module').symlink_to(module, target_is_directory=True)
            self.assertEqual(mod.decoder_nodes(root), ['/dev/video14'])
            (driver / 'module').unlink()
            (driver / 'module').symlink_to(base / 'modules/wrong_module')
            self.assertEqual(mod.decoder_nodes(root), [])
            (parent / 'driver').unlink()
            self.assertEqual(mod.decoder_nodes(root), [])

    def firmware_case(self, mutation):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); fw = root / 'fw'; aliases = root / 'aliases'
            fw.mkdir(); aliases.mkdir()
            files = {}
            for name in mod.NAMES:
                content = name.encode(); path = fw / name; path.write_bytes(content); path.chmod(0o644)
                files[name] = {'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
                (aliases / ('venus.mbn' if name == 'venus.mdt' else name)).symlink_to(path)
            manifest = root / 'manifest.json'; manifest.write_text(json.dumps({'files': files}))
            checksum = hashlib.sha256(manifest.read_bytes()).hexdigest()
            mutation(fw, aliases, manifest)
            with patch.object(mod, 'MANIFEST_SHA', checksum):
                return mod.validate_firmware(manifest, fw, aliases)

    @unittest.skipUnless(hasattr(__import__('os'), 'geteuid') and __import__('os').geteuid() == 0, 'root-owned Linux fixture')
    def test_real_split_files_and_aliases(self):
        self.assertEqual(self.firmware_case(lambda *args: None), 13)

    def test_corrupt_manifest_refused(self):
        with self.assertRaisesRegex(ValueError, 'manifest identity'):
            self.firmware_case(lambda fw, aliases, manifest: manifest.write_text('{}'))

    @unittest.skipUnless(hasattr(__import__('os'), 'geteuid') and __import__('os').geteuid() == 0, 'root-owned Linux fixture')
    def test_corrupt_blob_refused(self):
        with self.assertRaisesRegex(ValueError, 'content mismatch'):
            self.firmware_case(lambda fw, aliases, manifest: (fw / 'venus.b19').write_bytes(b'corrupt'))

    @unittest.skipUnless(hasattr(__import__('os'), 'geteuid') and __import__('os').geteuid() == 0, 'root-owned Linux fixture')
    def test_wrong_split_alias_refused(self):
        def mutate(fw, aliases, manifest):
            alias = aliases / 'venus.b19'; alias.unlink(); alias.symlink_to(fw / 'venus.b00')
        with self.assertRaisesRegex(ValueError, 'alias mismatch'):
            self.firmware_case(mutate)


if __name__ == '__main__':
    unittest.main(verbosity=2)
