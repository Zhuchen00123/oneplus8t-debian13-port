#!/usr/bin/env python3
"""Unit tests for the Titan HFI contract and codec.

Run:  python3 test_titan_hfi.py -v
No third-party dependencies, no firmware needed.

The header cross-checks below parse titan_hfi.h, so the C contract and the
Python codec cannot drift apart silently.  The firmware-derived facts
(opcode payload sizes) are re-verified end to end by extract_opcodes.py, which
needs the ELF; these tests only guard the constants that file also reads.
"""

from __future__ import annotations

import os
import re
import struct
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import titan_hfi as hfi          # noqa: E402
import make_config_io as mk      # noqa: E402

HEADER = os.path.join(HERE, "titan_hfi.h")

RE_TABLE_ENTRY = re.compile(r"X\((0x[0-9A-Fa-f]+),\s*([A-Za-z0-9_]+),\s*(\d+)\)")
RE_ASSERT_SIZE = re.compile(r"TITAN_HFI_ASSERT_SIZE\((\w+),\s*(\d+)\)")
RE_DEFINE = re.compile(r"^#define\s+TITAN_HFI_(\w+)\s+(\d+)u?\b")


def read_header():
    with open(HEADER, "r", encoding="utf-8") as fh:
        return fh.read()


class HeaderContract(unittest.TestCase):
    """titan_hfi.h must agree with this module, number for number."""

    def test_opcode_table_matches_header(self):
        table = {}
        for line in read_header().splitlines():
            m = RE_TABLE_ENTRY.search(line)
            if m:
                table[int(m.group(1), 16)] = (m.group(2), int(m.group(3)))
        self.assertTrue(table, "no TITAN_HFI_OPCODE_TABLE entries found")
        self.assertEqual(set(table), set(hfi.OPCODE_PAYLOAD_SIZE))
        for opcode, (name, size) in table.items():
            self.assertEqual(size, hfi.OPCODE_PAYLOAD_SIZE[opcode],
                             "opcode 0x%02x size" % opcode)
            self.assertEqual(name, hfi.OPCODE_NAMES[opcode],
                             "opcode 0x%02x name" % opcode)

    def test_static_asserts_pin_the_headline_sizes(self):
        pinned = {m.group(1): int(m.group(2))
                  for m in RE_ASSERT_SIZE.finditer(read_header())}
        expected = {
            "titan_hfi_q_tbl_hdr": 24,
            "titan_hfi_q_hdr": 956,
            "titan_hfi_pkt_hdr": 8,
            "titan_hfi_cmd_ping": 16,
            "titan_hfi_cmd_create_handle": 28,
            "titan_hfi_msg_create_handle_ack": 32,
            "titan_hfi_cmd_async": 40,
            "titan_hfi_msg_async_ack": 36,
            "titan_hfi_property": 12,
            "titan_hfi_msg_sys_init_done": 20,
            "titan_hfi_msg_event": 28,
            "titan_hfi_image_descriptor": 92,
            "titan_hfi_bps_config_io": 840,
            "titan_hfi_ipe_config_io": 1600,
            "titan_hfi_bps_iq_settings": 1036,
            "titan_hfi_ipe_iq_settings": 3676,
            "titan_hfi_ipe_bps_mem_map": 20,
            "titan_hfi_bps_destroy": 8,
            "titan_hfi_bps_abort": 16,
            "titan_hfi_ipe_bps_mem_map_done": 12,
            "titan_hfi_system_supported": 36,
        }
        for struct_name, size in expected.items():
            self.assertIn(struct_name, pinned,
                          "header does not pin %s" % struct_name)
            self.assertEqual(pinned[struct_name], size,
                             "%s pinned to %d" % (struct_name, pinned[struct_name]))

    def test_geometry_constants_match_header(self):
        defines = {}
        for line in read_header().splitlines():
            m = RE_DEFINE.match(line)
            if m:
                defines[m.group(1)] = int(m.group(2))
        self.assertEqual(defines["MAX_PKT_SIZE"], hfi.MAX_PKT_SIZE)
        self.assertEqual(defines["QTBL_HDR_SIZE"], hfi.QTBL_HDR_SIZE)
        self.assertEqual(defines["Q_HDR_SIZE"], hfi.Q_HDR_SIZE)
        self.assertEqual(defines["QTBL_MAP_WINDOW"], hfi.QTBL_MAP_WINDOW)
        self.assertEqual(defines["ASYNC_FIXED_HDR"], hfi.ASYNC_FIXED_HDR)
        self.assertEqual(defines["ASYNC_MIN_SIZE"], hfi.ASYNC_MIN_SIZE)
        self.assertEqual(hfi.MAX_PKT_SIZE, 6019)

    def test_three_queue_table_fits_the_mapping_window(self):
        size = hfi.QTBL_HDR_SIZE + hfi.Q_MAX * hfi.Q_HDR_SIZE
        self.assertEqual(size, 2892)
        self.assertLessEqual(size, hfi.QTBL_MAP_WINDOW)


class AsyncPayloadOffset(unittest.TestCase):
    """The one place upstream camss-icp-hfi.h is wrong: payload offset 32+4n."""

    PAYLOAD = bytes(range(20)) * 1        # 20 B == MEM_MAP / MEM_UNMAP payload

    def _packet(self, handles, opcode=0x0E):
        return hfi.encode_async(opcode, handles, self.PAYLOAD, direct=True)

    def test_payload_offset_with_one_handle(self):
        pkt = self._packet([0xA1])
        self.assertEqual(hfi.async_payload_off(1), 36)
        self.assertEqual(pkt[0x24:0x24 + 20], self.PAYLOAD)
        info = hfi.decode(pkt)
        self.assertEqual(info["payload_offset"], 36)
        self.assertEqual(info["nNumHandles"], 1)
        self.assertEqual(info["hHandles"], [0xA1])
        self.assertEqual(info["nSize"], 20 + 32 + 4 * 1)

    def test_payload_offset_with_two_handles(self):
        pkt = self._packet([0xA1, 0xA2])
        self.assertEqual(hfi.async_payload_off(2), 40)
        self.assertEqual(pkt[0x28:0x28 + 20], self.PAYLOAD)
        info = hfi.decode(pkt)
        self.assertEqual(info["payload_offset"], 40)
        self.assertEqual(info["hHandles"], [0xA1, 0xA2])
        self.assertEqual(info["nSize"], 20 + 32 + 4 * 2)

    def test_payload_offset_with_three_handles(self):
        pkt = self._packet([0xA1, 0xA2, 0xA3])
        self.assertEqual(hfi.async_payload_off(3), 44)
        self.assertEqual(pkt[0x2C:0x2C + 20], self.PAYLOAD)
        info = hfi.decode(pkt)
        self.assertEqual(info["payload_offset"], 44)
        self.assertEqual(info["hHandles"], [0xA1, 0xA2, 0xA3])
        self.assertEqual(info["nSize"], 20 + 32 + 4 * 3)

    def test_handle_field_offset_is_32_not_36(self):
        pkt = self._packet([0xDEADBEEF])
        self.assertEqual(struct.unpack_from("<I", pkt, 8)[0], 0x0E)      # opcode
        self.assertEqual(struct.unpack_from("<I", pkt, 28)[0], 1)        # nNumHandles
        self.assertEqual(struct.unpack_from("<I", pkt, 32)[0], 0xDEADBEEF)
        # the upstream layout would put a handle count here instead
        self.assertNotEqual(struct.unpack_from("<I", pkt, 32)[0], 1)


class AsyncPlacement(unittest.TestCase):
    """DIRECT vs INDIRECT, exactly as CheckCommandSize() decides."""

    def test_direct_bps_config_io(self):
        payload = bytes(840)
        pkt = hfi.encode_async(0x01, [1], payload, direct=True,
                               user_arg1=0x1122334455667788)
        self.assertEqual(len(pkt), 876)
        self.assertEqual(struct.unpack_from("<I", pkt, 4)[0],
                         hfi.HFI_CMD_IPEBPS_ASYNC_COMMAND_DIRECT)
        info = hfi.decode(pkt)
        self.assertTrue(info["direct"])
        self.assertTrue(info["firmware_check"])
        self.assertEqual(info["nUserArg1"], 0x1122334455667788)
        self.assertEqual(len(info["payload"]), 840)

    def test_indirect_ipe_frame_process(self):
        payload = bytes(14328)
        pkt = hfi.encode_async(0x06, [3], payload, direct=False,
                               indirect_addr=0x81234000)
        self.assertEqual(len(pkt), 40)              # 32 + 4*1 + 4
        self.assertEqual(struct.unpack_from("<I", pkt, 4)[0],
                         hfi.HFI_CMD_IPEBPS_ASYNC_COMMAND_INDIRECT)
        self.assertEqual(struct.unpack_from("<I", pkt, 36)[0], 0x81234000)
        info = hfi.decode(pkt)
        self.assertFalse(info["direct"])
        self.assertIsNone(info["payload"])
        self.assertEqual(info["indirect_addr"], 0x81234000)
        self.assertEqual(info["payload_offset"], 36)
        self.assertTrue(info["firmware_check"])

    def test_ipe_frame_process_cannot_be_direct(self):
        with self.assertRaises(ValueError) as ctx:
            hfi.encode_async(0x06, [3], bytes(14328), direct=True)
        self.assertIn("receive limit", str(ctx.exception))

    def test_indirect_requires_an_address(self):
        with self.assertRaises(ValueError):
            hfi.encode_async(0x06, [3], bytes(14328), direct=False)

    def test_indirect_address_must_fit_in_32_bits(self):
        with self.assertRaises(ValueError):
            hfi.encode_async(0x06, [3], bytes(14328), direct=False,
                             indirect_addr=0x1_0000_0000)


class SizeValidation(unittest.TestCase):
    """check_command_size() mirrors the firmware byte for byte."""

    def test_direct_boundary_is_exact(self):
        need = 840 + 32 + 4
        ok, _ = hfi.check_command_size(need, 840, 1, True)
        self.assertTrue(ok)
        ok, reason = hfi.check_command_size(need - 4, 840, 1, True)
        self.assertFalse(ok)
        self.assertIn("DIRECT needs", reason)

    def test_indirect_boundary_is_exact(self):
        self.assertTrue(hfi.check_command_size(40, 14328, 1, False)[0])
        self.assertFalse(hfi.check_command_size(36, 14328, 1, False)[0])
        self.assertFalse(hfi.check_command_size(39, 14328, 1, False)[0])

    def test_rejects_zero_unaligned_and_oversized(self):
        self.assertFalse(hfi.check_command_size(0, 8, 1, False)[0])
        self.assertFalse(hfi.check_command_size(42, 8, 1, False)[0])
        self.assertFalse(hfi.check_command_size(6020, 8, 1, False)[0])
        self.assertTrue(hfi.check_command_size(6019 - (6019 % 4), 8, 1, False)[0])

    def test_wrong_payload_length_is_rejected(self):
        with self.assertRaises(ValueError):
            hfi.encode_async(0x01, [1], bytes(839), direct=True)
        with self.assertRaises(ValueError):
            hfi.encode_async(0x01, [1], bytes(841), direct=True)
        # opt out is available for investigation of a firmware we do not know yet
        pkt = hfi.encode_async(0x01, [1], bytes(84), direct=True,
                               enforce_payload_size=False)
        self.assertEqual(len(pkt), 84 + 36)

    def test_unknown_opcode_is_rejected(self):
        with self.assertRaises(ValueError):
            hfi.encode_async(0x10, [1], bytes(16), direct=True)
        with self.assertRaises(ValueError):
            hfi.encode_async(0x00, [1], bytes(16), direct=True)

    def test_handle_count_rules(self):
        for opcode in sorted(hfi.OPCODES_REQUIRING_ONE_HANDLE):
            payload = bytes(hfi.OPCODE_PAYLOAD_SIZE[opcode])
            with self.assertRaises(ValueError):
                hfi.encode_async(opcode, [1, 2], payload, direct=True,
                                 enforce_payload_size=False)
        # MEM_MAP does not carry the check in the firmware
        hfi.encode_async(0x0E, [1, 2], bytes(20), direct=True)

    def test_handle_type_range(self):
        for handle_type in range(0, hfi.HANDLE_TYPE_MAX + 1):
            pkt = hfi.encode_create_handle(handle_type)
            self.assertEqual(len(pkt), 28)
        with self.assertRaises(ValueError):
            hfi.encode_create_handle(7)

    def test_handle_masks_cover_types_1_to_6(self):
        self.assertEqual(hfi.HANDLE_MASK_BPS | hfi.HANDLE_MASK_IPE, 0x7E)
        self.assertEqual(hfi.HANDLE_MASK_BPS & hfi.HANDLE_MASK_IPE, 0)
        for handle_type in (1, 5, 6):
            self.assertTrue(hfi.HANDLE_MASK_BPS & (1 << handle_type))
        for handle_type in (2, 3, 4):
            self.assertTrue(hfi.HANDLE_MASK_IPE & (1 << handle_type))


class Decode(unittest.TestCase):
    def test_async_roundtrip(self):
        payload = mk.build_bps_config_io(user_arg=7)
        pkt = hfi.encode_async(0x01, [9], payload, direct=True,
                               user_arg1=0xCC, user_arg2=0xDD)
        info = hfi.decode(pkt)
        self.assertEqual(info["kind"], "async")
        self.assertEqual(info["name"], "BPS_CONFIG_IO")
        self.assertEqual(info["domain"], hfi.DOMAIN_IPE_BPS)
        self.assertEqual(info["nNumHandles"], 1)
        self.assertEqual(info["hHandles"], [9])
        self.assertEqual(info["nUserArg1"], 0xCC)
        self.assertEqual(info["nUserArg2"], 0xDD)
        self.assertEqual(info["payload"], payload)
        self.assertTrue(info["payload_size_ok"])
        self.assertTrue(info["size_ok"])

    def test_async_ack_layout(self):
        body = bytes(152)                       # _IpeConfigIoDone
        pkt = struct.pack("<II", 36 + len(body) - 4, 0x0102000A) \
            + struct.pack("<I", 0x05) + struct.pack("<Q", 1) \
            + struct.pack("<Q", 2) + struct.pack("<I", 0) + body
        info = hfi.decode(pkt)
        self.assertEqual(info["kind"], "async_ack")
        self.assertEqual(info["eCmdOpcode"], 0x05)
        self.assertEqual(info["eErrorType"], 0)
        self.assertEqual(info["cmdPayload"], body)

    def test_unknown_packet_is_reported_not_fatal(self):
        pkt = struct.pack("<II", 8, 0x0F020001)
        info = hfi.decode(pkt)
        self.assertEqual(info["kind"], "unknown")
        self.assertEqual(info["domain"], 0x0F & 0x7)

    def test_short_packet_raises(self):
        with self.assertRaises(ValueError):
            hfi.decode(b"\x00\x00\x00")

    def test_firmware_accepts_and_rejects(self):
        good = hfi.encode_async(0x01, [1], bytes(840), direct=True)
        self.assertTrue(hfi.firmware_accepts(good)[0])
        truncated = good[:-4]
        ok, reason = hfi.firmware_accepts(truncated)
        self.assertFalse(ok)
        self.assertIn("nSize", reason)


class QueueTable(unittest.TestCase):
    def test_roundtrip(self):
        blob = hfi.queue_header_bytes([
            {"start_addr": 0x81000000, "type": hfi.Q_CMD, "q_size": 2048},
            {"start_addr": 0x81002000, "type": hfi.Q_MSG, "q_size": 2048},
            {"start_addr": 0x81004000, "type": hfi.Q_DBG, "q_size": 512},
        ])
        self.assertEqual(len(blob), 24 + 3 * 956)
        self.assertLessEqual(len(blob), hfi.QTBL_MAP_WINDOW)
        parsed = hfi.decode_queue_table(blob)
        self.assertEqual(parsed["num_queues"], 3)
        self.assertEqual(parsed["q_hdr0_offset"], 24)
        self.assertEqual(parsed["q_hdr_size"], 956)
        self.assertEqual(parsed["version"], 0xFFFFFFFF)
        self.assertEqual(parsed["queues"][0]["type"], hfi.Q_CMD)
        self.assertEqual(parsed["queues"][1]["type"], hfi.Q_MSG)
        self.assertEqual(parsed["queues"][2]["type"], hfi.Q_DBG)
        self.assertEqual(parsed["queues"][1]["start_addr"], 0x81002000)
        self.assertEqual(parsed["queues"][1]["q_size"], 2048)

    def test_field_offsets(self):
        blob = hfi.queue_header_bytes([{"start_addr": 0xAABBCCDD,
                                        "type": hfi.Q_MSG, "q_size": 0x10}])
        base = 24
        self.assertEqual(struct.unpack_from("<I", blob, base + 124)[0], 0xAABBCCDD)
        self.assertEqual(struct.unpack_from("<I", blob, base + 188)[0], 1)
        self.assertEqual(struct.unpack_from("<I", blob, base + 252)[0], 0x10)

    def test_rejects_too_many_queues(self):
        with self.assertRaises(ValueError):
            hfi.queue_header_bytes([
                {"start_addr": i * 0x1000, "type": 0, "q_size": 8} for i in range(4)
            ])

    def test_rejects_table_outside_the_window(self):
        with self.assertRaises(ValueError):
            hfi.queue_header_bytes([
                {"start_addr": 0, "type": 0, "q_size": 8},
            ], qhdr0_offset=0x2000)


class ConfigIoTemplate(unittest.TestCase):
    def test_payload_sizes(self):
        bps = mk.build_bps_config_io(user_arg=0x1234)
        ipe = mk.build_ipe_config_io(user_arg=0x1234)
        self.assertEqual(len(bps), 840)
        self.assertEqual(len(ipe), 1600)
        self.assertEqual(struct.unpack_from("<Q", bps, 832)[0], 0x1234)
        self.assertEqual(struct.unpack_from("<Q", ipe, 1592)[0], 0x1234)

    def test_descriptor_is_in_slot_zero(self):
        bps = mk.build_bps_config_io()
        self.assertEqual(struct.unpack_from("<I", bps, 0)[0], mk.IMAGE_FORMAT_BAYER_10)
        self.assertEqual(struct.unpack_from("<I", bps, 4)[0], 4000)   # width
        self.assertEqual(struct.unpack_from("<I", bps, 8)[0], 3000)   # height
        self.assertEqual(struct.unpack_from("<I", bps, 60)[0], 4096)  # stride
        # only slot 0 is populated: slot 1 starts at 92 and is empty
        self.assertEqual(bps[92:184], bytes(92))

    def test_ipe_tail_fields(self):
        ipe = mk.build_ipe_config_io(ica_max_upscaling_q16=0x10000,
                                     max_batch_size=3, mux_mode=1,
                                     topology_type=5)
        self.assertEqual(struct.unpack_from("<I", ipe, 1572)[0], 0)        # secureMode
        self.assertEqual(struct.unpack_from("<I", ipe, 1576)[0], 0x10000)  # ica
        self.assertEqual(struct.unpack_from("<I", ipe, 1580)[0], 3)        # batch
        self.assertEqual(struct.unpack_from("<I", ipe, 1584)[0], 1)        # mux
        self.assertEqual(struct.unpack_from("<I", ipe, 1588)[0], 5)        # topology

    def test_packets_fit_and_are_accepted(self):
        for name, pkt in mk.build_packets(user_arg=0x99, handle=2):
            self.assertLessEqual(len(pkt), hfi.MAX_PKT_SIZE, name)
            ok, reason = hfi.firmware_accepts(pkt)
            self.assertTrue(ok, "%s: %s" % (name, reason))
            info = hfi.decode(pkt)
            self.assertEqual(info["hHandles"], [2])
            self.assertEqual(info["nUserArg1"], 0x99)
            self.assertEqual(len(info["payload"]), info["expected_payload_size"])


class OtherPackets(unittest.TestCase):
    def test_system_commands(self):
        self.assertEqual(len(hfi.encode_sys_command(hfi.HFI_CMD_SYS_INIT)), 8)
        self.assertEqual(len(hfi.encode_sys_command(hfi.HFI_CMD_SYS_PING, 5)), 16)
        self.assertEqual(len(hfi.encode_sys_command(hfi.HFI_CMD_SYS_RESET, 5)), 16)
        with self.assertRaises(ValueError):
            hfi.encode_sys_command(hfi.HFI_CMD_SYS_GET_PROPERTY)
        with self.assertRaises(ValueError):
            hfi.encode_sys_command(hfi.HFI_CMD_SYS_INIT, 1)

    def test_set_property_lengths(self):
        # 12 B header + 12 B per property: one property is 24 B on the wire
        one = hfi.encode_set_property([(2, 0x1234, 0)])
        self.assertEqual(len(one), 24)
        self.assertEqual(struct.unpack_from("<I", one, 0)[0], 24)
        self.assertEqual(struct.unpack_from("<I", one, 8)[0], 1)
        self.assertEqual(struct.unpack_from("<I", one, 12)[0], 2)
        two = hfi.encode_set_property([(2, 0, 0), (9, 0xAA, 0xBB)])
        self.assertEqual(len(two), 36)
        info = hfi.decode(two)
        self.assertEqual(info["kind"], "set_property")
        self.assertEqual(info["nNumProperties"], 2)
        self.assertEqual(info["properties"][1]["id"], 9)
        self.assertEqual(info["properties"][1]["value1"], 0xBB)

    def test_create_handle_packet(self):
        pkt = hfi.encode_create_handle(1, user_arg1=0x11, user_arg2=0x22)
        self.assertEqual(struct.unpack_from("<I", pkt, 0)[0], 28)
        self.assertEqual(struct.unpack_from("<I", pkt, 4)[0],
                         hfi.HFI_CMD_IPEBPS_CREATE_HANDLE)
        self.assertEqual(struct.unpack_from("<I", pkt, 8)[0], 1)
        info = hfi.decode(pkt)
        self.assertEqual(info["kind"], "create_handle")
        self.assertEqual(info["eHandleType"], 1)
        self.assertEqual(info["nUserArg1"], 0x11)


if __name__ == "__main__":
    unittest.main(verbosity=2)
