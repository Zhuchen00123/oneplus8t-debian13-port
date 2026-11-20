#!/bin/bash
# Self-target smoke test for kebab-observer tls-attach.
#
# Creates its OWN TLS workload (a new in-memory fixture, no socket, no device,
# no Waydroid), attaches the collector to the system libssl.so.3 by dynamic
# symbol, and asserts the recorded structure. Nothing here touches the phone.
set -u

REPO=/codexprojects/oneplus8t/rust/kebab-observer
BIN=$REPO/target/x86_64-unknown-linux-musl/release/kebab-observer
LIB=/usr/lib/x86_64-linux-gnu/libssl.so.3
WORK=/tmp/ta-smoke
fail=0

rm -rf "$WORK"
mkdir -p "$WORK"

note_ok() { printf '  ok   %s\n' "$1"; }
note_fail() { printf '  FAIL %s\n' "$1"; fail=1; }
expect() {
    local label=$1 want=$2 got=$3
    if [ "$want" = "$got" ]; then note_ok "$label=$got"; else note_fail "$label=$got want=$want"; fi
}

echo "### build fixture"
if ! cc -O2 -Wall -Wextra -Werror -o "$WORK/fixture" "$REPO/fixtures/tls_attach_fixture.c" -lssl -lcrypto; then
    echo "fixture build failed"
    exit 1
fi
note_ok "fixture built"

echo "### report mode resolves dynsym offsets and the maps association"
# The report has to run against a process that really maps the library, so it
# gets its own fixture instance with a longer delay.
"$WORK/fixture" 6000 > "$WORK/fixture0.out" 2>"$WORK/fixture0.err" &
report_pid=$!
for _ in $(seq 1 100); do
    grep -q READY "$WORK/fixture0.out" 2>/dev/null && break
    sleep 0.05
done
"$BIN" tls-attach --pid "$report_pid" --report "--target" "sys-libssl=$LIB:SSL_write:SSL_read" > "$WORK/report.json" 2>"$WORK/report.err"
report_rc=$?
expect "report rc" 0 "$report_rc"
if [ "$report_rc" -ne 0 ]; then sed 's/^/    /' "$WORK/report.err"; fi

# Guest-path rehearsal of the container case: the path as given does not exist,
# only <maps-root>/system/... does, exactly like /data/app and /system paths in a
# Waydroid container. A symlink stands in for the mount so the test needs no
# privileges.
echo "### guest path resolved through --maps-root"
mkdir -p "$WORK/guest/system/lib64"
ln -sf "$LIB" "$WORK/guest/system/lib64/libssl.so"
"$BIN" tls-attach --pid "$report_pid" --report "--maps-root" "$WORK/guest" \
    "--target" "guest-libssl=/system/lib64/libssl.so:SSL_write:SSL_read" > "$WORK/report-guest.json" 2>"$WORK/report-guest.err"
guest_report_rc=$?
expect "guest report rc" 0 "$guest_report_rc"
if [ "$guest_report_rc" -ne 0 ]; then sed 's/^/    /' "$WORK/report-guest.err"; fi
wait "$report_pid" 2>/dev/null

echo "### live attach to the system libssl.so.3"
"$WORK/fixture" 2500 > "$WORK/fixture.out" 2>"$WORK/fixture.err" &
fixture_pid=$!
for _ in $(seq 1 100); do
    grep -q READY "$WORK/fixture.out" 2>/dev/null && break
    sleep 0.05
done
grep -q READY "$WORK/fixture.out" || note_fail "fixture never printed READY"

"$BIN" tls-attach --pid "$fixture_pid" --duration 8 --out "$WORK/session.jsonl" \
    "--target" "sys-libssl=$LIB:SSL_write:SSL_read" > "$WORK/session.out" 2>"$WORK/session.err"
rc=$?
wait "$fixture_pid" 2>/dev/null
sed 's/^/  /' "$WORK/session.err"
expect "attach rc" 0 "$rc"
sed 's/^/  /' "$WORK/fixture.out"

echo "### live attach through the guest path"
"$WORK/fixture" 2000 > "$WORK/fixture2.out" 2>"$WORK/fixture2.err" &
fixture2_pid=$!
for _ in $(seq 1 100); do
    grep -q READY "$WORK/fixture2.out" 2>/dev/null && break
    sleep 0.05
done
"$BIN" tls-attach --pid "$fixture2_pid" --duration 6 --out "$WORK/session-guest.jsonl"     "--maps-root" "$WORK/guest"     "--target" "guest-libssl=/system/lib64/libssl.so:SSL_write:SSL_read" > "$WORK/session-guest.out" 2>"$WORK/session-guest.err"
guest_rc=$?
wait "$fixture2_pid" 2>/dev/null
sed 's/^/  /' "$WORK/session-guest.err"
expect "guest attach rc" 0 "$guest_rc"

python3 - "$WORK/report.json" "$WORK/session.jsonl" "$WORK/report-guest.json" "$WORK/session-guest.jsonl" <<'PYEOF'
import json
import sys

report_path, session_path, guest_report_path, guest_session_path = sys.argv[1:5]
report = json.loads(open(report_path, encoding="utf-8").read())
report_guest = json.loads(open(guest_report_path, encoding="utf-8").read())
target = report["targets"][0]
problems = []
good = []

def check(condition, text):
    (good if condition else problems).append(text)

check(target["write"]["form"] == "symbol", "report resolved SSL_write by symbol")
check(target["write"]["file_offset"] > 0, "report produced a file offset")
check(target["write"]["pt_load_verified"] is True, "SSL_write lies in an executable PT_LOAD")
check(target["read"]["symbol"] == "SSL_read", "report resolved SSL_read")
check(target["arch_compatible_with_collector"] is True, "target architecture matches the collector")
check(report["maps_checked"] is True, "report mode reads the target's maps")
check(target["maps_checked"] is True, "report target carries its own maps verdict")
check(target["mapped_segments"] > 0, "report mode associates the file with mapped segments")
check(target["association"] == "tuple", "report association is the (dev, inode) tuple")
check(target["maps_dev"] == target["dev"], "report maps device equals the stat device")

guest = report_guest["targets"][0]
check(
    guest["resolution"] in ("maps_root", "maps_root+guest_path"),
    "guest path resolved through --maps-root",
)
check(guest["association"] == "tuple", "guest path associates by (dev, inode) tuple")
check(guest["mapped_segments"] > 0, "guest path has mapped segments")
check(guest["dev"] == target["dev"], "guest path is the same file as the host path")
check(
    guest["attach_path"].startswith(report_guest["maps_root"]),
    "guest attach path is inside --maps-root",
)

records = [json.loads(line) for line in open(session_path, encoding="utf-8") if line.strip()]
session = next(r for r in records if r["record"] == "session")
closeout = next(r for r in records if r["record"] == "closeout")
calls = [r for r in records if r["record"] == "call"]

st = session["targets"][0]
check(st["write"]["maps_verified"] is True, "attach offset verified against /proc/<pid>/maps")
check(st["read"]["maps_verified"] is True, "read offset verified against /proc/<pid>/maps")
check(st["load_bias"] is not None, "load bias derived from maps")
check(any(p == "/usr/lib/x86_64-linux-gnu/libssl.so.3" for p in st["mapped_paths"]), "mapped path matches the attach file")
check(st["executable_perms"].startswith("r-x"), "mapped segment is executable")

check(closeout["passed"] is True, "closeout passed")
check(closeout["failures"] == [], "closeout has no failures")
check(closeout["calls"] >= 6, "at least six calls paired (got %d)" % closeout["calls"])
check(closeout["unpaired_calls"] == 0, "no call without a return record")
check(closeout["unpaired_exits"] == 0, "no return record without a call")
check(closeout["sequence_gaps"] == 0, "no sequence gaps")
check(closeout["ring_fully_accounted"] is True, "ring fully accounted")
check(closeout["truncated_calls"] >= 1, "truncated call observed")
check(closeout["len_invalid_calls"] >= 1, "declared <= 0 observed and never read")
check(closeout["http"]["requests"] >= 1, "at least one HTTP request captured")
check(closeout["http"]["responses"] >= 1, "at least one HTTP response captured")
check("POST" in closeout["http"]["methods"], "POST method extracted")
check("ta.example.com" in closeout["http"]["hosts"], "host extracted")
check("/api/v1/runs/{id}/points" in closeout["http"]["paths"], "path normalised and ids dropped")
check(200 in closeout["http"]["statuses"], "response status extracted")

writes = [c for c in calls if c["direction"] == "write"]
reads = [c for c in calls if c["direction"] == "read"]
check(len(writes) >= 5, "write direction captured (%d)" % len(writes))
check(len(reads) >= 1, "read direction captured (%d)" % len(reads))
check(any(c["depth"] >= 1 for c in calls), "a nested call was observed (reentrancy)")
check(all(c["ssl"].startswith("ssl-") for c in calls), "SSL object reported as an opaque id")
check(all("payload_bytes_emitted" in c and c["payload_bytes_emitted"] is False for c in calls), "no payload bytes emitted")

# Redaction: no query value, cookie value or credential may appear anywhere.
blob = open(session_path, encoding="utf-8").read()
for forbidden in ["0123456789abcdef", "sid=deadbeef", "39.9", "116.3", "ta-fixture"]:
    check(forbidden not in blob, "redaction holds for %r" % forbidden)

guest_records = [
    json.loads(line) for line in open(guest_session_path, encoding="utf-8") if line.strip()
]
guest_session = next(r for r in guest_records if r["record"] == "session")
guest_closeout = next(r for r in guest_records if r["record"] == "closeout")
guest_plan = guest_session["targets"][0]
check(guest_plan["resolution"] == "maps_root+guest_path" or guest_plan["resolution"] == "maps_root",
      "guest live attach records how the path was resolved")
check(guest_plan["association"] == "tuple", "guest live attach associated by tuple")
check(guest_plan["write"]["maps_verified"] is True, "guest write probe verified against maps")
check(guest_plan["read"]["maps_verified"] is True, "guest read probe verified against maps")
check(guest_closeout["calls"] >= 1, "guest session paired at least one call")
check(guest_closeout["passed"] is True, "guest session passed")

for item in good:
    print("  ok   " + item)
for item in problems:
    print("  FAIL " + item)
sys.exit(0 if not problems else 1)
PYEOF
[ $? = 0 ] || fail=1

echo "### negatives"
"$BIN" tls-attach --pid "$fixture_pid" --start-ticks 1 --duration 2 --out "$WORK/never1.jsonl" \
    "--target" "l=$LIB:SSL_write" > /dev/null 2>&1
expect "wrong --start-ticks rc" 3 "$?"
[ -e "$WORK/never1.jsonl" ] && note_fail "wrong --start-ticks created an output file"

"$BIN" tls-attach --pid 1 --uid 4242 --duration 2 --out "$WORK/never2.jsonl" \
    "--target" "l=$LIB:SSL_write" > /dev/null 2>&1
expect "wrong --uid rc" 3 "$?"

"$BIN" tls-attach --pid 1 --duration 2 --out "$WORK/never3.jsonl" \
    "--target" "l=/etc/hostname:0" > /dev/null 2>&1
expect "unmapped inode refused rc" 3 "$?"
[ -e "$WORK/never3.jsonl" ] && note_fail "unmapped inode created an output file"

# A path that exists nowhere, not even under the maps root: the refusal must name
# the resolution failure instead of claiming a missing mapping.
"$BIN" tls-attach --pid 1 --duration 2 --out "$WORK/never5.jsonl" \
    "--maps-root" "$WORK/guest" "--target" "l=/no/such/libssl.so:0" > "$WORK/never5.out" 2>&1
expect "unresolvable path rc" 3 "$?"
[ -e "$WORK/never5.jsonl" ] && note_fail "unresolvable path created an output file"
grep -q "not visible from this namespace" "$WORK/never5.out" || note_fail "unresolvable path error is not self-explaining"
sed 's/^/  /' "$WORK/never5.out"

"$BIN" tls-attach --pid 1 --duration 0 --out "$WORK/never4.jsonl" \
    "--target" "l=$LIB:SSL_write" > /dev/null 2>&1
expect "usage rejection rc" 2 "$?"

echo "### summary"
if [ "$fail" -eq 0 ]; then echo "SMOKE PASS"; else echo "SMOKE FAIL"; fi
exit "$fail"
