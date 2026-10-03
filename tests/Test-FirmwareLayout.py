#!/usr/bin/env python3
"""Negative checks for incomplete or altered Qualcomm split firmware."""
import importlib.util
from pathlib import Path
import struct
import unittest

spec=importlib.util.spec_from_file_location('firmware',Path(__file__).resolve().parents[1]/'scripts/linux/stage-device-firmware.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class FirmwareLayout(unittest.TestCase):
    def fixture(self):
        header=struct.pack('<16sHHIIIIIHHHHHH',b'\x7fELF\x01\x01\x01'+b'\0'*9,2,40,1,0,52,0,0,52,32,3,0,0,0)
        ph=b''.join(struct.pack('<IIIIIIII',*x) for x in [
            (0,0,0,0,148,0,0x07000000,0),
            (0,4096,0,0,16,16,0x02000000,0),
            (1,8192,0,0,4,8,0x08000005,0)])
        meta=header+ph+b'S'*16
        return meta,{'dsp.b00':header+ph,'dsp.b01':b'S'*16,'dsp.b02':b'DATA'}
    def test_complete_split(self):
        meta,files=self.fixture()
        self.assertEqual(module.split_members(meta,'dsp',files.__getitem__),files)
    def test_missing_load_segment(self):
        meta,files=self.fixture(); del files['dsp.b02']
        with self.assertRaises(KeyError):module.split_members(meta,'dsp',files.__getitem__)
    def test_truncated_segment(self):
        meta,files=self.fixture(); files['dsp.b02']=b'D'
        with self.assertRaises(ValueError):module.split_members(meta,'dsp',files.__getitem__)
    def test_altered_signature_metadata(self):
        meta,files=self.fixture(); files['dsp.b01']=b'X'*16
        with self.assertRaises(ValueError):module.split_members(meta,'dsp',files.__getitem__)
    def test_altered_header_segment(self):
        meta,files=self.fixture(); files['dsp.b00']=b'X'*148
        with self.assertRaises(ValueError):module.split_members(meta,'dsp',files.__getitem__)
    def test_invalid_program_table(self):
        meta,files=self.fixture(); meta=bytearray(meta); struct.pack_into('<I',meta,28,900)
        with self.assertRaises(ValueError):module.split_members(meta,'dsp',files.__getitem__)


if __name__=='__main__':unittest.main()
