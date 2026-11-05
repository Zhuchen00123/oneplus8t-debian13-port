#!/usr/bin/env python3
"""Bounded GPU-trial observations; reads only, apart from its exclusive log."""
import argparse
import json
import os
from pathlib import Path
import time


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=120)
    parser.add_argument('--units', nargs='+', required=True)
    args = parser.parse_args()
    if not 5 <= args.seconds <= 300 or any('/' in unit or not unit.endswith('.service') for unit in args.units):
        parser.error('Bounded duration 5..300 and plain service unit names required')
    temperatures = {}
    for path in Path('/sys/class/thermal').glob('thermal_zone*'):
        kind = read(path / 'type') or ''
        if kind.startswith(('cpu', 'gpu', 'cluster')):
            temperatures[kind] = path / 'temp'
    battery = Path('/sys/class/power_supply/bq27541-0')
    source = Path('/sys/class/power_supply/tcpm-source-psy-c440000.spmi:pmic@2:typec@1500')
    deadline = time.monotonic() + args.seconds
    total, count = 0, 0
    fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as log:
        while True:
            units = {}
            for unit in args.units:
                group = Path('/sys/fs/cgroup/system.slice') / unit
                units[unit] = {key: read(group / key) for key in ('cgroup.procs', 'memory.current', 'cpu.stat')}
            data = {'monotonic': time.monotonic(), 'wall_time': time.time(),
                    'boot_id': read('/proc/sys/kernel/random/boot_id'),
                    'usb_online': read(source / 'online'),
                    'charge_snapshot': read('/sys/bus/i2c/devices/5-005c/charge_test_snapshot'),
                    'charge_lease': read('/sys/bus/i2c/devices/5-005c/charge_control_lease'),
                    'battery': {key: read(battery / key) for key in ('capacity', 'temp', 'voltage_now', 'current_now')},
                    'thermal_millic': {kind: read(path) for kind, path in temperatures.items()},
                    'cpu_total': (read('/proc/stat') or '').splitlines()[:1], 'units': units}
            line = (json.dumps(data) + '\n').encode()
            if total + len(line) > 524288:
                break
            log.write(line)
            log.flush()
            os.fsync(log.fileno())
            count += 1
            total += len(line)
            if time.monotonic() >= deadline:
                break
            time.sleep(min(2, max(0, deadline - time.monotonic())))
    print(json.dumps({'samples': count, 'bytes': total}))


if __name__ == '__main__':
    main()
