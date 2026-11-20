set -u
B=/srv/kebab-re-20261120/bin
cd "$B"
rm -f /tmp/sh12.pid /tmp/sh12.out /tmp/rep12.json /tmp/sh12.jsonl
"$B/selfhash" --timeout-ms 25000 --interval-ms 100 --pidfile /tmp/sh12.pid > /tmp/sh12.out 2>&1 &
fixture=$!
for i in $(seq 1 100); do [ -s /tmp/sh12.pid ] && break; sleep 0.05; done
pid=$(cat /tmp/sh12.pid)
echo "fixture_pid=$pid"
echo "--- report ---"
"$B/kebab-observer" tls-attach --pid "$pid" --report --target "selfhash=$B/selfhash@0:6516:6576" > /tmp/rep12.json 2>&1
echo "report_rc=$?"
python3 - /tmp/rep12.json <<'PY'
import json, sys
report = json.load(open(sys.argv[1]))
target = report["targets"][0] if isinstance(report, dict) else report[0]
write = target.get("write") or {}
print("load_bias=%r source=%r cross_checked=%r" % (target.get("load_bias"), target.get("load_bias_source"), target.get("load_bias_cross_checked")))
print("warnings=%d" % len(target.get("load_bias_warnings") or []))
for warning in (target.get("load_bias_warnings") or [])[:4]:
    print("  warn:", warning)
print("maps_verified=%r runtime_va=%r" % (write.get("maps_verified"), write.get("runtime_va")))
PY
echo "--- attach ---"
"$B/kebab-observer" tls-attach --pid "$pid" --duration 3 --out /tmp/sh12.jsonl --target "selfhash=$B/selfhash@0:6516:6576" > /tmp/attach12.out 2>&1
echo "attach_rc=$?"
wait "$fixture" 2>/dev/null
echo "--- fixture output ---"
cat /tmp/sh12.out
echo "--- session closeout ---"
python3 - <<'PY'
import json
path = "/tmp/sh12.jsonl"
session = closeout = None
for line in open(path):
    record = json.loads(line)
    if record.get("record") == "session":
        session = record
    elif record.get("record") == "closeout":
        closeout = record
target = (session or {}).get("targets", [{}])[0]
write = (target.get("write") or {})
print("target: bias=%r source=%r cross=%r warnings=%d maps_verified=%r" % (
    target.get("load_bias"), target.get("load_bias_source"), target.get("load_bias_cross_checked"),
    len(target.get("load_bias_warnings") or []), write.get("maps_verified")))
print("closeout: passed=%r attached_probes=%r failures=%r" % (
    (closeout or {}).get("passed"), (closeout or {}).get("attached_probes"), (closeout or {}).get("failures")))
PY