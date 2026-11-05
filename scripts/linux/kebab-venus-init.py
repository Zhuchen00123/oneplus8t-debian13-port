#!/usr/bin/env python3
"""Validate the tested kebab OEM firmware; load the decoder only with --load."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

RELEASE = '7.2.0-kebab-debian13-r4-venus+'
MANIFEST = Path('/etc/kebab/venus-firmware-manifest.json')
# This public template fails closed until a device owner configures the local manifest.
MANIFEST_SHA = 'REPLACE_WITH_LOCAL_MANIFEST_SHA256'
FIRMWARE = Path('/usr/lib/firmware/qcom/sm8250/oneplus/kebab/oem-14.0.0.602')
ALIASES = Path('/usr/lib/firmware/postmarketos')
NAMES = {'venus.mdt', *(f'venus.b{i:02d}' for i in range(11)), 'venus.b19'}


def validate_firmware(manifest=MANIFEST, firmware=FIRMWARE, aliases=ALIASES):
    raw = manifest.read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA:
        raise ValueError('OEM firmware manifest identity mismatch')
    data = json.loads(raw)
    if set(data['files']) != NAMES:
        raise ValueError('Unexpected OEM split firmware set')
    for name, identity in data['files'].items():
        path = firmware / name
        stat = path.stat()
        if not path.is_file() or stat.st_uid != 0 or stat.st_mode & 0o022:
            raise ValueError('OEM firmware is not a root-owned protected file')
        if stat.st_size != identity['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest() != identity['sha256']:
            raise ValueError('OEM firmware content mismatch: ' + name)
        alias = aliases / ('venus.mbn' if name == 'venus.mdt' else name)
        if not alias.is_symlink() or alias.resolve(strict=True) != path.resolve(strict=True):
            raise ValueError('OEM firmware alias mismatch: ' + name)
    return len(data['files'])


def decoder_nodes(root=Path('/sys/class/video4linux')):
    found = []
    for node in root.glob('video*'):
        if (node / 'name').read_text().strip() != 'qcom-venus-decoder':
            continue
        device = (node / 'device').resolve()
        for parent in (device, *device.parents):
            driver = parent / 'driver'
            if driver.is_symlink() and driver.resolve().name == 'qcom-venus':
                if (driver / 'module').resolve().name == 'venus_core':
                    found.append('/dev/' + node.name)
                    break
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--load', action='store_true', help='Explicitly load core and decoder; default checks firmware only')
    args = parser.parse_args()
    if os.geteuid() != 0 or subprocess.check_output(['uname', '-r'], text=True).strip() != RELEASE:
        raise SystemExit('Root on the verified r4 kernel required')
    count = validate_firmware()
    if not args.load:
        print(json.dumps({'firmware_files_verified': count, 'modules_requested': False}))
        return
    for module in ('venus_core', 'venus_dec'):
        subprocess.run(['modprobe', module], check=True, timeout=20)
    for _ in range(50):
        nodes = decoder_nodes()
        if len(nodes) == 1 and Path(nodes[0]).is_char_device():
            print(json.dumps({'firmware_files_verified': count, 'decoder': nodes[0],
                              'driver': 'qcom-venus', 'module': 'venus_core',
                              'encoder_requested': False}))
            return
        time.sleep(0.2)
    raise SystemExit('Venus decoder did not bind; module command success alone is insufficient')


if __name__ == '__main__':
    main()
