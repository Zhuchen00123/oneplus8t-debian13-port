# r4 Android13实验运行手册（2026-11-13实机通过软件闭环）

[当前实测报告](<../../docs/android-lab-r4-boot-20261113.md>)：3个完整启停周期、8次真实UI闭环、Frida3次、FD650硬件GLES、180秒在线采样。当前按原要求已清理停止，**不是开机自启环境**。上一阶段意外复位与仍未恢复的modem故障见[历史事故](<../../docs/android-lab-r4-reset-20261112.md>)，不得写成所有服务器从未受影响。

## 文件与职责

- [launcher](<waydroid_lab_launcher.py>)：仅进程内适配审计过的Waydroid1.6.3，传统Binder、精确字符设备名单、只允许kmsg节点创建而禁止读写、独立受限payload scope、禁物理音频/相机/硬件桥接。特权实验环境，不是任意恶意APK的安全沙箱。
- [network](<waydroid_lab_network.py>)：拥有bridge/网段/精确nft表与策略表5240、独立dnsmasq，from-subnet规则必须附iif=waydroid0。只租每接口forwarding，不改main默认路由、宿主DNS或全局ip_forward、不flush规则。
- [controller](<lab_control.py>)：资源门槛、真正boot+ADB检查、每次启动节点权限租约、身份核验恢复、失败清理、停止后缓存失效。HOLD存在即拒绝启动；本次用户追加授权后已保全并解除旧HOLD。
- [agent](<agent_loop.py>) / [Frida](<frida_loop.py>)：只针对自有包，不固定tap坐标，不复用旧boot或已停止的ADB缓存。
- [APK构建器](<build_test_apk.py>)与[源码](<test-app/src/org/oneplus8t/agentlab/MainActivity.java>)：零权限、自有签名，无第三方APK、账号或外部服务。私钥不入Git/归档。
- [指标](<metrics.py>)采集真正payload scope；require-android模式若触发温度/内存阈值，停止本轮实验，不改原服务。[基线对账](<compare_baseline.py>)未知iptables工具不当作成功，保护基线不符返回非零。
- [23项离线回归](<test_lab_control.py>)及[定位/状态](<test_agent_loop.py>)、[规则选择器](<test_network_policy.py>)。

## 首次最小安装原则

**当前已安装初始化，不要再init -f或清空data。** 固定版本/来源/双端SHA见[清单](<../../config/android-lab-r4-versions-20261112.json>)。

1. 保留r4，核/srv为实际ext4数据盘、有足够空间；真实Binder三个节点与ioctl协议8。禁止mknod伪造Binder或混装别版模块。
2. 本轮通过独立签名file:仓库安装32个新包、0升级/删除；未改全局apt源。安装前只mask新增Waydroid/LXC单元，仅在安装窗口用自己创建的policy-rc.d拒绝这些新服务；不覆盖已有policy或mask既有服务。
3. 官方arm64 VANILLA / MAINLINE ZIP先核OTA SHA/长度，设备再复核，只提取system.img/vendor.img到专用数据盘images。大镜像/data不落系统根。
4. 将本目录Python脚本部署到实验bin；[logd覆盖](<../../config/android-lab/logd-container.rc>)部署到实验overrides/logd.rc，禁宿主kernel/audit访问；配置ro.logd.kernel=false与ro.logd.auditd=false。
5. 在自有state临时bind后用launcher及本地已验镜像初始化；不在线OTA、不建fstab、自启动或替换宿主桌面。源码生成器现包含relative cgroup、硬限制、精确设备及logd只读覆盖；原始system/vendor镜像不修改。
6. [manager单元](<../../config/android-lab/kebab-waydroid-r4-lab.service>)、[compositor单元](<../../config/android-lab/kebab-waydroid-r4-compositor.service>)均手动放/run。payload由launcher创建独立scope，不能把manager的上限当payload已受限。
7. [专用Weston配置](<../../config/android-lab/weston.ini>)必须panel-position=none，避免1280与1248尺寸协议错误。只bind自己的单个Wayland socket到/srv私有runtime，不暴露整个/run/user、bus、家目录或凭据。

## 已安装环境：从最终清理态恢复运行时

下列模板已留在设备实验config目录；运行前核没有别人的Waydroid实例/冲突网段，ROOT的state/images/config均为本轮所有。下面每个命令目标固定，**不对/srv本身umount**。

```sh
sudo mount --bind /srv/waydroid-r4-lab-20261112/state /var/lib/waydroid
sudo install -m 644 /srv/waydroid-r4-lab-20261112/config/kebab-waydroid-r4-lab.service /run/systemd/system/
sudo install -m 644 /srv/waydroid-r4-lab-20261112/config/kebab-waydroid-r4-compositor.service /run/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start kebab-waydroid-r4-compositor.service
sudo mount --bind /run/user/1000/waydroid-lab /srv/waydroid-r4-user-20261112/runtime/waydroid-lab
sudo python3 /srv/waydroid-r4-lab-20261112/bin/lab_control.py start
```

`start`要求socket确为socket、镜像已初始化；真正sys.boot_completed=1后才配置**实验自有公钥**并验证ADB get-state=device，缓存只在同一host boot且未stop时有效。Root ADB server独立回环5038，鉴权不关闭，不借用户旧key。资源3GiB/CPU200%/pids2048，单元/ scope各1800秒上限。运行时过期或任何启动错误，不跳过安全断言。

## 一条命令执行三轮真实UI闭环

```sh
sudo python3 /srv/waydroid-r4-lab-20261112/bin/agent_loop.py --rounds 3
```

默认从本轮成功状态读取真实DHCP地址；不硬编码112，不复用停止态。使用自有build2 APK，自动新建UUID结果目录。按package+resource-id读当前XML，输入UUIDtoken，读回，点击Apply，断言OK结果，记录PNG/XML/命令/返回码/时间。显式--serial/--apk/--out仅供已确认环境的高级复现，目录不得覆盖旧结果。

## 一条命令执行真实Frida关联与清理

```sh
sudo env PYTHONPATH=/srv/waydroid-r4-lab-20261112/tools/site python3 /srv/waydroid-r4-lab-20261112/bin/frida_loop.py
```

client/server均16.7.19，server仅Android回环监听；精确自有App PID attach、hook ready后才触发相同UI，核唯一方法调用、入参/返回值与时间窗。finally卸载hook/detach、验证并停止自有server、force-stop自有App、删除自己分配的forward，核无残留。已实测3次，不外推任意第三方App。

## 采样、停止与完整清理

```sh
sudo python3 /srv/waydroid-r4-downloads-20261112/metrics.py --seconds 180 --interval 5 --require-android --out /srv/waydroid-r4-lab-20261112/runs/soak-NEW-UNIQUE
sudo python3 /srv/waydroid-r4-lab-20261112/bin/lab_control.py stop
```

NEW-UNIQUE必须换成未存在目录；这不是自动复写采样结果。stop会停止自有session/manager/payload scope、验证LXC STOPPED，再清理网络、恢复节点mode/ACL并停止私有ADB。跨boot不重放旧ACL，不能killall/全局flush/rmmod其它用户模块。

完整收尾还需在确认Android已停、socket alias/state bind确属自己后：

```sh
sudo umount /srv/waydroid-r4-user-20261112/runtime/waydroid-lab
sudo systemctl stop kebab-waydroid-r4-compositor.service
sudo umount /var/lib/waydroid
```

仅删除本轮两个/run单元文件并daemon-reload（先核准确路径、不是symlink、内容是本轮ExecStart）；不删除/etc已有单元或/srv数据。本轮已完整执行并回读全部10节点及规则/端口/挂载还原。标准Waydroid/LXC服务保持masked。

## 回退、数据与隐私

默认保留包、校验镜像/data、已签自有APK和所有旧结果，方便复用/取证。完整卸载需另审32包清单与逆依赖，不直接apt autoremove，不碰原有未配置-dev包。原始native trace/kernel/pstore和设备标识只留设备；分享包只含自有App截图/层级、限定命令与脱敏摘要。不挂音频/相机/传感器/GNSS，没有共享Pulse绕过保护。

```sh
python3 -m unittest discover -s scripts/android-lab -p 'test_*.py' -v
python3 -m compileall -q scripts/android-lab
```

离线23项通过；真实UI/Frida/启动证据单列，绝不把离线测试当实机通过。
