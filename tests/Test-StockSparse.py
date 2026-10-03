#!/usr/bin/env python3
"""Check native Android sparse framing and malformed/truncated rejection."""
import importlib.util
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('stock', Path(__file__).resolve().parents[1]/'scripts/linux/prepare-stock-recovery.py')
stock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stock)


class SparseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.raw = self.dir/'raw.img'
        self.raw.write_bytes(b'\0'*4096 + b'Q'*4096 + bytes(range(256))*16)
        self.sparse = self.dir/'sparse.img'
        subprocess.run(['img2simg', str(self.raw), str(self.sparse)], check=True)

    def test_native_roundtrip(self):
        report = stock.sparse_layout(self.sparse, self.raw.stat().st_size)
        self.assertEqual(report['expanded_bytes'], 12288)
        decoded = self.dir/'decoded.img'
        subprocess.run(['simg2img', str(self.sparse), str(decoded)], check=True)
        self.assertEqual(decoded.read_bytes(), self.raw.read_bytes())

    def test_declared_size_mismatch(self):
        with self.assertRaises(ValueError): stock.sparse_layout(self.sparse, 8192)

    def test_truncated_payload(self):
        self.sparse.write_bytes(self.sparse.read_bytes()[:-1])
        with self.assertRaises(ValueError): stock.sparse_layout(self.sparse, 12288)

    def test_invalid_chunk_type(self):
        data = bytearray(self.sparse.read_bytes()); struct.pack_into('<H', data, 28, 0xffff)
        self.sparse.write_bytes(data)
        with self.assertRaises(ValueError): stock.sparse_layout(self.sparse, 12288)

    def test_invalid_payload_length(self):
        data = bytearray(self.sparse.read_bytes()); struct.pack_into('<I', data, 36, 11)
        self.sparse.write_bytes(data)
        with self.assertRaises(ValueError): stock.sparse_layout(self.sparse, 12288)

    def test_trailing_bytes(self):
        self.sparse.write_bytes(self.sparse.read_bytes()+b'junk')
        with self.assertRaises(ValueError): stock.sparse_layout(self.sparse, 12288)


if __name__ == '__main__': unittest.main()
