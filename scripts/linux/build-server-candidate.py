#!/usr/bin/env python3
"""Create a paired persistent boot/ext4 candidate on the host; never flash it."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import uuid

from boot_image import inspect, verify_candidate


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def run(command, **kwargs):
    return subprocess.run(command,check=True,**kwargs)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build-dir',type=Path,required=True)
    p.add_argument('--kernel-artifacts',type=Path,required=True)
    p.add_argument('--stock-boot',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--rootfs-uuid',type=uuid.UUID,
                   help='Explicit existing target UUID for an in-place boot/modules update; default generates a new UUID')
    a=p.parse_args()
    build=a.build_dir.resolve(); root=build/'rootfs'; kernel=a.kernel_artifacts.resolve()
    project=Path(__file__).resolve().parents[2]; out=a.output_dir.resolve()
    if out.exists() or (build/'server-packed.json').exists():
        raise ValueError('Preserve existing exports; choose fresh output')
    integration=json.loads((build/'integration.json').read_text())
    firmware=json.loads((build/'firmware-staged.json').read_text())
    if not integration['alsa_ucm_integrated'] or not firmware['split_elf_segments_and_metadata_verified']:
        raise ValueError('Missing userspace/firmware integration')
    km=json.loads((kernel/'kernel-manifest.json').read_text())
    for name,wanted in km['files_sha256'].items():
        if Path(name).name!=name or sha(kernel/name)!=wanted:
            raise ValueError('Kernel group mismatch: '+name)
    release=(kernel/'kernel.release').read_text().strip()
    for name,wanted in [('Image.gz','boot/vmlinuz-'+release),('kernel.config','boot/config-'+release),
                        ('sm8250-oneplus-kebab.dtb','boot/dtb/sm8250-oneplus-kebab.dtb')]:
        if sha(kernel/name)!=sha(root/wanted):
            raise ValueError('Rootfs kernel/DTB/config mismatch')
    modules=list((root/'usr/lib/modules'/release).rglob('*.ko*'))
    expected=json.loads((kernel/'module-verification.json').read_text())['modules_checked']
    if len(modules)!=expected:
        raise ValueError('Rootfs modules incomplete')
    for module in modules:
        vermagic=subprocess.check_output(['modinfo','-F','vermagic',str(module)],text=True)
        if not vermagic.startswith(release+' '): raise ValueError('Module version mismatch')
    for f in firmware['files']:
        if sha(root/'usr/lib/firmware'/f['destination'])!=f['sha256']:
            raise ValueError('Staged OEM firmware changed')
    public = ['ath11k/QCA6390/hw2.0/'+n for n in ('amss.bin','m3.bin','board-2.bin')]
    public += ['qcom/a650_sqe.fw','qcom/a650_gmu.bin','qca/htbtfw20.tlv','qca/htnv20.bin']
    public_fw={n:sha(root/'usr/lib/firmware'/n) for n in public}
    if not (root/'usr/sbin/fsck.ext4').exists():
        raise ValueError('Install e2fsprogs before packing the ext4 system')
    # Upstream a6xx SQE firmware version is its second little-endian word.
    import struct
    sqe=(root/'usr/lib/firmware/qcom/a650_sqe.fw').read_bytes()
    sqe_version=struct.unpack_from('<I',sqe,4)[0]&0xfff
    if sqe_version<0x95: raise ValueError('A650 SQE too old for reference hardware')
    if list((root/'etc/ssh').glob('ssh_host_*')) or (root/'usr/sbin/policy-rc.d').exists():
        raise ValueError('Build-only policy or shared SSH host keys remain')
    authorized=root/'home/debian/.ssh/authorized_keys'
    key=authorized.read_text().strip()
    if not key.startswith('ssh-ed25519 ') or '\n' in key or 'PRIVATE KEY' in key:
        raise ValueError('Expected a single public SSH key')
    if subprocess.check_output(['chroot',str(root),'dpkg','--audit'],text=True).strip():
        raise ValueError('Incomplete dpkg state')
    profile=json.loads((project/'config/boot-kb2000.json').read_text())
    if sha(a.stock_boot)!=profile['stock_boot_sha256']:
        raise ValueError('Measured stock boot input mismatch')
    root_uuid=str(a.rootfs_uuid or uuid.uuid4())
    (root/'etc/fstab').write_text(f'UUID={root_uuid} / ext4 defaults,noatime 0 1\n')
    cfg=root/'etc/initramfs-tools/conf.d/kebab-server'
    # The target UUID is an offline image, so fsck cannot infer its type
    # from build-host block devices. State the image's filesystem explicitly.
    cfg.write_text('MODULES=most\nCOMPRESS=gzip\nFSTYPE=ext4\n')
    (root/'etc/initramfs-tools/modules').write_text('phy_qcom_qusb2\nlibcomposite\nusb_f_rndis\nusb_f_acm\n')
    hooks=root/'etc/initramfs-tools/hooks'; hooks.mkdir(parents=True,exist_ok=True)
    hook=hooks/'kebab-server'
    hook.write_text('''#!/bin/sh
set -eu
case "${1:-}" in prereqs) exit 0;; esac
. /usr/share/initramfs-tools/hook-functions
copy_exec /usr/sbin/ip /sbin/ip
copy_exec /usr/bin/mountpoint /bin/mountpoint
mkdir -p "$DESTDIR/usr/local/libexec"
cp /usr/local/libexec/kebab-usb-gadget "$DESTDIR/usr/local/libexec/"
# Include split bins as well as metadata; the generic firmware hook only
# knows firmware-name, not the .bNN files derived by the MDT loader.
mkdir -p "$DESTDIR/lib/firmware/postmarketos" "$DESTDIR/lib/firmware/qcom"
cp /usr/lib/firmware/postmarketos/* "$DESTDIR/lib/firmware/postmarketos/"
cp /usr/lib/firmware/qcom/a650_zap.* "$DESTDIR/lib/firmware/qcom/"
cp /usr/lib/firmware/qcom/a650_sqe.fw /usr/lib/firmware/qcom/a650_gmu.bin "$DESTDIR/lib/firmware/qcom/"
'''); hook.chmod(0o755)
    premount=root/'etc/initramfs-tools/scripts/init-premount/kebab-usb'
    premount.parent.mkdir(parents=True,exist_ok=True)
    premount.write_text('''#!/bin/sh
case "${1:-}" in prereqs) exit 0;; esac
/usr/local/libexec/kebab-usb-gadget rndis || echo 'USB initramfs setup failed; continuing root mount' >&2
'''); premount.chmod(0o755)
    # No arm64 binaries are started as host services; QEMU runs build tools.
    out.mkdir(parents=True)
    with (out/'initramfs-build.log').open('w') as log:
        run(['unshare','--mount','--propagation','private','bash',
             str(project/'scripts/linux/chroot-build-command.sh'),str(root),
             'mkinitramfs','-o','/boot/initrd.img-'+release,release],stdout=log,stderr=subprocess.STDOUT)
    initrd=root/'boot'/('initrd.img-'+release)
    shutil.copyfile(initrd,out/'initramfs-server.gz')
    cmdline=f'root=UUID={root_uuid} rootfstype=ext4 rootwait rw console=tty0 loglevel=6 panic=15'
    command=['mkbootimg','--kernel',str(kernel/'Image.gz'),'--ramdisk',str(out/'initramfs-server.gz'),
             '--dtb',str(kernel/'sm8250-oneplus-kebab.dtb'),'--header_version',str(profile['header_version']),
             '--pagesize',str(profile['pagesize']),'--base',profile['base'],
             '--os_version',profile['legacy_header_os_version'],'--os_patch_level',profile['legacy_header_os_patch_level'],
             '--cmdline',cmdline]
    for field in ('kernel_offset','ramdisk_offset','second_offset','tags_offset','dtb_offset'):
        command += ['--'+field,profile[field]]
    boot=out/'boot-server.img'; run(command+['--output',str(boot)])
    checked=verify_candidate(boot,a.stock_boot,kernel/'Image.gz',kernel/'sm8250-oneplus-kebab.dtb',out/'initramfs-server.gz')
    with (out/'unpack-verification.txt').open('w') as log:
        run(['unpack_bootimg','--boot_img',str(boot),'--out',str(out/'unpacked')],stdout=log)
    for name,source in [('kernel',kernel/'Image.gz'),('dtb',kernel/'sm8250-oneplus-kebab.dtb'),('ramdisk',out/'initramfs-server.gz')]:
        if sha(out/'unpacked'/name)!=sha(source): raise ValueError('AOSP unpack mismatch')
    image=build/'rootfs-server.img'
    with image.open('xb') as f: f.truncate(4*1024**3)
    run(['mke2fs','-q','-t','ext4','-b','4096','-m','1','-L','kebab-debian13','-U',root_uuid,'-d',str(root),str(image)])
    with (out/'ext4-check.txt').open('w') as log: run(['e2fsck','-fn',str(image)],stdout=log,stderr=subprocess.STDOUT)
    if subprocess.check_output(['blkid','-p','-s','UUID','-o','value',str(image)],text=True).strip()!=root_uuid:
        raise ValueError('Root UUID mismatch')
    # Fastboot's Android sparse container, not an Android logical super image.
    run(['img2simg',str(image),str(out/'rootfs-server.sparse.img')])
    run(['simg2img',str(out/'rootfs-server.sparse.img'),str(build/'sparse-roundtrip.img')])
    if sha(image)!=sha(build/'sparse-roundtrip.img'): raise ValueError('Sparse roundtrip mismatch')
    shutil.rmtree(out/'unpacked')
    (out/'kernel').mkdir()
    for name in km['files_sha256']: shutil.copyfile(kernel/name,out/'kernel'/name)
    shutil.copyfile(kernel/'kernel-manifest.json',out/'kernel/kernel-manifest.json')
    rescue=project/'artifacts/boot-preparation-20261003/rescue-final'
    shutil.copyfile(rescue/'dtbo-empty-community.img',out/'dtbo-empty-community.img')
    for name in ('integration.json','firmware-staged.json','packages.tsv','firmware-package-policy.txt'):
        shutil.copyfile(build/name,out/name)
    if (build/'userspace-qa.json').exists():
        shutil.copyfile(build/'userspace-qa.json',out/'userspace-qa.json')
    run(['tar','--numeric-owner','--sort=name','--one-file-system','-C',str(root),'-czf',str(out/'debian13-server.tar.gz'),'.'])
    run(['tar','--sort=name','-C',str(build/'packages'),'-czf',str(out/'server-authenticated-packages.tar.gz'),'.'])
    hashes={x.relative_to(out).as_posix():sha(x) for x in sorted(out.rglob('*')) if x.is_file()}
    result={'created_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'kernelrelease':release,
            'rootfs_uuid':root_uuid,'rootfs_bytes':image.stat().st_size,'rootfs_raw_sha256':sha(image),'cmdline':cmdline,
            'files_sha256':hashes,'boot_layout_and_aosp_unpack_verified':True,'boot_bytes':checked['bytes'],
            'modules_count':len(modules),'all_module_vermagic_verified':True,'public_firmware_sha256':public_fw,
            'a650_sqe_version':hex(sqe_version),'oem_firmware_files':len(firmware['files']),
            'authorized_public_key_sha256':sha(authorized),'private_ssh_keys_embedded':False,
            'ext4_readonly_check_passed':True,'sparse_roundtrip_sha256_verified':True,
            'proposed_rootfs_target':'super (actual /dev/block/sda15); overwrites Android logical partitions',
            'phone_commands_in_builder':False,
            'deployment_authorization_source':'User authorization recorded separately in AGENTS.md; this builder grants no device permissions',
            'hardware_boot_verified':False,'phone_deployment_executed':False,
            'limitations':['This sealed kernel/boot/rootfs group requires hardware verification; prior RAM results do not validate it',
                           'USB DHCP/Wi-Fi/charging and sustained-load untested for this group',
                           'No modem/NV or Venus deployment; audio remains optional','Wi-Fi credentials not embedded']}
    (out/'server-manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    (build/'server-packed.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='files_sha256'},indent=2))


if __name__=='__main__': main()
