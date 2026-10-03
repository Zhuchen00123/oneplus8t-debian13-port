#!/usr/bin/env python3
"""Compare actual rootfs/initramfs module bytes with the fixed kernel group."""
import argparse
import datetime
import gzip
import hashlib
import json
import lzma
from pathlib import Path, PurePosixPath
import subprocess
import tarfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def module_path(name, release):
    parts = PurePosixPath(name).parts
    if '..' in parts:
        raise ValueError('Invalid module archive path')
    try:
        index = parts.index('modules')
    except ValueError:
        return None
    if len(parts) <= index + 2 or parts[index+1] != release:
        raise ValueError('Module belongs to a different kernelrelease')
    if not name.endswith(('.ko', '.ko.zst', '.ko.xz', '.ko.gz')):
        return None
    return '/'.join(parts[index+2:])


def uncompressed(name, data):
    if name.endswith('.zst'):
        return subprocess.check_output(['zstd', '-d', '-q', '-c'], input=data)
    if name.endswith('.xz'):
        return lzma.decompress(data)
    if name.endswith('.gz'):
        return gzip.decompress(data)
    return data


def audit_rootfs_modules(rootfs_tar, release, reference):
    seen = set()
    with tarfile.open(rootfs_tar, 'r|gz') as archive:
        for member in archive:
            if not member.isfile() or not member.name.endswith(('.ko', '.ko.zst', '.ko.xz', '.ko.gz')):
                continue
            path = module_path(member.name, release)
            if path not in reference or path in seen:
                raise ValueError('Unexpected/duplicate rootfs module: '+member.name)
            if digest(archive.extractfile(member).read()) != reference[path]:
                raise ValueError('Rootfs module binary differs: '+path)
            seen.add(path)
    if seen != set(reference):
        raise ValueError('Rootfs is missing reference modules')
    return len(seen)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel-artifacts', required=True, type=Path)
    p.add_argument('--rootfs-tar', required=True, type=Path)
    p.add_argument('--server-initramfs', required=True, type=Path)
    p.add_argument('--rescue-initramfs', required=True, type=Path)
    p.add_argument('--work', required=True, type=Path)
    p.add_argument('--report', required=True, type=Path)
    a = p.parse_args()
    kernel, work = a.kernel_artifacts.resolve(), a.work.resolve()
    if work.exists() or a.report.exists() or not work.as_posix().startswith('/home/'):
        raise ValueError('Use a fresh Linux work directory/report')
    release = (kernel/'kernel.release').read_text().strip()
    expected_count = json.loads((kernel/'module-verification.json').read_text())['modules_checked']
    km = json.loads((kernel/'kernel-manifest.json').read_text())
    with (kernel/'modules.tar.gz').open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != km['files_sha256']['modules.tar.gz']:
            raise ValueError('Reference module archive checksum mismatch')
    reference, raw_reference = {}, {}
    with tarfile.open(kernel/'modules.tar.gz', 'r|gz') as archive:
        for member in archive:
            if not member.isfile():
                continue
            path = module_path(member.name, release)
            if path is None:
                continue
            data = archive.extractfile(member).read()
            if path in reference:
                raise ValueError('Duplicate reference module')
            reference[path] = digest(data)
            raw_path = path[:path.index('.ko')+3]
            raw_reference[raw_path] = digest(uncompressed(path, data))
    if len(reference) != expected_count:
        raise ValueError('Incomplete reference module archive')
    rootfs_count = audit_rootfs_modules(a.rootfs_tar, release, reference)
    work.mkdir(parents=True)
    initramfs_results = {}
    for label, image in [('server', a.server_initramfs), ('rescue', a.rescue_initramfs)]:
        directory = work/label
        subprocess.run(['unmkinitramfs', str(image), str(directory)], check=True)
        checked = {}
        for module in directory.rglob('*.ko*'):
            if module.is_symlink() or not module.is_file():
                raise ValueError('Unexpected module symlink/special file')
            path = module_path(module.relative_to(directory).as_posix(), release)
            if path is None:
                continue
            path = path[:path.index('.ko')+3]
            if path not in raw_reference or path in checked:
                raise ValueError('Unexpected/duplicate initramfs module')
            if digest(uncompressed(module.name, module.read_bytes())) != raw_reference[path]:
                raise ValueError('Initramfs module binary differs: '+path)
            checked[path] = raw_reference[path]
        required = {'kernel/drivers/usb/gadget/libcomposite.ko',
                    'kernel/drivers/usb/gadget/function/usb_f_rndis.ko',
                    'kernel/drivers/usb/gadget/function/usb_f_acm.ko',
                    'kernel/drivers/usb/gadget/function/u_ether.ko',
                    'kernel/drivers/usb/gadget/function/u_serial.ko',
                    'kernel/drivers/phy/qualcomm/phy-qcom-qusb2.ko'}
        if not required.issubset(checked):
            raise ValueError('Missing USB dependency in '+label+' initramfs')
        initramfs_results[label] = {'modules_checked': len(checked),
                                   'raw_elf_bytes_match_reference': True,
                                   'required_usb_dependency_payloads_match': True}
    result = {'created_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'kernelrelease': release, 'rootfs_module_files_checked': rootfs_count,
              'rootfs_module_bytes_exactly_match_reference': True,
              'initramfs': initramfs_results, 'hardware_module_loading_tested': False}
    a.report.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
