#!/bin/bash
# Build the selfhash fixture.
#
#   ./build-selfhash.sh            x86_64 (host cc) and aarch64 static (clang cross)
#   OUT=/tmp/x ./build-selfhash.sh
#
# The aarch64 side reuses the self-contained musl sysroot that rustup installs
# for the aarch64-unknown-linux-musl target, so no extra cross toolchain is
# needed.  A static binary has no .dynsym, which is why the fixture prints its
# own symbol file offsets: the collector then attaches with its
# PATH@ELF_BASE:WRITE:READ form.

set -u

HERE=$(cd "$(dirname "$0")" && pwd)
CRATE=$(cd "$HERE/.." && pwd)
SRC=$CRATE/fixtures/selfhash.c
OUT=$(printenv OUT || true)
[ -n "$OUT" ] || OUT=$CRATE/target/selfhash
CC=$(printenv CC || true)
[ -n "$CC" ] || CC=cc
CROSS_CC=$(printenv CROSS_CC || true)
[ -n "$CROSS_CC" ] || CROSS_CC=clang-19
SELF_DIR=$(printenv SELF_DIR || true)
[ -n "$SELF_DIR" ] || SELF_DIR=/mnt/c/Users/15185/.rustup/toolchains/stable-x86_64-pc-windows-msvc/lib/rustlib/aarch64-unknown-linux-musl/lib/self-contained

mkdir -p "$(dirname "$OUT")"

echo "### host x86_64"
"$CC" -O2 -static -no-pie -Wl,--export-dynamic -o "$OUT-x86_64" "$SRC"
file "$OUT-x86_64"

echo "### aarch64 static"
if [ ! -d "$SELF_DIR" ]; then
    echo "missing musl sysroot: $SELF_DIR" >&2
    echo "set SELF_DIR or install gcc-aarch64-linux-gnu" >&2
    exit 1
fi
link_aarch64() {
    "$CROSS_CC" --target=aarch64-unknown-linux-musl -O2 -nostdlib -static -no-pie \
        -fuse-ld=lld -B"$SELF_DIR" -Wl,--no-dynamic-linker \
        "$SELF_DIR/crt1.o" "$SELF_DIR/crti.o" "$SRC" "$SELF_DIR/libc.a" "$SELF_DIR/crtn.o" \
        "$@" -o "$OUT-aarch64"
}

log=$OUT-aarch64.log
if link_aarch64 2>"$log"; then
    echo "aarch64 linked with the musl sysroot alone"
else
    # musl's floatscan.o needs the soft-float helpers that normally come from
    # libgcc, which a static musl link has no copy of.  The fixture formats no
    # floating point, so those helpers are stubbed to abort: a call would be a
    # bug and fails loudly instead of returning a wrong number.
    syms=$(grep -o 'undefined symbol: [A-Za-z0-9_]*' "$log" | awk '{print $3}' | sort -u)
    if [ -z "$syms" ]; then
        sed 's/^/    /' "$log"
        exit 1
    fi
    echo "stubbing soft-float helpers (never called): $(echo $syms)"
    args=""
    for sym in $syms; do
        args="$args -Wl,--defsym=$sym=abort"
    done
    # shellcheck disable=SC2086
    if ! link_aarch64 $args 2>"$log"; then
        sed 's/^/    /' "$log"
        exit 1
    fi
fi
file "$OUT-aarch64"
