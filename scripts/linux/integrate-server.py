#!/usr/bin/env python3
"""Stage Debian-specific server integration in a build rootfs, never a phone."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build-dir', type=Path, required=True)
    p.add_argument('--bringup', type=Path, required=True)
    a = p.parse_args()
    build = a.build_dir.resolve()
    root = build/'rootfs'
    project = Path(__file__).resolve().parents[2]
    lock = json.loads((project/'sources.lock.json').read_text())
    wanted = next(r['commit'] for r in lock['repositories'] if r['id']=='kebab-bringup')
    # The source checkout belongs to Windows (CRLF); read pinned Git objects,
    # and check modifications using that checkout's line-ending convention.
    git = ['git','-c','safe.directory='+str(a.bringup.resolve()),'-c','core.autocrlf=true','-C',str(a.bringup)]
    actual = subprocess.check_output(git+['rev-parse','HEAD'],text=True).strip()
    if actual != wanted or subprocess.check_output(git+['status','--porcelain'],text=True):
        raise ValueError('Preserve/review changed community sources before integration')
    if not (root/'etc/debian_version').is_file() or (build/'integration.json').exists():
        raise ValueError('Expected an unintegrated staged Debian rootfs')
    upstream = []
    def write(name, text, mode=0o644):
        path = root/name.lstrip('/')
        if not path.resolve().is_relative_to(root):
            raise ValueError('Path escapes build rootfs')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(mode)
    def copy(src, dst, mode=0o644):
        relative = src.relative_to(a.bringup).as_posix()
        data = subprocess.check_output(git+['show',actual+':'+relative])
        write(dst, data.decode(), mode)
        upstream.append({'source': relative, 'destination': dst,
                         'sha256': hashlib.sha256(data).hexdigest()})
    device = a.bringup/'device/oneplus-kebab'
    for name, target, mode in [
        ('kebab-bluetooth-addr', '/usr/bin/kebab-bluetooth-addr', 0o755),
        ('kebab-bluetooth-addr@.service','/usr/lib/systemd/system/kebab-bluetooth-addr@.service',0o644),
        ('90-kebab-bluetooth-addr.rules','/usr/lib/udev/rules.d/90-kebab-bluetooth-addr.rules',0o644),
        ('77-mm-ignore-sdx55-efs.rules','/usr/lib/udev/rules.d/77-mm-ignore-sdx55-efs.rules',0o644),
        ('ucm-OnePlus-8T.conf','/usr/share/alsa/ucm2/conf.d/sm8250/OnePlus 8T.conf',0o644),
        ('ucm-HiFi.conf','/usr/share/alsa/ucm2/Qualcomm/sm8250/OnePlus8T/HiFi.conf',0o644),
        ('kebab-dp-mode','/usr/bin/kebab-dp-mode',0o755),
    ]:
        copy(device/name,target,mode)
    copy(a.bringup/'LICENSE','/usr/share/doc/kebab-community/COPYING')
    copy(device/'APKBUILD','/usr/share/doc/kebab-community/APKBUILD.reference')
    write('/usr/share/doc/kebab-community/SOURCE',f'https://github.com/jfbobier/Oneplus-8T-kebab-pmos-bringup\nCommit: {actual}\nDevice APKBUILD declares MIT; repository includes GPL-2.0. Original file headers retained.\n')
    # Retain pinned originals; apply the bounded Debian/BlueZ adaptation locally.
    adaptations = []
    for name, target, mode in [
        ('kebab-bluetooth-addr.sh', '/usr/local/libexec/kebab-bluetooth-addr', 0o755),
        ('kebab-bluetooth-addr.conf', '/etc/systemd/system/kebab-bluetooth-addr@.service.d/10-debian-bounded.conf', 0o644),
        ('test-gpu-headless.py', '/usr/local/libexec/kebab-test-gpu-headless.py', 0o755),
    ]:
        text = (project/'scripts/linux'/name).read_text()
        write(target, text, mode)
        adaptations.append({'source': name, 'destination': target,
                            'sha256': hashlib.sha256(text.encode()).hexdigest()})
    subprocess.run(['sh', '-n', str(root/'usr/local/libexec/kebab-bluetooth-addr')], check=True)
    # Keep the original RAM gadget script intact. A persistent initramfs can
    # leave an already bound gadget; the server service adopts that instance.
    write('/usr/local/libexec/kebab-usb-gadget',(project/'scripts/linux/usb-gadget.sh').read_text(),0o755)
    write('/usr/local/sbin/kebab-server-usb', '''#!/bin/sh
set -eu
g=/sys/kernel/config/usb_gadget/kebab
if [ -d "$g" ]; then
    [ -n "$(cat "$g/UDC")" ] || { echo 'Existing gadget is not bound; inspect it.' >&2; exit 1; }
    [ "$(cat "$g/idVendor")" = '0x1d6b' ] || exit 1
    [ -d "$g/functions/rndis.usb0" ] && [ -d "$g/functions/acm.usb0" ] || exit 1
    ip link set usb0 up
    ip address replace 192.168.8.1/24 dev usb0
else
    exec /usr/local/libexec/kebab-usb-gadget rndis
fi
''',0o755)
    write('/etc/systemd/system/kebab-usb-gadget.service','''[Unit]
Description=KB2000 USB server network and serial
After=systemd-modules-load.service systemd-udevd.service
Before=ssh.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/kebab-server-usb
RemainAfterExit=yes
TimeoutStartSec=90
[Install]
WantedBy=multi-user.target
''')
    write('/etc/systemd/network/10-kebab-usb.network','''[Match]
Name=usb0
[Link]
RequiredForOnline=no
[Network]
Address=192.168.8.1/24
DHCP=no
DHCPServer=yes
LinkLocalAddressing=no
ConfigureWithoutCarrier=yes
KeepConfiguration=static
[DHCPServer]
PoolOffset=2
PoolSize=1
EmitRouter=no
EmitDNS=no
EmitNTP=no
''')
    write('/etc/systemd/network/05-kebab-usb.link','''[Match]
MACAddress=02:00:00:00:08:01
[Link]
NamePolicy=
Name=usb0
''')
    write('/etc/systemd/network/05-kebab-wifi.link','''[Match]
OriginalName=wl*
[Link]
NamePolicy=
Name=wlan0
''')
    write('/etc/systemd/network/20-kebab-wifi.network','''[Match]
Name=wlan0
[Link]
RequiredForOnline=no
[Network]
DHCP=yes
[DHCPv4]
RouteMetric=600
UseDNS=yes
''')
    # Credentials are provisioned privately after USB SSH works.
    write('/etc/wpa_supplicant/wpa_supplicant-wlan0.conf.example','''ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev
update_config=0
# Add your network privately, then save as wpa_supplicant-wlan0.conf (0600).
''',0o600)
    write('/etc/systemd/system/wpa_supplicant@wlan0.service.d/credentials.conf','''[Unit]
ConditionPathExists=/etc/wpa_supplicant/wpa_supplicant-wlan0.conf
''')
    write('/etc/modprobe.d/90-kebab-server.conf','''# Server baseline: no unsolicited cellular firmware/NV activity or amp playback.
blacklist mhi_pci_generic
blacklist snd_soc_tfa2
''')
    write('/etc/systemd/journald.conf.d/90-kebab-server.conf','''[Journal]
Storage=persistent
SystemMaxUse=64M
RuntimeMaxUse=16M
''')
    write('/etc/systemd/logind.conf.d/90-kebab-server.conf','''[Login]
# A phone's short power-key press should not shut down a headless server.
HandlePowerKey=ignore
''')
    write('/etc/ssh/sshd_config.d/90-kebab.conf','''PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
AllowUsers debian
''')
    # apt-generated host identities must not become shared image identities.
    for key in (root/'etc/ssh').glob('ssh_host_*'):
        key.unlink()
    write('/etc/machine-id','')
    dbus_id=root/'var/lib/dbus/machine-id'
    dbus_id.parent.mkdir(parents=True,exist_ok=True)
    if dbus_id.exists() or dbus_id.is_symlink():
        dbus_id.unlink()
    dbus_id.symlink_to('/etc/machine-id')
    for binary in ('kebab-server-usb','kebab-bluetooth-addr','kebab-dp-mode'):
        file = root/('usr/local/sbin/' if binary=='kebab-server-usb' else 'usr/bin/')/binary
        subprocess.run(['sh','-n',str(file)],check=True)
    for unit in ('systemd-networkd.service','systemd-resolved.service','systemd-timesyncd.service','wpa_supplicant@wlan0.service','qrtr-ns.service'):
        # systemd-resolved is optional: networkd alone does not manage resolv.conf.
        if unit=='systemd-resolved.service' and not (root/'usr/lib/systemd/system'/unit).exists():
            continue
        subprocess.run(['chroot',str(root),'systemctl','enable',unit],check=True)
    subprocess.run(['chroot',str(root),'systemctl','disable','systemd-networkd-wait-online.service'],check=True)
    subprocess.run(['chroot',str(root),'systemctl','set-default','multi-user.target'],check=True)
    result = {'bringup_commit':actual,'upstream_files':upstream,'local_adaptations':adaptations,'network':'USB 192.168.8.1/24; DHCP offers host .2 without gateway/DNS; Wi-Fi DHCP after private credential provisioning',
              'alsa_ucm_integrated':True,'audio_autoload_disabled':True,'modem_autoload_disabled':True,
              'qrtr_tools_installed':True,'kernel_pd_mapper_enabled':True,'modem_nv_services_enabled':False,
              'hardware_boot_verified':False,'phone_deployment_executed':False}
    (build/'integration.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
