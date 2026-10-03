#!/usr/bin/env python3
"""Prepare a local stock restore group and test sparse conversion; no device I/O."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def sparse_layout(path, expected_bytes):
    """Check Android sparse v1 framing without trusting the conversion tool."""
    with path.open('rb') as stream:
        def read(size):
            data = stream.read(size)
            if len(data) != size:
                raise ValueError('Truncated Android sparse image')
            return data
        header = struct.unpack('<I4H4I', read(28))
        magic, major, minor, fh, ch, block, blocks, chunks, checksum = header
        if magic != 0xed26ff3a or major != 1 or minor != 0 or fh < 28 or ch < 12:
            raise ValueError('Unsupported sparse header')
        if block == 0 or block % 4 or block * blocks != expected_bytes:
            raise ValueError('Sparse expanded size differs from the original partition')
        read(fh - 28)
        total, kinds = 0, {}
        for _ in range(chunks):
            kind, reserved, count, size = struct.unpack('<2H2I', read(12))
            read(ch - 12)
            payload = size - ch
            needed = {0xcac1: count * block, 0xcac2: 4, 0xcac3: 0, 0xcac4: 4}.get(kind)
            if needed is None or payload != needed or (kind == 0xcac4 and count):
                raise ValueError('Invalid sparse chunk layout')
            if kind != 0xcac4:
                total += count
            if total > blocks or stream.tell() + payload > path.stat().st_size:
                raise ValueError('Sparse chunk exceeds declared image')
            stream.seek(payload, 1)
            kinds[hex(kind)] = kinds.get(hex(kind), 0) + 1
        if total != blocks or stream.tell() != path.stat().st_size:
            raise ValueError('Sparse image is incomplete or has trailing data')
    return {'major_version': major, 'block_bytes': block, 'expanded_bytes': block * blocks,
            'chunks': chunks, 'chunk_types': kinds,
            'framing_and_expanded_size_verified': True,
            'note': 'Chunk framing is checked separately; decoded content is SHA-256 compared to original raw.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--partition-backup', required=True, type=Path)
    parser.add_argument('--work', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    source, work, out = args.partition_backup.resolve(), args.work.resolve(), args.output.resolve()
    if work.exists() or out.exists():
        raise ValueError('Use fresh work/output directories; preserve previous recovery groups')
    if not work.as_posix().startswith('/home/'):
        raise ValueError('Keep roundtrip files on the Linux build filesystem')
    manifest = json.loads((source/'manifest.json').read_text(encoding='utf-8-sig'))
    inputs = {}
    for name in ('boot_a', 'dtbo_a', 'recovery_a', 'super'):
        entries = [p for p in manifest['Partitions'] if p['Name'] == name]
        if len(entries) != 1 or entries[0]['Verified'] is not True:
            raise ValueError('Missing verified stock restore input: '+name)
        part, image = entries[0], source/(name+'.img')
        if image.is_symlink() or not image.is_file() or image.stat().st_size != part['Bytes'] or sha(image) != part['Sha256']:
            raise ValueError('Stock restore checksum/size mismatch: '+name)
        inputs[name] = {'file': image.name, 'bytes': part['Bytes'], 'sha256': part['Sha256']}
    # Validate all sources before creating a destination.
    work.mkdir(parents=True)
    out.mkdir(parents=True)
    sparse, decoded = work/'super-stock.sparse.img', work/'super-roundtrip.img'
    subprocess.run(['img2simg', str(source/'super.img'), str(sparse)], check=True)
    layout = sparse_layout(sparse, inputs['super']['bytes'])
    subprocess.run(['simg2img', str(sparse), str(decoded)], check=True)
    if decoded.stat().st_size != inputs['super']['bytes'] or sha(decoded) != inputs['super']['sha256']:
        raise ValueError('Stock super sparse roundtrip mismatch')
    for name in ('boot_a', 'dtbo_a', 'recovery_a'):
        shutil.copyfile(source/(name+'.img'), out/(name+'.img'))
        if sha(out/(name+'.img')) != inputs[name]['sha256']:
            raise ValueError('Stock restore copy mismatch')
    shutil.copyfile(sparse, out/sparse.name)
    if sha(out/sparse.name) != sha(sparse):
        raise ValueError('Sparse export copy mismatch')
    # Recheck originals after the operation; no source is modified.
    for name, info in inputs.items():
        if sha(source/info['file']) != info['sha256']:
            raise ValueError('Original backup changed during recovery preparation')
    files = {p.name: {'bytes': p.stat().st_size, 'sha256': sha(p)}
             for p in sorted(out.iterdir()) if p.is_file()}
    result = {'created_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'scope': 'Stock slot-a boot/dtbo/recovery copies and sparse whole-super restore input; no NV/userdata/GPT',
              'original_inputs': inputs, 'files': files, 'sparse_layout': layout,
              'decoded_sha256_matches_original_super': True,
              'original_inputs_rehashed_before_and_after': True,
              'device_restore_tested': False, 'phone_commands_executed': False,
              'phone_write_authorized': False,
              'limitations': ['Offline format/copy verification only',
                              'Fastboot mode and hardware restoration not tested',
                              'Same-drive copy; not independent media or a full unbrick guarantee']}
    (out/'recovery-manifest.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({'output': str(out), 'sparse_bytes': sparse.stat().st_size,
                      'expanded_bytes': layout['expanded_bytes'], 'roundtrip_verified': True,
                      'hardware_restore_tested': False}, indent=2))


if __name__ == '__main__':
    main()
