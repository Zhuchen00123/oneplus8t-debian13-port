#!/usr/bin/env python3
"""Stage a verified kernel group into a host rootfs, without phone commands."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootfs", required=True, type=Path)
    parser.add_argument("--kernel-artifacts", required=True, type=Path)
    args = parser.parse_args()
    root, kernel = args.rootfs.resolve(), args.kernel_artifacts.resolve()
    if root == Path("/") or root.name != "rootfs" or not (root / "etc/debian_version").is_file():
        raise ValueError("Expected a separate host build rootfs")
    if not (root / "boot").is_dir() or not (root / "boot").resolve().is_relative_to(root):
        raise ValueError("Expected boot directory inside host rootfs")
    manifest = json.loads((kernel / "kernel-manifest.json").read_text())
    for name, wanted in manifest["files_sha256"].items():
        if Path(name).name != name or sha(kernel / name) != wanted:
            raise ValueError("Kernel group hash/path mismatch")
    release = (kernel / "kernel.release").read_text().strip()
    if "/" in release or release in ("", ".", ".."):
        raise ValueError("Invalid kernel release")
    destination = root / "usr/lib/modules" / release
    if destination.exists() or not destination.parent.resolve().is_relative_to(root):
        raise ValueError("Preserve existing modules; choose a fresh host stage")
    with tarfile.open(kernel / "modules.tar.gz") as archive:
        members = archive.getmembers()
        seen = set()
        for member in members:
            parts = PurePosixPath(member.name).parts
            if member.name.startswith("/") or ".." in parts or parts[:2] != ("lib", "modules"):
                raise ValueError("Module archive path escape")
            if not (member.isfile() or member.isdir()) or member.name in seen:
                raise ValueError("Unexpected module archive entry")
            if len(parts) > 2 and parts[2] != release:
                raise ValueError("Module archive version mismatch")
            seen.add(member.name)
        for member in members:
            if len(PurePosixPath(member.name).parts) < 3:
                continue
            target = root / "usr" / member.name
            if not target.parent.resolve().is_relative_to(root):
                raise ValueError("Module destination escapes rootfs")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())
                target.chmod(member.mode & 0o777)
    for source, target in (("Image.gz", "vmlinuz-" + release),
                           ("kernel.config", "config-" + release),
                           ("System.map", "System.map-" + release)):
        if not (root / "boot" / target).resolve().is_relative_to(root):
            raise ValueError("Boot destination escapes rootfs")
        shutil.copyfile(kernel / source, root / "boot" / target)
    dtb = root / "boot/dtb"
    if not dtb.resolve().is_relative_to(root):
        raise ValueError("DTB destination escapes rootfs")
    dtb.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(kernel / "sm8250-oneplus-kebab.dtb", dtb / "sm8250-oneplus-kebab.dtb")
    subprocess.run(["depmod", "-b", str(root), release], check=True)
    modules = list(destination.rglob("*.ko*"))
    count = json.loads((kernel / "module-verification.json").read_text())["modules_checked"]
    if len(modules) != count:
        raise ValueError("Staged module count mismatch")
    print(json.dumps({"kernelrelease": release, "modules": count, "kernel_group_verified": True,
                      "phone_commands_executed": False}))


if __name__ == "__main__":
    main()
