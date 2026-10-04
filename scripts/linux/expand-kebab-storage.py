#!/usr/bin/env python3
"""Measured KB2000 storage maintenance. Default preview; erase needs two flags."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import time
import uuid
import zlib

ROOT_BYTES = 7516192768
DATA_BYTES = 241008750592
STATE = Path('/var/lib/kebab-storage-deployment')


def run(*args):
    return subprocess.check_output(args, text=True, env={**os.environ, 'LC_ALL': 'C'}).strip()


def digest_file(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def gpt_identity():
    """CRC-validate both GPT copies; record hashes and measured target extents."""
    with Path('/dev/sda').open('rb', buffering=0) as f:
        sector = struct.unpack('I', fcntl.ioctl(f.fileno(), 0x1268, bytes(4)))[0]
        size = struct.unpack('Q', fcntl.ioctl(f.fileno(), 0x80081272, bytes(8)))[0]
        disk_sectors = size // sector
        tables, hashes, targets, geometry = [], {}, {}, []
        for label, lba in (('primary', 1), ('backup', disk_sectors - 1)):
            f.seek(lba * sector); header = f.read(sector)
            if len(header) != sector or header[:8] != b'EFI PART':
                raise ValueError('Bad GPT header')
            header_size, expected = struct.unpack_from('<II', header, 12)
            if not 92 <= header_size <= sector or struct.unpack_from('<Q', header, 24)[0] != lba:
                raise ValueError('Bad GPT geometry')
            check = bytearray(header[:header_size]); struct.pack_into('<I', check, 16, 0)
            if zlib.crc32(check) != expected:
                raise ValueError('GPT header CRC mismatch')
            table_lba, count, entry_size, entries_crc = struct.unpack_from('<QIII', header, 72)
            if not (1 <= count <= 1024 and 128 <= entry_size <= 4096 and
                    1 < table_lba < disk_sectors - 1 and
                    table_lba * sector + count * entry_size <= size):
                raise ValueError('Bad GPT table geometry')
            f.seek(table_lba * sector); entries = f.read(count * entry_size)
            if len(entries) != count * entry_size or zlib.crc32(entries) != entries_crc:
                raise ValueError('GPT entries CRC mismatch')
            tables.append(entries)
            geometry.append((count, entry_size, header[40:72]))
            hashes[label] = hashlib.sha256(header + entries).hexdigest()
        if tables[0] != tables[1] or geometry[0] != geometry[1]:
            raise ValueError('Primary/backup GPT disagree')
        for offset in range(0, len(tables[0]), entry_size):
            entry = tables[0][offset:offset + entry_size]
            name = entry[56:128].decode('utf-16-le').split('\0')[0]
            if name not in ('super', 'userdata'):
                continue
            if name in targets:
                raise ValueError('Duplicate target partition')
            start, end = struct.unpack_from('<QQ', entry, 32)
            targets[name] = {'start_bytes': start * sector, 'bytes': (end - start + 1) * sector}
        if set(targets) != {'super', 'userdata'}:
            raise ValueError('Missing target partition')
        for name, device, expected_bytes in (('super', 'sda15', ROOT_BYTES), ('userdata', 'sda28', DATA_BYTES)):
            actual_start = int(Path('/sys/class/block', device, 'start').read_text()) * 512
            if targets[name] != {'start_bytes': actual_start, 'bytes': expected_bytes}:
                raise ValueError('GPT extent does not match measured ' + name)
        return hashes


def direct_digest(path):
    h = hashlib.sha256()
    process = subprocess.Popen(['dd', 'if=' + str(path), 'iflag=direct', 'bs=4M', 'status=none'], stdout=subprocess.PIPE)
    try:
        while chunk := process.stdout.read(4 * 1024 * 1024):
            h.update(chunk)
    finally:
        process.stdout.close()
    if process.wait() != 0:
        raise ValueError('Direct disk read failed')
    return h.hexdigest()


def verify_data(manifest):
    data_uuid = str(uuid.UUID(manifest['data_uuid']))
    if run('findmnt', '-n', '-o', 'SOURCE', '--mountpoint', '/srv') != '/dev/sda28':
        raise ValueError('/srv is not the measured data partition')
    if run('findmnt', '-n', '-o', 'UUID', '--mountpoint', '/srv') != data_uuid:
        raise ValueError('/srv filesystem UUID mismatch')
    if direct_digest('/srv/.kebab-storage-check/persistence.bin') != manifest['marker_sha256']:
        raise ValueError('Data persistence check failed')
    if gpt_identity() != manifest['gpt_before']:
        raise ValueError('GPT differs from pre-expansion identity')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-rootfs-uuid', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--erase-userdata', action='store_true', help='Explicit user authorization required')
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    root_uuid = str(uuid.UUID(args.expected_rootfs_uuid))
    if run('id', '-u') != '0' or run('uname', '-r') != '7.2.0-kebab-debian13-r2-charge-policy+':
        raise ValueError('Expected root on verified KB2000 kernel')
    if (run('findmnt', '-n', '-o', 'SOURCE', '/') != '/dev/sda15' or
            run('findmnt', '-n', '-o', 'UUID', '/') != root_uuid or
            run('findmnt', '-n', '-o', 'FSTYPE', '/') != 'ext4'):
        raise ValueError('Measured Debian root mismatch')
    lock = Path('/run/lock/kebab-kernel-update.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if args.verify:
        if args.apply or args.erase_userdata:
            raise ValueError('Verify cannot mutate')
        manifest = json.loads((STATE / 'manifest.json').read_text())
        verify_data(manifest)
        print('DATA_DIRECT_READ_AND_UNCHANGED_GPT_VERIFIED')
        print(run('df', '-hT', '/', '/srv'))
        return
    gpt_before = gpt_identity()
    for label, device, expected in (('super', '/dev/sda15', ROOT_BYTES), ('userdata', '/dev/sda28', DATA_BYTES)):
        path = Path('/dev/disk/by-partlabel', label)
        if path.resolve() != Path(device) or not stat.S_ISBLK(path.stat().st_mode):
            raise ValueError('Measured partition path mismatch')
        if int(run('blockdev', '--getsize64', device)) != expected:
            raise ValueError('Measured partition size mismatch')
    if run('lsblk', '-nr', '-o', 'MOUNTPOINTS', '/dev/sda28') or list(Path('/sys/class/block/sda28/holders').iterdir()):
        raise ValueError('userdata is mounted/in use')
    signatures = run('wipefs', '--no-act', '--noheadings', '/dev/sda28')
    if signatures:
        raise ValueError('Existing filesystem signature; stop rather than reformat')
    if list(Path('/srv').iterdir()) or Path('/srv').is_symlink():
        raise ValueError('/srv is not an empty real directory')
    old_fstab = Path('/etc/fstab').read_bytes()
    if any(line.split() and not line.lstrip().startswith(b'#') and
           len(line.split()) > 1 and line.split()[1] == b'/srv' for line in old_fstab.splitlines()):
        raise ValueError('Existing /srv fstab entry')
    data_rdev = Path('/dev/sda28').stat().st_rdev
    for fd in Path('/proc').glob('[0-9]*/fd/*'):
        try:
            target = fd.stat()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if stat.S_ISBLK(target.st_mode) and target.st_rdev == data_rdev:
            raise ValueError('userdata has an open block-device descriptor')
    info = run('dumpe2fs', '-h', '/dev/sda15')
    block_count = int(re.search(r'^Block count:\s+(\d+)', info, re.M)[1])
    block_size = int(re.search(r'^Block size:\s+(\d+)', info, re.M)[1])
    if block_size != 4096 or block_count not in (1048576, 1835008):
        raise ValueError('Unexpected root ext4 size')
    if run('systemctl', 'is-active', 'kebab-charge-policy.service') != 'active':
        raise ValueError('Charge policy is not active before maintenance')
    if subprocess.run(['pgrep', '-x', 'java'], capture_output=True).returncode == 0:
        raise ValueError('Java workload is active; coordinate storage maintenance')
    print(json.dumps({'mode': 'apply' if args.apply else 'preview', 'root_bytes_before': block_count * block_size,
                      'root_bytes_target': ROOT_BYTES, 'data_partition_bytes': DATA_BYTES,
                      'data_mountpoint': '/srv', 'userdata_erasure_authorized_flag': args.erase_userdata,
                      'gpt_writes': False}), flush=True)
    if not args.apply:
        return
    if not args.erase_userdata:
        raise ValueError('Refusing erase without explicit --erase-userdata')
    STATE.mkdir(mode=0o700, exist_ok=True)
    backup = STATE / ('before-' + time.strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(mode=0o700)
    (backup / 'fstab').write_bytes(old_fstab)
    (backup / 'ext4-header.txt').write_text(info + '\n')
    (backup / 'gpt-hashes.json').write_text(json.dumps(gpt_before) + '\n')
    critical = ['/etc/fstab', '/usr/local/sbin/kebab-charge-policy', '/usr/local/sbin/kebab-health',
                '/etc/systemd/system/kebab-charge-policy.service']
    critical_before = {p: digest_file(p) for p in critical}
    if block_count != 1835008:
        subprocess.run(['resize2fs', '/dev/sda15', '1835008'], check=True)
    new_info = run('dumpe2fs', '-h', '/dev/sda15')
    if int(re.search(r'^Block count:\s+(\d+)', new_info, re.M)[1]) != 1835008:
        raise ValueError('Root enlargement not verified; userdata untouched')
    if {p: digest_file(p) for p in critical} != critical_before or gpt_identity() != gpt_before:
        raise ValueError('Unexpected change after root resize; userdata untouched')
    print('ROOT_EXT4_7GIB_AND_UNCHANGED_CONFIGS_VERIFIED', flush=True)
    # No -F: preserve mke2fs mounted-device/signature safety checks.
    subprocess.run(['mkfs.ext4', '-L', 'kebab-data', '-m', '0', '-E',
                    'nodiscard,lazy_itable_init=0,lazy_journal_init=0', '/dev/sda28'], check=True)
    data_uuid = str(uuid.UUID(run('blkid', '-p', '-s', 'UUID', '-o', 'value', '/dev/sda28')))
    subprocess.run(['udevadm', 'trigger', '--action=change', '--subsystem-match=block', '--sysname-match=sda28'], check=True)
    subprocess.run(['udevadm', 'settle', '--timeout=10'], check=True)
    subprocess.run(['mount', '-t', 'ext4', '-o', 'noatime', '/dev/sda28', '/srv'], check=True)
    if Path('/etc/fstab').read_bytes() != old_fstab:
        raise ValueError('fstab changed during preparation; data mounted, persistence not changed')
    line = f'UUID={data_uuid} /srv ext4 defaults,noatime,nofail,x-systemd.device-timeout=10s 0 2\n'.encode()
    temp = Path('/etc/fstab.kebab-storage-new')
    with temp.open('xb') as f:
        os.fchmod(f.fileno(), stat.S_IMODE(Path('/etc/fstab').stat().st_mode))
        f.write(old_fstab.rstrip(b'\n') + b'\n' + line); f.flush(); os.fsync(f.fileno())
    try:
        subprocess.run(['findmnt', '--verify', '--tab-file', str(temp)], check=True)
        os.replace(temp, '/etc/fstab')
        directory_fd = os.open('/etc', os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
    finally:
        temp.unlink(missing_ok=True)
    marker_dir = Path('/srv/.kebab-storage-check')
    marker_dir.mkdir(mode=0o700)
    marker = marker_dir / 'persistence.bin'
    with marker.open('xb') as f:
        os.fchmod(f.fileno(), 0o600)
        for _ in range(32):
            f.write(os.urandom(1024 * 1024))
        f.flush(); os.fsync(f.fileno())
    manifest = {'root_uuid': root_uuid, 'root_bytes': ROOT_BYTES, 'data_uuid': data_uuid,
                'data_partition_bytes': DATA_BYTES, 'gpt_before': gpt_before,
                'marker_bytes': marker.stat().st_size, 'marker_sha256': digest_file(marker),
                'root_config_hashes_before': critical_before, 'fstab_backup': str(backup / 'fstab')}
    verify_data(manifest)
    (STATE / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('ROOT_AND_DATA_EXPANSION_VERIFIED; REBOOT_VALIDATION_PENDING', flush=True)
    print(run('df', '-hT', '/', '/srv'))


if __name__ == '__main__':
    main()
