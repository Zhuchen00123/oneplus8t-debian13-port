#!/usr/bin/env python3
"""Protocol boundaries and independent captured-wire regressions, offline only."""
import importlib.util
import struct
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('ssc', Path(__file__).with_name('kebab-ssc-inspect.py'))
ssc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ssc)


class ProtocolTest(unittest.TestCase):
    def test_captured_libssc_registry_discovery(self):
        # e21/1-capture.log: an actual libssc discovery transmission, not generated.
        captured = bytes.fromhex(
            '00010020003a00100100010133003100'
            '0a1209abababababababab11abababababababab1500020000'
            '1a04080110002210120e0a08726567697374727910001801')
        payload = ssc.blob(1, b'registry') + b'\x10\x00\x18\x01'
        self.assertEqual(ssc.request_frame(1, ssc.DISCOVERY, 512, payload), captured)

    def test_varint_overflow_and_truncation(self):
        for value in (b'\x80', b'\x80' * 9 + b'\x02', b'\xff' * 10):
            with self.assertRaises(ValueError):
                ssc.read_varint(value, 0)
        self.assertEqual(ssc.read_varint(b'\xff' * 9 + b'\x01', 0), ((1 << 64) - 1, 10))

    def test_wire_truncation_and_invalid_tags(self):
        for value in (b'\x00', b'\x0a\x04a', b'\x09abc', b'\x0dabc', b'\x0b'):
            with self.assertRaises(ValueError):
                ssc.fields(value)
        with self.assertRaises(ValueError):
            ssc.one(b'\x08\x01\x08\x02', 1, 0)

    def test_qmi_rejects_inconsistent_lengths(self):
        for frame in (b'\0', bytes.fromhex('04010022000100'),
                      bytes.fromhex('04010022000300020100')):
            with self.assertRaises(ValueError):
                ssc.qmi_decode(frame)

    def test_ack_is_never_an_event(self):
        # Standard SUCCESS TLV + client ID + SSC response=0.
        tlv = bytes.fromhex('020400000000001008000b0000000000000011040000000000')
        ack = struct.pack('<BHHH', 2, 1, 0x20, len(tlv)) + tlv
        self.assertEqual(ssc.events_from_frame(ack), [])
        bad = bytearray(ack)
        bad[-1] = 1
        with self.assertRaises(RuntimeError):
            ssc.events_from_frame(bytes(bad))

    def test_registry_read_event_decodes_nested_typed_values(self):
        # Independent fixture encoded according to Qualcomm registry schema.
        integer_item = bytes.fromhex('0a03627573150c00000069fdffffffffffffff')
        float_item = bytes.fromhex('0a0472617465650000c842')
        subgroup = ssc.blob(3, integer_item) + ssc.blob(3, float_item)
        parent = ssc.blob(1, b'config') + ssc.blob(10, subgroup)
        decoded = ssc.registry_data(ssc.blob(3, parent))
        self.assertEqual(decoded[0]['items'][0], {'name': 'bus', 'version': 12, 'int': -3})
        self.assertEqual(decoded[0]['items'][1], {'name': 'rate', 'float': 100.0})

    def test_multi_event_indication_keeps_uid_timestamp_and_body(self):
        uid = (123, 456)
        body = b'\x0d' + struct.pack('<I', 514) + b'\x11' + struct.pack('<Q', 999) + ssc.blob(3, b'abc')
        pb = ssc.blob(1, ssc.uid_bytes(uid)) + ssc.blob(2, body) + ssc.blob(2, body)
        value = struct.pack('<H', len(pb)) + pb
        tlv = b'\x02' + struct.pack('<H', len(value)) + value
        frame = struct.pack('<BHHH', 4, 1, 0x22, len(tlv)) + tlv
        self.assertEqual(ssc.events_from_frame(frame), [(uid, 514, 999, b'abc')] * 2)

    def test_real_attributes_keep_integer_distinct_from_printable_bytes(self):
        # Actual stream_type and AVAILABLE attributes captured on this device.
        integer = bytes.fromhex('0810120b0a09210100000000000000')
        boolean = bytes.fromhex('080312040a022801')
        self.assertEqual(ssc.attribute_values(ssc.one(integer, 2, 2)), [1])
        self.assertEqual(ssc.attribute_values(ssc.one(boolean, 2, 2)), [True])
        text = bytes.fromhex('0800120c0a0a12087463733334303800')
        self.assertEqual(ssc.attribute_values(ssc.one(text, 2, 2)), ['tcs3408'])

    def test_captured_suid_event_is_768_not_measurement_1025(self):
        # e21/1-capture.log: real registry discovery indication.
        frame = bytes.fromhex(
            '0401002200540001080001000000000000000246004400'
            '0a1209abababababababab11abababababababab'
            '122e0d000300001189a2d4dc0e000000'
            '1a1e0a08726567697374727912120975221e70b441255e1159277f00a75427e1')
        events = ssc.events_from_frame(frame)
        self.assertEqual(events[0][0], ssc.DISCOVERY)
        self.assertEqual(events[0][1], 768)
        self.assertEqual(ssc.one(events[0][3], 1, 2), b'registry')


if __name__ == '__main__':
    unittest.main()
