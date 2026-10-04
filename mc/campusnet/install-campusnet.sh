#!/bin/bash
# 把 campusnet 装到手机，并接上已有的保活 timer。可重复执行（幂等）。
#
#   sudo bash install-campusnet.sh [源码目录]      # 默认 /tmp/campusnet-pkg
#
# 源码目录下要有 campusnet/ 这个包（从 GitHub tarball 解出来的那个）。
#
# 做四件事：
#   1. 把 campusnet 包放到 /usr/local/share/campusnet-pkg/campusnet
#   2. 打一行补丁：让 eportal_portal 的 wlan_ac_ip 可以配置
#      （上游写死空串；本校园的 AC 必需该字段，留空 → ret_code=4「AC认证失败」）
#   3. 装入口脚本 /usr/local/sbin/kebab-portal-login.sh（campusnet 主力 + 本地兜底）
#   4. 把保活 service 的 ExecStart 指到入口脚本，timer 保持原样
#
# 只动这几个文件，不碰网关/网络配置、不碰 SSH/USB、不碰充电电源温控。

set -euo pipefail

SRC="${1:-/tmp/campusnet-pkg}"
PKG_DIR=/usr/local/share/campusnet-pkg
WRAP=/usr/local/sbin/kebab-portal-login.sh
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ "$(id -u)" != "0" ]; then
    echo "需要 root：sudo bash $0 $*" >&2
    exit 1
fi

if [ ! -d "$SRC/campusnet" ]; then
    echo "找不到 $SRC/campusnet（先把 tarball 解压到那里）" >&2
    exit 1
fi

# ---------------------------------------------------------------- 1. 包
install -d -m 755 "$PKG_DIR"
rm -rf "$PKG_DIR/campusnet"
cp -r "$SRC/campusnet" "$PKG_DIR/campusnet"
find "$PKG_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
# cp -r 会继承调用者的 umask——如果安装时 umask 是 077，目录会变成 0700，
# 于是普通用户连 --help 都跑不了（systemd 单元以 root 跑，所以不报错但很难排障）。
# 显式放开，保证任何用户都能读。
chmod -R a+rX "$PKG_DIR"
echo "  [1/4] 包已装到 $PKG_DIR/campusnet（$(stat -c '%a' "$PKG_DIR/campusnet")）"

# ---------------------------------------------------------------- 2. 补丁
TARGET="$PKG_DIR/campusnet/providers/eportal_portal.py"
python3 - "$TARGET" <<'PY'
import io
import sys

path = sys.argv[1]
old = '                "wlan_ac_ip": "",\n'
new = '                "wlan_ac_ip": str(self.opt("wlan_ac_ip", "") or ""),\n'

src = io.open(path, encoding="utf-8").read()
if new in src:
    print("  [2/4] 补丁已存在，跳过")
elif old in src:
    io.open(path, "w", encoding="utf-8").write(src.replace(old, new, 1))
    print("  [2/4] 已打补丁：wlan_ac_ip 改为读 self.opt()")
else:
    print("  [2/4] ✗ 找不到锚点，未打补丁（上游可能改过这段）", file=sys.stderr)
    sys.exit(1)
PY
python3 -m py_compile "$TARGET"

# ---------------------------------------------------------------- 3. 入口脚本
install -m 755 "$HERE/kebab-portal-login.sh" "$WRAP"
echo "  [3/4] 入口脚本已装到 $WRAP"

# ---------------------------------------------------------------- 4. service
cat > /etc/systemd/system/kebab-portal-keeper.service <<'UNIT'
[Unit]
Description=kebab 校园网自动登录保活（campusnet 主力 + 本地兜底，单次执行）
Documentation=https://github.com/demo133/campusnet
# 有网才需要做事；但本校园是「没登录就上不了外网」，所以不能等 network-online
After=network.target
Wants=network.target

[Service]
Type=oneshot
# 凭据走两条通道：systemd 在这里注入一次，入口脚本自己再 source 一次。
# 开头的 "-" 表示文件缺失时忽略而不是让单元失败。
EnvironmentFile=-/etc/kebab-portal-keeper.env
ExecStart=/usr/local/sbin/kebab-portal-login.sh
# 单次最多跑 120 秒，避免卡住拖垮 timer 节奏
TimeoutStartSec=120
# 不做任何写操作，收紧权限
NoNewPrivileges=yes
ProtectSystem=full
ProtectHome=read-only
PrivateTmp=yes
ReadWritePaths=/run
UNIT

systemctl daemon-reload
systemctl enable kebab-portal-keeper.timer >/dev/null 2>&1 || true
systemctl restart kebab-portal-keeper.timer
echo "  [4/4] service 已指向入口脚本，timer 已重启"

# ---------------------------------------------------------------- 自检
echo
echo "--- 自检 ---"
echo -n "  unit 语法: "
systemd-analyze verify /etc/systemd/system/kebab-portal-keeper.service >/dev/null 2>&1 && echo OK || echo "（有告警，见 systemd-analyze verify）"
echo -n "  campusnet 可导入: "
PYTHONPATH="$PKG_DIR" python3 -c 'import campusnet; print("OK", campusnet.__version__)' 2>&1 | tail -1
echo -n "  campusnet provider 列表: "
PYTHONPATH="$PKG_DIR" python3 -m campusnet providers 2>&1 | grep -c '•' | tr -d '\n'
echo " 个"
echo -n "  补丁生效: "
grep -q 'self.opt("wlan_ac_ip"' "$TARGET" && echo OK || echo "✗ 未生效"
echo -n "  timer: "
echo "$(systemctl is-enabled kebab-portal-keeper.timer 2>&1) / $(systemctl is-active kebab-portal-keeper.timer 2>&1)"
echo
echo "完成。日志： journalctl -u kebab-portal-keeper.service -n 50 --no-pager"
