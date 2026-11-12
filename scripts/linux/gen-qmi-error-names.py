#!/usr/bin/env python3
"""Generate qmi-errors.json from libqmi's qmi-errors.h.

The modem answers almost every failure with just a number.  error 14 is
"call-failed", 74 is "information-unavailable" - names turn a dead end into a
next step.

usage: gen-qmi-error-names.py path/to/qmi-errors.h > qmi-errors.json
"""
import json
import re
import sys

RX = re.compile(r"^\s*QMI_PROTOCOL_ERROR_([A-Z0-9_]+)\s*=\s*(\d+)\s*,?")


def main(path):
    errors = {}
    for line in open(path, encoding="utf-8"):
        m = RX.match(line)
        if not m:
            continue
        name, value = m.group(1), int(m.group(2))
        errors.setdefault(str(value), name.lower().replace("_", "-"))
    out = {"source": "libqmi qmi-errors.h", "errors": dict(sorted(errors.items(), key=lambda kv: int(kv[0])))}
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: gen-qmi-error-names.py qmi-errors.h")
    main(sys.argv[1])
