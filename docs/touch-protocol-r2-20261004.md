# KB2000 TCM 触摸协议 r2 候选（2026-10-04）

> 阶段记录提示：这是触摸协议调查、候选模块及早期内建尝试的证据。后续r2已成组内建并通过真实单指/双指/抬起及重启测试，不再依赖r1模块接管；长期误触仍待验收。 最新入口见[当前状态](project-status-20261004.md)与[交接](handover-20261004.md)。

## 状态与边界

**根因已取证，真实触摸已在实机验收通过，并以"开机自动接管"的模块方式持久部署、重启后复验通过。** 手机运行 Debian 13 / `7.2.0-kebab-debian13-r1+`（`#4`，含黑屏修复），A 槽，`boot_a` 仍为黑屏修复镜像 `ccacb17a…c2b3dd`。

解析修正仍是[补丁 0002](<../patches/local/0002-input-synaptics-tcm-runtime-layout-candidate.patch>)，本阶段把它编译为模块 `tcm_r2_test.ko`（SHA-256 `2a7d0e726638ac3f418285cf2206f6db764703f6cf97e92691d4d52f74cd0b2f`）安装到 `/lib/modules/7.2.0-kebab-debian13-r1+/extra/`，由新增的 `kebab-touch-fix.service` 开机时 `modprobe`，再把 builtin `synaptics-tcm` 从 `13-004b` 解绑、把修正驱动绑上去；本轮没有刷任何分区、没有写触摸固件或 NV。

**内建（编入内核）路线的尝试与阻塞**：同一补丁编入内核得到提交 `32d0dc7c445a…`、引导镜像 `d6151db1…`（45,617,152 字节；仅内核段比可用镜像大 2,328 字节，其余头字段、偏移、总长一致；`gzip -t` 通过且解压 46,465,536 字节与 `Image` 完全一致）。写入 `boot_a` 后 ABL 报"系统文件被损坏，无法开机 / The current image(boot/recovery) have been destroyed"。相同 dd 写法此前两次（`5bfa275b…`、`ccacb17a…`）均可启动，**拒绝原因未查明**；已用 fastboot 刷回 `ccacb17a…` 恢复正常。因此本轮把触摸修复以模块方式落地，内核内建部署暂缓。

本阶段没有刷分区、切槽、清空数据、写触摸固件、修改持久触摸配置或 NV。临时模块测试后卸载并恢复 builtin `synaptics-tcm`；加载未签名外部模块产生 O/E taint，恢复绑定不会清除 taint，正常重启才清除本次加载标记。原封存 r1 boot/rootfs 不变。

## 本机协议证据

只读查询使用 GET_APPLICATION_INFO `0x20` 和 GET_TOUCH_REPORT_CONFIG `0x25`。完整证据保留在私有[查询日志](<../local/touch-r2-20261004/query-audit.log>)。应用信息给出 X 最大值 1079、Y 最大值 2399、最多 10 个对象。配置响应头 `a5 01 80 00` 表示 128 字节配置，前部为：

```text
10 08 c6 30 01 06 04 07 04 08 0c 09 0c 0b 0c 0c 0c 03 00
```

其后为零填充。配置字节码和触摸数据不能混为一种固定结构：

| 部分 | 配置 | 数据位数 |
|---|---|---|
| 手势前缀 | `10 08 c6 30` | 8 + 48 = 56（7 字节） |
| 活跃对象循环 | `01 ... 03` | 控制操作，无位宽参数 |
| 对象编号 / 分类 | `06 04 07 04` | 4 + 4 |
| X / Y | `08 0c 09 0c` | 12 + 12 |
| WX / WY | `0b 0c 0c 0c` | 12 + 12 |
| END | `00` | 无位宽参数 |

数据按 LSB-first 解码，每对象 56 位。因此 **14 / 21 字节 payload 分别是合法的一 / 二对象报文**，不是固件损坏证据。旧代码假定 1 字节前缀和 11 字节对象，与本机运行配置不匹配；活跃对象计数操作码还误用 23（厂商定义为 24）。

命令格式为 `[opcode, payload_length_le16, payload]`：无参数查询须发三字节；启停触摸报告须发 `05/06 01 00 11`。参考固定提交 `0a301570ef70f6f9bfe1840451c9d41f5ddce6b8` 的 [OnePlusOSS 厂商源码](https://raw.githubusercontent.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8250/0a301570ef70f6f9bfe1840451c9d41f5ddce6b8/vendor/oplus/kernel/touchpanel/oplus_touchscreen/Synaptics/Syna_tcm_oncell/synaptics_tcm_oncell.c)及[操作码头文件](https://raw.githubusercontent.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8250/0a301570ef70f6f9bfe1840451c9d41f5ddce6b8/vendor/oplus/kernel/touchpanel/oplus_touchscreen/Synaptics/Syna_tcm_oncell/synaptics_tcm_oncell.h)，不是移植厂商完整驱动。

## 命令通道修正

IRQ-only 启停测试曾返回 `-ETIMEDOUT`；后续读取发现零 payload 的成功 ACK `a5 01 00 00 5a` 已排队。因此本机报告启停 ACK 不能可靠依赖 IRQ 通知。证据见[失败/轮询日志](<../local/touch-r2-20261004/r2-timeout-poll.log>)，并非通过忽略超时判定成功。

候选以 mutex 串行化命令，spinlock 保护响应状态；只允许终结状态完成命令，触摸/异步报告不作为 ACK。报告启停在发送前禁止并同步 IRQ，独占读取，以 10–12 ms 间隔在 1 秒内等待真实响应，不重发命令，所有退出路径恢复 IRQ；查询仍通过 IRQ 等待。超时或传输失败标记通道失效，须重新 probe，避免无事务编号的旧响应被后续命令误认。

## 解析与输入安全

读取并编译实际报告配置，不发 SET_REPORT_CONFIG 或 RUN_APPLICATION。配置/报文解析限定一个非嵌套对象循环；校验必需字段、位宽、对象数、编号、重复编号、完整长度。全帧通过解析和坐标范围校验后才上报事件；手指/手套分类有效，`INPUT_MT_DROP_UNUSED` 配合帧同步释放缺失 slot。宽度和压力仅在配置包含相应字段时公布。失败输出保持原样，拒绝半帧产生输入状态。

这不是通用 TCM 实现：固定 256 字节接收，含四字节头和 `5a` padding 时 payload 最大 251 字节；不组装 continued-read 多段报文，不支持嵌套循环或有歧义的未计数循环尾部。未知/不支持的布局不得静默套用旧结构。

## 验证矩阵

| 检查 | 结果与限制 |
|---|---|
| 原 r1 基线 `git apply --check` | 通过；未改原内核仓库 |
| 实际共享解析头的 GCC ASan/UBSan | 通过固定用例、20,000 个合法随机帧和 100,000 个变异；不是实机事件 |
| ARM64 匹配临时模块 `W=1` | 编译通过，无警告，vermagic 与运行内核一致 |
| 本机查询 / probe | 通过；得到 10 个 slot、56 位对象、无压力字段 |
| 最终三轮 2 秒 open/close | 全部通过；六次启停 ACK 均成功，约 17–20 ms |
| 较早三轮 60 秒采集 | 控制路径通过，但每轮事件数为 0；未确认用户实际触摸，不能验收 |
| 模块卸载 / 原绑定恢复 | 最终短测 `RESTORED_BIND synaptics-tcm CLEANUP_ERRORS []` |
| 物理点按 / 滑动 / 双指 / 抬起 | **尚未通过** |
| 持久 r2 内核与重启 | 未构建部署；仍为 r1 |

最终实机证据：[短测日志](<../local/touch-r2-20261004/r2-final-smoke.log>)；零事件长测：[采集日志](<../local/touch-r2-20261004/r2-physical-touch.log>)。短测采样未见 Oops/panic 或候选驱动错误，不能替代长期稳定性验证。

### 锁定输入（SHA-256）

- 候选补丁：`e3c5368e8a073c0247c4589d2d1671a376be2b0593fbe03c2a33fc348ac67f86`。
- 候选驱动输出：`4b3431a82fc977c40edfe8ebe0d526b2c4c9486cf30cdd563340029e52e40763`。
- 共享解析头：`41038df820619fc05a11b35f9cc79afebe7fffd9098ffef9b94cea4fbb9de8d7`。
- 最终带命令取证打印的[临时模块](<../local/touch-r2-20261004/tcm_r2_test.ko>)：`2a7d0e726638ac3f418285cf2206f6db764703f6cf97e92691d4d52f74cd0b2f`。模块只替换名称及增加取证打印，候选生产驱动不含这些打印。

[锁定清单](<../sources.lock.json>)使用独立 `candidate_kernel_patches`，保持 `local_kernel_patches` 的生产 r1 内容不变。

### 主机回归复现

在独立 r1 候选工作树应用补丁后，以其实际解析头编译[回归程序](<../scripts/linux/test-tcm-protocol.c>)，不要直接修改或切换当前生产内核仓库：

```sh
git apply --check /mnt/f/codexprojects/oneplus8t/patches/local/0002-input-synaptics-tcm-runtime-layout-candidate.patch
git apply /mnt/f/codexprojects/oneplus8t/patches/local/0002-input-synaptics-tcm-runtime-layout-candidate.patch
gcc -Wall -Wextra -Werror -fsanitize=address,undefined -g \
  -I drivers/input/touchscreen \
  /mnt/f/codexprojects/oneplus8t/scripts/linux/test-tcm-protocol.c -o /tmp/test-tcm-r2
/tmp/test-tcm-r2
```

## 后续验收

### 已完成（2026-10-04 实机）

- 有界采集三轮（每轮 45 s），在用户实际触摸下第 0 轮录得 **5,209** 个事件、第 1 轮 40 个，收尾 `RESTORED_BIND synaptics-tcm CLEANUP_ERRORS []`；事件含 `ABS_MT_TRACKING_ID 0`、`ABS_MT_POSITION_X/Y`、`ABS_MT_TOUCH_MAJOR/MINOR`、`BTN_TOUCH 1/0`、`EV_SYN`，抬起有 `ABS_MT_TRACKING_ID -1`。
- 部署后再次采集（150 s）录得**两个并存触点**（tracking 372/373），即多指同时上报。
- **用户实机确认"触控能用了"**：桌面 `kebab-xfce-test.service` active、Xorg 运行、`DSI-1 1080x2400@120Hz`，点开始菜单、拖窗口有反应；**重启后再次确认"仍然能用"**。
- 开机自动接管已核验：`journalctl -u kebab-touch-fix.service` 显示 `bound to tcm-r2-test`，`systemctl is-enabled` = enabled，重启后 `13-004b` 绑定为 `tcm-r2-test`。

### 仍需注意

- 日志里 `invalid configured touch report: length 14/21/28/35 error -22` 与 `failed to receive report: -71` 仍在刷屏；**观察证明它与可用性无关**（同一批报错在"可用"和"不可用"两种状态下都出现），输入走的是另一条已修正的解析路径。
- 触摸精度、边缘区域、三指以上手势、掌拒、长时间稳定性未系统测量。
- 内建部署被 ABL 拒绝所阻，模块接管需要 `kebab-touch-fix.service`；若后续要回到内核内建，先查清 ABL 拒绝原因。
- 与本修复无关的遗留项不变：fbdev 缺 `FBINFO_VIRTFB` 的虚警、`vreg_l14a_1p8` 未申请、只暴露 120 Hz、启动期 DSI PLL probe 报错。

本阶段项目快照入口为 [Git bundle](<../backups/project-touch-r2-candidate-20261004/project.bundle>)及[校验清单](<../backups/project-touch-r2-candidate-20261004/manifest.json>)，私有证据另存本地[归档校验清单](<../backups/touch-r2-private-evidence-20261004/manifest.json>)。它们是候选源码与调查证据备份，不是可启动 rootfs 或新的内核产物组；同盘副本仍需复制到外部介质。
