# 限充 TCPM 在线误判修复（2026-11-05）

## 发现与当前边界

Venus 临时内核验收期间，用户追问限充是否确实工作。实际确认：**MP2762 停充动作与回读可以正常工作，但原用户态策略把 TCPM online=1 设为额外前提，重启后该读数失真时会一直等待。**这在 r4 与返回的原 r3 都发生，不能归为 r4 新增的独有故障。

修复已经部署并保留在原 r3 的限充服务；80/75 阈值、三次确认/15秒间隔、温度资格、硬件安全门、90秒内核租约及充电参数均保持。第二轮Venus结束正常返回后，**真实TCPM online=0的启动场景已自动停充并续租，不需要重插USB**，随后5次电池采样均0µA/Not charging。完整75→80充电循环仍未验证，TCPM误报本身仍存在。

## 实际证据

- r4 启动日志：电量84%、温度36.4°C、TCPM online=0；`REG08=0x16`（充电使能）、`REG13=0x3b`（POWER_GOOD置位），策略返回 wait/owned0。
- 改接路由器后，online变1，三次确认后策略 inhibit，回读 `REG08=0x06/owned1/last_error0`，随后 renew。五次电池采样均为0µA、Not charging。
- 主代理正常回原r3后，同一现象再次出现：87%、online0、`REG13=0x0b`、电池339,000µA/Charging。用户重插后恢复停充，后续电量计更新至88%；这是先前失去停充资格期间的记录，不能声称全程受保护。

原策略这一分支确实存在：`eligible`同时要求TCPM online=1及MP2762 POWER_GOOD。源代码证据为本地0005补丁中的 `MP2762_CHARGE_POWER_GOOD=0x02`；内核 inhibit 函数本身也要求该位、无UV、控制寄存器与watchdog安全，才清除CHG_EN。

## 修复

用户态策略改以 **MP2762 REG13 bit1** 作为输入有效证据，去掉TCPM在线值这一项冗余门槛。其它资格与所有权行为保持：无PG时不启动抑制；已持有租约时失去资格会恢复自身保存的充电使能；未持有租约时绝不打开别人关闭的充电。

日志保留真实 `online`，另记 `tcpm_online`、`mp2762_power_good`、`input_evidence=mp2762_reg13_power_good_bit1`。没有把0伪装成1。输入证据来自决策采样，动作后的snapshot来自寄存器回读，二者时点可能不同。

改动为 `scripts/linux/kebab-charge-policy.py`、`tests/Test-ChargePolicy.py`。原脚本SHA `a51b2d63e009c3cb5b0c31bb328f332f890ac0e367c2be086ea1c538c6af20c4`，新脚本SHA `baa773c519b1f45b6bdb6f3165abec774b1265fcee64ccf95d6b4a8f445f15d3`。root-only备份位于手机 `/srv/kebab-charge-gate-20261105/`；部署仅原子替换脚本并重启该服务，没有重启手机。

配置SHA保持 `baf79144bdb335d50cef18c6a4d304ea50af528a089a53daa56a6fb0566633da`；service SHA保持 `b9a96c78d62d2b9285e8a3442c880708cf845dc80d8ca5f0f3e20c46a85cfec3`。

## 验证

11项WSL单元测试通过，覆盖online0+PG1确认停充和续租、online0/1+PG0均不启动控制，以及旧的故障、温度、所有权、恢复和滞回边界。独立只读审查未发现阻断项。

用此次真实84%/online0/PG1样本做无sysfs写入的回放：旧策略三次均wait；新策略wait、wait、inhibit。手机上的 `--once` 预演也通过，control=false，没有抢占原服务租约。

正式部署后取得15个样本、跨度约70秒：停充owned始终1、last_error始终0，90秒租约按15秒续期，采样值76–86秒；电池电流读数从232,000µA逐步降至0，最后三个样本均为0/Not charging，电量88%、温度约36.2–36.3°C。因此不能把刚写停充后仍有电流读数的一瞬间当作最终结果，也不把电池0µA说成USB整机输入0W。

证据位于 `local/venus-enable-20261105/charge-policy-*`、`r3-charge-stopped.txt` 和手机备份目录。限充不会主动将88%放电回80%；此次修复处理的是策略资格门，不宣称修好了TCPM在线检测本身。

## 重启后online0真实验收

第二轮临时r4启动时TCPM为1，启动后约38秒自动停充，该轮不能证明online0分支。随后按约定正常返回原r3，boot `9a36b7eb-4419-499e-8ddf-7afdf8c72dd8`；期间没有拔插USB。启动约7.79秒：88%、online0、MP2762 PG1、REG08=0x16/owned0；约37.82秒：策略inhibit，REG08=0x06、owned1、last_error0、回读通过；约52.84秒及后续：online仍0，正常renew。

电量计在停充后短暂仍报73mA/Charging，未把这一刻当作电流归零。启动105.60–125.66秒的五次样本均88%、0µA、Not charging、36.7°C，确认实际净充电停止。此结果证明此次启动的误报不再挡住限充，不代表所有线材/掉电故障或完整滞回周期已验收。

记录 `local/venus-enable-20261105/oem-r3-restoration.json`（含完整本boot限充日志、五采样及服务/分区恢复信息），双端SHA `b464c6e3bf14132c9cba4b26a5eb022dcb41665256020dcca40c8c1c0edf3bab`。最终原r3限充脚本仍为baa773c5…，配置与unit保持；限充服务MainPID569/NRestarts0，无failed。

## 正式r4启动复验

正式启用r4后，无USB重插，boot7065f22c…仍出现TCPM online0/PG1；新策略在启动38.11秒自动inhibit、53.12秒及后续renew，REG08=06/owned1/last_error0。128秒时电量计89%、0µA/Not charging、37.6°C。这再次验证online0不挡住停充，不把89%说成此前从未升高，也不宣称完整滞回或TCPM本身已修复。证据存手机 `/srv/kebab-r4-deploy-20261105/`，与前一临时轮/原r3记录分开保存。
