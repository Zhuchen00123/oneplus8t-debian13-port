#!/usr/bin/env python3
"""Read-only phone server health; never modifies charger, power or thermal controls."""
import argparse
import datetime
import json
from pathlib import Path
import shutil


def number(path, low, high, scale=1):
    try:
        value = int(path.read_text().strip()) / scale
        return value if low <= value <= high else None
    except (OSError, ValueError):
        return None


def text(path):
    try:
        return path.read_text().strip()
    except OSError:
        return None


def sample(config, sysroot=Path('/sys'), procroot=Path('/proc'), diskroot=Path('/')):
    warnings, batteries, usb, cpus = [], [], [], []
    for supply in sorted((sysroot / 'class/power_supply').glob('*')):
        kind = text(supply / 'type')
        if kind == 'Battery':
            capacity = number(supply / 'capacity', 0, 100)
            temperature = number(supply / 'temp', -50, 150, 10)
            batteries.append({'capacity_percent': capacity, 'temperature_c': temperature,
                              'status': text(supply / 'status'), 'health': text(supply / 'health')})
            if capacity is None or temperature is None:
                warnings.append('battery_measurement_unavailable')
            if capacity is not None and capacity <= config['battery_low_warning_percent']:
                warnings.append('battery_low')
            if capacity is not None and capacity > config['requested_charge_limit_percent']:
                warnings.append('battery_above_requested_limit')
            if temperature is not None and temperature >= config['battery_temperature_warning_c']:
                warnings.append('battery_hot')
        elif kind == 'USB':
            usb.append({'online': number(supply / 'online', 0, 3), 'usb_type': text(supply / 'usb_type')})
    if not batteries:
        warnings.append('battery_measurement_unavailable')
    for zone in (sysroot / 'class/thermal').glob('thermal_zone*'):
        if (text(zone / 'type') or '').startswith(('cpu', 'cluster')):
            value = number(zone / 'temp', -50, 150, 1000)
            if value is not None:
                cpus.append(value)
    if cpus and max(cpus) >= config['cpu_temperature_warning_c']:
        warnings.append('cpu_hot')
    free_mib = round(shutil.disk_usage(diskroot).free / 1024**2)
    if free_mib < config['disk_free_warning_mib']:
        warnings.append('disk_space_low')
    memory_mib = None
    try:
        values = dict(line.split(':', 1) for line in (procroot / 'meminfo').read_text().splitlines())
        memory_mib = int(values['MemAvailable'].split()[0]) // 1024
        if memory_mib < config['memory_available_warning_mib']:
            warnings.append('memory_available_low')
    except (OSError, KeyError, ValueError):
        warnings.append('memory_measurement_unavailable')
    # This tool has no charger actuation path, including when new sysfs controls appear.
    warnings.append('charge_limit_not_enforced')
    return {'sampled_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'batteries': batteries, 'usb_power_contracts': usb,
            'requested_charge_limit_percent': config['requested_charge_limit_percent'],
            'charge_limit_enforced': False, 'charger_writes_performed': False,
            'charge_limit_reason': 'No validated charge-control backend configured; monitor is read-only',
            'cpu_max_temperature_c': max(cpus) if cpus else None,
            'disk_free_mib': free_mib, 'memory_available_mib': memory_mib,
            'warnings': sorted(set(warnings))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('/etc/kebab/server-health.json'))
    parser.add_argument('--record', action='store_true', help='Update RAM status and log only warning-state changes')
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    expected = {'requested_charge_limit_percent', 'battery_temperature_warning_c', 'battery_low_warning_percent',
                'cpu_temperature_warning_c', 'disk_free_warning_mib', 'memory_available_warning_mib'}
    if set(config) != expected or any(type(value) not in (int, float) for value in config.values()):
        raise ValueError('Invalid health configuration')
    if not 50 <= config['requested_charge_limit_percent'] <= 100:
        raise ValueError('Requested limit outside monitoring range')
    status = sample(config)
    if args.record:
        path = Path('/run/kebab-health/status.json')
        previous = None
        if path.exists():
            previous = json.loads(path.read_text())
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(status, indent=2) + '\n')
        temporary.replace(path)
        if previous is None or previous.get('warnings') != status['warnings']:
            print('KEBAB_HEALTH_STATE ' + json.dumps(status), flush=True)
    else:
        print(json.dumps(status, indent=2))


if __name__ == '__main__':
    main()
