#!/usr/bin/env python3
"""Guarded r2 -> r3 boot_a/module update, without reboot or charger writes."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile


def digest(path, length=None):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        left = length
        while left is None or left:
            data = stream.read(4*1024**2 if left is None else min(left, 4*1024**2))
            if not data:
                if left:
                    raise ValueError('Short read')
                break
            h.update(data)
            if left is not None:
                left -= len(data)
    return h.hexdigest()


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('bundle', type=Path)
    p.add_argument('--current-backup', type=Path, required=True)
    p.add_argument('--guard-library', type=Path, required=True)
    a = p.parse_args()
    if command('id', '-u') != '0' or command('uname', '-m') != 'aarch64':
        raise ValueError('Root on the measured ARM64 phone required')
    if command('uname', '-r') != '7.2.0-kebab-debian13-r2-charge-policy+':
        raise ValueError('Unexpected currently running kernel')
    lock = Path('/run/lock/kebab-kernel-update.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    spec = importlib.util.spec_from_file_location('guards', a.guard_library)
    guards = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guards)
    base = a.bundle.resolve()
    manifest = json.loads((base/'slider-bundle-manifest.json').read_text())
    release = '7.2.0-kebab-debian13-r3-slider+'
    if (manifest['kernelrelease'] != release or not manifest['slider_builtin'] or
            not manifest['boot_policy_unchanged'] or
            not manifest['initramfs_raw_module_bytes_match'] or
            not manifest['compiled_dtb_board_properties_verified']):
        raise ValueError('Unverified slider candidate')
    for name, expected in manifest['files_sha256'].items():
        if Path(name).name != name or digest(base/name) != expected:
            raise ValueError('Candidate checksum/path mismatch')
    boot = Path('/dev/disk/by-partlabel/boot_a')
    if boot.resolve() != Path('/dev/sde11') or Path('/sys/class/block/sde11/size').read_text().strip() != '196608':
        raise ValueError('Measured boot_a mismatch')
    if command('findmnt', '-n', '-o', 'SOURCE', '/') != '/dev/sda15':
        raise ValueError('Root partition mismatch')
    slots = guards.gpt_boot_slots()
    if not slots['boot_a']['successful'] or slots['boot_a']['unbootable']:
        raise ValueError('A slot is not confirmed successful')
    saved = a.current_backup.resolve()
    if saved != Path('/var/backups/kebab-slider-builtin-20261006'):
        raise ValueError('Unexpected recovery directory')
    record = json.loads((saved/'manifest.json').read_text())
    previous = record['boot_sha256']
    if record['boot_bytes'] != 100663296 or (saved/'boot_a.img').stat().st_size != 100663296:
        raise ValueError('Full recovery image size mismatch')
    if digest(saved/'boot_a.img') != previous or digest(boot) != previous:
        raise ValueError('Current boot differs from verified full backup')
    image = base/'boot-slider.img'
    if not 0 < image.stat().st_size <= 100663296:
        raise ValueError('Candidate boot size invalid')
    # Compare cmdline bytes directly: no root/slot/charge boot policy changes.
    def cmdline(path):
        with path.open('rb') as stream:
            header = stream.read(1660)
        if header[:8] != b'ANDROID!':
            raise ValueError('Invalid boot header')
        return header[64:576].split(b'\0', 1)[0]+header[608:1632].split(b'\0', 1)[0]
    if cmdline(image) != cmdline(saved/'boot_a.img'):
        raise ValueError('Current root/charging boot arguments differ')
    charge = json.loads(Path('/run/kebab-charge-policy/status.json').read_text())
    if not charge['control_enabled'] or not charge['inhibit_readback_verified'] or charge['snapshot']['last_error']:
        raise ValueError('Current charge controller is not healthy')
    module_root = Path('/usr/lib/modules')/release
    if module_root.parent.resolve() != Path('/usr/lib/modules') or module_root.exists():
        raise ValueError('Module destination must be a fresh direct directory')
    import tarfile
    with tarfile.open(base/'modules.tar.gz') as archive:
        members = archive.getmembers()
        total = 0
        names = set()
        count = 0
        for item in members:
            parts = PurePosixPath(item.name).parts
            if (item.name.startswith('/') or '..' in parts or
                    parts[:3] != ('lib', 'modules', release) or
                    not (item.isfile() or item.isdir()) or item.name in names):
                raise ValueError('Unsafe module archive member')
            names.add(item.name)
            total += item.size
            count += item.isfile() and item.name.endswith(('.ko', '.ko.zst'))
        if count != manifest['modules_checked'] or count != 578:
            raise ValueError('Module count mismatch')
        if shutil.disk_usage('/').free < total+200*1024**2:
            raise ValueError('Insufficient free space')
        with tempfile.TemporaryDirectory(prefix='kebab-slider-modules-', dir='/var/tmp') as tmp:
            archive.extractall(tmp, filter='data')
            tree = Path(tmp)/'lib/modules'/release
            for module in tree.rglob('*.ko*'):
                if not command('modinfo', '-F', 'vermagic', str(module)).startswith(release+' '):
                    raise ValueError('Actual module version mismatch')
            shutil.move(str(tree), str(module_root))
    subprocess.run(['depmod', release], check=True)
    helper = Path('/usr/local/sbin/kebab-alert-slider')
    if digest(helper) != digest(saved/'kebab-alert-slider'):
        raise ValueError('Current slider helper changed after backup')
    shutil.copyfile(base/'kebab-alert-slider', helper)
    helper.chmod(0o755)
    try:
        guards.replace_boot(boot, image, saved/'boot_a.img', previous,
                            manifest['files_sha256']['boot-slider.img'])
    except Exception:
        shutil.copyfile(saved/'kebab-alert-slider', helper)
        helper.chmod(0o755)
        raise
    (saved/'deployment.json').write_text(json.dumps({
        'kernelrelease': release, 'kernel_commit': manifest['kernel_commit'],
        'target': 'boot_a', 'slots': slots, 'input_sha256': digest(image),
        'readback_sha256': digest(boot, image.stat().st_size),
        'helper_sha256': digest(helper), 'modules_installed': count,
        'reboot_executed': False, 'charger_registers_written': False,
    }, indent=2)+'\n')
    print('boot_a readback verified; matching modules installed; reboot not executed')


if __name__ == '__main__':
    main()
