# OnePlus 8T 三段键识别（2026-10-06）

本机KB2000已实读确认两颗IST8801，用户三档采样和两轮真实拨动得到标准`EV_ABS / ABS_SND_PROFILE`事件：最上0、中间1、最下2。独立服务已运行并配置开机自启；当前只发布输入档位，没有绑定静音、屏幕、关机、充电或网络动作。

本分支发布源码、合成测试和安装模板；不包含可用模块二进制、本机校准、运行日志或原始交接。公开启动模板从本地JSON读取校准，测试使用合成中心值，因此与本机固定校准的私有包装脚本有所区别。

## 硬件与实现

原厂[20828板级设备树](https://github.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8250/blob/0a301570ef70f6f9bfe1840451c9d41f5ddce6b8/vendor/qcom/proprietary/devicetree-4.19/20828/kona-mtp.dtsi#L1604)同时列出IST8801与MXM1120替代配置。本机供电后仅IST8801的`0x18/0x19`地址返回ID `0x81`；不能据此把所有8T都视为相同传感器。

原厂`pm8150a_l10`对应资源`ldoc10`，主线为PM8150L的`vreg_l10c_3p0`、3.0V。不要误用主线L10A。控制器`0x984000`对应运行中的i2c-1；现有NFC地址不被接管。

`kebab-alert-slider.c`是针对这台机型和运行内核的外置板级模块，尚未改成标准OF I²C绑定，也未合入官方主线。核验机型、总线、供电、两颗ID和`CNTL2=0x28`，由I²C client保留两个地址。每100ms读取20Hz测量数据；用本机自行采集的二维三档校准窗口、容差12和连续三次有效样本去抖。磁场读数落在窗口外或传输失败时保持最近确认的输入值，status标记当前不可判档；不能把旧值当作新确认。

不写电压、GPIO、软件复位、OTP或增益。初始化保存`INTSRS/OPF/ACTION/IFCNTL`，停止时写回并读回核验，再释放供电。诊断模式10–600秒自动停止；常驻模式须显式`persistent=1 seconds=0`并提供有效校准。只在取得稳定初始位置后注册输入设备，避免默认静音档。

## 构建与校准

必须已有完整、与实际运行内核精确匹配的Linux源码、输出目录、`.config`和`Module.symvers`；通用r2构建不能替代本机`7.2.0-kebab-debian13-r2-charge-policy+`构建。本公开分支不提供完整充电内核组。使用Linux大小写敏感文件系统：

```sh
# 在独立模块目录放入kebab-alert-slider.c和kebab-alert-slider-state.h
printf '%s\n' 'obj-m += kebab-alert-slider.o' > Makefile
make -C "$KERNEL_SOURCE" O="$KERNEL_OUTPUT" M="$PWD" ARCH=arm64 LLVM=1 W=1 modules
# 在scripts/linux目录执行共享判档的合成测试
gcc -Wall -Wextra -Werror -fsanitize=address,undefined test-kebab-alert-slider-state.c -o /tmp/slider-state-test
/tmp/slider-state-test
```

先核对供电、驱动占用、ID和可恢复实验，使用有界模式采集上、中、下档的稳定原始数据。`kebab-alert-slider-probe.py`仅访问原厂候选地址的ID寄存器；无供电时无应答不表示无芯片。供电诊断模块30秒自动释放当前3.0V电源，不改变电压。它们会访问执行机器的硬件，仅在拥有设备授权并匹配前置条件时使用。

在目标手机的本地暂存目录自行创建`alert-slider-calibration.json`：六个有符号整数，按最上档的`[down,up]`、中档、最下档排列。不得使用测试中的合成值作为实机配置，不提交该文件。本机校准只适用于该设备、ADC配置和实测环境；不满足分离条件或初始数据落在窗口外时模块拒绝注册。

## 安装、验收与回退

将自行构建的`kebab-alert-slider.ko`、`kebab-alert-slider.sh`、`kebab-alert-slider.service`、`install-kebab-alert-slider.sh`和本地校准JSON放到独立暂存目录；生成覆盖输入文件的SHA256SUMS后，以root执行安装脚本。要求既有Debian arm64、匹配的精确内核以及原基础服务；存在目标文件时拒绝覆盖。安装完成仍保持禁用，先进行有界和服务启停验收，再选择启用：

```sh
sudo systemctl start kebab-alert-slider
sudo kebab-alert-slider status
sudo systemctl stop kebab-alert-slider
# 真实三档、稳定初始位置和停止恢复通过后
sudo systemctl enable --now kebab-alert-slider
# 回退：取消自启，恢复寄存器并卸载独立模块
sudo systemctl disable --now kebab-alert-slider
```

`test-kebab-alert-slider-events.py`只读采集实际evdev事件。本机有界候选两轮拨动记录三档齐全；262,996个分类点、异常边界和去抖测试通过，模块W=1构建无警告。最终模块10秒自动到期、寄存器读回恢复、供电释放和服务start→stop→start通过；未改引导镜像或重启手机，原有服务保持运行。

本机常驻包装脚本已启用，整机重启后自启、长期稳定性、温漂、外部磁干扰和供电功耗未验收。外置未签名模块使内核taint O/E；卸载不能清除taint。轮询未接管原厂中断线，后续可改为设备树与中断驱动。oneshot服务active只表示启动成功，需同时查看status中的样本更新、数据年龄和错误。
