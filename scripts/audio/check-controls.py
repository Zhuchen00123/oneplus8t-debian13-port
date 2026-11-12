#!/usr/bin/env python3
"""Check the live card against the frozen control map.

The map for this run was seeded before the codec was bound, so the first thing
that happens after load is this comparison: name present, same type, same count.
A mismatch aborts the run before any routing write.
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("mm", os.path.join(HERE, "make-manifests.py"))
mm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mm)


def main():
    srv, card = sys.argv[1], int(sys.argv[2])
    frozen = json.load(open(os.path.join(srv, "control-map.json")))["controls"]
    live_all = mm.load_all_controls(card)
    live = {}
    for name in frozen:
        entry = mm.read_control(card, name, live_all)
        if entry is None:
            print("FAIL missing on live card: %s" % name)
            return 1
        live[name] = entry
    bad = 0
    for name in sorted(frozen):
        a, b = frozen[name], live[name]
        for field in ("type", "count"):
            if a.get(field) != b.get(field):
                print("FAIL %s: %s frozen=%r live=%r" % (name, field, a.get(field), b.get(field)))
                bad += 1
        if a.get("items") != b.get("items"):
            print("FAIL %s: enum items differ" % name)
            bad += 1
    out = {"card_index": card, "checked": len(live), "mismatches": bad,
           "live": live, "frozen_provenance": json.load(
               open(os.path.join(srv, "control-map.json"))).get("provenance")}
    with open(os.path.join(srv, "control-map-live.json"), "w") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
    os.chmod(os.path.join(srv, "control-map-live.json"), 0o600)
    for name in sorted(live):
        print("  %-42s %-5s %s" % (name, live[name].get("type"),
                                   live[name].get("value")))
    print("VERDICT %s (%d controls, %d mismatches)" % ("pass" if bad == 0 else "FAIL",
                                                       len(live), bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
