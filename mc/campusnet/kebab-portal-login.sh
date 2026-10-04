#!/bin/sh
# 校园网自动登录（入口）：campusnet 主力 + 本地脚本兜底。
#
# 为什么是两个一起跑：
#   * campusnet 是上游维护的现成模块（6 个 provider、261 个测试），负责主力。
#   * 但它的 eportal_portal provider 默认 **不带 wlan_ac_ip**，而本校园的 AC
#     必需要这个字段——实测留空会得到 ret_code=4「AC认证失败」，填对了才处理。
#     所以安装时给它打了一行补丁（改成读 self.opt("wlan_ac_ip")），
#     这里再用 CAMPUSNET_OPTIONS 把值传进去。
#   * 账号前缀 ",0,"（哆点 ISP 索引，0 = 校园网）是本地实测出来的，
#     campusnet 自己不加，所以这里拼在 CAMPUSNET_USERNAME 里（PK_ACCOUNT_PREFIX 可调）。
#   * 跑完 campusnet 再交给本地脚本：在线时它什么都不做（不会重复登录），
#     离线时它按本校园实测参数再补一次。
#
# 在线时本脚本只发 1 个 HTTPS 请求就退出，不会每分钟刷一堆探测。
#
# 踩过的坑（别改回去）：
#   `.  env文件` 只设 shell 变量、**不会导出到环境**，而兜底脚本是 Python、
#   读的是 os.environ。所以必须 set -a 包住 source，并且显式 export 一遍。
#   同时 service 里也保留 EnvironmentFile= 作为第二条通道。

set -u

ENV_FILE=/etc/kebab-portal-keeper.env
PKG_DIR=/usr/local/share/campusnet-pkg
FALLBACK=/usr/local/sbin/kebab-portal-keeper.py

# --- 凭据来源 1：service 的 EnvironmentFile=（系统已注入环境）---
# --- 凭据来源 2：这里自己 source（两条都留，任一可用即可）------
if [ -r "$ENV_FILE" ]; then
  set -a
  . "$ENV_FILE"
  set +a
else
  echo "[login] ✗ 读不到 $ENV_FILE（权限或文件缺失），只能靠 service 注入的环境" >&2
fi

# 归一化 + 显式导出，保证 exec 出去的 Python 兜底脚本能看到
PK_USER="${PK_USER:-}"
PK_PASS="${PK_PASS:-}"
PK_AC_IP="${PK_AC_IP:-}"
PK_PORTAL_HOST="${PK_PORTAL_HOST:-}"
PK_ACCOUNT_PREFIX="${PK_ACCOUNT_PREFIX:-,0,}"
PK_PROVIDER="${PK_PROVIDER:-eportal_portal}"
export PK_USER PK_PASS PK_AC_IP PK_PORTAL_HOST PK_ACCOUNT_PREFIX PK_PROVIDER

# 门户必须用 HTTPS + 证书校验探测：HTTP 会被网关劫持成假页面，误判成在线。
# PK_ASSUME_OFFLINE=1 是测试钩子：跳过在线探测直接走登录分支，
# 这样能不改网络就验证整条登录链路（systemctl set-environment PK_ASSUME_OFFLINE=1）。
online() {
  if [ "${PK_ASSUME_OFFLINE:-0}" = "1" ]; then
    return 1
  fi
  curl -sS -m 6 -o /dev/null -f https://www.baidu.com/ 2>/dev/null
}

if online; then
  exit 0
fi

echo "[login] 离线，开始自动登录（账号长度 ${#PK_USER}，密码长度 ${#PK_PASS}）"
if [ -z "$PK_USER" ] || [ -z "$PK_PASS" ]; then
  echo "[login] ✗ PK_USER / PK_PASS 为空——检查 $ENV_FILE 是否为 600 root:root 且已填值" >&2
fi

cn_rc="-"
if [ -d "$PKG_DIR/campusnet" ] && [ -n "$PK_USER" ]; then
  CAMPUSNET_PROVIDER="$PK_PROVIDER"
  CAMPUSNET_USERNAME="${PK_ACCOUNT_PREFIX}${PK_USER}"
  CAMPUSNET_PASSWORD="$PK_PASS"
  CAMPUSNET_OPTIONS=$(printf '{"wlan_ac_ip":"%s"}' "$PK_AC_IP")
  export CAMPUSNET_PROVIDER CAMPUSNET_USERNAME CAMPUSNET_PASSWORD CAMPUSNET_OPTIONS
  export PYTHONPATH="$PKG_DIR"
  export PYTHONIOENCODING=utf-8

  echo "[login] → campusnet（provider=$CAMPUSNET_PROVIDER，wlan_ac_ip=${PK_AC_IP:-<空>}）"
  # 一定要显式给 --portal：campusnet 默认靠网关 302 去"发现"门户，
  # 发现不了就直接放弃（实测会输出「找不到认证门户地址」并以 1 退出）。
  # 我们知道门户恒为 PK_PORTAL_HOST，固定下来就不依赖那次劫持了。
  cn_args="once --provider $CAMPUSNET_PROVIDER"
  if [ -n "${PK_PORTAL_HOST:-}" ]; then
    cn_args="$cn_args --portal $PK_PORTAL_HOST"
  fi
  # 故意不加引号：$cn_args 需要按空格拆成多个参数
  # shellcheck disable=SC2086
  cn_out=$(/usr/bin/python3 -m campusnet $cn_args 2>&1)
  cn_rc=$?
  printf '%s\n' "$cn_out" | sed 's/^/[campusnet] /'
  echo "[login] campusnet 退出码=$cn_rc"
else
  echo "[login] ! 跳过 campusnet（包目录 $PKG_DIR/campusnet 不存在？）"
fi

if online; then
  echo "[login] 已恢复连通（campusnet 退出码 $cn_rc）"
  exit 0
fi

echo "[login] campusnet（退出码 $cn_rc）未能恢复连通，交给本地兜底脚本"
if [ -x "$FALLBACK" ]; then
  exec "$FALLBACK"
fi
echo "[login] 兜底脚本不存在，放弃"
exit 0
