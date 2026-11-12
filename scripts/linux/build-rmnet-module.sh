#!/bin/bash
# Build rmnet.ko out-of-tree against the kernel tree the running image came from.
#
# Why: the SDX55's raw-IP path has a known "~10 MB then wedged" failure on the
# Apollo project, fixed by QMAP multiplexing over rmnet.  Our kernel image has
# no CONFIG_RMNET, but rmnet is a self-contained module (it only registers a
# link type and an rx handler), so it can be built against the *running*
# kernel's own build tree and loaded with insmod -- no image change, no reboot.
#
# Refs: drivers/net/ethernet/qualcomm/rmnet/Kconfig says RMNET devices "can be
# attached to any IP mode physical device"; this kernel has GRO_CELLS=y, which
# rmnet selects.
#
# usage:
#   build-rmnet-module.sh [--tree DIR] [--out DIR] [--dest DIR] [--expect RELEASE]
#
# Defaults match the r4-venus tree on this host.  The module is signed with the
# tree's build key if present; the kernel has MODULE_SIG=y (not FORCE), so an
# unsigned module would also load, but signing keeps the build quiet.
set -euo pipefail

TREE=/home/zhuchen/oneplus8t-venus-20261105
OUT=""
DEST=""
EXPECT="7.2.0-kebab-debian13-r4-venus+"

while [ $# -gt 0 ]; do
	case "$1" in
	--tree)   TREE=$2; shift 2 ;;
	--out)    OUT=$2; shift 2 ;;
	--dest)   DEST=$2; shift 2 ;;
	--expect) EXPECT=$2; shift 2 ;;
	-h|--help) sed -n '2,20p' "$0"; exit 0 ;;
	*) echo "unknown argument: $1" >&2; exit 2 ;;
	esac
done

OUT=${OUT:-$TREE/out}
DEST=${DEST:-$(dirname "$0")/../../local/rmnet-build-$(date -u +%Y%m%d)}
SRC=$TREE/linux/drivers/net/ethernet/qualcomm/rmnet

for path in "$TREE/linux/Makefile" "$OUT/.config" "$OUT/Module.symvers" "$SRC/Makefile"; do
	[ -e "$path" ] || { echo "missing $path" >&2; exit 1; }
done

echo "tree   : $TREE"
echo "out    : $OUT"
echo "head   : $(git -C "$TREE/linux" log -1 --format=%H 2>/dev/null || echo unknown)"
echo "config : $(sha256sum "$OUT/.config" | cut -d' ' -f1)"

make -C "$TREE/linux" O="$OUT" ARCH=arm64 LLVM=1 \
	M="$SRC" CONFIG_RMNET=m modules

KO=$SRC/rmnet.ko
VERMAGIC=$(modinfo -F vermagic "$KO")
echo "ko     : $KO"
echo "size   : $(stat -c %s "$KO") bytes"
echo "sha256 : $(sha256sum "$KO" | cut -d' ' -f1)"
echo "vermagic: $VERMAGIC"

# leave the source tree as we found it (build products would otherwise linger)
make -C "$TREE/linux" O="$OUT" ARCH=arm64 LLVM=1 M="$SRC" clean >/dev/null 2>&1 || true

mkdir -p "$DEST"
cp "$KO" "$DEST/rmnet.ko"
sha256sum "$DEST/rmnet.ko" | cut -d' ' -f1 > "$DEST/rmnet.ko.sha256"
llvm-nm -u "$KO" | awk '{print $2}' | sort -u > "$DEST/undefined-symbols.txt"
{
	echo "tree: $TREE"
	echo "kernel head: $(git -C "$TREE/linux" log -1 --format=%H 2>/dev/null || echo unknown)"
	echo "config sha256: $(sha256sum "$OUT/.config" | cut -d' ' -f1)"
	echo "built: $(date -u +%FT%TZ)"
	echo "cc: $(clang --version | head -1)"
	echo "vermagic: $VERMAGIC"
} > "$DEST/build-info.txt"
echo "saved  : $DEST"

case "$VERMAGIC" in
"$EXPECT "*) echo "verdict : vermagic matches $EXPECT" ;;
*) echo "verdict : WARNING vermagic '$VERMAGIC' does not match expected '$EXPECT' -- the module will not load" >&2; exit 1 ;;
esac
