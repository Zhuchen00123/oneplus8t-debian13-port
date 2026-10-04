# 服务器基础与电池控制检查（2026-10-04）

用户暂停本会话的 MC/穿透，优先完成电池及服务器基础配置，选择长期插电目标 80%。[新会话提示词](mc-cnb-resume-prompt-20261004.md)已提供；MC会话与本会话的设备配置写集分开。

后续最新阶段：[80/75限充v1](charge-policy-v1-20261004.md)已构建与打包，默认只读启动及服务安装通过；control镜像重启后USB未出现，正在请求手机屏幕状态。有界租约测试与常驻启用尚未执行；下文为基础配置及较早30秒测试记录。

## 已完成

- 新增只读 `kebab-health` 与一分钟 systemd timer，监测电量、电池温度、CPU温度、根分区空间及可用内存。状态在 RAM 的 `/run/kebab-health/status.json`，只在告警集合变化时输出完整状态，避免每分钟写入详细电池日志。
- 配置记录目标80%，实际输出仍为 `charge_limit_enforced=false`。基础监控只读，不执行硬件操作；本阶段另用隔离候选内核完成了一次用户确认的有界停充测试，不能把该测试当作常驻限充策略。
- 温度/电量/空间阈值仅用于提示：电池45°C、CPU85°C、低电量15%、根分区空闲512MiB、可用内存256MiB。它们不是厂家充电温度限制，也不代替硬件保护。本轮不自动断电或关机。
- 屏蔽 sleep/suspend/hibernate/hybrid-sleep 四个目标，防止 Linux 软件休眠。硬件强制关机流程不受它们控制。
- 时区设为 Asia/Shanghai；NTP仍同步，标准 timesync clock 文件存在，RTC仍未修复。
- 原有 logind `HandlePowerKey=ignore` 实时 DBus 属性已核验；未要求物理短按复测。
- 原有持久 journal 64MiB、运行期16MiB限制已核验，实际占用约59.8MiB；保留原配置，没有清空日志。
- 默认仍 multi-user，LightDM仍禁用。USB严格SSH、校园Wi-Fi配置保留。没有刷分区、写NV/触摸固件、改充电电压/电流、改内核热保护阈值。
- 以上是手机运行后的配置，旧封存r2镜像组未重打包，不能声称它们已经包含这些新服务。

## 80%限充仍未实现；停充位已完成有界实机验证

实机只暴露 `bq27541-0` 电量计和 TCPM USB-PD电源，没有 start/end charge threshold、charge_behaviour、charging_enabled 或 input_suspend 等充电控制接口。电量计属性只读。

生产 r2 运行内核源码 `drivers/regulator/mp2762-otg-regulator.c` 明确只提供 MP2762A OTG升压，充电本身由启动固件/硬件处理；没有注册充电 power_supply。设备树的 charger@5c 也是该 OTG regulator。为做有界验证，隔离候选内核在同一驱动中增加了受门控的 `mp2762-charge-test`，默认启动不启用。

`drivers/usb/typec/tcpm/tcpm.c` 的可写 online/voltage_now/current_now 用于 PD/PPS/AVS协商，不能把它们当作电池停充开关。生产 r2 没有往这些节点写0，也没有直接 I2C/PMIC 寄存器操作；隔离候选测试只通过同一驱动写入并回读 MP2762A REG08 的 CHG_EN 位。

本轮采样电池报告100%，约35.2–35.6°C；状态在Charging与Not charging之间变化。后者不是80%限充已生效的证据。读取值未经过完整充放电校准验证，不能由一轮寄存器读数下结论电池寿命或持续充电安全。

真实80%停充需要在候选停充通路上增加电量滞回并实测，或使用可控的外置USB供电。这个缺口没有被监控脚本或本次30秒测试替代；常驻前仍需跨阈值、插拔、重启、温度及长期负载验收。

后续调查已找到 Android 社区接口和 MP2762A 停充位；具体固定来源、看门狗限制、候选实现及实机证据见 [充电控制调查](charge-control-research-20261004.md) 与 [驱动候选](charge-control-candidate-20261004.md)。一次有界候选测试确实写入并恢复了 REG08，但不能把它当作80%限充验收。

### 30秒实机停充测试

2026-10-04 在 `7.2.0-kebab-debian13-r2-charge-candidate+` 上以一次性 `allow_charge_test=1` 启动参数运行：`REG08=0x16` 清除 CHG_EN 后回读 `0x06`，`owned=1`；电池状态为 `Not charging`、电流读数为 `0`，USB 在线和 `usb0` 载波保持。约30秒后驱动自动恢复为 `REG08=0x16`、`owned=0`，电池恢复 `Charging`，USB/SSH 连续。随后恢复默认只读 boot 镜像并重启；重启后 `allow_test=0`、`charge_behaviour` 权限为 `444`、健康 timer active、无 failed unit。证据仅存 `local/charge-candidate-20261004/`，不上传。

## 验证与恢复

- 主机用例确认：即使模拟sysfs有阈值文件，监控也不写入；过热/超目标有提示；坏传感器数据不被当作有效数值。
- 实机 `systemd-analyze verify` 通过；沙箱内 service 成功，timer 已启用，连续多轮自动采样通过；内存峰值约6.5MiB。告警没有变化的后续采样未重复输出完整状态。
- 防休眠目标显示masked，时区/同步及电源键DBus状态通过，无failed unit。没有为验证监控自启动而主动重启，避免打断已认证校园会话。
- 安装前旧日志/电源配置、时区及休眠目标状态在设备 `/var/backups/kebab-server-base-20261004/`。输入和安装后文件hash核验记录、硬件只读原始材料在主机 `local/server-base-20261004/`，不上传。

查看最新实时状态：

```bash
sudo /usr/local/sbin/kebab-health
systemctl list-timers kebab-health.timer
```

撤销本轮常驻监测和防休眠：

```bash
sudo systemctl disable --now kebab-health.timer
sudo systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target
sudo timedatectl set-timezone Etc/UTC
```

安装的只读文件可保留作审计。上述恢复是本轮安装前实际状态，不包含原有 logind/journal 或USB配置的撤销。
