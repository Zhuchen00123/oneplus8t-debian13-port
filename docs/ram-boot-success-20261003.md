# KB2000 Linux 7.2 RAM 启动与原厂恢复

日期：2026-10-03。首次在本机验证主线救援环境启动、USB ACM、USB SSH 和 UFS 枚举。**持久 Debian 根文件系统尚未部署，Wi-Fi/固件/服务器负载尚未验收。**

## 已执行与实际证据

用户已单独授权一次 dtbo_a 对照及原厂 dtbo_a 恢复，并确认实体按键进入 Fastboot 可用。执行前实读 kona、A 槽、unlocked=yes、is-userspace=no、dtbo_a 容量 0x1800000（24 MiB）、max-download-size=805306368；三个输入重新大小/hash 核验通过。

唯一试验写入是社区原始无操作 DTB 到 dtbo_a：284 字节，SHA-256 `08f1551f1dd69a5799d3440c0dff7c183cbadad0764dd0ef7a21a1757661077f`。Fastboot Sending/Writing 均 OKAY。随后临时启动同一封存救援 boot：18,751,488 字节，SHA-256 `f339e7d993c8c4d3bf6527e706c4219c0d6da7d07bd569331cba54def057260f`，Sending/Booting OKAY。

此次与原厂 DTBO 的首轮结果不同：Windows 枚举 Linux USB 复合设备、ACM 和 RNDIS；ACM 远程 shell 实读 `7.2.0-kebab-debian13+`、OnePlus 8T DT 模型和 UFS。随后 USB SSH 正确私钥登录、严格校验 host key，远程只读命令退出码 0 并完成标记。

| 项目 | 实机结果 |
|---|---|
| 内核 | uname 为 `7.2.0-kebab-debian13+` |
| 设备树 | model 为 OnePlus 8T |
| ACM | 串口 shell 可执行只读命令；USB 重新绑定后再次可用 |
| USB SSH | 公钥认证成功；host key 从同一救援 ACM 获取，使用本轮私有 known_hosts，未修改全局文件 |
| UFS | /proc/partitions 与 sysfs 可枚举；PARTNAME=super 对应 sda15 |
| super 容量 | sysfs size × 512 = 7,516,192,768 字节（7 GiB） |
| 存储边界 | /proc/mounts 未发现手机块设备挂载；没有分区内容 I/O 测试或根文件系统部署 |

同一 boot 在原厂 DTBO 下未进入救援环境，在社区原始 DTB 下成功。这支持 DTBO/ABL 兼容路径为实际启动条件；本机 ABL 的具体条目选择和内部失败位置仍无日志证明，不能将公开参考源码当成本机实现。

## Windows USB 的实际修正

原封存镜像的 RNDIS function 默认 class/subclass/protocol 为 02/06/00。Windows 报 code 28，ACM 正常。在手机 RAM configfs 中改为 **EF/04/01**，Windows 自动匹配已有微软 `rndiscmp.inf` / usbrndis6；无需下载、修改或安装第三方 INF。

只重新链接 RNDIS 使 ACM 成为第一接口时，驱动出现 code 10。移除两个配置链接，再依次链接网络、ACM，设置 bcdDevice=0102 并重新绑定，Windows 网卡状态 Up、MAC `02-00-00-00-08-02`、设备状态 OK。此顺序与描述符已经实机验证；源脚本 `scripts/linux/usb-gadget.sh` 已纳入相同修正。

**既有 rescue-final 和 server ready 镜像保持封存，没有更新其中脚本。** 后续构建需生成新组、重新核验；不能把这次 RAM 修正当作旧持久镜像已原生通过。

电脑静态 192.168.8.2/24 配置因 Windows 非管理员权限拒绝；复查 DHCP 仍启用，未成功修改电脑 IPv4 或默认网关。实际测试使用 Windows 自动链路本地地址，手机只在 RAM usb0 添加 169.254.8.1/16，并另开相同禁用密码/转发的 Dropbear listener；SSH 显式绑定 USB 源地址。192.168.8.1 端仍保留，但主机计划静态地址未通过本次验证。

BusyBox 未含 nohup；长串口命令约 1024 字符截断，导致未完成的 subshell 输入。已恢复 shell，改用短命令分段传输，实际 SHA-256 匹配后才执行 RAM 脚本。USB 调整只涉及 configfs、/run 和 RAM 网络状态，没有写手机存储。

## 已完成的原厂回退

用户报告 USB 连着时无法按键回 Fastboot；拔下 USB 后按键返回成功，再接回电脑。此路径已从实际 Linux 环境验证。救援 BusyBox reboot 不提供明确 bootloader 目标，因此没有直接 reboot 启动 Android。

再次核验 kona/A 槽/unlocked/Bootloader 模式和原厂恢复输入 hash，**先恢复原厂 dtbo_a**：25,165,824 字节（24 MiB），SHA-256 `3e4a4aadeed79d5293bb2bfa655fafb7d40f7cb5ee0b1fd67bf20e1c02792706`。Fastboot Sending/Writing 均 OKAY；再 fastboot reboot。

ADB 实读 sys.boot_completed=1、构建 KB2000_14.0.0.602(CN01)、slot=_a、unlocked/orange、UID 2000、临时 su 不存在。原厂 Android 启动已确认。**恢复镜像输入 hash 已验证，手机端刷后 hash 回读未验证**；未为此重新 root。

没有写 boot、super、recovery、vbmeta、userdata，没有切槽。本次单次 DTBO 实验已结束，不能把该授权延伸为后续分区部署。

## 仍需处理

- 内核环形日志保存约 30,393 行，其中 30,387 行是 Synaptics TCM 的 `invalid touch report length`，早期 UFS/启动日志已被覆盖。日志洪泛与触摸协议需要主机源码排查；不能宣称触摸正常或内核稳定运行。
- Wi-Fi/GPU/DSP 等固件路径仅主机集成；本轮救援不装 OEM 固件、不启动蜂窝/NV 服务，没有验证这些硬件。
- 持久 Debian 13 systemd 启动、UFS 内容 I/O、供电/温度及 24 小时服务器负载仍待部署后验证。
- 新 USB 源脚本需进入下一组 boot/rootfs 并重新成组校验；Windows 自动地址或 DHCP 的持久连接策略仍需完善。

原始日志、SSH 公钥/host key、设备信息、RAM 调整脚本与结果只存忽略的 `local/linux-dtbo-experiment-*/`；入口指针 `local/dtbo-experiment-current.json`。Git 仅保存此摘要与非敏感配置，原候选及 OEM 文件不上传。
