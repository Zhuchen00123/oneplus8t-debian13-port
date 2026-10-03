#!/usr/bin/env python3
"""Copy verified local OEM firmware to the pinned 7.2 DTS paths, on the host."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import tarfile


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def split_members(metadata, prefix, read):
    """Validate the split ELF32 layout accepted by qcom/mdt_loader.c."""
    if len(metadata) < 52 or metadata[:7] != b'\x7fELF\x01\x01\x01':
        raise ValueError('Expected little-endian ELF32 MDT')
    header = struct.unpack_from('<16sHHIIIIIHHHHHH', metadata)
    offset, entsize, count = header[5], header[9], header[10]
    if entsize != 32 or not 2 <= count <= 100 or offset + count * 32 > len(metadata):
        raise ValueError('Invalid MDT program header table')
    ph = [struct.unpack_from('<IIIIIIII', metadata, offset + i*32) for i in range(count)]
    hashes = [i for i, p in enumerate(ph) if p[6] & 0x07000000 == 0x02000000]
    if ph[0][0] != 0 or len(hashes) != 1:
        raise ValueError('Missing/ambiguous OEM hash metadata')
    hi = hashes[0]
    if ph[0][4] + ph[hi][4] != len(metadata):
        raise ValueError('Expected packed ELF-header/hash split MDT')
    result = {}
    for i, p in enumerate(ph):
        # qcom_mdt_load_no_init skips BSS-only and hash segments. Validate
        # all available non-empty program segments, including the signature.
        if not p[4]:
            continue
        name = prefix + f'.b{i:02d}'
        data = read(name)
        if len(data) != p[4]:
            raise ValueError('Missing/truncated firmware segment: ' + name)
        if i == 0 and data != metadata[:p[4]]:
            raise ValueError('ELF header differs from original b00')
        if i == hi and data != metadata[ph[0][4]:]:
            raise ValueError('OEM signature metadata differs from original segment')
        if p[0] == 1 and p[4] > p[5]:
            raise ValueError('Loadable segment exceeds memory size')
        result[name] = data
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--rootfs', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    info = json.loads(args.manifest.read_text(encoding='utf-8-sig'))
    if not info['verified'] or args.archive.stat().st_size != info['bytes'] or sha(args.archive) != info['sha256']:
        raise ValueError('Original firmware archive checksum/size mismatch')
    root = args.rootfs.resolve()
    fw = root/'usr/lib/firmware'
    if not (root/'etc/debian_version').is_file() or fw.is_symlink():
        raise ValueError('Expected a staged Debian rootfs with ordinary firmware directory')
    if args.report.exists():
        raise ValueError('Existing firmware reports are preserved')
    selected = {}
    with tarfile.open(args.archive) as archive:
        def read(name):
            member = archive.getmember(name)
            if not member.isfile() or member.size > 64*1024*1024:
                raise ValueError('Firmware input must be a bounded regular file: ' + name)
            return archive.extractfile(member).read()
        for component in ('adsp', 'cdsp', 'slpi'):
            prefix = 'vendor/firmware_mnt/image/' + component
            metadata = read(prefix + '.mdt')
            # Renaming only the metadata is intentional: the kernel accepts
            # split bins for either .mdt or .mbn and derives .bNN filenames.
            selected[f'postmarketos/{component}.mbn'] = (prefix+'.mdt', metadata)
            for name, data in split_members(metadata, prefix, read).items():
                selected['postmarketos/' + Path(name).name] = (name, data)
        prefix = 'vendor/firmware/a650_zap'
        metadata = read(prefix+'.mdt')
        selected['qcom/a650_zap.mdt'] = (prefix+'.mdt', metadata)
        for name, data in split_members(metadata, prefix, read).items():
            selected['qcom/'+Path(name).name] = (name, data)
        # Match the reference driver's optional combined-image fallback too.
        selected['qcom/a650_zap.elf'] = (prefix+'.elf', read(prefix+'.elf'))
    for name, (_, data) in selected.items():
        path = fw/name
        if not path.resolve().is_relative_to(root) or path.exists():
            raise ValueError('Preserve existing firmware or reject escaping path: ' + name)
    files = []
    for name, (source, data) in sorted(selected.items()):
        path = fw/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o644)
        files.append({'source': source, 'destination': name, 'bytes': len(data), 'sha256': sha(path)})
    report = {'archive_sha256': info['sha256'], 'model': info['model'], 'android_build': info['build'],
              'files': files, 'split_elf_segments_and_metadata_verified': True,
              'oem_signature_cryptographically_verified': False, 'hardware_loading_verified': False,
              'venus_installed': False, 'sdx55_or_nv_installed': False,
              'note': 'Original signed bytes preserved; metadata-only .mbn plus .bNN uses the kernel split loader.'}
    args.report.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'oem_files_staged': len(files), 'split_layout_verified': True, 'hardware_loading_verified': False}))


if __name__ == '__main__':
    main()
