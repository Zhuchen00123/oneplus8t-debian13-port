#!/usr/bin/env bash
# ==============================================================
# 手机侧部署：CNB 中继保活 + 地址同步
#
# 在手机上以 root 执行。不写任何分区、不加载模块、不碰电源/充电/温控/SSH/USB。
#
# 幂等：可重复执行。
#   已存在的 /etc/kebab-cnb-keepalive.env **绝不覆盖**（里面有你的令牌），
#   需要换令牌时手工编辑那个文件。
#
# 回滚：
#   systemctl disable --now kebab-cnb-keepalive.timer
#   rm -f /etc/systemd/system/kebab-cnb-keepalive.{service,timer} \
#         /usr/local/sbin/kebab-cnb-keepalive.sh
#   systemctl daemon-reload
# ==============================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

[ "$(id -u)" = "0" ] || { echo "需要 root：sudo bash $0" >&2; exit 1; }

BIN_DST=/usr/local/sbin/kebab-cnb-keepalive.sh
ENV_DST=/etc/kebab-cnb-keepalive.env
SHARE=/usr/local/share/kebab-mc

echo "==== 1/5 检查依赖 ===="
for c in curl systemctl journalctl; do
    command -v "$c" >/dev/null || { echo "缺少 $c" >&2; exit 1; }
done
echo "  curl/systemctl/journalctl OK"

echo "==== 2/5 安装脚本 ===="
install -m 0755 "$HERE/kebab-cnb-keepalive.sh" "$BIN_DST"
echo "  → $BIN_DST"

echo "==== 3/5 安装配置 ===="
install -d -m 0755 "$SHARE"
if [ -f "$ENV_DST" ]; then
    echo "  已存在 $ENV_DST，**保留不动**（令牌在里面，绝不覆盖）"
else
    install -m 0600 "$HERE/kebab-cnb-keepalive.env.example" "$ENV_DST"
    echo "  → $ENV_DST (600)  ⚠️ 还需要手工填入 CNB_TOKEN"
fi
chmod 600 "$ENV_DST"

echo "==== 4/5 安装 systemd 单元 ===="
install -m 0644 "$HERE/kebab-cnb-keepalive.service" /etc/systemd/system/
install -m 0644 "$HERE/kebab-cnb-keepalive.timer" /etc/systemd/system/
systemctl daemon-reload
if grep -qE '^CNB_TOKEN=.+' "$ENV_DST" 2>/dev/null; then
    systemctl enable --now kebab-cnb-keepalive.timer >/dev/null 2>&1 || true
    echo "  → service + timer 已装，timer $(systemctl is-enabled kebab-cnb-keepalive.timer 2>&1)"
else
    systemctl disable --now kebab-cnb-keepalive.timer >/dev/null 2>&1 || true
    echo "  → service + timer 已装，但 timer **暂未启用**"
    echo "     ⚠️ CNB_TOKEN 还是空的。现在就启用的话，每 2 分钟会因拉不起环境而刷日志。"
    echo "     填好 $ENV_DST 里的 CNB_TOKEN 后执行："
    echo "        systemctl enable --now kebab-cnb-keepalive.timer"
fi

echo "==== 5/5 自检（只读，不改配置、不重启、不调 start） ===="
echo
"$BIN_DST" --status 2>&1 | sed 's/^/  /'
echo
echo "--- 预演（--dry-run，不会真的动作）---"
"$BIN_DST" --dry-run 2>&1 | sed 's/^/  /'
echo
echo "--- 失败单元检查 ---"
systemctl --failed --no-pager --no-legend 2>/dev/null | head -5 || true
echo "  （以上为空即无失败单元）"
echo
echo "日志： journalctl -u kebab-cnb-keepalive.service -n 50 --no-pager"
