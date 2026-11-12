# ModemManager patches for the kebab SDX55 data plane

Two patches that add the missing **`mhi_net` data-port branch** to ModemManager's
qcom-soc plugin. Both are compiled into the binary that now runs on the board
(see "Built and running" below).

## The gap

ModemManager sees the modem fine (see [MM doc §5](../../docs/sdx55-modemmanager-20261112.md)),
but the qcom-soc plugin dispatches on the data port's net driver and only knows
two of them (`src/plugins/qcom-soc/mm-broadband-modem-qmi-qcom-soc.c`):

    128: if (g_strcmp0 (net_port_driver, "ipa") == 0)
    131: if (g_strcmp0 (net_port_driver, "bam-dmux") == 0)
    134: g_set_error (error, ..., "Unsupported QMI kernel driver for 'net/%s': %s", ...)

kebab's data port is `mhi_net`, so a bearer request dies on the last branch.

**The rest of ModemManager already knows mhi_net.** In the same 1.24.0 tree,
`mm-broadband-modem-qmi.c:356` has a dedicated
`peek_port_qmi_for_data_mhi()`, and `mm-port-qmi.c`,
`mm-modem-helpers-qmi.c` and `mm-bearer-mbim.c` handle it in a dozen
places. Only the qcom-soc plugin — which is what this board gets selected
into, because its QRTR node carries `ID_MM_QCOM_SOC` — misses it.

## The patches

| File | Changes | Why |
| --- | --- | --- |
| `0001-qcom-soc-accept-mhi-net-data-ports.patch` | qcom-soc `peek_port_qmi_for_data()`: an `mhi_net` branch that reuses the IPA path | Endpoint info comes from the QMI port itself, exactly as for IPA |
| `0002-bearer-qmi-bind-pcie-endpoint.patch` | `mm-bearer-qmi.c`: bind the mux data port when the endpoint is PCIe even if unmultiplexed | **Measured on this board**: without the bind, `WDS Start Network` returns `error 70 invalid-operation`; with it the call is really attempted (`error 14`) |

Both come from the Apollo project (same SM8250 + external SDX55 over MHI) and
were re-verified here against Debian's own source, not accepted on trust.

## Verified application

Against Debian `modemmanager 1.24.0-1+deb13u1` source, both on the PC and on
the device itself:

    patch -p1 --dry-run < 0001-...patch   # checking file ...qcom-soc.c, clean
    patch -p1 --dry-run < 0002-...patch   # Hunk #1 at 2081 (offset -1), #2 at 2234 (offset -1)

Patch 0002 lands one line early because Debian's 1.24.0 carries a small patch of
its own nearby; the hunks still apply and the context guards the replacement.
Both files here are LF-normalised — the upstream copies were checked out on
Windows with CRLF, which makes `git apply` report a bogus conflict.

## Built and running (2026-11-13)

    su build-modemmanager-kebab.sh --check --src /srv/modem-20261112/src/modemmanager-1.24.0
    -> READY
    su build-modemmanager-kebab.sh --build --src ...
    -> 489/489 ninja targets, src/ModemManager 3,680,200 B
       sha256 61c7911031907a5c565a5c3a6822f0fee283da86199aae58819fa5823679b394
    -> installed /usr/local/lib/kebab-mm/ModemManager-patched + systemd drop-in
    -> `mmcli -L` shows Modem/0 again after 35 s

**Proof the branch is really in the binary** (not just "it compiled"):
the qcom-soc dispatcher symbol at `0x193700` contains **three** `g_strcmp0` calls
referencing `0x220380`, `0x227160` and `0x242890`; reading those
addresses out of the file gives `ipa`, `mhi_net`, `bam-dmux`. Stock has only
two comparisons. The packaged `/usr/sbin/ModemManager` is untouched
(`dpkg -V modemmanager` is clean); the drop-in only overrides `ExecStart`.

**Revert is tested**: `--revert` removes the drop-in and restarts the packaged
binary; writing the drop-in back returns to the patched one.

### What the build actually needed

Discovered by iterating (each one is now in the script's check list):

* `meson ninja-build gcc pkg-config`
* `libglib2.0-dev libgudev-1.0-dev libqmi-glib-dev libmbim-glib-dev`
* `libpolkit-gobject-1-dev libsystemd-dev libqrtr-glib-dev libdbus-1-dev`
* `gettext` (msgfmt) and `xsltproc` — meson fails without them even with
  docs and man pages disabled,
* `-Dudevdir=/usr/lib/udev` — without it meson asks for a `udev.pc` that
  Debian does not ship (`libudev.pc` only).

The board has no DNS, so the packages came from `fetch-debs-kebab.py`
(Packages.xz + recursive Depends + one `dpkg -i`). Two things bit us:
`dpkg -i` still needed one `dpkg --configure -a` follow-up, and the security
updates on this box are newer than the pool snapshot, so `libpcre2-*` had to
come from `security.debian.org` at the exactly matching `~deb13u3` version.

`libgudev-1.0-dev` and `libpolkit-gobject-1-dev` remain "unpacked but not
configured": they depend on `gir1.2-gobject-2.0-dev`, which does not exist in
Debian 13 main at all. Introspection is off for this build, the headers and
.pc files are in place, and `pkg-config` resolves every module — so this is
cosmetic here. Do not `dpkg --purge` them without checking that first.

## Upgrading does not help

Checked 2026-11-13: `modemmanager_1.25.95.orig.tar.xz` (the 1.26 pre-release)
still has **no `mhi_net` string at all** in
`mm-broadband-modem-qmi-qcom-soc.c`. The arm64 packages Debian offers are
1.14.12, 1.20.4, 1.24.0 (installed), 1.24.2 and 1.25.95; none lifts this.

## What this still does not prove

A patched build lets MM *ask* for a bearer; whether a bearer comes up is a
SIM-day question — with no card MM stops earlier, at
`state: failed / sim-missing`, before it ever reaches the data port.
It is also redundant with `kebab-qmi-wds.py data-up` for getting host data;
MM's value is the standard interfaces (registration, SMS, signal, and a bearer
NetworkManager could later use — NetworkManager is not installed here).
