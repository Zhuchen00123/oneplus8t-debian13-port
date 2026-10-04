# Debian 13 显示与触摸内建 r2（2026-10-04）

## 状态

用户授权完成完整 r2 组并部署。新内核 `7.2.0-kebab-debian13-r2+` 首启成功，严格 USB SSH、根 UUID/读写、内建触摸绑定、面板 DCS 读与 GLES 绘制回读通过。旧触摸接管服务按内核条件跳过。

真实单指、双指、抬起及第二次正常重启已取得事件证据。用户在重启前后均确认桌面正常显示、触控正常、松手后不漂移。

曾出现用户完全不碰屏幕时指针自行移动，随后恢复正常；协调用户放平手机的一分钟静置采集零事件，本轮未复现。不能据此宣称间歇误触根因已消除。

## 成组修复与构建

- 社区来源仍是固定 SM8250 7.2.0 和 bring-up `2e5e065d…`，不升级其他子系统。
- `sources.lock.json` 新增 `kernel_profiles.r2`，按 0001 → 0003 → 0004 → 标准邮件格式 0002-r2 顺序应用，逐个核验补丁和源码前像 hash。
- 0002-r2 从既有内建触摸提交导出，保留作者。实际驱动 SHA-256 与已验收候选一致，未修改触摸固件/持久报告配置。
- 新配置仅将 LOCALVERSION 从 r1 改为 r2；默认 r1 配方保留，r2 明确选 profile。
- 内核提交 `e3f3f54911c5…`，配置 SHA 前缀 `4b84f5c579ed`；内建 TCM，独立 r2 模块目录。
- 主机真实共享解析头 ASan/UBSan 回归：固定用例和 120,000 个随机/变异用例通过；DTB 先于全内核构建。
- 578 模块逐一通过 vermagic；实际 rootfs 578、持久 initramfs 200、RAM initramfs 8 模块与同次构建的原始字节一致，必要 USB 依赖齐全。
- 保留已有两个 qcom_fg missing-prototypes 编译告警及 DTS 结构告警，未宣称清除。

复现内核：

```bash
bash scripts/linux/prepare-kernel.sh /home/<user>/oneplus8t-build-r2 r2
bash scripts/linux/build-kernel.sh /home/<user>/oneplus8t-build-r2 dtb
bash scripts/linux/build-kernel.sh /home/<user>/oneplus8t-build-r2 kernel
```

## 系统镜像与恢复点

- 当前可用 r1 boot 全分区备份与设备读取前后 hash 一致；其候选前缀 hash 与此前已验证的显示修复 boot 一致。旧 r1 模块树（含接管模块）原位保留。
- 当前系统做了逻辑文件树备份，设备压缩流与主机 hash 相同，gzip 完整性通过。它排除虚拟挂载、易失日志、APT/桌面缓存，是运行中逻辑快照，非原子或原始分区备份。
- 首轮因 sysfs 目录变动返回失败，失败副本保留；第二轮排除挂载点本身后通过。
- 新离线 rootfs 基于已校验系统副本，包含现有 Mesa/XFCE/BlueZ/校时等用户空间；删除复制品身份、旧模块、旧接管服务，再安装完整 r2 内核组。
- rootfs 缺 /dev/null 导致首轮 chroot QA 失败；离线补齐标准设备节点后，ARM64 正确/错误公钥及禁用密码检查通过。
- 保留 500 个此前认证的 Debian 包输入；本轮逐份记录传输 hash，没有声称重新认证当前 APT 仓库。
- 新持久组 `artifacts/server-r2-20261004/ready/`，boot 45,625,344 字节、4 GiB ext4/sparse。为原位 boot/modules 升级明确保留当前根 UUID。
- boot/fstab/镜像 UUID、AOSP 解包、只读 e2fsck、sparse 往返、固件与 SSH 身份初始化通过。无需通过全量刷 super 才能更新内核。
- RAM 救援最终组 `artifacts/boot-r2-20261004/ready/`。中途 Git 所有权检查失败的目录保留；仅在单条 Git 读取中信任已锁定来源目录，没有更改全局 Git 信任。
- 源码、内核与持久产物均已建立校验快照。原 r1 组保持封存。

## 已执行的手机操作

只新增 `/usr/lib/modules/7.2.0-kebab-debian13-r2+` 和写 `boot_a`。写前核对旧 boot 完整 hash、标签/容量、根 UUID 和全部输入；写后按新镜像实际长度回读 hash 一致。**未写 super、dtbo、vbmeta、recovery、userdata、NV，未切槽。**

旧接管服务先新增 `ConditionKernelVersion=7.2.0-kebab-debian13-r1+`，首启日志确认跳过；真实触摸通过后禁用该服务，第二次重启确认 inactive/disabled、没有加载测试模块，实际绑定内建 `synaptics-tcm`。旧模块/脚本保留作恢复输入；回退 r1 后须通过 SSH 重新启用旧服务。

## 首启已验证

- 新 boot ID、严格保存主机密钥的 USB SSH，真实运行版本 r2。
- 无 failed unit；默认 multi-user.target，LightDM 保持禁用。
- 首启早期采样 BTF 拒绝、活动 Oops/panic、配置触摸报文错误与 frame done timeout 为零。后续较长窗口出现配置报文拒绝及 `-71`，正常触控时也出现；与指针漂移的因果尚未确定，不能称为已清除。
- 面板 actual_brightness 返回 39936；有界 XFCE 后 vsync 递增、underrun 0、frame_done_cnt 0。
- 普通用户 GLES FD650 绘制红三角和像素回读通过。
- 32 MiB fsync 写入、文件 hash 和直接读取一致；第二次重启后两种读取仍同 hash，测试文件已清理。

## 真实触控与第二次重启

- 单指/拖动采集 908 个事件，18 次 tracking 建立、18 次抬起；用户确认正常并且松手不漂移。
- 独立双指采集 3,197 个事件，两个同时 tracking ID、两次建立与抬起。
- 用户协调静置 60 秒：零事件、零触点、零坐标变化。此前未协调的 45 秒零事件仅作为辅助记录。
- 第二次正常重启：新 boot ID、严格 USB SSH 恢复，版本仍 r2，内建绑定与禁用旧接管服务通过；该早期采样 BTF 拒绝、配置报文错误、显示超时均为零。
- 第二次重启后的真实操作：2,305 个事件，20 次 tracking 建立与 20 次抬起；用户再次确认屏幕/点按/拖动正常、松手静置不漂移，收尾无失败系统单元。
- 重启后测试工具因 `/tmp` 已清理而首轮启动失败，传回临时工具后清除失败测试单元并重新开始；不影响实际内核服务。

尚未验收长期误触稳定性、边缘/掌拒、三指以上手势；原有 120 Hz 唯一模式、fbcon 映射与供电管理问题不在本次修复范围。

此前旧候选首次 ABL 错误、第二次启动后 BTF 拒绝的所有因果仍未完全定位。本次成组 r2 首启通过，不能据此宣称解决了所有 ABL 失败路径。

私有原始证据与本机备份入口：`local/r2-integrated-20261004/`。镜像、固件、原始日志与备份不上传 GitHub。
