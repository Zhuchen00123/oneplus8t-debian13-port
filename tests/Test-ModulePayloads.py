#!/usr/bin/env python3
"""Exercise compression-independent module comparison inputs and version paths."""
import gzip
import importlib.util
import io
import lzma
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('payloads', Path(__file__).resolve().parents[1]/'scripts/linux/verify-module-payloads.py')
payloads = importlib.util.module_from_spec(spec)
spec.loader.exec_module(payloads)


class ModulePayloads(unittest.TestCase):
    def fixture(self, entries):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name)/'rootfs.tar.gz'
        with tarfile.open(path, 'w:gz') as archive:
            for name, data in entries:
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        return path

    def test_paths_with_usr_merge(self):
        self.assertEqual(payloads.module_path('usr/lib/modules/7.2-test/kernel/net/test.ko.zst', '7.2-test'),
                         'kernel/net/test.ko.zst')
        self.assertEqual(payloads.module_path('main/lib/modules/7.2-test/kernel/net/test.ko', '7.2-test'),
                         'kernel/net/test.ko')

    def test_old_kernel_rejected(self):
        with self.assertRaises(ValueError):
            payloads.module_path('usr/lib/modules/6.18-test/kernel/net/test.ko', '7.2-test')

    def test_parent_traversal_rejected(self):
        with self.assertRaises(ValueError):
            payloads.module_path('lib/modules/7.2-test/../test.ko', '7.2-test')

    def test_raw_elf_comparison_survives_compression_change(self):
        data = b'\x7fELF'+b'fixture module data'*100
        zstd = subprocess.check_output(['zstd', '-q', '-c'], input=data)
        variants = [('test.ko', data), ('test.ko.gz', gzip.compress(data)),
                    ('test.ko.xz', lzma.compress(data)), ('test.ko.zst', zstd)]
        self.assertEqual({payloads.digest(payloads.uncompressed(n, b)) for n,b in variants},
                         {payloads.digest(data)})

    def test_altered_bytes_change_digest(self):
        data = b'\x7fELF'+b'fixture module data'*100
        altered = bytearray(data); altered[-1] ^= 1
        self.assertNotEqual(payloads.digest(data), payloads.digest(altered))

    def test_real_archive_payload_matches(self):
        data = b'\x7fELF'+b'vermagic=7.2-test\0'+b'payload'
        path = self.fixture([('./usr/lib/modules/7.2-test/kernel/test.ko', data)])
        self.assertEqual(payloads.audit_rootfs_modules(path, '7.2-test',
            {'kernel/test.ko': payloads.digest(data)}), 1)

    def test_modified_archive_payload_rejected(self):
        data = b'\x7fELF'+b'vermagic=7.2-test\0'+b'payload'
        path = self.fixture([('./usr/lib/modules/7.2-test/kernel/test.ko', data[:-1]+b'X')])
        with self.assertRaisesRegex(ValueError, 'binary differs'):
            payloads.audit_rootfs_modules(path, '7.2-test', {'kernel/test.ko': payloads.digest(data)})

    def test_missing_archive_module_rejected(self):
        path = self.fixture([])
        with self.assertRaisesRegex(ValueError, 'missing reference'):
            payloads.audit_rootfs_modules(path, '7.2-test', {'kernel/test.ko': '0'*64})

    def test_duplicate_archive_module_rejected(self):
        data = b'\x7fELF'+b'payload'
        name = './usr/lib/modules/7.2-test/kernel/test.ko'
        path = self.fixture([(name, data), (name, data)])
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            payloads.audit_rootfs_modules(path, '7.2-test', {'kernel/test.ko': payloads.digest(data)})


if __name__ == '__main__': unittest.main()
