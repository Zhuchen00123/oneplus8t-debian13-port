#!/usr/bin/env python3
"""Summarise a kebab modem soak CSV (see /srv/modem-20261112/soak2.sh).

The soak script writes one row per sample:
  ts,uptime,mhi1,opmode,node3,sats,fix,rssi,efs_sessions,errfatal,qrtr_err,wwan_ports,mhi_devs

This tool turns that into a verdict: how many samples, gaps, and every row that
does not match the expected healthy state.  Exit status is 0 when the soak is
healthy, 1 when any anomaly was found, 2 on a malformed file.
"""
import argparse
import sys

EXPECT = {
    "mhi1": 1,
    "opmode": "online",
    "node3": 3,
    "wwan_ports": 4,
    "mhi_devs": 10,
}


def load(path):
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        header = fh.readline().strip().split(",")
        for line in fh:
            line = line.strip()
            if not line or not line[0].isdigit():
                continue          # e.g. the trailing "soak2 done" marker
            parts = line.split(",")
            if len(parts) != len(header):
                continue
            rows.append(dict(zip(header, parts)))
    return header, rows


def num(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv", help="soak CSV path")
    ap.add_argument("--interval", type=int, default=60, help="expected sample interval in seconds (default 60)")
    ap.add_argument("--quiet", action="store_true", help="only print the verdict line")
    args = ap.parse_args(argv)

    header, rows = load(args.csv)
    if not rows:
        print("no data rows", file=sys.stderr)
        return 2

    anomalies = []
    for r in rows:
        for key, want in EXPECT.items():
            got = r.get(key)
            if key in ("mhi1", "node3", "wwan_ports", "mhi_devs"):
                if num(got) != want:
                    anomalies.append(f"{r['ts']}: {key}={got} (want {want})")
            elif got != want:
                anomalies.append(f"{r['ts']}: {key}={got} (want {want})")
        if num(r.get("errfatal")) not in (0, None):
            anomalies.append(f"{r['ts']}: errfatal={r['errfatal']}")

    sats = [num(r["sats"]) for r in rows if num(r.get("sats")) is not None]
    rssi = [num(r["rssi"]) for r in rows if num(r.get("rssi")) is not None]
    fixes = sum(1 for r in rows if r.get("fix") == "true")
    ups = [float(r["uptime"]) for r in rows if r.get("uptime")]
    gaps = []
    for a, b in zip(ups, ups[1:]):
        if b - a > args.interval * 1.5:
            gaps.append(round(b - a, 1))

    if not args.quiet:
        print(f"file      : {args.csv}")
        print(f"samples   : {len(rows)}   first={rows[0]['ts']}   last={rows[-1]['ts']}")
        print(f"uptime    : {ups[0]:.0f}s -> {ups[-1]:.0f}s  ({ups[-1] - ups[0]:.0f}s spanned)")
        print(f"satellites: min={min(sats) if sats else 'na'} max={max(sats) if sats else 'na'} "
              f"avg={sum(sats) / len(sats):.1f}" if sats else "satellites: na")
        print(f"fixes     : {fixes}   rssi: {min(rssi) if rssi else 'na'}..{max(rssi) if rssi else 'na'} dBm")
        print(f"gaps      : {gaps if gaps else 'none'}")
        print(f"anomalies : {len(anomalies)}")
        for a in anomalies[:20]:
            print(f"  - {a}")
        if len(anomalies) > 20:
            print(f"  ... {len(anomalies) - 20} more")
    verdict = "HEALTHY" if not anomalies else "ANOMALIES"
    print(f"{verdict} samples={len(rows)} anomalies={len(anomalies)} gaps={len(gaps)}")
    return 0 if not anomalies else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
