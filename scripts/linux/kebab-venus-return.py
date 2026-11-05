#!/usr/bin/env python3
"""Return from this temporary Venus kernel only when the original boot is intact."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

RELEASE = '7.2.0-kebab-debian13-r4-venus+'
ROOT_UUID = 'bc52fbbe-777b-47c8-84fc-ba56db7a8813'
ORIGINAL_BOOT_SHA256 = '4fab2d9152aa78b41b884e913afb53c9605b5bb5e2ecb821f7e72d20d2603dfa'


def permitted(kernel, root_uuid, boot_sha, manifest):
    return (kernel == RELEASE and root_uuid == ROOT_UUID and
            boot_sha == ORIGINAL_BOOT_SHA256 and
            manifest.get('kernelrelease') == RELEASE and
            manifest.get('current_boot_backup_sha256') == ORIGINAL_BOOT_SHA256 and
            manifest.get('hardware_boot_verified') is False and
            manifest.get('boot_partition_writes') is False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--reboot', action='store_true', help='Explicitly request return; default is check only')
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    kernel = subprocess.check_output(['uname', '-r'], text=True, timeout=10).strip()
    root_uuid = subprocess.check_output(['findmnt', '-no', 'UUID', '/'], text=True, timeout=10).strip()
    with open('/dev/disk/by-partlabel/boot_a', 'rb') as stream:
        boot_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    if not permitted(kernel, root_uuid, boot_sha, manifest):
        raise SystemExit('Candidate/root/original boot identity mismatch; no reboot issued')
    print('Original boot verified; this temporary Venus kernel may return', flush=True)
    if args.reboot:
        subprocess.run(['systemctl', '--no-wall', 'reboot'], check=True, timeout=30)


if __name__ == '__main__':
    main()
