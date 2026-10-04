#!/usr/bin/env python3
"""Short KEY_POWER releases toggle OLED brightness; never requests shutdown."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import struct
import time

BACKLIGHT = Path('/sys/class/backlight/ae94000.dsi.0')
EVENT = struct.Struct('<qqHHi')  # Verified aarch64 struct input_event (24 bytes).
EV_KEY, KEY_POWER, EV_SYN, SYN_REPORT, SYN_DROPPED = 1, 116, 0, 0, 3


class ShortPress:
    def __init__(self):
        self.pressed_at = None
        self.dropped = False

    def accept(self, seconds, micros, event_type, code, value):
        stamp = seconds + micros / 1_000_000
        if event_type == EV_SYN and code == SYN_DROPPED:
            self.pressed_at = None
            self.dropped = True
            return False
        if self.dropped:
            if event_type == EV_SYN and code == SYN_REPORT:
                self.dropped = False
            return False
        if event_type != EV_KEY or code != KEY_POWER:
            return False
        if value == 1 and self.pressed_at is None:
            self.pressed_at = stamp
        elif value == 0:
            before, self.pressed_at = self.pressed_at, None
            return before is not None and 0.03 <= stamp - before <= 1.2
        return False


def key_device():
    matches = [p for p in Path('/sys/class/input').glob('event*')
               if (p / 'device/name').read_text().strip() == 'pm8941_pwrkey']
    if len(matches) != 1:
        raise ValueError('Expected exactly one measured pm8941_pwrkey')
    return Path('/dev/input') / matches[0].name


def record(action):
    state = {'action': action, 'brightness_requested': int((BACKLIGHT / 'brightness').read_text()),
             'monotonic_seconds': time.monotonic(), 'system_shutdown_requested': False,
             'full_panel_powerdown_verified': False}
    try:
        state['actual_brightness_raw'] = int((BACKLIGHT / 'actual_brightness').read_text())
    except (OSError, ValueError) as error:
        state['actual_readback_error'] = type(error).__name__
    directory = Path('/run/kebab-powerkey-screen')
    directory.mkdir(mode=0o700, exist_ok=True)
    temp = directory / 'status.json.new'
    temp.write_text(json.dumps(state, indent=2) + '\n')
    temp.replace(directory / 'status.json')
    print('KEBAB_SCREEN_STATE ' + json.dumps(state), flush=True)


def set_brightness(value):
    maximum = int((BACKLIGHT / 'max_brightness').read_text())
    if maximum != 4095 or not 0 <= value <= maximum:
        raise ValueError('Unexpected measured backlight range')
    (BACKLIGHT / 'brightness').write_text(str(value) + '\n')
    if int((BACKLIGHT / 'brightness').read_text()) != value:
        raise ValueError('Brightness request readback mismatch')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--listen', action='store_true', help='Default is read-only probe')
    parser.add_argument('--start-off', action='store_true')
    parser.add_argument('--wake-brightness', type=int, default=512)
    args = parser.parse_args()
    if args.start_off and not args.listen:
        raise ValueError('start-off requires explicit listener mode')
    if os.uname().machine != 'aarch64' or not 1 <= args.wake_brightness <= 1024:
        raise ValueError('Expected measured aarch64 device and conservative wake setting')
    device = key_device()
    if not args.listen:
        print(json.dumps({'key_device': str(device), 'backlight_max': int((BACKLIGHT / 'max_brightness').read_text()),
                          'current_brightness': int((BACKLIGHT / 'brightness').read_text()), 'writes': False}))
        return
    directory = Path('/run/kebab-powerkey-screen')
    directory.mkdir(mode=0o700, exist_ok=True)
    lock = (directory / 'listener.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    try:
        # EVIOCSCLOCKID(CLOCK_MONOTONIC); prevent NTP steps misclassifying a hold.
        fcntl.ioctl(fd, 0x400445A0, struct.pack('i', time.CLOCK_MONOTONIC))
        # No EVIOCGRAB: other handlers remain attached. logind keeps ignore.
        if args.start_off:
            set_brightness(0)
        record('listener_started')
        parser_state, pending = ShortPress(), b''
        while running:
            readable, _, _ = select.select([fd], [], [], 1)
            if not readable:
                continue
            data = os.read(fd, EVENT.size * 32)
            if not data:
                raise OSError('Power key disappeared')
            pending += data
            while len(pending) >= EVENT.size:
                item, pending = pending[:EVENT.size], pending[EVENT.size:]
                if parser_state.accept(*EVENT.unpack(item)):
                    before = int((BACKLIGHT / 'brightness').read_text())
                    set_brightness(args.wake_brightness if before == 0 else 0)
                    record('short_press_wake' if before == 0 else 'short_press_dark')
    finally:
        os.close(fd)
        # Do not unexpectedly wake the screen or request shutdown on service stop.


if __name__ == '__main__':
    main()
