#!/usr/bin/env python3
"""Preview by default. Mark only GPT boot_a successful after a verified Linux boot."""
import argparse
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import time
import zlib

SUCCESS = 1 << 54


@dataclass
class Copy:
    header_lba: int
    header: bytes
    entries_lba: int
    entries: bytes
    count: int
    size: int


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_copy(stream, sector, lba, disk_sectors):
    stream.seek(lba * sector)
    header = stream.read(sector)
    if len(header) != sector or header[:8] != b'EFI PART':
        raise ValueError('GPT signature/length mismatch')
    size, expected = struct.unpack_from('<II', header, 12)
    if not 92 <= size <= sector:
        raise ValueError('Invalid GPT header size')
    check = bytearray(header[:size]); struct.pack_into('<I', check, 16, 0)
    if zlib.crc32(check) != expected or struct.unpack_from('<Q', header, 24)[0] != lba:
        raise ValueError('GPT header CRC/location mismatch')
    entries_lba, count, entry_size, expected = struct.unpack_from('<QIII', header, 72)
    if not (1 <= count <= 1024 and 128 <= entry_size <= 4096 and entry_size % 8 == 0):
        raise ValueError('Invalid GPT entry geometry')
    length = ((count * entry_size + sector - 1) // sector) * sector
    if entries_lba <= 1 or entries_lba + length // sector >= disk_sectors:
        raise ValueError('GPT entries outside disk')
    stream.seek(entries_lba * sector)
    entries = stream.read(length)
    if len(entries) != length or zlib.crc32(entries[:count * entry_size]) != expected:
        raise ValueError('GPT entry CRC/length mismatch')
    return Copy(lba, header, entries_lba, entries, count, entry_size)


def prepare(primary, backup, sector, disk_sectors):
    if (primary.count, primary.size, primary.entries) != (backup.count, backup.size, backup.entries):
        raise ValueError('Primary/backup GPT entries disagree')
    if primary.header_lba != 1 or backup.header_lba != disk_sectors - 1:
        raise ValueError('Unexpected GPT header locations')
    if struct.unpack_from('<Q', primary.header, 32)[0] != backup.header_lba or struct.unpack_from('<Q', backup.header, 32)[0] != 1:
        raise ValueError('Alternate GPT pointers disagree')
    first, last = struct.unpack_from('<QQ', primary.header, 40)
    if backup.header[40:72] != primary.header[40:72]:
        raise ValueError('Usable geometry/disk identity disagree')
    if not (primary.entries_lba + len(primary.entries) // sector <= first and
            first <= last < disk_sectors - 1 and
            last < backup.entries_lba and
            backup.entries_lba + len(backup.entries) // sector <= backup.header_lba):
        raise ValueError('GPT metadata overlaps usable partition area')
    slots = {}
    for i in range(primary.count):
        offset = i * primary.size
        entry = primary.entries[offset:offset + primary.size]
        name = entry[56:128].decode('utf-16-le').split('\0')[0]
        if name in ('boot_a', 'boot_b'):
            if name in slots:
                raise ValueError('Duplicate boot slot')
            start, end, flags = struct.unpack_from('<QQQ', entry, 32)
            if entry[:16] == bytes(16) or not first <= start <= end <= last:
                raise ValueError('Invalid boot partition type/extent')
            slots[name] = (offset, start, end, flags)
    if set(slots) != {'boot_a', 'boot_b'}:
        raise ValueError('Missing boot slot')
    offset, start, end, flags = slots['boot_a']
    if not (flags & (1 << 50)) or flags & (1 << 55) or slots['boot_b'][3] & (1 << 50):
        raise ValueError('A is not uniquely active and bootable')
    if (end - start + 1) * sector != 100663296:
        raise ValueError('Measured boot_a size mismatch')
    _, b_start, b_end, _ = slots['boot_b']
    if not (end < b_start or b_end < start):
        raise ValueError('Boot partitions overlap')
    entries = bytearray(primary.entries)
    struct.pack_into('<Q', entries, offset + 48, flags | SUCCESS)
    changed = {i for i, (a, b) in enumerate(zip(primary.entries, entries)) if a != b}
    if changed not in (set(), {offset + 54}):
        raise ValueError('Changes exceed the single success bit')
    output = []
    for item in (primary, backup):
        header = bytearray(item.header)
        struct.pack_into('<I', header, 88, zlib.crc32(entries[:item.count * item.size]))
        struct.pack_into('<I', header, 16, 0)
        size = struct.unpack_from('<I', header, 12)[0]
        struct.pack_into('<I', header, 16, zlib.crc32(header[:size]))
        if any(a != b and not (16 <= i < 20 or 88 <= i < 92)
               for i, (a, b) in enumerate(zip(item.header, header))):
            raise ValueError('Unexpected GPT header change')
        output.append(Copy(item.header_lba, bytes(header), item.entries_lba,
                           bytes(entries), item.count, item.size))
    return output, {'already_successful': bool(flags & SUCCESS),
                    'active_slot': 'a', 'tries_remaining_unchanged': (flags >> 51) & 7,
                    'boot_a_start_lba': start, 'partition_layout_unchanged': True,
                    'boot_a_end_lba': end,
                    'boot_b_unchanged': True, 'changed_entry_bytes_per_copy': len(changed)}


def verify_image(boot, length, expected):
    h = hashlib.sha256()
    with boot.open('rb') as f:
        remaining = length
        while remaining:
            data = f.read(min(remaining, 1024 * 1024))
            if not data:
                raise ValueError('Short boot read')
            h.update(data); remaining -= len(data)
    if h.hexdigest() != expected:
        raise ValueError('Running boot input prefix does not match reviewed image')


def write_copy(stream, item, sector):
    # Backup first keeps the original primary valid until a new backup is verified.
    for lba, data in ((item.entries_lba, item.entries), (item.header_lba, item.header)):
        stream.seek(lba * sector)
        if stream.write(data) != len(data):
            raise OSError('Short GPT write')
        stream.flush(); os.fsync(stream.fileno())
    stream.seek(item.header_lba * sector)
    if stream.read(sector) != item.header:
        raise OSError('GPT header write/readback mismatch')
    stream.seek(item.entries_lba * sector)
    if stream.read(len(item.entries)) != item.entries:
        raise OSError('GPT entries write/readback mismatch')


def save_backup(primary, backup, sector):
    directory = Path('/var/backups/kebab-boot-success') / time.strftime('%Y%m%d-%H%M%S')
    directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    manifest = {}
    for label, item in (('primary', primary), ('backup', backup)):
        for kind, lba, data in [('header', item.header_lba, item.header), ('entries', item.entries_lba, item.entries)]:
            name = label + '-' + kind + '.bin'
            path = directory / name
            with path.open('xb') as dest:
                os.fchmod(dest.fileno(), 0o600)
                dest.write(data); dest.flush(); os.fsync(dest.fileno())
            if sha(path.read_bytes()) != sha(data):
                raise ValueError('Private GPT backup hash mismatch')
            manifest[name] = {'offset': lba * sector, 'bytes': len(data), 'sha256': sha(data)}
    manifest_path = directory / 'manifest.json'
    with manifest_path.open('x') as dest:
        os.fchmod(dest.fileno(), 0o600)
        dest.write(json.dumps(manifest, indent=2) + '\n')
        dest.flush(); os.fsync(dest.fileno())
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return directory


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--expected-boot-sha256', required=True)
    p.add_argument('--expected-boot-bytes', type=int, required=True)
    p.add_argument('--expected-rootfs-uuid', required=True)
    p.add_argument('--apply', action='store_true', help='Explicit approval required; default is read-only preview')
    p.add_argument('--backup-preview', action='store_true', help='Save private read-only GPT snapshot during preview; no disk writes')
    a = p.parse_args()
    check = lambda *x: subprocess.check_output(x, text=True).strip()
    if check('id', '-u') != '0' or check('uname', '-r') != '7.2.0-kebab-debian13-r2-charge-policy+':
        raise ValueError('Expected root on verified charge-policy kernel')
    if check('findmnt', '-n', '-o', 'SOURCE', '/') != '/dev/sda15' or check('findmnt', '-n', '-o', 'UUID', '/') != a.expected_rootfs_uuid:
        raise ValueError('Measured root partition/UUID mismatch')
    boot = Path('/dev/disk/by-partlabel/boot_a')
    if boot.resolve() != Path('/dev/sde11') or not 0 < a.expected_boot_bytes <= 100663296:
        raise ValueError('Boot path/length mismatch')
    lock = Path('/run/lock/kebab-kernel-update.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    verify_image(boot, a.expected_boot_bytes, a.expected_boot_sha256)
    with Path('/dev/sde').open('r+b' if a.apply else 'rb', buffering=0) as f:
        sector = struct.unpack('I', fcntl.ioctl(f.fileno(), 0x1268, struct.pack('I', 0)))[0]
        sectors = struct.unpack('Q', fcntl.ioctl(f.fileno(), 0x80081272, bytes(8)))[0] // sector
        primary = read_copy(f, sector, 1, sectors)
        backup = read_copy(f, sector, sectors - 1, sectors)
        updated, plan = prepare(primary, backup, sector, sectors)
        expected_start = int(Path('/sys/class/block/sde11/start').read_text()) * 512
        if plan['boot_a_start_lba'] * sector != expected_start:
            raise ValueError('GPT boot_a extent mismatches kernel device map')
        if (plan['boot_a_end_lba'] - plan['boot_a_start_lba'] + 1) * sector != int(Path('/sys/class/block/sde11/size').read_text()) * 512:
            raise ValueError('GPT boot_a size mismatches kernel device map')
        print(json.dumps({'mode': 'apply' if a.apply else 'preview', **plan}), flush=True)
        if not a.apply:
            if a.backup_preview:
                print('PRIVATE_PREVIEW_BACKUP=' + str(save_backup(primary, backup, sector)))
            return
        if plan['already_successful']:
            return
        directory = save_backup(primary, backup, sector)
        # Re-read both originals immediately before the first write.
        if read_copy(f, sector, 1, sectors) != primary or read_copy(f, sector, sectors - 1, sectors) != backup:
            raise ValueError('GPT changed after preparation; no write performed')
        try:
            write_copy(f, updated[1], sector)
            if read_copy(f, sector, sectors - 1, sectors) != updated[1]:
                raise ValueError('Updated backup GPT invalid')
            write_copy(f, updated[0], sector)
            current = [read_copy(f, sector, 1, sectors), read_copy(f, sector, sectors - 1, sectors)]
            _, verified = prepare(*current, sector, sectors)
            if not verified['already_successful']:
                raise ValueError('Success bit not verified')
        except Exception as exc:
            try:
                write_copy(f, primary, sector)
                write_copy(f, backup, sector)
                if read_copy(f, sector, 1, sectors) != primary or read_copy(f, sector, sectors - 1, sectors) != backup:
                    raise ValueError('Original GPT restoration mismatch')
            except Exception as restore_exc:
                raise RuntimeError('GPT restoration unverified; DO NOT REBOOT; backup=' + str(directory)) from restore_exc
            raise RuntimeError('Update failed; original GPT restored') from exc
        print('BOOT_A_SUCCESS_BIT_AND_BOTH_GPT_CRCS_VERIFIED')
        print('PRIVATE_BACKUP=' + str(directory))


if __name__ == '__main__':
    main()
