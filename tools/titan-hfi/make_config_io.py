#!/usr/bin/env python3
"""Build the smallest CONFIG_IO payloads a B2 bring-up could dare to send.

Everything here is a *template*, not a working configuration:

  * payload length and field offsets are contractual (pinned by titan_hfi.h),
  * the values are deliberately boring (one input image, UBWC lossless, no
    metadata buffers) so that a first CONFIG_IO exercises the transport rather
    than the ISP tuning surface,
  * nobody has yet produced a real IQ parameter block (the iqSettingsAddr
    contents are still unresolved, see B0 section 9), so CONFIG_IO alone has to
    be enough to prove that the firmware accepts a command and answers with a
    _BpsConfigIoDone / _IpeConfigIoDone.

Usage:
    python3 make_config_io.py                 # summary + sizes
    python3 make_config_io.py --dump          # plus hexdumps
    python3 make_config_io.py --out DIR       # write bps_config_io.bin, ...
"""

from __future__ import annotations

import argparse
import os
import struct
import sys

import titan_hfi as hfi

# ---- image descriptor geometry (must match titan_hfi.h) --------------------
IMAGE_DESCRIPTOR_SIZE = 92
IMAGE_INFO_SIZE = 60
BPS_MAX_IMAGES = 9
IPE_MAX_IMAGES = 17

BPS_CONFIG_IO_SIZE = 840
IPE_CONFIG_IO_SIZE = 1600

IMAGE_FORMAT_BAYER_10 = 6
UBWC_VERSION_4 = 2
UBWC_LOSSLESS = 0
PIXEL_LSB_ALIGNED = 0
FIRST_PIXEL_R = 0


def image_descriptor(format=IMAGE_FORMAT_BAYER_10, width=4000, height=3000,
                     stride=4096, bayer_order=FIRST_PIXEL_R,
                     ubwc_version=UBWC_VERSION_4, ubwc_lossy=UBWC_LOSSLESS):
    """One 92 B _ImageDescriptor with a single populated buffer layout.

    metadataBufferLayout stays zero: PD metadata / stats buffers are a later
    problem, and the firmware only needs the descriptor to be self-consistent.
    """
    info = struct.pack(
        "<IIIIIIIIIIIIIII",
        format,            # +0  format
        width,             # +4  dimensions.widthPixels
        height,            # +8  dimensions.heightLines
        bayer_order,       # +12 bayerOrder
        PIXEL_LSB_ALIGNED,  # +16 pixelPackingAlignment
        0,                 # +20 yuv422Order
        0,                 # +24 enableByteSwap
        ubwc_version,      # +28 ubwcVersion
        ubwc_lossy,        # +32 ubwcLossyMode
        0,                 # +36 ubwcBwLimit
        0,                 # +40 ubwcThreshLossy0
        0,                 # +44 ubwcThreshLossy1
        0,                 # +48 argbAlpha
        0,                 # +52 ubwcOffsetsVarLossy
        0,                 # +56 fromDownscale
    )
    assert len(info) == IMAGE_INFO_SIZE
    layouts = struct.pack("<IIII", stride, height, 0, 0)      # bufferLayout[2]
    meta = struct.pack("<IIII", 0, 0, 0, 0)                   # metadataBufferLayout[2]
    desc = info + layouts + meta
    assert len(desc) == IMAGE_DESCRIPTOR_SIZE
    return desc


def _config_io_data(count, descriptors, tail):
    """count x 92 B descriptors, zero padded, followed by the struct tail."""
    if len(descriptors) > count:
        raise ValueError("at most %d descriptors" % count)
    body = bytearray(IMAGE_DESCRIPTOR_SIZE * count)
    for i, desc in enumerate(descriptors):
        body[i * IMAGE_DESCRIPTOR_SIZE:(i + 1) * IMAGE_DESCRIPTOR_SIZE] = desc
    return bytes(body) + tail


def build_bps_config_io(user_arg=0, images=None):
    """840 B _BpsConfigIo payload (opcode 0x01).

    9 image slots; slot 0 is a 4000x3000 BAYER_10 input by default.
    """
    if images is None:
        images = [image_descriptor()]
    tail = struct.pack("<I", 0)               # secureMode
    data = _config_io_data(BPS_MAX_IMAGES, images, tail)
    assert len(data) == 832, len(data)
    out = data + struct.pack("<Q", user_arg)
    assert len(out) == BPS_CONFIG_IO_SIZE, len(out)
    return out


def build_ipe_config_io(user_arg=0, images=None, stabilization_margins=(0, 0),
                        ica_max_upscaling_q16=0x10000, max_batch_size=1,
                        mux_mode=0, topology_type=0):
    """1600 B _IpeConfigIo payload (opcode 0x05).

    17 image slots; slot 0 is a 4000x3000 BAYER_10 input by default.
    icaMaxUpscalingQ16 defaults to 0x10000 (1.0 in Q16) rather than 0 so that a
    firmware that divides by it cannot see zero.
    """
    if images is None:
        images = [image_descriptor()]
    tail = struct.pack("<IIIIIII",
                       stabilization_margins[0], stabilization_margins[1],
                       0,                       # secureMode
                       ica_max_upscaling_q16,
                       max_batch_size,
                       mux_mode,
                       topology_type)           # 8+4+4+4+4+4 = 28 B
    assert len(tail) == 28, len(tail)
    data = _config_io_data(IPE_MAX_IMAGES, images, tail)
    assert len(data) == 1592, len(data)
    out = data + struct.pack("<Q", user_arg)
    assert len(out) == IPE_CONFIG_IO_SIZE, len(out)
    return out


def build_packets(user_arg=0x1122334455667788, handle=1):
    """Return [(name, packet bytes)] for the two CONFIG_IO commands.

    BPS CONFIG_IO (840 B) is 876 B direct, well inside the 6019 B limit.
    IPE CONFIG_IO (1600 B) is 1636 B direct, also fine.  IPE_FRAME_PROCESS
    (14328 B) can only travel INDIRECT and is not built here.
    """
    bps_payload = build_bps_config_io(user_arg=user_arg)
    ipe_payload = build_ipe_config_io(user_arg=user_arg)
    return [
        ("bps_config_io", hfi.encode_async(
            0x01, [handle], bps_payload, direct=True, user_arg1=user_arg)),
        ("ipe_config_io", hfi.encode_async(
            0x05, [handle], ipe_payload, direct=True, user_arg1=user_arg)),
    ]


def hexdump(data, prefix="    "):
    lines = []
    for off in range(0, len(data), 16):
        chunk = data[off:off + 16]
        hexpart = " ".join("%02x" % b for b in chunk)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append("%s%08x  %-47s  %s" % (prefix, off, hexpart, text))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dump", action="store_true", help="print hexdumps")
    ap.add_argument("--out", metavar="DIR",
                    help="write the payloads and packets as .bin files")
    ap.add_argument("--user-arg", default="0x1122334455667788",
                    help="FWUSERARG cookie (hex)")
    ap.add_argument("--handle", default="1", help="handle id to address")
    args = ap.parse_args(argv)

    user_arg = int(args.user_arg, 0) & 0xFFFFFFFFFFFFFFFF
    handle = int(args.handle, 0)

    bps = build_bps_config_io(user_arg=user_arg)
    ipe = build_ipe_config_io(user_arg=user_arg)
    bps_pkt = hfi.encode_async(0x01, [handle], bps, direct=True, user_arg1=user_arg)
    ipe_pkt = hfi.encode_async(0x05, [handle], ipe, direct=True, user_arg1=user_arg)

    print("minimal CONFIG_IO template")
    print("  user_arg           0x%016x" % user_arg)
    print("  handle             %d" % handle)
    print("  bps payload        %4d B  (_BpsConfigIo, opcode 0x01)" % len(bps))
    print("  ipe payload        %4d B  (_IpeConfigIo, opcode 0x05)" % len(ipe))
    print("  bps async packet   %4d B  direct (limit %d B)"
          % (len(bps_pkt), hfi.MAX_PKT_SIZE))
    print("  ipe async packet   %4d B  direct (limit %d B)"
          % (len(ipe_pkt), hfi.MAX_PKT_SIZE))

    for name, payload in (("bps_config_io", bps), ("ipe_config_io", ipe)):
        non_zero = sum(1 for b in payload if b)
        print("  %-16s non-zero bytes %d / %d" % (name, non_zero, len(payload)))

    for name, pkt in (("bps_config_io", bps_pkt), ("ipe_config_io", ipe_pkt)):
        ok, reason = hfi.firmware_accepts(pkt)
        print("  %-16s firmware check: %s%s"
              % (name, "accept" if ok else "REJECT", "" if ok else " (" + reason + ")"))
        info = hfi.decode(pkt)
        print("  %-16s decoded payload offset %d, nNumHandles %d, opcode 0x%02x"
              % (name, info["payload_offset"], info["nNumHandles"], info["eCmdOpcode"]))

    if args.dump:
        for name, payload in (("bps_config_io", bps), ("ipe_config_io", ipe)):
            print("\n%s (%d B)" % (name, len(payload)))
            print(hexdump(payload[:256]))
            if len(payload) > 256:
                print("    ... %d more bytes" % (len(payload) - 256))
        for name, pkt in (("bps_config_io packet", bps_pkt),
                          ("ipe_config_io packet", ipe_pkt)):
            print("\n%s (%d B)" % (name, len(pkt)))
            print(hexdump(pkt[:64]))
            print("    ... payload continues to byte %d" % len(pkt))

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        for name, blob in (("bps_config_io_payload", bps),
                           ("ipe_config_io_payload", ipe),
                           ("bps_config_io_packet", bps_pkt),
                           ("ipe_config_io_packet", ipe_pkt)):
            path = os.path.join(args.out, name + ".bin")
            with open(path, "wb") as fh:
                fh.write(blob)
            print("  wrote %s (%d B)" % (path, len(blob)))

    return 0


if __name__ == "__main__":
    sys.exit(main())
