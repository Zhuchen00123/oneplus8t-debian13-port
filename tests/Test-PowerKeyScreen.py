import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / 'scripts/linux/kebab-powerkey-screen.py'
spec = importlib.util.spec_from_file_location('screen_key', path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class Tests(unittest.TestCase):
    def event(self, handler, stamp, value, kind=1, code=116):
        return handler.accept(int(stamp), round((stamp % 1) * 1_000_000), kind, code, value)

    def test_short_release_toggles_once(self):
        h = mod.ShortPress()
        self.assertFalse(self.event(h, 10, 1))
        self.assertFalse(self.event(h, 10.1, 2))
        self.assertTrue(self.event(h, 10.2, 0))
        self.assertFalse(self.event(h, 10.3, 0))

    def test_long_hold_and_bounce_do_not_toggle(self):
        for duration in (0.01, 1.3, 10):
            h = mod.ShortPress()
            self.event(h, 10, 1)
            self.assertFalse(self.event(h, 10 + duration, 0))

    def test_volume_touch_and_unmatched_release_ignored(self):
        h = mod.ShortPress()
        self.assertFalse(self.event(h, 1, 0))
        self.assertFalse(self.event(h, 2, 1, code=115))
        self.assertFalse(self.event(h, 2.1, 0, code=115))
        self.assertFalse(self.event(h, 3, 1, kind=3, code=57))
        self.assertFalse(self.event(h, 3.1, 0))

    def test_dropped_stream_discards_stale_press(self):
        h = mod.ShortPress()
        self.event(h, 1, 1)
        self.assertFalse(self.event(h, 1.1, 0, kind=0, code=3))
        self.assertFalse(self.event(h, 1.2, 0))
        self.assertFalse(self.event(h, 1.3, 0, kind=0, code=0))
        self.assertFalse(self.event(h, 1.4, 0))
        self.event(h, 2, 1)
        self.assertTrue(self.event(h, 2.2, 0))

    def test_realtime_backwards_jump_not_a_short_press(self):
        h = mod.ShortPress()
        self.event(h, 10, 1)
        self.assertFalse(self.event(h, 9, 0))


if __name__ == '__main__':
    unittest.main(verbosity=2)
