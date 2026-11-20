#!/bin/bash
# Self-target smoke test for "kebab-observer track" (x86_64 WSL evidence only).
#
# It spawns its OWN workloads in the caller's cgroup and traces them.  It never
# names Waydroid, an Android app, an r5 kernel or any device: this is the
# x86_64 pipeline check, not an ARM64 attach and not a target result.
#
#   scenario 1  --tgid identity-refined capture of a single-process openat loop
#   scenario 2  cgroup+uid capture without --tgid
#   scenario 3  the 20000-event cap must report truncated=true
#   negatives   uid mismatch, cgroup mismatch, usage error, empty session
set -o pipefail

BIN="$1"
[ -n "$BIN" ] || BIN=/codexprojects/oneplus8t/rust/kebab-observer/target/x86_64-unknown-linux-musl/release/kebab-observer
DIR="$2"
[ -n "$DIR" ] || DIR=/tmp/kebab-observer-track-smoke
DURATION="$3"
[ -n "$DURATION" ] || DURATION=2

fail=0
note() { echo "### $*"; }
[ "$(id -u)" = 0 ] || { echo "need root: BPF loading" >&2; exit 2; }
[ -x "$BIN" ] || { echo "missing binary: $BIN" >&2; exit 2; }
rm -rf "$DIR"
mkdir -p "$DIR"

CGPATH=$(awk -F: '/^0::/ {print $3}' /proc/self/cgroup)
INODE=$(stat -c %i "/sys/fs/cgroup$CGPATH")
echo "collector cgroup=$CGPATH inode=$INODE uid=$(id -u) binary=$BIN"

expect() {
  local file="$1"; shift
  python3 - "$file" "$@" <<'PYEOF'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    lines = [line for line in handle if line.strip()]
record = json.loads(lines[-1])
bad = 0
for item in sys.argv[2:]:
    key, want = item.split("=", 1)
    got = record.get(key)
    ok = str(got).lower() == want.lower()
    print(("  ok   " if ok else "  FAIL ") + key + "=" + str(got) + ("" if ok else " want=" + want))
    bad += 0 if ok else 1
sys.exit(1 if bad else 0)
PYEOF
  [ "$?" = 0 ] || fail=1
}

# Start the single-process openat loop; sets LOOP_PID (no command substitution:
# a subshell would keep the pipe open and hide the child).
openat_loop() {
  python3 -c 'import os, time
end = time.time() + 9
while time.time() < end:
    fd = os.open("/etc/hostname", os.O_RDONLY)
    os.read(fd, 64)
    os.close(fd)
    time.sleep(0.002)' &
  LOOP_PID=$!
  local tries=0
  while [ ! -e "/proc/$LOOP_PID/status" ] && [ "$tries" -lt 30 ]; do
    sleep 0.1
    tries=$((tries + 1))
  done
}

# ---------------------------------------------------------------- scenario 1
note "scenario 1: --tgid identity-refined capture"
openat_loop
TARGET=$LOOP_PID
TU=$(awk '/^Uid:/ {print $2}' "/proc/$TARGET/status")
"$BIN" track --uid "$TU" --cgroup-id "$INODE" --tgid "$TARGET" \
  --duration "$DURATION" --out "$DIR/s1-tgid.jsonl"
rc1=$?
kill "$TARGET" 2>/dev/null; wait "$TARGET" 2>/dev/null
echo "  rc=$rc1"
[ "$rc1" = 0 ] || fail=1
expect "$DIR/s1-tgid.jsonl" passed=true truncated=false empty_session=false sequence_gaps=0
echo "  events=$(grep -c '"record":"event"' "$DIR/s1-tgid.jsonl")"
echo "  openat=$(grep -c '"kind":"sys_enter_openat"' "$DIR/s1-tgid.jsonl")"
echo "  sys_enter=$(grep -c '"kind":"sys_enter"' "$DIR/s1-tgid.jsonl")"
echo "  sys_exit=$(grep -c '"kind":"sys_exit"' "$DIR/s1-tgid.jsonl")"
grep -m1 '"kind":"sys_enter_openat"' "$DIR/s1-tgid.jsonl" | cut -c1-300 | sed 's/^/  first openat: /'
python3 - "$DIR/s1-tgid.jsonl" <<'PYEOF'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    events = [json.loads(line) for line in handle if '"record":"event"' in line]
paths = sorted({event["path"] for event in events if event.get("path")})
good = paths == ["/etc/hostname"]
print(("  ok   " if good else "  FAIL ") + "openat paths=" + repr(paths))
sys.exit(0 if good else 1)
PYEOF
[ "$?" = 0 ] || fail=1

# ---------------------------------------------------------------- scenario 2
note "scenario 2: cgroup+uid only (no --tgid)"
openat_loop
TARGET=$LOOP_PID
"$BIN" track --uid 0 --cgroup-id "$INODE" --duration "$DURATION" --out "$DIR/s2-cgroup.jsonl"
rc2=$?
kill "$TARGET" 2>/dev/null; wait "$TARGET" 2>/dev/null
echo "  rc=$rc2"
[ "$rc2" = 0 ] || fail=1
expect "$DIR/s2-cgroup.jsonl" passed=true empty_session=false sequence_gaps=0
echo "  openat=$(grep -c '"kind":"sys_enter_openat"' "$DIR/s2-cgroup.jsonl")"

# ---------------------------------------------------------------- scenario 3
note "scenario 3: event cap (expect truncated=true, 20000 written)"
dd if=/dev/zero of=/dev/null bs=4096 count=4000000 2>/dev/null &
BURN=$!
sleep 0.3
"$BIN" track --uid 0 --cgroup-id "$INODE" --duration "$DURATION" --out "$DIR/s3-cap.jsonl"
rc3=$?
kill "$BURN" 2>/dev/null; wait "$BURN" 2>/dev/null
echo "  rc=$rc3"
[ "$rc3" = 0 ] || fail=1
expect "$DIR/s3-cap.jsonl" truncated=true events_written=20000 last_sequence=19999 sequence_gaps=0 passed=true

# --------------------------------------------------------------- negative 1
note "negative 1: --uid mismatch must be refused before attach (want rc 3)"
openat_loop
TARGET=$LOOP_PID
"$BIN" track --uid 4242 --cgroup-id "$INODE" --tgid "$TARGET" --duration 1 --out "$DIR/n1.jsonl"
rcn1=$?
kill "$TARGET" 2>/dev/null; wait "$TARGET" 2>/dev/null
echo "  rc=$rcn1"
[ "$rcn1" = 3 ] || { echo "  FAIL want rc 3"; fail=1; }
[ -e "$DIR/n1.jsonl" ] && { echo "  FAIL output file created for refused run"; fail=1; }

# --------------------------------------------------------------- negative 2
note "negative 2: --cgroup-id mismatch must be refused before attach (want rc 3)"
openat_loop
TARGET=$LOOP_PID
"$BIN" track --uid 0 --cgroup-id 999999999 --tgid "$TARGET" --duration 1 --out "$DIR/n2.jsonl"
rcn2=$?
kill "$TARGET" 2>/dev/null; wait "$TARGET" 2>/dev/null
echo "  rc=$rcn2"
[ "$rcn2" = 3 ] || { echo "  FAIL want rc 3"; fail=1; }
[ -e "$DIR/n2.jsonl" ] && { echo "  FAIL output file created for refused run"; fail=1; }

# --------------------------------------------------------------- negative 3
note "negative 3: usage rejection (--duration 0) must exit 2"
"$BIN" track --uid 0 --cgroup-id "$INODE" --duration 0 --out "$DIR/n3.jsonl" 2>&1 | sed 's/^/  /'
"$BIN" track --uid 0 --cgroup-id "$INODE" --duration 0 --out "$DIR/n3.jsonl" >/dev/null 2>&1
[ "$?" = 2 ] && echo "  ok rc=2" || { echo "  FAIL want rc 2"; fail=1; }
[ -e "$DIR/n3.jsonl" ] && { echo "  FAIL output file created for rejected usage"; fail=1; }

# --------------------------------------------------------------- negative 4
note "negative 4: wrong cgroup without --tgid is accepted but reports an empty session"
"$BIN" track --uid 0 --cgroup-id 999999999 --duration 1 --out "$DIR/n4.jsonl" >/dev/null 2>&1
echo "  rc=$?"
expect "$DIR/n4.jsonl" empty_session=true events_written=0
python3 - "$DIR/n4.jsonl" <<'PYEOF'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    record = json.loads([line for line in handle if line.strip()][-1])
gate = record.get("counts", {}).get("gate")
print("  ok   counts.gate=0" if gate == 0 else "  FAIL counts.gate=" + str(gate))
sys.exit(0 if gate == 0 else 1)
PYEOF
[ "$?" = 0 ] || fail=1

note "summary"
if [ "$fail" = 0 ]; then echo "SMOKE PASS"; else echo "SMOKE FAIL"; fi
exit "$fail"
