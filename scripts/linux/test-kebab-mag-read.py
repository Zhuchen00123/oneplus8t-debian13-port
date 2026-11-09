#!/usr/bin/env python3
"""Offline tests for kebab-mag-read.py (no device access)."""
import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("magread", HERE / "kebab-mag-read.py")
magread = importlib.util.module_from_spec(spec)
spec.loader.exec_module(magread)


class FrameConversionTest(unittest.TestCase):
    def test_chip_to_device_mapping_matches_e28(self):
        # E28: chip +Z = device +Z, chip +X = device +Y, chip +Y = device -X
        self.assertEqual(magread.to_device_frame(1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
        self.assertEqual(magread.to_device_frame(0.0, 1.0, 0.0), (-1.0, 0.0, 0.0))
        self.assertEqual(magread.to_device_frame(0.0, 0.0, 1.0), (0.0, 0.0, 1.0))

    def test_mapping_is_orthonormal(self):
        for axis in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
            out = magread.to_device_frame(*axis)
            self.assertAlmostEqual(math.sqrt(sum(v * v for v in out)), 1.0)

    def test_mapping_preserves_magnitude(self):
        x, y, z = -25.0, -2.3, -37.2
        dx, dy, dz = magread.to_device_frame(x, y, z)
        self.assertAlmostEqual(math.sqrt(dx*dx + dy*dy + dz*dz),
                               math.sqrt(x*x + y*y + z*z), places=9)


class HeadingTest(unittest.TestCase):
    def test_north_is_zero(self):
        self.assertAlmostEqual(magread.heading_degrees(0.0, 30.0), 0.0)

    def test_clockwise_rotation_increases_heading(self):
        self.assertAlmostEqual(magread.heading_degrees(-30.0, 0.0), 90.0)
        self.assertAlmostEqual(magread.heading_degrees(0.0, -30.0), 180.0)
        self.assertAlmostEqual(magread.heading_degrees(30.0, 0.0), 270.0)

    def test_heading_always_in_range(self):
        for deg in range(0, 360, 7):
            rad = math.radians(deg)
            value = magread.heading_degrees(math.cos(rad), math.sin(rad))
            self.assertGreaterEqual(value, 0.0)
            self.assertLess(value, 360.0)

    def test_zero_vector_does_not_raise(self):
        self.assertAlmostEqual(magread.heading_degrees(0.0, 0.0), 0.0)


class RotationTest(unittest.TestCase):
    # Real sample captured on the device (2026-11-10 01:11): the geomag_rv
    # quaternion plus the chip-frame accel and mag of the same instant.
    MEASURED_Q = (-0.008592, -0.000979, -0.699112, 0.71496)
    MEASURED_ACCEL = (0.131, -0.105, 9.639)
    MEASURED_MAG = (-25.195, -2.246, -37.305)

    @staticmethod
    def yaw_quaternion(degrees):
        half = math.radians(degrees) / 2.0
        return (0.0, 0.0, math.sin(half), math.cos(half))

    def test_identity_quaternion_puts_top_edge_east(self):
        # R = I: chip +X (the phone top edge) points at +X = east -> heading 90.
        self.assertAlmostEqual(magread.heading_from_rotation((0.0, 0.0, 0.0, 1.0)), 90.0)

    def test_yaw_maps_to_azimuth_with_ninety_degree_offset(self):
        # A counter-clockwise yaw of t takes chip +X from east toward north, and
        # heading is measured clockwise from north, so the heading is 90 - t.
        for deg in (0.0, 30.0, 90.0, 135.0, 270.0):
            value = magread.heading_from_rotation(self.yaw_quaternion(deg))
            expected = (90.0 - deg) % 360.0
            delta = abs((value - expected + 180.0) % 360.0 - 180.0)
            self.assertLess(delta, 1e-6, (deg, value))

    def test_measured_quaternion_rotates_the_field_onto_north(self):
        # Physical invariant: in the world frame the magnetic field must point
        # north with the vertical part down.  This is what proved the rotation
        # vector is expressed in the chip frame.
        matrix = magread.rotation_matrix(self.MEASURED_Q)
        world = [sum(matrix[i][k] * self.MEASURED_MAG[k] for k in range(3)) for i in range(3)]
        self.assertGreater(world[1], 20.0)          # north component
        self.assertLess(abs(world[0]), 5.0)         # east component ~ 0
        self.assertLess(world[2], -30.0)            # down
        self.assertAlmostEqual(math.sqrt(sum(c*c for c in world)), 45.07, places=1)

    def test_measured_heading_matches_the_flat_estimate(self):
        # The mag-only flat formula is a different derivation; it must agree
        # within a few degrees or one of the two frames is wrong.
        measured = magread.heading_from_rotation(self.MEASURED_Q)
        dx, dy, _ = magread.to_device_frame(*self.MEASURED_MAG)
        flat = magread.heading_degrees(dx, dy)
        delta = abs((measured - flat + 180.0) % 360.0 - 180.0)
        self.assertLess(delta, 10.0, (measured, flat))

    def test_measured_accel_maps_onto_gravity(self):
        matrix = magread.rotation_matrix(self.MEASURED_Q)
        world = [sum(matrix[i][k] * self.MEASURED_ACCEL[k] for k in range(3)) for i in range(3)]
        self.assertAlmostEqual(world[2], 9.64, places=1)
        self.assertLess(abs(world[0]), 0.3)
        self.assertLess(abs(world[1]), 0.3)

    def test_quaternion_is_normalised_before_use(self):
        scaled = tuple(v * 7.0 for v in self.yaw_quaternion(45.0))
        self.assertAlmostEqual(magread.heading_from_rotation(scaled), 45.0, places=6)

    def test_rotation_matrix_is_orthonormal(self):
        matrix = magread.rotation_matrix(self.yaw_quaternion(37.0))
        for row in matrix:
            self.assertAlmostEqual(math.sqrt(sum(v * v for v in row)), 1.0, places=9)
        for i in range(3):
            for j in range(3):
                dot = sum(matrix[i][k] * matrix[j][k] for k in range(3))
                self.assertAlmostEqual(dot, 1.0 if i == j else 0.0, places=9)

    def test_wrong_width_rejected(self):
        with self.assertRaises(ValueError):
            magread.rotation_matrix((0.0, 0.0, 1.0))

    def test_zero_quaternion_rejected(self):
        with self.assertRaises(ValueError):
            magread.rotation_matrix((0.0, 0.0, 0.0, 0.0))


class SummaryTest(unittest.TestCase):
    def test_mag_only_uses_flat_heading(self):
        rec = magread.summarise({"mag": (-25.5, -2.0, -36.8)}, 25.0, {"mag": 5})
        self.assertEqual(rec["name"], "mmc5603x")
        self.assertEqual(rec["samples"], 5)
        self.assertEqual(rec["rate_hz"], 25.0)
        self.assertAlmostEqual(rec["chip"]["z"], -36.8, places=3)
        self.assertAlmostEqual(rec["device"]["x"], 2.0, places=3)
        self.assertAlmostEqual(rec["device"]["y"], -25.5, places=3)
        self.assertAlmostEqual(rec["magnitude_ut"],
                               round(math.sqrt(25.5**2 + 2.0**2 + 36.8**2), 3), places=3)
        self.assertAlmostEqual(rec["horizontal_ut"],
                               round(math.hypot(2.0, -25.5), 3), places=3)
        self.assertNotIn("orientation_source", rec)
        self.assertEqual(rec["heading_deg"], rec["heading_flat_deg"])

    def test_geomag_rotation_preferred_over_flat(self):
        quaternion = RotationTest.yaw_quaternion(120.0)
        rec = magread.summarise({"mag": (-25.0, -2.0, -37.0), "geomag_rv": quaternion}, 10.0,
                                {"mag": 3, "geomag_rv": 3})
        self.assertEqual(rec["orientation_source"], "geomag_rv")
        self.assertAlmostEqual(rec["heading_deg"], 330.0, places=3)  # 90 - 120
        self.assertNotEqual(rec["heading_deg"], rec["heading_flat_deg"])
        self.assertEqual(rec["per_sensor"], {"mag": 3, "geomag_rv": 3})
        self.assertEqual(rec["samples"], 6)

    def test_rotv_is_the_fallback(self):
        rec = magread.summarise({"rotv": RotationTest.yaw_quaternion(200.0)}, 10.0, {"rotv": 1})
        self.assertEqual(rec["orientation_source"], "rotv")
        self.assertAlmostEqual(rec["heading_deg"], 250.0, places=3)  # 90 - 200

    def test_other_datatypes_are_kept(self):
        rec = magread.summarise({"mag": (1.0, 2.0, 3.0), "accel": (0.1, 0.2, 9.8)}, 10.0,
                                {"mag": 1, "accel": 1})
        self.assertEqual(rec["values"]["accel"], [0.1, 0.2, 9.8])

    def test_empty_is_rejected(self):
        with self.assertRaises(ValueError):
            magread.summarise({}, 10.0)


class PublishTest(unittest.TestCase):
    def test_atomic_publish_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "mag.json"
            magread.publish({"a": 1}, path=target)
            self.assertEqual(json.loads(target.read_text()), {"a": 1})
            magread.publish({"a": 2}, path=target)
            self.assertEqual(json.loads(target.read_text()), {"a": 2})
            leftovers = [p.name for p in Path(tmp).iterdir() if p.name.startswith(".kebab-mag-")]
            self.assertEqual(leftovers, [])

    def test_publish_creates_parent_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "run" / "mag.json"
            magread.publish({"a": 1}, path=target)
            self.assertTrue(target.exists())


class GuardTest(unittest.TestCase):
    def test_rate_allowlist(self):
        self.assertEqual(magread.ALLOWED_RATES, (5.0, 10.0, 15.0, 25.0, 50.0, 100.0))
        self.assertIn(magread.DEFAULT_RATE, magread.ALLOWED_RATES)

    def test_reader_rejects_unknown_rate_before_touching_the_device(self):
        with self.assertRaises(ValueError):
            magread.Reader(rate=7.0)

    def test_reader_rejects_unknown_datatype(self):
        with self.assertRaises(ValueError):
            magread.Reader(targets=("bogus",), rate=10.0)

    def test_reader_rejects_empty_targets(self):
        with self.assertRaises(ValueError):
            magread.Reader(targets=(), rate=10.0)

    def test_only_disable_and_enable_mids_can_be_sent(self):
        reader = magread.Reader(rate=10.0)
        with self.assertRaises(ValueError):
            reader._send(999, (1, 2))

    def test_widths_cover_every_default_target(self):
        for target in magread.DEFAULT_TARGETS:
            self.assertIn(target, magread.WIDTHS)

class BatteryGuardTest(unittest.TestCase):
    def test_read_capacity_parses_the_sysfs_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capacity"
            path.write_text("93\n")
            self.assertEqual(magread.read_capacity(path), 93.0)

    def test_read_capacity_returns_none_when_missing(self):
        self.assertIsNone(magread.read_capacity(Path(tempfile.gettempdir()) / "no-such-capacity"))

    def test_low_battery_predicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capacity"
            path.write_text("15")
            self.assertTrue(magread.battery_is_low(path, 20.0))
            path.write_text("20")
            self.assertFalse(magread.battery_is_low(path, 20.0))
            path.write_text("93")
            self.assertFalse(magread.battery_is_low(path, 20.0))

    def test_guard_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capacity"
            path.write_text("1")
            self.assertFalse(magread.battery_is_low(path, 0))

    def test_unreadable_battery_never_trips_the_guard(self):
        self.assertFalse(magread.battery_is_low(Path(tempfile.gettempdir()) / "nope", 20.0))


if __name__ == "__main__":
    unittest.main()
