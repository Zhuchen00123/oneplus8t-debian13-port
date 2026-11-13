# r4 Android 13 真启动与 agent / Frida 验收（2026-11-13 UTC）

## 结论

**已真正启动，且不是仅有LXC RUNNING或准备代码。** 保留现役 `7.2.0-kebab-debian13-r4-venus+`，Android13 / arm64-v8a、`sys.boot_completed=1`、鉴权ADB、真实App UI、Frida调用全部实测。继续阶段没有手机重启、刷写、切核或改原开机链。

| 项目 | 实测结果 |
|---|---|
| Android框架 / ADB | 3个完整成功周期均通过，`ro.adb.secure=1`，独立ADB server5038 |
| UI自动化 | 独立agent 5/5（首周期3轮，后两周期各1轮），Frida关联UI另3/3，总计8次 |
| Frida | client/server16.7.19，3次真正attach自有APK，捕获 `MainActivity.transform(String)` 入参/返回值/时间 |
| 图形 | Android SurfaceFlinger：`freedreno, FD650, OpenGL ES 3.2 Mesa 26.0.8`，非软件渲染冒充GPU |
| 真实网络 | Android域名解析、1.1.1.1 ping、Android自身curl HTTPS200，未改宿主默认路由/DNS |
| 连续启停 | 3/3完整周期；后两周期各37.659 / 39.676秒，均含UI与Frida及干净停止 |
| 资源 | payload真正位于独立受限scope下，3GiB memory.max、CPU200%、pids2048；记录实际路径和数值 |
| 定长运行 | Android在线180.000秒、36/36 ready，同一host boot、限充36/36 owned无错 |
| 离线回归 | 23项通过，含定位正负例、真实节点/权限租约、HOLD、key格式、精确策略规则 |
| 最终清理 | 容器/ADB/Frida/bridge/owned nft与rule/state bind/socket alias/runtime units清除，10节点mode/uid/gid/rdev/ACL全还原 |

**不能扩大成“所有服务器从未受影响”。** [上一阶段事故](<android-lab-r4-reset-20261112.md>)确有非预期整机复位，原因仍未确定。用户随后授权继续。继续前modem已failed，本轮一次精确Sahara消费者重绑仍停SBL，原绑定还原；未卸载MHI控制器、未做PCI/手机reset、未写NV。modem尚未恢复、GNSS未重验出星。DSH/酒馆/QQbot/限充/GPS/SSC/MM等监控服务状态、PID、NRestarts在后两轮前后完全一致；不是声称modem健康。

## 从失败证据到最小修正

1. **不是Binderfs缺失阻塞**：r4三个真实传统Binder节点协议8，实际Binder服务成功注册；此轮单实例不需要换核。没有伪造Binder或混装模块，多容器隔离仍另验。
2. **first-stage init严格要求创建kmsg**：初始cgroup拒绝1:11的mknod导致fatal。私有普通文件预绑定取得了明确错误文本，却因EEXIST本身也会fatal而不能直接作为最终解法。最终只允许 `c 1:11 m`，**不允许r/w**；不绑定宿主kmsg，实测guest读/proc/kmsg及零字节open写/dev/kmsg均被拒绝。
3. **logd要求了被刻意删除的宿主能力**：原Android rc包含kernel log描述符及SYSLOG/AUDIT_CONTROL。用[仅用户态日志覆盖](<../config/android-lab/logd-container.rc>)保留Android日志socket，移除宿主kernel/audit路径与auditctl触发；guest ro.logd.kernel/auditd=false。没有给容器宿主audit控制权或全局禁用安全。logd现运行，UI/Frida命令均有真实输出。
4. **Wayland协议尺寸错误**：Weston默认顶栏32px，maximized配置720×1248，而Android发送720×1280，hwcomposer因xdg_wm_base error4 abort，继而SurfaceFlinger/系统watchdog。专用[Weston配置](<../config/android-lab/weston.ini>)仅关闭自己的顶栏；宿主桌面/物理输出不替换。
5. **资源限制不能只看unit配置**：LXC默认曾将payload放到根cgroup，父manager的上限没有约束它。启用relative后又因manager占着父组而EBUSY。最终把仅LXC命令放入独立 `kebab-waydroid-r4-payload.scope`，Delegate=yes、relative=1，启动时核实际payload路径和memory.max，异常即停止。
6. **ADB路由是真实缺陷**：原仅from-subnet规则同时抓走宿主源192.168.240.1，route-get实证把ADB发向wlan0。规则新增精确iif=waydroid0后，host-to-guest走bridge，只有真正容器入站流量走独立表5240；不改main default。
7. **ADB key不是外来凭据**：Android只给同一份自有公钥增加了末尾换行（720→721B）；改为比较key material，保留拒绝外来key，绝不为了通过而关鉴权。
8. **每次启动独立权限租约**：新boot不重放旧ACL，每次记录真实字符节点身份与ACL；停止后验证并幂等释放。停止也会把当前ADB缓存标为无效，不能用旧成功JSON冒充仍在线。

## 真正执行的应用与方法

仅自有零权限包 `org.oneplus8t.agentlab`；[源码](<../scripts/android-lab/test-app/src/org/oneplus8t/agentlab/MainActivity.java>)没有账号、外部服务或硬件需求。输入/Apply/结果均按当前XML中的package+resource-id定位，tap坐标来自当次bounds；断言输入值与 `OK:<唯一token>`，保存XML、PNG、argv、返回码和时间。

Frida第一条真实观测示例：`MainActivity.transform(String)`，入参 `o8t_4423f8e6dab7455fba46a5904b00266b`，返回 `OK:o8t_4423f8e6dab7455fba46a5904b00266b`，与同次UI结果完全相同；另外两周期各有独立token/截图/调用记录。3次均核卸载hook、detach、自有server无进程、自有App停止、本轮forward删除。

![自有应用真实输入、点击和结果](<../local/waydroid-r4-lab-20261112/own-app-frida-proof.png>)

## 资源、网络与服务器

180秒：可用RAM最低8,695,533,568 B（约8.10GiB），整机CPU busy峰17.7%，电池最高37.5°C，thermal最高46.897°C；限充owned=1/error=0且停充读回有效。一次活动态scope实际MemoryCurrent2,312,552,448 B、MemoryPeak2,623,651,840 B、Tasks1004，MemoryMax3,221,225,472 B，CPUQuota=2s。短时测量不是长稳或完整热安全标定。

Android curl使用自己的解析器和TLS验证，HTTPS200；example.com在现网解析为198.18.x.x，符合现有fake-IP路径，不能称为直接获得公网A记录。`net.dns1`为空不代表Android13解析失败，实际解析/ping/HTTPS均已过。主机DNS文件没改。

继续阶段主机modprobe、sysrq、kptr_restrict、dmesg_restrict、panic_on_oops读回与启动前相同；没有全局关闭LSM/防火墙/审计。后两轮停止后routes/rules/nft/services逐项完全相等，DNS比较项为resolv.conf解析目标。收尾另外核验DNS文件完整内容SHA256与此前复位后/继续前基线完全相等，未把symlink比较冒充内容比较。最终global与wlan0 forwarding均0，新增标准服务仍masked，不开机自启。

## 复现与交付

- [运行手册](<../scripts/android-lab/README.md>)含冷恢复运行时、启动/停止/完整清理、单命令UI与Frida。
- [固定版本与校验清单](<../config/android-lab-r4-versions-20261112.json>)记录当前通过状态并保留旧失败历史。
- [71项真实证据归档](<../local/waydroid-r4-lab-20261112/real-android-acceptance-20261113.tar.gz>)，SHA256 `f7c643e3a49b0bf32230a94c213849b2960436b2f91aa66c1471a8f78adb594f`；已逐文件复核。
- [最终清理读回](<../local/waydroid-r4-lab-20261112/final-cleanup-20261113.json>)。本轮刻意按原验收要求停止Android并卸载实验运行时，不是未能启动。镜像、数据、APK与配置保留供手动复现。
- 原始native trace、kernel/pstore、任何原始设备标识、ADB/签名私钥、NV、音像均不进入分享归档；截图仅本轮自有测试应用，不是相机照片。

音频/传感器/GNSS/相机下一阶段仍遵守[依赖与保护边界](<android-lab-r4-reset-20261112.md#6-主闭环后的硬件依赖排序只读评估>)；没有把共享Pulse socket当Android音频已适配，没有开放无保护TFA2扬声器，也没有宣称任意第三方APK兼容。
