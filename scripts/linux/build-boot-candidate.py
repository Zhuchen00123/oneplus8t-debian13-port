#!/usr/bin/env python3
"""Package/verify a RAM rescue boot.img. This tool has no device commands."""
import argparse
import datetime
import gzip
import json
from pathlib import Path
import re
import shutil
import subprocess

from boot_image import inspect, sha256, verify_candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, required=True)
    parser.add_argument('--kernel-artifacts', type=Path, required=True)
    parser.add_argument('--initramfs', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[2]
    profile_path = project/'config/boot-kb2000.json'
    profile = json.loads(profile_path.read_text())
    artifacts = args.kernel_artifacts.resolve()
    stock = inspect(args.stock)
    if stock['sha256'] != profile['stock_boot_sha256'] or stock['bytes'] != profile['boot_partition_bytes']:
        raise ValueError('Stock image is not the measured KB2000 boot input')
    expected_fields = {'header_version': profile['header_version'], 'page_size': profile['pagesize']}
    for field, offset in (('kernel_address', 'kernel_offset'), ('ramdisk_address', 'ramdisk_offset'),
                          ('second_address', 'second_offset'), ('tags_address', 'tags_offset'), ('dtb_address', 'dtb_offset')):
        expected_fields[field] = int(profile['base'], 16)+int(profile[offset], 16)
    for field, expected in expected_fields.items():
        if stock[field] != expected:
            raise ValueError('Stock/profile mismatch: ' + field)
    inputs = json.loads((artifacts/'kernel-manifest.json').read_text())
    for name, expected in inputs['files_sha256'].items():
        if Path(name).name != name or sha256((artifacts/name).read_bytes()) != expected:
            raise ValueError('Kernel input mismatch: ' + name)
    kernel, dtb = artifacts/'Image.gz', artifacts/'sm8250-oneplus-kebab.dtb'
    image = gzip.decompress(kernel.read_bytes())
    if sha256(image) != inputs['files_sha256']['Image'] or image[56:60] != b'ARMd':
        raise ValueError('Compressed Image does not match verified ARM64 Image')
    init_report_path = args.initramfs.with_suffix('.manifest.json')
    init_report = json.loads(init_report_path.read_text())
    release = (artifacts/'kernel.release').read_text().strip()
    if init_report['kernelrelease'] != release or init_report['initramfs_sha256'] != sha256(args.initramfs.read_bytes()):
        raise ValueError('Initramfs version/checksum mismatch')
    if init_report['storage_mounts'] or init_report['storage_writes'] or init_report['private_ssh_keys_embedded']:
        raise ValueError('RAM rescue scope violated')
    for name, expected in init_report['scripts_sha256'].items():
        if Path(name).name != name or sha256((Path(__file__).parent/name).read_bytes()) != expected:
            raise ValueError('Runtime script changed after initramfs build')
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    candidate = output/'boot-rescue.img'
    if candidate.exists() or (output/'boot-manifest.json').exists() or (output/'kernel').exists():
        raise ValueError('Existing boot candidates are preserved; choose a fresh output directory')
    command = ['mkbootimg', '--kernel', str(kernel), '--ramdisk', str(args.initramfs), '--dtb', str(dtb),
               '--header_version', str(profile['header_version']), '--pagesize', str(profile['pagesize']),
               '--base', profile['base'], '--os_version', profile['legacy_header_os_version'],
               '--os_patch_level', profile['legacy_header_os_patch_level'], '--cmdline', profile['rescue_cmdline']]
    for name in ('kernel_offset', 'ramdisk_offset', 'second_offset', 'tags_offset', 'dtb_offset'):
        command.extend(['--'+name, profile[name]])
    subprocess.run(command+['--output', str(candidate)], check=True)
    result = verify_candidate(candidate, args.stock, kernel, dtb, args.initramfs)
    unpacked = output/'unpacked-verification'
    log = subprocess.check_output(['unpack_bootimg', '--boot_img', str(candidate), '--out', str(unpacked)], text=True)
    (output/'unpack-verification.txt').write_text(log)
    for name, source in (('kernel', kernel), ('ramdisk', args.initramfs), ('dtb', dtb)):
        if sha256((unpacked/name).read_bytes()) != sha256(source.read_bytes()):
            raise ValueError('Independent AOSP unpack mismatch: ' + name)
    # Keep the whole corresponding kernel/DTB/config/module set together.
    (output/'kernel').mkdir()
    for name in inputs['files_sha256']:
        shutil.copyfile(artifacts/name, output/'kernel'/name)
    shutil.copyfile(artifacts/'kernel-manifest.json', output/'kernel/kernel-manifest.json')
    for source in (args.initramfs, init_report_path):
        if source.resolve().parent != output:
            shutil.copyfile(source, output/source.name)
    shutil.copyfile(profile_path, output/profile_path.name)
    (output/'pack-command.json').write_text(json.dumps(command+['--output', str(candidate)], indent=2)+'\n')
    (output/'stock-layout.json').write_text(json.dumps(stock, indent=2)+'\n')
    tools = subprocess.check_output(['dpkg-query', '-W', 'mkbootimg', 'debootstrap', 'qemu-user', 'cpio', 'busybox-static', 'dropbear-bin'], text=True)
    (output/'host-tools.tsv').write_text(tools)
    # Build the community's no-op DTBO as a separate, explicitly unauthorized
    # write candidate. The pinned community package uses a raw DTB, not an invented
    # Android DTBO table with zero entries.
    lock = json.loads((project/'sources.lock.json').read_text())
    bringup = next(r for r in lock['repositories'] if r['id'] == 'kebab-bringup')
    repository = project/bringup['path']
    device_path = 'device/oneplus-kebab/'
    source_name = 'sm8250-oneplus-kebab-empty.dts'
    def git_file(name):
        return subprocess.check_output(['git', '-c', 'safe.directory='+str(repository.resolve()),
                                        '-C', str(repository), 'show', bringup['commit']+':'+device_path+name])
    source = git_file(source_name)
    apkbuild = git_file('APKBUILD').decode()
    expected = re.search(r'^([a-f0-9]{128})  '+re.escape(source_name)+r'$', apkbuild, re.M)
    import hashlib
    if expected is None or hashlib.sha512(source).hexdigest() != expected.group(1):
        raise ValueError('Community empty DTBO source checksum mismatch')
    source_file = output/source_name
    source_file.write_bytes(source)
    empty_dtbo = output/'dtbo-empty-community.img'
    subprocess.run(['dtc', '-I', 'dts', '-O', 'dtb', '-o', str(empty_dtbo), str(source_file)], check=True)
    # libfdt must accept the no-op overlay without changing the board tree.
    empty_applied = output/'unpacked-verification/empty-overlay-applied.dtb'
    subprocess.run(['fdtoverlay', '-i', str(dtb), '-o', str(empty_applied), str(empty_dtbo)], check=True)
    base_text = subprocess.check_output(['dtc', '-q', '-I', 'dtb', '-O', 'dts', str(dtb)])
    applied_text = subprocess.check_output(['dtc', '-q', '-I', 'dtb', '-O', 'dts', str(empty_applied)])
    if base_text != applied_text:
        raise ValueError('Community empty overlay unexpectedly changes the board description')
    hashes = {str(p.relative_to(output)): sha256(p.read_bytes()) for p in sorted(output.rglob('*')) if p.is_file()}
    manifest = {
        'created_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'device': 'OnePlus 8T KB2000', 'distribution': 'Debian 13 ARM64 rescue userspace',
        'kernelrelease': release, 'boot_image': result, 'files_sha256': hashes,
        'purpose': 'First temporary RAM boot experiment, not a persistent Debian installation',
        'stock_bootloader_temporary_boot_verified_for_android': True,
        'stock_dtbo_with_mainline_dtb_verified': False, 'hardware_boot_verified': False,
        'partition_flash_authorized': False, 'phone_deployment_executed': False,
        'ufs_rootfs_target_selected': False, 'private_ssh_keys_embedded': False,
        'optional_dtbo_candidate': {'file': empty_dtbo.name, 'source_commit': bringup['commit'],
                                    'source_sha512': expected.group(1), 'license': 'MIT',
                                    'offline_noop_overlay_verified': True,
                                    'hardware_abl_acceptance_verified': False,
                                    'write_requires_separate_authorization': True},
    }
    (output/'boot-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps({'output': str(output), 'kernelrelease': release,
                      'boot_bytes': result['bytes'], 'boot_sha256': result['sha256'],
                      'aosp_unpack_verified': True, 'hardware_boot_verified': False}, indent=2))


if __name__ == '__main__':
    main()
