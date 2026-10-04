#!/usr/bin/env python3
"""Host-only packaging of an in-place boot/modules/charge-policy update."""
import argparse
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import uuid

from boot_image import verify_candidate


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel-artifacts', type=Path, required=True)
    parser.add_argument('--initramfs', type=Path, required=True)
    parser.add_argument('--stock-boot', type=Path, required=True)
    parser.add_argument('--rootfs-uuid', type=uuid.UUID, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    a = parser.parse_args()
    project = Path(__file__).resolve().parents[2]
    k = a.kernel_artifacts.resolve()
    out = a.output_dir.resolve()
    if out.exists():
        raise ValueError('Choose a fresh output directory')
    km = json.loads((k / 'kernel-manifest.json').read_text())
    for name, expected in km['files_sha256'].items():
        if Path(name).name != name or sha(k / name) != expected:
            raise ValueError('Kernel artifact mismatch')
    release = (k / 'kernel.release').read_text().strip()
    if not release.endswith('-r2-charge-policy+'):
        raise ValueError('Unexpected kernel profile')
    if gzip.decompress((k / 'Image.gz').read_bytes()) != (k / 'Image').read_bytes():
        raise ValueError('Image compression mismatch')
    # Independently decode the actual initramfs modules, not its filename/manifest.
    spec = importlib.util.spec_from_file_location('payloads', project / 'scripts/linux/verify-module-payloads.py')
    payloads = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(payloads)
    raw_modules = {}
    with tarfile.open(k / 'modules.tar.gz') as tar:
        prefix = 'lib/modules/' + release + '/'
        modules = {}
        for item in tar:
            if item.isfile() and item.name.endswith(('.ko', '.ko.zst')):
                if not item.name.startswith(prefix):
                    raise ValueError('Module release mismatch')
                modules[item.name[len(prefix):]] = hashlib.sha256(tar.extractfile(item).read()).hexdigest()
                key = item.name[len(prefix):].split('.ko')[0] + '.ko'
                raw_modules[key] = payloads.digest(payloads.uncompressed(item.name, tar.extractfile(item).read()))
    if len(modules) != 578:
        raise ValueError('Expected 578 modules')
    with tempfile.TemporaryDirectory(prefix='kebab-initramfs-audit-') as temp:
        subprocess.run(['unmkinitramfs', str(a.initramfs), temp], check=True)
        checked = set()
        directory = Path(temp)
        for module in directory.rglob('*.ko*'):
            if module.is_symlink() or not module.is_file():
                raise ValueError('Unexpected module file type')
            key = payloads.module_path(module.relative_to(directory).as_posix(), release)
            key = key.split('.ko')[0] + '.ko'
            if key in checked or key not in raw_modules or payloads.digest(
                    payloads.uncompressed(module.name, module.read_bytes())) != raw_modules[key]:
                raise ValueError('Initramfs module content mismatch')
            checked.add(key)
        required = {'kernel/drivers/usb/gadget/libcomposite.ko',
                    'kernel/drivers/usb/gadget/function/usb_f_rndis.ko',
                    'kernel/drivers/usb/gadget/function/usb_f_acm.ko',
                    'kernel/drivers/usb/gadget/function/u_ether.ko',
                    'kernel/drivers/usb/gadget/function/u_serial.ko',
                    'kernel/drivers/phy/qualcomm/phy-qcom-qusb2.ko'}
        if not required.issubset(checked) or len(checked) < 8:
            raise ValueError('Required USB modules absent')
        initramfs_audit = {'modules_checked': len(checked), 'raw_module_bytes_match': True}
    profile = json.loads((project / 'config/boot-kb2000.json').read_text())
    if sha(a.stock_boot) != profile['stock_boot_sha256']:
        raise ValueError('Measured stock boot mismatch')
    out.mkdir(parents=True)
    files = {
        'modules.tar.gz': k / 'modules.tar.gz',
        'initramfs-server.gz': a.initramfs,
        'kebab-charge-policy.py': project / 'scripts/linux/kebab-charge-policy.py',
        'kebab-charge-policy.json': project / 'config/kebab-charge-policy.json',
        'kebab-charge-policy.service': project / 'config/kebab-charge-policy.service',
        'kebab-health.py': project / 'scripts/linux/kebab-health.py',
        'install-charge-policy.sh': project / 'scripts/linux/install-charge-policy.sh',
        'deploy-charge-policy-kernel.py': project / 'scripts/linux/deploy-charge-policy-kernel.py',
    }
    for name, source in files.items():
        shutil.copyfile(source, out / name)
    cmdline = f'root=UUID={a.rootfs_uuid} rootfstype=ext4 rootwait rw console=tty0 loglevel=6 panic=15'
    reports = {}
    for name, extra in [('boot-readonly.img', ''), ('boot-control.img',
                         ' mp2762_otg_regulator.allow_charge_control=1')]:
        cmd = ['mkbootimg', '--kernel', str(k / 'Image.gz'), '--ramdisk', str(a.initramfs),
               '--dtb', str(k / 'sm8250-oneplus-kebab.dtb'),
               '--header_version', str(profile['header_version']), '--pagesize', str(profile['pagesize']),
               '--base', profile['base'], '--os_version', profile['legacy_header_os_version'],
               '--os_patch_level', profile['legacy_header_os_patch_level'], '--cmdline', cmdline + extra]
        for offset in ('kernel_offset', 'ramdisk_offset', 'second_offset', 'tags_offset', 'dtb_offset'):
            cmd += ['--' + offset, profile[offset]]
        subprocess.run(cmd + ['--output', str(out / name)], check=True)
        reports[name] = verify_candidate(out / name, a.stock_boot, k / 'Image.gz',
                                        k / 'sm8250-oneplus-kebab.dtb', a.initramfs)
        unpack = out / ('unpacked-' + name)
        subprocess.run(['unpack_bootimg', '--boot_img', str(out / name), '--out', str(unpack)],
                       check=True, stdout=subprocess.DEVNULL)
        for part, source in [('kernel', k / 'Image.gz'), ('dtb', k / 'sm8250-oneplus-kebab.dtb'),
                             ('ramdisk', a.initramfs)]:
            if sha(unpack / part) != sha(source):
                raise ValueError('Independent AOSP unpack mismatch')
    payloads = list(files) + ['boot-readonly.img', 'boot-control.img']
    manifest = {'version': 1, 'kernelrelease': release, 'rootfs_uuid': str(a.rootfs_uuid),
                'lease_seconds': 90, 'stop_percent': 80, 'resume_percent': 75,
                'default_control_enabled': False, 'full_charge_cycle_validated': False,
                'files_sha256': {n: sha(out / n) for n in payloads}, 'modules': modules,
                'initramfs_audit': initramfs_audit,
                'boot_verification': reports}
    (out / 'bundle-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('Deployable local bundle prepared:', out)


if __name__ == '__main__':
    main()
