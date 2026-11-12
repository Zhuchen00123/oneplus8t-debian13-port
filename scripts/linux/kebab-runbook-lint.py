#!/usr/bin/env python3
"""Check that every kebab-* command the docs tell an operator to run exists.

Documentation drifts: a tool gets renamed, an alias is never installed, a unit
file is copied without the helper its ExecStart points at.  On SIM day that is
the worst possible time to discover it, and reading the page cannot prove it.

It extracts commands from markdown fenced code blocks and from inline spans that
start with `sudo`, resolves each program the way root would (the runbook always
says sudo), and for systemd units also resolves the ExecStart program.  Inline
bare names (interface names, payload markers, the community's scripts that we
deliberately do not install) are reported as mentions, not failures.

usage: kebab-runbook-lint.py FILE [FILE...]   (default: the two modem docs)
exit : 0 everything referenced exists, 1 something is missing, 2 usage/read error
"""
import os
import re
import subprocess
import sys

DEFAULT_DOCS = [
    "docs/sdx55-sim-day-runbook-20261112.md",
    "docs/sdx55-modemmanager-20261112.md",
]
PROG_RE = re.compile(r"^kebab-[a-z0-9-]+$")
UNIT_DIRS = ("/etc/systemd/system", "/usr/lib/systemd/system", "/lib/systemd/system")
SEARCH_DIRS = ("/usr/local/sbin", "/usr/local/bin", "/usr/local/lib/kebab-modem",
               "/usr/local/lib/kebab-mm")
# Names the docs discuss on purpose but which are NOT installed on this box.
# Every entry needs a reason, otherwise this list becomes a silencer.
EXPECTED_ABSENT = {
    "kebab-uim-provision": "community script (kebab-pmos); we ship sim-provision.sh instead",
}


def strip_prefix(tokens):
    while tokens and (tokens[0] in ("sudo", "-n", "-E", "-H") or "=" in tokens[0]):
        tokens = tokens[1:]
    return tokens


def split_commands(line):
    return [p for p in re.split(r"\s*(?:;|&&|\|\|)\s*", line) if p.strip()]


def candidates_in(text):
    """(prog, sub, src, hard) tuples; hard=False means a bare inline mention."""
    out = []
    in_code = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            for piece in split_commands(line):
                toks = strip_prefix(piece.split())
                if toks and PROG_RE.match(toks[0]):
                    sub = toks[1] if len(toks) > 1 and not toks[1].startswith("-") else ""
                    out.append((toks[0], sub, piece.strip(), True))
        for span in re.findall(r"`([^`]+)`", raw):
            span = span.strip()
            for piece in split_commands(span):
                toks = strip_prefix(piece.split())
                if not toks or not PROG_RE.match(toks[0]):
                    continue
                is_cmd = piece.strip().startswith("sudo") or len(toks) > 1
                sub = toks[1] if len(toks) > 1 and not toks[1].startswith("-") else ""
                out.append((toks[0], sub, piece.strip(), is_cmd))
    return out


def resolve(prog):
    rc = subprocess.run(["sh", "-c", f"command -v {prog}"], capture_output=True, text=True)
    if rc.returncode == 0 and rc.stdout.strip():
        return rc.stdout.strip()
    for d in SEARCH_DIRS:
        p = os.path.join(d, prog)
        if os.path.exists(p):
            return p
    return ""


def resolve_unit(name):
    """(unit_path, execstart_path) for a unit named after the tool."""
    for d in UNIT_DIRS:
        p = os.path.join(d, name + ".service")
        if os.path.exists(p):
            execstart = ""
            try:
                for ln in open(p, encoding="utf-8", errors="replace"):
                    if ln.startswith("ExecStart="):
                        execstart = ln.split("=", 1)[1].split()[0]
                        break
            except OSError:
                pass
            return p, execstart
    return "", ""


def check_installed_units():
    """Every kebab-* unit that IS installed must have a runnable ExecStart.

    This is the check that catches a half-done install: copying the .service
    without the helper script it starts makes an enable that can never work.
    """
    bad = 0
    d = "/etc/systemd/system"
    try:
        names = sorted(n for n in os.listdir(d) if n.startswith("kebab-") and n.endswith(".service"))
    except OSError:
        return 0
    for name in names:
        path = os.path.join(d, name)
        execstart = ""
        try:
            for ln in open(path, encoding="utf-8", errors="replace"):
                if ln.startswith("ExecStart="):
                    execstart = ln.split("=", 1)[1].split()[0]
                    break
        except OSError:
            continue
        if not execstart:
            print(f"ok      unit {name} (no ExecStart)")
        elif os.path.exists(execstart):
            print(f"ok      unit {name} -> {execstart}")
        else:
            print(f"MISSING ExecStart for installed unit {name}: {execstart}")
            bad += 1
    return bad


def main(argv):
    if len(argv) > 1 and argv[1] == "--selftest":
        return selftest()
    docs = argv[1:] or DEFAULT_DOCS
    entries = {}
    for doc in docs:
        if not os.path.exists(doc):
            print(f"skip {doc} (not present)")
            continue
        with open(doc, encoding="utf-8", errors="replace") as fh:
            cands = candidates_in(fh.read())
        for prog, sub, src, hard in cands:
            key = (prog, sub, hard)
            entries.setdefault(key, (doc, src))
        print(f"{doc}: {len(cands)} reference(s)")
    print()
    bad = 0
    for (prog, sub, hard), (doc, src) in sorted(entries.items()):
        if prog in EXPECTED_ABSENT:
            print(f"skip    {prog} - {EXPECTED_ABSENT[prog]}")
            continue
        path = resolve(prog)
        if path:
            if not sub:
                print(f"ok      {prog} -> {path}")
            elif sub in open(path, encoding="utf-8", errors="replace").read():
                print(f"ok      {prog} {sub}")
            else:
                print(f"MISSING subcommand: {prog} {sub}\n    {doc}: {src}")
                bad += 1
            continue
        unit, execstart = resolve_unit(prog)
        if unit:
            if not execstart:
                print(f"ok      {prog} (unit {unit}, no ExecStart)")
            elif os.path.exists(execstart):
                print(f"ok      {prog} (unit) -> {execstart}")
            else:
                print(f"MISSING ExecStart: {prog} -> {execstart} (unit {unit})\n    {doc}: {src}")
                bad += 1
            continue
        if hard:
            print(f"MISSING program: {prog}\n    {doc}: {src}")
            bad += 1
        else:
            print(f"mention {prog} (not a program; discussed in text)")
    print()
    print("== installed kebab-* units ==")
    bad += check_installed_units()
    print()
    print("RESULT:", "PASS" if bad == 0 else f"{bad} missing")
    return 0 if bad == 0 else 1


def selftest():
    """Pin the parsing rules that decide what counts as a command."""
    cases = [
        ("sudo kebab-sim-day --hold 60", "kebab-sim-day"),
        ("sudo -n LC_ALL=C kebab-modem-qmap up --mux 1", "kebab-modem-qmap"),
        ("$ sudo kebab-modem-status", "kebab-modem-status"),
    ]
    bad = 0
    for line, want in cases:
        line = re.sub(r"^\$\s+", "", line.strip())
        toks = strip_prefix(line.split())
        got = toks[0] if toks else ""
        print(("ok   " if got == want else "FAIL ") + f"prefix strip: {line!r} -> {got!r}")
        bad += 0 if got == want else 1
    split_cases = [
        ("sudo kebab-modem-qmap down; sudo kebab-modem-qmap unload", 2),
        ("kebab-modem-qmap status | head -5", 1),
        ("sudo a && sudo b || sudo c", 3),
    ]
    for line, want in split_cases:
        got = len(split_commands(line))
        print(("ok   " if got == want else "FAIL ") + f"split: {line!r} -> {got} piece(s)")
        bad += 0 if got == want else 1
    if "kebab-qmap-tun-7" in "`kebab-qmap-tun-7`":
        pass
    print("RESULT:", "PASS" if bad == 0 else f"{bad} failed")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except OSError as exc:
        print(f"cannot read: {exc}", file=sys.stderr)
        sys.exit(2)
