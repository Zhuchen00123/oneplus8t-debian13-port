#!/usr/bin/env python3
"""Prepare an offline SM8250 Venus boot bundle; never run device commands."""
import argparse
import gzip
import hashlib
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile

from boot_image import inspect, verify_candidate


KERNEL_COMMIT = '83d0c460b621f7effe0a400f32d261c4f18335f2'
KERNEL_RELEASE = '7.2.0-kebab-debian13-r4-venus+'
KERNEL_CONFIG_SHA256 = 'e2543ac04e3ca5ffeabce188a313e4d72f4ae985bc0cfd3732dd12cb91ec4e12'
VENUS_POWER_DOMAIN_PATCH_SHA256 = '22350cd8151753e4e1a29f7bb5cbafbd5cac38bf5e49da8aef772d5e83570d4c'
VENUS_FREQUENCY_UPSTREAM_COMMIT = '68ea007df9293fcb29d38219d73094bbf4b59673'
VENUS_POWER_DOMAIN_PATCH = '0008-arm64-dts-sm8250-venus-power-opp.patch'
VENUS_POWER_DOMAIN_PATCH_SHA_NAME = 'patches/local/' + VENUS_POWER_DOMAIN_PATCH
TOUCH_PATCH_NAME = 'patches/local/0009-input-synaptics-tcm-padding-tail.patch'
TOUCH_PATCH_SHA256 = '5b019e42c85c4123971630623ca1d85042fec921a8b753b7dc90ce7a65e38250'
TOUCH_HEADER_SHA256 = '4c1141de2444e2a6f0674e92dba984fe5ea2bce27d42d00c812c71569bcb0fb6'
VENUS_POWER_DOMAIN_PATCH_URL = (
    'https://lore.kernel.org/r/20260604-iris-venus-fix-sm8250-v7-1-7bd2f0e5bae8@oss.qualcomm.com'
)
BOOT_BACKUP_SHA256 = '4fab2d9152aa78b41b884e913afb53c9605b5bb5e2ecb821f7e72d20d2603dfa'
ROOTFS_UUID = 'bc52fbbe-777b-47c8-84fc-ba56db7a8813'
CHARGE_POLICY = 'mp2762_otg_regulator.allow_charge_control=1'
BOOT_PARTITION_LIMIT = 100663296

VENUS_FIRMWARE_SHA256 = '13088ef634f4b9ee7cb329b2a4d66800c797a70a3cfb407f5818b0ff0af78db3'
VENUS_FIRMWARE_BYTES = 1985348
QCA6390_FIRMWARE_SHA256 = {
    'ath11k/QCA6390/hw2.0/amss.bin': '8ab2a06422e3989012df423bb6a156fde2c3feb4cd5db4c5d3588f674dc872ff',
    'ath11k/QCA6390/hw2.0/m3.bin': '1921d25edb52f90a7a0acc7275861e14399f904d579aae9f7ab1c4d28533fad6',
    'ath11k/QCA6390/hw2.0/board-2.bin': 'dd865b73d172c224a6e8a998dd05342b0294883e5f8514fae663d357e9be7725',
}
REQUIRED_FIRMWARE = frozenset({
    *QCA6390_FIRMWARE_SHA256,
    'qca/htbtfw20.tlv',
    'qca/htnv20.bin',
    'qcom/vpu/vpu20_p4.mbn',
})
VENUS_MODULES = frozenset({'venus_core', 'venus_dec', 'venus_enc'})
VENUS_DEPENDENCY_MODULES = frozenset({'v4l2_mem2mem', 'videobuf2_dma_contig'})
EARLY_FORBIDDEN_MODULES = VENUS_MODULES | VENUS_DEPENDENCY_MODULES
MODULE_SUFFIXES = ('.ko.zst', '.ko.xz', '.ko.gz', '.ko')
BLACKLIST_PATH = 'etc/modprobe.d/90-venus-blacklist.conf'
BLACKLIST_TEXT = (
    '# Keep Venus out of initramfs/rootfs udev modalias autoload until a supervised test.\n'
    'blacklist venus_core\n'
    'blacklist venus_dec\n'
    'blacklist venus_enc\n'
)
MAX_TAR_MEMBERS = 50000
MAX_TAR_UNPACKED_BYTES = 2 * 1024 * 1024 * 1024


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    with Path(path).open('r', encoding='utf-8') as stream:
        return json.load(stream)


def fail(message):
    raise ValueError(message)


def safe_relative_path(value):
    """Return a canonical POSIX path and reject traversal and host-specific paths."""
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        fail('Unsafe path value')
    if value.startswith('/') or (len(value) >= 2 and value[1] == ':'):
        fail('Absolute or drive-qualified path rejected: ' + value)
    path = PurePosixPath(value)
    if any(part in ('', '.', '..') for part in path.parts):
        fail('Non-canonical path rejected: ' + value)
    if path.as_posix() != value.rstrip('/'):
        fail('Non-canonical path rejected: ' + value)
    return path


def module_suffix(name):
    for suffix in MODULE_SUFFIXES:
        if name.endswith(suffix):
            return suffix
    return None


def module_key(name):
    suffix = module_suffix(name)
    if suffix is None:
        return None
    stem = name[:-len(suffix)]
    return stem.replace('-', '_')


def module_name(name):
    return module_key(PurePosixPath(name).name)


def module_raw_bytes(path):
    path = Path(path)
    if path.name.endswith('.ko.zst'):
        data = subprocess.check_output(['zstd', '-q', '-d', '-c', str(path)], timeout=30)
    elif path.name.endswith('.ko.xz'):
        data = lzma.decompress(path.read_bytes())
    elif path.name.endswith('.ko.gz'):
        data = gzip.decompress(path.read_bytes())
    else:
        data = path.read_bytes()
    if not data.startswith(b'\x7fELF'):
        fail('Decoded module is not an ELF file: ' + str(path))
    return data


def parse_config(text):
    values = {}
    for line in text.splitlines():
        if line.startswith('CONFIG_') and '=' in line:
            key, value = line.split('=', 1)
            if key in values:
                fail('Duplicate kernel config entry: ' + key)
            values[key] = value
    return values


def require_config(config_text):
    config = parse_config(config_text)
    required = {
        'CONFIG_LOCALVERSION': '"-kebab-debian13-r4-venus"',
        'CONFIG_VIDEO_QCOM_VENUS': 'm',
        'CONFIG_OF_DYNAMIC': 'y',
        'CONFIG_VIDEOBUF2_DMA_CONTIG': 'm',
        'CONFIG_V4L2_MEM2MEM_DEV': 'm',
        'CONFIG_KEYBOARD_GPIO': 'y',
        'CONFIG_INPUT_PM8941_PWRKEY': 'y',
        'CONFIG_TOUCHSCREEN_SYNAPTICS_TCM_ONCELL': 'y',
        'CONFIG_INPUT_KEBAB_ALERT_SLIDER': 'y',
        'CONFIG_REGULATOR_MP2762_OTG': 'y',
        'CONFIG_USB_GADGET': 'y',
        'CONFIG_USB_CONFIGFS': 'm',
        'CONFIG_USB_CONFIGFS_ACM': 'y',
        'CONFIG_USB_CONFIGFS_RNDIS': 'y',
    }
    missing = [key + '=' + value for key, value in required.items()
               if config.get(key) != value]
    if missing:
        fail('Required candidate config missing or changed: ' + ', '.join(missing))
    return config


def _dts_block_end(text, open_brace):
    depth = 0
    in_string = False
    escaped = False
    for index in range(open_brace, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return index
    fail('Unbalanced compiled DTS node')


def dts_node_blocks(text):
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    pattern = re.compile(r'(?m)^\s*([A-Za-z0-9,._+@-]+)\s*\{')
    for match in pattern.finditer(text):
        brace = text.find('{', match.start(), match.end())
        end = _dts_block_end(text, brace)
        yield match.group(1), text[brace + 1:end]


def dts_property(body, name):
    match = re.search(r'(?m)^\s*' + re.escape(name) + r'\s*=\s*([^;]+);', body)
    return match.group(1).strip() if match else None


def dts_integer_cells(expression):
    return [int(token, 0) for token in re.findall(r'0[xX][0-9a-fA-F]+|(?<![\w.])\d+', expression)]


def validate_venus_dts(dts):
    blocks = list(dts_node_blocks(dts))
    venus_nodes = [body for name, body in blocks if name == 'video-codec@aa00000']
    if len(venus_nodes) != 1:
        fail('Compiled DTS must contain exactly one video-codec@aa00000 node')
    venus = venus_nodes[0]
    compatible = dts_property(venus, 'compatible') or ''
    status = dts_property(venus, 'status') or ''
    firmware_name = dts_property(venus, 'firmware-name') or ''
    power_names = dts_property(venus, 'power-domain-names') or ''
    if '"qcom,sm8250-venus"' not in compatible or '"okay"' not in status:
        fail('Compiled Venus node is not enabled for SM8250')
    if '"postmarketos/venus.mbn"' not in firmware_name:
        fail('Compiled Venus node firmware name changed')
    power_domain_names = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', power_names)
    if power_domain_names != ['venus', 'vcodec0', 'mx', 'mmcx']:
        fail('Compiled Venus power-domain-names do not include MX and MMCX in order')

    operating_points = dts_property(venus, 'operating-points-v2')
    if not operating_points:
        fail('Compiled Venus node has no operating-points-v2 reference')
    references = dts_integer_cells(operating_points)
    if len(references) != 1:
        fail('Expected one Venus OPP table reference')
    opp_tables = []
    for name, body in blocks:
        if dts_property(body, 'compatible') != '"operating-points-v2"':
            continue
        phandle = dts_property(body, 'phandle')
        if phandle and references[0] in dts_integer_cells(phandle):
            opp_tables.append((name, body))
    if len(opp_tables) != 1:
        fail('Could not uniquely resolve the Venus OPP table phandle')
    table_name, table_body = opp_tables[0]
    child_blocks = list(dts_node_blocks(table_body))
    opp_nodes = {name: body for name, body in child_blocks if name.startswith('opp-')}
    expected_hz = {240000000, 338000000, 366000000, 444000000}
    expected_names = {'opp-' + str(value) for value in expected_hz}
    if set(opp_nodes) != expected_names:
        fail('Venus OPP node names differ from the corrected four operating points')
    actual_hz, required_counts = set(), {}
    for name, body in opp_nodes.items():
        hz_value = dts_property(body, 'opp-hz')
        if not hz_value:
            fail('Venus OPP lacks a 64-bit opp-hz value: ' + name)
        cell_match = re.search(r'<([^>]*)>', hz_value)
        if not cell_match:
            fail('Venus opp-hz has no cell value: ' + name)
        cells = dts_integer_cells(cell_match.group(1))
        if len(cells) == 1 and '/bits/ 64' in hz_value:
            frequency = cells[0]
        elif len(cells) == 2:
            frequency = (cells[0] << 32) | cells[1]
        else:
            fail('Unexpected Venus opp-hz cell count: ' + name)
        actual_hz.add(frequency)
        required = dts_property(body, 'required-opps')
        groups = re.findall(r'<([^>]*)>', required or '')
        required_cells = [cell for group in groups for cell in dts_integer_cells(group)]
        if len(required_cells) != 2:
            fail('Each Venus OPP must reference the MX and MMCX requirement: ' + name)
        required_counts[name] = len(required_cells)
    if actual_hz != expected_hz:
        fail('Compiled Venus OPP frequencies differ from corrected MVS0 rates')
    return {
        'node': 'video-codec@aa00000',
        'opp_table': table_name,
        'frequencies_hz': sorted(actual_hz),
        'power_domain_names': power_domain_names,
        'required_opps_cells_per_level': required_counts,
        'firmware_name': 'postmarketos/venus.mbn',
    }


def validate_kernel_artifacts(kernel, expected_kernel_commit=None):
    kernel = Path(kernel).resolve(strict=True)
    manifest_path = kernel / 'kernel-manifest.json'
    if not manifest_path.is_file() or manifest_path.is_symlink():
        fail('Kernel build-kernel.sh manifest is missing or unsafe')
    manifest = read_json(manifest_path)
    if manifest.get('build_mode') != 'kernel':
        fail('Kernel artifacts are not a kernel-mode build-kernel.sh group')
    declared = manifest.get('files_sha256')
    required_files = {
        'Image', 'Image.gz', 'System.map', 'sm8250-oneplus-kebab.dtb',
        'kernel.config', 'build-info.json', 'kernel.release', 'compiled.dts',
        'modules.tar.gz', 'module-verification.json', 'vmlinux-elf-header.txt',
    }
    if not isinstance(declared, dict) or not required_files.issubset(declared):
        fail('Kernel manifest is incomplete')
    for name, expected in declared.items():
        path = safe_relative_path(name)
        if len(path.parts) != 1 or not isinstance(expected, str) or len(expected) != 64:
            fail('Unsafe kernel manifest entry: ' + str(name))
        source = kernel / path.name
        if source.is_symlink() or not source.is_file() or sha(source) != expected:
            fail('Kernel artifact checksum mismatch: ' + name)

    release = (kernel / 'kernel.release').read_text(encoding='ascii').strip()
    if release != KERNEL_RELEASE:
        fail('Unexpected Venus release: ' + release)
    build_info = read_json(kernel / 'build-info.json')
    source_commit = build_info.get('kernel_commit')
    if (build_info.get('parent_kernel_commit') != KERNEL_COMMIT or
            not isinstance(source_commit, str) or not re.fullmatch(r'[0-9a-f]{40}', source_commit) or
            source_commit == KERNEL_COMMIT):
        fail('Kernel source must be a new local commit derived from the pinned r3 commit')
    if expected_kernel_commit and source_commit != expected_kernel_commit:
        fail('Kernel source commit differs from --expected-kernel-commit')
    patches = build_info.get('candidate_additional_patches')
    if not isinstance(patches, list):
        fail('build-info does not preserve candidate_additional_patches')
    patch_entries = [item for item in patches if isinstance(item, dict) and
                     str(item.get('file', '')).endswith(VENUS_POWER_DOMAIN_PATCH)]
    if len(patch_entries) != 1:
        fail('build-info must record exactly one 0008 Venus OPP/power-domain patch')
    if not any(isinstance(item, dict) and '0007' in str(item.get('file', '')) for item in patches):
        fail('build-info did not preserve the earlier 0007 candidate patch entry')
    patch_entry = patch_entries[0]
    project = Path(__file__).resolve().parents[2]
    patch_path = project / VENUS_POWER_DOMAIN_PATCH_SHA_NAME
    if (not patch_path.is_file() or patch_path.is_symlink() or
            sha(patch_path) != VENUS_POWER_DOMAIN_PATCH_SHA256 or
            patch_entry.get('sha256') != sha(patch_path) or
            patch_entry.get('upstream_frequency_commit') != VENUS_FREQUENCY_UPSTREAM_COMMIT or
            patch_entry.get('upstream_power_domain_patch_url') != VENUS_POWER_DOMAIN_PATCH_URL):
        fail('Recorded 0008 Venus patch metadata/hash differs from the reviewed local patch')
    touch_entries = [item for item in patches if isinstance(item, dict) and item.get('file') == TOUCH_PATCH_NAME]
    touch_patch = project / TOUCH_PATCH_NAME
    if (len(touch_entries) != 1 or not touch_patch.is_file() or touch_patch.is_symlink() or
            sha(touch_patch) != TOUCH_PATCH_SHA256 or touch_entries[0].get('sha256') != TOUCH_PATCH_SHA256 or
            touch_entries[0].get('parser_header_sha256') != TOUCH_HEADER_SHA256 or
            touch_entries[0].get('driver_name_unchanged') is not True):
        fail('Candidate must integrate the reviewed, deployed touch padding fix as builtin')
    if build_info.get('config_sha256') != KERNEL_CONFIG_SHA256:
        fail('build-info config identity mismatch')
    config_sha = sha(kernel / 'kernel.config')
    if config_sha != KERNEL_CONFIG_SHA256:
        fail('Unexpected Venus kernel configuration SHA-256')
    require_config((kernel / 'kernel.config').read_text(encoding='utf-8'))
    if gzip.decompress((kernel / 'Image.gz').read_bytes()) != (kernel / 'Image').read_bytes():
        fail('Compressed kernel differs from Image')
    dts = (kernel / 'compiled.dts').read_text(encoding='utf-8')
    dts_report = validate_venus_dts(dts)
    dts_report['patch_sha256'] = sha(patch_path)
    dts_report['upstream_frequency_commit'] = VENUS_FREQUENCY_UPSTREAM_COMMIT
    dts_report['local_kernel_commit'] = source_commit

    module_check = read_json(kernel / 'module-verification.json')
    if module_check.get('kernelrelease') != release:
        fail('Module verification release mismatch')
    return kernel, manifest, release, module_check, build_info, dts_report


def _tar_member_path(member):
    raw = member.name
    path = safe_relative_path(raw.rstrip('/') if member.isdir() else raw)
    if member.isdir() and raw != path.as_posix() and raw != path.as_posix() + '/':
        fail('Non-canonical module archive directory: ' + raw)
    if not member.isdir() and raw != path.as_posix():
        fail('Non-canonical module archive file: ' + raw)
    if not path.parts or path.parts[0] != 'lib':
        fail('Unexpected module archive root: ' + raw)
    return path


def inspect_modules_tar(archive_path, release):
    """Validate every tar member without extracting; symlinks and special files fail."""
    prefix = PurePosixPath('lib/modules') / release
    seen = {}
    modules = {}
    total = 0
    count = 0
    root_seen = False
    with tarfile.open(archive_path, 'r:gz') as archive:
        members = archive.getmembers()
        if len(members) > MAX_TAR_MEMBERS:
            fail('Module archive has too many entries')
        for member in members:
            count += 1
            path = _tar_member_path(member)
            if path.parts[:len(prefix.parts)] != prefix.parts:
                fail('Module archive contains another release or payload: ' + member.name)
            rel = PurePosixPath(*path.parts[len(prefix.parts):])
            if not rel.parts:
                if not member.isdir():
                    fail('Module archive release root is not a directory')
                if root_seen:
                    fail('Duplicate module archive release root')
                root_seen = True
            elif member.isfile():
                if member.size < 0:
                    fail('Negative module archive member size')
                total += member.size
                if total > MAX_TAR_UNPACKED_BYTES:
                    fail('Module archive exceeds the unpacked size limit')
                rel_name = rel.as_posix()
                if rel_name in seen:
                    fail('Duplicate module archive member: ' + rel_name)
                seen[rel_name] = 'file'
                if module_suffix(rel_name):
                    key = module_key(rel_name)
                    if key in modules:
                        fail('Duplicate kernel module name: ' + key)
                    modules[key] = rel_name
            elif member.isdir():
                rel_name = rel.as_posix()
                if rel.parts:
                    if rel_name in seen:
                        fail('Duplicate module archive member: ' + rel_name)
                    seen[rel_name] = 'directory'
            else:
                fail('Module archive symlink or special entry rejected: ' + member.name)
    for relative, kind in seen.items():
        parts = PurePosixPath(relative).parts
        for index in range(1, len(parts)):
            ancestor = PurePosixPath(*parts[:index]).as_posix()
            if seen.get(ancestor) == 'file':
                fail('Module archive file is used as a parent directory: ' + ancestor)
    if count == 0 or not modules:
        fail('Module archive is empty')
    return {'modules': modules, 'member_count': count, 'unpacked_bytes': total}


def extract_modules_tar(archive_path, release, destination):
    """Extract a previously validated module archive without tarfile.extract()."""
    archive_info = inspect_modules_tar(archive_path, release)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    root_prefix = PurePosixPath('lib/modules') / release
    with tarfile.open(archive_path, 'r:gz') as archive:
        for member in archive.getmembers():
            path = _tar_member_path(member)
            rel = PurePosixPath(*path.parts[len(root_prefix.parts):])
            target = destination.joinpath(*path.parts)
            if not target.resolve(strict=False).is_relative_to(destination.resolve()):
                fail('Module extraction escaped its staging directory')
            cursor = destination
            for component in path.parts:
                cursor = cursor / component
                if cursor.is_symlink():
                    fail('Unexpected symlink in module extraction path')
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
            os.chmod(target, member.mode & 0o777)
    module_root = destination / 'lib' / 'modules' / release
    extracted = {}
    for path in sorted(module_root.rglob('*.ko*')):
        if path.is_symlink() or not path.is_file() or not module_suffix(path.name):
            fail('Unsafe module extraction result: ' + str(path))
        key = module_key(path.relative_to(module_root).as_posix())
        if key in extracted:
            fail('Duplicate module after archive extraction: ' + key)
        extracted[key] = path.relative_to(module_root).as_posix()
    if extracted != archive_info['modules']:
        fail('Extracted module set differs from validated archive set')
    return module_root


def read_builtin_module_names(module_root):
    builtin = Path(module_root) / 'modules.builtin'
    if not builtin.is_file() or builtin.is_symlink():
        fail('modules.builtin is missing or unsafe')
    names = set()
    for line in builtin.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        path = safe_relative_path(line)
        if not path.name.endswith('.ko'):
            fail('Unexpected modules.builtin entry: ' + line)
        names.add(module_key(path.name))
    return names


def modinfo_field(module_path, field):
    try:
        return subprocess.check_output(['modinfo', '-F', field, str(module_path)],
                                       text=True, stderr=subprocess.PIPE).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        fail('modinfo failed for ' + str(module_path) + ' (' + field + '): ' + str(exc))


def validate_module_tree(module_root, release, module_check):
    module_root = Path(module_root)
    modules = {}
    module_names = {}
    actual_vermagic = set()
    import_map = {}
    for path in sorted(module_root.rglob('*.ko*')):
        if path.is_symlink() or not path.is_file() or not module_suffix(path.name):
            fail('Unsafe or unsupported module file: ' + str(path))
        relative = path.relative_to(module_root).as_posix()
        key = module_key(relative)
        if key in modules:
            fail('Duplicate module in extracted module tree: ' + key)
        modules[key] = relative
        simple_name = module_name(relative)
        if simple_name in module_names:
            fail('Duplicate kernel module basename: ' + simple_name)
        module_names[simple_name] = relative
        vermagic = modinfo_field(path, 'vermagic')
        if not vermagic.startswith(release + ' '):
            fail('Module vermagic mismatch: ' + relative + ': ' + vermagic)
        actual_vermagic.add(vermagic)
        dependencies = [item.strip() for item in modinfo_field(path, 'depends').split(',') if item.strip()]
        import_map[simple_name] = sorted(set(dependency.replace('-', '_') for dependency in dependencies))
    if not modules:
        fail('No modules found after extraction')
    expected_count = module_check.get('modules_checked')
    if expected_count != len(modules):
        fail('Complete module count differs from module-verification.json')
    expected_vermagic = module_check.get('vermagic_values')
    if not isinstance(expected_vermagic, list) or sorted(actual_vermagic) != sorted(expected_vermagic):
        fail('Module vermagic set differs from module-verification.json')
    builtin = read_builtin_module_names(module_root)
    available = set(module_names) | builtin
    unresolved = sorted({dependency for values in import_map.values() for dependency in values
                         if dependency not in available})
    if unresolved:
        fail('Module depends/imports do not resolve in the full release: ' + ', '.join(unresolved))
    missing_venus = sorted((VENUS_MODULES | VENUS_DEPENDENCY_MODULES) - set(module_names))
    if missing_venus:
        fail('Required Venus module/dependency missing from full module group: ' + ', '.join(missing_venus))
    return {'modules': modules, 'module_names': module_names, 'vermagic': sorted(actual_vermagic),
            'imports': import_map, 'builtins': builtin}


def _firmware_source(root, relative):
    path = safe_relative_path(relative)
    current = Path(root)
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            fail('Firmware source symlink rejected: ' + relative)
    try:
        mode = current.stat(follow_symlinks=False).st_mode
    except FileNotFoundError:
        fail('Firmware source is missing: ' + relative)
    if not stat.S_ISREG(mode):
        fail('Firmware source is not a regular file: ' + relative)
    return current


def validate_firmware_manifest(firmware_root, expected_files=None):
    """Check an explicit backup manifest and exact bytes; tests inject tiny file sets."""
    firmware_root = Path(firmware_root).resolve(strict=True)
    manifest_path = firmware_root / 'manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file():
        fail('Firmware input requires a regular manifest.json')
    manifest = read_json(manifest_path)
    hashes = manifest.get('files_sha256')
    sizes = manifest.get('files_bytes')
    expected_files = set(expected_files or REQUIRED_FIRMWARE)
    if not isinstance(hashes, dict) or not isinstance(sizes, dict):
        fail('Firmware manifest requires files_sha256 and files_bytes maps')
    if set(hashes) != expected_files or set(sizes) != expected_files:
        fail('Firmware manifest file set differs from the required explicit inputs')
    checked = {}
    for relative in sorted(expected_files):
        safe_relative_path(relative)
        source = _firmware_source(firmware_root, relative)
        expected_hash = hashes.get(relative)
        expected_size = sizes.get(relative)
        if (not isinstance(expected_hash, str) or len(expected_hash) != 64 or
                any(char not in '0123456789abcdef' for char in expected_hash) or
                not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size <= 0):
            fail('Invalid firmware manifest entry: ' + relative)
        actual_size = source.stat().st_size
        actual_hash = sha(source)
        if actual_size != expected_size or actual_hash != expected_hash:
            fail('Firmware source bytes differ from its manifest: ' + relative)
        known = QCA6390_FIRMWARE_SHA256.get(relative)
        if known and actual_hash != known:
            fail('QCA6390 firmware differs from the current-device backup identity: ' + relative)
        if relative == 'qcom/vpu/vpu20_p4.mbn' and (
                actual_size != VENUS_FIRMWARE_BYTES or actual_hash != VENUS_FIRMWARE_SHA256):
            fail('VPU20 P4 firmware size/SHA-256 mismatch')
        checked[relative] = {'bytes': actual_size, 'sha256': actual_hash}
    return firmware_root, manifest, checked


def validate_baseline(baseline):
    baseline = Path(baseline).resolve(strict=True)
    manifest_path = baseline / 'slider-bundle-manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file():
        fail('Sealed slider r3 bundle manifest is missing')
    manifest = read_json(manifest_path)
    if (manifest.get('kernelrelease') != '7.2.0-kebab-debian13-r3-slider+' or
            manifest.get('kernel_commit') != KERNEL_COMMIT):
        fail('Unexpected sealed r3 baseline identity')
    hashes = manifest.get('files_sha256', {})
    for name in ('boot-slider.img', 'initramfs-server.gz', 'modules.tar.gz'):
        source = baseline / name
        if (source.is_symlink() or not source.is_file() or not isinstance(hashes.get(name), str) or
                sha(source) != hashes[name]):
            fail('Sealed r3 baseline checksum mismatch: ' + name)
    boot = inspect(baseline / 'boot-slider.img')
    if boot['segments']['ramdisk']['sha256'] != hashes['initramfs-server.gz']:
        fail('Sealed r3 boot image does not contain its recorded initramfs')
    manifest_boot = manifest.get('boot', {})
    recorded_segments = manifest_boot.get('segments', {})
    if (not recorded_segments or
            recorded_segments.get('dtb', {}).get('sha256') != boot['segments']['dtb']['sha256'] or
            manifest_boot.get('cmdline_sha256') != boot['cmdline_sha256']):
        fail('Sealed r3 DTB manifest mismatch')
    return baseline, manifest, boot


def validate_boot_backup(boot_backup, profile, baseline_boot):
    boot_backup = Path(boot_backup).resolve(strict=True)
    actual = sha(boot_backup)
    if actual != BOOT_BACKUP_SHA256:
        fail('Boot backup SHA-256 differs from the measured boot_a identity')
    original = inspect(boot_backup)
    layout_fields = ('header_version', 'page_size', 'header_size', 'kernel_address',
                     'ramdisk_address', 'second_address', 'tags_address', 'dtb_address',
                     'os_version_encoded')
    for field in layout_fields:
        if original[field] != baseline_boot[field]:
            fail('Measured boot backup differs from sealed r3 layout: ' + field)
    if original['cmdline_sha256'] != baseline_boot['cmdline_sha256']:
        fail('Measured boot backup command line differs from sealed r3 policy')
    raw = boot_backup.read_bytes()
    cmdline = (raw[64:576].split(b'\0', 1)[0] + raw[608:1632].split(b'\0', 1)[0]).decode('ascii')
    tokens = cmdline.split()
    if 'root=UUID=' + ROOTFS_UUID not in tokens or CHARGE_POLICY not in tokens:
        fail('Measured boot backup lacks required root UUID or charging policy')
    if profile.get('boot_partition_bytes') != BOOT_PARTITION_LIMIT:
        fail('Boot profile partition limit mismatch')
    return boot_backup, original, cmdline


def _unpack_initramfs(image, destination):
    subprocess.run(['unmkinitramfs', str(image), str(destination)], check=True)
    main = Path(destination) / 'main'
    return main if main.is_dir() else Path(destination)


def find_module_root(tree, release):
    tree = Path(tree).resolve(strict=True)
    matches = []
    for current, dirs, _files in os.walk(tree, followlinks=False):
        base = Path(current)
        dirs[:] = [name for name in dirs if not (base / name).is_symlink()]
        if base.name == release and base.parent.name == 'modules':
            resolved = base.resolve(strict=True)
            if not resolved.is_relative_to(tree):
                fail('Initramfs module root escapes its tree')
            matches.append(base)
    if len(matches) != 1:
        fail('Expected exactly one initramfs module root for ' + release)
    return matches[0]


def initramfs_module_files(module_root):
    module_root = Path(module_root)
    result = {}
    for path in sorted(module_root.rglob('*')):
        if path.is_symlink():
            fail('Initramfs module-tree symlink rejected: ' + str(path))
        if path.is_file() and module_suffix(path.name):
            relative = path.relative_to(module_root).as_posix()
            if module_key(relative) in result:
                fail('Duplicate initramfs module: ' + relative)
            result[module_key(relative)] = (relative, path)
    if not result:
        fail('No modules found in sealed initramfs')
    return result


def verify_all_modules_are_in_release(unpack_root, main_tree, module_root, release, expected_paths):
    unpack_root = Path(unpack_root).resolve(strict=True)
    main_tree = Path(main_tree).resolve(strict=True)
    module_root = Path(module_root).resolve(strict=True)
    actual = set()
    for path in unpack_root.rglob('*.ko*'):
        if path.is_symlink() or not path.is_file() or not module_suffix(path.name):
            fail('Unsafe initramfs module outside the validated release: ' + str(path))
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(main_tree) or not resolved.is_relative_to(module_root):
            fail('Initramfs contains a module outside its one validated release: ' + str(path))
        relative = resolved.relative_to(module_root).as_posix()
        if release not in module_root.parts:
            fail('Unexpected initramfs module root release')
        actual.add(relative)
    if actual != set(expected_paths):
        fail('Initramfs contains modules outside or missing from the sealed release subset')


def tree_file_inventory(tree, excluded_roots=(), excluded_files=()):
    """Hash regular files and record symlink targets without following symlinks."""
    tree = Path(tree).resolve(strict=True)
    excluded_roots = [PurePosixPath(p) for p in excluded_roots]
    excluded_files = {PurePosixPath(p).as_posix() for p in excluded_files}
    inventory = {}
    for current, dirs, files in os.walk(tree, followlinks=False):
        base = Path(current)
        kept_dirs = []
        for name in dirs:
            path = base / name
            relative = path.relative_to(tree).as_posix()
            rel = PurePosixPath(relative)
            if any(rel == prefix or prefix in rel.parents for prefix in excluded_roots):
                continue
            if path.is_symlink():
                if relative in excluded_files:
                    continue
                inventory[relative] = ('symlink', os.readlink(path))
            else:
                kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in files:
            path = base / name
            relative = path.relative_to(tree).as_posix()
            rel = PurePosixPath(relative)
            if relative in excluded_files or any(rel == prefix or prefix in rel.parents
                                                 for prefix in excluded_roots):
                continue
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                inventory[relative] = ('symlink', os.readlink(path))
            elif stat.S_ISREG(mode):
                inventory[relative] = ('file', stat.S_IMODE(mode), sha(path))
            elif stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
                inventory[relative] = ('device', stat.S_IFMT(mode), os.major(path.stat().st_rdev),
                                       os.minor(path.stat().st_rdev))
            elif stat.S_ISFIFO(mode):
                inventory[relative] = ('fifo', stat.S_IMODE(mode))
            else:
                fail('Unsupported initramfs file type: ' + relative)
    return inventory


def _resolved_tree_path(tree, path):
    tree = Path(tree).resolve(strict=True)
    path = Path(path)
    parent = path.parent.resolve(strict=True)
    if not parent.is_relative_to(tree):
        fail('Initramfs path resolves outside its tree: ' + str(path))
    return (parent / path.name).relative_to(tree).as_posix()


def _create_tree_parent(tree, parent):
    tree = Path(tree).resolve(strict=True)
    parent = Path(parent)
    cursor = parent
    while not cursor.exists() and not cursor.is_symlink():
        if cursor == cursor.parent:
            fail('Could not find a safe existing initramfs parent')
        cursor = cursor.parent
    if not cursor.resolve(strict=True).is_relative_to(tree):
        fail('Initramfs parent resolves outside its tree: ' + str(cursor))
    parent.mkdir(parents=True, exist_ok=True)
    if not parent.resolve(strict=True).is_relative_to(tree):
        fail('Created initramfs parent resolves outside its tree: ' + str(parent))


def _check_existing_file(path, expected_sha):
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return False
    if path.is_symlink():
        target = path.resolve(strict=True)
        if not target.is_file() or sha(target) != expected_sha:
            fail('Existing initramfs firmware symlink conflicts: ' + str(path))
        return True
    if not path.is_file() or sha(path) != expected_sha:
        fail('Existing initramfs file conflicts by type or SHA-256: ' + str(path))
    return True


def add_firmware(tree, firmware_root, firmware_entries):
    tree = Path(tree).resolve(strict=True)
    added = set()
    copied = {}
    for relative, identity in sorted(firmware_entries.items()):
        source = _firmware_source(firmware_root, relative)
        destination = tree / 'lib' / 'firmware' / Path(*safe_relative_path(relative).parts)
        _create_tree_parent(tree, destination.parent)
        if (destination.is_symlink() and
                not destination.resolve(strict=True).is_relative_to(tree)):
            fail('Existing initramfs firmware symlink escapes its tree: ' + str(destination))
        if _check_existing_file(destination, identity['sha256']):
            copied[relative] = {'action': 'preserved-identical', **identity}
            continue
        shutil.copyfile(source, destination)
        os.chmod(destination, source.stat().st_mode & 0o777)
        added.add(_resolved_tree_path(tree, destination))
        copied[relative] = {'action': 'added', **identity}

    alias_rel = 'postmarketos/venus.mbn'
    alias_target = '../qcom/vpu/vpu20_p4.mbn'
    alias = tree / 'lib' / 'firmware' / alias_rel
    _create_tree_parent(tree, alias.parent)
    target = alias.parent / alias_target
    if (not target.is_file() or not target.resolve(strict=True).is_relative_to(tree) or
            sha(target.resolve(strict=True)) != VENUS_FIRMWARE_SHA256):
        fail('Verified VPU20 P4 target is unavailable for the DT firmware alias')
    if alias.is_symlink():
        if os.readlink(alias) != alias_target:
            fail('Existing postmarketos/venus.mbn symlink has a different target')
        if alias.resolve(strict=True) != target.resolve(strict=True):
            fail('Existing Venus firmware alias resolves somewhere unexpected')
        copied[alias_rel] = {'action': 'preserved-identical-symlink', 'target': alias_target}
    elif alias.exists():
        if not alias.is_file() or sha(alias) != VENUS_FIRMWARE_SHA256:
            fail('Existing postmarketos/venus.mbn conflicts with verified VPU20 P4')
        copied[alias_rel] = {'action': 'preserved-identical-file', 'sha256': VENUS_FIRMWARE_SHA256}
    else:
        alias.symlink_to(alias_target)
        added.add(_resolved_tree_path(tree, alias))
        copied[alias_rel] = {'action': 'added-symlink', 'target': alias_target}
    return added, copied


def add_blacklist(tree):
    tree = Path(tree).resolve(strict=True)
    path = tree / Path(*safe_relative_path(BLACKLIST_PATH).parts)
    _create_tree_parent(tree, path.parent)
    expected = BLACKLIST_TEXT.encode('utf-8')
    if path.is_symlink():
        fail('Existing Venus blacklist symlink rejected')
    if path.exists():
        if not path.is_file() or path.read_bytes() != expected:
            fail('Existing Venus blacklist has different contents')
        return set(), sha_bytes(expected)
    path.write_bytes(expected)
    os.chmod(path, 0o644)
    return {_resolved_tree_path(tree, path)}, sha_bytes(expected)


def cpio(directory):
    names = subprocess.check_output(['find', '.', '-print0'], cwd=directory)
    return subprocess.check_output(['cpio', '--quiet', '--null', '-o', '-H', 'newc',
                                    '--owner=0:0'], input=names, cwd=directory)


def repack_initramfs(unpack, main_tree, destination):
    unpack = Path(unpack)
    main_tree = Path(main_tree)
    early = sorted(path for path in unpack.iterdir()
                   if path.is_dir() and path.name.startswith('early')) if main_tree != unpack else []
    destination.write_bytes(b''.join(cpio(path) for path in early) +
                             gzip.compress(cpio(main_tree), mtime=0))


def ensure_output_disjoint(output, protected_paths):
    output = Path(output).resolve(strict=False)
    for protected in protected_paths:
        protected = Path(protected).resolve(strict=True)
        if output == protected or output.is_relative_to(protected) or protected.is_relative_to(output):
            fail('Output directory overlaps an input tree: ' + str(protected))


def prepare_bundle(kernel_artifacts, baseline_bundle, boot_backup, firmware_dir, output_dir,
                   expected_kernel_commit=None):
    kernel, kernel_manifest, release, module_check, build_info, dts_report = \
        validate_kernel_artifacts(kernel_artifacts, expected_kernel_commit)
    baseline, baseline_manifest, baseline_boot = validate_baseline(baseline_bundle)
    profile_path = Path(__file__).resolve().parents[2] / 'config' / 'boot-kb2000.json'
    profile = read_json(profile_path)
    boot_backup, measured_boot, cmdline = validate_boot_backup(boot_backup, profile, baseline_boot)
    firmware_root, firmware_manifest, firmware_entries = validate_firmware_manifest(firmware_dir)

    output = Path(output_dir).resolve(strict=False)
    if output.exists() or output.is_symlink():
        fail('Existing outputs are preserved; choose a fresh directory')
    ensure_output_disjoint(output, [kernel, baseline, boot_backup.parent, firmware_root,
                                    profile_path.parent])

    with tempfile.TemporaryDirectory(prefix='kebab-venus-stage-') as work_name:
        work = Path(work_name)
        full_modules_dir = extract_modules_tar(kernel / 'modules.tar.gz', release, work / 'full-modules')
        if (sha(kernel / 'modules.tar.gz') != kernel_manifest['files_sha256']['modules.tar.gz']):
            fail('Module archive changed while staging')
        module_info = validate_module_tree(full_modules_dir, release, module_check)

        init_unpack = work / 'initramfs-original'
        main_tree = _unpack_initramfs(baseline / 'initramfs-server.gz', init_unpack)
        old_release = baseline_manifest['kernelrelease']
        old_module_root = find_module_root(main_tree, old_release)
        original_modules = initramfs_module_files(old_module_root)
        expected_subset_count = baseline_manifest.get('initramfs_modules_checked')
        if expected_subset_count != len(original_modules):
            fail('Sealed initramfs module count differs from its audit manifest')
        subset_paths = {relative for relative, _path in original_modules.values()}
        verify_all_modules_are_in_release(init_unpack, main_tree, old_module_root,
                                          old_release, subset_paths)
        missing_from_new = sorted(set(original_modules) - set(module_info['modules']))
        if missing_from_new:
            fail('New release lacks sealed initramfs module paths: ' + ', '.join(missing_from_new))
        early_names = {module_name(relative) for relative in subset_paths}
        forbidden_present = sorted(EARLY_FORBIDDEN_MODULES & early_names)
        if forbidden_present:
            fail('Venus/autoload-sensitive modules are already in the sealed early set: ' +
                 ', '.join(forbidden_present))

        before = tree_file_inventory(init_unpack, excluded_roots=[
            old_module_root.resolve(strict=True).relative_to(init_unpack.resolve()).as_posix()])
        module_parent = old_module_root.parent
        shutil.rmtree(old_module_root)
        new_module_root = module_parent / release
        new_module_root.mkdir(parents=True, exist_ok=False)
        subset_sha256 = {}
        for relative in sorted(subset_paths):
            source = full_modules_dir / module_info['modules'][module_key(relative)]
            if source.is_symlink() or not source.is_file():
                fail('New release module is missing or unsafe: ' + relative)
            destination = new_module_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            # The sealed r3 boot subset uses raw ELF .ko paths; complete release
            # archives use compressed .ko.zst. Preserve the boot paths and put
            # the exact decompressed new module bytes at those paths.
            if not relative.endswith('.ko'):
                fail('Unexpected compressed module in sealed r3 boot subset: ' + relative)
            raw = module_raw_bytes(source)
            destination.write_bytes(raw)
            subset_sha256[relative] = sha_bytes(raw)
            os.chmod(destination, source.stat().st_mode & 0o777)

        # Keep builtin metadata from this exact release, then rebuild dependencies
        # against the sealed early subset so no new module is pulled into initramfs.
        for name in sorted(path.name for path in full_modules_dir.iterdir()
                           if path.name.startswith('modules.builtin') or path.name == 'modules.order'):
            source = full_modules_dir / name
            if source.is_symlink() or not source.is_file():
                fail('Unsafe full-release module metadata: ' + name)
            shutil.copyfile(source, new_module_root / name)
        modules_basedir = '/' + module_parent.relative_to(main_tree).as_posix()
        subprocess.run(['depmod', '-b', str(main_tree), '-m', modules_basedir, release], check=True)

        firmware_added, firmware_actions = add_firmware(main_tree, firmware_root, firmware_entries)
        blacklist_added, blacklist_sha = add_blacklist(main_tree)
        allowed_added = firmware_added | blacklist_added
        main_prefix = main_tree.resolve(strict=True).relative_to(init_unpack.resolve()).as_posix()
        added_in_unpack = {
            (PurePosixPath(main_prefix) / PurePosixPath(relative)).as_posix()
            for relative in allowed_added
        }
        current = tree_file_inventory(init_unpack, excluded_roots=[
            new_module_root.resolve(strict=True).relative_to(init_unpack.resolve()).as_posix()],
            excluded_files=added_in_unpack)
        if before != current:
            before_only = sorted(set(before) - set(current))[:8]
            after_only = sorted(set(current) - set(before))[:8]
            changed = sorted(key for key in set(before) & set(current)
                             if before[key] != current[key])[:8]
            fail('Non-module initramfs bytes changed before repack; removed=%r added=%r changed=%r' %
                 (before_only, after_only, changed))

        repacked = work / 'initramfs-server.gz'
        repack_initramfs(init_unpack, main_tree, repacked)
        verify_unpack = work / 'initramfs-verified'
        verify_tree = _unpack_initramfs(repacked, verify_unpack)
        verify_module_root = find_module_root(verify_tree, release)
        actual_modules = initramfs_module_files(verify_module_root)
        actual_paths = {relative for relative, _path in actual_modules.values()}
        if actual_paths != subset_paths:
            fail('Repacked initramfs module path set differs from sealed r3 subset')
        verify_all_modules_are_in_release(verify_unpack, verify_tree, verify_module_root,
                                          release, subset_paths)
        for relative in sorted(subset_paths):
            expected = subset_sha256[relative]
            actual = sha(verify_module_root / relative)
            if actual != expected:
                fail('Repacked initramfs module SHA-256 mismatch: ' + relative)
        repacked_inventory = tree_file_inventory(verify_unpack, excluded_roots=[
            verify_module_root.resolve(strict=True).relative_to(verify_unpack.resolve()).as_posix()],
            excluded_files=added_in_unpack)
        if before != repacked_inventory:
            before_only = sorted(set(before) - set(repacked_inventory))[:8]
            after_only = sorted(set(repacked_inventory) - set(before))[:8]
            changed = sorted(key for key in set(before) & set(repacked_inventory)
                             if before[key] != repacked_inventory[key])[:8]
            fail('Non-module initramfs bytes changed after repack; removed=%r added=%r changed=%r' %
                 (before_only, after_only, changed))

        # The blacklist must survive cpio and still block all module names used by
        # the actual r4 driver, before rootfs state is available for a manual test.
        verified_blacklist = verify_tree / Path(*safe_relative_path(BLACKLIST_PATH).parts)
        if not verified_blacklist.is_file() or verified_blacklist.read_text(encoding='utf-8') != BLACKLIST_TEXT:
            fail('Initramfs Venus autoload blacklist did not survive repacking')
        for module in sorted(VENUS_MODULES):
            if ('blacklist ' + module) not in BLACKLIST_TEXT.splitlines():
                fail('Initramfs blacklist does not cover ' + module)

        firmware_root_in_verified = verify_tree / 'lib' / 'firmware'
        for relative, identity in sorted(firmware_entries.items()):
            path = firmware_root_in_verified / Path(*safe_relative_path(relative).parts)
            if path.is_symlink():
                if not path.resolve(strict=True).is_relative_to(verify_tree.resolve()):
                    fail('Repacked initramfs firmware symlink escapes its tree: ' + relative)
            if not path.is_file() or sha(path.resolve(strict=True) if path.is_symlink() else path) != identity['sha256']:
                fail('Repacked initramfs firmware SHA-256 mismatch: ' + relative)
        verified_vpu = firmware_root_in_verified / 'qcom' / 'vpu' / 'vpu20_p4.mbn'
        if not verified_vpu.is_file() or sha(verified_vpu) != VENUS_FIRMWARE_SHA256:
            fail('Repacked initramfs does not contain the verified VPU20 P4 firmware')
        verified_alias = firmware_root_in_verified / 'postmarketos' / 'venus.mbn'
        if verified_alias.is_symlink():
            alias_valid = (os.readlink(verified_alias) == '../qcom/vpu/vpu20_p4.mbn' and
                           verified_alias.resolve(strict=True).is_relative_to(verify_tree.resolve()) and
                           sha(verified_alias.resolve(strict=True)) == VENUS_FIRMWARE_SHA256)
        else:
            alias_valid = (verified_alias.is_file() and
                           sha(verified_alias) == VENUS_FIRMWARE_SHA256)
        if not alias_valid:
            fail('Repacked DT firmware alias does not resolve to verified VPU20 P4')

        output.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=output.name + '.tmp-', dir=output.parent))
        try:
            shutil.copyfile(repacked, stage / 'initramfs-server.gz')
            shutil.copyfile(kernel / 'modules.tar.gz', stage / 'modules.tar.gz')
            if sha(stage / 'modules.tar.gz') != sha(kernel / 'modules.tar.gz'):
                fail('Output modules.tar.gz differs from the complete verified release archive')
            project = Path(__file__).resolve().parents[2]
            prep = stage / 'rootfs-prep'
            prep.mkdir()
            helper_inputs = {
                'kebab-alert-slider':project/'scripts/linux/kebab-alert-slider.sh',
                'kebab-venus-return':project/'scripts/linux/kebab-venus-return.py',
                'kebab-venus-trial-return.service':project/'config/kebab-venus-trial-return.service',
                'kebab-venus-trial-return.timer':project/'config/kebab-venus-trial-return.timer',
            }
            for name, source in helper_inputs.items():
                shutil.copyfile(source, prep/name)
            (prep/'90-venus-blacklist.conf').write_text(BLACKLIST_TEXT, encoding='utf-8')
            image = stage / 'boot-venus.img'
            command = [
                'mkbootimg', '--kernel', str(kernel / 'Image.gz'), '--ramdisk', str(stage / 'initramfs-server.gz'),
                '--dtb', str(kernel / 'sm8250-oneplus-kebab.dtb'),
                '--header_version', str(profile['header_version']), '--pagesize', str(profile['pagesize']),
                '--base', profile['base'], '--os_version', profile['legacy_header_os_version'],
                '--os_patch_level', profile['legacy_header_os_patch_level'], '--cmdline', cmdline,
            ]
            for name in ('kernel_offset', 'ramdisk_offset', 'second_offset', 'tags_offset', 'dtb_offset'):
                command.extend(['--' + name, profile[name]])
            subprocess.run(command + ['--output', str(image)], check=True)
            boot = verify_candidate(image, boot_backup, kernel / 'Image.gz',
                                    kernel / 'sm8250-oneplus-kebab.dtb', stage / 'initramfs-server.gz')
            if boot['cmdline_sha256'] != measured_boot['cmdline_sha256']:
                fail('Candidate boot command line differs from measured boot backup')
            if image.stat().st_size > BOOT_PARTITION_LIMIT:
                fail('Candidate boot image exceeds the 100663296-byte partition limit')
            independent = work / 'aosp-unpacked'
            subprocess.run(['unpack_bootimg', '--boot_img', str(image), '--out', str(independent)],
                           check=True, stdout=subprocess.DEVNULL)
            for name, source in (('kernel', kernel / 'Image.gz'),
                                 ('dtb', kernel / 'sm8250-oneplus-kebab.dtb'),
                                 ('ramdisk', stage / 'initramfs-server.gz')):
                if sha(independent / name) != sha(source):
                    fail('Independent AOSP unpack SHA-256 mismatch: ' + name)

            report = {
                'kernelrelease': release,
                'kernel_commit': build_info['kernel_commit'],
                'parent_kernel_commit': build_info['parent_kernel_commit'],
                'candidate_additional_patches': build_info['candidate_additional_patches'],
                'kernel_config_sha256': KERNEL_CONFIG_SHA256,
                'venus_dtb_adaptation': dts_report,
                'modules_count': len(module_info['modules']),
                'modules_sha256_verified': True,
                'modules_vermagic_verified': True,
                'module_dependencies_resolved': True,
                'initramfs_subset_count': len(subset_paths),
                'initramfs_module_paths_match_sealed_r3_subset': True,
                'initramfs_module_sha256_verified': True,
                'venus_and_selected_dependencies_absent_from_early_initramfs': True,
                'old_non_module_initramfs_files_byte_preserved': True,
                'firmware_input_manifest': str(Path(firmware_dir).resolve() / 'manifest.json'),
                'firmware_input_manifest_sha256': sha(firmware_root / 'manifest.json'),
                'firmware_files': firmware_entries,
                'firmware_actions': firmware_actions,
                'venus_firmware_alias': {
                    'path': '/lib/firmware/postmarketos/venus.mbn',
                    'target': '../qcom/vpu/vpu20_p4.mbn',
                    'sha256': VENUS_FIRMWARE_SHA256,
                },
                'venus_autoload_policy': {
                    'initramfs_blacklist_path': '/' + BLACKLIST_PATH,
                    'initramfs_blacklist_sha256': blacklist_sha,
                    'modules': sorted(VENUS_MODULES),
                    'rootfs_must_sync_blacklist_before_boot': True,
                    'rootfs_blacklist_path': '/' + BLACKLIST_PATH,
                    'rootfs_blacklist_contents': BLACKLIST_TEXT,
                    'must_not_explicitly_load_until_supervised_manual_test': True,
                },
                'current_boot_backup_sha256': BOOT_BACKUP_SHA256,
                'boot_policy_unchanged': True,
                'rootfs_prep_payloads_deployed': False,
                'rootfs_prep_requires_root_ownership_and_install_modes': True,
                'root_uuid': ROOTFS_UUID,
                'charging_policy': CHARGE_POLICY,
                'boot_partition_limit_bytes': BOOT_PARTITION_LIMIT,
                'venus_dtb_power_opp_verified': True,
                'stock_layout_and_aosp_unpack_verified': True,
                'hardware_boot_verified': False,
                'venus_module_loaded': False,
                'hardware_decode_tested': False,
                'boot_partition_writes': False,
                'device_commands_executed': False,
                'files_sha256': {
                    path.relative_to(stage).as_posix():sha(path)
                    for path in sorted(stage.rglob('*')) if path.is_file()
                },
                'boot': boot,
                'independent_aosp_unpack_sha256': {
                    name: sha(independent / name) for name in ('kernel', 'dtb', 'ramdisk')
                },
            }
            (stage / 'venus-bundle-manifest.json').write_text(
                json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
            if output.exists() or output.is_symlink():
                fail('Output directory appeared during packaging; refusing overwrite')
            stage.rename(output)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
    return output / 'venus-bundle-manifest.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel-artifacts', type=Path, required=True,
                        help='kernel-mode artifact directory produced by scripts/linux/build-kernel.sh')
    parser.add_argument('--baseline-bundle', type=Path, required=True,
                        help='sealed artifacts/slider-builtin-20261006/ready directory')
    parser.add_argument('--boot-backup', type=Path, required=True,
                        help='measured boot_a backup with the fixed SHA-256 in this script')
    parser.add_argument('--firmware-dir', type=Path, required=True,
                        help='explicit current-device firmware copy with manifest.json')
    parser.add_argument('--expected-kernel-commit',
                        help='optional exact local source commit to require from build-info.json')
    parser.add_argument('--output-dir', type=Path, required=True,
                        help='fresh output directory; existing paths are never overwritten')
    args = parser.parse_args()
    manifest = prepare_bundle(args.kernel_artifacts, args.baseline_bundle,
                              args.boot_backup, args.firmware_dir, args.output_dir,
                              args.expected_kernel_commit)
    print('Prepared offline Venus bundle:', manifest)


if __name__ == '__main__':
    main()
