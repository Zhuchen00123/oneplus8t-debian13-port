#!/usr/bin/env bash
# ==============================================================
# 手机侧 CNB 中继保活 + 地址同步
#
# 由 kebab-cnb-keepalive.timer 每 2 分钟调一次。纯 bash + curl，零依赖。
#
# 它解决的三个问题：
#   1. CNB 开发环境被平台回收（10 分钟空闲 / 4-6 点强制 / 18 小时硬上限）
#      → 调 OpenAPI 重新拉起。
#   2. 环境重建后公网地址变了（<envid>-9000.cnb.run 里的 envid 会变）
#      → 以**远端仓库的 endpoint.txt 为唯一权威**读新地址，
#        改写客户端配置并重启客户端。
#   3. 客户端进程死了或卡在重连循环 → 重启。
#
# 它解决不了的（必须知情）：
#   * 18 小时是 CNB 平台的硬上限，保活只能"自动重建"，做不到不断线。
#     每次重建期间隧道会断几分钟，玩家会掉线重连。
#   * 环境启动后需要有人进过 VS Code 页面才不会被 10 分钟空闲回收；
#     这一点靠 CNB 侧 .cnb.yml 的 keepAliveTimeout + api_trigger 兜底，
#     不在本脚本能力范围内。
#
# 凭据放 /etc/kebab-cnb-keepalive.env（600），不进 git、不打印。
#
# 退出码永远是 0：它是 timer 的 oneshot，非零会把单元标成 failed，
# 污染"无 failed unit"这条基线。真实状态看 journal。
#
# 用法：
#   kebab-cnb-keepalive.sh              正常跑一轮
#   kebab-cnb-keepalive.sh --dry-run    只打印将要做什么，不改配置、不重启、不调 start
#   kebab-cnb-keepalive.sh --status     只报状态
# ==============================================================
set -uo pipefail

CONF="${KEBAB_CNB_CONF:-/etc/kebab-cnb-keepalive.env}"
# shellcheck disable=SC1090
[ -f "$CONF" ] && . "$CONF"

REPO="${REPO:-}"
BRANCH="${BRANCH:-main}"
API="${API:-https://api.cnb.cool}"
ENDPOINT_URL="${ENDPOINT_URL:-}"
CLIENT_UNIT="${CLIENT_UNIT:-kebab-mcbridge-client.service}"
CLIENT_ENV="${CLIENT_ENV:-/etc/kebab-mcbridge-client.env}"
STATE_DIR="${STATE_DIR:-/run/kebab-cnb-keepalive}"
MAX_ACTIONS_PER_HOUR="${MAX_ACTIONS_PER_HOUR:-6}"
START_SETTLE_SECONDS="${START_SETTLE_SECONDS:-20}"
PROBE_TIMEOUT="${PROBE_TIMEOUT:-12}"

DRY=0
STATUS_ONLY=0
VERBOSE=0
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --status) STATUS_ONLY=1 ;;
    --verbose|-v) VERBOSE=1 ;;
  esac
done

mkdir -p "$STATE_DIR" 2>/dev/null || true
COUNT_FILE="$STATE_DIR/action-count"
HOUR_FILE="$STATE_DIR/action-hour"

log() { echo "[$(date '+%F %T')] $*"; }

# 每轮都会重复的常规诊断行只在 --verbose 下打。
# 这类行（"远端 endpoint.txt：…"、"探测 … → HTTP 426"）本身没错，
# 但 2 分钟一轮重复打就是噪声，会把真正的变化淹掉。
vlog() { [ "$VERBOSE" = "1" ] && log "$*"; return 0; }

STATE_LAST_FILE="$STATE_DIR/last-state"

# 只在"稳态描述"发生变化时打日志。
# 这是 2 分钟一轮的 timer：每轮都打一行的话，journal 一天会被刷进 700 多行，
# 真正出事的那几天反而翻不出来。所以稳态重复的不打，变了才打。
log_state() {
  local msg="$1" prev=""
  [ -f "$STATE_LAST_FILE" ] && prev="$(cat "$STATE_LAST_FILE" 2>/dev/null || true)"
  if [ "$msg" != "$prev" ]; then
    log "$msg"
    printf '%s\n' "$msg" >"$STATE_LAST_FILE" 2>/dev/null || true
  fi
}

# 真的动手了就清掉稳态记录，好让下一个稳态重新打一次（形成"变化-恢复"的时间线）
clear_state() { rm -f "$STATE_LAST_FILE" 2>/dev/null || true; }

# ---------------------------------------------------------------- 防抖
# 一小时内动作次数超阈值就停手并告警。
# 说明根因不是"偶尔挂了"，而是"压根保不住"，空转只会把地址刷来刷去、把环境反复重启。
allow_action() {
  local now hour prev cnt=0
  now=$(date +%s); hour=$((now / 3600))
  if [ -f "$HOUR_FILE" ] && [ -f "$COUNT_FILE" ]; then
    prev=$(cat "$HOUR_FILE" 2>/dev/null || echo 0)
    [ "$prev" = "$hour" ] && cnt=$(cat "$COUNT_FILE" 2>/dev/null || echo 0)
  fi
  if [ "$cnt" -ge "$MAX_ACTIONS_PER_HOUR" ]; then
    log "⚠️ 本小时已动作 $cnt 次（阈值 $MAX_ACTIONS_PER_HOUR），停手。请人工介入。"
    return 1
  fi
  echo "$hour" >"$HOUR_FILE"
  echo $((cnt + 1)) >"$COUNT_FILE"
  clear_state
  return 0
}

# ---------------------------------------------------------------- 工具
client_get() {
  [ -f "$CLIENT_ENV" ] || return 1
  sed -n "s/^$1=\(.*\)$/\1/p" "$CLIENT_ENV" | head -1
}

# 幂等改写客户端配置里的某一项
client_set() {
  local key="$1" val="$2" out
  if [ "$DRY" = "1" ]; then
    log "  [DRY] 会把 $key 设为 $val"
    return 0
  fi
  # ⚠️ 绝对不能用 `sed -i`。它是"在同目录新建临时文件、再 rename 覆盖"，
  #    而 service 的 ProtectSystem=full 把 /etc 挂成只读；
  #    即使 ReadWritePaths 单独放开了这个文件，rename 依然要写目录 → 必然失败。
  #    改成"读进内存、原地写回"，只要求文件本身可写，不要求目录可写。
  if [ -f "$CLIENT_ENV" ] && grep -q "^$key=" "$CLIENT_ENV"; then
    out=$(sed "s|^$key=.*|$key=$val|" "$CLIENT_ENV")
  else
    out=$( { [ -f "$CLIENT_ENV" ] && cat "$CLIENT_ENV"; printf '%s=%s\n' "$key" "$val"; } )
  fi
  printf '%s\n' "$out" >"$CLIENT_ENV"
  chmod 600 "$CLIENT_ENV" 2>/dev/null || true
}

# 探测中继是否活着。
# ⚠️ 判据只能是 426（要求 Upgrade）或 101（握手成功）。
#    cnb.run 代理对**任何**端口都返回 500/302——包括根本没监听的端口，
#    所以 500/302 都不是活性信号，只有 426/101 才是。
probe_relay() {
  local host="$1" port="${2:-443}" code
  code=$(curl -sS -m "$PROBE_TIMEOUT" -o /dev/null -w '%{http_code}' \
           "https://${host}:${port}/" 2>/dev/null) || code=000
  echo "$code"
  case "$code" in
    426|101) return 0 ;;
    *) return 1 ;;
  esac
}

is_alive_code() { [ "$1" = "426" ] || [ "$1" = "101" ]; }

# 拉远端 endpoint.txt，输出 "host port"（解析失败则无输出）
# 格式：endpoint=主机:端口/令牌
fetch_endpoint() {
  local raw hostport host port
  raw=$(curl -sS -m "$PROBE_TIMEOUT" "$ENDPOINT_URL" 2>/dev/null) || return 1
  hostport=$(printf '%s' "$raw" | sed -n 's/^endpoint=\(.*\)$/\1/p' | head -1)
  [ -n "$hostport" ] || return 1
  hostport="${hostport%%/*}"          # 去掉 /令牌
  host="${hostport%%:*}"
  port="${hostport##*:}"
  [ "$port" = "$host" ] && port=443   # 没写端口
  [ -n "$host" ] || return 1
  printf '%s %s\n' "$host" "$port"
}

# 中继活着时，保证客户端在跑、且没卡在重连循环
check_client_health() {
  local n
  if systemctl is-active --quiet "$CLIENT_UNIT" 2>/dev/null; then :; else
    log "中继活着，但客户端未运行 → 启动它"
    if allow_action; then
      if [ "$DRY" = "1" ]; then log "  [DRY] 跳过启动"; else
        systemctl restart "$CLIENT_UNIT" && log "✅ 已启动 $CLIENT_UNIT" || log "❌ 启动失败"
      fi
    fi
    return 0
  fi
  # 只看"最后一次成功连上中继"**之后**的日志。
  # 之前的隧道错误是历史——已经被后来的成功连接覆盖了。
  # 直接数最近 N 行会把历史错误算进来，导致每轮都误判、无脑重启客户端，
  # 结果隧道每 2 分钟断一次，比不做保活还糟。
  local recent tail_part n
  recent=$(journalctl -u "$CLIENT_UNIT" -n 80 --no-pager -o cat 2>/dev/null || true)
  tail_part=$(printf '%s\n' "$recent" | awk '/已连上中继/{buf=""} {buf=buf $0 "\n"} END{printf "%s", buf}')
  n=$(printf '%s' "$tail_part" | grep -c '隧道错误' || true)
  if [ "${n:-0}" -ge 5 ]; then
    log "中继活着，但客户端最后一条成功连接之后仍有 $n 条隧道错误 → 重启客户端"
    if allow_action; then
      if [ "$DRY" = "1" ]; then log "  [DRY] 跳过重启"; else
        systemctl restart "$CLIENT_UNIT" && log "✅ 已重启客户端" || log "❌ 重启失败"
      fi
    fi
  else
    log_state "无变化：中继活着、客户端运行中"
  fi
}

# 没有可用地址时，把 CNB 环境拉起来
start_env() {
  local run resp
  if [ -z "${CNB_TOKEN:-}" ]; then
    log_state "✗ 没有可用地址，但 CNB_TOKEN 未配置，无法自动拉起环境"
    return 0
  fi
  run=$(curl -sS -m 25 -H "Authorization: Bearer $CNB_TOKEN" \
          "$API/workspace/list?status=running" 2>/dev/null || true)
  if [ -z "$run" ]; then
    log_state "workspace/list 无响应（网络或令牌问题），本轮跳过"
    return 0
  fi
  if printf '%s' "$run" | grep -q "$REPO"; then
    log_state "环境显示在运行，但地址探测是死的 → 可能仍在启动中，等下一轮"
    return 0
  fi
  log "中继环境不在运行，调 $REPO/-/workspace/start（branch=$BRANCH）"
  allow_action || return 0
  if [ "$DRY" = "1" ]; then
    log "  [DRY] 会 POST $API/$REPO/-/workspace/start  {\"branch\":\"$BRANCH\"}"
    return 0
  fi
  resp=$(curl -sS -m 30 -X POST \
           -H "Authorization: Bearer $CNB_TOKEN" \
           -H 'Content-Type: application/json' \
           -d "{\"branch\":\"$BRANCH\"}" \
           "$API/$REPO/-/workspace/start" 2>/dev/null || true)
  log "workspace/start → ${resp:-<无响应>}"
  log "环境启动中，等 ${START_SETTLE_SECONDS}s 后由下一轮接管"
  sleep "$START_SETTLE_SECONDS"
}

# ---------------------------------------------------------------- 前置检查
if [ -z "$REPO" ]; then
  log "✗ 未配置 REPO（见 kebab-cnb-keepalive.env.example），跳过"
  exit 0
fi
if [ -z "$ENDPOINT_URL" ]; then
  ENDPOINT_URL="https://cnb.cool/${REPO}/-/git/raw/${BRANCH}/endpoint.txt"
fi

CUR_HOST="$(client_get MCB_HOST || true)"
CUR_PORT="$(client_get MCB_PORT || true)"
[ -n "$CUR_PORT" ] || CUR_PORT=443

# ---------------------------------------------------------------- --status
if [ "$STATUS_ONLY" = "1" ]; then
  echo "REPO            : $REPO"
  echo "endpoint.txt    : $ENDPOINT_URL"
  if systemctl is-active --quiet "$CLIENT_UNIT" 2>/dev/null; then
    echo "客户端单元      : $CLIENT_UNIT（active）"
  else
    echo "客户端单元      : $CLIENT_UNIT（未运行）"
  fi
  echo "客户端配置地址  : ${CUR_HOST:-<未配置>}:${CUR_PORT}"
  if EP="$(fetch_endpoint)"; then
    echo "远端 endpoint   : $EP"
  else
    echo "远端 endpoint   : <解析不到——环境没跑，或启动脚本还没写回>"
  fi
  if [ -n "$CUR_HOST" ]; then
    c="$(probe_relay "$CUR_HOST" "$CUR_PORT")"
    if is_alive_code "$c"; then
      echo "当前地址活性    : HTTP $c（活）"
    else
      echo "当前地址活性    : HTTP $c（死）"
    fi
  else
    echo "当前地址活性    : <无地址>"
  fi
  echo "CNB_TOKEN       : $([ -n "${CNB_TOKEN:-}" ] && echo 已配置 || echo 未配置)"
  exit 0
fi

# ---------------------------------------------------------------- 主流程
# ⚠️ 顺序很重要：**先同步地址，再判活性**。
#    如果先判活性，就会出现"远端给了新地址、新地址又是活的"这种情况——
#    脚本以为一切正常，直接退出，客户端却还拿着旧地址一直重连。
EP="$(fetch_endpoint || true)"
EP_HOST=""; EP_PORT=""
if [ -n "$EP" ]; then
  EP_HOST="${EP%% *}"; EP_PORT="${EP##* }"
fi

if [ -n "$EP_HOST" ]; then
  vlog "远端 endpoint.txt：$EP_HOST:$EP_PORT"

  if [ "$EP_HOST:$EP_PORT" != "$CUR_HOST:$CUR_PORT" ]; then
    log "地址变化：${CUR_HOST:-<无>}:${CUR_PORT} → $EP_HOST:$EP_PORT"
    if allow_action; then
      client_set MCB_HOST "$EP_HOST"
      client_set MCB_PORT "$EP_PORT"
      log "→ 重启 $CLIENT_UNIT 以启用新地址"
      if [ "$DRY" = "1" ]; then
        log "  [DRY] 跳过重启"
      elif systemctl restart "$CLIENT_UNIT"; then
        log "✅ 已按新地址重启"
      else
        log "❌ 重启失败"
      fi
    fi
    exit 0
  fi

  # 地址一致：这时才看它活不活
  CODE="$(probe_relay "$EP_HOST" "$EP_PORT")"
  vlog "探测 $EP_HOST:$EP_PORT → HTTP $CODE"
  if is_alive_code "$CODE"; then
    check_client_health
  else
    log "远端给的地址不回 426/101（HTTP $CODE）→ 环境可能已被回收"
    start_env
  fi
  exit 0
fi

# 远端还没写回地址（环境没跑，或刚拉起还没写好）
if [ -n "$CUR_HOST" ]; then
  CODE="$(probe_relay "$CUR_HOST" "$CUR_PORT")"
  vlog "远端暂无地址，探测本地配置的 $CUR_HOST:$CUR_PORT → HTTP $CODE"
  if is_alive_code "$CODE"; then
    check_client_health
    exit 0
  fi
  vlog "本地配置的地址也是死的（HTTP $CODE）"
else
  log "远端与本地都没有可用地址"
fi

start_env
exit 0
