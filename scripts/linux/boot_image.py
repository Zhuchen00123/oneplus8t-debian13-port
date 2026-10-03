#!/usr/bin/env python3
"""Strict, host-only Android v2 boot inspection and candidate verification."""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def inspect(path):
    data = Path(path).read_bytes()
    if len(data) < 1660 or data[:8] != b'ANDROID!':
        raise ValueError('Not an Android boot image')
    u32 = lambda offset: struct.unpack_from('<I', data, offset)[0]
    u64 = lambda offset: struct.unpack_from('<Q', data, offset)[0]
    page, version = u32(36), u32(40)
    if version != 2 or page not in (2048, 4096, 8192, 16384) or u32(1644) != 1660:
        raise ValueError('Only a valid Android v2 header is supported')
    align = lambda size: (size + page - 1) // page * page
    sizes = dict(kernel=u32(8), ramdisk=u32(16), second=u32(24), recovery_dtbo=u32(1632), dtb=u32(1648))
    segments, offset = {}, page
    for name, size in sizes.items():
        if name == 'recovery_dtbo' and size and u64(1636) != offset:
            raise ValueError('Unexpected recovery DTBO position')
        if offset + size > len(data):
            raise ValueError('Truncated ' + name)
        segments[name] = {'offset': offset, 'bytes': size, 'sha256': sha256(data[offset:offset+size])}
        offset += align(size)
    if not sizes['kernel'] or not sizes['ramdisk'] or not sizes['dtb']:
        raise ValueError('Kernel, initramfs and DTB must all be present')
    cmdline = data[64:576].split(b'\0', 1)[0] + data[608:1632].split(b'\0', 1)[0]
    return {
        'bytes': len(data), 'sha256': sha256(data), 'header_version': version, 'page_size': page,
        'header_size': u32(1644), 'kernel_address': u32(12), 'ramdisk_address': u32(20),
        'second_address': u32(28), 'tags_address': u32(32), 'dtb_address': u64(1652),
        'os_version_encoded': u32(44), 'cmdline_sha256': sha256(cmdline),
        'segments': segments,
    }


def verify_candidate(candidate, stock, kernel, dtb, initramfs):
    result, original = inspect(candidate), inspect(stock)
    for field in ('header_version', 'page_size', 'header_size', 'kernel_address',
                  'ramdisk_address', 'second_address', 'tags_address', 'dtb_address',
                  'os_version_encoded'):
        if result[field] != original[field]:
            raise ValueError('Candidate differs from stock layout: ' + field)
    for name, file in (('kernel', kernel), ('dtb', dtb), ('ramdisk', initramfs)):
        if result['segments'][name]['sha256'] != sha256(Path(file).read_bytes()):
            raise ValueError('Candidate payload mismatch: ' + name)
    if result['segments']['second']['bytes'] or result['segments']['recovery_dtbo']['bytes']:
        raise ValueError('Unexpected second-stage/recovery DTBO payload')
    if result['bytes'] > original['bytes']:
        raise ValueError('Candidate exceeds the measured boot partition image size')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--expected-sha256')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = inspect(args.image)
    if args.expected_sha256 and result['sha256'] != args.expected_sha256:
        raise ValueError('Boot input SHA-256 mismatch')
    text = json.dumps(result, indent=2)+'\n'
    if args.output:
        with args.output.open('x') as stream:
            stream.write(text)
    print(text, end='')


if __name__ == '__main__':
    main()
