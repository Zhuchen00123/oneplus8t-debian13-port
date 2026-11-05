#!/usr/bin/env python3
"""Unit tests for safe decoder selection and Venus device binding."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock


PROBE_PATH = Path(__file__).with_name("kebab-venus-decode-probe.py")
SPEC = importlib.util.spec_from_file_location("kebab_venus_decode_probe", PROBE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load probe module from {PROBE_PATH}")
PROBE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PROBE
SPEC.loader.exec_module(PROBE)


class _FakeLog:
    def __init__(self, text: str) -> None:
        self.text = text

    def read_text(self, encoding: str = "utf-8", errors: str = "strict") -> str:
        return self.text


class _FakeSysfsPath:
    symlinks: dict[str, str] = {}
    files: dict[str, str] = {}

    def __init__(self, value: str | Path) -> None:
        self.value = str(PurePosixPath(str(value)))

    @property
    def name(self) -> str:
        return PurePosixPath(self.value).name

    def __truediv__(self, child: str | Path) -> "_FakeSysfsPath":
        return _FakeSysfsPath(str(PurePosixPath(self.value) / str(child)))

    def __str__(self) -> str:
        return self.value

    def is_symlink(self) -> bool:
        return self.value in self.symlinks

    def resolve(self) -> "_FakeSysfsPath":
        return _FakeSysfsPath(self.symlinks.get(self.value, self.value))

    def read_text(self, encoding: str = "utf-8", errors: str = "strict") -> str:
        if self.value not in self.files:
            raise OSError(f"mock sysfs file not found: {self.value}")
        return self.files[self.value]


class VenusBindingTests(unittest.TestCase):
    def _binding(
        self,
        log_text: str,
        devices: dict[str, tuple[str, str, str]] | None = None,
    ) -> dict:
        """Run the binding helper against mocked sysfs; no Windows symlinks used."""
        _FakeSysfsPath.symlinks = {}
        _FakeSysfsPath.files = {}
        for node, (driver, module, card) in (devices or {}).items():
            base = f"/sys/class/video4linux/{node}"
            _FakeSysfsPath.symlinks[f"{base}/device/driver"] = f"/sys/bus/platform/drivers/{driver}"
            _FakeSysfsPath.symlinks[f"{base}/device/driver/module"] = f"/sys/module/{module}"
            _FakeSysfsPath.files[f"{base}/name"] = card

        with mock.patch.object(PROBE, "Path", _FakeSysfsPath):
            return PROBE._venus_device_binding(_FakeLog(log_text))

    def test_confirms_supported_venus_core_parent_binding(self) -> None:
        result = self._binding(
            "[h264_v4l2m2m] Using device /dev/video42\n",
            {"video42": ("qcom-venus", "venus_core", "qcom-venus-decoder\n")},
        )
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["entries"][0]["node"], "/dev/video42")
        self.assertEqual(result["entries"][0]["driver"], "qcom-venus")
        self.assertEqual(result["entries"][0]["module"], "venus_core")

    def test_confirms_supported_venus_decoder_binding(self) -> None:
        result = self._binding(
            "Using device /dev/video7\n",
            {"video7": ("qcom-venus-decoder", "venus_dec", "qcom-venus-decoder\n")},
        )
        self.assertTrue(result["confirmed"])

    def test_missing_using_device_is_not_confirmed(self) -> None:
        result = self._binding("decoder initialized\n")
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["entries"], [])

    def test_wrong_driver_or_module_is_not_confirmed(self) -> None:
        cases = [
            ("v4l2loopback", "venus_core", "qcom-venus-decoder"),
            ("qcom-venus", "other_module", "qcom-venus-decoder"),
        ]
        for driver, module, card in cases:
            with self.subTest(driver=driver, module=module):
                result = self._binding(
                    "Using device /dev/video3\n",
                    {"video3": (driver, module, card)},
                )
                self.assertFalse(result["confirmed"])

    def test_wrong_card_name_is_not_confirmed(self) -> None:
        result = self._binding(
            "Using device /dev/video3\n",
            {"video3": ("qcom-venus", "venus_core", "qcom-venus-vpu\n")},
        )
        self.assertFalse(result["confirmed"])

    def test_multiple_selected_video_nodes_are_not_confirmed(self) -> None:
        result = self._binding(
            "Using device /dev/video3\nUsing device /dev/video4\n",
            {
                "video3": ("qcom-venus", "venus_core", "qcom-venus-decoder\n"),
                "video4": ("qcom-venus-decoder", "venus_dec", "qcom-venus-decoder\n"),
            },
        )
        self.assertFalse(result["confirmed"])
        self.assertEqual(len(result["entries"]), 2)


class DecodeCommandAndParserTests(unittest.TestCase):
    def test_decoder_selection_precedes_input_and_has_no_implicit_fallback(self) -> None:
        sample = Path("sample.h264")
        manifest = Path("out.framemd5")
        for decoder in ("h264", "h264_v4l2m2m"):
            with self.subTest(decoder=decoder):
                command = PROBE._decode_command("ffmpeg", sample, manifest, decoder, 30)
                input_codec_index = command.index("-c:v")
                input_decoder_index = input_codec_index + 1
                input_index = command.index("-i")
                self.assertLess(input_decoder_index, input_index)
                self.assertEqual(command[input_decoder_index], decoder)
                self.assertEqual(command.count("-threads"), 2)
                self.assertIn("-filter_threads", command)
                self.assertEqual(command[command.index("-pix_fmt") + 1], "yuv420p")
                self.assertEqual(command[command.index("-fps_mode") + 1], "passthrough")
                output_codec_index = command.index("-c:v", input_index)
                self.assertEqual(command[output_codec_index + 1], "rawvideo")
                self.assertEqual(command[-2], "framemd5")

        hardware = PROBE._decode_command("ffmpeg", sample, manifest, "h264_v4l2m2m", 30)
        self.assertEqual(hardware.count("h264_v4l2m2m"), 1)

    def test_framemd5_parser_rejects_malformed_frame(self) -> None:
        header = (
            "#format: frame checksums\n"
            "#version: 2\n"
            "#hash: MD5\n"
            "#codec_id 0: rawvideo\n"
            "#dimensions 0: 320x240\n"
        )
        good = header + "0, 0, 0, 1, 115200, 5d41402abc4b2a76b9719d911017c592\n"
        parsed = PROBE.parse_framemd5(good)
        self.assertEqual(len(parsed["frames"]), 1)
        self.assertEqual(parsed["frames"][0]["size_bytes"], 115200)

        malformed = header + "0, 0, 0, 1, 115200, not-an-md5\n"
        with self.assertRaises(ValueError):
            PROBE.parse_framemd5(malformed)

    def test_software_reference_failure_never_dispatches_hardware(self) -> None:
        repo_root = PROBE_PATH.parents[2]
        with tempfile.TemporaryDirectory(prefix=".venus-probe-test-", dir=repo_root) as temp_dir:
            work_dir = Path(temp_dir) / "run"
            called_stems: list[str] = []

            def fake_run_command(argv, command_dir, stem, deadline):
                called_stems.append(stem)
                stdout_path = command_dir / f"{stem}.stdout.log"
                stderr_path = command_dir / f"{stem}.stderr.log"
                stdout_path.write_bytes(b"")
                stderr_path.write_bytes(b"")
                if stem == "generate":
                    (command_dir / "testsrc2-baseline.mp4").write_bytes(b"mock h264 sample")
                return {
                    "argv": argv,
                    "returncode": 1 if stem == "software-decode" else 0,
                    "started": True,
                    "timed_out": False,
                    "stdout_limit_exceeded": False,
                    "stderr_limit_exceeded": False,
                    "stdout_log_path": str(stdout_path),
                    "stderr_log_path": str(stderr_path),
                    "timeout_seconds": 1.0,
                    "duration_seconds": 0.01,
                    "error": None,
                }

            stdout = io.StringIO()
            with (
                mock.patch.object(PROBE.shutil, "which", return_value="ffmpeg"),
                mock.patch.object(PROBE, "_run_command", side_effect=fake_run_command),
                mock.patch.object(PROBE, "_read_venus_interrupts", return_value={"available": True, "entries": []}),
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = PROBE.main(["--work-dir", str(work_dir), "--hardware"])

            self.assertEqual(exit_code, 1)
            self.assertEqual(called_stems, ["generate", "software-decode"])
            report = json.loads((work_dir / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["software_decode"]["status"], "failed_or_inconclusive")
            self.assertEqual(report["hardware_decode"]["status"], "skipped")
            self.assertNotIn("explicit_h264_v4l2m2m_decode", [entry["stage"] for entry in report["commands"]])
            self.assertIn('"success": false', stdout.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
