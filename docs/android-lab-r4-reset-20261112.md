# r4 Android实验：非预期复位与受阻收口（2026-11-12 UTC，历史）

> 后续用户已明确授权继续；[2026-11-13报告](<android-lab-r4-boot-20261113.md>)记录真实Android13启动、UI/Frida与三轮启停通过。modem尚未恢复、上次意外复位原因未定仍有效；本页未启动/待授权为旧阶段快照，不覆盖后续实测。

配套：[执行总记录](<android-lab-r4-execution-20261112.md>)、[复现/停止/回退入口](<../scripts/android-lab/README.md>)、[固定版本和校验清单](<../config/android-lab-r4-versions-20261112.json>)。**未通过Android验收，不是完成声明。**

## 1. 三次尝试，按证据收窄

| 尝试 | 实际证据与分类 |
|---|---|
| 1，约16:46Z | 专用Wayland socket ENOENT，LXC未到/init。实验单元ProtectHome隐藏/run/user，属于配置/图形会话问题。清理后bridge/规则/进程/Binder模式恢复。 |
| 2，约16:54Z | 单文件BindReadOnlyPaths例外仍ENOENT。nsenter进入精确服务mount namespace，stat/test仍失败，否定“配置已生效”；未反复空重试。 |
| 3，约16:57Z | 仅将专用socket bind-alias到专用/srv runtime，精确namespace test -S通过。DEBUG证实socket/data挂载与设备cgroup过滤、exec /init成功；/init约20ms即退出，data仍空。没有框架、ADB或真实App。 |

当前仅能分类为早期init的配置/镜像/内核交互未分离，不能宣称r4缺Binder能力。LXC短暂RUNNING不是Android启动成功。第三次控制器只看session存活导致多等300秒，最终代码已加早退出/新旧boot校验；**没有再次启动验证**。

源码审计还发现传统Binder节点原0600，而上游传统节点探测不改权限；最终适配源码将三个节点列入精确临时权限与回退名单。此改动尚未复测，不编造它已经解决/init退出。LXC另报绝对/run/xdg挂载点被忽略和post-stop=/dev/null执行126；前者未阻止第三次socket挂载，后者不是最初退出原因。最终源码将noop改为/usr/bin/true，并显式proc:mixed/sys:ro，不允许Android脚本改变宿主全局sysctl。

## 2. 非预期整机复位

- 约17:05Z SSH重置，重新连接后uptime归零，仍r4。boot标识只留设备，导出只含same_boot=false。
- 本会话没有发reboot/poweroff/fastboot/分区写命令。最后容器停止日志约17:02:26Z，之后只读检查镜像并正常卸载，准备取证工具时连接断开。**不能仅据时间先后认定是Android造成，也不能排除实验影响、内核/固件/供电或外部操作。**跨boot墙钟不是精确因果链。
- /sys/fs/pstore为空不代表没有证据：原有systemd-pstore在新boot将console-ramoops-0归档。已设备内另存、哈希，未主动清pstore或重置既有observer预算/改timer。
- 归档console共169行、11,746 B，末条binder_debug限流提示，随后ECC无错误；没有明确panic/Oops/BUG记录。**缺panic文字不能排除内核故障。**首版大小写不敏感子串把binder_debug误计成BUG，summary-v2已纠正为0；v1保留。
- 原始上一boot kernel/system journal、pstore、modem开机日志和SHA清单保留在设备的本轮事故目录（0700），没有上传原始设备标识、凭据、音像。

## 3. 服务器结果：不能算保持正常

- modem原开机链报 `modem did not answer QMI within 70s`，unit failed；没有重载MHI、重启该单元或改开机链。
- 17:09–17:11Z快照中QQbot为ConditionResult=no、inactive，原6099端口消失；**17:30Z补查已ConditionResult=yes/active，PID7080**，本会话没有启动它或绕过条件。不能把早期快照写成最终状态。
- SSH/DSH/酒馆/SSC/Venus/限充恢复；GNSS服务active，但modem未验通时**不能声称实际出星或可用**。
- 限充97%、0µA、37.3–37.5°C，REG08=6、owned=1、last_error=0、停充读回有效；未改限充或温控。
- 对账明确fail：same_boot=false，部分接口/路由/监听与服务PID/状态改变；DNS文件与nft相同；iptables-save在前后均不可用，首版对账将两个空输出误当相同，v2改为未知；收尾root读取已加载legacy表名为空，Binder仍0600/ioctl协议8。新boot的NRestarts=0不能证明服务没中断。
- 根可用555,237,376 B，数据盘可用208,740,741,120 B。相对初始根使用约+36.7MiB，数据盘约+3.45GiB，为新增包、已校验镜像/索引/工具/证据；没有把Android大镜像放根。

### 定长观察，不冒充Android soak

Android停止后的宿主观察180.000秒、36样本，同一新boot无再复位，MemAvailable最低10,862,006,272 B，整机CPU busy峰10.43%，thermal峰44.9°C，电池峰37.3°C，限充36/36 owned且无错。Android ready样本**0**，不是Android稳定性或长期安全验收。

## 4. 清理与保留

三次失败各执行清理；复位后又核无waydroid0、容器/Frida进程、state bind或socket alias bind，实验临时单元已消失。四个新增具体Waydroid/LXC服务仍masked，无Android开机自启。全局/每接口转发均恢复0，无本轮nft表/策略规则或监听；没有flush全局规则。三个Binder仍0600。

本轮目录已写HOLD_UNEXPECTED_RESET，最终controller拒绝新启动。跨boot不重放旧ACL、PID、forwarding快照。最新代码/自有APK已留数据盘，**复位后未再启动Android或注入**。包、校验镜像与所有旧结果保留，没有rm -rf用户数据或自动卸载依赖。

## 5. 最小必要后续与边界

1. **先恢复保护基线**：需要单独允许定位并按原配置恢复仍failed的modem；QQbot已自行恢复，不再对它启动/重启。先查QMI失败，不能绕过原条件、不盲目重载MHI。若恢复必须手机reboot/换核，仍需新的明确授权；本轮不做。
2. **一次有早期日志的维护窗口**：socket可见性已定位修正，Binder临时权限有明确配置依据；下次应先取/init退出码/控制台或系统调用，不放开全部设备、不禁用宿主安全、不无假设循环。strace准备时SSH断开，**没有完成strace跟踪**。
3. **没有证据必须换r5**。若后来证明具体内核能力缺失，再列最小配置/源码变更与独立试机授权，不由“缺binderfs”推导换核。
4. 之后再闭合框架→ADB→三轮真实agent→Frida调用关联→三轮成功启停→定长Android soak。当前不是等待相机/GNSS/完整音频，而是具体的早期init与保护服务器基线阻塞。

## 6. 主闭环后的硬件依赖排序（只读评估）

1. **音频受控路径**：先virtual/null输出、专用认证/权限/lifecycle，再精确PipeWire节点。AudioRecord到o8t-mic需Android audio HAL、采集权限/定时退出与原始数据留机；AudioTrack到o8t-speaker必须证明guardian不可绕过、租约/预算/故障静音有效。**共享Pulse socket不是Android适配完成**，更可能绕过保护访问默认物理设备。DSP-bypass无行程/热保护，unattended_ready仍fail。
2. **传感器→GNSS**：先受限SSC只读样本/速率与Android sensors HAL，再GNSS provider/坐标隐私/权限/lifecycle、modem健康前置。host JSON/socket可读或服务active不能替代Android HAL。若增加长期高权限受限broker，优先评估Rust；不为本轮小脚本强行重构。
3. **相机最后**：host CAMSS/RAW成功不代表Android CameraProvider/HAL/ISP/处理管线；还需格式/曝光/流生命周期、内存和隐私预算。当前不挂video、不采帧、不上传照片。

## 7. 交付范围与隐私

工作区有执行/事故记录、安装/手动启停/回退说明、单命令agent/Frida脚本、自有APK源码、固定版本和SHA、离线13项测试、基线对账与脱敏归档。UI和Frida实测各0次，**不能给出伪造截图、调用记录或3/3成功率**。

可分享归档仅白名单的本轮源码/报告、校验清单、脱敏基线/事故摘要/宿主指标、自有APK；不含私钥、Git全历史、Android镜像、原始设备日志、原始音像。私有Git bundle/工作文件快照只留本机backups，需另存外部介质。未自动commit/push，用户已有改动保留。
