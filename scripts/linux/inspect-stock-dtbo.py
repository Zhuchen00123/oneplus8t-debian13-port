#!/usr/bin/env python3
"""Offline probe of stock Android DTBO entries against a mainline DTB."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dtbo', type=Path, required=True)
    parser.add_argument('--dtb', type=Path, required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    data = args.dtbo.read_bytes()
    if hashlib.sha256(data).hexdigest() != args.expected_sha256:
        raise ValueError('Stock DTBO input checksum mismatch')
    if len(data) < 32:
        raise ValueError('Truncated DTBO table')
    magic, total, header, entry_size, count, entries_offset, pagesize, version = struct.unpack_from('>8I', data)
    if magic != 0xd7b7ab1e or version != 0 or header != 32 or entry_size != 32 or not count:
        raise ValueError('Unsupported or empty DTBO table')
    if total > len(data) or entries_offset < header or entries_offset+count*entry_size > total:
        raise ValueError('DTBO table outside input bounds')
    if args.output_dir.exists():
        raise ValueError('Existing probe results are preserved')
    args.output_dir.mkdir(parents=True)
    entries = []
    for index in range(count):
        size, offset, board_id, revision, *custom = struct.unpack_from('>8I', data, entries_offset+index*entry_size)
        if offset < entries_offset+count*entry_size or offset+size > total or data[offset:offset+4] != b'\xd0\x0d\xfe\xed':
            raise ValueError('Invalid DTBO entry bounds/magic')
        overlay = args.output_dir/f'entry-{index}.dtbo'
        overlay.write_bytes(data[offset:offset+size])
        result = subprocess.run(['fdtoverlay', '-i', str(args.dtb), '-o', str(args.output_dir/f'applied-{index}.dtb'), str(overlay)],
                                capture_output=True, text=True)
        entries.append({'index': index, 'id': board_id, 'revision': revision, 'custom': custom,
                        'bytes': size, 'single_apply_exit': result.returncode, 'stderr': result.stderr.strip()})
    passed = sum(e['single_apply_exit'] == 0 for e in entries)
    report = {'dtbo_sha256': args.expected_sha256, 'mainline_dtb_sha256': hashlib.sha256(args.dtb.read_bytes()).hexdigest(),
              'dtbo_entries': count, 'single_overlay_apply_passed': passed, 'entries': entries,
              'selection_by_abl_verified': False, 'combined_overlay_order_verified': False,
              'bootloader_failure_policy_verified': False, 'hardware_boot_verified': False,
              'limitation': 'Individual libfdt application is an offline compatibility probe, not an ABL boot test'}
    (args.output_dir/'dtbo-probe.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'entries'}, indent=2))


if __name__ == '__main__':
    main()
