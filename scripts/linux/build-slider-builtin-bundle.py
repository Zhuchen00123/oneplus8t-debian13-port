#!/usr/bin/env python3
"""Host-only r3 candidate: preserve sealed boot policy, replace module payloads."""
import argparse
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

from boot_image import inspect, verify_candidate


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def cpio(directory):
    names = subprocess.check_output(['find', '.', '-print0'], cwd=directory)
    return subprocess.check_output(['cpio', '--quiet', '--null', '-o', '-H', 'newc',
                                    '--owner=0:0'], input=names, cwd=directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel-artifacts', type=Path, required=True)
    parser.add_argument('--baseline-bundle', type=Path, required=True)
    parser.add_argument('--stock-boot', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[2]
    kernel = args.kernel_artifacts.resolve()
    baseline = args.baseline_bundle.resolve()
    output = args.output_dir.resolve()
    if output.exists():
        raise ValueError('Existing outputs are preserved; choose a fresh directory')
    km = json.loads((kernel/'kernel-manifest.json').read_text())
    for name, expected in km['files_sha256'].items():
        if Path(name).name != name or sha(kernel/name) != expected:
            raise ValueError('Kernel group checksum mismatch')
    release = (kernel/'kernel.release').read_text().strip()
    if release != '7.2.0-kebab-debian13-r3-slider+':
        raise ValueError('Unexpected candidate kernel release')
    if 'CONFIG_INPUT_KEBAB_ALERT_SLIDER=y' not in (kernel/'kernel.config').read_text():
        raise ValueError('Slider is not configured builtin')
    if gzip.decompress((kernel/'Image.gz').read_bytes()) != (kernel/'Image').read_bytes():
        raise ValueError('Compressed kernel differs from Image')
    tree = (kernel/'compiled.dts').read_text()
    hall = re.search(r'alert-slider@18\s*\{([^{}]*)\}', tree)
    rail = re.search(r'ldo10\s*\{([^{}]*regulator-name = "vreg_l10c_3p0";[^{}]*)\}', tree)
    if not hall or not rail:
        raise ValueError('Compiled board supply/slider node absent')
    def cells(block, name):
        match = re.search(re.escape(name)+r'\s*=\s*<([^>]*)>', block)
        if not match:
            raise ValueError('Compiled DT property absent: '+name)
        return [int(value, 0) for value in match.group(1).split()]
    if ('compatible = "oneplus,kebab-alert-slider";' not in hall.group(1) or
            cells(hall.group(1), 'reg') != [0x18] or
            cells(hall.group(1), 'oneplus,companion-address') != [0x19] or
            cells(hall.group(1), 'oneplus,calibration') != [55, 148, 93, 102, 147, 55] or
            cells(hall.group(1), 'oneplus,tolerance') != [12] or
            cells(hall.group(1), 'vdd-supply') != cells(rail.group(1), 'phandle') or
            cells(rail.group(1), 'regulator-min-microvolt') != [3000000] or
            cells(rail.group(1), 'regulator-max-microvolt') != [3000000]):
        raise ValueError('Compiled slider DT properties mismatch')
    original = json.loads((baseline/'bundle-manifest.json').read_text())
    old_release = original['kernelrelease']
    if old_release != '7.2.0-kebab-debian13-r2-charge-policy+':
        raise ValueError('Unexpected baseline kernel release')
    for name in ['boot-control.img', 'initramfs-server.gz', 'modules.tar.gz']:
        if sha(baseline/name) != original['files_sha256'][name]:
            raise ValueError('Sealed baseline checksum mismatch')
    profile = json.loads((project/'config/boot-kb2000.json').read_text())
    if sha(args.stock_boot) != profile['stock_boot_sha256']:
        raise ValueError('Measured stock boot mismatch')
    old_boot = (baseline/'boot-control.img').read_bytes()
    cmdline = (old_boot[64:576].split(b'\0', 1)[0] +
               old_boot[608:1632].split(b'\0', 1)[0]).decode()
    assert 'root=UUID='+original['rootfs_uuid'] in cmdline
    assert 'mp2762_otg_regulator.allow_charge_control=1' in cmdline
    spec = importlib.util.spec_from_file_location('payloads', project/'scripts/linux/verify-module-payloads.py')
    payloads = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(payloads)
    reference, metadata = {}, {}
    with tarfile.open(kernel/'modules.tar.gz') as archive:
        for member in archive:
            if not member.isfile():
                continue
            parts = Path(member.name).parts
            if '..' in parts or member.name.startswith('/'):
                raise ValueError('Unsafe module archive member')
            key = payloads.module_path(member.name, release)
            if key is not None:
                raw_key = key.split('.ko')[0]+'.ko'
                if raw_key in reference:
                    raise ValueError('Duplicate module')
                reference[raw_key] = payloads.uncompressed(member.name, archive.extractfile(member).read())
            elif member.name.startswith('lib/modules/'+release+'/modules.'):
                metadata[Path(member.name).name] = archive.extractfile(member).read()
    if len(reference) != 578 or b'kebab_alert_slider.ko' not in metadata['modules.builtin']:
        raise ValueError('Expected complete module group and builtin slider entry')
    output.mkdir(parents=True)
    initramfs = output/'initramfs-server.gz'
    with tempfile.TemporaryDirectory(prefix='kebab-slider-initramfs-') as tmp:
        unpack = Path(tmp)/'unpack'
        subprocess.run(['unmkinitramfs', str(baseline/'initramfs-server.gz'), str(unpack)], check=True)
        main_tree = unpack/'main' if (unpack/'main').is_dir() else unpack
        modules = list(main_tree.rglob('*.ko*'))
        expected_count = original['initramfs_audit']['modules_checked']
        if len(modules) != expected_count:
            raise ValueError('Baseline initramfs module count differs from audit')
        new_roots, checked = set(), set()
        for module in modules:
            if module.is_symlink() or not module.resolve().is_relative_to(main_tree):
                raise ValueError('Unsafe initramfs module path')
            relative = module.relative_to(main_tree)
            key = payloads.module_path(relative.as_posix(), old_release).split('.ko')[0]+'.ko'
            if key not in reference or key in checked:
                raise ValueError('Unknown or duplicate boot module')
            prefix, suffix = relative.as_posix().split('/'+old_release+'/', 1)
            new_root = main_tree/prefix/release
            destination = new_root/key
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(reference[key])
            new_roots.add(new_root)
            checked.add(key)
        if len(new_roots) != 1:
            raise ValueError('Ambiguous module root')
        new_root = new_roots.pop()
        for name in ['modules.builtin', 'modules.builtin.modinfo', 'modules.order']:
            (new_root/name).write_bytes(metadata[name])
        if 'modules.builtin.ranges' in metadata:
            (new_root/'modules.builtin.ranges').write_bytes(metadata['modules.builtin.ranges'])
        # Move the preserved old subtree outside the tree being repacked.
        old_root = new_root.parent/old_release
        assert old_root.resolve().is_relative_to(main_tree.resolve())
        old_root.rename(Path(tmp)/'old-modules')
        moduledir = '/'+new_root.parent.relative_to(main_tree).as_posix()
        subprocess.run(['depmod', '-b', str(main_tree), '-m', moduledir, release], check=True)
        early = sorted(p for p in unpack.iterdir() if p.is_dir() and p.name.startswith('early')) if main_tree != unpack else []
        initramfs.write_bytes(b''.join(cpio(p) for p in early) + gzip.compress(cpio(main_tree), mtime=0))
        second = Path(tmp)/'verify'
        subprocess.run(['unmkinitramfs', str(initramfs), str(second)], check=True)
        actual = set()
        for module in second.rglob('*.ko*'):
            key = payloads.module_path(module.relative_to(second).as_posix(), release)
            key = key.split('.ko')[0]+'.ko'
            if key not in reference or key in actual or module.read_bytes() != reference[key]:
                raise ValueError('Repacked initramfs module bytes mismatch')
            actual.add(key)
        if actual != checked:
            raise ValueError('Repacked module set differs')
    image = output/'boot-slider.img'
    command = ['mkbootimg', '--kernel', str(kernel/'Image.gz'), '--ramdisk', str(initramfs),
               '--dtb', str(kernel/'sm8250-oneplus-kebab.dtb'),
               '--header_version', str(profile['header_version']), '--pagesize', str(profile['pagesize']),
               '--base', profile['base'], '--os_version', profile['legacy_header_os_version'],
               '--os_patch_level', profile['legacy_header_os_patch_level'], '--cmdline', cmdline]
    for name in ['kernel_offset', 'ramdisk_offset', 'second_offset', 'tags_offset', 'dtb_offset']:
        command.extend(['--'+name, profile[name]])
    subprocess.run(command+['--output', str(image)], check=True)
    boot = verify_candidate(image, args.stock_boot, kernel/'Image.gz', kernel/'sm8250-oneplus-kebab.dtb', initramfs)
    if boot['cmdline_sha256'] != inspect(baseline/'boot-control.img')['cmdline_sha256']:
        raise ValueError('Root or charging boot policy changed')
    independent = output/'unpacked-verification'
    subprocess.run(['unpack_bootimg', '--boot_img', str(image), '--out', str(independent)],
                   check=True, stdout=subprocess.DEVNULL)
    for name, source in [('kernel', kernel/'Image.gz'), ('dtb', kernel/'sm8250-oneplus-kebab.dtb'), ('ramdisk', initramfs)]:
        if sha(independent/name) != sha(source):
            raise ValueError('Independent AOSP unpack mismatch')
    shutil.copyfile(kernel/'modules.tar.gz', output/'modules.tar.gz')
    shutil.copyfile(project/'scripts/linux/kebab-alert-slider.sh', output/'kebab-alert-slider')
    rollback = output/'rollback'
    rollback.mkdir()
    for name in ['boot-control.img', 'modules.tar.gz', 'bundle-manifest.json']:
        shutil.copyfile(baseline/name, rollback/name)
    report = {
        'kernelrelease': release, 'kernel_commit': json.loads((kernel/'build-info.json').read_text())['kernel_commit'],
        'modules_checked': len(reference), 'initramfs_modules_checked': len(checked),
        'initramfs_raw_module_bytes_match': True, 'slider_builtin': True,
        'compiled_dtb_board_properties_verified': True,
        'boot_policy_unchanged': True, 'stock_layout_and_aosp_unpack_verified': True,
        'phone_deployment_executed': False, 'hardware_boot_verified': False,
        'files_sha256': {name: sha(output/name) for name in ['boot-slider.img', 'initramfs-server.gz', 'modules.tar.gz', 'kebab-alert-slider']},
        'rollback_files_sha256': {name: sha(rollback/name) for name in ['boot-control.img', 'modules.tar.gz', 'bundle-manifest.json']},
        'boot': boot,
    }
    (output/'slider-bundle-manifest.json').write_text(json.dumps(report, indent=2)+'\n')
    print('Candidate prepared:', release, 'modules', len(reference), 'initramfs', len(checked))


if __name__ == '__main__':
    main()
