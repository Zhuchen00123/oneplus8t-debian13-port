#!/usr/bin/env python3
"""Guarded boot_a/modules update. Default installs a read-only charging backend."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import tarfile
import time
import zlib


def digest(path, length=None):
    h = hashlib.sha256()
    with path.open('rb') as f:
        left = length
        while left is None or left > 0:
            data = f.read(1024 * 1024 if left is None else min(left, 1024 * 1024))
            if not data:
                if left:
                    raise ValueError('Short read')
                break
            h.update(data)
            if left is not None:
                left -= len(data)
    return h.hexdigest()


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def replace_boot(boot, image, rollback, previous_sha, input_sha):
    try:
        run('dd', 'if=' + str(image), 'of=' + str(boot), 'bs=4M', 'conv=fsync', 'status=none')
        if digest(boot, image.stat().st_size) != input_sha:
            raise RuntimeError('Candidate readback mismatch')
    except Exception as exc:
        # Also restore after a partially successful dd/I/O error, not only bad readback.
        try:
            run('dd', 'if=' + str(rollback), 'of=' + str(boot), 'bs=4M', 'conv=fsync', 'status=none')
            if digest(boot) != previous_sha:
                raise RuntimeError('Full restoration hash mismatch')
        except Exception as restore_exc:
            raise RuntimeError('Boot restore unverified; DO NOT reboot; ' + str(restore_exc)) from exc
        raise RuntimeError('Candidate update failed; previous boot restored') from exc


def gpt_boot_slots(disk=Path('/dev/sde')):
    """Read/CRC-check both GPT copies. Never writes GPT or slot attributes.

    Qualcomm BootLib's locked PartitionTableUpdate.h uses active bit50,
    priority bits48:49 and unbootable bit55; cmdline often lacks slot suffix.
    """
    with disk.open('rb') as f:
        sector = struct.unpack('I', fcntl.ioctl(f.fileno(), 0x1268, struct.pack('I', 0)))[0]

        def table(lba):
            f.seek(lba * sector)
            header = bytearray(f.read(sector))
            if header[:8] != b'EFI PART':
                raise ValueError('GPT signature mismatch')
            size, expected = struct.unpack_from('<II', header, 12)
            if not 92 <= size <= sector:
                raise ValueError('Invalid GPT header size')
            struct.pack_into('<I', header, 16, 0)
            if zlib.crc32(header[:size]) != expected:
                raise ValueError('GPT header CRC mismatch')
            alternate = struct.unpack_from('<Q', header, 32)[0]
            entries_lba, count, entry_size, entries_crc = struct.unpack_from('<QIII', header, 72)
            if not (1 <= count <= 1024 and 128 <= entry_size <= 4096):
                raise ValueError('Invalid GPT entry limits')
            f.seek(entries_lba * sector)
            entries = f.read(count * entry_size)
            if zlib.crc32(entries) != entries_crc:
                raise ValueError('GPT entries CRC mismatch')
            boot = {}
            for offset in range(0, len(entries), entry_size):
                entry = entries[offset:offset + entry_size]
                name = entry[56:128].decode('utf-16-le').split('\0')[0]
                if name in ('boot_a', 'boot_b'):
                    if name in boot:
                        raise ValueError('Duplicate boot slot entry')
                    boot[name] = struct.unpack_from('<QQQ', entry, 32)
            if set(boot) != {'boot_a', 'boot_b'}:
                raise ValueError('Boot slot entries not unique/present')
            return boot, alternate

        primary, backup_lba = table(1)
        backup, _ = table(backup_lba)
        if primary != backup:
            raise ValueError('Primary/backup slot metadata disagree')
    result = {}
    for name, (start, end, attributes) in primary.items():
        result[name] = {'start_lba': start, 'end_lba': end,
                        'active': bool(attributes & (1 << 50)),
                        'priority': (attributes >> 48) & 3,
                        'successful': bool(attributes & (1 << 54)),
                        'tries_remaining': (attributes >> 51) & 7,
                        'unbootable': bool(attributes & (1 << 55))}
    if not (result['boot_a']['active'] and not result['boot_b']['active'] and
            not result['boot_a']['unbootable'] and
            result['boot_a']['priority'] >= result['boot_b']['priority']):
        raise ValueError('GPT does not confirm A as uniquely active boot slot')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('bundle', type=Path)
    p.add_argument('--mode', choices=('readonly', 'control'), default='readonly')
    p.add_argument('--expected-current-prefix-sha256', required=True)
    p.add_argument('--expected-current-prefix-bytes', required=True, type=int)
    a = p.parse_args()
    if run('id', '-u') != '0' or run('uname', '-m') != 'aarch64':
        raise ValueError('Run as root on the measured KB2000')
    lock = Path('/run/lock/kebab-kernel-update.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    base = a.bundle.resolve()
    m = json.loads((base / 'bundle-manifest.json').read_text())
    if m['version'] != 1 or m['kernelrelease'] != '7.2.0-kebab-debian13-r2-charge-policy+':
        raise ValueError('Unsupported bundle')
    for name, expected in m['files_sha256'].items():
        if Path(name).name != name or digest(base / name) != expected:
            raise ValueError('Bundle hash/path mismatch')
    if run('findmnt', '-n', '-o', 'SOURCE', '/') != '/dev/sda15':
        raise ValueError('Root partition mismatch')
    if run('findmnt', '-n', '-o', 'UUID', '/') != m['rootfs_uuid']:
        raise ValueError('Root UUID mismatch')
    boot = Path('/dev/disk/by-partlabel/boot_a')
    if boot.resolve() != Path('/dev/sde11') or Path('/sys/class/block/sde11/size').read_text().strip() != '196608':
        raise ValueError('Measured boot_a mismatch')
    # ABL appends this property. Keep complete command line private.
    fields = dict(token.split('=', 1) for token in Path('/proc/cmdline').read_text().split() if '=' in token)
    if fields.get('androidboot.slot_suffix') not in (None, '_a'):
        raise ValueError('Boot args disagree with slot A')
    slots = gpt_boot_slots()
    if not slots['boot_a']['successful'] and slots['boot_a']['tries_remaining'] < 2:
        raise ValueError('A-slot retry budget too low; do not reboot or replace boot from Linux')
    if subprocess.run(['pgrep', '-x', 'java'], capture_output=True).returncode == 0:
        raise ValueError('Java workload is active; coordinate kernel maintenance')
    if subprocess.run(['systemctl', 'is-active', '--quiet', 'kebab-charge-policy.service']).returncode == 0:
        raise ValueError('Stop the charge policy and verify restoration before boot update')
    snapshot = Path('/sys/bus/i2c/devices/5-005c/charge_test_snapshot').read_text()
    if 'owned=0' not in snapshot or 'last_error=0' not in snapshot:
        raise ValueError('Charger ownership/restoration unverified')
    if not (0 < a.expected_current_prefix_bytes <= 100663296):
        raise ValueError('Invalid expected boot length')
    if digest(boot, a.expected_current_prefix_bytes) != a.expected_current_prefix_sha256:
        raise ValueError('Current boot differs from reviewed input')
    chosen = 'boot-' + a.mode + '.img'
    if (base / chosen).stat().st_size > 100663296:
        raise ValueError('Boot image exceeds measured partition')
    root = Path('/usr/lib/modules') / m['kernelrelease']
    if root.parent.resolve() != Path('/usr/lib/modules'):
        raise ValueError('Unexpected module-root redirection')
    with tarfile.open(base / 'modules.tar.gz') as tar:
        members = tar.getmembers()
        seen = set()
        total = 0
        for item in members:
            parts = PurePosixPath(item.name).parts
            if (item.name.startswith('/') or '..' in parts or
                    parts[:3] != ('lib', 'modules', m['kernelrelease']) or
                    not (item.isfile() or item.isdir()) or item.name in seen):
                raise ValueError('Unsafe module archive member')
            seen.add(item.name)
            total += item.size
        if shutil.disk_usage('/').free < total + 150 * 1024**2:
            raise ValueError('Insufficient space for preserved module tree and full boot backup')
        backup = Path('/var/backups/kebab-charge-policy') / time.strftime('%Y%m%d-%H%M%S')
        backup.mkdir(parents=True, exist_ok=False, mode=0o700)
        current = backup / 'boot_a.img'
        before = digest(boot)
        with boot.open('rb') as source, current.open('xb') as dest:
            shutil.copyfileobj(source, dest, 4 * 1024**2)
        current.chmod(0o600)
        run('sync')
        if current.stat().st_size != 100663296 or digest(current) != before or digest(boot) != before:
            raise ValueError('Full boot backup verification failed')
        (backup / 'manifest.json').write_text(json.dumps({'boot_sha256': before,
             'boot_bytes': 100663296, 'input_prefix_sha256': a.expected_current_prefix_sha256,
             'target': 'boot_a', 'mode': a.mode, 'slots': slots}, indent=2) + '\n')
        # Preserve existing trees. An identical already-installed tree is reusable.
        if not root.exists():
            for item in members:
                target = Path('/usr') / item.name
                if not target.parent.resolve().is_relative_to(Path('/usr/lib/modules')):
                    raise ValueError('Module path escapes destination')
                if item.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open('xb') as dest:
                        shutil.copyfileobj(tar.extractfile(item), dest)
                    target.chmod(item.mode & 0o777)
    actual = {x.relative_to(root).as_posix(): digest(x) for x in root.rglob('*.ko*')}
    if actual != m['modules']:
        raise ValueError('Installed module content mismatch; boot unchanged')
    run('depmod', '-a', m['kernelrelease'])
    run('sync')
    replace_boot(boot, base / chosen, current, before, m['files_sha256'][chosen])
    print('BOOT_PREFIX_READBACK_VERIFIED; old module trees retained; no charger writes')
    print('ROLLBACK_BACKUP=' + str(current))
    print('BOOT_MODE=' + a.mode + '; manual normal reboot required')


if __name__ == '__main__':
    main()
