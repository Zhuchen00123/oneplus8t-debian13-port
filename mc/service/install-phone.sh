#!/usr/bin/env bash
# ==============================================================
# 手机侧部署脚本：CNB 反向隧道客户端
#
# 在手机上以 root 执行（本脚本不写任何分区、不加载模块、不碰电源/充电/温控/SSH/USB）。
#
# 幂等：可重复执行。
#   已存在的 /etc/kebab-mcbridge-client.env 默认**不覆盖**（避免清掉已改的密钥），
#   需要强制覆盖时加 --force-env。
#
# 回滚：
#   systemctl disable --now kebab-mcbridge-client.service
#   rm -f /etc/systemd/system/kebab-mcbridge-client.service \
#         /usr/local/sbin/kebab-mcbridge-client.py
#   systemctl daemon-reload
# ==============================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
FORCE_ENV=0
[ "${1:-}" = "--force-env" ] && FORCE_ENV=1

[ "$(id -u)" = "0" ] || { echo "需要 root"; exit 1; }

BIN_DST=/usr/local/sbin/kebab-mcbridge-client.py
ENV_DST=/etc/kebab-mcbridge-client.env
UNIT_DST=/etc/systemd/system/kebab-mcbridge-client.service
SHARE=/usr/local/share/kebab-mc

echo "==== 1/5 检查依赖 ===="
command -v python3 >/dev/null || { echo "缺少 python3"; exit 1; }
echo "  python3: $(python3 -V 2>&1)  路径 $(command -v python3)"
python3 -c 'import ssl,socket,struct,threading,base64;print("  标准库 ssl/socket/struct/threading/base64 OK   OpenSSL", ssl.OPENSSL_VERSION)'

echo "==== 2/5 安装客户端 ===="
install -m 0755 "$HERE/../client/mcbridge_client.py" "$BIN_DST"
echo "  → $BIN_DST"

echo "==== 3/5 安装配置 ===="
mkdir -p "$SHARE"
if [ -f "$ENV_DST" ] && [ "$FORCE_ENV" = "0" ]; then
  echo "  已存在 $ENV_DST，保留不动（需要覆盖加 --force-env）"
else
  install -m 0600 "$HERE/kebab-mcbridge-client.env" "$ENV_DST"
  echo "  → $ENV_DST (600)"
fi
chmod 600 "$ENV_DST"

echo "==== 4/5 安装 systemd 单元 ===="
install -m 0644 "$HERE/kebab-mcbridge-client.service" "$UNIT_DST"
systemctl daemon-reload
echo "  → $UNIT_DST"

echo "==== 5/5 启动并检查 ===="
systemctl enable --now kebab-mcbridge-client.service
sleep 6
systemctl is-enabled kebab-mcbridge-client.service || true
systemctl is-active kebab-mcbridge-client.service || true
echo "--- 最近日志 ---"
journalctl -u kebab-mcbridge-client.service -n 15 --no-pager 2>/dev/null || true
echo
echo "--- 当前配置（隐藏密钥）---"
sed 's/^\(MCB_SECRET=\).*/\1<已隐藏>/' "$ENV_DST"
echo
echo "--- 失败单元检查 ---"
systemctl --failed --no-pager 2>/dev/null | head -5 || true
