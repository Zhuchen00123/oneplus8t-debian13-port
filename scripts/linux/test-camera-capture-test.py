#!/usr/bin/env python3
"""Offline tests for camera-capture-test.py (no hardware needed).

The rear-camera path discovers the pipeline from `media-ctl -p` output, so it
can be exercised with a synthetic camss topology.  These tests pin down the
three bugs that used to make a working sensor report failure:
  1. one-pad sensors print their outgoing link under a SINK pad;
  2. csiphy/csid links must use the SOURCE pad of the entity;
  3. v4l2-ctl needs the /dev node, not the media entity name.
"""
from types import SimpleNamespace
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("capture", HERE / "camera-capture-test.py")
cap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cap)

TOPO = """- entity 4: imx586 0-001a (1 pad, 1 link)
             type V4L2 subdev subtype Sensor flags 0
             device node name /dev/v4l-subdev0
        pad0: SINK
                [fmt:SRGGB10_1X10/4000x3000 field:none]
                -> "msm_csiphy0":0 [ENABLED]
- entity 5: msm_csiphy0 (2 pads, 2 links)
             device node name /dev/v4l-subdev1
        pad0: SINK
                [fmt:SRGGB10_1X10/4000x3000 field:none]
                <- "imx586 0-001a":0 [ENABLED]
        pad1: SOURCE
                [fmt:SRGGB10_1X10/4000x3000 field:none]
                -> "msm_csid0":0 [ENABLED]
- entity 6: msm_csid0 (2 pads, 2 links)
             device node name /dev/v4l-subdev2
        pad0: SINK
                <- "msm_csiphy0":1 [ENABLED]
        pad1: SOURCE
                -> "msm_vfe0_rdi0":0 [ENABLED]
- entity 7: msm_vfe0_rdi0 (3 pads, 3 links)
             device node name /dev/v4l-subdev3
        pad0: SINK
                <- "msm_csid0":1 [ENABLED]
        pad1: SOURCE
                -> "msm_vfe0_video0":0 [ENABLED]
- entity 8: msm_vfe0_video0 (1 pad, 1 link)
             device node name /dev/video0
        pad0: SINK
                <- "msm_vfe0_rdi0":1 [ENABLED]
"""

CALLS = []


def fake_check(cmd, timeout=30):
    CALLS.append(cmd)
    if cmd[:2] == ["media-ctl", "-p"]:
        return TOPO
    return ""


def test_discovery():
    cap.check = fake_check
    d = cap.discover_rear("/dev/media0")
    assert d["sensor"] == "imx586 0-001a", d
    assert d["sensor_dev"] == "/dev/v4l-subdev0", d
    assert d["csiphy"] == "msm_csiphy0", d
    assert d["csiphy_pad"] == 1, "csiphy must be wired from its SOURCE pad: %r" % (d,)
    assert d["csid"] == "msm_csid0", d
    assert d["csid_pad"] == 1, "csid must be wired from its SOURCE pad: %r" % (d,)
    assert d["vfe"] == "msm_vfe0_rdi0", d
    assert d["video"] == "/dev/video0", d


def test_one_pad_sensor_link_is_seen():
    # the link of the sensor lives under "pad0: SINK" in real output
    ents = cap.topology.__wrapped__("/dev/media0") if hasattr(cap.topology, "__wrapped__") else None
    cap.check = fake_check
    ents = cap.topology("/dev/media0")
    assert "imx586 0-001a" in ents, ents.keys()
    links = ents["imx586 0-001a"]["links"]
    assert links and links[0]["dst"] == "msm_csiphy0", links


TOPO_FRONT = """
- entity 1: imx471 24-0010 (1 pad, 1 link)
             device node name /dev/v4l-subdev0
        pad0: SINK
                -> "msm_csiphy4":0 [ENABLED]
- entity 2: msm_csiphy4 (3 pads, 3 links)
             device node name /dev/v4l-subdev2
        pad0: SINK
                <- "imx471 24-0010":0 [ENABLED]
        pad1: SOURCE
                -> "msm_csid0":0 [ENABLED]
- entity 3: msm_csid0 (3 pads, 3 links)
             device node name /dev/v4l-subdev5
        pad0: SINK
                <- "msm_csiphy4":1 [ENABLED]
        pad1: SOURCE
                -> "msm_vfe0_rdi0":0 [ENABLED]
- entity 4: msm_vfe0_rdi0 (3 pads, 3 links)
             device node name /dev/v4l-subdev7
        pad0: SINK
                <- "msm_csid0":1 [ENABLED]
        pad1: SOURCE
                -> "msm_vfe0_video0":0 [ENABLED]
- entity 5: msm_vfe0_video0 (1 pad, 1 link)
             device node name /dev/video3
        pad0: SINK
                <- "msm_vfe0_rdi0":1 [ENABLED]
"""


def test_front_pipeline_discovery():
    """The front camera's nodes move when camss is reloaded, so they are looked up."""
    def fake_front(cmd, timeout=30):
        if cmd[:2] == ["media-ctl", "-p"]:
            return TOPO_FRONT
        return ""
    cap.check = fake_front
    d = cap.discover_pipeline("/dev/media0", "imx471")
    assert d["sensor"] == "imx471 24-0010", d
    assert d["csiphy"] == "msm_csiphy4", d
    assert d["csid"] == "msm_csid0", d
    assert d["vfe"] == "msm_vfe0_rdi0", d
    assert d["video"] == "/dev/video3", "the video node must follow the graph: %r" % (d,)


def test_front_discovery_falls_back_to_defaults():
    """A graph without imx471 must not raise: main() keeps its verified defaults."""
    cap.check = fake_check              # rear-only topology
    try:
        cap.discover_pipeline("/dev/media0", "imx471")
    except RuntimeError as exc:
        assert "imx471" in str(exc), exc
    else:
        raise AssertionError("expected a RuntimeError for a missing sensor")


def test_raw10_decode():
    import tempfile
    # four pixels, 10-bit values 0x000, 0x001, 0x002, 0x003 packed MIPI RAW10
    row = bytes([0x00, 0x00, 0x00, 0x00, 0b11100100])
    with tempfile.TemporaryDirectory() as td:
        raw = Path(td) / "r.raw"
        raw.write_bytes(row * 2)
        stats = cap.decode_raw10(str(raw), 4, 2, 5, str(Path(td) / "r.pgm"))
        assert stats["min"] == 0 and stats["max"] == 0, stats
        raw.write_bytes(bytes([0xff, 0xff, 0xff, 0xff, 0xff]) * 2)
        stats = cap.decode_raw10(str(raw), 4, 2, 5, str(Path(td) / "r2.pgm"))
        assert stats["min"] == 255 and stats["max"] == 255, stats


def test_pixfmt_map():
    assert cap.PIXFMT["SRGGB10_1X10"] == "pRAA"
    assert cap.PIXFMT["SBGGR8_1X8"] == "BA81"


def test_rear_format_falls_back_when_subdev_node_is_missing():
    args = SimpleNamespace(width=None, height=None, mbus=None, pixfmt=None)
    result = {}
    mbus, w, h, pixfmt = cap.rear_format(
        args, {"sensor": "imx586 24-001a", "sensor_dev": ""}, result)
    assert (mbus, w, h) == ("SRGGB10_1X10", 4000, 3000), (mbus, w, h)
    assert pixfmt == "pRAA", pixfmt
    assert result["rear_from_subdev"].startswith("fallback:"), result["rear_from_subdev"]


def test_rear_format_uses_the_subdev_when_available():
    orig_mbus, orig_size = cap.subdev_mbus, cap.subdev_size
    cap.subdev_mbus = lambda dev, pad=0: (0x2B, "SRGGB10_1X10")
    cap.subdev_size = lambda dev, code, pad=0: (4000, 3000)
    try:
        args = SimpleNamespace(width=None, height=None, mbus=None, pixfmt=None)
        result = {}
        mbus, w, h, pixfmt = cap.rear_format(
            args, {"sensor": "imx586 24-001a", "sensor_dev": "/dev/v4l-subdev7"}, result)
    finally:
        cap.subdev_mbus, cap.subdev_size = orig_mbus, orig_size
    assert (mbus, w, h) == ("SRGGB10_1X10", 4000, 3000), (mbus, w, h)
    assert result["rear_from_subdev"]["dev"] == "/dev/v4l-subdev7", result


def main():
    tests = [test_discovery, test_one_pad_sensor_link_is_seen, test_raw10_decode, test_pixfmt_map,
             test_front_pipeline_discovery, test_front_discovery_falls_back_to_defaults,
             test_rear_format_falls_back_when_subdev_node_is_missing,
             test_rear_format_uses_the_subdev_when_available]
    failed = 0
    for t in tests:
        try:
            t()
            print("PASS", t.__name__)
        except Exception as exc:
            failed += 1
            print("FAIL", t.__name__, "-", exc)
    print("%d/%d passed" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
