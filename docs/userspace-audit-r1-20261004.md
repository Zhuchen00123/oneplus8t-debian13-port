# KB2000 r1 用户空间盘点与补项

> 阶段记录提示：这是r1用户空间补齐阶段的盘点。当前已升级r2-charge-policy并有显示触摸、限充、存储和按键验收；硬件未测范围仍按各专题的后续证据判断。 最新入口见[当前状态](project-status-20261004.md)与[交接](handover-20261004.md)。

调查与实机验证：2026-10-04，Asia/Shanghai。设备继续持久运行 Debian 13 / `7.2.0-kebab-debian13-r1+`，A 槽、`/dev/sda15` ext4 可写。本轮经固定 USB SSH 维护，没有刷分区、切槽、配置 Wi-Fi 认证或触碰蜂窝/NV。

## 来源与历史状态

- 对照 [jfbobier 固定版本](https://github.com/jfbobier/Oneplus-8T-kebab-pmos-bringup/tree/2e5e065da8ea8d242ecc34bfe064b88257797aa9)，提交 `2e5e065da8ea8d242ecc34bfe064b88257797aa9`；来源锁定未变。社区 KB2003 / postmarketOS 成功记录不是 KB2000 / Debian 验收。没有执行社区刷机脚本。
- 旧准备/首启文档及协作规范中的“尚未启动 Linux”“USB DHCP 待测”“单次授权已耗尽”是历史阶段，应按 [10-03 持久部署记录](<persistent-r1-20261003.md>)、后续实测及持续授权理解，不能当成当前状态。
- 10-03 的“采样无 TCM invalid-length 洪泛”仅描述当时窗口：本轮较长运行日志已再次出现大量无效触摸报文，见下文。
- 原始设备身份、蓝牙地址、machine-id、完整日志及下载包只留在本地忽略目录，不发布。

## 三层验收矩阵

| 项目 | 文件/用户空间 | 驱动/枚举 | 本机功能与限制 |
|---|---|---|---|
| 固件 | r1 清单中 59 个本机 OEM 文件逐份 SHA-256 相同；无线/GPU Debian 固件包已有 | ath11k、Adreno/GMU、Bluetooth，ADSP/CDSP/SLPI 已出现 | 文件校验不是所有外设功能通过；未混入 KB2003 固件或 NV |
| 蓝牙 | BlueZ 5.82；新增有界地址初始化适配 | hci0 配置完成；正常重启后地址保持一致，地址服务约 4 秒成功退出 | 地址初始化通过；未扫描、配对或传输，bluetoothd 仍按服务器策略不默认启动 |
| GPU/Mesa | Mesa 25.0.7-2+deb13u1、EGL/GLES、Vulkan 工具齐备 | msm / Adreno 650、GMU 2.1.8、renderD128；FD650 / Turnip 枚举 | 普通用户 GLES 着色器绘制及像素回读通过，重启复验通过；不是 Vulkan 绘制、显示或长期负载验收 |
| 相机 | v4l-utils 1.30.1-1、libcamera-tools/IPA 0.4.0-7 已补齐 | CAMSS、IMX471、media0 和视频节点出现，libcamera 枚举前摄 | root 有界捕获仍在 DMA-heap 缓冲分配阶段失败，零帧；后摄未验收 |
| NFC | 内核模块具备；没有盲装不匹配的 libnfc 用户空间 | nfc0，nxp-nci_i2c 已绑定 | 未读卡、射频或写卡测试 |
| 传感器 | QRTR 工具已有，SLPI 固件就绪 | SLPI running；SSC service 400，node 9 / port 12 | 三个 IIO 为 PMIC ADC，不等于陀螺仪/加速度计；未获得运动传感器数据 |
| Wi-Fi | ath11k 固件、wpa_supplicant、签名 regdb 齐备 | wlan0 出现 | 未配置认证、DHCP/DNS 或互联网；不以文件或枚举代替联网 |
| 校时 | systemd-timesyncd 257.13-1~deb13u1 已安装并启用，保留标准 clock 种子 | NTP 服务 active | System clock synchronized=no；USB 不提供网关/DNS，RTC 1970，不能声称已联网校时 |
| 音频/蜂窝 | UCM/固件保留，原安全策略保持 | 不主动启用放大器、蜂窝/NV 服务 | 未播放、通话或蜂窝联网，不纳入本轮验收 |

## 蓝牙地址服务卡住：原因与修复

原社区 oneshot 服务停在 activating，btmgmt/grep 等待且 systemd 启动超时为无限。实测 `btmgmt info/config` 使用 systemd 的 `/dev/null` 标准输入时不执行命令；其内置超时也未解围，外部 timeout 返回 124。空管道标准输入可执行。这与 [BlueZ 5.82 shell 输入附加逻辑](https://github.com/bluez/bluez/blob/5.82/src/shared/shell.c) 相符，并非固件缺失或需要抄写原厂蓝牙 NV。

还有两个独立兼容点：

1. 未配置 public-address 的控制器不出现在普通 info 列表；BlueZ 5.82 的 indexed config 使用 `UINT_TO_PTR(index)` 而非 mgmt_index，回显错误索引。对照 [client/mgmt.c](https://github.com/bluez/bluez/blob/5.82/client/mgmt.c) 与实测，改用全局 info/config，再严格选择目标 hci 块。
2. Set Public Address 会异步移除/重建管理索引；立即查询空列表不能当成失败，应继续有界轮询。

[本项目 helper](<../scripts/linux/kebab-bluetooth-addr.sh>) 保留上游 machine-id + `-kebab-bt` 的 SHA-256 前五字节、02 前缀派生规则，已配置地址直接保留，只在缺项恰为 public-address 时设置。每次 btmgmt 外部超时 5 秒、kill 宽限 1 秒，单调时钟轮询窗口 60 秒；[systemd drop-in](<../scripts/linux/kebab-bluetooth-addr.conf>) 最终限制 90 秒。未读取或写入 Qualcomm NV。固定社区原件未覆盖，适配安装于 `/usr/local/libexec` 和 `/etc/systemd/system`。

正常重启后严格 SSH 重连、boot ID 改变、内核/根 UUID 不变；地址初始化约 4 秒完成，active(exited)、Result=success、ExecMainStatus=0，地址与重启前一致。这只验收初始化，不验收蓝牙无线功能。

## GPU 与相机实测

[无显示 GPU 测试](<../scripts/linux/test-gpu-headless.py>) 创建 64×64 surfaceless EGL pbuffer，编译/链接 GLES 着色器、绘制红三角、glFinish 后 glReadPixels。普通 debian 用户（video/render 组）实测 FD650：中心 `(255,0,0,255)`、角点 `(0,0,0,255)`、glGetError=0；重启后再通过。拒绝软件回退作为成功。vulkaninfo 枚举 Turnip Adreno 650 与 llvmpipe，仅为 Vulkan 枚举。eglinfo 遍历无 X/Wayland 后端的退出 2 不能抹去 GBM/surfaceless 成功，也不能当作所有后端成功。

相机初次缺 IPA 已补齐。普通用户因 DMA heap 权限不足失败；未放宽设备节点权限。随后 root 用 timeout 限制 35 秒尝试三帧：请求 raw 1928×1088，实际被调整为 1920×1088-ABGR8888。日志仍有 crop ioctl Invalid argument、缺少 IMX471 属性/校准/helper，回退 uncalibrated.yaml，最终 `dma-heap allocation failure for frame-3`，无法分配缓冲，退出 1、无图像文件。CMA 32 MiB 是后续调查线索，尚不能证明是唯一根因；没有改 CMA、DT 或内核参数。

## 安全补项和复现边界

- 从主机隔离的 arm64 Debian trixie / updates / security 签名 APT 索引准备离线包；核对下载包 SHA-256 后传入手机。新增共 140 包，无升级或删除，dpkg --audit 干净。libcamera/工具间接依赖 Qt/X 库不等于启动桌面。
- 首次离线安装退出 100：APT 仍选择仓库 URI，而非已下载文件；检查 --print-uris 后将归档放入 APT cache，重试成功。未关闭签名校验或假称手机已联网。
- 原 regulatory.db alternatives 自动选 Debian 签名数据库，社区内核只信任上游 sforshee/wens 证书。切换为 upstream 数据库及其配对 p7s 从属链接；正常重启后旧签名错误不再出现。没有禁用签名、改变国家码或射频限制。
- [rootfs 构建配方](<../scripts/linux/prepare-server-rootfs.sh>) 和 [集成程序](<../scripts/linux/integrate-server.py>) 已固化所需工具、video/render 组、regdb 配对选择、timesyncd 与蓝牙适配，并记录适配 SHA-256。构建脚本只操作主机。
- 配方通过 shell 语法与 Python AST 检查；[主机蓝牙回归](<../scripts/linux/test-bluetooth-addr.py>) 的 7 项 mock 用例通过，覆盖保留已配置地址、目标块选择、异步重枚举、拒绝其他缺项/非法身份/非法设备、控制器拒绝与有界等待。测试不接触硬件/NV；最初测试文件生成有字符串转义语法错误，修正后重跑通过。未重新完整构建或打包 boot/rootfs；[r1 封存组](<../artifacts/server-r1-20261003/ready/server-manifest.json>) 保持不变，不能假称它包含这次运行后补项。
- 重启后 /tmp 脚本已消失，首轮复测退出 127；重新上传脚本与测试输入后完成复验，没有忽略失败。

## 尚未验收与下一步

较长运行日志再次出现大量 `invalid touch report length 14/21`；新重启短窗口没有复现不代表问题解决。两条 DSI 时钟启动 WARNING 仍在。本轮未继续改 TCM；触摸事件、充电、音频、蜂窝/NV、长时稳定性仍待验收。优先独立调查触摸报文协议与相机缓冲路径；Wi-Fi 认证由用户稍后提供后再做联网/NTP实测。NFC 和 SSC 需要匹配 NCI / SSC 的用户空间及真实功能证据，不能只根据设备节点宣布支持。

## 隐私与备份

原始日志、包清单、失败记录、设备身份只保存在本机忽略目录。运行后配置归档 [私有 overlay](<../local/audit-r1-20261004/kebab-runtime-overlay-final-20261004.tar.gz>) 已核对设备端/主机端 SHA-256：`ad46d3ab5e883f4e27646cafc6019486d7ccb03158eb2e44d16ee64b375acc1d`。含 /etc、/usr/local/libexec、clock、dpkg 状态、regdb 链接及回滚配置；含密钥，不能上传。它不是完整 rootfs 或跨文件原子快照；恢复 dpkg 状态不等于安装依赖，不能把该 /etc 复制成另一设备身份。包归档另存本地。

本阶段项目 Git bundle 和工作树 ZIP 校验记录位于 [备份清单](<../backups/project-userspace-r1-20261004/manifest.json>)。同盘备份仍需用户复制到外部介质；原 r1 镜像与原厂恢复组未被替换。
