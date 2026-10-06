#!/usr/bin/env python3
"""Bounded evdev capture for the physical OnePlus 8T alert slider."""
import argparse
import array
import fcntl
import json
import os
from pathlib import Path
import select
import struct
import time

parser = argparse.ArgumentParser()
parser.add_argument('--seconds', type=int, default=150, choices=range(1, 181))
args = parser.parse_args()
# name sits inside eventN/device; select the enclosing event directory.
devices = [p.parts[-3] for p in Path('/sys/class/input').glob('event*/device/name')
           if p.read_text().strip() == 'OnePlus 8T Alert Slider']
if len(devices) != 1:
    raise SystemExit(f'Expected one slider evdev device; found {devices}')
fd = os.open('/dev/input/' + devices[0], os.O_RDONLY | os.O_NONBLOCK)
event = struct.Struct('@llHHi')
info = array.array('i', [0] * 6)
fcntl.ioctl(fd, 0x80184562, info, True)  # EVIOCGABS(ABS_SND_PROFILE)
print(json.dumps({'initial': info[0], 'range': list(info[1:3]),
                  'device': devices[0]}), flush=True)
deadline = time.monotonic() + args.seconds
profiles = []
buffer = b''
try:
    while time.monotonic() < deadline:
        if not select.select([fd], [], [], max(0, min(1, deadline - time.monotonic())))[0]:
            continue
        data = os.read(fd, event.size * 64)
        if not data:
            break
        buffer += data
        while len(buffer) >= event.size:
            sec, usec, kind, code, value = event.unpack(buffer[:event.size])
            buffer = buffer[event.size:]
            if kind == 3 and code == 0x22:
                profiles.append(value)
                print(json.dumps({'type': kind, 'code': code, 'value': value,
                                  'event_time': sec + usec / 1e6}), flush=True)
    print(json.dumps({'profiles': profiles, 'all_three_seen': set(profiles) == {0, 1, 2}}),
          flush=True)
finally:
    os.close(fd)
