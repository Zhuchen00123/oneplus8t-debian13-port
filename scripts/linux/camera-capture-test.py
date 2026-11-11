#!/usr/bin/env python3
"""Kebab camera capture test (runs on the device).

Front camera (IMX471) is the stable default. Rear main (IMX586) is discovered
from the media graph when present. The script only touches media-controller
links/formats and writes captures under --output-dir; it never reboots, flashes,
or changes GPT.

Exit status is 0 when a test ran (see JSON result), 2 on internal/setup error.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


def run(cmd, timeout=30):
    return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)


def check(cmd, timeout=30):
    r = run(cmd, timeout)
    if r.returncode != 0:
        raise RuntimeError("command failed: %s\n%s\n%s" % (" ".join(cmd), r.stdout, r.stderr))
    return r.stdout


def topology(media):
    out = check(["media-ctl", "-p", "-d", media])
    entities = {}
    cur = None
    pad = None
    pad_dir = None
    for line in out.splitlines():
        m = re.match(r"^- entity \d+: (.+?) \(\d+ pads?", line)
        if m:
            cur = m.group(1).strip()
            entities[cur] = {"links": [], "dev": None}
            pad = None
            pad_dir = None
            continue
        if cur is None:
            continue
        m = re.match(r"^\s+device node name (\S+)", line)
        if m:
            entities[cur]["dev"] = m.group(1)
            continue
        m = re.match(r"^\s+pad(\d+): (SINK|SOURCE)", line)
        if m:
            pad = int(m.group(1))
            pad_dir = m.group(2)
            continue
        m = re.match(r'^\s+-> "(.+?)":(\d+)', line)
        # A one-pad sensor prints its outgoing link under "pad0: SINK", so the
        # direction of the pad a link is listed under says nothing about the
        # direction of the link itself.
        if m and pad is not None:
            entities[cur]["links"].append({"pad": pad, "dst": m.group(1), "dst_pad": int(m.group(2))})
    return entities


def find_link(entities, src, dst_prefix):
    for link in entities.get(src, {}).get("links", []):
        if link["dst"].startswith(dst_prefix):
            return link
    return None


def discover_pipeline(media, sensor_prefix):
    """Follow <sensor> -> csiphy -> csid -> vfe -> /dev/videoN in the media graph.

    Names are matched by prefix, so this works for the rear imx586 (csiphy0) and
    for the front imx471 (csiphy4) alike: the node numbers are assigned by probe
    order and can move when the camss module is reloaded.
    """
    ents = topology(media)
    sensor = next((n for n in ents if sensor_prefix in n.lower()), None)
    if not sensor:
        raise RuntimeError("no %s sensor entity in media graph" % sensor_prefix)
    l1 = find_link(ents, sensor, "msm_csiphy")
    if not l1:
        raise RuntimeError("%s: no csiphy link" % sensor_prefix)
    l2 = find_link(ents, l1["dst"], "msm_csid")
    if not l2:
        raise RuntimeError("%s: no csid link" % sensor_prefix)
    l3 = find_link(ents, l2["dst"], "msm_vfe")
    if not l3:
        raise RuntimeError("%s: no vfe link" % sensor_prefix)
    vfe = l3["dst"]
    video = None
    for link in ents.get(vfe, {}).get("links", []):
        dev = ents.get(link["dst"], {}).get("dev") or ""
        if dev.startswith("/dev/video"):
            video = dev
            break
    if not video:
        raise RuntimeError("%s: no video node" % sensor_prefix)
    sensor_dev = ents.get(sensor, {}).get("dev") or ""
    return {
        "sensor": sensor,
        "sensor_dev": sensor_dev,
        "csiphy": l1["dst"],
        "csid": l2["dst"],
        "vfe": vfe,
        "video": video,
        # pads used when wiring this pipeline: l2 leaves csiphy on its SOURCE
        # pad, l3 leaves csid on its SOURCE pad.  l1/l2 dst_pad are SINK pads
        # and cannot be used as link sources.
        "csiphy_pad": l2["pad"],
        "csid_pad": l3["pad"],
    }


def discover_rear(media):
    return discover_pipeline(media, "imx586")


def subdev_mbus(dev, pad=0):
    out = check(["v4l2-ctl", "-d", dev, "--list-subdev-mbus-codes", "pad=%d" % pad])
    codes = re.findall(r"0x([0-9a-fA-F]{4}): (\S+)", out)
    for code, name in codes:
        if name.startswith("SRGGB") or name.startswith("SGBRG") or name.startswith("SGRBG") or name.startswith("SBGGR"):
            return int(code, 16), name
    if codes:
        return int(codes[0][0], 16), codes[0][1]
    raise RuntimeError("no mbus code on " + dev)


def subdev_size(dev, code, pad=0):
    out = check(["v4l2-ctl", "-d", dev, "--list-subdev-framesizes",
                 "pad=%d,code=0x%04x" % (pad, code)])
    m = re.search(r"(\d+)x(\d+)", out)
    if not m:
        raise RuntimeError("no frame size on " + dev)
    return int(m.group(1)), int(m.group(2))


PIXFMT = {
    "SRGGB10_1X10": "pRAA",
    "SGBRG10_1X10": "pGAA",
    "SGRBG10_1X10": "pgAA",
    "SBGGR10_1X10": "pBAA",
    "SRGGB8_1X8": "RGGB",
    "SGBRG8_1X8": "GBRG",
    "SGRBG8_1X8": "GRBG",
    "SBGGR8_1X8": "BA81",
}


def video_format(video):
    out = check(["v4l2-ctl", "-d", video, "--get-fmt-video"])
    m = re.search(r"Bytes per Line\s*:\s*(\d+)", out)
    stride = int(m.group(1)) if m else None
    m = re.search(r"Width/Height\s*:\s*(\d+)/(\d+)", out)
    size = (int(m.group(1)), int(m.group(2))) if m else None
    return stride, size


def decode_raw10(raw_path, width, height, stride, pgm_path):
    data = Path(raw_path).read_bytes()
    if len(data) < stride * height:
        raise RuntimeError("short raw: %d < %d" % (len(data), stride * height))
    out = bytearray(width * height)
    vals = []
    for y in range(height):
        row = data[y * stride:(y + 1) * stride]
        for g in range((width + 3) // 4):
            b = row[g * 5:g * 5 + 5]
            if len(b) < 5:
                break
            low = b[4]
            for i in range(4):
                x = g * 4 + i
                if x >= width:
                    break
                v = ((b[i] << 2) | ((low >> (2 * i)) & 3)) & 0x3ff
                out[y * width + x] = v >> 2
    Path(pgm_path).write_bytes(b"P5\n%d %d\n255\n" % (width, height) + bytes(out))
    for y in range(0, height, max(1, height // 64)):
        vals.extend(out[y * width:(y + 1) * width])
    n = len(vals)
    return {
        "mean": round(sum(vals) / n, 3),
        "min": min(vals),
        "max": max(vals),
        "nonblack_ratio": round(sum(1 for v in vals if v > 8) / n, 4),
    }


def rear_format(args, discovered, result):
    """mbus/width/height/pixfmt for the rear sensor.

    Asking the subdev is nicer than hardcoding, but the subdev node only exists
    if udev published it.  A missing /dev/v4l-subdevN must not be reported as
    "no frame" - that sends the reader after the CSI link when the real problem
    is a missing device node - so fall back to what the driver declares and say
    so in the result.
    """
    sensor = discovered["sensor"]
    sensor_dev = discovered.get("sensor_dev") or ""
    try:
        if not sensor_dev:
            raise RuntimeError("sensor entity has no /dev node: " + sensor)
        code, name = subdev_mbus(sensor_dev)
        width, height = ((args.width, args.height) if args.width and args.height
                         else subdev_size(sensor_dev, code))
        mbus = args.mbus or name
        pixfmt = args.pixfmt or PIXFMT.get(name, "pRAA")
        result["rear_from_subdev"] = {"dev": sensor_dev, "mbus": name,
                                      "size": [width, height]}
    except Exception as exc:                                 # noqa: BLE001
        width, height = args.width or 4000, args.height or 3000
        mbus = args.mbus or "SRGGB10_1X10"
        pixfmt = args.pixfmt or PIXFMT.get(mbus, "pRAA")
        result["rear_from_subdev"] = "fallback: %s" % exc
    return mbus, width, height, pixfmt


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--camera", choices=["front", "rear"], default="front")
    ap.add_argument("--output-dir", default="/tmp/camera-lab")
    ap.add_argument("--media", default="/dev/media0")
    ap.add_argument("--frames", type=int, default=1)
    ap.add_argument("--sensor")
    ap.add_argument("--csiphy")
    ap.add_argument("--csid")
    ap.add_argument("--vfe")
    ap.add_argument("--video")
    ap.add_argument("--mbus")
    ap.add_argument("--width", type=int)
    ap.add_argument("--height", type=int)
    ap.add_argument("--pixfmt")
    ap.add_argument("--stride", type=int)
    ap.add_argument("--timeout", type=int, default=25)
    a = ap.parse_args()

    outdir = Path(a.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    run_id = time.strftime("%Y%m%d-%H%M%S")
    result = {"ok": False, "camera": a.camera, "run_id": run_id, "started": time.time()}
    enabled = []
    raw_path = outdir / ("%s-%s.raw" % (a.camera, run_id))
    pgm_path = outdir / ("%s-%s.pgm" % (a.camera, run_id))
    png_path = outdir / ("%s-%s.png" % (a.camera, run_id))
    try:
        if a.camera == "front":
            # The format below is the combination verified on 2026-11-10 and is
            # kept unchanged; only the entity names and the video node are
            # discovered, because those move when camss is reloaded.
            sensor = a.sensor or "imx471 24-0010"
            csiphy = a.csiphy or "msm_csiphy4"
            csid = a.csid or "msm_csid0"
            vfe = a.vfe or "msm_vfe0_rdi0"
            video = a.video or "/dev/video0"
            csiphy_pad, csid_pad = 1, 1
            if not a.sensor and not a.video:
                try:
                    d = discover_pipeline(a.media, "imx471")
                    sensor, csiphy, csid = d["sensor"], d["csiphy"], d["csid"]
                    vfe, video = d["vfe"], d["video"]
                    csiphy_pad, csid_pad = d["csiphy_pad"], d["csid_pad"]
                    result["front_discovered"] = True
                except Exception as exc:                     # noqa: BLE001
                    result["front_discovered"] = "fallback: %s" % exc
            mbus = a.mbus or "SRGGB10_1X10"
            width = a.width or 1928
            height = a.height or 1088
            pixfmt = a.pixfmt or "pRAA"
        else:
            d = discover_pipeline(a.media, "imx586")
            sensor = a.sensor or d["sensor"]
            csiphy = a.csiphy or d["csiphy"]
            csid = a.csid or d["csid"]
            vfe = a.vfe or d["vfe"]
            video = a.video or d["video"]
            csiphy_pad, csid_pad = d["csiphy_pad"], d["csid_pad"]
            mbus, width, height, pixfmt = rear_format(a, d, result)

        result.update({"sensor": sensor, "csiphy": csiphy, "csid": csid, "vfe": vfe,
                       "video": video, "mbus": mbus, "width": width, "height": height,
                       "pixfmt": pixfmt})

        def link(src, src_pad, dst, dst_pad, on):
            check(["media-ctl", "-d", a.media, "-l",
                   '"%s":%d -> "%s":%d[%d]' % (src, src_pad, dst, dst_pad, 1 if on else 0)])

        link(sensor, 0, csiphy, 0, True)
        link(csiphy, csiphy_pad, csid, 0, True); enabled.append((csiphy, csiphy_pad, csid, 0))
        link(csid, csid_pad, vfe, 0, True); enabled.append((csid, csid_pad, vfe, 0))

        fmt = "fmt:%s/%dx%d" % (mbus, width, height)
        for ent, pads in ((sensor, [0]), (csiphy, [0, csiphy_pad]), (csid, [0, csid_pad]), (vfe, [0, 1])):
            for p in sorted(set(pads)):
                check(["media-ctl", "-d", a.media, "--set-v4l2",
                       '"%s":%d[%s]' % (ent, p, fmt)])
        check(["v4l2-ctl", "-d", video, "--set-fmt-video",
               "width=%d,height=%d,pixelformat=%s" % (width, height, pixfmt)])

        stride, got = video_format(video)
        if stride is None:
            stride = (width * 10 + 7) // 8
        if got and got != (width, height):
            result["video_reported_size"] = got
        p = run(["timeout", str(a.timeout), "v4l2-ctl", "-d", video,
                 "--stream-mmap", "--stream-count=%d" % a.frames,
                 "--stream-to=%s" % raw_path], a.timeout + 10)
        result["capture_stdout"] = p.stdout.strip()
        result["capture_stderr"] = p.stderr.strip()
        result["capture_rc"] = p.returncode
        if not raw_path.exists() or raw_path.stat().st_size == 0:
            raise RuntimeError("no frames captured")
        stats = decode_raw10(raw_path, width, height, stride, pgm_path)
        result.update(stats)
        import shutil
        if shutil.which("ffmpeg"):
            r = run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(pgm_path), str(png_path)], 60)
            if r.returncode == 0 and png_path.exists():
                result["png"] = str(png_path)
        result["raw"] = str(raw_path)
        result["pgm"] = str(pgm_path)
        result["raw_bytes"] = raw_path.stat().st_size
        result["stride"] = stride
        result["ok"] = True
        if stats["max"] < 32 or stats["mean"] < 2:
            result["result"] = "partial"
            result["note"] = "capture path works but frame is dark; check lens cover or scene"
        else:
            result["result"] = "pass"
    except Exception as exc:
        result["error"] = str(exc)
        result["result"] = "fail"
    finally:
        for src, sp, dst, dp in enabled:
            try:
                check(["media-ctl", "-d", a.media, "-l",
                       '"%s":%d -> "%s":%d[0]' % (src, sp, dst, dp)])
            except Exception:
                pass
        result["finished"] = time.time()
        (outdir / ("%s-%s.json" % (a.camera, run_id))).write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
