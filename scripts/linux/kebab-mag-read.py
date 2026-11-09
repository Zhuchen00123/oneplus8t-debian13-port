#!/usr/bin/env python3
"""Read the OnePlus 8T (kebab) magnetometer and orientation from the SSC.

Modes:
  * one-shot (default) -- enable the requested instances, collect for N seconds,
                          print one JSON line;
  * --daemon           -- keep the streams open and publish /run/kebab-mag.json.

Sensor instances only exist when kebab-ssc-mag-gate.service has declared them in
SMEM130 (MMC5603 magnetometer, plus BMI260 accelerometer in the 2026-11-10 build);
without that gate discovery finds nothing and this tool exits with a clear error.

Two derived orientations are published:
  * heading_flat_deg -- from the magnetometer alone, valid only when the phone
                        lies flat and screen up (needs no other sensor);
  * heading_deg      -- from the DSP rotation vector (geomag_rv, magnetometer
                        aided), valid in any attitude.  Preferred when present.

Wire format reuses kebab-ssc-inspect.py: discovery 512, attribute read 1,
continuous config 513, measurement 1025, disable 10.
"""
import argparse
import importlib.util
import json
import math
import os
import signal
import socket
import struct
import sys
import tempfile
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location("ssc", Path(__file__).with_name("kebab-ssc-inspect.py"))
ssc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ssc)

ALLOWED_RATES = (5.0, 10.0, 15.0, 25.0, 50.0, 100.0)
DEFAULT_RATE = 10.0
MAG_NAME = "mmc5603x"
PUBLISH_PATH = Path(os.environ.get("KEBAB_MAG_PUBLISH", "/run/kebab-mag.json"))
RECONNECT_DELAY_S = 5.0
BATTERY_CAPACITY_PATH = Path(os.environ.get("KEBAB_BATTERY", "/sys/class/power_supply/bq27541-0/capacity"))
DEFAULT_MIN_BATTERY = 20.0
BATTERY_CHECK_S = 60.0


def read_capacity(path=BATTERY_CAPACITY_PATH):
    """Return the battery percentage, or None when it cannot be read."""
    try:
        return float(Path(path).read_text().strip())
    except (OSError, ValueError):
        return None


def battery_is_low(path=BATTERY_CAPACITY_PATH, limit=DEFAULT_MIN_BATTERY):
    """True only when the level is known and strictly below the limit."""
    if not limit or limit <= 0:
        return False
    level = read_capacity(path)
    return level is not None and level < limit

# datatype -> number of published floats (all instances are fixed width)
WIDTHS = {
    "mag": 3,
    "accel": 3,
    "gyro": 3,
    "gravity": 3,
    "rotv": 4,
    "geomag_rv": 4,
}
DEFAULT_TARGETS = ("mag", "geomag_rv")


def decode_sample(body):
    """Return (values, status) from an sns_std_sensor_event body."""
    values = []
    for number, wire, value in ssc.fields(body):
        if (number, wire) == (1, 2):
            if not value or len(value) % 4:
                raise ValueError("invalid packed floats")
            values.extend(struct.unpack("<" + "f" * (len(value) // 4), value))
        elif (number, wire) == (1, 5):
            values.append(struct.unpack("<f", value)[0])
    status = ssc.one(body, 2, 0)
    if not values or not all(math.isfinite(v) for v in values):
        raise ValueError("expected finite values, got %r" % (values,))
    if status not in (0, 1, 2, 3):
        raise ValueError("missing or invalid status")
    return values, status


def to_device_frame(cx, cy, cz):
    """Chip frame -> device frame.

    From the E28 posture trial (accelerometer, same package orientation):
    chip +Z = device +Z, chip +X = device +Y, chip +Y = device -X.
    """
    return -cy, cx, cz


def field_magnitude(x, y, z):
    return math.sqrt(x * x + y * y + z * z)


def heading_degrees(dx, dy):
    """Magnetic heading for a device lying flat, screen up (0 = north, clockwise)."""
    return (math.degrees(math.atan2(-dx, dy)) + 360.0) % 360.0


def rotation_matrix(quaternion):
    """Device->world rotation matrix from (x, y, z, w) as published by the SSC."""
    if len(quaternion) != 4:
        raise ValueError("rotation vector needs four values, got %r" % (quaternion,))
    x, y, z, w = quaternion
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm == 0.0:
        raise ValueError("zero quaternion")
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    return (
        (1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)),
        (2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)),
        (2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)),
    )


def heading_from_rotation(quaternion):
    """Azimuth of the phone's top edge, in degrees (0 = north, clockwise).

    The SSC rotation vector maps the *chip* frame to the world frame: rotating a
    measured chip-frame magnetic vector by it lands on +Y (north) with the
    vertical part on -Z, which is what the 2026-11-10 check measured.  The DSP
    does not apply the physical mounting rotation (bmi26x_0_platform.orient is
    the identity), so the phone's top edge is chip +X (E28: chip +X = device +Y)
    and its world direction is R[:,0] = (east, north, up).
    """
    matrix = rotation_matrix(quaternion)
    east, north = matrix[0][0], matrix[1][0]
    return (math.degrees(math.atan2(east, north)) + 360.0) % 360.0


def summarise(latest, rate, counts=None):
    """Build the published record from the latest value per datatype.

    latest maps datatype -> tuple of floats.  counts maps datatype -> samples seen.
    """
    if not latest:
        raise ValueError("no samples")
    counts = counts or {}
    record = {
        "timestamp": time.time(),
        "rate_hz": rate,
        "samples": sum(counts.values()) if counts else 1,
        "per_sensor": {k: counts.get(k, 0) for k in latest},
    }

    mag = latest.get("mag")
    if mag is not None and len(mag) == 3:
        x, y, z = mag
        dx, dy, dz = to_device_frame(x, y, z)
        record["name"] = MAG_NAME
        record["datatype"] = "mag"
        record["chip"] = {"x": round(x, 3), "y": round(y, 3), "z": round(z, 3)}
        record["device"] = {"x": round(dx, 3), "y": round(dy, 3), "z": round(dz, 3)}
        record["magnitude_ut"] = round(field_magnitude(x, y, z), 3)
        record["horizontal_ut"] = round(math.hypot(dx, dy), 3)
        record["heading_flat_deg"] = round(heading_degrees(dx, dy), 1)

    for key in ("geomag_rv", "rotv"):
        quaternion = latest.get(key)
        if quaternion is not None and len(quaternion) == 4:
            record["orientation_source"] = key
            record["quaternion"] = {
                "x": round(quaternion[0], 6),
                "y": round(quaternion[1], 6),
                "z": round(quaternion[2], 6),
                "w": round(quaternion[3], 6),
            }
            record["heading_deg"] = round(heading_from_rotation(quaternion), 1)
            break

    if "heading_deg" not in record and "heading_flat_deg" in record:
        record["heading_deg"] = record["heading_flat_deg"]

    extra = {k: list(v) for k, v in latest.items() if k not in ("mag", "geomag_rv", "rotv")}
    if extra:
        record["values"] = extra
    return record


def publish(record, path=PUBLISH_PATH):
    """Atomically replace the published JSON so readers never see half a file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".kebab-mag-")
        with os.fdopen(fd, "w") as handle:
            json.dump(record, handle, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, str(path))
    except OSError as exc:
        print("publish failed: %s" % exc, file=sys.stderr, flush=True)


class Reader:
    """Opens one or more SSC sensor streams and decodes them until closed."""

    def __init__(self, targets=DEFAULT_TARGETS, rate=DEFAULT_RATE, trace=None):
        if rate not in ALLOWED_RATES:
            raise ValueError("rate %r is not in %r" % (rate, ALLOWED_RATES))
        if not targets:
            raise ValueError("no targets requested")
        for target in targets:
            if target not in WIDTHS:
                raise ValueError("unsupported datatype %r" % (target,))
        self.targets = tuple(targets)
        self.rate = rate
        self.trace = trace
        self.client = None
        self.uids = {}

    def open(self):
        self.client = ssc.Client(self.trace)
        for target in self.targets:
            uids = self.client.discover(target)
            if len(uids) > 1:
                raise RuntimeError("expected at most one %r instance, found %r" % (target, uids))
            if not uids:
                if target == "mag":
                    raise RuntimeError("no magnetometer instance; is kebab-ssc-mag-gate active?")
                continue
            uid = uids[0]
            self.uids[target] = uid
            attrs = self.client.query(uid, 1, b"\x10\x00", 128)
            name = None
            rates = []
            for number, wire, value in ssc.fields(attrs):
                if (number, wire) != (1, 2):
                    continue
                key = ssc.one(value, 1, 0)
                values = ssc.attribute_values(ssc.one(value, 2, 2, b""))
                if key == 0 and values:
                    name = values[0]
                elif key == 6:
                    rates = [float(v) for v in values if isinstance(v, (int, float))]
            if target == "mag" and name != MAG_NAME:
                raise RuntimeError("mag instance reports name %r, expected %r" % (name, MAG_NAME))
            if rates and self.rate not in rates:
                raise RuntimeError("%r does not offer %r Hz: %r" % (target, self.rate, rates))
        if not self.uids:
            raise RuntimeError("none of %r are present" % (self.targets,))
        return self

    def _send(self, mid, uid, payload=b""):
        if mid not in (513, 10):
            raise ValueError("operation not allowed: %r" % (mid,))
        self.client.txn += 1
        frame = ssc.request_frame(self.client.txn, uid, mid, payload)
        self.client.log("send", frame)
        self.client.sock.send(frame)

    def enable(self):
        for uid in self.uids.values():
            self._send(513, uid, b"\x0d" + struct.pack("<f", self.rate))

    def disable(self, wait_ack=False):
        """Always attempt the disable, even while unwinding from an error."""
        for uid in self.uids.values():
            try:
                self._send(10, uid)
            except Exception:
                pass
        if wait_ack and self.uids:
            try:
                self._await_ack(2.0)
            except Exception:
                pass

    def _await_ack(self, timeout):
        sock = self.client.sock
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sock.settimeout(max(0.01, deadline - time.monotonic()))
            try:
                frame = sock.recv(65536)
            except socket.timeout:
                return False
            self.client.log("recv", frame)
            try:
                for uid, mid, _ticks, _body in ssc.events_from_frame(frame):
                    if mid == 10:
                        return True
            except Exception:
                continue
        return False

    def _datatype_for(self, uid):
        for target, known in self.uids.items():
            if known == uid:
                return target
        return None

    def samples(self, seconds=None, count=None, idle_timeout=6.0):
        """Yield (datatype, values, status) until the deadline/count is reached."""
        sock = self.client.sock
        deadline = None if seconds is None else time.monotonic() + seconds
        seen = 0
        last = time.monotonic()
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                return
            if count is not None and seen >= count:
                return
            sock.settimeout(idle_timeout)
            try:
                frame = sock.recv(65536)
            except socket.timeout:
                if time.monotonic() - last > idle_timeout:
                    raise TimeoutError("no sensor frames for %.1f s" % idle_timeout)
                continue
            self.client.log("recv", frame)
            last = time.monotonic()
            try:
                events = ssc.events_from_frame(frame)
            except Exception:
                continue
            for uid, mid, _ticks, body in events:
                datatype = self._datatype_for(uid)
                if datatype is None:
                    continue
                if mid == 128:
                    raise RuntimeError("SSC error event for %r: %s" % (datatype, body.hex()))
                if mid != 1025:
                    continue
                try:
                    values, status = decode_sample(body)
                except ValueError:
                    continue
                if len(values) != WIDTHS[datatype]:
                    continue
                seen += 1
                yield datatype, tuple(values), status

    def close(self):
        if self.client is not None:
            self.client.close()
            self.client = None


def _collect(reader, seconds, count=None):
    latest = {}
    counts = {}
    statuses = {}
    for datatype, values, status in reader.samples(seconds=seconds, count=count):
        latest[datatype] = values
        counts[datatype] = counts.get(datatype, 0) + 1
        statuses[datatype] = status
    return latest, counts, statuses


def run_once(seconds, rate, trace=None, targets=DEFAULT_TARGETS):
    reader = Reader(targets=targets, rate=rate, trace=trace).open()
    try:
        reader.enable()
        latest, counts, _ = _collect(reader, seconds)
    finally:
        reader.disable(wait_ack=True)
        reader.close()
    return summarise(latest, rate, counts)


def run_daemon(seconds, rate, trace=None, targets=DEFAULT_TARGETS, min_battery=DEFAULT_MIN_BATTERY):
    stopping = {"flag": False}

    def _stop(_signum, _frame):
        stopping["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    deadline = None if not seconds else time.monotonic() + seconds
    published = 0
    next_battery_check = time.monotonic() + BATTERY_CHECK_S
    while not stopping["flag"]:
        if min_battery and time.monotonic() >= next_battery_check:
            next_battery_check = time.monotonic() + BATTERY_CHECK_S
            if battery_is_low(limit=min_battery):
                level = read_capacity()
                print("battery %.0f%% is below %.0f%%; stopping the reader"
                      % (level if level is not None else -1, min_battery),
                      file=sys.stderr, flush=True)
                publish({"stopped": "battery-low", "capacity": level, "timestamp": time.time()})
                return {"published": published, "stopped": "battery-low"}
        reader = None
        try:
            reader = Reader(targets=targets, rate=rate, trace=trace).open()
            reader.enable()
            latest = {}
            counts = {}
            for datatype, values, _status in reader.samples(seconds=None):
                latest[datatype] = values
                counts[datatype] = counts.get(datatype, 0) + 1
                publish(summarise(latest, rate, counts))
                published += 1
                if stopping["flag"]:
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    stopping["flag"] = True
        except Exception as exc:
            print("sensor reader: %s; reconnecting in %.0f s" % (exc, RECONNECT_DELAY_S),
                  file=sys.stderr, flush=True)
            publish({"error": str(exc), "timestamp": time.time()})
            time.sleep(RECONNECT_DELAY_S)
        finally:
            if reader is not None:
                reader.disable()
                reader.close()
    return {"published": published}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read the kebab SSC magnetometer and orientation")
    parser.add_argument("--daemon", action="store_true", help="keep streaming and publish JSON")
    parser.add_argument("--seconds", type=float, default=None, help="one-shot window (default 1 s); --daemon runs forever unless set")
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE, help="sampling rate in Hz")
    parser.add_argument("--datatype", action="append", default=None,
                        help="sensor datatype to stream (repeatable); default mag + geomag_rv")
    parser.add_argument("--min-battery", type=float, default=DEFAULT_MIN_BATTERY,
                        help="stop the daemon below this percentage (0 disables)")
    parser.add_argument("--trace", help="write the raw wire log here (O_EXCL)")
    args = parser.parse_args(argv)
    if args.rate not in ALLOWED_RATES:
        parser.error("--rate must be one of %r" % (ALLOWED_RATES,))
    targets = tuple(args.datatype) if args.datatype else DEFAULT_TARGETS
    try:
        if args.daemon:
            limit = args.seconds if args.seconds and args.seconds > 0 else None
            run_daemon(limit, args.rate, args.trace, targets, args.min_battery)
        else:
            window = 1.0 if args.seconds is None else args.seconds
            print(json.dumps(run_once(window, args.rate, args.trace, targets), sort_keys=True))
    except Exception as exc:
        print("mag read failed: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
