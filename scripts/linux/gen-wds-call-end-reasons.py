#!/usr/bin/env python3
"""Generate wds-call-end-reasons.json from libqmi's qmi-enums-wds.h.

A failed WDS Start Network returns both a flat "call end reason" and a
"verbose call end reason" (a type plus a type-specific code).  Without the
names those are just numbers, and on SIM day "... error 14" tells you nothing
about whether the APN, the subscription, or the radio was at fault.

usage: gen-wds-call-end-reasons.py path/to/qmi-enums-wds.h > wds-call-end-reasons.json
"""
import json
import re
import sys

RX = re.compile(r"^\s*(QMI_WDS_[A-Z0-9_]+)\s*=\s*(\d+)\s*,?\s*$")

CALL_END_GROUPS = {
    "QMI_WDS_CALL_END_REASON_GENERIC_": "generic",
    "QMI_WDS_CALL_END_REASON_CDMA_": "cdma",
    "QMI_WDS_CALL_END_REASON_GSM_": "gsm-wcdma",
    "QMI_WDS_CALL_END_REASON_WCDMA_": "gsm-wcdma",
    "QMI_WDS_CALL_END_REASON_EVDO_": "evdo",
}

VERBOSE_GROUPS = {
    "QMI_WDS_VERBOSE_CALL_END_REASON_MIP_": "mip",
    "QMI_WDS_VERBOSE_CALL_END_REASON_INTERNAL_": "internal",
    "QMI_WDS_VERBOSE_CALL_END_REASON_CM_": "cm",
    "QMI_WDS_VERBOSE_CALL_END_REASON_3GPP_": "3gpp",
    "QMI_WDS_VERBOSE_CALL_END_REASON_PPP_": "ppp",
    "QMI_WDS_VERBOSE_CALL_END_REASON_EHRPD_": "ehrpd",
    "QMI_WDS_VERBOSE_CALL_END_REASON_IPV6_": "ipv6",
}


def slug(name):
    return name.lower().replace("_", "-")


def main(path):
    call_end = {k: {} for k in set(CALL_END_GROUPS.values())}
    verbose = {k: {} for k in set(VERBOSE_GROUPS.values())}
    vtypes = {}
    for line in open(path, encoding="utf-8"):
        m = RX.match(line)
        if not m:
            continue
        name, value = m.group(1), int(m.group(2))
        if name.startswith("QMI_WDS_VERBOSE_CALL_END_REASON_TYPE_"):
            vtypes[str(value)] = slug(name[len("QMI_WDS_VERBOSE_CALL_END_REASON_TYPE_"):])
            continue
        for prefix, group in VERBOSE_GROUPS.items():
            if name.startswith(prefix):
                verbose[group][str(value)] = slug(name[len(prefix):])
                break
        else:
            for prefix, group in CALL_END_GROUPS.items():
                if name.startswith(prefix):
                    call_end[group][str(value)] = slug(name[len(prefix):])
                    break
    out = {
        "source": "libqmi qmi-enums-wds.h",
        "call_end": {k: dict(sorted(v.items(), key=lambda kv: int(kv[0]))) for k, v in call_end.items() if v},
        "verbose_type": dict(sorted(vtypes.items(), key=lambda kv: int(kv[0]))),
        "verbose": {k: dict(sorted(v.items(), key=lambda kv: int(kv[0]))) for k, v in verbose.items() if v},
    }
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: gen-wds-call-end-reasons.py qmi-enums-wds.h")
    main(sys.argv[1])
