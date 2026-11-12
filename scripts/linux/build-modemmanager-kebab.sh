#!/bin/sh
# build-modemmanager-kebab.sh — turn stock ModemManager into one that can bring
# up a data bearer on the kebab SDX55.
#
# Why this exists
#   Stock ModemManager picks the data port but, when a bearer is created, the
#   qcom-soc plugin dispatches on the net driver and only knows 'ipa' and
#   'bam-dmux'.  kebab's data port is 'mhi_net', which falls through to
#     "Unsupported QMI kernel driver for 'net/mhi_hwip0': mhi_net"
#   The two patches in patches/mm add the mhi_net branch and bind the PCIe
#   endpoint before WDS Start Network (measured on this board: without the
#   bind the modem answers error 70 invalid-operation; with it the call is
#   actually attempted and ends with error 14).  Neither patch is upstream, and
#   neither is in 1.24.x or 1.25.95 (checked: the 1.26 pre-release source still
#   has only ipa and bam-dmux), so an upgrade does not lift this restriction.
#
# What it does
#   --check    (default) verify everything without changing anything
#   --build    apply the patches, build, install the binary to
#              /usr/local/lib/kebab-mm/ModemManager-patched and point the
#              systemd unit at it through a drop-in (dpkg files stay intact)
#   --revert   drop the drop-in and restart the packaged binary
#
# Building needs meson, ninja and the -dev packages; this script only reports
# what is missing, it never installs anything.
#
# Usage: build-modemmanager-kebab.sh [--check|--build|--revert] [--src DIR]

set -eu

MODE=check
SRC=
PROFILE=/usr/local/lib/kebab-mm/ModemManager-patched
DROPIN_DIR=/etc/systemd/system/ModemManager.service.d
DROPIN=$DROPIN_DIR/zz-kebab-mm-patched.conf

while [ $# -gt 0 ]; do
    case "$1" in
        --check)  MODE=check ;;
        --build)  MODE=build ;;
        --revert) MODE=revert ;;
        --src)    shift; SRC=$1 ;;
        -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

self=$(readlink -f "$0")
here=$(dirname "$self")
PATCHDIR=
for cand in "$here/../../patches/mm" "$here/patches/mm" /usr/local/lib/kebab-mm/patches/mm; do
    [ -d "$cand" ] && { PATCHDIR=$cand; break; }
done

fail=0
ok()   { printf '  ok    %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; fail=$((fail+1)); }
info() { printf '        %s\n' "$1"; }

[ -n "$PATCHDIR" ] || { echo "FATAL: patches/mm not found (looked next to the script and in /usr/local/lib/kebab-mm/patches)" >&2; exit 2; }
patch_count=$(ls "$PATCHDIR"/*.patch 2>/dev/null | wc -l)

echo "== ModemManager / kebab data-plane build helper =="
info "mode      : $MODE"
info "patches   : $PATCHDIR ($patch_count patch(es))"
[ "$patch_count" -ge 2 ] || { echo "FATAL: expected at least 2 patches in $PATCHDIR" >&2; exit 2; }

if [ "$MODE" = revert ]; then
    if [ -f "$DROPIN" ]; then
        rm -f "$DROPIN"
        systemctl daemon-reload
        systemctl restart ModemManager
        echo "  reverted: drop-in removed, packaged ModemManager restarted"
    else
        echo "  nothing to revert ($DROPIN absent)"
    fi
    exit 0
fi

echo "-- state"
if dpkg-query -W -f='${Status}' modemmanager 2>/dev/null | grep -q 'install ok installed'; then
    ver=$(dpkg-query -W -f='${Version}' modemmanager)
    ok "modemmanager installed ($ver)"
else
    bad "modemmanager is not installed"
fi
if [ -f "$PROFILE" ]; then
    ok "patched binary present ($(stat -c%s "$PROFILE") bytes)"
    info "sha256 $(sha256sum "$PROFILE" | cut -d' ' -f1)"
else
    info "no patched binary yet ($PROFILE)"
fi
if [ -f "$DROPIN" ]; then
    ok "drop-in active ($DROPIN)"
else
    info "no drop-in (packaged binary is what runs)"
fi

echo "-- toolchain"
for c in meson ninja pkg-config gcc dpkg-buildpackage; do
    if command -v "$c" >/dev/null 2>&1; then ok "$c present"; else bad "$c missing"; fi
done

echo "-- build dependencies (pkg-config modules are what the build needs)"
for m in glib-2.0 gio-2.0 gmodule-2.0 dbus-1 gudev-1.0 qmi-glib mbim-glib polkit-gobject-1 libsystemd qrtr-glib; do
    if pkg-config --exists "$m" 2>/dev/null; then
        ok "$m $(pkg-config --modversion "$m")"
    else
        bad "$m missing"
    fi
done
if dpkg -C 2>/dev/null | grep -q "unpacked but not yet configured"; then
    info "note: dpkg -C lists some -dev packages as unpacked-but-not-configured"
    info "      (gir1.2-gobject-2.0-dev is absent from trixie main on this board, so"
    info "      libgudev/libpolkit -dev cannot finish configuring). Introspection is"
    info "      disabled here, so the headers and .pc files are all the build needs."
fi
echo "-- source tree"
if [ -z "$SRC" ]; then
    for cand in /usr/src/modemmanager-1.24.0 /srv/modem-20261112/src/modemmanager-1.24.0; do
        [ -f "$cand/meson.build" ] && { SRC=$cand; break; }
    done
fi
if [ -z "$SRC" ] || [ ! -f "$SRC/meson.build" ]; then
    bad "source tree not found (use --src DIR)"
    info "fetch the Debian source (both files, ~1.6 MB):"
    info "  https://deb.debian.org/debian/pool/main/m/modemmanager/modemmanager_1.24.0.orig.tar.xz"
    info "  https://deb.debian.org/debian/pool/main/m/modemmanager/modemmanager_1.24.0-1+deb13u1.debian.tar.xz"
    info "  tar -xf modemmanager_1.24.0.orig.tar.xz -C /usr/src"
    info "  cd /usr/src/modemmanager-1.24.0 && tar -xf ../modemmanager_1.24.0-1+deb13u1.debian.tar.xz"
else
    ok "source tree at $SRC"
    info "version $(grep -m1 "version:" "$SRC/meson.build" | sed "s/.*'\(.*\)'.*/\1/")"
    if grep -q 'mhi_net' "$SRC/src/plugins/qcom-soc/mm-broadband-modem-qmi-qcom-soc.c" 2>/dev/null; then
        info "patch 0001 already applied to this tree"
    else
        for p in "$PATCHDIR"/*.patch; do
            if patch -p1 --dry-run -f -d "$SRC" --no-backup-if-mismatch < "$p" >/dev/null 2>&1; then
                ok "applies cleanly: $(basename "$p")"
            else
                bad "does not apply: $(basename "$p")"
            fi
        done
    fi
fi

if [ "$MODE" = check ]; then
    echo "-- result"
    if [ "$fail" -eq 0 ]; then
        echo "  READY: every prerequisite is present; run with --build"
        exit 0
    fi
    echo "  NOT READY: $fail prerequisite(s) missing (nothing was changed)"
    exit 1
fi

# ---- build ----
[ "$fail" -eq 0 ] || { echo "refusing to build: $fail prerequisite(s) missing" >&2; exit 1; }

echo "-- applying patches"
if grep -q 'mhi_net' "$SRC/src/plugins/qcom-soc/mm-broadband-modem-qmi-qcom-soc.c"; then
    info "already applied, skipping"
else
    for p in "$PATCHDIR"/*.patch; do
        patch -p1 -d "$SRC" --no-backup-if-mismatch < "$p"
    done
fi

echo "-- meson setup"
rm -rf "$SRC/build-kebab"   # always from scratch: a stale dir from a failed run blocks meson
meson setup "$SRC/build-kebab" "$SRC" \
    --buildtype=release \
    -Dintrospection=false -Dgtk_doc=false -Dvapi=false \
    -Dman=false -Dtests=false -Dexamples=false -Dbash_completion=false \
    -Dbuiltin_plugins=true -Dqmi=true -Dmbim=true -Dqrtr=true \
    -Dudevdir=/usr/lib/udev -Dsystemdsystemunitdir=/usr/lib/systemd/system

echo "-- ninja"
ninja -C "$SRC/build-kebab"

echo "-- install"
install -d "$(dirname "$PROFILE")"
install -m 755 "$SRC/build-kebab/src/ModemManager" "$PROFILE"
info "installed $(stat -c%s "$PROFILE") bytes"
info "sha256 $(sha256sum "$PROFILE" | cut -d' ' -f1)"

install -d "$DROPIN_DIR"
{
    echo '# kebab: run the locally patched ModemManager (patches/mm: mhi_net data'
    echo '# port + PCIe endpoint bind).  Revert with build-modemmanager-kebab.sh --revert.'
    echo '[Service]'
    echo 'ExecStart='
    echo "ExecStart=$PROFILE --log-journal"
} > "$DROPIN"
systemctl daemon-reload
systemctl restart ModemManager
echo "-- waiting 35 s for the modem to be re-probed"
sleep 35
systemctl is-active ModemManager | sed 's/^/  ModemManager: /'
mmcli -L 2>&1 | head -2 | sed 's/^/  /'
echo "  (MM needs ~30 s after a restart; 'mmcli --scan-modems' nudges it)"
