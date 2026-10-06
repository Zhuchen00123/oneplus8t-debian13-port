# 全屏只读状态面板与按需XFCE（2026-10-04）

## 2026-10-06 网络与CNB入口增补（当前）

按用户要求阅读项目入口、酒馆迁移/性能、校园网络与MC修复记录后，已经在手机部署第三页“网络与入口”。启动先显示此页，轻触依次切换网络页→服务器指标→状态说明→网络页；原按键服务继续负责亮灭。本次仅更新面板程序、新增只读快照服务与timer，没有重启手机、改网络认证、中继配置、充电、温控、按键或USB SSH。

- 校园网：wlan0当前IPv4、有效全局IPv6（没有则明确未获取）、大学城/番禺认证入口`http://10.0.3.2/`及SSH端口提示。地址从本机`ip -j address`读取，不硬编码动态IP。
- CNB酒馆：控制器最近同步的HTTPS入口，以及独立控制器、SSH、HTTP服务状态。读取成功同步时间，超过180秒隐藏旧入口；地址同步和服务active不等于公网访问测试。
- CNB MC：只显示配置中的中继主机/端口与客户端状态，不包含路径令牌或共享密钥，不代表Minecraft本体或玩家可用。
- 新增`kebab-panel-network.timer`每30秒运行一次本地只读采集；不会访问CNB API、登录校园门户或发公网探测。root采集器仅把经过格式校验的地址和服务状态发布到`/run/kebab-panel-network/status.json`，普通面板不获得读取私有目录的权限。90秒以上的网络快照视为未知。
- 面板亮屏仍每3秒读快照，熄屏暂停指标采样/重绘；轻量网络快照timer独立继续运行。账号、密码、令牌、工作区编号不进入快照、画面或面板诊断；动态地址与实机截图只留忽略的local。

实机11项回归、传输及部署后SHA-256、systemd静态验证通过。真实Xorg回读为1080×2400，第三页校园地址/CNB两条入口与状态已显示；网络timer enabled/active且采集结果success，原面板/按键/限充/健康/酒馆中继/MC/校园维护服务采样正常、无failed unit。按键、限充及CNB控制器启动时间保留，软件亮度请求仍0。这里的画面回读不是用户物理亮屏或触摸确认；本轮不重新宣称真实切页、重启或长期热稳定性已验收。

另发现`--preview --page 0`在旧版和候选版均有部分文字缺失，而既有线上Xorg画面正常；本次没有改动其无关渲染机制。第三页离屏预览与部署后Xorg画面均已复核。

新增源码`scripts/linux/kebab-panel-network.py`，配置`config/kebab-panel-network.{service,timer}`；现有部署的首次升级脚本`scripts/linux/update-kebab-panel-network.sh`要求传入核对过的旧程序SHA-256，拒绝覆盖已存在的新采集器/单元，先备份后原子替换，失败恢复面板并停用新增timer。首次完整面板安装需连同采集器/单元暂存，验证后启用`kebab-panel-network.timer`。本轮手机旧程序在`/var/backups/kebab-panel-network-20261006-040238/`，主机证据在`local/panel-network-20261006/`。新面板SHA-256为`134ffd788a0b3ebb2a80bfe2d20ac120c66a8acd766e178c714fd879b0cf3b00`。

如需回退，仅停止/禁用新增timer、停止采集oneshot，核验备份SHA256SUMS后原子恢复该目录的`kebab-status-panel`到`/usr/local/bin/kebab-status-panel`，最后重启`kebab-status-panel.service`。保留新增文件供审计，不停止任何校园网、CNB中继或电源服务。

下文是10/04部署及验证的历史记录，其中“两页”“不显示IP”和公开分支提交信息仅适用于当时版本；本次改动没有推送GitHub。

用户选择全屏只读状态面板，保留XFCE作为需要时启动的维护桌面。已部署到当前Debian 13 / `7.2.0-kebab-debian13-r2-charge-policy+`，用户实机确认显示和切页正常、短时使用未烫手；用户明确指出长期开启的发热仍不确定。本轮没有重启、刷分区、改校园网/充电/温控/既有电源键或基础监控配置，没有安装或升级软件包。

代码、配置及本阶段文档保存在独立Git分支`codex/status-panel-20261004`，按用户要求不合并main。用户随后明确授权推送GitHub，已从公开库main的独立脱敏历史导出12个增量文本文件，发布到[同名公开分支](https://github.com/Zhuchen00123/oneplus8t-debian13-port/tree/codex/status-panel-20261004)，公开提交`18694a21863d155817e539ac3d59c8406280d0e1`，源代码对应本地`16ab07f`。公开main仍为`99c24fe9516cc81be2c907eb6e862ac983a26227`，没有合并或上传本地私有历史。公开版说明独立重写，未上传原始截图/记录或配置备份。手机运行状态与Git分支是两件事：手机已经部署了该分支功能，切换主机Git分支不会自动卸载手机面板。

## 运行方式

- 面板使用已安装的Xorg modesetting / Adreno 650 glamor、GTK3、Cairo、Pango与中文字体。单个普通用户图形程序，分辨率1080×2400；没有桌面窗口管理器、桌面图标或常驻浏览器。
- 首页：CPU区间占比与CPU温度、内存已用/总量、系统盘与数据盘可用空间、wlan0接口与双向速率、电量与电池温度、限充读回快照。
- 轻触屏幕切换说明页：运行时间、内核版本、限充快照年龄、使用方法与验证限制。轻触只切页，不控制系统服务或硬件。
- 亮屏每3秒采样，静态黑底、无动画；整体布局每分钟轻微移位。亮度请求为0时暂停指标采样与定时重绘，只读亮度请求并更新RAM诊断状态；初始窗口或X expose事件仍可能绘制一次。
- 熄屏和亮屏继续由既有`kebab-powerkey-screen.service`负责。面板程序不写亮度、充电、温控或休眠接口，不捕获电源键；不把`actual_brightness=39936`作为物理亮度判据。仍未降低120Hz扫描或验证整条显示电源链断电。
- 限充来自每分钟健康快照；只接受90秒内、结构有效的记录，过期、格式异常或读取失败显示未确认。显示“停充读回已验证（快照）”不代表瞬时USB功率、旁路供电或完整物理充电周期。电池温度不是USB附近局部温度。
- wlan0接口在线不代表外网认证通过；面板不发外网探测，不显示实际IP、账号、凭据或设备序列号。`/srv`未挂载时显示未知，不用根目录容量冒充数据盘。

## 启停入口（手机SSH内）

```sh
sudo kebab-display panel     # 切到面板
sudo kebab-display desktop   # 切到XFCE维护桌面，最多20分钟
sudo kebab-display off       # 停止图形会话；需要熄屏时短按电源键
sudo kebab-display status
```

面板与维护桌面互斥；切换脚本用锁串行执行。正常`lightdm.service`或旧`kebab-xfce-test.service`正在运行时拒绝切换，避免意外覆盖别的显示会话。维护桌面20分钟到期会停止，systemd可能记录预期的timeout；再次执行`panel`可清除该失败状态并返回面板，**不会自动返回**。

`kebab-status-panel.service`已enabled/active，默认目标仍是`multi-user.target`；原`lightdm.service`disabled，维护桌面单元static、按需启动。开机先熄屏由既有按键服务负责，面板排在该服务启动之后；**本轮未新增整机重启验收**。Xorg禁用TCP监听，XDMCP和VNC关闭；LightDM仅用于创建本地图形会话，面板以debian普通用户运行。

## 验证记录与占用

| 验证 | 实际结果 |
| --- | --- |
| 输入故障处理 | 6项测试通过：CPU差分及异常、网络重置、失效/损坏/格式漂移的快照、数据盘缺失、无效传感器；未知值不显示成正常0 |
| 文件与服务 | 传输SHA-256、Python运行、shell语法、systemd-analyze verify通过；新增文件安装前拒绝覆盖既有文件 |
| 实际显示路径 | Xorg日志`glamor X acceleration enabled on FD650`；xrandr为1080×2400@120Hz；实际X root画面回读为1080×2400 PNG |
| 真实操作 | 用户确认显示切页正常；实际亮/灭请求、page变化、采样计数均取得；不是合成点击验收 |
| 熄屏暂停 | 12秒窗口采样数1→1、重绘数1→1、按键请求0；窗口外可能有正常expose事件 |
| 亮屏刷新 | 12秒窗口采样39→43、重绘60→64，前后请求亮屏；每3秒一次，无动画 |
| 整体图形资源 | 两个LightDM进程、Xorg与面板PSS合计约176.8MiB；RSS相加约259.1MiB含共享页，不能等同独占内存。亮屏12秒进程CPU差分约单核1.66%，不是整机占比或长期基准 |
| 桌面切换 | 实际启动XFCE的session/xfwm4/xfdesktop及Xorg，再返回面板；同屏模式，亮度请求仍0 |
| 基础服务 | 多轮SSH/限充/健康timer/电源键连续active，最终无failed unit；没有新增Xorg TCP监听 |

初版切换入口对已卸载单元调用`reset-failed`导致提前退出；已修正为仅重置实际failed单元，并执行XFCE→面板复测。LightDM保留AccountsService缺失、locale/utmp提示；未因这些提示安装额外组件，实际会话正常。

尚未验收：长期开屏温升、OLED长期固定内容风险、驱动完全退电、开机面板与按键服务的联合重启、长期运行稳定性。短时未烫手只反映用户本次观察，不能关闭既有局部热问题。曾出现的间歇误触限制继续保留。

## 文件、备份与回退

源码：`scripts/linux/kebab-status-panel.py`、`kebab-panel-session.sh`、`kebab-display.sh`、`install-kebab-status-panel.sh`、`test-kebab-status-panel.py`；配置在`config/kebab-*panel*`及`config/kebab-maintenance-*`。

手机新增：`/usr/local/bin/kebab-status-panel`、`/usr/local/libexec/kebab-panel-session`、`/usr/local/sbin/kebab-display`、`/usr/share/xsessions/kebab-panel.desktop`、`/etc/kebab/{panel,maintenance}-lightdm.conf`、两份systemd单元及面板启动链接。面板诊断在普通用户的`$XDG_RUNTIME_DIR/kebab-status-panel.json`，不包含账号/IP。

基线图形配置、服务状态和亮度请求记录在私有`/var/backups/kebab-status-panel-20261004-223754/`，tar.gz哈希校验通过；部署前两处dmrc不存在，归档警告已记录，不假装备份了它们。程序输入、实机画面、测量JSON与最终哈希在主机`local/status-panel-20261004/`。这是配置级备份，不是整机或分区快照；LightDM会话可能生成普通用户图形缓存，不覆盖既有充电/网络配置。

停用自启并回到无桌面服务器：

```sh
sudo kebab-display off
sudo systemctl disable kebab-status-panel.service
```

既有短按切屏服务保持运行；程序和配置可保留，便于以后按需启动。默认目标及原LightDM开机禁用状态与部署前一致，不需要恢复boot、GPT或数据盘。
