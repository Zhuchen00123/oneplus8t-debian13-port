#!/usr/bin/env python3
"""Audit sealed boot/rootfs pairing and boot-critical contents offline."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

from boot_image import inspect


def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('candidate',type=Path)
    a=p.parse_args(); out=a.candidate.resolve()
    m=json.loads((out/'server-manifest.json').read_text())
    for name,wanted in m['files_sha256'].items():
        f=out/name
        if not f.resolve().is_relative_to(out) or sha(f)!=wanted:raise ValueError('Candidate checksum mismatch: '+name)
    assert not m['phone_deployment_executed'] and not m['hardware_boot_verified']
    boot=inspect(out/'boot-server.img')
    assert boot['cmdline_sha256']==hashlib.sha256(m['cmdline'].encode()).hexdigest()
    assert 'root=UUID='+m['rootfs_uuid'] in m['cmdline']
    # Debian combines an early uncompressed CPIO with the compressed main
    # archive. Use its format-aware utility instead of assuming raw gzip.
    listing=subprocess.check_output(['lsinitramfs',str(out/'initramfs-server.gz')],text=True).splitlines()
    def contains(end):return any(x.endswith(end) for x in listing)
    for required in ('init','scripts/init-premount/kebab-usb','sbin/ip','sbin/fsck','sbin/e2fsck',
                     'usr/local/libexec/kebab-usb-gadget','firmware/postmarketos/adsp.mbn',
                     'firmware/postmarketos/adsp.b18','firmware/qcom/a650_zap.mdt','firmware/qcom/a650_zap.b02'):
        if not contains(required):raise ValueError('Boot-critical initramfs file missing: '+required)
    for module in ('libcomposite','usb_f_rndis','usb_f_acm','phy-qcom-qusb2'):
        if not any(Path(x).name.startswith(module+'.ko') for x in listing):raise ValueError('Missing USB module: '+module)
    with tarfile.open(out/'debian13-server.tar.gz') as t:
        names={x.name.removeprefix('./'):x for x in t.getmembers()}
        def read(name):return t.extractfile(names[name]).read()
        assert 'UUID='+m['rootfs_uuid'] in read('etc/fstab').decode()
        assert read('etc/machine-id')==b''
        assert names['var/lib/dbus/machine-id'].issym() and names['var/lib/dbus/machine-id'].linkname=='/etc/machine-id'
        assert not any(x.startswith('etc/ssh/ssh_host_') or x.startswith('run/kebab-qa/') for x in names)
        assert hashlib.sha256(read('home/debian/.ssh/authorized_keys')).hexdigest()==m['authorized_public_key_sha256']
        assert b'PasswordAuthentication no' in read('etc/ssh/sshd_config.d/90-kebab.conf')
        assert 'etc/systemd/system/multi-user.target.wants/kebab-usb-gadget.service' in names
        assert 'etc/systemd/system/multi-user.target.wants/systemd-networkd.service' in names
        assert 'etc/systemd/system/multi-user.target.wants/qrtr-ns.service' in names
        assert 'etc/wpa_supplicant/wpa_supplicant-wlan0.conf' not in names
        assert 'etc/systemd/network/20-kebab-wifi.network' in names
        usb = read('usr/local/libexec/kebab-usb-gadget')
        for attribute, value in (('class', '0xef'), ('subclass', '0x04'), ('protocol', '0x01')):
            assert f"printf '{value}\\n' > \"$g/functions/rndis.usb0/{attribute}\"".encode() in usb
        assert usb.index(b'"$g/configs/c.1/01-network"') < usb.index(b'"$g/configs/c.1/02-serial"')
        network = read('etc/systemd/network/10-kebab-usb.network')
        for option in (b'DHCPServer=yes', b'PoolOffset=2', b'PoolSize=1', b'EmitRouter=no', b'EmitDNS=no'):
            assert option in network
        assert b'Name=usb0' in read('etc/systemd/network/05-kebab-usb.link')
        sources=json.loads((out/'integration.json').read_text())['upstream_files']
        for source in sources:
            assert hashlib.sha256(read(source['destination'].lstrip('/'))).hexdigest()==source['sha256']
        fw=json.loads((out/'firmware-staged.json').read_text())
        for f in fw['files']:
            assert hashlib.sha256(read('usr/lib/firmware/'+f['destination'])).hexdigest()==f['sha256']
        assert b'blacklist mhi_pci_generic' in read('etc/modprobe.d/90-kebab-server.conf')
        assert b'blacklist snd_soc_tfa2' in read('etc/modprobe.d/90-kebab-server.conf')
    result={'all_manifest_files_sha256_verified':True,'boot_fstab_uuid_consistent':True,
            'initramfs_usb_fsck_and_oem_split_files_present':True,'oem_and_upstream_payloads_verified':True,
            'no_exported_ssh_host_keys_or_qa_private_keys':True,'wifi_credentials_not_embedded':True,
            'hardware_boot_verified':False}
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
