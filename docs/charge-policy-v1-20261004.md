# 80% 限充可部署版本 v1（2026-10-04）

目标是长期插电时达到80%停充、下降至75%恢复。此版本把内核寄存器访问与用户态电量策略分开；90秒租约的续期、停止与进程丢失恢复已实机通过，常驻策略已启用并正常重启复验。真实80/75完整循环、插拔与长期负载仍待验收。

## 实现

- 在已验证的补丁0005之上应用 [租约补丁0006](../patches/local/0006-regulator-mp2762a-charge-control-lease.patch)，保留现有30秒测试模式；新增 `mp2762_otg_regulator.allow_charge_control=1` 显式门控，二者不能同时启用。默认仍不允许充电控制写入。
- 新模式首次停充只清除REG08的CHG_EN，建立90秒恢复期限。`charge_control_lease` 接受 `renew` 时只实读REG08/09/13确认状态，再更新内核软件期限；不会反复启停CHG_EN，也不会修改看门狗、充电电压/电流、BATTFET、SUSP或OTP。
- 每次续约须在期限前、仍拥有停充状态、无恢复任务、供电和寄存器状态有效时完成。期限过后不能续约；已排队的旧恢复任务会重新检查期限，避免抢在刚完成的续约之后恢复充电。I²C失败保留恢复所有权并重试，不把未确认恢复当作成功。
- [策略服务](../scripts/linux/kebab-charge-policy.py)每15秒从真实sysfs重新读取电量、温度和USB在线状态，连续3次电量达到80%后请求停充，连续3次降至75%后恢复；中间电量维持停充并续约。连续确认会有约30秒的响应延迟，80%不是物理电量的绝对无误差上限。
- 服务退出时尝试恢复；进程崩溃、被杀或持续读写失败后不再续约，内核在最后续约约90秒后尝试恢复。软件期限不保证内核崩溃、断电或I²C永久故障时仍能执行。原厂硬件保护保留，不能把本策略称为完整充电管理驱动或旁路供电。
- 温度5–43°C是本候选允许申请/续约的测量范围，不是厂商热保护阈值。数据异常、USB断开或状态冲突时释放本服务拥有的停充状态、退出或等待；不会开启本服务没有关闭的充电器。
- 状态只写 `/run/kebab-charge-policy/status.json`；日志只记录状态转换和故障，续约不刷详细日志。状态区分控制是否启用、请求动作、寄存器读回、完整充电循环是否已验证。

## 构建与部署产物

固定来源与补丁顺序见 `sources.lock.json` 的 `r2-charge-policy` 配置，独立内核版本为 `7.2.0-kebab-debian13-r2-charge-policy+`。构建遵循先设备树后内核，不使用Windows文件系统构建源码。

主机脚本 [build-charge-policy-bundle.py](../scripts/linux/build-charge-policy-bundle.py)生成本地 `artifacts/charge-policy-20261004/ready/`；新增只读健康联动的同镜像包另存 `ready-with-health/`，旧部署输入保持封存：

| 文件 | 用途 |
| --- | --- |
| boot-readonly.img | 默认不允许充电写入，用于先验证新内核启动 |
| boot-control.img | 同一组内核/DTB/initramfs，仅增加明确启用租约的启动参数 |
| modules.tar.gz | 578个同版本匹配模块，保留旧树供回退 |
| initramfs-server.gz | 逐个原始ELF字节核验USB与存储依赖 |
| bundle-manifest.json | 输入哈希、模块清单、boot格式与解包校验结果 |
| deploy-charge-policy-kernel.py | 校验本机目标/槽位/输入，备份完整boot_a后更新boot与模块 |
| install-charge-policy.sh | 安装服务并保持禁用；activate/deactivate分别明确启停策略 |
| kebab-health.py | 只读报告策略新鲜度、真实CHG_EN和未过期租约；不执行充电写入 |

这些镜像、模块、固件和本机部署参数只保存在忽略的本地目录，不能上传公开仓库。公开代码只包含构建脚本、策略、配置模板、补丁与脱敏记录。

## 部署与回退流程

1. 核验输入manifest、USB SSH、当前根UUID与boot_a路径，停止Java负载。部署脚本检查当前充电恢复所有权已释放，并校验正在替换的boot前缀哈希。
2. 先部署readonly镜像。脚本在手机保存完整100,663,296字节boot_a，前后设备哈希与备份哈希相同才写入；模块逐份核验后写boot_a并读回。它不重启、不切槽、不写GPT/misc、DTBO、super、NV或vbmeta。
3. 正常重启后核验新版本、`allow_control=0`、`owned=0`、USB/SSH、实际文件持久性。安装服务时默认disable；`--once`只读检查真实传感器，不能触发停充。
4. 经本阶段授权后切换同组control镜像并重启，先有界验证超过90秒的续约、停止服务恢复、杀进程期限恢复，随后才启用常驻80/75策略。后续完整验收仍需真实低于80%的跨阈值循环、插拔、重启与长期负载。
5. 停用策略先执行deactivate，检查`owned=0`与CHG_EN恢复，再回到readonly镜像或保存的前一完整boot_a；旧内核模块树保持，禁止混用版本。内核无法恢复时不执行自动重启来掩盖错误，保留状态并使用已校验的回退组。

槽位依据：本机Linux未收到`androidboot.slot_suffix`，标准misc A/B块也不匹配。本机主/备GPT CRC和boot属性只读核验一致：A active、可启动；B inactive、unbootable。位定义来自固定的 [Qualcomm BootLib参考](https://github.com/SHIFTPHONES/android_bootable_bootloader_edk2/blob/7d391d8b1059ed48259728cab004ca6f16ee3de2/QcomModulePkg/Include/Library/PartitionTableUpdate.h)，另结合已核验当前boot前缀，不对GPT或misc做写入。参考源码不等于本机ABL二进制已完整验证。

## 验证记录

- 已执行：实际共享寄存器核心的ASan/UBSan测试，131,608种停充/续约配置、输入状态、读写故障、结果不确定与恢复冲突；续约均零寄存器写入。
- 已执行：9项用户态策略测试，包含电量抖动、完整模拟80/75周期、模拟一小时纯续约、断开外部供电、温度/寄存器异常、只读模式、写入/读回错误和严格配置校验。
- 已执行：8项部署保护测试，A/B冲突、无active、CRC损坏和主备不一致均拒绝；部分写入失败和坏回读恢复完整备份，恢复失败明确禁止重启。
- 实机历史：补丁0005的一次30秒停充/自动恢复/USB连续性已通过，详见 [候选记录](charge-control-candidate-20261004.md)。这不能替代0006租约调度与持续策略的实机验证。
- 已执行：完整内核构建与578个模块vermagic核验，改动驱动单独W=1通过；完整构建仍有两条已有qcom_fg函数原型警告。新设备树与此前已启动候选逐字节相同，完整配置只改变版本后缀。
- 已执行：200个initramfs模块原始ELF与匹配模块树逐字节核验；两套boot格式与独立AOSP解包通过。两镜像长度均为45,637,632字节，payload相同，44个差异字节全部位于header的cmdline字段。
- 已执行：默认只读镜像写boot_a前完整备份并校验，写后同范围回读一致，正常重启后严格SSH、`allow_control=0 owned=0`、接口444和USB载波1通过。策略服务已安装并disable，实际`--once`读取100%/37.4°C/USB在线，未写充电位。
- 已执行：5项健康联动测试，缓存超时、未来时间、实际寄存器冲突、无效/到期租约和已停止服务均不能声称停充读回已验证。监控的`charge_limit_enforced`仅表示本次采样的真实停充读回，不等于完整80/75循环已验收；监控自身仍无写充电路径。
- 当前尚未证明：常驻策略真实跨阈值周期、插拔/重启组合、长期负载与完整物理充电效果。不能用主机模拟将上述事项标为通过；以下最新记录已补充90秒租约实际测试。

## 历史检查点（18:45；下方有更新）

用户已明确授权：控制模式下不超过约4分钟的续约/服务停止/进程丢失恢复测试，通过后开启常驻80/75策略。该授权已记录，恢复工作时不重复申请。

随后control镜像写boot_a并按同范围回读一致，正常重启后USB未重新枚举；连续SSH、Windows RNDIS与Fastboot查询均未发现设备。此时**有界测试尚未开始、常驻服务仍disable**，不得宣称控制镜像已启动、80%限充已生效或实机90秒租约已通过。已向用户请求当前屏幕状态与USB连接情况；须先确认实际状态并恢复管理链路。

本地readonly镜像SHA-256为 `a3330619b4d55d1a61d71f9335eee364fa4947248a70269caade81d59c4f44f3`；control镜像为 `0ddb6f49e97e7835b77cd605b4b6bce683cf2455ea3c16ea59e917c09c631f96`。手机完整回退备份在 `/var/backups/kebab-charge-policy/20261004-184009/boot_a.img`，前一候选完整备份在 `/var/backups/kebab-charge-policy/20261004-183534/boot_a.img`；本机旧r2与原厂恢复组继续保留。若出现ABL拒绝，应先按已实测按键进入Fastboot，核验设备与A槽，再刷同组readonly镜像并重启恢复；不能在未确认USB/槽位时盲刷。

原始证据仅存 `local/charge-policy-20261004/`：构建/测试记录、只读启动、安装、control写入及两镜像差异。`test-runtime.sh`已准备异常恢复trap，先连续续约100秒，再测试正常停止，最后实际高电量冷启动后SIGKILL、等待内核期限恢复；尚未执行。恢复管理链路后再执行，成功才启用常驻服务。

## 恢复与部署结果（18:59；成功标记随后已修复）

- 用户确认ABL损坏提示并返回Fastboot。只读查询确认A槽successful=no、retry=0、unbootable=yes；Fastboot重刷已验证readonly镜像后retry=7、unbootable=no、当前A槽不变，严格SSH恢复。重新部署SHA-256不变的原control镜像后也正常启动，allow_control=1。证据支持首次失败由未登记启动成功而耗尽次数导致，无存储介质损坏证据；没有做全盘完整性诊断。[启动记账与修复方案](boot-success-repair-20261004.md)。
- 实际有界测试用时224秒：直接停充后服务接管、连续续约超过100秒，CHG_EN持续关闭；停止服务恢复；以真实100%电量冷启动，3次确认后停充；临时关闭进程自动重启，SIGKILL后租约90秒逐步到期，内核恢复CHG_EN，owned=0。全程USB载波/供电在线，清理临时覆盖后没有失败单元。原始证据 `local/charge-policy-20261004/runtime-test.txt`。
- 用户此前明确允许测试通过后开启常驻；现已执行activate，`kebab-charge-policy.service` enabled/active。后续采样真实100%/37.2°C，REG08=0x06、REG09=0x4c、REG13=0x03、owned=1、租约78秒，状态 `inhibit_readback_verified=true`。这证明当前高电量下停充与续约，不证明尚未经历的物理75%恢复周期。
- 电量计的status/current有延迟：关CHG_EN后曾短时仍显示Charging和小电流，后续稳定Not charging/0；恢复后Charging/63mA。因此不把寄存器切换瞬间的电量计读数当作实时功率测量，也不称为已验证旁路供电。
- 最新部署包为本地 `ready-retry-guard/`，两套boot字节与前包相同；部署检查增加successful与tries读取，未成功且剩余次数不足2时拒绝Linux更新。它仍只读GPT，不自动重置次数或改标记。
- 当前A槽剩余5次、successful=false，可正常运行；成功位修复工具及只读原始表备份已完成并核验，尚未授权GPT写入。暂不进行常驻服务启用后的重启验收，避免未经修复重复消耗次数。主机8项成功标记工具测试通过，不能替代实机写入/重启验证。

## 最终本轮部署结果

用户明确允许成功位修复并重启验证后，按已核验方案仅设置主备GPT的boot_a成功位和对应CRC；原始块双端备份、两副本写后读回均通过。正常重启后successful=true、retry保持5、control镜像哈希不变，严格USB SSH和策略服务enabled/active通过。

重启后的真实三次采样完成后，100%/37.9°C、REG08=0x06、REG09=0x4c、REG13=0x03、owned=1、租约83秒、`inhibit_readback_verified=true`，电量计Not charging/0；无failed unit。可交付本轮已实机部署的v1，同时保留 `full_charge_cycle_validated=false`；它不代表尚未执行的真实75%恢复、插拔或长期负载验收。

一次成功位修复不安装自动GPT写服务，不扩大后续设备操作授权。实际修改与恢复备份详见 [成功标记记录](boot-success-repair-20261004.md)。
