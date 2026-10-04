#!/usr/bin/env bash
# ==============================================================
# 手机侧部署脚本：校园网门户自动登录保活
#
# 在手机上以 root 执行。不写任何分区、不加载模块、不碰电源/充电/温控/SSH/USB，
# 也不改 Wi-Fi / 网络配置——只装一个 python 脚本 + 一个 systemd timer。
#
# 幂等：可重复执行。
#   已存在的 /etc/kebab-portal-keeper.env 默认**不覆盖**（避免清掉已填的账号密码），
#   需要强制覆盖时加 --force-env。
#
# 装完必须做的一步（脚本不会替你做，因为涉及密码）：
#   sudo install -m 600 /dev/null /etc/kebab-portal-keeper.env
#   sudo tee /etc/kebab-portal-keeper.env >/dev/null <<'EOF'
#   PK_USER=你的学号
#   PK_PASS=你的密码
#   EOF
#   或者从主机 scp 一个已经写好、权限 600 的文件过来，避免密码进 shell 历史。
#
# 回滚：
#   systemctl disable --now kebab-portal-keeper.timer
#   rm -f /etc/systemd/system/kebab-portal-keeper.{service,timer} \
#         /usr/local/sbin/kebab-portal-keeper.py
#   systemctl daemon-reload
# ==============================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
FORCE_ENV=0
[ "${1:-}" = "--force-env" ] && FORCE_ENV=1

[ "$(id -u)" = "0" ] || { echo "需要 root"; exit 1; }

BIN_DST=/usr/local/sbin/kebab-portal-keeper.py
ENV_DST=/etc/kebab-portal-keeper.env
SVC_DST=/etc/systemd/system/kebab-portal-keeper.service
TMR_DST=/etc/systemd/system/kebab-portal-keeper.timer

echo "==== 1/5 检查依赖 ===="
command -v python3 >/dev/null || { echo "缺少 python3"; exit 1; }
echo "  python3: $(python3 -V 2>&1)  ($(command -v python3))"
python3 -c 'import http.client,ssl,socket,json,urllib.parse;print("  标准库 http.client/ssl/socket/json/urllib OK")'

echo "==== 2/5 安装脚本 ===="
install -m 0755 "$HERE/kebab-portal-keeper.py" "$BIN_DST"
echo "  → $BIN_DST"
python3 -m py_compile "$BIN_DST" && echo "  py_compile OK"

echo "==== 3/5 安装配置 ===="
if [ -f "$ENV_DST" ] && [ "$FORCE_ENV" = "0" ]; then
  echo "  已存在 $ENV_DST，保留不动（需要覆盖加 --force-env）"
else
  install -m 0600 "$HERE/kebab-portal-keeper.env.example" "$ENV_DST"
  echo "  → $ENV_DST (600，账号密码待填)"
fi
chmod 600 "$ENV_DST"
grep -q '^PK_USER=.\+' "$ENV_DST" && echo "  PK_USER 已配置" || echo "  ⚠ PK_USER 还是空的，保活不会生效"

echo "==== 4/5 安装 systemd 单元 ===="
install -m 0644 "$HERE/kebab-portal-keeper.service" "$SVC_DST"
install -m 0644 "$HERE/kebab-portal-keeper.timer" "$TMR_DST"
systemctl daemon-reload
echo "  → $SVC_DST"
echo "  → $TMR_DST"

echo "==== 5/5 启用定时器并实测 ===="
systemctl enable --now kebab-portal-keeper.timer
sleep 1
echo "  timer: $(systemctl is-enabled kebab-portal-keeper.timer) / $(systemctl is-active kebab-portal-keeper.timer)"
echo "  下次触发: $(systemctl show kebab-portal-keeper.timer -p NextElapseUSecRealtime --value 2>/dev/null)"
echo
echo "--- 当前在线探测（只读，不登录）---"
sudo -u root env -i PATH=/usr/bin:/bin "$BIN_DST" --check 2>&1 | tail -3 || true
echo
echo "--- 最近日志 ---"
journalctl -u kebab-portal-keeper.service -n 10 --no-pager 2>/dev/null || true
echo
echo "--- 失败单元检查 ---"
systemctl --failed --no-pager 2>/dev/null | head -5 || true
