# 全屏只读状态面板与按需XFCE

## 2026-10-06：网络与入口页

新增第三页，启动先显示网络页，轻触轮换三页。显示wlan0的有效IPv4/IPv6、校园认证入口及已有CNB酒馆/MC中继地址和服务状态；动态地址从本机读取，凭据不进入快照或画面。

`kebab-panel-network.timer`每30秒运行本地只读采集，把经过格式校验的地址发布到`/run/kebab-panel-network/status.json`；普通面板不读取私有配置。网络快照超过90秒失效，酒馆入口超过180秒隐藏。服务active和地址同步均不代表公网可达或Minecraft玩家验收。没有访问CNB API、登录门户或改变已有网络配置。

已部署的面板首次升级使用`scripts/linux/update-kebab-panel-network.sh`，参数为重新核对的旧面板SHA-256。新增文件和单元已存在时拒绝覆盖；先保存旧面板，再替换并启用新增timer，失败恢复旧面板。完整首次安装还需暂存`kebab-panel-network.py`及两个新增单元，并在验证后启用timer。新源码附11项回归。

本机11项测试、部署哈希、systemd验证和Xorg画面回读通过。新版用户物理亮屏/触摸、整机重启与长期温升未验收。下方两页及不显示IP的描述保留为10/04历史。

2026-10-04：本分支为OnePlus 8T KB2000上已运行的Debian 13提供本地状态面板，保留XFCE作为按需维护桌面。分支为`codex/status-panel-20261004`，不合并到`main`。本次只发布面板增量的文本源码、配置和说明，使用公开仓库原有的独立脱敏历史；没有发布镜像、固件、身份、地址、凭据、原始截图或本地管理仓库历史。

## 功能与边界

- Xorg modesetting / Adreno 650 glamor，GTK3单个全屏窗口，1080×2400竖屏、中文字体、黑底、无动画。
- 首页显示CPU区间占比与温度、内存、系统盘与数据盘可用空间、wlan0双向速率、电量与电池温度、限充快照。轻触切换说明页。
- 亮屏每3秒采样，布局每分钟微移；亮度请求为0时暂停指标采样和定时重绘，仍轮询亮度请求。X expose事件及初始窗口可能绘制一次。
- 面板程序以普通用户运行，只读指标；不写充电、亮度、温控、休眠或服务控制。既有电源键服务负责短按亮灭屏，Xorg不另外定时消隐。
- 限充采用每分钟健康快照，90秒内且格式有效才展示状态。未知、失效数据不会显示成正常0。停充读回快照不代表瞬时USB功率或完整充电周期。
- wlan0接口在线不代表校园门户认证或外网正常；面板不发外网探测，不展示实际IP或身份。数据盘未挂载时不会显示根盘容量作为数据盘。
- Xorg禁用TCP监听，XDMCP和VNC关闭。面板与维护桌面互斥，切换入口用文件锁串行运行。

## 前置条件

这是针对已验证手机运行环境的安装入口，不是从Android开始的完整部署工具。实际验收运行于`7.2.0-kebab-debian13-r2-charge-policy+`；本分支没有补齐该充电内核组及其配套服务的完整移植配方。安装脚本会核验arm64、运行内核、默认`multi-user.target`、既有充电策略和电源键服务，并拒绝覆盖已有面板文件。

需要已有`debian`普通用户、正常显示触摸、LightDM/Xorg/XFCE、GTK3与Python绘图桥接：`python3-gi`、`python3-gi-cairo`、`python3-cairo`、`gir1.2-gtk-3.0`、`fonts-noto-cjk`、`xserver-xorg-input-libinput`以及`xset`。本次实机已有这些依赖，没有安装或升级包。

面板读取已有`/run/kebab-health/status.json`、标准proc/sysfs、`/srv`挂载点和已验证的亮度请求节点；**安装脚本不会创建充电策略、电源键服务或修改其参数**。其它设备/内核须重新核对路径、服务及显示行为，不能直接当作已验收部署。

## 安装与启停

在已经满足前置条件的手机Linux环境，将下面文件放入独立临时目录并生成输入校验清单：

```sh
stage=$(mktemp -d /tmp/kebab-panel.XXXXXX)
cp scripts/linux/kebab-status-panel.py scripts/linux/kebab-panel-session.sh \
   scripts/linux/kebab-display.sh scripts/linux/test-kebab-status-panel.py \
   scripts/linux/install-kebab-status-panel.sh "$stage/"
cp config/kebab-panel.desktop config/kebab-panel-lightdm.conf \
   config/kebab-maintenance-lightdm.conf config/kebab-status-panel.service \
   config/kebab-maintenance-desktop.service "$stage/"
(cd "$stage" && sha256sum *.py *.sh *.conf *.desktop *.service > SHA256SUMS)
sudo bash "$stage/install-kebab-status-panel.sh"
```

安装脚本保存原图形配置和状态，运行测试及单元检查，新增文件后仍不自动启用服务。确认有界试运行、真实显示触摸与热状况后，可选择启用`kebab-status-panel.service`。部署后的入口为：

```sh
sudo kebab-display panel     # 切到面板
sudo kebab-display desktop   # XFCE，最多20分钟
sudo kebab-display off       # 停止图形会话，亮灭仍由电源键负责
sudo kebab-display status
```

维护桌面到期停止，systemd可能记录预期timeout；需手动执行`panel`返回面板，不会自动返回。原`lightdm.service`或旧测试会话正在运行时入口拒绝切换，应先检查并明确停止该会话。

## 实机验证与限制

- 6项故障处理测试通过：CPU差分与异常、网络计数器重置、过期/损坏/格式漂移的快照、数据盘缺失、无效传感器。
- 用户确认实际显示和轻触切页正常；短按亮灭仍工作。12秒熄屏窗口采样和重绘计数不增加，亮屏12秒各增加4次。
- 实际启动XFCE会话、窗口管理器与桌面，再返回面板；SSH、充电策略、健康timer和电源键服务持续正常。
- LightDM、Xorg与面板的PSS合计约177MiB；RSS相加约259MiB包含共享页。亮屏12秒进程CPU差分约为单核1.7%，不是长期性能基准。
- 用户确认本次短时使用不烫，但明确指出长期开启温升仍不确定。扫描仍120Hz，面板完全断电、长期OLED固定内容影响、联合重启和长期稳定性未验收；电池温度不能代替USB附近局部温度。

本轮没有重启、刷分区、改充电/温控/网络/既有按键配置。手机上的面板已部署及启用开机启动；Git分支切换不改变手机服务状态。软件退出和停止服务不会请求整机关机。

## 回退

```sh
sudo kebab-display off
sudo systemctl disable kebab-status-panel.service
```

程序和配置可保留供以后按需启动；默认`multi-user.target`及原LightDM禁用状态保持，既有电源键服务继续控制亮灭。不需要恢复boot或修改分区。部署前配置归档和原始验收记录只保存本地，不随本次分支发布。
