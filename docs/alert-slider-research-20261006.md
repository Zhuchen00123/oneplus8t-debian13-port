# OnePlus 8T 三段键可行性核查（2026-10-06）

最终内建状态：用户授权后已部署 r3 I²C 设备树内建驱动并正常重启，初始中档、三档标准事件和用户真实界面／触摸通过。三段键服务仅做健康检查，无外置 insmod；详见[内建验收](slider-builtin-20261006.md)。下方外置模块记录是开发及回退阶段。

后续冷启动修复：手机非主动重启后原外置模块因 RPMh 启用缓存为未知（-EINVAL）而报 busy。常驻路径改为正常引用计数供电，新模块 3feede28…4abfa 已部署并持续正常；旧模块备份保留，未再次重启验收。独立内建候选正在准备，详见[故障与恢复](desktop-apps-recovery-20261006.md)。下方 cf497b… 是修复前模块，不能作为今天的安装哈希。

Git发布：用户授权后，本地实施提交`c52b659`，公开脱敏分支[codex/alert-slider-20261006](https://github.com/Zhuchen00123/oneplus8t-debian13-port/tree/codex/alert-slider-20261006)提交`d6fac93`已推送并回读。公开版不含本机校准、模块二进制或原始证据，包装脚本改为从本地校准JSON读取；main未合并。详见[发布流程](git-workflow.md)。

## 10/06 已部署的三段键识别

用户随后要求“开始着手”，并实际配合最上／中／最下档采样及两轮拨动。已确认本机为一对 IST8801，地址 `0x18/0x19`、ID 均为 `0x81`。当前已部署独立 `kebab-alert-slider.service`，**enabled/active**，普通 Linux 输入设备名为 `OnePlus 8T Alert Slider`；没有绑定静音、亮灭屏、关机、充电或网络动作。

| 物理档位 | 下传感器 | 上传感器 | `EV_ABS / ABS_SND_PROFILE` |
| --- | --- | --- | --- |
| 最上 | 55 | 148–149 | 0（silent profile） |
| 中间 | 92–93 | 102–103 | 1（vibrate profile） |
| 最下 | 147 | 55 | 2（ring profile） |

原厂供电标签 `pm8150a_l10` 的 **a 是厂商辅助 PMIC 命名**，不能按字母直接对应主线 PMIC a。官方 `kona-regulators.dtsi` 的资源名为 `ldoc10`，实际对应主线 `vreg_l10c_3p0`（PM8150L，3.0V），不是 `vreg_l10a_1p8`。初始无供电时四个候选地址均无应答；有界启用 L10C 后仅 `0x18/0x19` 返回预期 ID。没有对错误的 L10A 电源做实机写入，没有改变电压。

### 实现与验证

- [驱动](../scripts/linux/kebab-alert-slider.c)是适配当前内核的板级外置模块。核验 `oneplus,kebab`、控制器 `0x984000`、两个芯片 ID、L10C 当前 3.0V、ADC/增益配置 `CNTL2=0x28`；地址由内核 I²C client 保留，已经占用时拒绝加载。
- 传感器工作在20Hz测量、驱动每100ms读取两颗的状态与有符号值。以本机三档二维校准窗口判断，轴向容差12，连续三份有效样本才更新输入事件；窗口外、读失败和无新数据打断去抖，不拿旧数据重新判档。初始档位先取得稳定读数，再注册输入设备，避免默认静音档。
- 此方案目前采用轮询；未接管原厂 GPIO157/GPIO138 中断，也未修改启动设备树。不是已经合入官方主线的驱动。后续可改成标准 OF I²C 描述与中断方式，当前无需替换 boot 或内核。
- 保存并读回恢复 `INTSRS/OPF/ACTION/IFCNTL`；不写软件复位、OTP、测试寄存器或增益。限时诊断与服务停止均释放供电，实机 L10C use count 恢复为0，`restore_error=0`。
- 标准事件候选在用户真实拨动中录到 `[0,1,2,1,0,1,2]`（经过中档时的事件也正常），三档齐全；之后约两分钟保持最下档未出现额外档位事件。
- 共享判档代码经 GCC `-Wall -Wextra -Werror`、ASan/UBSan 检查，262,996个二维分类点、越界／校准重叠／去抖及100,000次稳定重复检查通过；匹配内核的模块 `W=1` 构建无警告。
- 最终模块10秒自动到期停止、寄存器读回恢复、释放供电与卸载通过。常驻服务 start→stop→start通过，最后稳定读为2，采样更新且error=0；原限充、健康timer、电源键、状态面板、酒馆仍active。前述真实拨动事件来自同一判档代码的有界候选；最终常驻模式已验证初始档位和服务恢复，但未另做一轮用户拨动。

外置诊断模块加载使内核 taint O/E（外置／未签名），卸载不会清除 taint。没有重启手机、刷分区、改内核或既有基础服务；没有测量三段键供电功耗，也未验收长期热稳定性、强磁干扰、温漂或整机重启后自启。校准限定这台 KB2000，不能直接用于其它手机。

### 运行入口与回退

```sh
sudo kebab-alert-slider status
sudo systemctl stop kebab-alert-slider
sudo systemctl start kebab-alert-slider
# 停用并取消自启；停止会恢复寄存器并卸载独立模块
sudo systemctl disable --now kebab-alert-slider
```

`kebab-alert-slider.service`使用 oneshot/RemainAfterExit；active说明启动完成，不代替持续采样健康。应同时查看status中的`active/count/age_ms/error/profile`，其中`profile=-1`表示当前读数不可判档；`reported`是最近一次确认并上报的档位。停止和卸载后输入设备消失。

安装文件：匹配模块树的 `extra/kebab-alert-slider.ko`、`/usr/local/sbin/kebab-alert-slider`、`/etc/systemd/system/kebab-alert-slider.service`、`/etc/kebab/alert-slider-module.sha256`。启动检查内核精确版本、机型及模块哈希；升级内核必须重新构建，不复制同名旧模块。没有写通用modules-load配置，亦不覆盖其它extra模块。

最终模块SHA-256：`cf497b8b6edccc5f5aed2d82dc1afa09a3209faca17f475523aeb37062fc1f55`。私有部署包、原始采样与构建证据在 `local/alert-slider-20261006/`；手机部署前输入／电源／服务基线在受限 `/var/backups/kebab-alert-slider-20261006-143032/`。这不是整机或应用一致性快照。

下面保留最初只读调查，语境为实施前；其中“目前／未执行”不覆盖以上部署结果。

## 实施前调查（历史）

## 结论

右上角三段键具备移植到当前社区主线 Linux 的条件，但本机目前没有接入，不能宣称已经可用。缺口是霍尔传感器驱动、设备树及档位判定；不是安装一个桌面快捷键工具就能启用。

本次按用户要求阅读项目状态、交接、酒馆迁移及性能记录，并经既有严格 USB SSH 只读核查手机。没有加载候选模块、访问未知 I²C 寄存器、改变供电或 GPIO，也没有改手机配置、刷分区或重启。项目已有面板网络页改动保持原状。

## 本机现状

- 实读内核为 `7.2.0-kebab-debian13-r2-charge-policy+`。
- `/proc/bus/input/devices` 只有 Synaptics 触摸、gpio-keys 音量键、pm8941_pwrkey 和 pm8941_resin，没有三段键；这些设备均未声明三段键所需的绝对轴能力。
- 运行设备树按 hall / slider / tri-state 名称检索没有匹配，`/sys/class/extcon` 没有设备。设备名检索本身不能证明硬件不存在；结合固定社区 DTS 与驱动源码，可以判断当前未集成。
- 固定 bring-up DTS 的 gpio-keys 只包含音量增减。其 `&i2c1` 只有地址 `0x28` 的 NFC。
- 手机已启用 I²C 控制器 `0x984000`，实读对应 `i2c-1`，目前没有实例化三段键传感器。

原始只读输出与官方源码缓存保存在 Git 忽略的 `local/alert-slider-20261006/`。没有用历史记录代替这次运行核查。

## 原厂提供的接入线索

来源固定在项目原有 `sources.lock.json`：设备树仓库提交 `0a301570ef70f6f9bfe1840451c9d41f5ddce6b8`，厂商内核提交 `846e71f228ad605c75b53064c4c165a1361a0bfe`。源码下载未执行其中的代码。

[原厂 20828 板级描述](https://github.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8250/blob/0a301570ef70f6f9bfe1840451c9d41f5ddce6b8/vendor/qcom/proprietary/devicetree-4.19/20828/kona-mtp.dtsi#L1604)同时声明两种替代配置：

| 配置 | 上传感器 | 下传感器 | 原厂供电与中断 |
| --- | --- | --- | --- |
| IST8801 | `0x19` | `0x18` | `pm8150a_l10`；TLMM GPIO157 / GPIO138 |
| MXM1120 | `0x0d` | `0x0c` | 同上 |

两组都在 `qupv3_se1_i2c`；[原厂总线定义](https://github.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8250/blob/0a301570ef70f6f9bfe1840451c9d41f5ddce6b8/vendor/qcom/proprietary/devicetree-4.19/20828/kona-qupv3.dtsi#L128)把它映射到 `0x984000`，与当前社区主线 `&i2c1` 对应。

这些是板级源码声明，**还没有实读本机芯片 ID，不能确定 KB2000 装的是哪种传感器**。同一文件列出两个供应商，不表示本机同时装有四颗芯片。不能把某种型号或某一组地址直接写成已验收硬件。

[官方驱动目录](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/tree/846e71f228ad605c75b53064c4c165a1361a0bfe/drivers/misc/tri_state_key)包含 IST8801、MXM1120 的读写、供电、测量及中断代码，还有统一的 `oplus_tri_key.c` 档位判定、校准和磁干扰处理。原厂 GPIO 是中断线，并非三档各自独立的开关触点；不能仅用 GPIO 电平推断所有档位。

## 主线接口与缺口

当前固定 SM8250 7.2 源码中没有 IST8801 / MXM1120 / oplus tri-state 驱动，固定 bring-up 补丁集也没有添加它们。这一结论限定于本项目核查的源码，不声称所有社区分支都没有相关工作。

Linux 已定义 `EV_ABS / ABS_SND_PROFILE`，三种值为 `SND_PROFILE_SILENT=0`、`VIBRATE=1`、`RING=2`。当前源码中的一加 6/6T DTS 已采用此接口，但它们使用 GPIO 接法，不能直接复制到 8T。

[原厂统一驱动](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/846e71f228ad605c75b53064c4c165a1361a0bfe/drivers/misc/tri_state_key/oplus_tri_key.c#L730)通过 `KEY_F3` 上报厂商档位编码，不能原样作为标准三段键协议。移植应读取两颗传感器并判定档位，再上报标准 `ABS_SND_PROFILE`；策略由用户空间决定。输入事件可用于静音、显示模式或其它三个状态，但仅有事件不会自动实现音频静音或振动。

## 推荐实施顺序

1. 确认供电、引脚与总线当前占用，在指定候选地址读取芯片 ID，确定真实型号；不做整条总线盲扫。若供电未开启，先单独设计可恢复的有界实验，不能把无响应当作无硬件。
2. 在独立候选中实现最小传感器读取与标准输入事件，补设备树供电、引脚和中断描述。适配当前 Linux API，保留厂商来源和许可；先验证原始数据，再接入校准、去抖及干扰处理。
3. 协调用户实际拨动上／中／下档，验证读数、事件、静止无抖动、快速切换和初始档位；同时检查 NFC、触摸、USB SSH 与原有服务连续性。
4. 临时候选通过后再准备匹配的持久内核、DTB、模块、initramfs 与回退组。开机初始档位、重启、长期稳定性分别验收，不用短时成功代替。
5. 最后按用户选定用途接入服务或面板；不预先把三段键绑定充电、关机或网络动作。

本次完成的是可行性调查。芯片识别、实际拨动、候选构建和部署均未执行。
