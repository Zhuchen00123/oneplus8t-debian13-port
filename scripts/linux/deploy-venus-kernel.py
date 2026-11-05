#!/usr/bin/env python3
"""Guarded persistent r3 -> r4 boot_a install. Default is read-only preflight."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile

try:  # Keep the validation helpers importable by Windows-hosted unit tests.
    import fcntl
except ImportError:  # pragma: no cover - Linux deployment requires fcntl.
    fcntl = None


R3 = '7.2.0-kebab-debian13-r3-slider+'
R4 = '7.2.0-kebab-debian13-r4-venus+'
ROOT_UUID = 'bc52fbbe-777b-47c8-84fc-ba56db7a8813'
BOOT_BYTES = 100663296
BOOT_SECTORS = 196608
ORIGINAL_BOOT_SHA256 = '4fab2d9152aa78b41b884e913afb53c9605b5bb5e2ecb821f7e72d20d2603dfa'
ORIGINAL_DTBO_A_SHA256 = '99af1264aa5f1a2bdcff12b4e4d5ec5ff0a04c86b88a207bdcd28429b79b21b0'
ORIGINAL_HELPER_SHA256 = '47782d153bad9905c93dd98747ce8cece8d75b5fa3029b862d40694899edfaf9'
CHARGE_SCRIPT_SHA256 = 'baa773c519b1f45b6bdb6f3165abec774b1265fcee64ccf95d6b4a8f445f15d3'
GUARD_LIBRARY_SHA256 = '23e288be707345a087da6311bc9d8df68d3078a914086992db4301e8291a041c'
BUNDLE_MANIFEST_SHA256 = '5740e2cc002ee0078a72cab78428ff45558183a701cd3424064dd02385e16bcd'
BOOT_IMAGE_SHA256 = '8d409dfd6645d81e03fa4adc4006a4d64f8afb7200bb385b250b0e4661e30bed'
MODULE_ARCHIVE_SHA256 = '918e416258ad23470156d3b54418ad27b8821289006e83fb6f5ef49d2930b1cb'
SLIDER_HELPER_SHA256 = 'ea7a25bb9bc9c64d8703bf9ff45074c8b1096cc05685cfca81044ad63be74ee8'
OEM_MANIFEST_SHA256 = 'f5eb50d8924392142cc2e87896ef62c94400a4f265855a5048f87005db8ee82b'
OEM_ARCHIVE_SHA256 = 'db7d97ec87643e72ff6e0032fb6c4b7b7956f5f17a38e54bd79445079cfaf406'
OEM_BUILD_TAG = 'KB2000_14.0.0.602(CN01)'
MODULE_COUNT = 583

DEPLOYMENT_ROOT = Path('/srv/kebab-r4-deploy-20261105')
DEFAULT_BACKUP = DEPLOYMENT_ROOT / 'backup'
DEFAULT_GUARD = DEPLOYMENT_ROOT / 'boot-guards.py'
DEFAULT_FIRMWARE_MANIFEST = Path('/etc/kebab/venus-oem-20261105.json')
OEM_FIRMWARE_DIR = Path('/usr/lib/firmware/qcom/sm8250/oneplus/kebab/oem-14.0.0.602')
FIRMWARE_ROOT = Path('/usr/lib/firmware')
MODULE_ROOT = Path('/usr/lib/modules') / R4
HELPER_PATH = Path('/usr/local/sbin/kebab-alert-slider')
CHARGE_PATH = Path('/usr/local/sbin/kebab-charge-policy')
CHARGE_STATUS = Path('/run/kebab-charge-policy/status.json')
LOCK_PATH = Path('/run/lock/kebab-kernel-update.lock')

OEM_NAMES = {'venus.mdt', 'venus.b19', *(f'venus.b{i:02d}' for i in range(11))}
REQUIRED_BUNDLE_FILES = {
    'boot-venus.img': BOOT_IMAGE_SHA256,
    'modules.tar.gz': MODULE_ARCHIVE_SHA256,
    'rootfs-prep/kebab-alert-slider': SLIDER_HELPER_SHA256,
}


def digest(path, length=None):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        left = length
        while left is None or left:
            block = stream.read(4 * 1024 * 1024 if left is None else min(left, 4 * 1024 * 1024))
            if not block:
                if left:
                    raise ValueError('Short read: ' + str(path))
                break
            h.update(block)
            if left is not None:
                left -= len(block)
    return h.hexdigest()


def digest_stream(stream):
    h = hashlib.sha256()
    for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
        h.update(block)
    return h.hexdigest()


def run(*args):
    return subprocess.check_output(args, text=True, timeout=30).strip()


def require_plain_root_input(path, directory=False, private_directory=False):
    """Reject links, non-root owners and group/other-writable staged inputs."""
    path = Path(path)
    try:
        info = path.lstat()
    except OSError as exc:
        raise ValueError('Missing input: ' + str(path)) from exc
    if stat.S_ISLNK(info.st_mode):
        raise ValueError('Symlink input rejected: ' + str(path))
    expected_kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_kind(info.st_mode):
        raise ValueError('Unexpected input type: ' + str(path))
    if hasattr(info, 'st_uid') and info.st_uid != 0:
        raise ValueError('Input is not root-owned: ' + str(path))
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o022:
        raise ValueError('Group/other-writable input rejected: ' + str(path))
    if directory and private_directory and mode & 0o077:
        raise ValueError('Bundle directory must be root-private (0700): ' + str(path))
    parent = path.parent
    while True:
        try:
            parent_info = parent.lstat()
        except OSError as exc:
            raise ValueError('Unsafe input parent: ' + str(parent)) from exc
        if not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode):
            raise ValueError('Input parent is not a real directory: ' + str(parent))
        if (hasattr(parent_info, 'st_uid') and parent_info.st_uid != 0) or \
                stat.S_IMODE(parent_info.st_mode) & 0o022:
            raise ValueError('Input parent is not root-owned and protected: ' + str(parent))
        if parent.parent == parent:
            break
        parent = parent.parent
    return path


def secure_child(base, relative, directory=False):
    rel = PurePosixPath(relative)
    if (not relative or relative.startswith('/') or '\\' in relative or
            any(part in ('', '.', '..') for part in rel.parts) or rel.as_posix() != relative):
        raise ValueError('Unsafe bundle path: ' + relative)
    path = base.joinpath(*rel.parts)
    # Check every component below the root so a replaced directory cannot redirect a source.
    current = base
    for part in rel.parts[:-1]:
        current = current / part
        require_plain_root_input(current, directory=True)
    return require_plain_root_input(path, directory=directory)


def validate_bundle_metadata(manifest):
    if not isinstance(manifest, dict):
        raise ValueError('Invalid bundle manifest')
    if (manifest.get('kernelrelease') != R4 or manifest.get('root_uuid') != ROOT_UUID or
            manifest.get('current_boot_backup_sha256') != ORIGINAL_BOOT_SHA256 or
            manifest.get('boot_policy_unchanged') is not True or
            manifest.get('boot_partition_limit_bytes') != BOOT_BYTES or
            manifest.get('modules_count') != MODULE_COUNT or
            manifest.get('modules_sha256_verified') is not True or
            manifest.get('modules_vermagic_verified') is not True or
            manifest.get('rootfs_prep_payloads_deployed') is not False):
        raise ValueError('Bundle identity or boot policy does not match reviewed r4')
    hashes = manifest.get('files_sha256')
    if not isinstance(hashes, dict):
        raise ValueError('Bundle file hash map is missing')
    for name, expected in REQUIRED_BUNDLE_FILES.items():
        if hashes.get(name) != expected:
            raise ValueError('Reviewed bundle hash mismatch: ' + name)
    return manifest


def validate_bundle(bundle):
    base = Path(bundle).absolute()
    require_plain_root_input(base, directory=True, private_directory=True)
    resolved = base.resolve(strict=True)
    if resolved != base:
        raise ValueError('Bundle path resolves through a symlink')
    manifest_path = secure_child(base, 'venus-bundle-manifest.json')
    if digest(manifest_path) != BUNDLE_MANIFEST_SHA256:
        raise ValueError('Venus bundle manifest SHA-256 mismatch')
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('Cannot read Venus bundle manifest') from exc
    validate_bundle_metadata(manifest)
    paths = {}
    for name, expected in REQUIRED_BUNDLE_FILES.items():
        path = secure_child(base, name)
        if digest(path) != expected:
            raise ValueError('Bundle member SHA-256 mismatch: ' + name)
        paths[name] = path
    image = paths['boot-venus.img']
    if image.stat().st_size != 47869952 or image.stat().st_size > BOOT_BYTES:
        raise ValueError('Candidate boot image size differs from the reviewed image')
    return {'base': base, 'manifest_path': manifest_path, 'manifest': manifest,
            'image': image, 'modules_archive': paths['modules.tar.gz'],
            'new_helper': paths['rootfs-prep/kebab-alert-slider']}


def validate_firmware_metadata(manifest):
    if not isinstance(manifest, dict) or manifest.get('schema_version') != 1:
        raise ValueError('Invalid OEM firmware manifest schema')
    source = manifest.get('source', {})
    extraction = manifest.get('extraction', {})
    files = manifest.get('files')
    if (not isinstance(source, dict) or not isinstance(extraction, dict) or
            source.get('device_build_tag') != OEM_BUILD_TAG or
            source.get('archive_sha256') != OEM_ARCHIVE_SHA256 or
            extraction.get('member_count') != 13 or
            extraction.get('selected_only') is not True or
            extraction.get('byte_preserving') is not True or
            extraction.get('firmware_repack_or_signature_change') is not False or
            not isinstance(files, dict) or set(files) != OEM_NAMES):
        raise ValueError('OEM manifest is not the measured KB2000 14.0.0.602 set')
    for name, identity in files.items():
        if (not isinstance(identity, dict) or identity.get('type') != 'regular' or
                identity.get('output') != name or identity.get('archive_member') !=
                'vendor/firmware_mnt/image/' + name or
                not isinstance(identity.get('bytes'), int) or isinstance(identity.get('bytes'), bool) or
                identity['bytes'] <= 0 or not isinstance(identity.get('sha256'), str) or
                len(identity['sha256']) != 64 or
                any(char not in '0123456789abcdef' for char in identity['sha256'])):
            raise ValueError('Invalid OEM firmware identity: ' + str(name))
    return files


def validate_installed_firmware(manifest_path, firmware_root=OEM_FIRMWARE_DIR):
    manifest_path = require_plain_root_input(manifest_path)
    if digest(manifest_path) != OEM_MANIFEST_SHA256:
        raise ValueError('OEM firmware manifest SHA-256 mismatch')
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('Cannot read OEM firmware manifest') from exc
    files = validate_firmware_metadata(manifest)
    firmware_root = Path(firmware_root)
    require_plain_root_input(firmware_root, directory=True)
    expected_links = {}
    for name, identity in files.items():
        target = secure_child(firmware_root, name)
        if (target.stat().st_size != identity['bytes'] or
                digest(target) != identity['sha256']):
            raise ValueError('Installed OEM firmware bytes differ: ' + name)
        expected_links[name] = '../qcom/sm8250/oneplus/kebab/oem-14.0.0.602/' + name

    alias_dir = FIRMWARE_ROOT / 'postmarketos'
    require_plain_root_input(alias_dir, directory=True)
    names = set(files) - {'venus.mdt'}
    aliases = {'venus.mbn': 'venus.mdt', **{name: name for name in names if name != 'venus.mdt'}}
    for alias_name, source_name in aliases.items():
        alias = alias_dir / alias_name
        try:
            link = os.readlink(alias)
        except OSError as exc:
            raise ValueError('Missing OEM loader alias: ' + str(alias)) from exc
        if link != expected_links[source_name]:
            raise ValueError('OEM loader alias target mismatch: ' + alias_name)
    return {'manifest_path': manifest_path, 'manifest_sha256': OEM_MANIFEST_SHA256,
            'files': files, 'aliases': aliases}


def module_archive_members(archive_path):
    members_by_name = {}
    modules = {}
    count = 0
    total = 0
    with tarfile.open(archive_path, 'r:gz') as archive:
        members = archive.getmembers()
        if len(members) > 50000:
            raise ValueError('Module archive has too many members')
        for item in members:
            name = item.name.rstrip('/') if item.isdir() else item.name
            parts = PurePosixPath(name).parts
            if (not name or name.startswith('/') or '\\' in name or '..' in parts or
                    parts[:3] != ('lib', 'modules', R4) or name in members_by_name or
                    not (item.isfile() or item.isdir())):
                raise ValueError('Unsafe module archive member: ' + item.name)
            members_by_name[name] = item
            total += item.size
            if total > 2 * 1024**3:
                raise ValueError('Module archive expands beyond limit')
            if item.isfile() and name.endswith(('.ko', '.ko.zst')):
                count += 1
                if count > MODULE_COUNT:
                    raise ValueError('Module archive contains too many modules')
                stream = archive.extractfile(item)
                if stream is None:
                    raise ValueError('Unreadable module archive member: ' + name)
                with stream:
                    modules[name[len('lib/modules/' + R4) + 1:]] = digest_stream(stream)
        if count != MODULE_COUNT:
            raise ValueError('Module archive count mismatch: ' + str(count))
    return modules


def verify_installed_modules(archive_path, module_root=MODULE_ROOT):
    if digest(archive_path) != MODULE_ARCHIVE_SHA256:
        raise ValueError('Module archive SHA-256 mismatch')
    archive_modules = module_archive_members(archive_path)
    module_root = Path(module_root)
    require_plain_root_input(module_root, directory=True)
    installed = {}
    for path in module_root.rglob('*'):
        if path.name.endswith(('.ko', '.ko.zst')):
            if path.is_symlink() or not path.is_file():
                raise ValueError('Unsafe installed module path: ' + str(path))
            relative = path.relative_to(module_root).as_posix()
            installed[relative] = path
    if set(installed) != set(archive_modules):
        raise ValueError('Installed module path/count differs from the 583-member bundle')
    for relative, expected in archive_modules.items():
        path = installed[relative]
        current = path.parent
        while current != module_root:
            if current.is_symlink():
                raise ValueError('Installed module traverses a symlink: ' + relative)
            current = current.parent
        require_plain_root_input(path)
        if digest(path) != expected:
            raise ValueError('Installed module SHA-256 mismatch: ' + relative)
    paths = [str(installed[key]) for key in sorted(installed)]
    result = subprocess.run(['modinfo', '-F', 'vermagic', *paths], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=120)
    vermagics = result.stdout.splitlines()
    if len(vermagics) != MODULE_COUNT or any(not value.startswith(R4 + ' ') for value in vermagics):
        raise ValueError('Installed module vermagic mismatch')
    return {'count': len(installed), 'archive_sha256': MODULE_ARCHIVE_SHA256,
            'vermagic_verified': True}


def boot_cmdline(path):
    with Path(path).open('rb') as stream:
        header = stream.read(1660)
    if len(header) < 1660 or header[:8] != b'ANDROID!':
        raise ValueError('Invalid Android boot image header: ' + str(path))
    return (header[64:576].split(b'\0', 1)[0] +
            header[608:1632].split(b'\0', 1)[0])


def require_charge_state():
    if digest(CHARGE_PATH) != CHARGE_SCRIPT_SHA256:
        raise ValueError('Installed charge-policy implementation is not the reviewed version')
    require_plain_root_input(CHARGE_STATUS)
    try:
        state = json.loads(CHARGE_STATUS.read_text(encoding='utf-8'))
        snapshot = state['snapshot']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError('Cannot verify live charge-policy state') from exc
    if (state.get('control_enabled') is not True or
            state.get('inhibit_readback_verified') is not True or
            snapshot.get('owned') != 1 or snapshot.get('last_error') != 0):
        raise ValueError('Charge policy is not actively holding a verified inhibit lease')
    return {'sha256': CHARGE_SCRIPT_SHA256, 'owned': 1, 'last_error': 0,
            'inhibit_readback_verified': True}


def require_slot_state(slots):
    try:
        a = slots['boot_a']
        b = slots['boot_b']
    except (KeyError, TypeError) as exc:
        raise ValueError('GPT boot slot data is incomplete') from exc
    if not (a.get('active') is True and a.get('successful') is True and
            a.get('unbootable') is False and b.get('active') is False):
        raise ValueError('Only successful, bootable slot A may be updated')
    return slots


def read_block(partlabel, expected=None):
    link = Path('/dev/disk/by-partlabel') / partlabel
    resolved = link.resolve(strict=True)
    if expected is not None and resolved != Path(expected):
        raise ValueError('Unexpected ' + partlabel + ' partition: ' + str(resolved))
    try:
        mode = resolved.stat().st_mode
    except OSError as exc:
        raise ValueError('Block path unavailable: ' + str(resolved)) from exc
    if not stat.S_ISBLK(mode):
        raise ValueError('Block path unavailable: ' + str(resolved))
    return resolved


def preflight(bundle, guard_library, firmware_manifest, backup):
    if run('id', '-u') != '0' or run('uname', '-m') != 'aarch64':
        raise ValueError('Run as root on the measured KB2000')
    if run('uname', '-r') != R3:
        raise ValueError('Persistent deployment must start from the verified r3 kernel')
    if run('findmnt', '-n', '-o', 'SOURCE', '/') != '/dev/sda15':
        raise ValueError('Root partition mismatch')
    if run('findmnt', '-n', '-o', 'UUID', '/') != ROOT_UUID:
        raise ValueError('Root filesystem UUID mismatch')

    bundle_info = validate_bundle(bundle)
    guard_library = require_plain_root_input(guard_library)
    if digest(guard_library) != GUARD_LIBRARY_SHA256:
        raise ValueError('Boot guard library SHA-256 mismatch')
    spec = importlib.util.spec_from_file_location('kebab_boot_guards', guard_library)
    if spec is None or spec.loader is None:
        raise ValueError('Cannot load the reviewed boot guard library')
    guards = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guards)

    slots = require_slot_state(guards.gpt_boot_slots())
    boot = read_block('boot_a', '/dev/sde11')
    if Path('/sys/class/block/sde11/size').read_text().strip() != str(BOOT_SECTORS):
        raise ValueError('Measured boot_a size mismatch')
    boot_b = read_block('boot_b')
    dtbo_a = read_block('dtbo_a')
    dtbo_b = read_block('dtbo_b')
    before_boot = digest(boot)
    if before_boot != ORIGINAL_BOOT_SHA256:
        raise ValueError('Current full boot_a differs from the reviewed r3 baseline')
    if digest(dtbo_a) != ORIGINAL_DTBO_A_SHA256:
        raise ValueError('Current dtbo_a differs from the reviewed baseline')
    if boot_cmdline(bundle_info['image']) != boot_cmdline(boot):
        raise ValueError('Candidate boot command line differs from the current boot partition')

    current_helper = require_plain_root_input(HELPER_PATH)
    helper_info = current_helper.stat()
    if digest(current_helper) != ORIGINAL_HELPER_SHA256:
        raise ValueError('Current slider helper differs from the verified r3 copy')
    if stat.S_IMODE(helper_info.st_mode) != 0o755:
        raise ValueError('Current slider helper mode is not 0755')

    charge = require_charge_state()
    firmware = validate_installed_firmware(firmware_manifest)
    modules = verify_installed_modules(bundle_info['modules_archive'])
    backup = Path(backup)
    if backup != DEFAULT_BACKUP:
        raise ValueError('Recovery backup path must be ' + str(DEFAULT_BACKUP))
    if backup.exists() or backup.is_symlink():
        raise ValueError('The fixed recovery directory already exists; refusing overwrite')
    if shutil.disk_usage(DEPLOYMENT_ROOT.parent).free < BOOT_BYTES + 16 * 1024**2:
        raise ValueError('Insufficient free space for a full boot_a and helper backup')

    return {
        **bundle_info, 'guards': guards, 'guard_library': guard_library,
        'firmware': firmware, 'modules': modules, 'charge': charge,
        'boot': boot, 'boot_b': boot_b, 'dtbo_a': dtbo_a, 'dtbo_b': dtbo_b,
        'boot_before_sha256': before_boot,
        'boot_before_prefix_sha256': digest(boot, bundle_info['image'].stat().st_size),
        'boot_before_tail_sha256': digest_range(boot, bundle_info['image'].stat().st_size,
                                                BOOT_BYTES - bundle_info['image'].stat().st_size),
        'boot_b_before_sha256': digest(boot_b),
        'dtbo_a_before_sha256': digest(dtbo_a), 'dtbo_b_before_sha256': digest(dtbo_b),
        'slots_before': slots, 'helper': current_helper,
        'helper_before_sha256': ORIGINAL_HELPER_SHA256,
        'helper_before_mode': stat.S_IMODE(helper_info.st_mode),
        'firmware_manifest_sha256': OEM_MANIFEST_SHA256,
        'module_count': modules['count'], 'backup': backup,
    }


def digest_range(path, offset, length):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        stream.seek(offset)
        left = length
        while left:
            block = stream.read(min(left, 4 * 1024 * 1024))
            if not block:
                raise ValueError('Short read: ' + str(path))
            h.update(block)
            left -= len(block)
    return h.hexdigest()


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json_atomic(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.new')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps(value, indent=2, sort_keys=True) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        fsync_directory(path.parent)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def backup_file(source, destination, expected_sha, expected_bytes=None):
    source = Path(source)
    destination = Path(destination)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(destination, flags, 0o600)
    try:
        with source.open('rb') as incoming, os.fdopen(fd, 'wb') as outgoing:
            shutil.copyfileobj(incoming, outgoing, 4 * 1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        os.chmod(destination, 0o600)
    except BaseException:
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        raise
    if (expected_bytes is not None and destination.stat().st_size != expected_bytes) or digest(destination) != expected_sha:
        raise ValueError('Verified backup copy mismatch: ' + str(destination))


def atomic_install(source, target, mode):
    source = Path(source)
    target = Path(target)
    temporary = target.with_name(target.name + '.r4-new')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(temporary, flags, mode)
    try:
        with source.open('rb') as incoming, os.fdopen(fd, 'wb') as outgoing:
            shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, target)
        fsync_directory(target.parent)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def create_recovery_directory(path):
    path = Path(path)
    if path != DEFAULT_BACKUP or path.exists() or path.is_symlink():
        raise ValueError('Unexpected or existing recovery directory')
    parent = path.parent
    if not parent.exists():
        parent.mkdir(mode=0o700)
        fsync_directory(parent.parent)
    require_plain_root_input(parent, directory=True)
    path.mkdir(mode=0o700)
    os.chmod(path, 0o700)
    fsync_directory(parent)


def acquire_update_lock():
    if fcntl is None:
        raise RuntimeError('Linux flock support is required for --apply')
    fd = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or
            stat.S_IMODE(info.st_mode) & 0o022):
        os.close(fd)
        raise RuntimeError('Kernel update lock file is not root-owned and protected')
    stream = os.fdopen(fd, 'r+')
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        stream.close()
        raise RuntimeError('Another guarded kernel update is active')
    return stream


def deployment_record(plan, boot_after, slots_after, backup):
    candidate_bytes = plan['image'].stat().st_size
    return {
        'schema_version': 1,
        'from_kernelrelease': R3,
        'to_kernelrelease': R4,
        'kernel_commit': plan['manifest']['kernel_commit'],
        'root_uuid': ROOT_UUID,
        'target_partition': '/dev/sde11 (boot_a only)',
        'backup_directory': str(backup),
        'boot_a_before_sha256': plan['boot_before_sha256'],
        'boot_a_before_prefix_bytes': candidate_bytes,
        'boot_a_before_prefix_sha256': plan['boot_before_prefix_sha256'],
        'boot_a_before_tail_bytes': BOOT_BYTES - candidate_bytes,
        'boot_a_before_tail_sha256': plan['boot_before_tail_sha256'],
        'boot_a_after_sha256': digest(plan['boot']),
        'boot_a_after_prefix_bytes': candidate_bytes,
        'boot_a_after_prefix_sha256': boot_after['prefix_sha256'],
        'boot_a_after_tail_sha256': boot_after['tail_sha256'],
        'boot_b_before_sha256': plan['boot_b_before_sha256'],
        'boot_b_after_sha256': digest(plan['boot_b']),
        'dtbo_a_before_sha256': plan['dtbo_a_before_sha256'],
        'dtbo_a_after_sha256': digest(plan['dtbo_a']),
        'dtbo_b_before_sha256': plan['dtbo_b_before_sha256'],
        'dtbo_b_after_sha256': digest(plan['dtbo_b']),
        'slots_before': plan['slots_before'],
        'slots_after': slots_after,
        'bundle_manifest_sha256': BUNDLE_MANIFEST_SHA256,
        'boot_image_sha256': BOOT_IMAGE_SHA256,
        'modules_archive_sha256': MODULE_ARCHIVE_SHA256,
        'modules_checked': plan['module_count'],
        'oem_firmware_manifest_sha256': plan['firmware_manifest_sha256'],
        'charge_policy_sha256': CHARGE_SCRIPT_SHA256,
        'charge_owned': plan['charge']['owned'],
        'charge_last_error': plan['charge']['last_error'],
        'slider_helper_before_sha256': plan['helper_before_sha256'],
        'slider_helper_after_sha256': SLIDER_HELPER_SHA256,
        'reboot_executed': False,
        'gpt_written': False,
        'boot_b_written': False,
        'dtbo_written': False,
        'charge_policy_written': False,
    }


def apply_deployment(plan):
    backup = plan['backup']
    create_recovery_directory(backup)
    boot_backup = backup / 'boot_a.img'
    helper_backup = backup / 'kebab-alert-slider'
    backup_file(plan['boot'], boot_backup, plan['boot_before_sha256'], BOOT_BYTES)
    backup_file(plan['helper'], helper_backup, plan['helper_before_sha256'])
    fsync_directory(backup)
    if digest(plan['boot']) != plan['boot_before_sha256']:
        raise ValueError('boot_a changed while the full recovery backup was being made')
    if digest(plan['helper']) != plan['helper_before_sha256']:
        raise ValueError('Slider helper changed while the recovery backup was being made')

    preimage = {'target': 'boot_a only', 'boot_a_sha256': plan['boot_before_sha256'],
                'boot_bytes': BOOT_BYTES, 'helper_sha256': plan['helper_before_sha256'],
                'candidate_image_sha256': BOOT_IMAGE_SHA256,
                'backup_verified': True, 'reboot_executed': False}
    write_json_atomic(backup / 'preimage.json', preimage)

    helper_install_attempted = False
    boot_write_attempted = False
    try:
        helper_install_attempted = True
        atomic_install(plan['new_helper'], plan['helper'], 0o755)
        if digest(plan['helper']) != SLIDER_HELPER_SHA256:
            raise ValueError('Installed slider helper readback mismatch')
        boot_write_attempted = True
        plan['guards'].replace_boot(plan['boot'], plan['image'], boot_backup,
                                    plan['boot_before_sha256'], BOOT_IMAGE_SHA256)

        prefix = digest(plan['boot'], plan['image'].stat().st_size)
        tail = digest_range(plan['boot'], plan['image'].stat().st_size,
                            BOOT_BYTES - plan['image'].stat().st_size)
        if prefix != BOOT_IMAGE_SHA256 or tail != plan['boot_before_tail_sha256']:
            raise RuntimeError('boot_a post-write prefix/tail verification failed')
        if digest(plan['boot_b']) != plan['boot_b_before_sha256']:
            raise RuntimeError('boot_b changed during boot_a deployment')
        if (digest(plan['dtbo_a']) != ORIGINAL_DTBO_A_SHA256 or
                digest(plan['dtbo_a']) != plan['dtbo_a_before_sha256'] or
                digest(plan['dtbo_b']) != plan['dtbo_b_before_sha256']):
            raise RuntimeError('dtbo changed during boot_a deployment')
        slots_after = require_slot_state(plan['guards'].gpt_boot_slots())
        if slots_after != plan['slots_before']:
            raise RuntimeError('GPT slot metadata changed during boot_a deployment')
        if digest(plan['helper']) != SLIDER_HELPER_SHA256:
            raise RuntimeError('Slider helper changed after installation')
        boot_after = {'prefix_sha256': prefix, 'tail_sha256': tail}
        record = deployment_record(plan, boot_after, slots_after, backup)
        write_json_atomic(backup / 'deployment.json', record)
    except BaseException as exc:
        restore_errors = []
        if helper_install_attempted:
            try:
                atomic_install(helper_backup, plan['helper'], plan['helper_before_mode'])
                if digest(plan['helper']) != plan['helper_before_sha256']:
                    raise RuntimeError('slider helper restoration hash mismatch')
            except BaseException as restore_exc:
                restore_errors.append('helper restore failed: ' + str(restore_exc))
        if boot_write_attempted:
            try:
                plan['guards'].replace_boot(plan['boot'], boot_backup, boot_backup,
                                            plan['boot_before_sha256'], plan['boot_before_sha256'])
                if digest(plan['boot']) != plan['boot_before_sha256']:
                    raise RuntimeError('boot_a restoration hash mismatch')
            except BaseException as restore_exc:
                restore_errors.append('boot_a restore failed: ' + str(restore_exc))
        if restore_errors:
            raise RuntimeError('Deployment failed; DO NOT reboot; ' + '; '.join(restore_errors)) from exc
        raise RuntimeError('Deployment failed; previous boot/helper restored; do not reboot') from exc

    print('boot_a and slider helper installed and readback-verified; r3 remains recoverable')
    print('BACKUP=' + str(boot_backup))
    print('deployment.json records unchanged boot_b/dtbo/GPT; no reboot was executed')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path, help='root-private staged Venus bundle directory')
    parser.add_argument('--guard-library', type=Path, default=DEFAULT_GUARD)
    parser.add_argument('--firmware-manifest', type=Path, default=DEFAULT_FIRMWARE_MANIFEST)
    parser.add_argument('--backup', type=Path, default=DEFAULT_BACKUP)
    parser.add_argument('--apply', action='store_true', help='write the verified image to boot_a')
    args = parser.parse_args(argv)
    try:
        plan = preflight(args.bundle, args.guard_library, args.firmware_manifest, args.backup)
        print('PREFLIGHT PASS: r3 -> r4, boot_a only; no files or partitions changed')
        print('candidate=' + BOOT_IMAGE_SHA256 + '; modules=' + str(plan['module_count']) +
              '; firmware=' + OEM_MANIFEST_SHA256)
        if not args.apply:
            print('READ ONLY: pass --apply to create the verified backup and write boot_a; no reboot')
            return 0

        with acquire_update_lock():
            # Re-run every live and source check under the same lock immediately before any write.
            locked_plan = preflight(args.bundle, args.guard_library,
                                    args.firmware_manifest, args.backup)
            if locked_plan['boot_before_sha256'] != plan['boot_before_sha256']:
                raise ValueError('boot_a changed after preflight; refusing apply')
            apply_deployment(locked_plan)
        return 0
    except Exception as exc:
        print('REFUSED/FAILED: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
