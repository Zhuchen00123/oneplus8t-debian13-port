# OnePlus 8T 限充调查与适配方案（2026-10-04）

结论：Android 社区已有充电控制路径；当前采用的主线社区基线没有可直接使用的限充接口。MP2762A 数据手册提供停充位，可作为本项目补接口的依据，但本机硬件行为尚未验证，80%限充仍未实现。本轮只读研究，没有修改设备充电状态或部署新驱动。

## 社区与原厂证据

| 来源 | 固定版本与发现 | 适用限制 |
| --- | --- | --- |
| [LineageOS SM8250 common.mk](https://github.com/LineageOS/android_device_oneplus_sm8250-common/blob/d772af0971b01a6b4f9b0d265a9476df494ef280/common.mk#L209) | `d772af0971b01a6b4f9b0d265a9476df494ef280`，Lineage Health 使用 `/sys/class/oplus_chg/battery/mmi_charging_enable` | Android 厂商接口配置；不能证明当前 Debian 有该节点，不能直接安装 Android 限充工具解决驱动缺口 |
| [OnePlusOSS 充电实现](https://github.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8250/blob/0a301570ef70f6f9bfe1840451c9d41f5ddce6b8/vendor/oplus/kernel/charger/charger_ic/oplus_mp2650.c#L1318) / [位定义](https://github.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8250/blob/0a301570ef70f6f9bfe1840451c9d41f5ddce6b8/vendor/oplus/kernel/charger/charger_ic/oplus_mp2650.h#L198) | 已锁定的 OP8T Android 14 来源，`mp2650_enable_charging` / `mp2650_disable_charging` 修改 REG08 的 CHG_EN bit4 | 文件名是 MP2650；代码吻合可作参考，不能把同仓库所有驱动都当成本机已用，不能整套搬入主线 |
| [当前社区 MP2762A 补丁](https://github.com/jfbobier/Oneplus-8T-kebab-pmos-bringup/blob/2e5e065da8ea8d242ecc34bfe064b88257797aa9/kernel/0003-regulator-add-MP2762A-OTG-VBUS-regulator.patch) | `2e5e065da8ea8d242ecc34bfe064b88257797aa9`，只有 OTG regulator，明确不提供 charging power_supply | 本轮 API 核对 main 未变，完整树中未发现另一个 MP2762A 充电驱动；不等于证明全互联网都没有候选补丁 |
| [ACC 项目](https://github.com/VR-25/acc/blob/908a5a4b716b7f2506a2e1426429293a11f42ab3/README.md) | `908a5a4b716b7f2506a2e1426429293a11f42ab3`，通过现有内核控制参数管理充电 | 它是策略工具，仍需要可工作的底层开关；本项目没有安装或运行它 |

通过 gh API 下载固定提交内容核对，未执行上游脚本。OnePlusOSS 模块树 API 返回 `truncated=false`，驱动位于 `vendor/oplus/kernel/charger/`，不是内核仓库的 `drivers/power/oplus/`。原始源码与树、文件hash仅保存在忽略的 `local/charge-research-20261004/`。网页缓存旧目录不能代替固定提交内容。

## 硬件依据

依据 [MPS 官方 MP2762A 数据手册 Rev 1.1](https://www.monolithicpower.com/en/documentview/productdocument/index/version/2/document_type/Datasheet/lang/en/sku/MP2762AGV/document_id/9073/)（2023-07-14）：

- 第35页 REG08 bit4 `CHG_EN` 控制充电；bit3 `SUSP_EN` 控制 DC/DC。优先研究停充位，不能用 suspend 或 USB-PD online 代替。
- 同寄存器 bit7 是寄存器复位，bit6 是看门狗复位命令，bit5 是 OTG。更新充电位必须保留无关配置，并显式处理命令位；不能向整字节写固定常数。
- 第27–28、36页：I2C写入进入 host mode；启用的看门狗超时会恢复默认参数。实际 OTP 默认值与运行配置待本机读取，不能假定看门狗一定启用或关闭，也不为限充关闭安全保护。
- 第25页：充电使能从0到1会重启安全计时器；不能每分钟反复启停“刷新”限充。

数据手册停充时保留 DC/DC 的描述提供了保持外部供电的研究方向。**OnePlus 8T 整板上停充后是否继续稳定供电、是否仍有电池补电、USB是否掉线，必须实测；目前不能称为旁路供电已实现。**

## 最小适配路线

1. **先读后写。** 在匹配 r2 的独立 Linux 源码/构建目录制作诊断候选，从现有 I2C client 内读取已核对的配置寄存器；probe 不初始化充电器、不写寄存器。确认驱动绑定、芯片型号依据、REG08/REG09与外部供电状态。故障寄存器先核对读清除语义。不能用强制 `i2cset -f` 绕过已绑定驱动。
2. **同一驱动统一访问。** 扩展现有 MP2762A owner，共用互斥锁，暴露 power_supply `charge_behaviour` 的 auto/inhibit。不另建抢占同地址的第二个驱动。OTG与充电控制需互斥；未支持或不满足条件返回明确错误。
3. **受控开关。** 仅改变 CHG_EN，并读回确认；普通更新清除复位命令位。不改充电电压、电流、NTC、安全计时器、BATTFET独立开关、船运模式或OTP。看门狗如启用，由内核按已读取周期维护，不能依赖一分钟健康timer；失败/卸载/关机恢复策略须先验证。
4. **再加电量策略。** 用户已选80%上限，候选采用80%停充、75%恢复的滞回（75%为拟定值），只在状态转换时写入。输入需要新鲜且有效的电量/温度/外部供电数据；异常不得强行恢复充电。策略日志区分“请求”“读回”“物理效果”，接口或传感器失败明确显示未执行。
5. **分阶段验收。** 先做主机寄存器模型测试及匹配内核 W=1 构建，再准备有界实机实验和恢复程序。真实充电中验证停充前后电流、外部供电、温度、USB SSH及原状态恢复；超过实际看门狗周期仍保持。最后从80%以下跨阈值，验证75%恢复、拔插/重启、长时间与MC负载。

不能由当前100%且电流为0的样本证明停充成功。接口读回一致也不足以证明物理效果。未通过上述验收不启用开机限充，不写入正式镜像，也不把 `charge_limit_enforced` 改成true。

## 操作边界与备选

研究和主机构建可继续。实机充电开关会影响电池连接与USB供电；在硬件行为未确认时，按 AGENTS.md“危险、破坏性或影响范围不明确的操作必须先确认”，先完成可审查候选、保存原寄存器配置及恢复方案，再申请有界实机写入。现有 boot_a/dtbo_a/super 持续授权不自动扩展为未知充电寄存器写入。

若本机停充路径不能安全保留系统供电，备选是可控外置USB供电；它会让电池承担停电区间负载，不等同于旁路供电。当前没有购置或部署该设备。

本阶段交付是固定来源调查与适配路线；没有驱动实现、构建或实机停充结果。既有只读健康监控、热保护和校园认证会话保持原状态。当前状态另见 [服务器基础记录](server-base-20261004.md)。
