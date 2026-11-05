# OnePlus 8T Venus 视频硬解候选（2026-11-05）

## 当前状态

已于用户明确授权后正式启用 **`7.2.0-kebab-debian13-r4-venus+`**：仅写boot_a，保留原r3完整备份，经正常重启从持久分区启动；Venus开机自动绑定并再次完成720p/30fps、90帧与软件逐帧MD5一致的硬解测试。用户确认触摸与三档正常。限充在TCPM online0下自动停充与续租；Firefox硬解、长播放和其它codec仍未验收。下面两轮临时试机是正式部署前的历史。

本轮原运行内核为 `7.2.0-kebab-debian13-r3-slider+`，boot 为 `eb8b7a4b-a689-444b-b6aa-99ba2b45ae95`。正式 Xfce 软件渲染配置保持；Adreno 绘图验收见 [GPU 报告](gpu-validation-20261105.md)，其结论不能代替 Venus 解码验收。

## 正式启用与验收（21:52起）

用户明确要求“没问题的话就启用r4吧，并更新git并推送”后，主代理在原r3上完成只读预检：583模块与封存tar逐SHA一致、实际vermagic一致、OEM13文件与别名一致，A槽active/successful、充电租约正常。`deploy-venus-kernel.py` 默认不写，显式`--apply`在同一flock内重新检查身份和所有输入；完整96MiB boot_a及原helper先备份到 `/srv/kebab-r4-deploy-20261105/backup/` 并fsync核SHA，再原子更新helper、写boot前缀并核对前缀/未写尾部。部分写入、读回或后续检查失败会尝试恢复完整boot与helper，不自动重启。

实际写入镜像仍为8d409dfd…，47,869,952B；boot_a完整分区由4fab…变为 `4838d489afb25402c64c439b35d58701ec4dacecb879825ce3187790b7cc2ac5`，镜像以后的分区尾部SHA保持。boot_b、dtbo_a、dtbo_b及GPT槽字段前后全部一致，不切槽、不更新DTBO。r3备份仍完整SHA4fab…，电脑既有同SHA备份继续保留。新部署记录与临时trial manifest分开，后者仍保留历史的no-write字段，不能用于本次分区状态。

正式入口为 `kebab-venus.service`：精确匹配r4，先校验root-only OEM清单固定SHA与13个文件/别名，再显式加载core/decoder；udev黑名单继续保留，encoder不自动加载。该oneshot无自动重试/循环，不主动播放视频。脚本默认只检查，只有unit显式`--load`才加载；成功还要求sysfs qcom-venus/venus_core父驱动绑定，不能只看modprobe退出值。unit与脚本已安装并enable，原厂文件从rootfs加载；initramfs内仍是旧generic固件且不加载Venus，不能单拿镜像用于另一rootfs。正式桌面软件渲染及Firefox配置保持。

主代理提前告知后正常重启，boot `7065f22c-00ca-472e-b8eb-937d11c8b5b8`，实读r4、GNU build-id `a4c5c07b0468d585388115087d2543f86ac9799d`、/proc/config.gz解压SHA e2543ac0…完全一致。Venus服务开机自动成功，13文件校验通过、decoder `/dev/video14`，峰值9.8MiB、CPU126ms；随后正式720p90帧对照再次90/90一致、IRQ+193，服务2.375秒/CPU2.599秒/峰168.5MiB。与临时轮74.7MiB样本是两次不同的实测，不挑较小值当永久资源承诺。

用户实测确认触摸和上/中/下档均正常；新builtin `synaptics-tcm`接管触摸，旧r3修复服务按条件跳过。DSH672/酒馆683/NapCat738/三档876/限充573 active且NRestarts0；active不代替QQ登录、公网、付费生成验收。TCPM online仍0，启动38.11秒inhibit、53.12秒renew，REG08=06/owned1/错误0；128秒时89%、0µA/Not charging、37.6°C。未主动将89%放电回80%。

新增9项启动门禁/绑定测试、8项正式部署测试及复用8项GPT/部分写入回退测试通过；实际preflight及apply回读通过。部署护栏最终SHA `794aa6feb788e62a5302f0a9b51fab82dec05b39d30b5656afc375eba6351b1d`。第一次上传期间脚本最终版发生变化，固定SHA门禁拒绝，尚未创建执行副本或写分区；重新冻结最终版后才通过预检与部署，拒绝不计成功。

开机有界观察实际90样本/180.0016秒，同boot，USB online全0；前38秒按原三次确认保持owned0，随后owned1/last_error0、正常续租。电池37.2–37.7°C；CPU/GPU最高采样73.6°C出现在开机12.12秒的cpu7，窗末最高42.2°C，不当作持续高温或Venus独立温升。最后五次电池样本四次0/Not charging、一次-1mA/Discharging，均无正向充电，电池37.3°C；没有将微小净放电当作主动放电策略。最终无failed，kernel无所筛panic/OOM/固件崩溃匹配；旧DSI WARN与触摸残余错误仍保留。临时开机观察unit收尾已disable并移除，仅Venus正式入口保持enable。

正式现场归档 `local/venus-enable-20261105/permanent-r4-evidence.tar.gz`，2,850,516B，双端SHA `260787a8be80aa78285629aea6417c54aad652e808fd4996e8b1e1aaf636633b`；包括部署/回读记录、最终观察、内核与服务日志、成功样片/帧摘要，不包含大分区备份和固件。原始资料仅本地保存，公开发布采用独立脱敏文本分支。

原临时回退timer/service保持移除；`kebab-venus-return`用于“原boot未改”的旧临时方案，**不可作为现在正式r4的回退入口**。正式回退需先核本次deployment.json和r3完整备份SHA，再恢复boot_a及旧helper、停用Venus服务，正常重启；若无法进入Linux，使用电脑既有同SHA完整r3备份和实体Fastboot恢复，不使用早期NOT-FOR-BOOT候选。没有安排下一次自动重启。

## 首轮实机与返回原组

用户明确确认试机窗口并协助实体Fastboot。只对序列号已核验的KB2000执行一次 `fastboot boot`；product=kona、槽a、unlocked=yes、非userspace、容量及镜像SHA均核验，发送与启动均OKAY。运行内核实读r4，boot `4348534e-9e94-4548-ad37-2e419fa5b4f6`，GNU build-id与最终候选完全一致；18分钟回退timer在候选上实际active，guard默认检查也通过。

三段键正常，触摸由新builtin `synaptics-tcm` 接管。用户最初报告触摸失效，后报告又能操作；有界采集记录4001事件/1067 SYN帧，含8次按下、7次抬起及坐标，受采集上限截断，不能用不等计数判抬起丢失，也不能宣称触摸长期稳定。内核仍有-71与少量无效报文，已保留。

电脑USB下TCPM online持续0，重插也未恢复；接回路由器后online1，原80/75服务恢复停充与续租。机器人曾在启动队列等待校园portal oneshot，随后自行active，不把最初未启动误判为Venus驱动导致。上述均发生在加载Venus之前。

供电恢复后有界加载 `venus_core`、`venus_dec`，模块命令rc0；但实际probe报 `error -22 initializing firmware postmarketos/venus.mbn`。源码定位至 `drivers/soc/qcom/mdt_loader.c` 中 `qcom_scm_pas_init_image()` 返回，早于 `pas_auth_and_reset` 和HFI/解码；没有Venus视频节点。**模块rc0不等于设备绑定成功。**该证据不足以单独定成签名不匹配，但说明SoC默认通用固件不能直接当作本机已验证固件。

现场打包并复制至 `local/venus-enable-20261105/r4-evidence.tar.gz`（43,759B，SHA `a5e619b5db4e343fae1376f9a54bc3e417940a8bf447446dc80bebe1442cc81a`），保留kernel、模块加载/限充日志、观察器与触摸事件。主代理随后显式运行guard的正常重启返回；用户询问时已明确这是主代理执行，并非意外复位。

返回实读原r3，boot `8bc9b6a5-4df2-47d3-b5cc-5b2e8d54d6df`。酒馆/DSH/机器人/三档及限充active，触摸回到原`tcm-r3-fix`。完整boot_a/dtbo_a SHA仍为4fab…/99af…；恢复原三段键helper字节，移除本轮timer/service及启用链接。新r4模块、正确命名固件映射和黑名单仅作为停用准备保留。限充缺口在r3再次复现后，按用户关切单独修复用户态资格判断，未改内核/阈值/网络策略。

## 同机原厂固件与第二轮实机

第二轮固件从同机 `KB2000_14.0.0.602(CN01)` 原厂归档选择性提取。用户另行确认第二个临时启动窗口并操作Fastboot。来源 `firmware/kb2000-14.0.0.602-20261003-154007/firmware.tar` 为467,082,752B/SHA `db7d97ec87643e72ff6e0032fb6c4b7b7956f5f17a38e54bd79445079cfaf406`；源清单与设备前/后/主机摘要一致。仅提取13个Venus原字节文件，未修改签名。

OEM MDT为8,220B/SHA `7809a6913b0230e1b7ec1b0f689e082608e24adc766ec96d9afc91bb534ad6ee`，配套b00–b10及b19。分片长度与program header匹配，loadable段均可重定位、跨度恰5MiB。正式清单SHA `f5eb50d8924392142cc2e87896ef62c94400a4f265855a5048f87005db8ee82b`，位于 `local/venus-enable-20261105/oem-firmware/`。首次helper索引解析失败及部分目录已删除的事实已记录，没有重建伪原始产物。

第二候选内核镜像仍8d409dfd…，initramfs保持原内容且禁止Venus早期加载；运行时rootfs覆盖另写入trial manifest。DT请求的 `postmarketos/venus.mbn` 链到OEM原始 `venus.mdt`，同目录12个bNN别名满足loader按请求名推导分片的规则；原Debian通用blob未覆盖。准备位于手机 `/srv/kebab-venus-trial-20261105-oem/`，effective trial manifest SHA `43a570d2dd19df53ee2b24e592c6ce1d4cd62c4b17eba9df6e9288773461b7b1`。该manifest是启动前身份/许可快照，里面的未验证字段不追写成验收结论，实际结果以报告和日志为准。

第二轮boot为 `2a27a24b-61b0-4dc4-831a-8e6d85d06a61`，r4及镜像一致；用户确认触摸与三段键正常并接回路由器。先确认USB online1、限充owned1/错误0，再有界加载core/decoder；实际 `/dev/video14` 对应 `qcom-venus-decoder`，平台父驱动 `qcom-venus` / 模块 `venus_core`。V4L2回读M2M multiplanar/streaming及H264、VP8、VP9、HEVC、MPG2输入格式；列表不算其它codec解码通过。未加载encoder。

| 样片 | 软件/硬件帧数 | 归一化yuv420p逐帧MD5 | Venus IRQ增量 |
| --- | --- | --- | --- |
| 320×240，15fps | 30 / 30 | 30帧一致 | 74 |
| 320×240，15fps | 90 / 90 | 90帧一致 | 195 |
| 1280×720，30fps | 90 / 90 | 90帧一致 | 181 |

均显式选择 `h264_v4l2m2m`、禁止软件回退，并将FFmpeg所选video节点与sysfs驱动/模块绑定交叉核验；IRQ仅辅助。720p90帧输入为3秒样片，硬件命令耗时0.467秒，整个生成/软件/硬件服务1.642秒、CPU2.220秒、峰值内存74.7MiB。此耗时包括解码及输出处理，不作为远程桌面帧率或长时吞吐承诺。

首次raw AnnexB样片没有有效包时间戳，FFmpeg解出22帧后CFR输出丢掉21帧，最终摘要仅2帧，严格门禁判失败；未见固件崩溃。失败产物保留。探针改为带时间戳MP4和 `-fps_mode passthrough` 后完成上述三组，9项探针回归通过；最终探针SHA `3459350b90283441ae3758022474c2be1f4115d964930a2ec8f849bcd4312b96`。

两段各120秒/61样本观察均同boot、online1/owned1/错误0，电池36.6–37.0°C，CPU/GPU采样最高57.0°C；观察窗覆盖加载与低分辨率测试，**不覆盖最后720p命令**，不当作720p温度峰值。720p结束仍同boot，电池36.4°C/0µA/Not charging；未见panic/OOM/firmware crashed匹配。启动早期保留两条DSI时钟重复disable/unprepare WARN，触摸仍有-71/invalid report；不宣称内核无告警或历史故障修复。旧OEM约42帧异常缺固件身份，本次90帧成功不能闭合旧根因或证明长播放稳定。

主代理明确告知后于21:25左右运行guard正常返回，早于18分钟timer到期。最终原r3 boot `9a36b7eb-4419-499e-8ddf-7afdf8c72dd8`，DSH666/酒馆680/NapCat734/三档864/限充569 active、NRestarts0，无failed；active不等于QQ登录或公网功能验收。恢复原三段键helper SHA47782d15…，移除试机timer/service与启用链接，boot_a/dtbo_a完整SHA仍4fab…/99af…。OEM覆盖和r4模块停用保留，限充修复不回退。正式有线桌面按原规则未开机常驻，原会话随获准的重启结束。

第二轮现场归档 `local/venus-enable-20261105/oem-trial-evidence.tar.gz`，3,504,568B，双端SHA `6f66866c29f15780149ecbad4ded0bc2fb968e4a0310b28aaef9feb3f164ddd0`，包括成功与失败样片、逐帧摘要、命令日志、kernel和观察记录。派生摘要 `oem-trial-summary.json`；返回记录 `oem-r3-restoration.json` 双端SHA `b464c6e3bf14132c9cba4b26a5eb022dcb41665256020dcca40c8c1c0edf3bab`。这是当时仅临时启动的授权边界；随后用户另行批准正式启用，结果见本文正式部署段。

## 为什么必须配套新内核

原 r3 未启用 `VIDEO_QCOM_VENUS`、`OF_DYNAMIC`、`V4L2_MEM2MEM_DEV` 和 `VIDEOBUF2_DMA_CONTIG`。Venus Kconfig 会选择后三项，其中 `OF_DYNAMIC=y` 必须内建进 Image，会新增 `of_node_get/put` 导出并改变 `struct property` 的条件布局。原内核不具备对应 ABI；其 `CONFIG_MODVERSIONS` 还未启用，不能凭相同 vermagic 强行加载新模块。

候选基于源码提交 `83d0c460b621f7effe0a400f32d261c4f18335f2`；在独立树额外适配下文的上游 DT 修正。配置仅有以下差异：

```text
LOCALVERSION "-kebab-debian13-r3-slider" -> "-kebab-debian13-r4-venus"
VIDEO_QCOM_VENUS n -> m
+OF_DYNAMIC y
+V4L2_MEM2MEM_DEV m
+VIDEOBUF2_DMA_CONTIG m
```

原构建目录 `/home/zhuchen/oneplus8t-slider-builtin-20261006` 保持；新目录为 `/home/zhuchen/oneplus8t-venus-20261105`。配置 SHA256 从 `e63bfb240b4f9ed8e9db4c68b07280acfb594bd0aefef1ee57a378520ad922fd` 变为 `e2543ac04e3ca5ffeabce188a313e4d72f4ae985bc0cfd3732dd12cb91ec4e12`。复制的 Kbuild 文件不是硬链接；编译采用 clang19.1.7 / LLVM、ARCH=arm64、4 并行任务。现有 `prepare-kernel.sh` 禁止 Venus 的默认护栏没有删除；本次使用独立、显式候选准备脚本记录新的配置身份。

最终源码提交为 `88903750a310ded3f8ed7d8682d7181c67a4299e`，在原提交上追加 DT 修正及下文的已用触摸修复。0008 patch SHA256 为 `22350cd8151753e4e1a29f7bb5cbafbd5cac38bf5e49da8aef772d5e83570d4c`。最终 artifact 位于 `artifacts/7.2.0-kebab-debian13-r4-venus+/88903750a310-e2543ac04e3c/kernel/`。11 个清单文件 SHA 全部复核，583 模块的 vermagic 全为 `7.2.0-kebab-debian13-r4-venus+ SMP preempt mod_unload aarch64`；新内核 GNU build-id 为 `a4c5c07b0468d585388115087d2543f86ac9799d`。DTC 输出中 **Venus 节点以外字节完全相同**；旧 r3 配置、Module.symvers 及 Image.gz 摘要仍匹配封存值。DTC 的已有总线/图节点警告仍保留，不宣称所有 DT schema 已通过。早期 83d0…和 d4d9…两组离线候选均有 NOT-FOR-BOOT 标记，不是最终试机组。

## 同步整合现用触摸与版本入口

用户询问是否将全部更新统一重编后，又核对实机运行模块与启动入口。唯一额外模块为 `tcm_r3_fix.ko`，当前触摸实际绑定 `tcm-r3-fix`；r3 内建解析器还没有 [10/16 已部署的 0x5a 尾填充修复](touch-fix-r3-20261016.md)。旧修复服务仅匹配 r3，若直接换内核会跳过它，因此必须一并整合。

0009 补丁仅向内建驱动共用的 `synaptics_tcm_report.h` 加入相同 38 行解析修复，不带外置模块的 driver rename。新头文件 SHA `4c1141de2444e2a6f0674e92dba984fe5ea2bce27d42d00c812c71569bcb0fb6`，与现用修复模块的源码头逐字节一致；补丁 SHA `5b019e42c85c4123971630623ca1d85042fec921a8b753b7dc90ce7a65e38250`。oncell 驱动 C 保持原 SHA `4b3431a82fc977c40edfe8ebe0d526b2c4c9486cf30cdd563340029e52e40763`，新 r4 将直接使用 `synaptics-tcm`，旧 r3 专属模块/服务保持供回退使用。

已扩展 `test-tcm-protocol.c` 的真实布局与 payload 测试：抬手整槽填充、有效对象后完整填充、混合尾、非整槽、未对齐、counted/fixed loop 等。主代理用最终候选的真实头文件执行 GCC ASan/UBSan，原功能与 120,000 随机样本全部 PASS。它证明解析器回归覆盖，不代替新内核的触摸实机验收，也未扩大旧填充语义的验证范围。

`kebab-alert-slider.sh` 新增精确 r4 版本的内建健康检查分支，保留 r3/r2 行为；Bash 语法检查通过。该 helper 在启动包 `rootfs-prep` 内备妥，离线阶段尚未覆盖手机活动脚本；两轮临时试机曾安装，最终均恢复原r3脚本。其余应用和 Wi-Fi 省电策略属于用户态配置，不需要为了这些更新重新刷内核。

## 离线启动包

`local/venus-enable-20261105/bundle/boot-venus.img` 为 **47,869,952 B**，SHA256 `8d409dfd6645d81e03fa4adc4006a4d64f8afb7200bb385b250b0e4661e30bed`；同目录有完整模块包、initramfs、准备载荷和 manifest。8 个输出文件 SHA 已独立复核；该段为离线打包阶段记录；随后的部署、两轮临时启动与结果见前文。

打包器保留封存 r3 的 **200 个早期模块路径**，按规范化模块名从新 583 模块组中取对应文件；完整包的 `.ko.zst` 必须先解成原 `.ko` ELF 路径，重新解包后逐字节 SHA 比较。其余 initramfs 文件及符号链接保持，只有显式添加的六份固件、正确 VPU 别名和 Venus 黑名单例外。没有把 Venus 或新 M2M/DMA-contig 模块提前放入 initramfs。

旧启动参数（root UUID、充电控制参数等）、v2 布局与容量约束保持；AOSP 独立解包的内核、DTB、ramdisk SHA 均匹配输入。`rootfs-prep` 包含三段键 helper、回退 guard/两 unit 和黑名单，仍须在安排试机后以 root 所有、正确权限安装。最终 manifest 与回退 guard 的字段/身份已做集成检查：候选接受、原 r3 拒绝，未执行重启。

12 项打包器单测（含 DTC u64 语法及备用 GZIP/XZ 解压）、9 项回退测试、9 项解码探针测试通过。独立终审未发现当前启动包的阻断问题；目录重叠、设备归因和回退时序边界已收口或明确记录。OPP 验证器核双 required-opps 的数量，具体 MX/MMCX 语义依赖本次固定补丁、源码身份与实际 DT 比对，未声称任意 DT 的语义审计。首轮 Windows 临时目录权限失败、WSL shell 变量失败及 GCC 参数解析失败都发生在有效执行前，未算作功能通过。有效测试和实包日志保留。

## 设备树与固件

实机 DT 与 r3 artifact 均已将 `soc@0/video-codec@aa00000` 设为 `okay`，兼容串 `qcom,sm8250-venus`；时钟、IOMMU、reset、互连、电源域和 OPP 已描述。预留内存为 `0x8e800000 + 0x500000`，5 MiB、no-map。**这些是描述存在的证据，不是硬件初始化通过。**

继续与维护者补丁比对发现，旧 DT 的 OPP 仍为 720/1014/1098/1332 MHz，并只声明 MX。驱动设频所用的 MVS0 时钟是源时钟的三分之一；候选需修正为 **240/338/366/444 MHz**，追加 MMCX 电源域，并按维护者定义为每档提供 MX/MMCX 两项 required-opps。现有 Venus C 代码已经支持两个域，旧 DT 兼容回退不能代替正确电压/频率描述。修正仅落在新的隔离源码，补丁为 `patches/local/0008-arm64-dts-sm8250-venus-power-opp.patch`；不更改当前手机 DT 或原 r3。第一轮未包含这项修正的离线产物不用于试机。[上游频率修正 68ea007](https://kernel.googlesource.com/pub/scm/linux/kernel/git/bpf/bpf-next/+/68ea007df9293fcb29d38219d73094bbf4b59673)、[维护者电源域修正](https://lkml.rescloud.iu.edu/2606.0/09136.html)。这些是已知 DT 错误的修正，不据此解释旧固件断言或上午重启。

DT 固件名为 `postmarketos/venus.mbn`，实机缺少该文件。SM8250 legacy Venus 默认名是 `qcom/vpu-1.0/venus.mbn`，Debian 包将其链接到 `qcom/vpu/vpu20_p4.mbn`。实机已包含正确 VPU20 P4：

- 大小 1,985,348 B，SHA256 `13088ef634f4b9ee7cb329b2a4d66800c797a70a3cfb407f5818b0ff0af78db3`。
- ELF PT_LOAD 区域按 MDT loader 排除 hash segment 后，跨度恰为 5 MiB，均为可重定位段；仍需实机 SCM 认证、HFI 握手验证。
- 拟在候选的固件树提供 `postmarketos/venus.mbn -> ../qcom/vpu/vpu20_p4.mbn`，使现有 DT 请求解析到已核 SHA 的正确文件。

`venus-5.4` 和 `venus-6.0` 也在机器上，但属于不同固件，不能按 HFI6 名称猜测或用来代替 VPU20 P4。最初只读检查也记录了这两个文件，后续已按平台数据纠正选型，未部署错误别名。

参考：[Linux v7.2 Venus 平台数据](https://github.com/torvalds/linux/blob/v7.2/drivers/media/platform/qcom/venus/core.c)、[固件加载的 DT 优先规则](https://github.com/torvalds/linux/blob/v7.2/drivers/media/platform/qcom/venus/firmware.c)、[Debian trixie 固件文件清单](https://packages.debian.org/trixie/all/firmware-qcom-soc/filelist)。

## 用户态和浏览器边界

已安装 Debian `ffmpeg 7:7.1.5-0+deb13u1`，84 个新包、无升级或删除，约 153 MB 安装空间；实际构建列出 `h264_v4l2m2m` 和 `hevc_v4l2m2m`。不因 decoder 列表存在就判硬解成功。

第一阶段使用显式 `h264_v4l2m2m`，生成本地 320×240、15 fps 的 H.264 短样片，比较软件与硬件输出的真实帧、记录 decoder 日志和 VPU IRQ。硬解失败应明确失败，不回退软件后报成功。短测之后还需覆盖更多帧、更高分辨率与连续播放。

已在原 r3 上运行探针的 `--software-only` 对照：本地生成样片及 native H.264 解码均 rc=0，得到 30 帧 320×240/yuv420p；两条 FFmpeg 命令各约 0.17 秒。该模式未请求硬件解码或 IRQ 采样，不能计为 Venus 验收。完整帧摘要、样片及日志保存在 `local/venus-enable-20261105/software-control/`。

Firefox 是下一阶段：FFmpeg7.1 的 V4L2 MMAP/NV12 输出不等同 Firefox 所需的 DRM PRIME 帧；安装 libva 也不会把 Venus 变成 VA-API 设备。需另行核实 Firefox 实际加载的 libavcodec、DMABUF 路径与播放日志。关于 about:support 可能显示支持而实际回退软件的案例，见 [Mozilla1852765](https://bugzilla.mozilla.org/show_bug.cgi?id=1852765)；用户态接口参考 [FFmpeg7.1.5 V4L2 实现](https://github.com/FFmpeg/FFmpeg/blob/n7.1.5/libavcodec/v4l2_context.c)。本轮不自动改正式桌面硬解或 GPU 设置。

## 已知风险和试机边界

固定 bring-up 提交 `2e5e065da8ea8d242ecc34bfe064b88257797aa9` 的 `sources/bringup/docs/REGRESSIONS.md`（VIDEO-1）记录过：旧 OEM `postmarketos/venus.mbn` 在 H.264 720p/NV12 约 42 帧后出现固件异常，同一调查会话多次重启；FFmpeg 与 GStreamer 都遇到相同内部地址/cause。旧记录没有固件 SHA 或 OxygenOS 版本，**既不能等同当前 Debian VPU20 P4，也不能据此断言新组合已修复。**

因此候选首次启动应阻止 Venus 自动加载，先核 USB、限充、三段键、触摸、Wi-Fi 和关键服务，再安排有界加载和短片；保留原 boot、原模块及原故障证据。优先 Fastboot 临时启动，不为首次验收写 boot 分区。手机接路由器时无法直接由电脑 Fastboot 操作，可能需要用户实体按键并临时将 USB 接到电脑。重启会中断酒馆和机器人，也可能需要 QQ 重新登录。

预备的 18 分钟回退 timer 仅匹配 `7.2.0-kebab-debian13-r4-venus+`；guard 默认只检查，只有显式 `--reboot` 且候选版本、root UUID、原 boot 完整 SHA 与 manifest 相符才正常重启回原组。9 项离线测试覆盖身份拒绝、CLI 默认不重启、错误身份即使显式请求也不重启，以及正确身份请求正常重启；不执行真实重启。service 拒绝手工直接启动，timer 才能间接激活它。部署前需核最终 artifact/runtime release 与 guard/两 unit 完全一致，并把 guard/manifest 放在 root 所有、普通用户不可写的路径。

`OnBootSec=18min` 从开机计时，必须随候选早期开机启用；18 分钟后才启动会立即到期。guard 的 45 秒 service 超时或底层 I/O 卡死可能使自动回退失效；timer 不是硬件复位保证，不能代替人在手机旁的 Fastboot/实体恢复。原分区已只读实算 SHA 并匹配完整备份，正式试机前仍要复核及记录耗时。模板随后已用于两轮实机，最终返回原r3后均移除；当前不再启用。

实机只读核验的 `boot_a` / `dtbo_a` SHA 分别为 `4fab2d9152aa78b41b884e913afb53c9605b5bb5e2ecb821f7e72d20d2603dfa` / `99af1264aa5f1a2bdcff12b4e4d5ec5ff0a04c86b88a207bdcd28429b79b21b0`，与已有完整回退备份一致。这是首次试机前的只读基线。后续已临时启动并部署新模块/运行时固件别名、修复限充用户态资格门；始终未刷分区或修改网络策略。

原始证据：`local/venus-enable-20261105/`。固件输入的六个文件（现用 Wi-Fi 三件套、实际请求的蓝牙两文件、VPU20 P4）已复制并双端核 SHA/大小，供离线 initramfs 打包使用；未覆盖实机活动固件。

离线准备阶段19:33收尾复读（不是最终实机收尾）：仍是原 r3 / 同 boot；桌面43460、DSH236037、酒馆748、NapCat747、三档877全部 active/PID保持，USB online1、停充owned1/错误0，无failed单元，原pstore SHA保持。未因候选编译或软件对照中断这些服务；不把 active 当作公网、QQ登录或付费生成验收。
