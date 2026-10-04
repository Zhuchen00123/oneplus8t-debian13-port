#!/usr/bin/env python3
"""Validate boot payload corruption/layout rejection against real AOSP packing."""
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts/linux'))
from boot_image import inspect, verify_candidate


class BootImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.kernel, self.ramdisk, self.dtb = (self.root/n for n in ('Image.gz', 'initramfs.gz', 'board.dtb'))
        self.kernel.write_bytes(b'kernel payload')
        self.ramdisk.write_bytes(b'initramfs payload')
        self.dtb.write_bytes(b'board tree payload')
        self.image = self.root/'boot.img'
        subprocess.run(['mkbootimg', '--kernel', str(self.kernel), '--ramdisk', str(self.ramdisk),
                        '--dtb', str(self.dtb), '--header_version', '2', '--pagesize', '4096',
                        '--base', '0', '--kernel_offset', '0x8000', '--ramdisk_offset', '0x1000000',
                        '--dtb_offset', '0x1f00000', '--os_version', '12.0.0', '--os_patch_level', '2024-08',
                        '--output', str(self.image)], check=True)
        self.stock = self.root/'stock.img'
        shutil.copyfile(self.image, self.stock)

    def tearDown(self):
        self.temp.cleanup()

    def verify(self):
        return verify_candidate(self.image, self.stock, self.kernel, self.dtb, self.ramdisk)

    def test_real_aosp_v2_roundtrip(self):
        result = self.verify()
        self.assertEqual((result['header_version'], result['page_size'], result['kernel_address']), (2, 4096, 32768))

    def test_payload_bit_flip_is_rejected(self):
        data = bytearray(self.image.read_bytes())
        data[4096] ^= 1
        self.image.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'payload mismatch: kernel'):
            self.verify()

    def test_wrong_load_address_is_rejected(self):
        data = bytearray(self.image.read_bytes())
        struct.pack_into('<I', data, 20, 0x2000000)
        self.image.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'stock layout: ramdisk_address'):
            self.verify()

    def test_truncated_dtb_is_rejected(self):
        data = self.image.read_bytes()
        self.image.write_bytes(data[:3*4096+len(self.dtb.read_bytes())-1])
        with self.assertRaisesRegex(ValueError, 'Truncated dtb'):
            inspect(self.image)

    def test_unsupported_header_is_rejected(self):
        data = bytearray(self.image.read_bytes())
        struct.pack_into('<I', data, 40, 3)
        self.image.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'v2 header'):
            inspect(self.image)


if __name__ == '__main__':
    unittest.main(verbosity=2)
