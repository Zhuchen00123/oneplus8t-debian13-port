#!/bin/bash
# Capture N frames from the kebab front camera through the mainline CAMSS/VFE
# (imx471 -> csiphy4 -> csid0 -> vfe0_rdi0 -> /dev/video0), RAW10 1928x1088.
#
# Usage:  ./camss-frame.sh [frames] [out.raw]
#         ./camss-frame.sh 3 /tmp/f3.raw
#
# The format string for media-ctl MUST use media-bus names (SRGGB10_1X10),
# not V4L2 fourccs: the fourcc form fails with -22 and then STREAMON -32.
set -eu
FRAMES=${1:-1}
OUT=${2:-/tmp/camss.raw}
M="media-ctl -d /dev/media0"
F=SRGGB10_1X10/1928x1088
S=SRGGB10_1X10

$M -l '"msm_csiphy4":1->"msm_csid0":0[1]' >/dev/null
$M -l '"msm_csid0":1->"msm_vfe0_rdi0":0[1]' >/dev/null
for t in '"imx471 24-0010":0' '"msm_csiphy4":0' '"msm_csiphy4":1' \
         '"msm_csid0":0' '"msm_csid0":1' '"msm_vfe0_rdi0":0'; do
  $M --set-v4l2 "$t[fmt:$F]" >/dev/null
done

v4l2-ctl -d /dev/video0 --set-fmt-video=width=1928,height=1088,pixelformat=pRAA >/dev/null
v4l2-ctl -d /dev/video0 --stream-mmap --stream-count="$FRAMES" --stream-to="$OUT"
echo "captured $FRAMES frame(s) -> $OUT ($(stat -c%s "$OUT") bytes)"
