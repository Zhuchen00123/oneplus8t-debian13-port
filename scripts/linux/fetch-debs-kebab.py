#!/usr/bin/env python3
"""fetch-debs-kebab.py -- resolve and fetch Debian .deb files without apt.

Why this exists
    The kebab server has no working DNS (the default route points at the USB
    router, which does not resolve), but wlan0 egress works when the address is
    pinned.  apt therefore cannot install anything, and the ModemManager build
    needs meson, ninja and seven -dev packages.  This script reads the real
    Packages index, resolves the dependency closure the way apt would (Depends
    only -- Recommends is ignored on purpose), and either prints the plan
    (--list, the default) or downloads and installs it in one dpkg run.

Usage
    sudo fetch-debs-kebab.py --list  --packages "meson ninja-build libglib2.0-dev"
    sudo fetch-debs-kebab.py --apply --packages "meson ninja-build libglib2.0-dev"
"""

import argparse
import lzma
import os
import re
import subprocess
import sys

MIRROR_IP = "151.101.2.132"
MIRROR_HOST = "deb.debian.org"
DEFAULT_SUITE = "trixie"
DEFAULT_ARCH = "arm64"
DEFAULT_DIR = "/srv/modem-debs"


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def fetch_index(suite, arch, cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, "Packages-%s-%s.xz" % (suite, arch))
    if not os.path.exists(path) or os.path.getsize(path) < 1024:
        url = "http://%s/debian/dists/%s/main/binary-%s/Packages.xz" % (MIRROR_HOST, suite, arch)
        cmd = ["curl", "-sS", "--interface", "wlan0",
               "--resolve", "%s:80:%s" % (MIRROR_HOST, MIRROR_IP),
               "--max-time", "300", "-o", path, url]
        res = run(cmd)
        if res.returncode != 0:
            raise SystemExit("could not fetch Packages index: %s" % res.stderr.strip())
    with lzma.open(path, "rt", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def parse_index(text):
    pkgs = {}
    for stanza in text.split("\n\n"):
        if not stanza.strip():
            continue
        fields = {}
        last = None
        for line in stanza.splitlines():
            if line.startswith(" ") and last:
                fields[last] += " " + line.strip()
                continue
            if ":" in line:
                key, _, val = line.partition(":")
                last = key.strip()
                fields[last] = val.strip()
        name = fields.get("Package")
        if name and fields.get("Filename"):
            pkgs[name] = {
                "version": fields.get("Version", ""),
                "filename": fields["Filename"],
                "size": int(fields.get("Size", "0") or 0),
                "depends": fields.get("Depends", ""),
            }
    return pkgs


def installed_versions():
    """One dpkg-query for the whole system; per-package calls are far too slow."""
    fmt = "${Package}|${Status}|${Version}"
    res = run(["dpkg-query", "-W", "-f=" + fmt + "\\n"])
    out = {}
    if res.returncode != 0:
        return out
    for line in res.stdout.splitlines():
        parts = line.split("|")
        if len(parts) != 3:
            continue
        name, status, ver = parts
        if "install ok installed" in status:
            out[name] = ver
    return out


def strip_dep(dep):
    dep = dep.strip()
    m = re.match(r"^([A-Za-z0-9.+\-]+)(?::[a-z0-9]+)?(?:\s*\((.*?)\))?$", dep)
    if not m:
        return None, None
    return m.group(1), (m.group(2) or None)


def resolve(roots, index, installed):
    seen, order, missing, ignored = set(), [], [], []
    stack = list(roots)
    while stack:
        pkg = stack.pop()
        if pkg in seen:
            continue
        seen.add(pkg)
        if pkg in installed:
            continue
        if pkg not in index:
            missing.append(pkg)
            continue
        order.append(pkg)
        for group in index[pkg]["depends"].split(","):
            group = group.strip()
            if not group:
                continue
            chosen = None
            for alt in group.split("|"):
                name, constraint = strip_dep(alt)
                if not name:
                    continue
                if name in installed:
                    chosen = None
                    break
                if name in index:
                    chosen = name
                    if constraint:
                        ignored.append("%s %s" % (name, constraint))
                    break
            if chosen:
                stack.append(chosen)
    return order, missing, ignored


def main():
    ap = argparse.ArgumentParser(description="resolve/fetch .deb files on a server without apt")
    ap.add_argument("--packages", help="space separated package names")
    ap.add_argument("--packages-file", help="file with one package per line")
    ap.add_argument("--apply", action="store_true", help="download and install (default: plan only)")
    ap.add_argument("--list", action="store_true", help="print the plan (default)")
    ap.add_argument("--suite", default=DEFAULT_SUITE)
    ap.add_argument("--arch", default=DEFAULT_ARCH)
    ap.add_argument("--dir", default=DEFAULT_DIR)
    args = ap.parse_args()

    roots = []
    if args.packages:
        roots += args.packages.split()
    if args.packages_file:
        with open(args.packages_file) as fh:
            roots += [l.strip() for l in fh if l.strip() and not l.startswith("#")]
    if not roots:
        ap.error("give --packages or --packages-file")

    print("== kebab .deb fetcher ==")
    print("   suite %s / arch %s / mirror %s (%s)"
          % (args.suite, args.arch, MIRROR_HOST, MIRROR_IP))
    text = fetch_index(args.suite, args.arch, args.dir)
    index = parse_index(text)
    print("   index parsed: %d packages" % len(index))

    all_installed = installed_versions()
    installed = {p: v for p, v in all_installed.items() if p in index}
    print("   installed already: %d of those (%d packages on the system)"
          % (len(installed), len(all_installed)))

    order, missing, ignored = resolve(roots, index, installed)
    total = sum(index[p]["size"] for p in order)
    print("")
    print("-- plan (%d package(s), %.1f MB)" % (len(order), total / 1048576.0))
    for p in sorted(order):
        print("   %-28s %-22s %8.1f kB" % (p, index[p]["version"], index[p]["size"] / 1024.0))
    if ignored:
        print("")
        print("-- version constraints not checked (%d): using the suite's version"
              % len(set(ignored)))
        for c in sorted(set(ignored))[:12]:
            print("   %s" % c)
    if missing:
        print("")
        print("-- NOT FOUND in this index (%d): %s" % (len(missing), " ".join(sorted(missing))))
        return 2

    if not args.apply:
        print("")
        print("   plan only -- nothing downloaded, nothing installed.")
        print("   re-run with --apply to download and install in one dpkg run.")
        return 0

    os.makedirs(args.dir, exist_ok=True)
    debs = []
    print("")
    for i, p in enumerate(sorted(order), 1):
        url = "http://%s/debian/%s" % (MIRROR_HOST, index[p]["filename"])
        dest = os.path.join(args.dir, os.path.basename(index[p]["filename"]))
        print("   [%2d/%2d] %s" % (i, len(order), os.path.basename(dest)))
        if not os.path.exists(dest) or os.path.getsize(dest) != index[p]["size"]:
            cmd = ["curl", "-sS", "--interface", "wlan0",
                   "--resolve", "%s:80:%s" % (MIRROR_HOST, MIRROR_IP),
                   "--max-time", "300", "-o", dest, url]
            res = run(cmd)
            if res.returncode != 0:
                print("        FAILED: %s" % res.stderr.strip())
                return 3
        if os.path.getsize(dest) != index[p]["size"]:
            print("        SIZE MISMATCH (got %d, expected %d)"
                  % (os.path.getsize(dest), index[p]["size"]))
            return 3
        debs.append(dest)

    print("")
    print("-- installing %d package(s) in one dpkg run" % len(debs))
    res = run(["dpkg", "-i"] + debs)
    sys.stdout.write(res.stdout)
    sys.stderr.write(res.stderr)
    if res.returncode != 0:
        print("dpkg returned %d -- inspect the output above; nothing else was changed"
              % res.returncode)
        return 4
    print("   installed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
