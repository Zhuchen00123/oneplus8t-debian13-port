#!/usr/bin/env python3
"""Experiment-only adapter for packaged Waydroid 1.6.3. No dpkg file edits.
Existing launcher remains masked; invoke this adapter for BOTH init and container.
The adapter narrows mounts/permissions and substitutes the owned network helper."""
import hashlib
import os
from pathlib import Path
import runpy
import stat
import sys
import time

ROOT = Path('/srv/waydroid-r4-lab-20261112')
UPSTREAM = Path('/usr/lib/waydroid')
sys.path.insert(0, str(UPSTREAM))
import tools.helpers.lxc as lxc
import tools.helpers.run as runner
import tools.actions.container_manager as manager
import tools.services.hardware_manager as hardware
import tools.config

if tools.config.version != '1.6.3':
    raise RuntimeError('Re-audit adapter against installed Waydroid before changing versions')
# Local, checksum-verified images; no online initializer or global source edits.
tools.config.defaults['preinstalled_images_paths'] = [str(ROOT / 'images')]

ALLOW = {'/dev/zero', '/dev/null', '/dev/full', '/dev/tty', '/dev/binder',
         '/dev/hwbinder', '/dev/vndbinder', '/dev/dri/renderD128'}
ALLOW.update(str(p) for p in Path('/dev/dma_heap').glob('*') if p.is_char_device())
original_nodes = lxc.generate_nodes_lxc_config

def nodes(args):
    result = []
    for line in original_nodes(args):
        fields = line.split(' = ', 1)[1].split()
        source, target = fields[:2]
        if source in ALLOW or source in {'tmpfs', 'none'} or target == 'vendor/etc/host-permissions':
            result.append(line)
    return result

lxc.generate_nodes_lxc_config = nodes
original_config = lxc.set_lxc_config

def config(args):
    original_config(args)
    import tools.config
    path = Path(tools.config.defaults['lxc']) / 'waydroid/config'
    text = path.read_text().replace('sys:rw', 'sys:ro').replace('sys:ro proc\n', 'sys:ro proc:mixed\n')
    text = text.replace('lxc.hook.post-stop = /dev/null', 'lxc.hook.post-stop = /usr/bin/true')
    text = text.replace(' sys_time ', ' ').replace(' syslog ', ' ').replace(' audit_control ', ' ')
    text += '\nlxc.cgroup.relative = 1\n'
    text += 'lxc.cgroup2.memory.max = 3221225472\nlxc.cgroup2.memory.swap.max = 0\n'
    text += 'lxc.cgroup2.cpu.max = 200000 100000\nlxc.cgroup2.pids.max = 2048\n'
    override=ROOT/'overrides/logd.rc'
    if not override.is_file():
        raise RuntimeError('install the audited userspace-only Android logd override before initialization')
    text += 'lxc.mount.entry = '+str(override)+' system/etc/init/logd.rc none bind,ro 0 0\n'
    text += '# Lab exact device whitelist; no camera, sound, modem, input, debugfs.\n'
    text += 'lxc.cgroup2.devices.deny = a\n'
    allowed = {'c 1:11 m', 'c 1:3 rwm', 'c 1:5 rwm', 'c 1:7 rwm', 'c 1:8 rwm',
               'c 1:9 rwm', 'c 5:0 rwm', 'c 5:1 rwm', 'c 5:2 rwm', 'c 136:* rwm'}
    for name in ALLOW:
        if Path(name).exists():
            s = os.stat(name)
            if stat.S_ISCHR(s.st_mode):
                allowed.add(f'c {os.major(s.st_rdev)}:{os.minor(s.st_rdev)} rwm')
    text += ''.join('lxc.cgroup2.devices.allow = ' + item + '\n' for item in sorted(allowed))
    path.write_text(text)

lxc.set_lxc_config = config
original_session = lxc.generate_session_lxc_config

def session(args, data):
    original_session(args, data)
    import tools.config
    path = Path(tools.config.defaults['lxc']) / 'waydroid/config_session'
    # Exclude host Pulse socket even when the host audio stack is running.
    lines = [line for line in path.read_text().splitlines() if '/pulse/' not in line]
    path.write_text('\n'.join(lines) + '\n')
    # First-stage init insists on creating its own kmsg inodes (EEXIST is fatal).
    # Permit mknod only for 1:11; read/write stay denied by the cgroup whitelist.
    # Do not bind host kmsg, and remove only our previous diagnostic log aliases.
    node_path=Path(tools.config.defaults['lxc'])/'waydroid/config_nodes'
    keep=[]
    for line in node_path.read_text().splitlines():
        fields=line.split(' = ',1)[-1].split()
        if len(fields)>1 and fields[1] in ['dev/kmsg','dev/kmsg_debug']:
            continue
        keep.append(line)
    node_path.write_text('\n'.join(keep)+'\n')

lxc.generate_session_lxc_config = session
original_run = runner.user

def user(args, command, *a, **kw):
    if command and command[0] == 'lxc-start':
        stamp=str(time.time_ns())
        command = [command[0], '--logpriority=DEBUG', '--logfile=' + str(ROOT / 'logs' / ('lxc-' + stamp + '.log')), *command[1:]]
        # A scope contains only the LXC monitor/payload, not the DBus manager.
        # Once LXC moves its monitor, the scope parent is empty and cgroup v2
        # domain controllers can be delegated without EBUSY or escaping limits.
        command=['systemd-run','--scope','--quiet','--collect','--unit=kebab-waydroid-r4-payload',
                 '--property=Delegate=yes','--property=CPUQuota=200%',
                 '--property=MemoryHigh=2500M','--property=MemoryMax=3G',
                 '--property=MemorySwapMax=0','--property=TasksMax=2048',
                 '--property=RuntimeMaxSec=1800',*command]
        # Keep an optional tracer outside the delegated scope; otherwise the
        # tracer itself would leave a process in the domain-controller parent.
        if os.environ.get('O8T_LAB_TRACE')=='1':
            tracer=ROOT/'tools/strace/usr/bin/strace'
            if not tracer.is_file():
                raise RuntimeError('explicit init trace requested but verified tracer missing')
            command=[str(tracer),'-ff','-s','240','-o',str(ROOT/'logs'/('init-trace-'+stamp)),'-e','trace=process,openat,write,writev,mknodat,mount,prctl,ioctl',*command]
    if command and str(command[0]).endswith('/waydroid-net.sh'):
        command = ['/usr/bin/python3', str(ROOT / 'bin/waydroid_lab_network.py'), *command[1:]]
    if command[:2] in (['systemctl', 'start'], ['systemctl', 'stop']) and 'nfcd' in command:
        raise RuntimeError('Lab forbids altering host NFC services')
    return original_run(args, command, *a, **kw)

runner.user = user

def permissions(args, perm_list=None, mode='777'):
    # Never recursively chmod host /dev/video*, framebuffer, or all render nodes.
    selected = perm_list if perm_list is not None else sorted(ALLOW)
    for name in selected:
        if name not in ALLOW:
            raise RuntimeError('Permission target outside exact allowlist: ' + name)
        if Path(name).exists():
            os.chmod(name, int('666' if mode == '777' else mode, 8))

manager.set_permissions = permissions
# No host sensors, geoclue/GNSS, clipboard, NFC or hardware bridge in this phase.
hardware.start = lambda args: None
hardware.stop = lambda args: None
runpy.run_path('/usr/bin/waydroid', run_name='__main__')
