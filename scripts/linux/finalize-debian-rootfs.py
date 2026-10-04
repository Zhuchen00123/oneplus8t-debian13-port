#!/usr/bin/env python3
"""Stage matching kernel/modules in the base rootfs and export host-only archives."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--kernel-artifacts', type=Path, required=True)
    parser.add_argument('--authorized-key', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    build, kernel, rootfs = args.build_dir.resolve(), args.kernel_artifacts.resolve(), args.build_dir.resolve()/'rootfs'
    if args.output_dir.exists() or (build/'rootfs-finalized.json').exists():
        raise ValueError('Preserve existing exports; choose a new finalized rootfs')
    info = json.loads((build/'rootfs-manifest.json').read_text())
    if info['phase'] != 'base_userspace_prepared' or info['architecture'] != 'arm64':
        raise ValueError('Base userspace is not prepared')
    audit = subprocess.check_output(['chroot', str(rootfs), 'dpkg', '--audit'], text=True)
    if audit.strip() or subprocess.check_output(['chroot', str(rootfs), 'dpkg', '--print-architecture'], text=True).strip() != 'arm64':
        raise ValueError('ARM64 dpkg state is incomplete')
    manifest = json.loads((kernel/'kernel-manifest.json').read_text())
    for name, wanted in manifest['files_sha256'].items():
        if Path(name).name != name or digest(kernel/name) != wanted:
            raise ValueError('Kernel source checksum/path mismatch: ' + name)
    release = (kernel/'kernel.release').read_text().strip()
    destination = rootfs/'usr/lib/modules'/release
    if destination.exists():
        raise ValueError('Existing modules are preserved')
    # The generated, checksum-verified archive must contain only module files
    # and directories; never follow archive symlinks into the build host.
    with tarfile.open(kernel/'modules.tar.gz') as archive:
        members = archive.getmembers()
        for member in members:
            parts = PurePosixPath(member.name).parts
            if member.name.startswith('/') or '..' in parts or parts[:1] != ('lib',) or not (member.isdir() or member.isfile()):
                raise ValueError('Unsafe module archive member')
            if len(parts) > 1 and parts[1] != 'modules':
                raise ValueError('Module archive contains unrelated files')
            if len(parts) > 2 and parts[2] != release:
                raise ValueError('Module archive contains a different version')
        for member in members:
            if len(PurePosixPath(member.name).parts) < 3:
                continue
            target = rootfs/'usr'/member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())
                target.chmod(member.mode & 0o777)
    modules = sorted(destination.rglob('*.ko*'))
    for module in modules:
        version = subprocess.check_output(['modinfo', '-F', 'vermagic', str(module)], text=True).strip()
        if not version.startswith(release+' '):
            raise ValueError('Rootfs module version mismatch')
    expected_count = json.loads((kernel/'module-verification.json').read_text())['modules_checked']
    if len(modules) != expected_count:
        raise ValueError('Rootfs module count mismatch')
    subprocess.run(['depmod', '-b', str(rootfs), release], check=True)
    for source, target in ((kernel/'Image.gz', rootfs/'boot'/('vmlinuz-'+release)),
                           (kernel/'kernel.config', rootfs/'boot'/('config-'+release)),
                           (kernel/'System.map', rootfs/'boot'/('System.map-'+release)),
                           (kernel/'sm8250-oneplus-kebab.dtb', rootfs/'boot/dtb/sm8250-oneplus-kebab.dtb')):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    scripts = Path(__file__).resolve().parent
    gadget = rootfs/'usr/local/sbin/kebab-usb-gadget'
    gadget.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(scripts/'usb-gadget.sh', gadget)
    gadget.chmod(0o755)
    service = rootfs/'etc/systemd/system/kebab-usb-gadget.service'
    service.write_text('[Unit]\nDescription=KB2000 USB network and serial\nAfter=systemd-modules-load.service systemd-udevd.service\nBefore=ssh.service\n'
                       '[Service]\nType=oneshot\nExecStart=/usr/local/sbin/kebab-usb-gadget rndis\nRemainAfterExit=yes\n'
                       '[Install]\nWantedBy=multi-user.target\n')
    (rootfs/'etc/systemd/system/multi-user.target.wants/kebab-usb-gadget.service').symlink_to('../kebab-usb-gadget.service')
    network = rootfs/'etc/systemd/network'
    network.mkdir(parents=True, exist_ok=True)
    (network/'10-kebab-usb.link').write_text('[Match]\nMACAddress=02:00:00:00:08:01\n[Link]\nName=usb0\n')
    key = args.authorized_key.read_text().strip()
    if not key.startswith('ssh-ed25519 ') or '\n' in key or '\r' in key:
        raise ValueError('Expected one public SSH key')
    authorized = rootfs/'home/debian/.ssh/authorized_keys'
    authorized.write_text(key+'\n')
    authorized.chmod(0o600)
    if list((rootfs/'etc/ssh').glob('ssh_host_*')):
        raise ValueError('SSH host identities must be generated on first device boot')
    args.output_dir.mkdir(parents=True)
    for source in (build/'rootfs-manifest.json', build/'rootfs-packages.tsv', build/'rootfs-build.log'):
        shutil.copyfile(source, args.output_dir/source.name)
    subprocess.run(['tar', '--numeric-owner', '--sort=name', '--one-file-system', '-C', str(rootfs),
                    '-czf', str(args.output_dir/'debian13-arm64-base.tar.gz'), '.'], check=True)
    subprocess.run(['tar', '--sort=name', '-C', str(build/'packages'), '-czf', str(args.output_dir/'authenticated-packages.tar.gz'), '.'], check=True)
    hashes = {p.name: digest(p) for p in sorted(args.output_dir.iterdir()) if p.is_file()}
    result = {'created_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'phase': 'base_userspace_with_matching_kernel_modules_exported', 'distribution': 'Debian 13 trixie', 'architecture': 'arm64',
              'kernelrelease': release, 'modules_count': len(modules), 'all_module_vermagic_verified': True,
              'kernel_manifest_sha256': digest(kernel/'kernel-manifest.json'),
              'authorized_public_key_sha256': digest(args.authorized_key), 'files_sha256': hashes,
              'usb_gadget_recipe_staged': True, 'ssh_public_key_login_only': True, 'login_user': 'debian',
              'firmware_installed': False, 'alsa_ucm_integrated': False, 'qrtr_services_integrated': False,
              'rootfs_block_image_created': False, 'rootfs_uuid': None, 'installation_target': None,
              'hardware_boot_verified': False, 'phone_deployment_executed': False,
              'limitations': 'Base system archive only; choose/test rootfs destination and integrate device firmware/services before persistent installation'}
    (args.output_dir/'rootfs-export-manifest.json').write_text(json.dumps(result, indent=2)+'\n')
    (build/'rootfs-finalized.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k != 'files_sha256'}, indent=2))


if __name__ == '__main__':
    main()
