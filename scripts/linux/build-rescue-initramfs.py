#!/usr/bin/env python3
"""Build a Debian ARM64 RAM rescue image from verified kernel artifacts."""
import argparse
from collections import deque
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def root_file(root, guest):
    """Resolve guest absolute symlinks against the guest, never the host /lib."""
    pending, current, links = deque(PurePosixPath(guest).parts), PurePosixPath('/'), 0
    while pending:
        part = pending.popleft()
        if part in ('/', '.', ''):
            continue
        if part == '..':
            current = current.parent
            continue
        candidate = root / str(current / part).lstrip('/')
        if candidate.is_symlink():
            links += 1
            if links > 40:
                raise ValueError('Guest symlink loop: ' + guest)
            target = PurePosixPath(os.readlink(candidate))
            if target.is_absolute():
                current = PurePosixPath('/')
            pending.extendleft(reversed(target.parts))
        else:
            current /= part
    source = root / str(current).lstrip('/')
    if not source.is_file():
        raise FileNotFoundError(source)
    return source


def require_arm64(path):
    header = path.read_bytes()[:64]
    if header[:5] != b'\x7fELF\x02' or header[5] != 1 or int.from_bytes(header[18:20], 'little') != 183:
        raise ValueError('Not a little-endian ARM64 ELF: ' + str(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rootfs', type=Path, required=True)
    parser.add_argument('--kernel-artifacts', type=Path, required=True)
    parser.add_argument('--authorized-key', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rootfs, artifacts, tree = args.rootfs.resolve(), args.kernel_artifacts.resolve(), args.work.resolve()
    if tree.exists() or args.output.exists():
        raise ValueError('Existing outputs are preserved; choose fresh work/output paths')
    manifest = json.loads((artifacts/'kernel-manifest.json').read_text())
    for name, expected in manifest['files_sha256'].items():
        if Path(name).name != name or digest(artifacts/name) != expected:
            raise ValueError('Kernel input checksum/path mismatch: ' + name)
    release = (artifacts/'kernel.release').read_text().strip()
    if not re.fullmatch(r'[A-Za-z0-9.+_-]+', release):
        raise ValueError('Invalid kernel release')
    key = args.authorized_key.read_text().strip()
    if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]+)?', key):
        raise ValueError('Expected one public Ed25519 key; private keys are forbidden')
    for directory in ('bin', 'sbin', 'usr/bin', 'usr/sbin', 'etc', 'dev', 'proc', 'sys',
                      'sys/kernel/config', 'run', 'tmp', 'root/.ssh'):
        (tree/directory).mkdir(parents=True, exist_ok=True)
    copied = set()

    def copy_elf(guest):
        if guest in copied:
            return
        source = root_file(rootfs, guest)
        require_arm64(source)
        target = tree/guest.lstrip('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o755)
        copied.add(guest)
        details = subprocess.check_output(['llvm-readelf', '--dynamic', '--program-headers', str(source)], text=True)
        interpreter = re.search(r'Requesting program interpreter: ([^\]]+)', details)
        if interpreter:
            copy_elf(interpreter.group(1))
        for name in re.findall(r'Shared library: \[([^\]]+)\]', details):
            for prefix in ('/lib/aarch64-linux-gnu/', '/usr/lib/aarch64-linux-gnu/', '/lib/', '/usr/lib/'):
                try:
                    root_file(rootfs, prefix+name)
                except FileNotFoundError:
                    continue
                copy_elf(prefix+name)
                break
            else:
                raise ValueError('Missing ARM64 library: ' + name)

    copy_elf('/bin/busybox')
    copy_elf('/usr/sbin/dropbear')
    copy_elf('/usr/bin/dropbearkey')
    applets = subprocess.check_output(['qemu-aarch64-static', str(tree/'bin/busybox'), '--list-full'], text=True).splitlines()
    for applet in applets:
        if applet.startswith('/') or '..' in PurePosixPath(applet).parts:
            raise ValueError('Unexpected BusyBox applet path')
        target = tree/applet
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to('/bin/busybox')
    scripts = Path(__file__).resolve().parent
    for name, dest in (('rescue-init.sh', 'init'), ('usb-gadget.sh', 'sbin/kebab-usb-gadget')):
        shutil.copyfile(scripts/name, tree/dest)
        (tree/dest).chmod(0o755)
    (tree/'root/.ssh').chmod(0o700)
    (tree/'root/.ssh/authorized_keys').write_text(key+'\n')
    (tree/'root/.ssh/authorized_keys').chmod(0o600)
    (tree/'etc/passwd').write_text('root:x:0:0:RAM rescue:/root:/bin/sh\n')
    # Invalid crypt string, no shared/default password, and dropbear -s disables password auth.
    (tree/'etc/shadow').write_text('root:x:0:0:99999:7:::\n')
    (tree/'etc/shadow').chmod(0o600)
    (tree/'etc/group').write_text('root:x:0:\n')
    (tree/'etc/shells').write_text('/bin/sh\n')
    shutil.copyfile(root_file(rootfs, '/etc/os-release'), tree/'etc/os-release')
    os.mknod(tree/'dev/console', stat.S_IFCHR | 0o600, os.makedev(5, 1))
    os.mknod(tree/'dev/null', stat.S_IFCHR | 0o666, os.makedev(1, 3))
    module_dir = tree/'lib/modules'/release
    module_dir.mkdir(parents=True)
    with tarfile.open(artifacts/'modules.tar.gz') as archive:
        prefix = 'lib/modules/'+release+'/'
        members = {m.name[len(prefix):]: m for m in archive.getmembers() if m.name.startswith(prefix) and m.isfile()}
        dependencies = {}
        for line in archive.extractfile(members['modules.dep']).read().decode().splitlines():
            name, deps = line.split(':', 1)
            dependencies[name] = deps.split()
        names = {PurePosixPath(p).name.split('.ko')[0].replace('-', '_'): p for p in dependencies}
        pending = [names[n] for n in ('phy_qcom_qusb2', 'libcomposite', 'usb_f_acm', 'usb_f_rndis', 'usb_f_ecm', 'usb_f_ncm')]
        selected = set()
        while pending:
            item = pending.pop()
            if item in selected:
                continue
            selected.add(item)
            pending.extend(dependencies[item])
        for relative in sorted(selected):
            if PurePosixPath(relative).is_absolute() or '..' in PurePosixPath(relative).parts:
                raise ValueError('Unsafe module path')
            content = archive.extractfile(members[relative]).read()
            if relative.endswith('.zst'):
                content = subprocess.check_output(['zstd', '-d', '-c'], input=content)
                relative = relative[:-4]
            elif not relative.endswith('.ko'):
                raise ValueError('Unsupported module compression')
            target = module_dir/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            vermagic = subprocess.check_output(['modinfo', '-F', 'vermagic', str(target)], text=True).strip()
            if not vermagic.startswith(release+' '):
                raise ValueError('Rescue module version mismatch')
        for name in ('modules.builtin', 'modules.builtin.modinfo'):
            (module_dir/name).write_bytes(archive.extractfile(members[name]).read())
        (module_dir/'modules.order').write_text(''.join(p.removesuffix('.zst')+'\n' for p in sorted(selected)))
    subprocess.run(['depmod', '-b', str(tree), release], check=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    paths = ['.'] + sorted(str(p.relative_to(tree)) for p in tree.rglob('*'))
    archive = subprocess.run(['cpio', '--null', '-o', '--format=newc', '--owner=0:0', '--reproducible'],
                             cwd=tree, input=('\0'.join(paths)+'\0').encode(), capture_output=True, check=True)
    args.output.write_bytes(gzip.compress(archive.stdout, mtime=0))
    report = {
        'kernelrelease': release, 'architecture': 'arm64', 'modules_count': len(selected),
        'modules': sorted(selected), 'modules_uncompressed_for_busybox': True,
        'elf_files': sorted(copied), 'initramfs_bytes': args.output.stat().st_size,
        'initramfs_sha256': digest(args.output), 'authorized_public_key_sha256': digest(args.authorized_key),
        'scripts_sha256': {name: digest(scripts/name) for name in ('rescue-init.sh', 'usb-gadget.sh')},
        'storage_mounts': [], 'storage_writes': False, 'firmware_included': False,
        'ssh_password_login': False, 'private_ssh_keys_embedded': False,
        'usb_network_function': 'rndis', 'ssh_device_ipv4': '192.168.8.1',
        'hardware_validated': False,
    }
    args.output.with_suffix('.manifest.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
