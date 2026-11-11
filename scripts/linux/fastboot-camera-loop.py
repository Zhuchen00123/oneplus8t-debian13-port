#!/usr/bin/env python3
"""Automated camera test loop for kebab (host side).

Two modes:
  --no-reboot            run camera-capture-test.py on the running r4 and collect results
  --boot-image PATH      fastboot boot a candidate, test, then reboot back to r4

The boot path never flashes or writes a partition; it only runs "fastboot boot".
Entering fastboot uses the installed PON helper (--entry pon) by default. The
helper writes only the volatile PON reason; it does not write a partition. The
script refuses to reboot when boot_a is not successful/tries>=3.

"""
import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "local/ssh/kebab-server.conf"
DEFAULT_REMOTE_SCRIPT = ROOT / "scripts/linux/camera-capture-test.py"
DEFAULT_SLOT_SCRIPT = ROOT / "scripts/linux/boot-slot-state.py"
MAX_BOOT_BYTES = 100663296
FORBIDDEN = ("flash", "erase", "format", "set_active", "-w", "wipe")


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def run(cmd, timeout=60, dry=False, check=False):
    log("run: " + " ".join(str(x) for x in cmd))
    if dry:
        return subprocess.CompletedProcess(cmd, 0, "", "")
    try:
        r = subprocess.run([str(x) for x in cmd], text=True, capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise RuntimeError("missing command: %s" % cmd[0]) from exc
    if check and r.returncode != 0:
        raise RuntimeError("command failed (%s): %s" % (r.returncode, " ".join(map(str, cmd))))
    return r


def ssh_cmd(a, remote, timeout=30, dry=False):
    return run(["ssh", "-F", str(a.ssh_config), "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                a.ssh_host, remote], timeout=timeout, dry=dry)


def scp_to(a, local, remote, dry=False):
    return run(["scp", "-F", str(a.ssh_config), "-q", str(local), "%s:%s" % (a.ssh_host, remote)],
               timeout=60, dry=dry, check=True)


def scp_from(a, remote, local, dry=False):
    return run(["scp", "-F", str(a.ssh_config), "-q", "-r", "%s:%s" % (a.ssh_host, remote), str(local)],
               timeout=120, dry=dry, check=True)


def wait_ssh(a, timeout, dry=False):
    end = time.time() + timeout
    while time.time() < end:
        r = ssh_cmd(a, "true", timeout=8, dry=dry)
        if dry or r.returncode == 0:
            return True
        time.sleep(3)
    return False


def boot_id(a, dry=False):
    r = ssh_cmd(a, "cat /proc/sys/kernel/random/boot_id", timeout=10, dry=dry)
    return r.stdout.strip() if r.returncode == 0 else ""


def slot_state(a, dry=False):
    if dry:
        return {"boot_a": {"active": True, "tries": 7, "successful": True, "unbootable": False},
                "boot_b": {}, "dry_run": True}
    scp_to(a, a.slot_script, "/tmp/boot-slot-state.py", dry=dry)
    r = ssh_cmd(a, "sudo -n python3 /tmp/boot-slot-state.py", timeout=30, dry=dry)
    if r.returncode != 0:
        raise RuntimeError("slot-state read failed: " + (r.stderr or r.stdout)[-500:])
    text = r.stdout
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise RuntimeError("slot-state produced no JSON")
    return json.loads(text[start:end + 1])


def check_slot_health(a, dry=False):
    state = slot_state(a, dry=dry)
    boot_a = state.get("boot_a") or {}
    problems = []
    if not boot_a.get("active"):
        problems.append("active=0")
    if not boot_a.get("successful"):
        problems.append("successful=0")
    if boot_a.get("unbootable"):
        problems.append("unbootable=1")
    if int(boot_a.get("tries") or 0) < 3:
        problems.append("tries<3")
    if problems:
        raise RuntimeError("boot_a slot unhealthy (%s); refusing automatic reboot/loop" % ", ".join(problems))
    log("slot health: boot_a active successful tries=%s" % boot_a.get("tries"))
    return state


def wait_fastboot(a, timeout, dry=False):
    end = time.time() + timeout
    while time.time() < end:
        r = run(["fastboot", "devices"], timeout=10, dry=dry)
        if dry:
            return True, a.serial or ""
        lines = [x for x in r.stdout.splitlines() if x.strip()]
        if lines:
            serial = lines[0].split()[0]
            if not a.serial or serial == a.serial:
                return True, serial
        time.sleep(2)
    return False, ""


def fastboot(a, args, timeout=60, dry=False):
    base = ["fastboot"]
    if a.serial:
        base += ["-s", a.serial]
    return run(base + args, timeout=timeout, dry=dry)


def run_remote_test(a, camera, run_dir, dry=False):
    scp_to(a, a.remote_script, "/tmp/camera-capture-test.py", dry=dry)
    r = ssh_cmd(a, "python3 /tmp/camera-capture-test.py --camera %s --output-dir /tmp/camera-lab" % camera,
                timeout=120, dry=dry)
    if dry:
        return {"dry_run": True, "camera": camera, "ok": True}
    text = r.stdout
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0:
        return {"ok": False, "result": "fail", "error": "no json from remote test",
                "stdout": text[-2000:], "stderr": r.stderr[-2000:]}
    data = json.loads(text[start:end + 1])
    if data.get("png"):
        try:
            scp_from(a, data["png"], run_dir / (camera + "-preview.png"), dry=dry)
        except Exception as exc:
            data["collect_error"] = str(exc)
    if data.get("raw"):
        try:
            scp_from(a, data["raw"], run_dir / (camera + "-frame.raw"), dry=dry)
        except Exception as exc:
            data["collect_raw_error"] = str(exc)
    (run_dir / (camera + ".json")).write_text(json.dumps(data, indent=2) + "\n")
    return data


def boot_layout(data):
    if len(data) < 1660 or data[:8] != b"ANDROID!":
        raise RuntimeError("not an Android boot image")
    kernel_size = struct.unpack_from("<I", data, 8)[0]
    ramdisk_size = struct.unpack_from("<I", data, 16)[0]
    ramdisk_addr = struct.unpack_from("<I", data, 20)[0]
    page_size = struct.unpack_from("<I", data, 36)[0]
    header_version = struct.unpack_from("<I", data, 40)[0]
    dtb_size = struct.unpack_from("<I", data, 1648)[0]
    dtb_addr = struct.unpack_from("<Q", data, 1652)[0]
    if page_size not in (2048, 4096, 8192, 16384):
        raise RuntimeError("unexpected boot page size: %d" % page_size)
    if kernel_size == 0 or ramdisk_size == 0:
        raise RuntimeError("boot image kernel/ramdisk is empty")
    if header_version >= 2 and dtb_size:
        if dtb_addr == 0:
            raise RuntimeError("boot image has a DTB but dtb_addr=0")
        if ramdisk_addr + ramdisk_size > dtb_addr:
            raise RuntimeError(
                "boot image layout overlap: ramdisk ends 0x%x > dtb 0x%x; rebuild with --dtb_offset >= 0x%x"
                % (ramdisk_addr + ramdisk_size, dtb_addr, ramdisk_addr + ramdisk_size))
    return {"kernel_size": kernel_size, "ramdisk_size": ramdisk_size,
            "ramdisk_addr": ramdisk_addr, "dtb_size": dtb_size, "dtb_addr": dtb_addr,
            "header_version": header_version}


def preflight_boot_image(a, dry=False):
    p = Path(a.boot_image)
    if not p.is_file():
        raise RuntimeError("boot image not found: %s" % p)
    size = p.stat().st_size
    if size <= 0 or size > MAX_BOOT_BYTES:
        raise RuntimeError("boot image size out of range: %d" % size)
    data = p.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    layout = boot_layout(data)
    log("boot image: %s bytes=%d sha256=%s ramdisk_end=0x%x dtb=0x%x" %
        (p, size, sha, layout["ramdisk_addr"] + layout["ramdisk_size"], layout["dtb_addr"]))
    return size, sha


def enter_fastboot(a, dry=False):
    if a.entry == "none":
        return wait_fastboot(a, a.fastboot_timeout, dry=dry)
    if a.entry == "manual":
        log("请把设备进入 fastboot（音量键/电源键或现有手段）；等待 %ds" % a.fastboot_timeout)
        return wait_fastboot(a, a.fastboot_timeout, dry=dry)
    if a.entry == "pon":
        probe = ssh_cmd(a, "test -x /usr/local/sbin/kebab-reboot-fastboot && echo ok", timeout=10, dry=dry)
        if not dry and probe.returncode != 0:
            raise RuntimeError("PON helper not installed at /usr/local/sbin/kebab-reboot-fastboot")
        # SSH disconnects when the helper reboots; ignore that exit status.
        ssh_cmd(a, "sudo -n /usr/local/sbin/kebab-reboot-fastboot", timeout=20, dry=dry)
        return wait_fastboot(a, a.fastboot_timeout, dry=dry)
    if a.entry == "command":
        if not a.entry_command:
            raise RuntimeError("--entry-command is required with --entry command")
        r = ssh_cmd(a, a.entry_command, timeout=20, dry=dry)
        if not dry and r.returncode != 0:
            raise RuntimeError("entry command failed: " + r.stderr[-500:])
        return wait_fastboot(a, a.fastboot_timeout, dry=dry)
    if a.entry == "adb":
        r = run(["adb"] + (["-s", a.serial] if a.serial else []) + ["reboot", "bootloader"],
                timeout=30, dry=dry)
        if not dry and r.returncode != 0:
            raise RuntimeError("adb reboot bootloader failed")
        return wait_fastboot(a, a.fastboot_timeout, dry=dry)
    raise RuntimeError("unknown entry mode: " + a.entry)


def run_no_reboot(a, run_dir):
    if not wait_ssh(a, 15, dry=a.dry_run):
        raise RuntimeError("SSH not reachable")
    before = boot_id(a, dry=a.dry_run)
    state = slot_state(a, dry=a.dry_run)
    (run_dir / "pre.json").write_text(json.dumps({"boot_id": before, "slot": state}, indent=2) + "\n")
    results = {}
    cameras = ["front", "rear"] if a.camera == "both" else [a.camera]
    for cam in cameras:
        results[cam] = run_remote_test(a, cam, run_dir, dry=a.dry_run)
    after = boot_id(a, dry=a.dry_run)
    return {"mode": "no-reboot", "boot_id_before": before, "boot_id_after": after,
            "slot": state, "cameras": results, "ok": any(x.get("ok") for x in results.values())}


def run_boot(a, run_dir):
    size, sha = preflight_boot_image(a, dry=a.dry_run)
    if not wait_ssh(a, 15, dry=a.dry_run):
        raise RuntimeError("SSH not reachable before boot")
    before = boot_id(a, dry=a.dry_run)
    baseline_kernel = ssh_cmd(a, "uname -r", timeout=10, dry=a.dry_run).stdout.strip()
    state = check_slot_health(a, dry=a.dry_run)
    (run_dir / "pre.json").write_text(json.dumps({"boot_id": before, "kernel": baseline_kernel, "slot": state}, indent=2) + "\n")
    ok, serial = enter_fastboot(a, dry=a.dry_run)
    if not ok:
        raise RuntimeError("device did not enter fastboot")
    if serial:
        a.serial = serial
    r = fastboot(a, ["getvar", "product"], dry=a.dry_run)
    product = r.stderr + r.stdout
    log("fastboot product: " + product.strip()[-200:])
    if not a.dry_run and not any(x in product.lower() for x in ("kebab", "kona", "sm8250")):
        raise RuntimeError("fastboot product does not look like kebab/kona")
    fastboot(a, ["getvar", "current-slot"], dry=a.dry_run)
    log("fastboot boot candidate")
    boot = fastboot(a, ["boot", str(Path(a.boot_image))], timeout=60, dry=a.dry_run)
    if not a.dry_run and boot.returncode != 0:
        err = (boot.stderr + boot.stdout)[-800:]
        log("fastboot boot failed; returning to baseline")
        fastboot(a, ["reboot"], dry=a.dry_run)
        wait_ssh(a, 90)
        return {"mode": "boot", "ok": False, "error": "fastboot boot failed: " + err,
                "boot_id_before": before, "slot": state, "image_sha256": sha, "image_bytes": size}
    if not a.dry_run and not wait_ssh(a, a.ssh_timeout):
        log("candidate did not reach SSH; rebooting")
        fastboot(a, ["reboot"], dry=a.dry_run)
        wait_ssh(a, 60)
        return {"mode": "boot", "ok": False, "error": "candidate did not reach SSH",
                "boot_id_before": before, "slot": state, "image_sha256": sha, "image_bytes": size}
    cameras = ["front", "rear"] if a.camera == "both" else [a.camera]
    results = {}
    for cam in cameras:
        results[cam] = run_remote_test(a, cam, run_dir, dry=a.dry_run)
    candidate_id = boot_id(a, dry=a.dry_run)
    if not a.dry_run:
        ssh_cmd(a, "sudo -n systemctl reboot --no-block", timeout=15)
        # Wait until the in-memory candidate is gone; boot_id changes on reboot.
        for _ in range(90):
            time.sleep(2)
            cur = boot_id(a)
            if not cur or cur != candidate_id:
                break
        wait_ssh(a, 120)
    after = boot_id(a, dry=a.dry_run)
    after_kernel = ssh_cmd(a, "uname -r", timeout=10, dry=a.dry_run).stdout.strip()
    returned = bool(a.dry_run or (after and after != candidate_id and after_kernel == baseline_kernel))
    return {"mode": "boot", "ok": bool(any(x.get("ok") for x in results.values()) and returned),
            "boot_id_before": before, "boot_id_after": after, "slot": state,
            "baseline_kernel": baseline_kernel, "candidate_boot_id": candidate_id,
            "image_sha256": sha, "image_bytes": size, "cameras": results,
            "returned_to_baseline": returned}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-reboot", action="store_true", help="test the running r4 only")
    ap.add_argument("--boot-image", help="candidate boot.img for fastboot boot")
    ap.add_argument("--entry", choices=["pon", "manual", "command", "adb", "none"], default="pon")
    ap.add_argument("--entry-command", help="remote shell command to enter fastboot")
    ap.add_argument("--serial", help="adb/fastboot serial")
    ap.add_argument("--ssh-host", default="kebab")
    ap.add_argument("--ssh-config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--remote-script", default=str(DEFAULT_REMOTE_SCRIPT))
    ap.add_argument("--slot-script", default=str(DEFAULT_SLOT_SCRIPT))
    ap.add_argument("--camera", choices=["front", "rear", "both"], default="front")
    ap.add_argument("--output-dir", default=str(ROOT / "local/camera-runs"))
    ap.add_argument("--fastboot-timeout", type=int, default=180)
    ap.add_argument("--ssh-timeout", type=int, default=180)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.no_reboot and a.boot_image:
        raise SystemExit("choose either --no-reboot or --boot-image")
    if not a.no_reboot and not a.boot_image:
        raise SystemExit("one of --no-reboot or --boot-image is required")
    if a.boot_image:
        for bad in FORBIDDEN:
            if bad in Path(a.boot_image).name:
                raise SystemExit("refusing suspicious image name: " + a.boot_image)

    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    run_dir = Path(a.output_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    lock = Path(a.output_dir) / ".lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        raise SystemExit("another camera loop is running (lock: %s)" % lock)

    record = {"run_id": run_id, "args": vars(a), "started": time.time()}
    try:
        if a.dry_run:
            log("dry-run: no device commands will be executed")
        if a.no_reboot:
            res = run_no_reboot(a, run_dir)
        else:
            res = run_boot(a, run_dir)
        record.update(res)
    except Exception as exc:
        record.update({"ok": False, "error": str(exc)})
        log("ERROR: " + str(exc))
    finally:
        record["finished"] = time.time()
        (run_dir / "run.json").write_text(json.dumps(record, indent=2) + "\n")
        try:
            lock.unlink()
        except FileNotFoundError:
            pass
        print(json.dumps({"run_dir": str(run_dir), "ok": record.get("ok"),
                          "error": record.get("error")}, indent=2))
    return 0 if record.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
