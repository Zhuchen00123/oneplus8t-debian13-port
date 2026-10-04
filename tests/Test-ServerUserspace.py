#!/usr/bin/env python3
"""Run real ARM64 OpenSSH under user emulation; no phone or hardware claims."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rootfs',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True)
    a=p.parse_args(); root=a.rootfs.resolve()
    if not (root/'etc/debian_version').is_file() or not root.as_posix().startswith('/home/'):
        raise ValueError('Expected a build rootfs')
    if os.readlink('/proc/self/ns/mnt')==os.readlink('/proc/1/ns/mnt'):
        raise ValueError('Run under unshare --mount --propagation private')
    subprocess.run(['mount','-t','proc','proc',str(root/'proc')],check=True)
    qa=root/'run/kebab-qa'; qa.mkdir()
    (root/'run/sshd').mkdir(exist_ok=True)
    original=root/'home/debian/.ssh/authorized_keys'; saved=original.read_bytes()
    server=None
    results={'arm64_userspace_executed':False,'correct_public_key_login':False,
             'wrong_public_key_rejected':False,'password_authentication_disabled':False,
             'pty_tested':False,'hardware_boot_verified':False}
    try:
        for name in ('host','client','wrong'):
            subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(qa/name)],check=True)
        original.write_bytes((qa/'client.pub').read_bytes())
        with socket.socket() as s:s.bind(('127.0.0.1',0)); port=s.getsockname()[1]
        options=['-p',str(port),'-h','/run/kebab-qa/host','-o','ListenAddress=127.0.0.1',
                 '-o','PidFile=/run/kebab-qa/sshd.pid']
        config=subprocess.check_output(['chroot',str(root),'/usr/sbin/sshd','-T']+options,text=True)
        assert 'passwordauthentication no' in config and 'permitrootlogin no' in config
        results['password_authentication_disabled']=True
        log=(qa/'server.log').open('w')
        server=subprocess.Popen(['chroot',str(root),'/usr/sbin/sshd','-D','-e']+options,stdout=log,stderr=log)
        for _ in range(50):
            if server.poll() is not None:raise RuntimeError('ARM64 sshd exited: '+(qa/'server.log').read_text())
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.2):break
            except OSError:time.sleep(.1)
        common=['ssh','-F','/dev/null','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
                '-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null',
                '-o','ConnectTimeout=10','-p',str(port)]
        accepted=subprocess.run(common+['-i',str(qa/'client'),'debian@127.0.0.1',
            'printf "QA-ARM64-OK\\n"; dpkg --print-architecture'],text=True,capture_output=True,timeout=30)
        if accepted.returncode or accepted.stdout!='QA-ARM64-OK\narm64\n':
            raise RuntimeError('Public-key SSH failed: '+accepted.stderr+(qa/'server.log').read_text())
        results['arm64_userspace_executed']=results['correct_public_key_login']=True
        rejected=subprocess.run(common+['-i',str(qa/'wrong'),'debian@127.0.0.1','true'],text=True,capture_output=True,timeout=30)
        assert rejected.returncode==255 and 'Permission denied' in rejected.stderr
        results['wrong_public_key_rejected']=True
        a.report.write_text(json.dumps(results,indent=2)+'\n')
        print(json.dumps(results,indent=2))
    finally:
        if server is not None:
            server.terminate()
            try:server.wait(timeout=10)
            except subprocess.TimeoutExpired:server.kill();server.wait()
        original.write_bytes(saved)
        shutil.rmtree(qa)
        subprocess.run(['umount',str(root/'proc')],check=True)


if __name__=='__main__':main()
