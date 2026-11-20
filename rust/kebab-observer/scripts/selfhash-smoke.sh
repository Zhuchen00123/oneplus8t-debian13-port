#!/bin/bash
# Self-hash uprobe smoke test.
#
# Proves, on the machine that runs it, whether an attached uprobe is visible
# from inside the target: the fixture hashes its own .text around SSL_write and
# reports DETECTED once those bytes change.
#
#   1. no attach                       -> CLEAN
#   2. dynamic build, PATH:SYMBOL:SYM  -> DETECTED at the probe site
#   3. static build, PATH@0:W:R        -> DETECTED (static has no .dynsym)
#   4. static build, never calls it    -> still DETECTED: the patch lands when
#                                         the probe is attached, not when the
#                                         function is called
#
# Everything is local: no device, no Waydroid, no container.

set -u

HERE=$(cd "$(dirname "$0")" && pwd)
CRATE=$(cd "$HERE/.." && pwd)
WORK=$(printenv WORK || true)
[ -n "$WORK" ] || WORK=/tmp/selfhash-smoke
BIN=$(printenv BIN || true)
[ -n "$BIN" ] || BIN=$CRATE/target/x86_64-unknown-linux-musl/release/kebab-observer
CC=$(printenv CC || true)
[ -n "$CC" ] || CC=cc
SRC=$CRATE/fixtures/selfhash.c

rm -rf "$WORK"
mkdir -p "$WORK"

fail=0
ok() { printf '  ok   %s\n' "$1"; }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail + 1)); }
expect() {
    if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (want '$2' got '$3')"; fi
}
field() {
    # first numeric field of a JSON line
    grep -o "\"$2\":-\{0,1\}[0-9]*" "$1" | head -1 | cut -d: -f2
}

echo "### build the fixture"
if "$CC" -O2 -static -no-pie -Wl,--export-dynamic -o "$WORK/selfhash-static" "$SRC" >"$WORK/build-static.log" 2>&1; then
    ok "static fixture compiled"
else
    bad "static fixture failed"
    sed 's/^/    /' "$WORK/build-static.log"
    echo "SMOKE FAIL"
    exit 1
fi
if "$CC" -O2 -no-pie -Wl,--export-dynamic -o "$WORK/selfhash-dyn" "$SRC" >"$WORK/build-dyn.log" 2>&1; then
    ok "dynamic fixture compiled"
else
    bad "dynamic fixture failed"
    sed 's/^/    /' "$WORK/build-dyn.log"
fi
if [ -f "$BIN" ]; then ok "collector present"; else bad "collector missing: $BIN"; fi

echo "### 0. symbol offsets printed by the fixture"
"$WORK/selfhash-static" --timeout-ms 10 --interval-ms 1 >"$WORK/self-test.out" 2>&1
expect "fixture exits 0 on a bare run" 0 "$?"
write_off=$(field "$WORK/self-test.out" ssl_write_file_offset)
read_off=$(field "$WORK/self-test.out" ssl_read_file_offset)
if [ -n "$write_off" ] && [ -n "$read_off" ] && [ "$write_off" -gt 0 ] && [ "$read_off" -gt 0 ]; then
    ok "fixture reports its own symbol offsets ($write_off / $read_off)"
else
    bad "no symbol offsets in the before record"
fi
if grep -q '"record":"before"' "$WORK/self-test.out"; then
    ok "before record present"
else
    bad "no before record"
fi

echo "### 1. no attach: expect CLEAN"
"$WORK/selfhash-static" --timeout-ms 1200 --interval-ms 100 >"$WORK/clean.out" 2>&1
expect "clean rc" 0 "$?"
if grep -q '"record":"clean"' "$WORK/clean.out"; then
    ok "clean record present"
else
    bad "clean record missing"
    sed 's/^/    /' "$WORK/clean.out"
fi
if grep -q '"record":"detected"' "$WORK/clean.out"; then
    bad "detected without any probe"
else
    ok "no detection without a probe"
fi

run_attached() {
    label=$1
    binary=$2
    target=$3
    extra=$4
    out=$5
    rm -f "$WORK/pid"
    # shellcheck disable=SC2086
    "$binary" --timeout-ms 9000 --interval-ms 100 --pidfile "$WORK/pid" $extra >"$out" 2>&1 &
    fixture_pid=$!
    for _ in $(seq 1 100); do
        [ -s "$WORK/pid" ] && break
        sleep 0.05
    done
    target_pid=$(cat "$WORK/pid" 2>/dev/null || echo "$fixture_pid")
    "$BIN" tls-attach --pid "$target_pid" --duration 3 \
        --out "$WORK/session-$label.jsonl" \
        --target "selfhash=$target" >"$WORK/attach-$label.out" 2>&1
    rc=$?
    wait "$fixture_pid" 2>/dev/null
    expect "attach rc ($label)" 0 "$rc"
    if [ "$rc" -ne 0 ]; then sed 's/^/    /' "$WORK/attach-$label.out"; fi
}

echo "### 2. dynamic build, symbol form: expect DETECTED"
# This is the case t11 hit: a glibc-linked host executable maps its RELRO page
# at a page offset that is not congruent with the segment's virtual address, so
# the old "every segment must agree" rule refused a perfectly good target.  The
# bias now comes from the segment holding the probe offset and the other
# segments are only a warning.
"$WORK/selfhash-dyn" --timeout-ms 9000 --interval-ms 100 --pidfile "$WORK/dyn.pid" >"$WORK/detected-dyn.out" 2>&1 &
dyn_pid=$!
for _ in $(seq 1 100); do
    [ -s "$WORK/dyn.pid" ] && break
    sleep 0.05
done
"$BIN" tls-attach --pid "$(cat "$WORK/dyn.pid" 2>/dev/null || echo "$dyn_pid")" --duration 3 \
    --out "$WORK/session-dyn.jsonl" \
    --target "selfhash=$WORK/selfhash-dyn:SSL_write:SSL_read" >"$WORK/attach-dyn.out" 2>&1
dyn_rc=$?
wait "$dyn_pid" 2>/dev/null
expect "attach rc (dyn, symbol form)" 0 "$dyn_rc"
if grep -q '"record":"detected"' "$WORK/detected-dyn.out"; then
    ok "detected through the symbol form as well"
else
    bad "no detection through the symbol form"
    sed 's/^/    /' "$WORK/detected-dyn.out"
fi

echo "### 2b. the disagreeing segment is a warning, not a refusal"
"$WORK/selfhash-dyn" --timeout-ms 3000 --interval-ms 100 --pidfile "$WORK/rep.pid" >"$WORK/rep.out" 2>&1 &
rep_pid=$!
for _ in $(seq 1 100); do
    [ -s "$WORK/rep.pid" ] && break
    sleep 0.05
done
"$BIN" tls-attach --pid "$(cat "$WORK/rep.pid" 2>/dev/null || echo "$rep_pid")" --report \
    --target "selfhash=$WORK/selfhash-dyn:SSL_write:SSL_read" >"$WORK/report.json" 2>&1
report_rc=$?
wait "$rep_pid" 2>/dev/null
expect "report rc" 0 "$report_rc"
python3 - "$WORK/report.json" <<'PY'
import json, sys
report = json.load(open(sys.argv[1]))
target = report["targets"][0] if isinstance(report, dict) else report[0]
ok = True
def check(condition, label):
    global ok
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        ok = False
check(target.get("load_bias_source") == "segment", "bias came from the anchor segment")
check(target.get("load_bias") == 0, "bias is the anchor's, not a rejected mix")
check(target.get("load_bias_cross_checked", 0) >= 1, "other segments were cross-checked")
warnings = target.get("load_bias_warnings") or []
check(len(warnings) >= 1, "the disagreeing segment is recorded as a warning")
check(all("implies" in warning for warning in warnings), "warning names both biases")
write = (target.get("write") or {})
check(write.get("maps_verified") is True, "probe verified against the maps")
check(isinstance(write.get("runtime_va"), int), "the probe has a runtime address")
sys.exit(0 if ok else 1)
PY
if [ $? -ne 0 ]; then fail=$((fail + 1)); fi

echo "### 3. static build, offset form: expect DETECTED"
run_attached static "$WORK/selfhash-static" "$WORK/selfhash-static@0:$write_off:$read_off" "" "$WORK/detected-static.out"
if grep -q '"record":"detected"' "$WORK/detected-static.out"; then
    ok "detected through the offset form as well"
else
    bad "no detection with the offset form"
    sed 's/^/    /' "$WORK/detected-static.out"
fi

echo "### 4. static build, never calls the function: still DETECTED"
run_attached nocall "$WORK/selfhash-static" "$WORK/selfhash-static@0:$write_off:$read_off" "--no-call" "$WORK/detected-nocall.out"
if grep -q '"record":"detected"' "$WORK/detected-nocall.out"; then
    ok "detected even though the fixture never called it"
else
    bad "no detection with --no-call"
fi

echo "### 5. the session records the maps proof"
SESSION=$WORK/session-dyn.jsonl
[ -f "$SESSION" ] || SESSION=$WORK/session-static.jsonl
python3 - "$SESSION" <<'PY'
import json, sys
path = sys.argv[1]
session, closeout = None, None
with open(path) as handle:
    for line in handle:
        record = json.loads(line)
        if record.get("record") == "session":
            session = record
        elif record.get("record") == "closeout":
            closeout = record
ok = True
def check(condition, label):
    global ok
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        ok = False
check(closeout is not None and closeout.get("attached_probes", 0) >= 2,
      "probe set attached (entry+return per symbol)")

targets = (session or {}).get("targets") or []
first = targets[0] if targets else {}
probe = first.get("write") or {}
check(probe.get("maps_verified") is True, "probe verified against the target's maps")
check(first.get("mapped_segments", 0) > 0, "the fixture maps its own executable")
check(first.get("association") == "tuple", "association is the (dev, inode) tuple")
check(first.get("load_bias_source") == "segment", "load bias came from the anchor segment")
check(isinstance(first.get("load_bias_cross_checked"), int),
      "cross-check count is recorded")
check(closeout is not None and closeout.get("passed") is True, "session closeout passed")
sys.exit(0 if ok else 1)
PY
if [ $? -ne 0 ]; then fail=$((fail + 1)); fi

echo "### 6. --load-bias override"
bias=$(python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); t=r["targets"][0] if isinstance(r,dict) else r[0]; print(t["load_bias"])' "$WORK/report.json")
if [ -n "$bias" ]; then
    "$WORK/selfhash-dyn" --timeout-ms 6000 --interval-ms 100 --pidfile "$WORK/ov.pid" >"$WORK/override.out" 2>&1 &
    ov_pid=$!
    for _ in $(seq 1 100); do
        [ -s "$WORK/ov.pid" ] && break
        sleep 0.05
    done
    "$BIN" tls-attach --pid "$(cat "$WORK/ov.pid" 2>/dev/null || echo "$ov_pid")" --duration 2 \
        --load-bias "$bias" --out "$WORK/session-override.jsonl" \
        --target "selfhash=$WORK/selfhash-dyn:SSL_write:SSL_read" >"$WORK/attach-override.out" 2>&1
    expect "attach rc with a correct --load-bias" 0 "$?"
    wait "$ov_pid" 2>/dev/null
    bad_bias=$((bias + 1048576))
    "$WORK/selfhash-dyn" --timeout-ms 1500 --interval-ms 100 >"$WORK/override-bad.out" 2>&1 &
    bad_pid=$!
    "$BIN" tls-attach --pid "$bad_pid" --duration 2 --load-bias "$bad_bias" \
        --out "$WORK/session-override-bad.jsonl" \
        --target "selfhash=$WORK/selfhash-dyn:SSL_write:SSL_read" >"$WORK/attach-override-bad.out" 2>&1
    bad_rc=$?
    wait "$bad_pid" 2>/dev/null
    if [ "$bad_rc" -eq 3 ] && grep -q 'not inside an executable mapping' "$WORK/attach-override-bad.out"; then
        ok "a wrong --load-bias is still refused at the landing point"
    else
        bad "expected a landing-point refusal (rc=$bad_rc)"
        sed 's/^/    /' "$WORK/attach-override-bad.out"
    fi
else
    bad "no load_bias in the report, override not exercised"
fi

echo "### summary"
if [ "$fail" -eq 0 ]; then
    echo "SMOKE PASS"
    exit 0
fi
echo "SMOKE FAIL ($fail)"
exit 1
