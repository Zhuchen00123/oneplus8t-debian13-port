#!/usr/bin/env bash
# ==============================================================
# kebab-cnb-keepalive.sh 的离线自测
#
# 用 PATH 上的假 curl / systemctl / journalctl 跑真的脚本，
# 不联网、不碰真 CNB、不重启任何真单元、不写系统目录。
#
#   bash mc/tests/test-cnb-keepalive.sh
# ==============================================================
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# 脚本可能在几个位置：工作树里是 ../cnb-keepalive/，拷到手机上常被拍平到同一目录。
SCRIPT="${KEBAB_CNB_SCRIPT:-}"
if [ -z "$SCRIPT" ]; then
  for c in "$HERE/../cnb-keepalive/kebab-cnb-keepalive.sh" \
           "$HERE/kebab-cnb-keepalive.sh" \
           "/usr/local/sbin/kebab-cnb-keepalive.sh"; do
    if [ -f "$c" ]; then SCRIPT="$c"; break; fi
  done
fi
if [ ! -f "$SCRIPT" ]; then
  echo "找不到 kebab-cnb-keepalive.sh；可用 KEBAB_CNB_SCRIPT=/path/to/it 指定" >&2
  exit 2
fi
echo "被测脚本：$SCRIPT"

PASS=0
FAIL=0
ok()  { printf '  PASS  %s\n' "$1"; PASS=$((PASS + 1)); }
bad() { printf '  FAIL  %s   %s\n' "$1" "${2:-}"; FAIL=$((FAIL + 1)); }
eq()  { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "期望 [$3] 实际 [$2]"; fi; }
has() { if printf '%s' "$2" | grep -q -- "$3"; then ok "$1"; else bad "$1" "输出里没有 [$3]"; fi; }
hasnt() { if printf '%s' "$2" | grep -q -- "$3"; then bad "$1" "输出里不该有 [$3]"; else ok "$1"; fi; }

TMP="$(mktemp -d /tmp/cnbtest.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

# ---------------------------------------------------------------- 假工具
write_shims() {
  local d="$1"
  mkdir -p "$d/bin"

  cat >"$d/bin/curl" <<'SHIM'
#!/bin/sh
MOCK_DIR="${MOCK_DIR:?}"
echo "curl $*" >>"$MOCK_DIR/curl.calls"

url=""
for a in "$@"; do
  case "$a" in http://*|https://*) url="$a" ;; esac
done
is_code=0
for a in "$@"; do [ "$a" = "%{http_code}" ] && is_code=1; done

case "$url" in
  *endpoint.txt)
    if [ -f "$MOCK_DIR/endpoint.txt" ]; then cat "$MOCK_DIR/endpoint.txt"; else exit 7; fi
    ;;
  *workspace/list*)
    [ -f "$MOCK_DIR/workspace_list.json" ] && cat "$MOCK_DIR/workspace_list.json"
    ;;
  *workspace/start*)
    echo '{"sn":"mock-sn","message":"started"}'
    ;;
  *)
    hostport=$(printf '%s' "$url" | sed -E 's#^https?://##; s#/.*$##')
    # 用 tail -1 而不是 head -1：测试里同一个地址可能先写默认值再追加，
    # 后面的必须能覆盖前面的，否则测的就不是想测的分支。
    code=$(sed -n "s#^${hostport}=\([0-9]*\)\$#\1#p" "$MOCK_DIR/relay.codes" 2>/dev/null | tail -1)
    [ -n "$code" ] || code=302
    [ "$is_code" = 1 ] && echo "$code"
    ;;
esac
SHIM

  cat >"$d/bin/systemctl" <<'SHIM'
#!/bin/sh
MOCK_DIR="${MOCK_DIR:?}"
echo "systemctl $*" >>"$MOCK_DIR/systemctl.calls"
case "$1" in
  is-active)
    [ "$(cat "$MOCK_DIR/client.active" 2>/dev/null)" = "1" ] && exit 0 || exit 3
    ;;
  restart)
    echo "$2" >>"$MOCK_DIR/restarts"
    exit 0
    ;;
  *) exit 0 ;;
esac
SHIM

  cat >"$d/bin/journalctl" <<'SHIM'
#!/bin/sh
MOCK_DIR="${MOCK_DIR:?}"
echo "journalctl $*" >>"$MOCK_DIR/journalctl.calls"
[ -f "$MOCK_DIR/journal.txt" ] && cat "$MOCK_DIR/journal.txt"
exit 0
SHIM

  chmod +x "$d/bin/curl" "$d/bin/systemctl" "$d/bin/journalctl"
}

# ---------------------------------------------------------------- 场景搭建
# new_case <名字>  之后可以用 $CASE 目录，并调用 run_script
new_case() {
  CASE="$TMP/$1"
  rm -rf "$CASE"
  mkdir -p "$CASE"
  write_shims "$CASE"
  : >"$CASE/curl.calls"
  : >"$CASE/systemctl.calls"
  : >"$CASE/restarts"
  : >"$CASE/journal.txt"
  printf 'old0000-9000.cnb.run:443=302\n' >"$CASE/relay.codes"
  echo 1 >"$CASE/client.active"
  cat >"$CASE/client.env" <<EOF
MCB_HOST=old0000-9000.cnb.run
MCB_PORT=443
MCB_SECRET=wst_test_only
MCB_SESSION=kebab
MCB_MC=127.0.0.1:25565
EOF
  cat >"$CASE/cnb.env" <<EOF
REPO=testowner/testrepo
BRANCH=main
CLIENT_UNIT=kebab-mcbridge-client.service
CLIENT_ENV=$CASE/client.env
STATE_DIR=$CASE/state
MAX_ACTIONS_PER_HOUR=6
START_SETTLE_SECONDS=0
PROBE_TIMEOUT=3
EOF
}

run_script() {
  MOCK_DIR="$CASE" \
    KEBAB_CNB_CONF="$CASE/cnb.env" \
    PATH="$CASE/bin:$PATH" \
    bash "$SCRIPT" "$@" >"$CASE/out.txt" 2>&1
  echo $?
}

calls() { cat "$CASE/curl.calls" 2>/dev/null; }

# ---------------------------------------------------------------- 用例
echo "假工具目录：$TMP"
echo

# --- 1. 地址变化 → 改写配置并重启 ---
echo "[1] 远端给了新地址，与本地不同 → 应改写 MCB_HOST/MCB_PORT 并重启客户端"
new_case t1
printf 'endpoint=new1234-9000.cnb.run:443/fwd_tok\n' >"$CASE/endpoint.txt"
printf 'new1234-9000.cnb.run:443=426\n' >>"$CASE/relay.codes"
rc=$(run_script)
out=$(cat "$CASE/out.txt")
eq "退出码 0" "$rc" "0"
eq "MCB_HOST 已更新" "$(sed -n 's/^MCB_HOST=//p' "$CASE/client.env")" "new1234-9000.cnb.run"
eq "MCB_PORT 已更新" "$(sed -n 's/^MCB_PORT=//p' "$CASE/client.env")" "443"
eq "密钥没被动" "$(sed -n 's/^MCB_SECRET=//p' "$CASE/client.env")" "wst_test_only"
eq "重启了客户端" "$(cat "$CASE/restarts")" "kebab-mcbridge-client.service"
has "日志说明地址变化" "$out" "地址变化"
hasnt "没有调 workspace/start" "$(calls)" "workspace/start"

# --- 2. 无变化 ---
echo
echo "[2] 远端地址与本地一致、中继活着、客户端在跑 → 什么都不做"
new_case t2
printf 'endpoint=old0000-9000.cnb.run:443/fwd_tok\n' >"$CASE/endpoint.txt"
printf 'old0000-9000.cnb.run:443=426\n' >>"$CASE/relay.codes"
rc=$(run_script)
out=$(cat "$CASE/out.txt")
eq "退出码 0" "$rc" "0"
eq "没有重启" "$(cat "$CASE/restarts")" ""
has "报告无变化" "$out" "无变化"
hasnt "没有调 start" "$(calls)" "workspace/start"

# --- 3. 客户端卡在重连循环 ---
echo
echo "[3] 中继活着，但客户端最后一条成功连接之后仍有 >=5 条隧道错误 → 重启客户端"
new_case t3
printf 'endpoint=old0000-9000.cnb.run:443/fwd_tok\n' >"$CASE/endpoint.txt"
printf 'old0000-9000.cnb.run:443=426\n' >>"$CASE/relay.codes"
for i in 1 2 3 4 5 6; do echo "隧道错误: WsError: WS 升级失败: HTTP/1.1 404"; done >"$CASE/journal.txt"
rc=$(run_script)
out=$(cat "$CASE/out.txt")
eq "退出码 0" "$rc" "0"
eq "重启了客户端" "$(cat "$CASE/restarts")" "kebab-mcbridge-client.service"
has "说明了原因" "$out" "隧道错误"

# --- 3b. 历史错误已被成功连接覆盖 → 不该重启 ---
echo
echo "[3b] 错误都在最后一次成功连接之前 → 属于历史，不该重启（否则每轮都白重启）"
new_case t3b
printf 'endpoint=old0000-9000.cnb.run:443/fwd_tok\n' >"$CASE/endpoint.txt"
printf 'old0000-9000.cnb.run:443=426\n' >>"$CASE/relay.codes"
{
  for i in 1 2 3 4 5 6 7 8; do echo "隧道错误: WsError: WS 升级失败: HTTP/1.1 404"; echo "5 秒后重连…"; done
  echo "[23:49:19] 已连上中继 sd2xm49rnt-9000.cnb.run:443（session=kebab），转发到 127.0.0.1:25565"
} >"$CASE/journal.txt"
rc=$(run_script)
out=$(cat "$CASE/out.txt")
eq "退出码 0" "$rc" "0"
eq "没有重启" "$(cat "$CASE/restarts")" ""
has "报告稳态" "$out" "无变化"

# --- 4. 环境不在跑 → 调 workspace/start ---
echo
echo "[4] 远端无 endpoint、地址已死、环境不在运行、有令牌 → 调 workspace/start"
new_case t4
echo 'CNB_TOKEN=fake-token-for-test' >>"$CASE/cnb.env"
echo '[]' >"$CASE/workspace_list.json"
rc=$(run_script)
out=$(cat "$CASE/out.txt")
eq "退出码 0" "$rc" "0"
has "调了 workspace/start" "$(calls)" "workspace/start"
has "说明在拉起环境" "$out" "不在运行"
hasnt "没有打印真令牌" "$out" "fake-token-for-test"

# --- 5. 环境不在跑但没有令牌 ---
echo
echo "[5] 同上但没有 CNB_TOKEN → 不调 start，安静退出 0"
new_case t5
rc=$(run_script)
out=$(cat "$CASE/out.txt")
eq "退出码 0" "$rc" "0"
hasnt "没有调 start" "$(calls)" "workspace/start"
has "提示缺令牌" "$out" "CNB_TOKEN 未配置"

# --- 6. 防抖 ---
echo
echo "[6] 本小时动作已达上限 → 拒绝动作"
new_case t6
printf 'endpoint=new9999-9000.cnb.run:443/fwd_tok\n' >"$CASE/endpoint.txt"
printf 'new9999-9000.cnb.run:443=426\n' >>"$CASE/relay.codes"
mkdir -p "$CASE/state"
echo "$(( $(date +%s) / 3600 ))" >"$CASE/state/action-hour"
echo 6 >"$CASE/state/action-count"
rc=$(run_script)
out=$(cat "$CASE/out.txt")
eq "退出码 0" "$rc" "0"
eq "配置没被改" "$(sed -n 's/^MCB_HOST=//p' "$CASE/client.env")" "old0000-9000.cnb.run"
eq "没有重启" "$(cat "$CASE/restarts")" ""
has "提示停手" "$out" "停手"

# --- 7. dry-run 只说不做 ---
echo
echo "[7] --dry-run：地址变化场景下也不改配置、不重启、不调 start"
new_case t7
echo 'CNB_TOKEN=fake-token-for-test' >>"$CASE/cnb.env"
printf 'endpoint=new7777-9000.cnb.run:443/fwd_tok\n' >"$CASE/endpoint.txt"
printf 'new7777-9000.cnb.run:443=426\n' >>"$CASE/relay.codes"
rc=$(run_script --dry-run)
out=$(cat "$CASE/out.txt")
eq "退出码 0" "$rc" "0"
eq "配置没被改" "$(sed -n 's/^MCB_HOST=//p' "$CASE/client.env")" "old0000-9000.cnb.run"
eq "没有重启" "$(cat "$CASE/restarts")" ""
hasnt "没有调 start" "$(calls)" "workspace/start"

# --- 8. --status ---
echo
echo "[8] --status 只报状态"
new_case t8
printf 'endpoint=old0000-9000.cnb.run:443/fwd_tok\n' >"$CASE/endpoint.txt"
printf 'old0000-9000.cnb.run:443=426\n' >>"$CASE/relay.codes"
rc=$(run_script --status)
out=$(cat "$CASE/out.txt")
eq "退出码 0" "$rc" "0"
has "报了仓库" "$out" "testowner/testrepo"
has "报了客户端地址" "$out" "old0000-9000.cnb.run"
eq "没有重启" "$(cat "$CASE/restarts")" ""

# --- 9. 未配置 REPO ---
echo
echo "[9] 未配置 REPO → 提示并退出 0"
new_case t9
printf 'CLIENT_ENV=%s\nSTATE_DIR=%s\n' "$CASE/client.env" "$CASE/state" >"$CASE/cnb.env"
rc=$(run_script)
out=$(cat "$CASE/out.txt")
eq "退出码 0" "$rc" "0"
has "提示未配置 REPO" "$out" "未配置 REPO"

# --- 10. 活性判据：500 不算活 ---
echo
echo "[10] 活性判据必须是 426/101：返回 500 要当成死"
new_case t10
echo 'CNB_TOKEN=fake-token-for-test' >>"$CASE/cnb.env"
echo '[]' >"$CASE/workspace_list.json"
printf 'endpoint=old0000-9000.cnb.run:443/fwd_tok\n' >"$CASE/endpoint.txt"
printf 'old0000-9000.cnb.run:443=500\n' >>"$CASE/relay.codes"
rc=$(run_script)
out=$(cat "$CASE/out.txt")
eq "退出码 0" "$rc" "0"
has "把 500 当死" "$out" "HTTP 500"
has "因此去拉起环境" "$(calls)" "workspace/start"

# --- 11. 稳态重复不打日志 ---
echo
echo "[11] 稳态重复轮次不应重复刷日志（2 分钟一轮，一天 720 行会把 journal 淹掉）"
new_case t11
printf 'endpoint=old0000-9000.cnb.run:443/fwd_tok\n' >"$CASE/endpoint.txt"
printf 'old0000-9000.cnb.run:443=426\n' >>"$CASE/relay.codes"
rc1=$(run_script); out1=$(cat "$CASE/out.txt")
rc2=$(run_script); out2=$(cat "$CASE/out.txt")
rc3=$(run_script); out3=$(cat "$CASE/out.txt")
eq "第一轮退出码 0" "$rc1" "0"
has "第一轮报告稳态" "$out1" "无变化"
hasnt "第二轮不再重复打" "$out2" "无变化"
hasnt "第三轮也不打" "$out3" "无变化"
eq "第二轮输出为空" "$(printf '%s' "$out2" | tr -d '[:space:]')" ""

# --- 12. 状态变化后应该重新打一次 ---
echo
echo "[12] 从故障恢复回稳态时，应重新打一次（形成时间线）"
new_case t12
printf 'endpoint=old0000-9000.cnb.run:443/fwd_tok\n' >"$CASE/endpoint.txt"
printf 'old0000-9000.cnb.run:443=500\n' >>"$CASE/relay.codes"
run_script >/dev/null   # 这一轮是"地址死了"，会去调 start
printf 'old0000-9000.cnb.run:443=426\n' >>"$CASE/relay.codes"
last="${CASE}/state/last-state"
rc=$(run_script); out=$(cat "$CASE/out.txt")
eq "退出码 0" "$rc" "0"
has "恢复后重新打了稳态" "$out" "无变化"

echo
echo "================ 结果 ================"
if [ "$FAIL" -gt 0 ]; then
  echo "失败 $FAIL 项，通过 $PASS 项"
  exit 1
fi
echo "全部通过 ✅（$PASS 项）"
exit 0
