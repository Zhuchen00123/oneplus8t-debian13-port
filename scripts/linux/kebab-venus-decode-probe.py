#!/usr/bin/env python3
"""Bounded software/reference and explicit V4L2 H.264 decode probe for kebab.

The hardware decoder is never selected implicitly: pass --hardware to run it.
The default coverage is only 320x240 at 15 fps for 30 frames (2 seconds).
This probe does not establish long-run stability or a direct NV12 client path.
It intentionally performs no loops and never falls back to software if
the requested hardware decoder fails.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO


WIDTH = 320
HEIGHT = 240
FPS = 15
DEFAULT_FRAMES = 30
MAX_FRAMES = 90
FRAME_BYTES_YUV420P = WIDTH * HEIGHT * 3 // 2
COMMAND_TIMEOUT_SECONDS = 15.0
TOTAL_TIMEOUT_SECONDS = 55.0
CLEANUP_RESERVE_SECONDS = 3.0
MAX_LOG_BYTES = 256 * 1024
MAX_SAMPLE_BYTES = 8 * 1024 * 1024
MAX_FRAMEMD5_BYTES = 64 * 1024
INTERRUPT_LABEL_RE = re.compile(r"\bvenus\b|\bvideo(?:[ _-]+)codec\b", re.IGNORECASE)


class ProbeError(Exception):
    """Expected probe failure that should be recorded in the JSON report."""


def parse_framemd5(text: str) -> dict[str, Any]:
    """Parse FFmpeg's framemd5 text format, validating metadata and records."""
    headers: dict[str, str] = {}
    frames: list[dict[str, Any]] = []

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            match = re.match(r"#([^:]+):\s*(.*)$", line)
            if match:
                headers[match.group(1).strip().lower()] = match.group(2).strip()
            continue

        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 6:
            raise ValueError(f"framemd5 line {line_number}: expected 6 CSV fields")
        try:
            stream_index, dts, pts, duration, size = (int(value) for value in fields[:5])
        except ValueError as exc:
            raise ValueError(f"framemd5 line {line_number}: non-integer frame metadata") from exc
        digest = fields[5].lower()
        if not re.fullmatch(r"[0-9a-f]{32}", digest):
            raise ValueError(f"framemd5 line {line_number}: expected a 32-digit MD5")
        if size <= 0:
            raise ValueError(f"framemd5 line {line_number}: frame size must be positive")
        frames.append({
            "stream_index": stream_index,
            "dts": dts,
            "pts": pts,
            "duration": duration,
            "size_bytes": size,
            "md5": digest,
        })

    if headers.get("hash", "").upper() != "MD5":
        raise ValueError("framemd5 header must declare #hash: MD5")
    if not any(key.startswith("dimensions ") for key in headers):
        raise ValueError("framemd5 header is missing #dimensions 0: WIDTHxHEIGHT")
    if not frames:
        raise ValueError("framemd5 contains no frame records")
    return {"headers": headers, "frames": frames}


def _parse_framemd5_file(path: Path) -> dict[str, Any]:
    size = path.stat().st_size
    if size > MAX_FRAMEMD5_BYTES:
        raise ValueError(f"framemd5 exceeds {MAX_FRAMEMD5_BYTES} bytes")
    return parse_framemd5(path.read_text(encoding="ascii"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _drain_limited(
    pipe: BinaryIO,
    destination: Path,
    limit: int,
    process: subprocess.Popen[bytes],
    exceeded: threading.Event,
) -> None:
    saved = 0
    try:
        with destination.open("wb") as output:
            while True:
                chunk = pipe.read(8192)
                if not chunk:
                    break
                remaining = max(0, limit - saved)
                if remaining:
                    accepted = chunk[:remaining]
                    output.write(accepted)
                    saved += len(accepted)
                if len(chunk) > remaining:
                    exceeded.set()
                    try:
                        process.kill()
                    except OSError:
                        pass
            output.flush()
    except OSError:
        exceeded.set()
        try:
            process.kill()
        except OSError:
            pass
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def _run_command(
    argv: list[str],
    work_dir: Path,
    stem: str,
    deadline: float,
) -> dict[str, Any]:
    stdout_path = work_dir / f"{stem}.stdout.log"
    stderr_path = work_dir / f"{stem}.stderr.log"
    result: dict[str, Any] = {
        "argv": argv,
        "returncode": None,
        "started": False,
        "timed_out": False,
        "stdout_limit_exceeded": False,
        "stderr_limit_exceeded": False,
        "stdout_log_path": str(stdout_path),
        "stderr_log_path": str(stderr_path),
        "timeout_seconds": None,
        "duration_seconds": None,
        "error": None,
    }

    remaining = deadline - time.monotonic()
    if remaining <= CLEANUP_RESERVE_SECONDS:
        result["error"] = "overall 55-second deadline reached before command start"
        stdout_path.touch(exist_ok=True)
        stderr_path.touch(exist_ok=True)
        return result

    timeout = min(COMMAND_TIMEOUT_SECONDS, remaining - CLEANUP_RESERVE_SECONDS)
    result["timeout_seconds"] = round(timeout, 3)
    started_at = time.monotonic()
    stdout_exceeded = threading.Event()
    stderr_exceeded = threading.Event()
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except OSError as exc:
        result["error"] = f"could not start command: {exc}"
        stdout_path.write_bytes(b"")
        stderr_path.write_bytes(b"")
        return result

    result["started"] = True
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_thread = threading.Thread(
        target=_drain_limited,
        args=(process.stdout, stdout_path, MAX_LOG_BYTES, process, stdout_exceeded),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_drain_limited,
        args=(process.stderr, stderr_path, MAX_LOG_BYTES, process, stderr_exceeded),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    try:
        result["returncode"] = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        result["timed_out"] = True
        try:
            process.kill()
        except OSError:
            pass
        try:
            result["returncode"] = process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            result["error"] = "process did not exit after kill"

    stdout_thread.join(timeout=0.5)
    stderr_thread.join(timeout=0.5)
    if stdout_thread.is_alive() or stderr_thread.is_alive():
        try:
            process.kill()
        except OSError:
            pass
        stdout_thread.join(timeout=0.25)
        stderr_thread.join(timeout=0.25)
        result["error"] = "output drain did not finish after process exit"

    result["stdout_limit_exceeded"] = stdout_exceeded.is_set()
    result["stderr_limit_exceeded"] = stderr_exceeded.is_set()
    if result["stdout_limit_exceeded"] or result["stderr_limit_exceeded"]:
        result["error"] = "stdout/stderr exceeded the 256 KiB per-stream cap"
    result["duration_seconds"] = round(time.monotonic() - started_at, 3)
    return result


def _decoder_evidence(log_path: Path, expected: str) -> dict[str, Any]:
    try:
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"expected_decoder": expected, "mapping_confirmed": False, "mapping_lines": [], "error": str(exc)}

    mapping_lines = [
        line.strip()
        for line in log_text.splitlines()
        if " -> " in line and ("h264" in line.lower() or "rawvideo" in line.lower())
    ][:12]
    if expected == "h264_v4l2m2m":
        confirmed = any(re.search(r"h264\s*\(h264_v4l2m2m\)", line, re.IGNORECASE) for line in mapping_lines)
    else:
        confirmed = any(re.search(r"h264\s*\(native\)", line, re.IGNORECASE) for line in mapping_lines)
    return {
        "expected_decoder": expected,
        "mapping_confirmed": confirmed,
        "mapping_lines": mapping_lines,
        "log_path": str(log_path),
    }


def _venus_device_binding(log_path: Path) -> dict[str, Any]:
    """Tie FFmpeg's selected device to the actual Venus decoder platform driver."""
    text = log_path.read_text(encoding="utf-8", errors="replace")
    nodes = sorted(set(re.findall(r"Using device\s+(/dev/video[0-9]+)\b", text)))
    entries = []
    for node in nodes:
        entry = Path('/sys/class/video4linux')/Path(node).name
        device = entry/'device'
        driver, module = device/'driver', device/'driver/module'
        driver_name = driver.resolve().name if driver.is_symlink() else None
        module_name = module.resolve().name if module.is_symlink() else None
        try:
            card_name = (entry/'name').read_text().strip()
        except OSError:
            card_name = None
        known_binding = (driver_name, module_name) in {
            ('qcom-venus', 'venus_core'), ('qcom-venus-decoder', 'venus_dec')}
        entries.append({'node':node, 'device_path':str(device.resolve()),
                        'driver':driver_name, 'module':module_name, 'card_name':card_name,
                        'venus_decoder':known_binding and card_name == 'qcom-venus-decoder'})
    return {'confirmed':len(entries) == 1 and entries[0]['venus_decoder'], 'entries':entries,
            'method':'FFmpeg selected-device log plus sysfs platform driver/module binding',
            'limitations':'M2M capabilities/formats and firmware startup must also be captured in the trial preflight.'}


def _read_venus_interrupts(path: Path = Path("/proc/interrupts")) -> dict[str, Any]:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"available": False, "path": str(path), "entries": [], "error": str(exc)}

    entries: list[dict[str, Any]] = []
    for raw_line in content.splitlines():
        if not INTERRUPT_LABEL_RE.search(raw_line):
            continue
        if ":" not in raw_line:
            continue
        irq, remainder = raw_line.split(":", 1)
        fields = remainder.split()
        counters: list[int] = []
        for field in fields:
            if not field.isdigit():
                break
            counters.append(int(field))
        label = " ".join(fields[len(counters):])
        entries.append({
            "key": f"{irq.strip()}:{label}",
            "irq": irq.strip(),
            "label": label,
            "per_cpu_counts": counters,
            "total_count": sum(counters),
        })
    return {"available": True, "path": str(path), "entries": entries}


def _interrupt_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    if not before.get("available") or not after.get("available"):
        return {
            "available": False,
            "reason": "one or both /proc/interrupts snapshots could not be read",
            "before": before,
            "after": after,
            "entries": [],
        }

    before_by_key = {entry["key"]: entry for entry in before["entries"]}
    after_by_key = {entry["key"]: entry for entry in after["entries"]}
    deltas: list[dict[str, Any]] = []
    for key in sorted(set(before_by_key) | set(after_by_key)):
        old = before_by_key.get(key)
        new = after_by_key.get(key)
        old_counts = old["per_cpu_counts"] if old else []
        new_counts = new["per_cpu_counts"] if new else []
        width = max(len(old_counts), len(new_counts))
        old_padded = old_counts + [0] * (width - len(old_counts))
        new_padded = new_counts + [0] * (width - len(new_counts))
        deltas.append({
            "key": key,
            "before_per_cpu_counts": old_counts,
            "after_per_cpu_counts": new_counts,
            "per_cpu_delta": [new_padded[index] - old_padded[index] for index in range(width)],
            "total_delta": sum(new_padded) - sum(old_padded),
            "appeared_after_start": old is None,
            "disappeared_after_start": new is None,
        })
    return {
        "available": True,
        "before": before,
        "after": after,
        "entries": deltas,
        "limitations": (
            "Label matches are auxiliary only. IRQs may be shared or triggered by other paths; "
            "a delta does not prove this decode used the VPU, and no delta does not prove it did not. "
            "No video device node name is treated as proof of a VPU."
        ),
    }


def _framemd5_summary(path: Path, expected_frames: int) -> dict[str, Any]:
    try:
        parsed = _parse_framemd5_file(path)
        headers = parsed["headers"]
        frames = parsed["frames"]
        dimensions = next((value for key, value in headers.items() if key.startswith("dimensions ")), None)
        return {
            "available": True,
            "path": str(path),
            "frame_count": len(frames),
            "expected_frame_count": expected_frames,
            "frame_count_matches": len(frames) == expected_frames,
            "frame_size_bytes": sorted({frame["size_bytes"] for frame in frames}),
            "dimensions": dimensions,
            "hash_algorithm": headers.get("hash"),
            "codec_id": next((value for key, value in headers.items() if key.startswith("codec_id ")), None),
            "frames": frames,
            "error": None,
        }
    except (OSError, UnicodeError, ValueError) as exc:
        return {
            "available": False,
            "path": str(path),
            "frame_count": 0,
            "expected_frame_count": expected_frames,
            "frame_count_matches": False,
            "frame_size_bytes": [],
            "dimensions": None,
            "hash_algorithm": None,
            "codec_id": None,
            "frames": [],
            "error": str(exc),
        }


def _decode_command(
    ffmpeg: str,
    sample_path: Path,
    manifest_path: Path,
    decoder: str,
    frames: int,
) -> list[str]:
    return [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "verbose",
        "-filter_threads",
        "1",
        "-threads",
        "1",
        "-c:v",
        decoder,
        "-i",
        str(sample_path),
        "-map",
        "0:v:0",
        "-frames:v",
        str(frames),
        "-fps_mode",
        "passthrough",
        "-an",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "rawvideo",
        "-threads",
        "1",
        "-fs",
        str(MAX_FRAMEMD5_BYTES),
        "-f",
        "framemd5",
        str(manifest_path),
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True, type=Path, help="empty or new directory for sample, logs, and JSON report")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--hardware", action="store_true", help="explicitly run h264_v4l2m2m after software reference decode")
    mode.add_argument("--software-only", action="store_true", help="create the sample and software reference without touching hardware")
    parser.add_argument("--frames", type=int, default=DEFAULT_FRAMES, help=f"frame count at {FPS} fps, 1..{MAX_FRAMES}; default {DEFAULT_FRAMES}")
    parser.add_argument("--resolution", choices=("320x240", "1280x720"), default="320x240")
    parser.add_argument("--fps", type=int, choices=(15, 30), default=15)
    return parser


def _prepare_work_dir(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.exists() and not resolved.is_dir():
        raise ProbeError(f"work directory path exists and is not a directory: {resolved}")
    if resolved.exists() and any(resolved.iterdir()):
        raise ProbeError(f"refusing to overwrite a non-empty work directory: {resolved}")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _make_report(args: argparse.Namespace, work_dir: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "probe": "kebab-venus-decode-probe",
        "mode": "hardware" if args.hardware else "software_only",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "finished_utc": None,
        "work_dir": str(work_dir),
        "ffmpeg_path": None,
        "coverage": {
            "width": WIDTH,
            "height": HEIGHT,
            "fps": FPS,
            "requested_frames": args.frames,
            "duration_seconds": round(args.frames / FPS, 6),
            "normalized_pixel_format": "yuv420p",
            "loops": 1,
            "automatic_frame_expansion": False,
        },
        "limits": {
            "per_command_timeout_seconds": COMMAND_TIMEOUT_SECONDS,
            "overall_deadline_seconds": TOTAL_TIMEOUT_SECONDS,
            "stdout_and_stderr_cap_bytes_each": MAX_LOG_BYTES,
            "sample_cap_bytes": MAX_SAMPLE_BYTES,
            "framemd5_cap_bytes": MAX_FRAMEMD5_BYTES,
        },
        "sample": {},
        "software_decode": {"status": "not_started"},
        "hardware_decode": {"status": "not_requested" if args.software_only else "not_started"},
        "comparison": {"status": "not_performed", "pixel_bytes_identical_by_framemd5": None},
        "venus_interrupts": {"status": "not_requested" if args.software_only else "not_sampled", "delta": None},
        "commands": [],
        "limitations": [
            f"A passing result covers only this single {WIDTH}x{HEIGHT}, {FPS} fps, yuv420p-normalized run and the requested frame count; it is not a long-run stability claim.",
            "This does not exercise a direct NV12 consumer or a GStreamer pipeline; it cannot establish behavior of older firmware blobs or reproduce a historical crash by itself.",
            "framemd5 compares MD5 hashes of each normalized yuv420p frame. If hashes differ, the exact mismatching frame indices and per-frame hashes are reported; this probe does not report per-pixel coordinates.",
            "The /proc/interrupts label delta is auxiliary evidence only; it cannot independently prove that a VPU performed the decode.",
        ],
        "failure_reasons": [],
        "success": False,
    }


def _add_command(report: dict[str, Any], stage: str, result: dict[str, Any]) -> None:
    report["commands"].append({"stage": stage, **result})


def _command_ok(result: dict[str, Any]) -> bool:
    return (
        result.get("started")
        and result.get("returncode") == 0
        and not result.get("timed_out")
        and not result.get("stdout_limit_exceeded")
        and not result.get("stderr_limit_exceeded")
        and result.get("error") is None
    )


def _write_report(work_dir: Path, report: dict[str, Any]) -> None:
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    report_path = work_dir / "report.json"
    with report_path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")


def main(argv: list[str] | None = None) -> int:
    global WIDTH, HEIGHT, FPS, FRAME_BYTES_YUV420P
    parser = _parser()
    args = parser.parse_args(argv)
    WIDTH, HEIGHT = (int(value) for value in args.resolution.split('x'))
    FPS = args.fps
    FRAME_BYTES_YUV420P = WIDTH * HEIGHT * 3 // 2
    if not 1 <= args.frames <= MAX_FRAMES:
        parser.error(f"--frames must be between 1 and {MAX_FRAMES}")

    try:
        work_dir = _prepare_work_dir(args.work_dir)
    except ProbeError as exc:
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    report = _make_report(args, work_dir)
    deadline = time.monotonic() + TOTAL_TIMEOUT_SECONDS
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        report["failure_reasons"].append("ffmpeg was not found on PATH; no download or installation was attempted")
        report["sample"] = {"status": "not_generated", "input_sha256": None}
        _write_report(work_dir, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    report["ffmpeg_path"] = ffmpeg

    # Give V4L2 explicit packet PTS. A raw Annex-B stream can arrive with all
    # timestamps zero, making CFR output drop frames and invalidating the test.
    sample_path = work_dir / "testsrc2-baseline.mp4"
    generation_command = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "verbose",
        "-filter_threads",
        "1",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={WIDTH}x{HEIGHT}:rate={FPS}",
        "-frames:v",
        str(args.frames),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-profile:v",
        "baseline",
        "-pix_fmt",
        "yuv420p",
        "-threads",
        "1",
        "-fs",
        str(MAX_SAMPLE_BYTES),
        "-f",
        "mp4",
        str(sample_path),
    ]
    generation = _run_command(generation_command, work_dir, "generate", deadline)
    _add_command(report, "generate_h264_baseline_testsrc2", generation)
    sample_exists = sample_path.is_file()
    sample_size = sample_path.stat().st_size if sample_exists else 0
    sample_ok = _command_ok(generation) and 0 < sample_size <= MAX_SAMPLE_BYTES
    report["sample"] = {
        "status": "ready" if sample_ok else "generation_failed",
        "path": str(sample_path),
        "size_bytes": sample_size,
        "sha256": _sha256_file(sample_path) if sample_exists and sample_size > 0 else None,
        "expected_codec": "H.264 Constrained Baseline",
        "expected_pixel_format": "yuv420p",
        "expected_frames": args.frames,
        "frames": None,
        "frame_count_note": "MP4 carries packet timestamps; complete decoded frame count is confirmed by the software framemd5 output.",
        "generation_command_stage": "generate_h264_baseline_testsrc2",
    }
    if not sample_ok:
        report["failure_reasons"].append("testsrc2 H.264 baseline generation failed, timed out, exceeded an output cap, or produced an invalid-size file")

    software_manifest = work_dir / "software-reference.framemd5"
    software_summary: dict[str, Any] = {"status": "skipped", "framemd5_path": str(software_manifest)}
    if sample_ok and time.monotonic() < deadline:
        software_command = _decode_command(ffmpeg, sample_path, software_manifest, "h264", args.frames)
        software_run = _run_command(software_command, work_dir, "software-decode", deadline)
        _add_command(report, "software_reference_decode", software_run)
        software_manifest_summary = _framemd5_summary(software_manifest, args.frames)
        software_evidence = _decoder_evidence(Path(software_run["stderr_log_path"]), "native")
        software_valid = (
            _command_ok(software_run)
            and software_manifest_summary["available"]
            and software_manifest_summary["frame_count_matches"]
            and software_manifest_summary["dimensions"] == f"{WIDTH}x{HEIGHT}"
            and software_manifest_summary["hash_algorithm"] == "MD5"
            and software_manifest_summary["codec_id"] == "rawvideo"
            and all(frame["stream_index"] == 0 for frame in software_manifest_summary["frames"])
            and all(size == FRAME_BYTES_YUV420P for size in software_manifest_summary["frame_size_bytes"])
            and software_evidence["mapping_confirmed"]
        )
        software_summary = {
            "status": "passed" if software_valid else "failed_or_inconclusive",
            "decoder": "h264 (FFmpeg native software decoder)",
            "decoder_evidence": software_evidence,
            "framemd5": software_manifest_summary,
            "command_stage": "software_reference_decode",
        }
        if not software_valid:
            report["failure_reasons"].append(f"software reference decode did not produce the expected {args.frames} normalized frames and native decoder mapping")
    elif sample_ok:
        software_summary = {"status": "skipped", "framemd5_path": str(software_manifest), "reason": "overall deadline reached before decode"}
        report["failure_reasons"].append("overall 55-second deadline reached before software reference decode")
    else:
        software_summary = {"status": "skipped", "framemd5_path": str(software_manifest), "reason": "sample generation failed"}
    report["software_decode"] = software_summary

    if args.hardware:
        irq_before = _read_venus_interrupts()
        report["venus_interrupts"]["status"] = "sampled" if irq_before.get("available") else "unavailable"
        report["venus_interrupts"]["before"] = irq_before
        hardware_manifest = work_dir / "hardware-h264-v4l2m2m.framemd5"
        if sample_ok and software_summary.get("status") == "passed" and time.monotonic() < deadline:
            hardware_command = _decode_command(ffmpeg, sample_path, hardware_manifest, "h264_v4l2m2m", args.frames)
            hardware_run = _run_command(hardware_command, work_dir, "hardware-decode", deadline)
            _add_command(report, "explicit_h264_v4l2m2m_decode", hardware_run)
            hardware_manifest_summary = _framemd5_summary(hardware_manifest, args.frames)
            hardware_evidence = _decoder_evidence(Path(hardware_run["stderr_log_path"]), "h264_v4l2m2m")
            hardware_binding = _venus_device_binding(Path(hardware_run["stderr_log_path"]))
            hardware_valid = (
                _command_ok(hardware_run)
                and hardware_manifest_summary["available"]
                and hardware_manifest_summary["frame_count_matches"]
                and hardware_manifest_summary["dimensions"] == f"{WIDTH}x{HEIGHT}"
                and hardware_manifest_summary["hash_algorithm"] == "MD5"
                and hardware_manifest_summary["codec_id"] == "rawvideo"
                and all(frame["stream_index"] == 0 for frame in hardware_manifest_summary["frames"])
                and all(size == FRAME_BYTES_YUV420P for size in hardware_manifest_summary["frame_size_bytes"])
                and hardware_evidence["mapping_confirmed"]
                and hardware_binding['confirmed']
            )
            report["hardware_decode"] = {
                "status": "passed" if hardware_valid else "failed_or_inconclusive",
                "decoder": "h264_v4l2m2m (explicit; software fallback prohibited)",
                "decoder_evidence": hardware_evidence,
                "venus_device_binding":hardware_binding,
                "framemd5": hardware_manifest_summary,
                "command_stage": "explicit_h264_v4l2m2m_decode",
            }
            if not hardware_valid:
                report["failure_reasons"].append("explicit h264_v4l2m2m decode failed, timed out, lacked decoder/device-binding evidence, or produced an invalid frame manifest; no software fallback was used")
        else:
            report["hardware_decode"] = {
                "status": "skipped",
                "decoder": "h264_v4l2m2m (explicit; software fallback prohibited)",
                "framemd5_path": str(hardware_manifest),
                "reason": "sample/reference validation failed or overall deadline reached",
            }
            report["failure_reasons"].append("hardware decode could not start because the sample/reference was invalid or the overall deadline was reached")
        irq_after = _read_venus_interrupts()
        report["venus_interrupts"]["after"] = irq_after
        report["venus_interrupts"]["delta"] = _interrupt_delta(irq_before, irq_after)

        software_frames = software_summary.get("framemd5", {}).get("frames", [])
        hardware_frames = report["hardware_decode"].get("framemd5", {}).get("frames", [])
        if len(software_frames) == args.frames and len(hardware_frames) == args.frames:
            mismatches = []
            for index, (software_frame, hardware_frame) in enumerate(zip(software_frames, hardware_frames), start=1):
                if software_frame["md5"] != hardware_frame["md5"]:
                    mismatches.append({
                        "frame_number": index,
                        "software_md5": software_frame["md5"],
                        "hardware_md5": hardware_frame["md5"],
                        "software_frame_size_bytes": software_frame["size_bytes"],
                        "hardware_frame_size_bytes": hardware_frame["size_bytes"],
                    })
            identical = len(mismatches) == 0
            report["comparison"] = {
                "status": "identical" if identical else "different",
                "method": "FFmpeg framemd5, per-frame MD5 over yuv420p output",
                "pixel_format_for_both": "yuv420p",
                "pixel_bytes_identical_by_framemd5": identical,
                "software_frame_count": len(software_frames),
                "hardware_frame_count": len(hardware_frames),
                "matched_frames": args.frames - len(mismatches),
                "mismatched_frames": mismatches,
                "note": "An NV12 decoder output, if present, is converted by FFmpeg to requested yuv420p before hashing. Differences are reported per frame; no success is inferred when hashes differ.",
            }
            if not identical:
                report["failure_reasons"].append("software and hardware normalized yuv420p frame hashes differ")
        else:
            report["comparison"] = {
                "status": "not_comparable",
                "method": "FFmpeg framemd5, per-frame MD5 over yuv420p output",
                "pixel_format_for_both": "yuv420p",
                "pixel_bytes_identical_by_framemd5": None,
                "software_frame_count": len(software_frames),
                "hardware_frame_count": len(hardware_frames),
                "expected_frame_count": args.frames,
                "mismatched_frames": [],
            }
            report["failure_reasons"].append("software and hardware outputs did not both contain the requested frame count")
    else:
        report["hardware_decode"] = {"status": "not_requested", "reason": "--software-only was selected; no hardware decoder command or interrupt sample ran"}
        report["comparison"] = {"status": "not_performed", "pixel_bytes_identical_by_framemd5": None}

    software_passed = report["software_decode"].get("status") == "passed"
    if args.software_only:
        report["success"] = sample_ok and software_passed
    else:
        report["success"] = (
            sample_ok
            and software_passed
            and report["hardware_decode"].get("status") == "passed"
            and report["comparison"].get("pixel_bytes_identical_by_framemd5") is True
        )
    _write_report(work_dir, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
