# 校园网门户自动登录（campusnet）

**结论：本项目走 `eportal_portal` provider。** 上游模块用 [demo133/campusnet](https://github.com/demo133/campusnet) v0.1.2，
本地打了 1 行补丁 + 1 个包装脚本，接在已有的 systemd timer 上。

## 我们学校到底是什么

挂 **Dr.COM 城市热点**的牌子，但**已经换成新版 eportal 接口**。判定依据全是实测：

| 证据 | 结果 |
|---|---|
| HTTP `Server` 头 | `DrcomServer1.0` |
| 门户页面 | `a79.htm` / `Dr.COMWebLoginID_*` / 注销页 |
| `loadConfig` 返回 | `login_method: 1` → 代码走 `page.portal_api + 'login'` |
| `en_md5` / `account_suffix` | `0`（密码明文）/ 空（普通校园网，无 `@dx`/`@lt`） |
| **可用接口** | `:801/eportal/portal/login` → `{"result":1,"msg":"Portal协议认证成功！"}` |
| 老接口 `ACSetting&a=Login` | **HTTP 500 系统发生错误** |
| `/drcom/login`（带账号） | **HTTP 404 URL no found** |
| 注销接口（3 种都试了） | `Logout Error(-1)` / `Logout failed.` / `Radius注销失败！` —— **全坏，没法主动下线** |

### ⚠️ 必须强制指定 provider

campusnet 的自动指纹**会认错**：

```
detect --portal 10.0.3.2
  • drcom      1.00   ← 它会挑这个
  • eportal    0.85
  （eportal_portal 得 0 分）
```

原因是 `eportal_portal` 的指纹要求页面文本里出现 `portal/login`、`dr1003`，而我们门户的登录页
里没有这些字样（真接口是 JS 动态拼的）。而 `drcom` provider 走的 `ACSetting` 在我们这儿是 **500**。

所以 `PK_PROVIDER=eportal_portal` 是**必须**的，不是优化。

## 本地对上游做的改动

### 1. 一行补丁（`install-campusnet.sh` 自动打，幂等）

`campusnet/providers/eportal_portal.py`：

```diff
-                "wlan_ac_ip": "",
+                "wlan_ac_ip": str(self.opt("wlan_ac_ip", "") or ""),
```

**为什么必须改**：本校园的 AC 需要这个字段。同一组参数、同一个 IP，只换这一个值：

```
wlan_ac_ip=172.16.254.2  → {"result":0,"msg":"IP: 10.44.0.100 已经在线！","ret_code":2}
wlan_ac_ip=(空)          → {"result":0,"ret_code":4,"msg":"AC认证失败"}
```

改成读 `self.opt()` 后，用上游自己的 `CAMPUSNET_OPTIONS`（JSON）传值，不再硬编码。

### 2. 账号前缀 `,0,`

哆点格式，`,` 中间那位是 ISP 索引，`0` = 校园网。上游不加，所以本地拼在 `CAMPUSNET_USERNAME` 里
（`PK_ACCOUNT_PREFIX` 可调，默认 `,0,`）：

```
user_account=%2C0%2C3120000000   →  ,0,3120000000
```

### 3. 包装脚本 `kebab-portal-login.sh`

- 在线时**只发 1 个 HTTPS 请求就退出**，不刷日志（campusnet 自己的探测是 4 个 HTTP 请求，没必要每分钟跑）
- 离线时：**先 campusnet（主力）→ 再本地 `kebab-portal-keeper.py`（兜底）**
- **必须给 campusnet 传 `--portal`**：它默认靠网关 302 去"发现"门户，发现不了就直接
  `✘ 找不到认证门户地址` 并以 1 退出（已实测复现）。门户恒为 `PK_PORTAL_HOST`，固定下来。
- `PK_ASSUME_OFFLINE=1` 是测试钩子，用来在不改网络的前提下演练登录分支

## 部署

```bash
# 主机侧：从 GitHub 拿包（raw.githubusercontent.com 在国内会被 reset，用 api.github.com）
curl -L -o campusnet.tar.gz https://api.github.com/repos/demo133/campusnet/tarball/main
tar -xzf campusnet.tar.gz && mv demo133-campusnet-* /tmp/campusnet-pkg
scp -r /tmp/campusnet-pkg kebab:/tmp/

# 手机侧
sudo bash install-campusnet.sh /tmp/campusnet-pkg
```

装到：

```
/usr/local/share/campusnet-pkg/campusnet/          755，a+rX
/usr/local/sbin/kebab-portal-login.sh              755
/usr/local/sbin/kebab-portal-keeper.py             755  ← 兜底
/etc/kebab-portal-keeper.env                       600  ← 唯一凭据来源
/etc/systemd/system/kebab-portal-keeper.service    ExecStart → 包装脚本
/etc/systemd/system/kebab-portal-keeper.timer      60 秒一轮，不动
```

凭据走**两条通道**：service 的 `EnvironmentFile=` + 脚本自己 `set -a; . env`。
（`. file` 只设 shell 变量、不导出到环境，而兜底脚本是 Python、读 `os.environ`——
少一条就会静默拿到空账号。这个坑已经踩过一次。）

## 验证证据

**对真门户（`campusnet login --force`）**：

```
GET http://10.0.3.2/eportal/portal/login?...&user_account=%2C0%2C3120000000
    &wlan_ac_ip=172.16.254.2&jsVersion=4.2.1&terminal_type=1&v=5911
→ :80 先 404，自动退到 :801 → HTTP 200
✔ [eportal_portal] 成功：Portal协议认证成功！
```

**真实 systemd 单元里（临时把探测域名指向 127.0.0.1 制造离线）**：

```
[login] 离线，开始自动登录（账号长度 10，密码长度 9）
[login] → campusnet（provider=eportal_portal，wlan_ac_ip=172.16.254.2）
[campusnet]   • 尝试顺序：eportal_portal
[campusnet]   • 使用 ePortal 新版（portal/login） 登录…
[campusnet]   ✔ [eportal_portal] 成功：IP: 10.44.0.100 已经在线！
[login] campusnet 退出码=0
```

**在线时**：单元零输出（`(无输出 = 正确静默)`），只在 journal 留 systemd 的启停行。

**旁证**：`wlan0` down 10 秒再 up，5 秒内同 IP 自动恢复在线（USB SSH 不受影响）。

## 已知限制

- **没有"免认证"这回事**：任何方案都要账号密码，能免就等于这校园网白送。
- **还没在真实掉线中观察过完整恢复**：注销接口全坏、AC 的会话又能跨 Wi-Fi 重连存活
  （实测 down 10 秒不掉），所以造不出真离线。上面是用 hosts 阻断模拟的，等价性请自行判断。
  真实断线会自动发生，届时看 `journalctl -u kebab-portal-keeper.service`。
- campusnet 的 `wlan_ac_ip` 补丁是**本校园特有**，换学校要重新验证。
- 门户地址硬编码为 `10.0.3.2` / API 端口 `801`（`PK_*` 可改）。学校若换认证系统，整份重来。
- 手机时钟比真实时间慢约 45 分钟（RTC 1970）；门户认证不看时间，无碍。

## 排障

```bash
sudo /usr/local/sbin/kebab-portal-keeper.py --check      # 只探测在线状态
sudo /usr/local/sbin/kebab-portal-keeper.py --dry-run    # 打印将要发的 URL（密码打码）
sudo systemctl start kebab-portal-keeper.service         # 手动跑一轮
journalctl -u kebab-portal-keeper.service -n 50 --no-pager
```

演练登录分支但不改网络：

```bash
sudo systemctl set-environment PK_ASSUME_OFFLINE=1
sudo systemctl start kebab-portal-keeper.service
sudo systemctl unset-environment PK_ASSUME_OFFLINE
```
