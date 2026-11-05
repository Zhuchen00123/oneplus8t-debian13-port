# SM8250 Venus r4 enablement and decode validation

## Observed result

On 2026-11-05, an r4 kernel was temporarily booted with the matching KB2000 OEM firmware set. FFmpeg explicitly selected `h264_v4l2m2m`; it bound to the `qcom-venus` decoder backed by `venus_core`. Hardware output matched the software reference frame by frame using MD5 over normalized `yuv420p` output for:

- 320脳240 at 15 fps, 30 frames and 90 frames.
- 1280脳720 at 30 fps, 90 frames.

This was a short functional check. It does not establish long-run stability, remote playback performance, Firefox DRM PRIME, other codecs, or extended playback. The boot-time DSI warning and residual touchscreen errors remain outside this result and are not claimed as fixed.

The kernel was temporary-booted; this trial did not write a boot partition. The 13 OEM firmware split files were used locally and are not included here.

## Permanent enablement

The r4 kernel was installed to the active boot slot and the device completed a normal reboot. On that boot, `kebab-venus.service` validated the 13 locally installed OEM firmware files and bound the decoder at `/dev/video14` through `qcom-venus` / `venus_core`; the unit reported `0/SUCCESS` (peak 9.8 MiB, 126 ms CPU). The user confirmed touch and all three local UI modes after reboot.

A separate 1280脳720 / 30 fps, 90-frame hardware probe after reboot exited successfully. Its report confirmed `success=true`, 90 software frames and 90 hardware frames, all 90 matched, no mismatches, and the same `/dev/video14` Venus binding; the IRQ count increased by 193. This is a distinct persistent-boot result from the temporary-boot trials above.

## Public files and local firmware gate

`0008-arm64-dts-sm8250-venus-power-opp.patch` contains the Venus power-domain and OPP adjustment. `kebab-venus-decode-probe.py` provides a bounded software reference check and an opt-in hardware check. Hardware decoding is only requested with `--hardware`; the probe does not silently fall back to software.

`kebab-venus-init.py`, its systemd unit, and its tests are an optional local template. The checked-in `MANIFEST_SHA` is deliberately `REPLACE_WITH_LOCAL_MANIFEST_SHA256`, so the template refuses to run until the device owner configures it. Before use:

1. Obtain the correct OEM firmware for the target device through an authorized source. No firmware archive or split file is supplied by this repository.
2. Install the expected 13 split files as root-owned, non-group/world-writable files under the path in the script. Create the `postmarketos` aliases expected by the script.
3. Create `/etc/kebab/venus-firmware-manifest.json` with a `files` object mapping each expected filename to its local `bytes` and `sha256` values. Independently check that these files are the intended firmware; a manifest only checks installed files against the manifest you provide.
4. Compute the manifest file's SHA-256 and replace the placeholder in the local script with that value. Keep the manifest and firmware private.
5. Review the kernel release and unit dependencies, install the local template, then run its tests before explicitly enabling the service.

The manifest hash and firmware digests are intentionally absent from this branch. The service is specific to the documented r4 kernel release and local charge/slider units. It is not a complete kernel build, firmware acquisition, or deployment workflow. Kernel configuration, dependency modules, build grouping, recovery and device deployment guardrails remain local and must be reviewed for the target checkout.

## Patch and host checks

The touch parser change in `0009-input-synaptics-tcm-padding-tail.patch` accepts only whole-slot `0x5a` fill tails in the uncounted object loop. `test-tcm-protocol.c` must be compiled against the actual patched kernel header; it is not a self-contained copy of the parser:

```sh
cc -Wall -Wextra -Werror -fsanitize=address,undefined -g \
  -I "$KERNEL_DIR/drivers/input/touchscreen" \
  scripts/linux/test-tcm-protocol.c -o /tmp/test-tcm-protocol
/tmp/test-tcm-protocol
```

Run the Python checks from the repository root:

```sh
python3 scripts/linux/test-kebab-venus-decode-probe.py
python3 tests/Test-VenusInit.py
python3 tests/Test-ChargePolicy.py
```
