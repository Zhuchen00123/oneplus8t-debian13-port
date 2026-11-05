import importlib.util
import io
import gzip
import lzma
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest


SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location(
    'build_venus_bundle', SCRIPT_DIR / 'build-venus-bundle.py')
venus = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(venus)


def write_tar(path, entries):
    with tarfile.open(path, 'w:gz') as archive:
        for name, kind, payload in entries:
            info = tarfile.TarInfo(name)
            if kind == 'dir':
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            elif kind == 'symlink':
                info.type = tarfile.SYMTYPE
                info.linkname = payload
                archive.addfile(info)
            else:
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))


class SafePathTests(unittest.TestCase):
    def test_decodes_gzip_and_xz_modules_without_changing_elf_bytes(self):
        raw = b'\x7fELF' + b'module-byte-fixture'
        with tempfile.TemporaryDirectory() as directory:
            for suffix, encode in (('.ko.gz', gzip.compress), ('.ko.xz', lzma.compress)):
                with self.subTest(suffix=suffix):
                    path = Path(directory)/('example'+suffix)
                    path.write_bytes(encode(raw))
                    self.assertEqual(venus.module_raw_bytes(path), raw)

    def test_accepts_canonical_relative_posix_path(self):
        self.assertEqual(venus.safe_relative_path('qcom/vpu/vpu20_p4.mbn').as_posix(),
                         'qcom/vpu/vpu20_p4.mbn')

    def test_rejects_paths_that_can_escape_or_change_host_semantics(self):
        for path in ('../escape', '/absolute', 'C:/drive', 'a\\b', 'a/./b', 'a//b'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                venus.safe_relative_path(path)

    def test_requires_the_venus_config_closure_and_existing_device_features(self):
        values = {
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
        config = ''.join(key + '=' + value + '\n' for key, value in values.items())
        venus.require_config(config)
        with self.assertRaises(ValueError):
            venus.require_config(config.replace('CONFIG_KEYBOARD_GPIO=y', 'CONFIG_KEYBOARD_GPIO=m'))


class VenusDtsTests(unittest.TestCase):
    def make_dts(self):
        opps = []
        for index, frequency in enumerate((240000000, 338000000, 366000000, 444000000), 1):
            opps.append(f'''opp-{frequency} {{
                opp-hz = /bits/ 64 <{frequency}>;
                required-opps = <0x{index:x}>, <0x{index + 4:x}>;
            }};''')
        return '''/ {
            video-codec@aa00000 {
                compatible = "qcom,sm8250-venus";
                status = "okay";
                firmware-name = "postmarketos/venus.mbn";
                power-domain-names = "venus", "vcodec0", "mx", "mmcx";
                operating-points-v2 = <0x100>;
                venus-opp-table {
                    compatible = "operating-points-v2";
                    phandle = <0x100>;
                    %s
                };
            };
        };''' % '\n'.join(opps)

    def test_accepts_corrected_four_frequency_table_and_two_power_requirements(self):
        result = venus.validate_venus_dts(self.make_dts())
        self.assertEqual(result['frequencies_hz'], [240000000, 338000000, 366000000, 444000000])
        self.assertEqual(result['power_domain_names'], ['venus', 'vcodec0', 'mx', 'mmcx'])

    def test_accepts_dtc_decompiled_u64_cells(self):
        text = self.make_dts()
        for frequency in (240000000, 338000000, 366000000, 444000000):
            text = text.replace(f'/bits/ 64 <{frequency}>', f'<0x00 {frequency:#x}>')
        result = venus.validate_venus_dts(text)
        self.assertEqual(result['frequencies_hz'], [240000000, 338000000, 366000000, 444000000])

    def test_rejects_old_frequency_table(self):
        dts = self.make_dts().replace('240000000', '720000000')
        with self.assertRaises(ValueError):
            venus.validate_venus_dts(dts)


class ModuleArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.archive = Path(self.temp.name) / 'modules.tar.gz'
        self.root = 'lib/modules/7.2.0-test'

    def test_accepts_regular_files_under_only_the_requested_release(self):
        write_tar(self.archive, [
            (self.root + '/', 'dir', b''),
            (self.root + '/kernel/drivers/media/test.ko.zst', 'file', b'compressed'),
            (self.root + '/modules.builtin', 'file', b'kernel/drivers/base/core.ko\n'),
        ])
        result = venus.inspect_modules_tar(self.archive, '7.2.0-test')
        self.assertEqual(result['modules'], {
            'kernel/drivers/media/test': 'kernel/drivers/media/test.ko.zst',
        })

    def test_rejects_traversal_symlinks_and_other_release_roots(self):
        cases = (
            [(self.root + '/', 'dir', b''), (self.root + '/../escape.ko', 'file', b'x')],
            [(self.root + '/', 'dir', b''), (self.root + '/escape.ko', 'symlink', '../../escape')],
            [('lib/modules/7.2.0-other/x.ko', 'file', b'x')],
        )
        for entries in cases:
            with self.subTest(entries=entries):
                write_tar(self.archive, entries)
                with self.assertRaises(ValueError):
                    venus.inspect_modules_tar(self.archive, '7.2.0-test')

    def test_rejects_a_regular_file_as_an_archive_parent(self):
        write_tar(self.archive, [
            (self.root + '/kernel', 'file', b'not a directory'),
            (self.root + '/kernel/child.ko', 'file', b'x'),
        ])
        with self.assertRaises(ValueError):
            venus.inspect_modules_tar(self.archive, '7.2.0-test')


class FirmwareManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.relative = 'fixture/example.bin'
        self.data = b'offline-firmware-fixture'
        self.path = self.root / self.relative
        self.path.parent.mkdir()
        self.path.write_bytes(self.data)
        self.expected = {self.relative}
        self.manifest_path = self.root / 'manifest.json'
        self.write_manifest()

    def write_manifest(self, relative=None, data=None):
        relative = relative or self.relative
        data = self.data if data is None else data
        self.manifest_path.write_text(json.dumps({
            'files_sha256': {relative: venus.sha_bytes(data)},
            'files_bytes': {relative: len(data)},
        }), encoding='utf-8')

    def test_accepts_manifest_with_matching_regular_file_bytes(self):
        _, _, checked = venus.validate_firmware_manifest(self.root, self.expected)
        self.assertEqual(checked[self.relative]['sha256'], venus.sha_bytes(self.data))

    def test_rejects_manifest_path_traversal_and_modified_bytes(self):
        self.write_manifest('../outside.bin')
        with self.assertRaises(ValueError):
            venus.validate_firmware_manifest(self.root, {'../outside.bin'})
        self.write_manifest()
        self.path.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            venus.validate_firmware_manifest(self.root, self.expected)


if __name__ == '__main__':
    unittest.main()
