#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('state', Path(__file__).with_name('kebab-ui-state.py'))
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)


def sample(position, count=10, **changes):
    return {'active': True, 'owned': True, 'error': 0, 'age_ms': 20,
            'profile': position, 'reported': position, 'count': count, **changes}


class ModesAndKeys(unittest.TestCase):
    def test_stable_switch_is_idempotent(self):
        gate = state.ModeGate()
        self.assertIsNone(gate.accept(sample(2), 0))
        self.assertEqual(gate.accept(sample(2, 20), .8), 'desktop')
        self.assertIsNone(gate.accept(sample(2, 30), 2))

    def test_crossing_middle_does_not_flash_panel(self):
        gate = state.ModeGate()
        gate.accept(sample(0), 0)
        self.assertEqual(gate.accept(sample(0, 20), 1), 'terminal')
        gate.accept(sample(1, 25), 1.1)
        gate.accept(sample(2, 30), 1.3)
        self.assertEqual(gate.accept(sample(2, 40), 2.1), 'desktop')

    def test_invalid_readings_break_debounce(self):
        for change in ({'age_ms': 501}, {'profile': -1}, {'error': -6},
                       {'reported': 2}, {'active': False}, {'owned': False}, {'profile': True}):
            gate = state.ModeGate()
            gate.accept(sample(0), 0)
            gate.accept(sample(0, 20, **change), 1)
            self.assertIsNone(gate.accept(sample(0, 30), 1.1))

    def test_frozen_samples_and_driver_reload(self):
        gate = state.ModeGate()
        gate.accept(sample(1, 100), 0)
        self.assertIsNone(gate.accept(sample(1, 100), 1))
        self.assertIsNone(gate.accept(sample(1, 3), 2))
        self.assertEqual(gate.accept(sample(1, 12), 2.8), 'panel')

    def test_terminal_control_and_symbol_keys(self):
        self.assertEqual(state.key_bytes('c', ctrl=True), b'\x03')
        self.assertEqual(state.key_bytes('d', ctrl=True), b'\x04')
        self.assertEqual(state.key_bytes('Space', ctrl=True), b'\0')
        self.assertEqual(state.key_bytes('1', shift=True), b'!')
        self.assertEqual(state.key_bytes('/', shift=True), b'?')
        self.assertEqual(state.key_bytes('x', alt=True), b'\x1bx')
        self.assertEqual(state.key_bytes('Up', ctrl=True), b'\x1b[1;5A')
        self.assertEqual(state.key_bytes('Tab', shift=True), b'\x1b[Z')
        with self.assertRaises(ValueError):
            state.key_bytes('COMMAND')

    def test_external_keyboard_detection(self):
        mask = sum(1 << i for i in (14, 16, 28, 30, 57))
        self.assertTrue(state.keyboard_capable(5, f'{mask:x}'))
        self.assertTrue(state.keyboard_capable(3, f'{mask:x}'))
        self.assertFalse(state.keyboard_capable(6, f'{mask:x}'))
        self.assertFalse(state.keyboard_capable(5, f'{1 << 116:x}'))
        self.assertFalse(state.keyboard_capable(5, 'malformed'))


if __name__ == '__main__':
    unittest.main()
