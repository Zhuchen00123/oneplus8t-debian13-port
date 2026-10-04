#!/usr/bin/env python3
"""Exercise ARM64 rescue binaries/auth over loopback under QEMU, not on a phone."""
import argparse
import atexit
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rescue-root', type=Path, required=True)
    parser.add_argument('--private-key', type=Path, required=True)
    parser.add_argument('--kernelrelease', required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--probe-pty', action='store_true', help='Additional strict interactive PTY probe; QEMU/chroot is not full system emulation')
    args = parser.parse_args()
    if not args.private_key.read_bytes().startswith(b'-----BEGIN OPENSSH PRIVATE KEY-----'):
        raise ValueError('Expected a readable local QA private key; its content is never printed')
    if os.geteuid() != 0 or args.work.exists():
        raise ValueError('Run as Linux host root with a fresh QA directory')
    args.work.mkdir(parents=True)
    tree = args.work/'root'
    subprocess.run(['cp', '-a', '--', str(args.rescue_root), str(tree)], check=True)
    for name, minor in (('random', 8), ('urandom', 9)):
        os.mknod(tree/'dev'/name, stat.S_IFCHR | 0o666, os.makedev(1, minor))
    os.mknod(tree/'dev/tty', stat.S_IFCHR | 0o666, os.makedev(5, 0))
    pts = tree/'dev/pts'
    pts.mkdir()
    proc = tree/'proc'
    def unmount_if_needed(path):
        if subprocess.run(['mountpoint', '-q', str(path)]).returncode == 0:
            subprocess.run(['umount', str(path)], check=True)
    subprocess.run(['mount', '-t', 'proc', 'proc', str(proc)], check=True)
    atexit.register(unmount_if_needed, proc)
    # Match /init's mount options. A separate newinstance is not representative
    # of the phone's initial namespace and breaks QEMU's ttyname translation.
    subprocess.run(['mount', '-t', 'devpts', '-o', 'ptmxmode=0666,mode=0620,gid=5', 'devpts', str(pts)], check=True)
    atexit.register(unmount_if_needed, pts)
    (tree/'dev/ptmx').symlink_to('pts/ptmx')
    local_key = args.work/'client-key'
    shutil.copyfile(args.private_key, local_key)
    local_key.chmod(0o600)
    atexit.register(local_key.unlink, missing_ok=True)
    prefix = ['chroot', str(tree)]
    def guest(*cmd):
        return subprocess.check_output(prefix+list(cmd), text=True, stderr=subprocess.STDOUT, timeout=30)
    if guest('/bin/uname', '-m').strip() != 'aarch64':
        raise ValueError('Rescue userspace is not executing as ARM64')
    for script in ('/init', '/sbin/kebab-usb-gadget'):
        guest('/bin/sh', '-n', script)
    # QEMU user mode reports the host kernelrelease. Alias only this QA copy;
    # the candidate itself keeps exclusively its real 7.2.0 module directory.
    emulated_release = guest('/bin/uname', '-r').strip()
    if emulated_release != args.kernelrelease:
        (tree/'lib/modules'/emulated_release).symlink_to(args.kernelrelease)
    dependency_probe = guest('/sbin/modprobe', '-D', '-a',
                             'phy_qcom_qusb2', 'libcomposite', 'usb_f_acm', 'usb_f_rndis', 'usb_f_ecm', 'usb_f_ncm')
    (args.work/'module-dry-run.txt').write_text(dependency_probe)
    guest('/usr/bin/dropbearkey', '-t', 'ed25519', '-f', '/tmp/test-host-key')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    server_log = (args.work/'dropbear-test.log').open('w')
    server = subprocess.Popen(prefix+['/usr/sbin/dropbear', '-F', '-E', '-s', '-j', '-k',
                                     '-r', '/tmp/test-host-key', '-p', f'127.0.0.1:{port}'],
                              stdin=subprocess.DEVNULL, stdout=server_log, stderr=server_log)
    try:
        for _ in range(100):
            if server.poll() is not None:
                raise ValueError('Dropbear test server exited; see local log')
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise TimeoutError('Dropbear test server did not start')
        ssh = ['ssh', '-F', '/dev/null', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
               '-o', 'StrictHostKeyChecking=accept-new', '-o', 'UserKnownHostsFile='+str(args.work/'known-hosts'),
               '-o', 'ConnectTimeout=10', '-p', str(port)]
        command = 'uname -m; id -u; cat /etc/os-release'
        good = subprocess.run(ssh+['-i', str(local_key), 'root@127.0.0.1', command], capture_output=True, text=True, timeout=30)
        (args.work/'authorized-ssh.txt').write_text(good.stdout+good.stderr)
        if good.returncode or not good.stdout.startswith('aarch64\n0\n'):
            raise ValueError('Authorized key SSH failed; see local QA log')
        if args.probe_pty:
            pty = subprocess.run(ssh+['-tt', '-i', str(local_key), 'root@127.0.0.1', 'tty; uname -m'],
                                 stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
            (args.work/'interactive-ssh.txt').write_text(pty.stdout+pty.stderr)
            if pty.returncode or not pty.stdout.startswith('/dev/pts/') or 'aarch64' not in pty.stdout:
                raise ValueError('Interactive SSH PTY probe failed; see log, do not claim PTY support')
        wrong_key = args.work/'wrong-key'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(wrong_key)], check=True)
        wrong = subprocess.run(ssh+['-i', str(wrong_key), 'root@127.0.0.1', 'true'], capture_output=True, text=True, timeout=30)
        if wrong.returncode != 255 or 'Permission denied (publickey)' not in wrong.stderr:
            raise ValueError('Wrong public key was not rejected as expected')
        password = subprocess.run(ssh+['-o', 'PubkeyAuthentication=no', '-o', 'PreferredAuthentications=password',
                                       'root@127.0.0.1', 'true'], capture_output=True, text=True, timeout=30)
        if password.returncode != 255 or 'Permission denied (publickey)' not in password.stderr:
            raise ValueError('Password authentication was not disabled')
        result = {'arm64_binaries_execute_under_qemu': True, 'runtime_shell_syntax': True,
                  'busybox_module_dependency_dry_run': True, 'authorized_key_ssh': True,
                  'wrong_key_rejected': True, 'password_authentication_disabled': True,
                  'interactive_ssh_pty': args.probe_pty,
                  'interactive_pty_requires_device_test': not args.probe_pty,
                  'usb_hardware_enumeration_tested': False, 'kernel_boot_tested': False,
                  'scope': 'Host loopback QEMU userspace only; no phone access'}
        (args.work/'qa-result.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result, indent=2))
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
        server_log.close()
        subprocess.run(['umount', str(pts)], check=True)
        subprocess.run(['umount', str(proc)], check=True)
        local_key.unlink()
        for suffix in ('', '.pub'):
            key_file = args.work/('wrong-key'+suffix)
            if key_file.exists():
                key_file.unlink()


if __name__ == '__main__':
    main()
