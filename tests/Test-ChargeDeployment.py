#!/usr/bin/env python3
"""Validate real deployment GPT parser against corruption and slot conflicts."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

p = Path(__file__).resolve().parents[1] / 'scripts/linux/deploy-charge-policy-kernel.py'
spec = importlib.util.spec_from_file_location('deploy', p)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def fixture(active_a=True, active_b=False, mismatch=False, crc_bad=False):
    entries = bytearray(256)
    for index, (name, active) in enumerate([('boot_a', active_a), ('boot_b', active_b)]):
        offset = index * 128
        entries[offset:offset + 16] = b'1' * 16
        struct.pack_into('<QQQ', entries, offset + 32, 10 + index, 11 + index,
                         (3 << 48) | (int(active) << 50))
        label = name.encode('utf-16-le')
        entries[offset + 56:offset + 56 + len(label)] = label

    def header(lba, other, table_lba, table):
        data = bytearray(512)
        data[:8] = b'EFI PART'
        struct.pack_into('<III', data, 8, 0x10000, 92, 0)
        struct.pack_into('<QQ', data, 24, lba, other)
        struct.pack_into('<QIII', data, 72, table_lba, 2, 128, zlib.crc32(table))
        struct.pack_into('<I', data, 16, zlib.crc32(data[:92]))
        return data

    backup = bytearray(entries)
    if mismatch:
        backup[48] ^= 1
    data = bytearray(10 * 512)
    data[512:1024] = header(1, 9, 2, entries)
    data[1024:1280] = entries
    data[8 * 512:8 * 512 + 256] = backup
    data[9 * 512:10 * 512] = header(9, 1, 8, backup)
    if crc_bad:
        data[1024] ^= 1
    return data


class Tests(unittest.TestCase):
    def parse(self, data):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'disk'
            p.write_bytes(data)
            with patch.object(mod.fcntl, 'ioctl', return_value=struct.pack('I', 512)):
                return mod.gpt_boot_slots(p)

    def test_active_a(self):
        slots = self.parse(fixture())
        self.assertTrue(slots['boot_a']['active'])
        self.assertFalse(slots['boot_b']['active'])

    def test_active_b_rejected(self):
        with self.assertRaises(ValueError):
            self.parse(fixture(False, True))

    def test_two_active_rejected(self):
        with self.assertRaises(ValueError):
            self.parse(fixture(True, True))

    def test_no_active_rejected(self):
        with self.assertRaises(ValueError):
            self.parse(fixture(False, False))

    def test_tables_disagree_rejected(self):
        with self.assertRaises(ValueError):
            self.parse(fixture(mismatch=True))

    def test_crc_failure_rejected(self):
        with self.assertRaises(ValueError):
            self.parse(fixture(crc_bad=True))

    def test_partial_write_and_bad_readback_restore_full_previous_image(self):
        for partial_error in (True, False):
            with tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                boot, image, backup = [directory / x for x in ('boot', 'image', 'backup')]
                boot.write_bytes(b'previous boot with tail')
                backup.write_bytes(boot.read_bytes())
                image.write_bytes(b'new image')
                previous_sha = mod.digest(backup)
                calls = []

                def dd(*args):
                    source = Path(args[1].removeprefix('if='))
                    calls.append(source)
                    if source == image:
                        boot.write_bytes(b'partial')
                        if partial_error:
                            raise OSError('write failed after partial transfer')
                    else:
                        boot.write_bytes(source.read_bytes())
                    return ''

                with patch.object(mod, 'run', side_effect=dd):
                    with self.assertRaisesRegex(RuntimeError, 'previous boot restored'):
                        mod.replace_boot(boot, image, backup, previous_sha, mod.digest(image))
                self.assertEqual(calls, [image, backup])
                self.assertEqual(boot.read_bytes(), backup.read_bytes())

    def test_recovery_failure_reports_do_not_reboot(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            boot, image, backup = [directory / x for x in ('boot', 'image', 'backup')]
            for p in (boot, image, backup):
                p.write_bytes(p.name.encode())
            with patch.object(mod, 'run', side_effect=OSError('I/O failure')):
                with self.assertRaisesRegex(RuntimeError, 'DO NOT reboot'):
                    mod.replace_boot(boot, image, backup, mod.digest(backup), mod.digest(image))


if __name__ == '__main__':
    unittest.main(verbosity=2)
