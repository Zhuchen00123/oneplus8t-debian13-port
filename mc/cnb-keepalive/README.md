# CNB 中继保活 + 地址同步（手机侧）

**状态：已部署并启用**（`kebab-cnb-keepalive.timer` enabled/active，每 2 分钟一轮）。

## 它解决什么

| 问题 | 做法 |
|---|---|
| CNB 开发环境被平台回收 | 调 OpenAPI `workspace/start` 重新拉起 |
| 环境重建后公网地址变了（`<envid>-9000.cnb.run` 的 envid 会变） | 以**远端仓库的 `endpoint.txt` 为唯一权威**读新地址，改写客户端配置并重启客户端 |
| 客户端进程死了 / 卡在重连循环 | 重启客户端 |

**它解决不了的**（必须知情）：18 小时是 CNB 平台硬上限，保活只能"自动重建"，做不到不断线；
每次重建期间隧道断几分钟，玩家会掉线重连。另外环境启动后需要有人进过 VS Code 页面才不会被
10 分钟空闲回收，这一点靠 CNB 侧 `.cnb.yml` 的 `keepAliveTimeout` + `api_trigger` 兜底，不在本脚本范围内。

## 文件

```
/usr/local/sbin/kebab-cnb-keepalive.sh        0755
/etc/kebab-cnb-keepalive.env                  0600   ← 令牌在这里，唯一凭据来源
/etc/systemd/system/kebab-cnb-keepalive.service
/etc/systemd/system/kebab-cnb-keepalive.timer      2 分钟一轮
```

凭据走**两条通道**：service 的 `EnvironmentFile=` + 脚本自己 `set -a; . env`。
（`. file` 只设 shell 变量、不导出到环境——这个坑在门户保活那边已经踩过一次。）

## 关键判据：活性只看 426/101

```
cnb.run 代理对**任何**端口都返回 500/302——包括根本没监听的端口。
所以 500/302 都不是活性信号，只有 426（要求 Upgrade）或 101（握手成功）才是。
```

实测对照：

```
sd2xm49rnt-9000.cnb.run  → HTTP 426   （活）
zzsh2enrz7-9000.cnb.run  → HTTP 302   （死，环境早已回收）
```

## 测试

`mc/tests/test-cnb-keepalive.sh`，**48 项**，用 PATH 上的假 `curl`/`systemctl`/`journalctl`
跑真脚本，不联网、不碰真 CNB、不重启任何真单元、不写系统目录。

```bash
bash mc/tests/test-cnb-keepalive.sh
# 手机上脚本被拍平到别处时：
KEBAB_CNB_SCRIPT=/usr/local/sbin/kebab-cnb-keepalive.sh bash mc/tests/test-cnb-keepalive.sh
```

覆盖：地址变化同步、稳态静默、客户端卡死、环境拉起、缺令牌、防抖、dry-run、status、
未配置 REPO、活性判据、稳态不刷屏、故障恢复后重新打日志。

### 抓到的三个真 bug

**1. "远端给了活的新地址 → 直接退出、永不同步"**（mock 测试抓到）

原来的顺序是"先判活性、再比地址"。当环境重建、`endpoint.txt` 已经写上新地址、
而新地址又是活的（426）时，脚本走"中继活着"分支直接 `exit 0`，
**客户端却还拿着旧地址一直重连**——保活看起来完全正常，实际永远修不好。

修正为：**先同步地址，再判活性**。远端是权威，地址不一致就无条件切过去。

**2. "历史错误被当成当前故障 → 每 2 分钟无脑重启客户端"**（真机日志抓到）

健康检查原本数客户端最近 30 行里的"隧道错误"。但这些错误是**连上之前**的历史，
已经被后来的成功连接覆盖了；于是每轮都误判、每轮都重启客户端，
**隧道每 2 分钟断一次，比不做保活还糟**。

修正为：只看**最后一次"已连上中继"之后**的日志。

**3. `sed -i` 在 systemd 沙箱下必然失败**（推演 + 真机验证抓到）

`sed -i` 的实现是"在同目录新建临时文件、再 rename 覆盖"。而 service 用了
`ProtectSystem=full`，`/etc` 是只读的——即使 `ReadWritePaths` 单独放开了
`/etc/kebab-mcbridge-client.env` 这个**文件**，`rename` 依然要写**目录**，所以会失败。

改成"读进内存、原地写回"（`printf > 文件`），只要求文件可写、不要求目录可写。

**真机验证**：故意把 `MCB_HOST` 改回死地址 `zzsh2enrz7-9000.cnb.run`，
然后通过真实 systemd 单元跑一轮：

```
[23:59:11] ✅ 已按新地址重启
MCB_HOST 现在是: sd2xm49rnt-9000.cnb.run
有无只读文件系统报错: 0
[客户端 23:59:12] 已连上中继 sd2xm49rnt-9000.cnb.run:443
```

## 验证证据

### 真实数据上的地址同步（2026-10-04 23:43）

环境被回收后重新拉起，`endpoint.txt` 写入了新地址，本地还是死的旧地址：

```
远端 endpoint.txt：sd2xm49rnt-9000.cnb.run:443   （HTTP 426 活）
本地配置        ：zzsh2enrz7-9000.cnb.run:443   （HTTP 302 死）

地址变化：zzsh2enrz7-9000.cnb.run:443 → sd2xm49rnt-9000.cnb.run:443
✅ 已按新地址重启
```

客户端随后的日志：

```
[23:43:47] 启动：中继 sd2xm49rnt-9000.cnb.run:443  session=kebab  →  本机 127.0.0.1:25565  TLS=True
[23:43:47] 已连上中继 sd2xm49rnt-9000.cnb.run:443（session=kebab），转发到 127.0.0.1:25565
```

中继自己的 `/status` 页面：

```
魔搭 DSW 隧道 : ✅ 已连接（这一段的管道是通的）
玩家/好友连接 : 0 条
```

### 稳态静默（2026-10-04 23:50）

```
[23:49:50] 无变化：中继活着、客户端运行中
[23:51:00] 无变化：中继活着、客户端运行中
```

三轮连跑只有第一轮打一行；`已重启客户端` 计数为 0。
（稳态行只在**状态变化时**打，`clear_state` 在真动手后清记录，形成"变化 → 恢复"的时间线。
每轮重复的探测细节降级到 `--verbose`。否则 2 分钟一轮一天能刷 700 多行，真出事时反而翻不出来。）

## 运维

```bash
sudo /usr/local/sbin/kebab-cnb-keepalive.sh --status      # 只读状态
sudo /usr/local/sbin/kebab-cnb-keepalive.sh --dry-run     # 预演，不改配置、不重启、不调 start
sudo /usr/local/sbin/kebab-cnb-keepalive.sh --verbose     # 带上每轮探测细节
sudo systemctl start kebab-cnb-keepalive.service          # 手动跑一轮
journalctl -u kebab-cnb-keepalive.service -n 50 --no-pager
```

防抖：一小时内最多 6 次"改地址/重启/拉起环境"。触发防抖说明根因不是"偶尔挂了"，
而是"压根保不住"，需要人工介入，别再让它空转。

回滚：

```bash
systemctl disable --now kebab-cnb-keepalive.timer
rm -f /etc/systemd/system/kebab-cnb-keepalive.{service,timer} /usr/local/sbin/kebab-cnb-keepalive.sh
systemctl daemon-reload
```

## 还没做的 / 未验证的

- **`workspace/start` 这一步只在 mock 里验证过，没在真机上跑过。**
  `workspace/list`（判定环境在不在）已经用真令牌打过，HTTP 200 且响应里能找到
  `zc354/mmmmcccccbuild`；但真正发起"重新拉起"那个 POST 需要环境处于停止状态，
  而它现在正跑着、还驮着隧道，不适合为了测试把它停掉。
  **等它自然被回收时会自动走一遍**，届时看 journal 即可。
- **重启后行为未验收**：`kebab-cnb-keepalive.timer` 的 `OnBootSec=90s` 没有实机重启验证过。
- **公开默认密钥要换掉**：`wst_9f3a7c2e5b18d460`（隧道）与 `fwd_2b7e91c4d6a80f53`（好友转发）
  是 CNB 参考仓库里的公开值，只能挡扫描器。要连同 CNB 侧 `start-bridge.sh` 一起改成私有值。
- **滥用风险**：CNB 官方文档只描述"建分支→开发→提 MR→自动回收"的开发流程，
  把开发环境当常驻游戏中转不在推荐用法内，存在被判滥用而清理的可能。
  架构上落点已做成可替换（手机端只改 `MCB_HOST` 一个参数），将来加一台国内 VPS 即可升级为 7×24。

## 令牌

`CNB_TOKEN` 已配置（27 字符，写在 `/etc/kebab-cnb-keepalive.env`，600 root:root）。
来源是 `F:\codexprojects\mcfuwuqi\.cnb-token`（源文件未改动，主机侧不留副本）。

验证：带该令牌 `GET https://api.cnb.cool/workspace/list?status=running` → **HTTP 200**，
响应里含 `zc354/mmmmcccccbuild`。

安装脚本在令牌为空时会**故意不启用 timer**，免得每 2 分钟刷一条拉不起环境的日志。
