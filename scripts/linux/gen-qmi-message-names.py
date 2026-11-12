#!/usr/bin/env python3
"""Generate qmi-messages.json: message id -> name, per QMI service.

Used to turn "Get Supported Messages" bitmaps into readable lists, so a check
like "is Set Data Format supported by this firmware?" has an answer instead of
a hex blob.

usage: gen-qmi-message-names.py qmi-service-wds.json [more.json ...] > out.json
"""
import json
import os
import re
import sys

LINE_COMMENT = re.compile(r"^\s*//.*$", re.M)
TRAILING = re.compile(r"//[^\n\"]*$", re.M)
TRAILING_COMMA = re.compile(r",\s*([}\]])")


def load(path):
    text = open(path, encoding="utf-8").read()
    text = LINE_COMMENT.sub("", text)
    text = TRAILING.sub("", text)
    text = TRAILING_COMMA.sub(r"\1", text)
    return json.loads(text)


def main(paths):
    out = {"source": "libqmi data/*.json"}
    for path in paths:
        doc = load(path)
        svc = next((x for x in doc if x.get("type") == "Service"), None)
        if not svc:
            continue
        name = svc["name"]
        msgs = {}
        for item in doc:
            if item.get("type") != "Message":
                continue
            mid = item.get("id")
            if not mid:
                continue
            try:
                msgs[str(int(mid, 16))] = item["name"]
            except ValueError:
                continue
        out[name] = dict(sorted(msgs.items(), key=lambda kv: int(kv[0])))
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: gen-qmi-message-names.py qmi-service-*.json [...]")
    main(sys.argv[1:])
