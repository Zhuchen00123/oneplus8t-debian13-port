#!/usr/bin/env python3
"""80/75 charge policy for the MP2762A opt-in, 90-second kernel lease."""
import argparse
import fcntl
import json
from pathlib import Path
import signal
import time

VERSION = 1
DEFAULT_CONFIG = {
    'stop_percent': 80, 'resume_percent': 75, 'interval_seconds': 15,
    'confirm_samples': 3, 'valid_temperature_min_c': 5,
    'valid_temperature_max_c': 43,
}


def config_from(path):
    config = json.loads(path.read_text())
    if set(config) != set(DEFAULT_CONFIG):
        raise ValueError('Unknown or missing configuration field')
    if any(type(v) not in (int, float) for v in config.values()):
        raise ValueError('Configuration values must be numbers')
    if not (50 <= config['resume_percent'] < config['stop_percent'] <= 90):
        raise ValueError('Invalid hysteresis')
    if not (5 <= config['interval_seconds'] <= 20):
        raise ValueError('Sample interval must be 5..20 seconds')
    if type(config['confirm_samples']) is not int or not 2 <= config['confirm_samples'] <= 6:
        raise ValueError('Confirm samples must be 2..6')
    if not (0 <= config['valid_temperature_min_c'] < config['valid_temperature_max_c'] <= 43):
        raise ValueError('Invalid measurement eligibility temperature range')
    return config


def integer(path, low, high):
    result = int(path.read_text().strip())
    if not low <= result <= high:
        raise ValueError(f'Out of range: {path.name}')
    return result


class Backend:
    def __init__(self, sysroot=Path('/sys')):
        self.device = sysroot / 'bus/i2c/devices/5-005c'
        self.behaviour = sysroot / 'class/power_supply/mp2762-charge-test/charge_behaviour'
        self.battery = sysroot / 'class/power_supply/bq27541-0'
        self.source = sysroot / 'class/power_supply/tcpm-source-psy-c440000.spmi:pmic@2:typec@1500'

    def snapshot(self):
        fields = dict(item.split('=', 1) for item in
                      (self.device / 'charge_test_snapshot').read_text().split())
        return {key: int(value, 0) for key, value in fields.items()}

    def sample(self):
        # Reopen gauge files each iteration; cached JSON is never an actuation input.
        return {'capacity': integer(self.battery / 'capacity', 0, 100),
                'temperature_c': integer(self.battery / 'temp', -500, 1500) / 10,
                'online': integer(self.source / 'online', 0, 1),
                'snapshot': self.snapshot()}

    def action(self, action):
        if action == 'renew':
            (self.device / 'charge_control_lease').write_text('renew\n')
        elif action in ('inhibit', 'restore'):
            self.behaviour.write_text('inhibit-charge\n' if action == 'inhibit' else 'auto\n')
        else:
            raise ValueError('Unknown action')

    def restore_owned(self):
        snap = self.snapshot()
        if snap.get('allow_control') == 1 and snap['owned']:
            self.action('restore')
        return self.snapshot()


class Policy:
    def __init__(self, config):
        self.config = config
        self.high = self.low = 0

    def decide(self, sample):
        snap = sample['snapshot']
        cfg = self.config
        owned = bool(snap['owned'])
        safe_ctrl = not (snap['reg08'] & 0xe8) and bool(snap['reg08'] & 2)
        eligible = (sample['online'] == 1 and safe_ctrl and
                    not snap['reg09'] & 0x30 and bool(snap['reg13'] & 2) and
                    not snap['reg13'] & 0xc0 and snap['last_error'] == 0 and
                    not snap.get('recovering', 0) and
                    cfg['valid_temperature_min_c'] <= sample['temperature_c'] <=
                    cfg['valid_temperature_max_c'])
        if not eligible:
            self.high = self.low = 0
            return 'restore' if owned else 'wait'
        if owned:
            self.high = 0
            if snap['reg08'] & 0x10:
                self.low = 0
                return 'restore'  # Unexpected re-enable; relinquish ownership first.
            self.low = self.low + 1 if sample['capacity'] <= cfg['resume_percent'] else 0
            return 'restore' if self.low >= cfg['confirm_samples'] else 'renew'
        self.low = 0
        if not snap['reg08'] & 0x10:
            self.high = 0
            return 'wait'  # Never enable charging disabled by another owner/firmware.
        self.high = self.high + 1 if sample['capacity'] >= cfg['stop_percent'] else 0
        return 'inhibit' if self.high >= cfg['confirm_samples'] else 'wait'


def record(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, indent=2) + '\n')
    temporary.replace(path)


def step(policy, backend, control):
    sample = backend.sample()
    snap = sample['snapshot']
    supported = snap.get('allow_control') == 1 and snap.get('lease_seconds') == 90
    action = policy.decide(sample)
    if control and not supported:
        raise RuntimeError('90s charge-control mode is not enabled; refusing writes')
    if control and action != 'wait':
        backend.action(action)
        snap = backend.snapshot()
        if action in ('inhibit', 'renew') and not (
                snap['owned'] == 1 and not snap['reg08'] & 0x10 and
                snap['last_error'] == 0 and snap.get('recovering') == 0):
            raise RuntimeError('Charge-inhibit readback failed')
        if action == 'restore' and snap['owned']:
            raise RuntimeError('Charge restoration unverified')
    return {'version': VERSION, 'monotonic_seconds': time.monotonic(),
            'requested_action': action, 'control_enabled': control,
            'stop_percent': policy.config['stop_percent'],
            'resume_percent': policy.config['resume_percent'],
            'capacity_percent': sample['capacity'], 'temperature_c': sample['temperature_c'],
            'online': sample['online'], 'snapshot': snap,
            'inhibit_readback_verified': bool(control and snap['owned'] and
                                             not snap['reg08'] & 0x10 and
                                             snap['last_error'] == 0),
            'full_charge_cycle_validated': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('/etc/kebab/charge-policy.json'))
    parser.add_argument('--control', action='store_true', help='Actuate opt-in lease backend')
    parser.add_argument('--once', action='store_true', help='Read/plan once; incompatible with control')
    parser.add_argument('--sysroot', type=Path, default=Path('/sys'))
    parser.add_argument('--state', type=Path, default=Path('/run/kebab-charge-policy/status.json'))
    args = parser.parse_args()
    if args.control and (args.once or args.sysroot != Path('/sys')):
        parser.error('Control requires daemon operation on real /sys')
    config = config_from(args.config)
    backend = Backend(args.sysroot)
    policy = Policy(config)
    if args.once:
        print(json.dumps(step(policy, backend, False), indent=2))
        return
    if not args.control:
        parser.error('Daemon requires explicit --control; use --once for read-only inspection')
    args.state.parent.mkdir(parents=True, exist_ok=True)
    lock = (args.state.parent / 'owner.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    previous = None
    authorized = False
    failure = None
    try:
        snap = backend.snapshot()
        if snap.get('allow_control') != 1 or snap.get('lease_seconds') != 90:
            raise RuntimeError('Control mode is disabled in kernel')
        authorized = True
        while not stopping:
            status = step(policy, backend, True)
            record(args.state, status)
            key = (status['requested_action'], status['snapshot']['owned'])
            if key != previous:
                print('KEBAB_CHARGE_STATE ' + json.dumps(status), flush=True)
                previous = key
            deadline = time.monotonic() + config['interval_seconds']
            while not stopping and time.monotonic() < deadline:
                time.sleep(min(0.5, max(0, deadline - time.monotonic())))
    except Exception as exc:
        failure = str(exc)
        record(args.state, {'version': VERSION, 'monotonic_seconds': time.monotonic(),
                            'control_enabled': False, 'error': str(exc)})
        raise
    finally:
        if authorized:
            # Best effort; kernel's independent deadline also restores after process loss.
            try:
                snapshot = backend.restore_owned()
                record(args.state, {'version': VERSION, 'monotonic_seconds': time.monotonic(),
                                    'control_enabled': False, 'stopped': True,
                                    'snapshot': snapshot, 'restore_verified': not snapshot['owned'],
                                    'error': failure})
                print('KEBAB_CHARGE_STOP ' + json.dumps(snapshot), flush=True)
            except Exception as exc:
                print('KEBAB_CHARGE_RESTORE_UNVERIFIED ' + str(exc), flush=True)
        lock.close()


if __name__ == '__main__':
    main()
