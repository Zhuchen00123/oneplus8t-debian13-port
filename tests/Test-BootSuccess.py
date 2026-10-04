#!/usr/bin/env python3
"""Verify GPT success-bit transformation and reject unsafe inputs on sparse files."""
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

path = Path(__file__).resolve().parents[1] / 'scripts/linux/mark-kebab-boot-success.py'
spec = importlib.util.spec_from_file_location('boot_success', path)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def fixture(sector=4096, successful=False, tries=5):
    sectors = 100000
    entries = bytearray(sector)
    length = 100663296 // sector
    for index, name in enumerate(('boot_a', 'boot_b')):
        offset = index * 128
        entries[offset:offset + 16] = bytes([index + 1]) * 16
        entries[offset + 16:offset + 32] = bytes([index + 3]) * 16
        start = 40 + index * length
        flags = ((3 << 48) | (1 << 50) | (tries << 51) |
                 (int(successful) << 54)) if index == 0 else (1 << 55)
        struct.pack_into('<QQQ', entries, offset + 32, start, start + length - 1, flags)
        label = name.encode('utf-16-le')
        entries[offset + 56:offset + 56 + len(label)] = label

    def header(lba, other, table_lba):
        data = bytearray(sector)
        data[:8] = b'EFI PART'
        struct.pack_into('<III', data, 8, 0x10000, 92, 0)
        struct.pack_into('<QQQQ', data, 24, lba, other, 34, sectors - 34)
        data[56:72] = b'disk-identity-01'
        struct.pack_into('<QIII', data, 72, table_lba, 2, 128, zlib.crc32(entries[:256]))
        struct.pack_into('<I', data, 16, zlib.crc32(data[:92]))
        return bytes(data)

    return [mod.Copy(1, header(1, sectors - 1, 2), 2, bytes(entries), 2, 128),
            mod.Copy(sectors - 1, header(sectors - 1, 1, sectors - 2),
                     sectors - 2, bytes(entries), 2, 128)], sector, sectors


class Tests(unittest.TestCase):
    def test_only_success_bit_changes_and_counters_preserved(self):
        for tries in range(8):
            copies, sector, sectors = fixture(tries=tries)
            updated, plan = mod.prepare(*copies, sector, sectors)
            self.assertFalse(plan['already_successful'])
            self.assertEqual(plan['tries_remaining_unchanged'], tries)
            for old, new in zip(copies, updated):
                self.assertEqual([i for i, (a, b) in enumerate(zip(old.entries, new.entries)) if a != b], [54])
                self.assertEqual(old.entries[54] ^ new.entries[54], 0x40)
                self.assertEqual(old.entries[128:], new.entries[128:])

    def test_successful_is_idempotent(self):
        copies, sector, sectors = fixture(successful=True)
        updated, plan = mod.prepare(*copies, sector, sectors)
        self.assertTrue(plan['already_successful'])
        self.assertEqual(updated, copies)

    def test_real_file_roundtrip_crc_and_restoration(self):
        copies, sector, sectors = fixture()
        updated, _ = mod.prepare(*copies, sector, sectors)
        with tempfile.TemporaryFile() as f:
            f.truncate(sector * sectors)
            for c in copies:
                mod.write_copy(f, c, sector)
            self.assertEqual(mod.read_copy(f, sector, 1, sectors), copies[0])
            for c in reversed(updated):
                mod.write_copy(f, c, sector)
                self.assertEqual(mod.read_copy(f, sector, c.header_lba, sectors), c)
            for c in copies:
                mod.write_copy(f, c, sector)
                self.assertEqual(mod.read_copy(f, sector, c.header_lba, sectors), c)

    def test_short_write_and_bad_readback_rejected(self):
        copies, sector, _ = fixture()
        with tempfile.TemporaryFile() as f:
            with patch.object(mod.os, 'fsync', return_value=None):
                from unittest.mock import Mock
                stream = Mock(wraps=f)
                stream.write.return_value = 0
                with self.assertRaisesRegex(OSError, 'Short GPT write'):
                    mod.write_copy(stream, copies[0], sector)
                stream = Mock(wraps=f)
                stream.read.return_value = b'bad'
                with self.assertRaisesRegex(OSError, 'readback mismatch'):
                    mod.write_copy(stream, copies[0], sector)

    def test_corrupt_header_or_table_rejected(self):
        copies, sector, sectors = fixture()
        for lba in (copies[0].header_lba, copies[0].entries_lba):
            with tempfile.TemporaryFile() as f:
                f.truncate(sector * sectors)
                mod.write_copy(f, copies[0], sector)
                f.seek(lba * sector + 40)
                f.write(b'\xff'); f.flush()
                with self.assertRaises(ValueError):
                    mod.read_copy(f, sector, 1, sectors)

    def test_mismatched_tables_rejected(self):
        copies, sector, sectors = fixture()
        entries = bytearray(copies[1].entries); entries[48] ^= 1
        copies[1].entries = bytes(entries)
        with self.assertRaisesRegex(ValueError, 'disagree'):
            mod.prepare(*copies, sector, sectors)

    def test_slot_conflicts_and_invalid_extents_rejected(self):
        for target, value in ((48, 1 << 55), (176, 1 << 50), (32, 0), (40, 39)):
            copies, sector, sectors = fixture()
            entries = bytearray(copies[0].entries)
            if target in (48, 176):
                value |= struct.unpack_from('<Q', entries, target)[0]
            struct.pack_into('<Q', entries, target, value)
            for c in copies:
                c.entries = bytes(entries)
            with self.assertRaises(ValueError):
                mod.prepare(*copies, sector, sectors)

    def test_geometry_and_disk_identity_mismatch_rejected(self):
        for offset in (32, 40, 56):
            copies, sector, sectors = fixture()
            header = bytearray(copies[1].header); header[offset] ^= 1
            copies[1].header = bytes(header)
            with self.assertRaises(ValueError):
                mod.prepare(*copies, sector, sectors)


if __name__ == '__main__':
    unittest.main(verbosity=2)
