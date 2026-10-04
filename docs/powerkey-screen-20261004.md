# 短按电源键切换屏幕（2026-10-04）

用户要求“没事的时候按一下关机键熄屏”。已在当前无图形桌面的服务器环境启用短按切换亮/灭，服务器继续运行；用户实机确认“已不烫手，短按能亮屏和熄屏”。

## 实现与范围

- [按键程序](../scripts/linux/kebab-powerkey-screen.py)只读取实际pm8941_pwrkey设备（当前event2），按设备名称发现，不硬编码event编号。aarch64 input_event为24字节，设置事件时钟为monotonic，避免校时跳变误判长按。
- 只处理KEY_POWER的一次完整短按释放（30ms–1.2s）；重复/音量/触摸事件不触发，长按和丢事件不切屏，不抓占输入设备，也不调用关机、休眠或充电操作。
- OLED亮度请求在0与512之间切换，512是保守软件请求值，不是已校准的物理亮度百分比。启动监听时先请求0；[systemd单元](../config/kebab-powerkey-screen.service)已enabled/active，配置为后续开机启动并先熄屏，本轮尚未新增重启验收。
- logind继续HandlePowerKey=ignore，避免同时执行短按关机；实际DBus值已核验。原有硬件长按行为不由这个服务更改，也没有做破坏性的长按试验。
- 本方案控制屏幕发光，没有验证完全关闭面板/DSI/DPU供电或改变120Hz扫描；不能把熄屏称为整机断电或证明局部发热原因已解决。未来启动XFCE时要复核桌面自身的电源键处理，当前验收限于无桌面服务器模式。

## 验证

已执行5项Linux按键解析测试：短按/重复释放、长按/抖动、其它键、丢事件、时间倒退。主机Windows直接运行因缺少Linux fcntl未执行测试，随后在WSL Debian实际运行全部通过。默认程序模式只读探测；实机探测匹配pm8941_pwrkey、4095最大请求值，systemd-analyze verify通过。

服务启用后真实记录多次short_press_wake/dark、亮度请求512/0；用户确认屏幕能亮/灭且当前已不烫手。严格USB SSH和限充服务保持active，系统无failed unit，监听服务NRestarts=0、内存约6.25MiB；最后状态为请求0。

重要限制：内核actual_brightness一直返回39936，即使请求0或512仍不变，且超出4095的软件最大请求值。该数值读回不能作为物理亮度或熄屏效果的独立证据；本轮物理效果来自用户确认。当前驱动软件默认亮度4095也不等于已经证明实际最大亮度。

## 部署与回退

仅新增/usr/local/sbin/kebab-powerkey-screen和/etc/systemd/system/kebab-powerkey-screen.service，以及其启动链接和RAM状态。原始亮度请求、实际读回、logind配置已备份手机私有目录/var/backups/kebab-powerkey-screen-20261004-203450/。程序日志只记状态切换，不记录其他输入或按键内容。

服务状态在/run/kebab-powerkey-screen/status.json。若停用，执行systemctl disable --now kebab-powerkey-screen.service；停止监听保留最后亮度请求，不自动亮屏或关机。需要手动亮屏时，可在确认设备已冷却后将受支持亮度节点设为512；不要根据actual_brightness=39936直接写回该数值，因为它超出软件范围。

本轮没有改充电参数、网络配置、boot或GPT，旧封存镜像也没有重打包；它们不包含本轮新增服务。原始安装与功能验收证据在local/server-readiness-20261004/。局部热问题另见[发热记录](local-heating-20261004.md)。
