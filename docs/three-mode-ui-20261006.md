# 三段键三模式界面：实施与回退（2026-10-06）

后续状态：三段键已内建于 r3，正常重启后的三档真实界面／触摸已获用户确认；Firefox ESR 和官方 ARM64 QQ 已加入下档。参见[内建与重启验收](slider-builtin-20261006.md)、[应用与恢复](desktop-apps-recovery-20261006.md)。下方 r2、未重启描述为最初实施阶段。

已部署到持久 Debian 13，内核仍为 `7.2.0-kebab-debian13-r2-charge-policy+`。三段键驱动不变，新增普通用户 Phoc / Phosh 会话；软件安装没有升级或删除既有包，没有刷分区或整机重启。用户暂无蓝牙键盘，先完成软件，真实 HID 验收留待有外设时进行。

## 当前交互

| 档位 | 界面 | 输入与保留 |
| --- | --- | --- |
| 上／0 | GTK3 + VTE 本机终端 | 自带完整 ASCII 键盘，Esc/Tab/Ctrl/Alt/Shift/方向键；点击修饰键作用一次，长按持续锁定；退格长按重复；复制、粘贴、字号和收起键盘；独立 tmux 保留任务 |
| 中／1 | 原三页只读仪表盘 | 点按切页，保留页码；请求亮度为0时暂停指标采样／重绘 |
| 下／2 | Phosh 手机桌面 | 点击输入框自动弹出 Squeekboard，沿用桌面键盘收起方式；没有额外右上角按钮；普通桌面应用适配程度各异 |

用户原生实测回复“好了”，指出下档独立键盘按钮不好点、输入框本身能自动唤出，因此最终去掉独立按钮，改为桌面自动输入方式。此前的手动按钮方案已被该反馈覆盖。

换档只隐藏／显示覆盖层，共用一个 Wayland 会话，不结束桌面应用和终端客户端。档位层读取驱动新鲜、有效、已报告的快照，另外等待约0.7秒稳定；未知／过期样本保留最后视图。熄屏拨档不会写亮度。电源键仍由既有服务控制，Phoc专用US键盘映射把KEY_POWER映射为VoidSymbol，避免Phosh再处理同一次电源键；原始evdev监听不受影响。关闭Phosh自动休眠、锁屏和空闲超时，没有安装GNOME设置守护进程、NetworkManager或完整phosh-core套件。

终端是普通用户 `debian` 的真实PTY，提权沿用sudo，不记录按键内容、密码或终端输出。VTE滚动容器支持触摸回看，长按提供选择全部／复制选区，原生选区也可复制。多行粘贴预览确认。终端客户端退出后提供重连；整个图形会话重启后重新附着独立tmux。第一版单会话、US布局，无中文输入法、自动旋转或20分钟使用提醒。

## 运行结构

- `kebab-alert-slider.service`：既有IST8801档位发布，enabled/active。
- `kebab-terminal-backend.service`：独立tmux服务器 `-L kebab-terminal`、会话 `console`，enabled/active。
- `kebab-three-mode.service`：普通用户PAM seat0/tty7，Phoc原生DRM、Phosh、Squeekboard和界面，enabled/active；DSI-1按scale=2显示。
- `kebab-three-mode-fallback.service`：图形故障后启动原只读面板。心跳超过8秒未更新会终止图形组；启动有20秒宽限。被冻结的客户端也会有界清理。
- 原 `kebab-status-panel.service`：改为disabled，文件与旧Xorg路径保留。LightDM及20分钟XFCE维护环境仍保留。
- `bluetooth.service`：由原服务器策略disabled改为enabled/active；地址初始化服务active，BlueZ看到一个控制器，Powered=true、Discovering=false、Discoverable=false。本轮没有扫描、配对、修改NV或保存外设身份。

PAM会把图形子进程移到登录session scope，因此不能只靠系统服务cgroup回收。包装程序用独立进程组收尾，故障或冻结时清理该组；tmux不属于这个图形组。正常停止返回成功，避免误触发故障回退。原Xorg与新Phoc互斥并明确启停顺序，启动前等待旧显示进程释放设备，避免DRM被占用。

检测实体键盘使用USB／Bluetooth总线和真实键盘能力，排除触摸、电源、音量、三段键和虚拟键盘。连接时收起软键盘，断开时清理本页修饰键并恢复桌面自动键盘策略。硬件枚举、BlueZ服务和合成检测测试不能替代真实无线配对／输入／重连。以后可在上档运行 `bluetoothctl` 完成配对；仅有外设并明确操作时才扫描。配对材料只留手机私有BlueZ目录和忽略的本地证据。

## 验证结果与限制

| 检查 | 结果 |
| --- | --- |
| 纯状态／输入逻辑 | 6项通过：跨中档、无效／冻结样本、驱动重新加载计数、终端组合键、实体键盘能力 |
| 专用XKB | 真实libxkbcommon编译；KEY_POWER为VoidSymbol，a、Enter、方向键保留 |
| 嵌套真实图形 | Phoc X11后端、Phosh、VTE、Squeekboard运行；合成指针点击真实键盘按钮，tmux执行 `echo ui123` 得到对应结果；独立按钮曾通过模拟点击，实机触摸反馈不佳，最终移除 |
| 原生显示／交互 | Phoc发现DSI-1原生DRM，三档画面grim回读；用户原生操作反馈如上。详细长按、滚动、sudo、连续退格等没有逐项物理触摸验收 |
| 应用保留 | 原生GTK4 GNOME文本编辑器在终端／面板／桌面往返后仍存活；空白诊断窗口已关闭 |
| 终端保留 | 图形停止／重启、界面正常退出模拟故障、SIGSTOP冻结测试时，独立tmux后台sleep任务仍活着 |
| 故障回退 | 界面退出自动恢复旧面板；真实冻结触发心跳回退，冻结进程被移除；回退后原生恢复通过 |
| 开销 | 最终熄屏终端档6秒短采样：图形组PSS约242.9MiB，CPU约单核1.0%；不含独立tmux及组外包装程序，不等于长期／亮屏／输入时开销 |
| 基础服务 | 电源键、80/75限充、健康timer、酒馆和三段键仍active；dpkg audit干净；本轮预期测试failed状态已清理 |
| 自启／长期 | 只核验enabled与当前运行；没有整机重启、长期亮屏温升、真实蓝牙键盘、磁干扰或完整充电周期验收 |

此前嵌套阶段检出缺失GNOME设置schema、VTE GI参数差异、Squeekboard焦点重置强制隐藏；原生回退检出Xorg释放竞争及正常退出码误触发回退，均按实际运行修正。GNOME编辑器及最小schema、GTK层协议、截图／VT工具已补齐。精确版本与审阅源码archive hash见[软件来源](../config/kebab-three-mode-packages.json)。GNOME编辑器提供原生输入应用；Thunar/XFCE应用只是兼容入口，不宣称所有旧桌面应用都完成手机适配。

## 维护和回退

日常换档用物理开关。普通用户的 `kebab-ui-mode terminal|panel|desktop|status` 仅选择同一会话内视图，应用入口也调用它；手动选择持续到下一次有效物理换档。

```sh
# 返回三模式会话；已经运行时不会重建
sudo kebab-display mobile
# 旧Xorg只读面板／旧XFCE维护桌面；会结束当前图形窗口
sudo kebab-display panel
sudo kebab-display desktop
# 仅停止图形会话，不负责屏幕亮灭；tmux后台继续
sudo kebab-display off
```

这些维护切换会结束图形窗口，不能绑定三段键。普通换档和短按电源键不会注销。永久退回旧面板：

```sh
sudo systemctl disable --now kebab-three-mode.service
sudo systemctl enable --now kebab-status-panel.service
# 保留正在运行的终端任务；确认不再需要后才停止其后端
# sudo systemctl disable --now kebab-terminal-backend.service
```

蓝牙若不再使用，可 `sudo systemctl disable --now bluetooth.service`；没有必要删配对目录。原面板、基础服务配置与旧维护命令备份留在手机root受限的 `/var/backups/kebab-three-mode-*`。本次候选初装和最终启用分别对应[安装](../scripts/linux/install-kebab-three-mode-candidate.sh)与[启用](../scripts/linux/activate-kebab-three-mode.sh)脚本，初装拒绝覆盖已有候选文件；不能重复初装来掩盖已有改动。

最终20份源码／配置SHA-256清单、包安装日志、原生／嵌套截图及私有测试脚本在忽略的 `local/three-mode-20261006/`，截图含可能的本地状态，不公开上传。项目Git与工作树快照另存backups，管理库不直接推送公开历史。
