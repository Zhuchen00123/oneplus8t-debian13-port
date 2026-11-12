# ModemManager for the kebab SDX55 — **installed and verified 2026-11-13**

ModemManager 1.24.0-1+deb13u1 is now installed on the device and manages the
modem's control plane.  The verification, the three facts it produced and the
remaining data-plane gap are in
[docs/sdx55-modemmanager-20261112.md](../../docs/sdx55-modemmanager-20261112.md) §5.

## What each file is

| File | Destination | Purpose |
| --- | --- | --- |
| `77-mm-ignore-sdx55-efs.rules` | `/etc/udev/rules.d/` | Hard prerequisite: keeps MM from AT-probing the remote-EFS channel, which ERRFATALs the firmware (this board hit it on 2026-11-10). |
| `77-mm-sdx55-fusion.rules` | `/etc/udev/rules.d/` | Tags the SDX55 wwan ports and mhi_hwip/mhi_swip net ports with the qcom-soc physdev UID; also ignores the dead raw QMI port (measured: 10 s timeout / CTL allocation timeout). |
| `zz-kebab-mm.conf` | `/etc/systemd/system/ModemManager.service.d/` | Orders MM after `kebab-sdx55-modem.service`. |
| `kebab-mm-restart-once.sh` | `/usr/local/lib/kebab-mm/` | Optional helper: restarts MM until it sees the modem, then cycles the radio once if registration hangs. Does nothing without a SIM. |
| `kebab-mm-restart-once.service` | `/etc/systemd/system/` | oneshot wrapper for the helper. |

## What is installed right now

```sh
dpkg -l modemmanager polkitd xml-core sgml-base | tail -4
```

* `modemmanager 1.24.0-1+deb13u1`, `polkitd 126-2`, `xml-core 0.19`, `sgml-base 1.31`
* `/etc/udev/rules.d/77-mm-ignore-sdx55-efs.rules`
* `/etc/udev/rules.d/77-mm-sdx55-fusion.rules`
* `/etc/systemd/system/ModemManager.service.d/zz-kebab-mm.conf`
* `ModemManager.service` enabled + active

The dependency list in the first version of this README was short by two
packages: `polkitd` needs `xml-core` (`>= 0.19`), which needs `sgml-base`.
`dpkg --no-act -i` does not resolve dependencies recursively, so it did not
catch them.  Both are architecture-independent packages fetched over `wlan0`.

## Verified state (no SIM)

```
$ mmcli -L
    /org/freedesktop/ModemManager1/Modem/0 [QUALCOMM INCORPORATED] 0
$ mmcli -m 0 | grep -E 'state|failed reason|drivers|ports'
           |           drivers: qrtr, mhi_net
           |             ports: mhi_hwip0 (net), mhi_swip0 (net), qrtr3 (qmi)
           |             state: failed
           |     failed reason: sim-missing
```

* QMI over QRTR works through MM (and through `qmicli -d qrtr://3`);
  `/dev/wwan0qmi0` is dead (`CID allocation failed in the CTL client`) and stays ignored.
* No ERRFATAL, GNSS unaffected, `kebab-qmi-wds.py selftest` still PASS with MM running.
* After `systemctl restart ModemManager` allow ~30 s, or run
  `sudo mmcli --scan-modems`, before expecting the modem to reappear.
* `mmcli` prints the real IMEI — do not paste its output into docs or logs.

## The patched binary that is actually running

As of 2026-11-13 the daemon is **not** the packaged binary:

| Path | What |
| --- | --- |
| `/usr/local/lib/kebab-mm/ModemManager-patched` | built from Debian 1.24.0 + both patches, 3,680,200 B, sha256 `61c79110…` |
| `/etc/systemd/system/ModemManager.service.d/zz-kebab-mm-patched.conf` | overrides `ExecStart` only |
| `/usr/sbin/ModemManager` | dpkg-owned, untouched (`dpkg -V modemmanager` clean) |
| `/usr/local/lib/kebab-mm/build-modemmanager-kebab.sh` | `--check` / `--build` / `--revert` |
| `/usr/local/lib/kebab-mm/patches/mm/` | the two patches + README |
| `/usr/local/lib/kebab-mm/fetch-debs-kebab.py` | apt-less .deb resolver/fetcher (no DNS on this board) |

Both patches are compiled in — verified by disassembling the qcom-soc data-port
dispatcher (three driver comparisons, the middle one references the `mhi_net`
string). `--revert` has been exercised and restores the packaged binary.
See [patches/mm/README.md](../../patches/mm/README.md).
## What still needs the two patches (data plane only)

Stock qcom-soc picks the data port but, when a bearer is created, dispatches on
the net driver and only knows `ipa` and `bam-dmux`; `mhi_net` ends in
"unsupported data port driver".  Apollo's `0001-qcom-soc-mhi-net.patch` adds the
`mhi_net` branch, and `0002-bearer-bind-pcie.patch` binds the PCIe endpoint
before `WDS Start Network` (independently confirmed here: without the bind the
modem answers `error 70 invalid-operation`, with it the call reaches `error 14`).

Note: the generic QMI class in the same tree **already** handles `mhi_net`
(`peek_port_qmi_for_data_mhi()`), so only the qcom-soc plugin was missing it.

The multiplexing machinery is also already upstream in 1.24
(`--test-multiplex-requested`, `qmi_device_add_link_with_flags`,
`MM_PORT_QMI_SETUP_DATA_FORMAT_ACTION_SET_MULTIPLEX`), so once the patches are
built in and `rmnet.ko` is loaded, MM's QMAP bearer path is plausible — still
unverified without a SIM.

## Two files added 2026-11-13 (port choice + the QMAP lever)

| File | Installed to | What it does |
| --- | --- | --- |
| `77-mm-sdx55-swip-ignore.rules` | `/etc/udev/rules.d/` | marks `mhi_swip0` as `ID_MM_PORT_IGNORE` |
| `zzz-kebab-mm-multiplex.conf` | `/usr/local/lib/kebab-mm/dropins/` (inert) | opt-in ModemManager drop-in that passes `--test-multiplex-requested` |

### Why the SW port must be ignored

This board exposes **two** net ports from `mhi_net`:

| ifindex | iface | MHI channel | role |
| --- | --- | --- | --- |
| 5 | `mhi_swip0` | IP_SW0 46/47 | idle |
| 6 | `mhi_hwip0` | IP_HW0 100/101 | **the data path** |

ModemManager does not pick by name or by index: `mm_base_modem_peek_best_data_port()`
returns *the first not-connected data port of the requested type* from
an internal list, and `mm_base_modem.c` then takes `priv->data->data` (that
same first port) to set the QMI port's `net_sysfs_path` / `net_driver` and to
build the endpoint info.  With two candidates that is a coin flip decided by
list order, and a bearer on `mhi_swip0` would bind an endpoint that carries
no data.  Only one of them has ever carried anything in our measurements.

Ignoring the SW port makes the choice deterministic.  Verified on the running
daemon by restarting it once with `--log-level=DEBUG`:

    [mhi_swip0/probe] port is ignored via udev tag
    [modem0] net/mhi_hwip0: net (data)

and `mmcli -m 0` now shows `mhi_hwip0 (net), mhi_swip0 (ignored), qrtr3 (qmi)`.
Without the rule both appeared as `(net)`.

### Why the multiplex drop-in is not installed

It changes the on-wire encapsulation to QMAP and needs `rmnet` loaded, so it
must stay a deliberate act taken after raw-IP has actually wedged — the same
lever the community lists as missing on kebab.  Enabling it:

    sudo kebab-modem-qmap load
    sudo install -m 644 /usr/local/lib/kebab-mm/dropins/zzz-kebab-mm-multiplex.conf \
         /etc/systemd/system/ModemManager.service.d/
    sudo systemctl daemon-reload && sudo systemctl restart ModemManager

**The file name matters.**  Drop-ins are applied in filename order: the first
version was called `zz-kebab-mm-multiplex.conf`, which sorts *before*
`zz-kebab-mm-patched.conf`, so the patched drop-in's `ExecStart` silently
overwrote it and the flag never reached the daemon.  Renamed to `zzz-…`
which sorts after; `systemctl show -p ExecStart` now ends with
`--test-multiplex-requested` only when the file is installed (both states
tested, and removing it restores the plain command line).
## Rollback

```sh
sudo systemctl disable --now ModemManager
sudo rm -f /etc/udev/rules.d/77-mm-ignore-sdx55-efs.rules /etc/udev/rules.d/77-mm-sdx55-fusion.rules
sudo rm -rf /etc/systemd/system/ModemManager.service.d
sudo udevadm control --reload-rules
sudo dpkg -r modemmanager polkitd xml-core sgml-base   # or apt purge
```

The modem keeps working under `kebab-sdx55-modem.service` with or without MM;
GNSS and EFS are unaffected either way.
