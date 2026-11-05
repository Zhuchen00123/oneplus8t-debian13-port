# OnePlus 8T GPU 逐项验证（2026-11-05）

> 本文为18:11–18:29原r3阶段的GPU验证快照。随后r4与同机OEM固件已正式部署并通过FFmpeg硬解短测，见[Venus正式结果](venus-video-decode-20261105.md)；Firefox视频硬解仍未通过实测。

## 结论

用户明确要求逐项验证后，主代理在独立测试显示和独立 Firefox profile 中完成了短时功能检查。**GPU 基础绘图、TigerVNC DRI3、Firefox 硬件 WebRender、WebGL 均通过；现有内核与用户态栈不具备可用的视频硬解链路。**

本轮没有再次整机重启，没有改动正式桌面配置、重启正式桌面、加载新内核模块、调整充电策略、修改网络或部署公网入口。正式桌面仍维持此前的软件渲染配置；本轮目标是验证能力，并非自动切换正在使用的会话。短测通过不等于长期稳定，也没有确定上午意外重启的根因。

## 环境与隔离

- 测试约在北京时间18:11–18:29进行，boot始终为 `eb8b7a4b-a689-444b-b6aa-99ba2b45ae95`。
- 内核 `7.2.0-kebab-debian13-r3-slider+`，Mesa25.0.7，TigerVNC1.15，Firefox ESR153.4。
- 正式桌面`:1`/5901/PID43460保持运行。独立测试用`:2`/1280×720，仅开放0700运行目录内的0600 RFB Unix socket；没有新增公网或LAN监听端口。
- 测试进程为普通debian用户，仅允许真实GPU render node，未接管物理DRM显示。Firefox使用全新独立profile、PrivateNetwork、CPUQuota200%和MemoryMax1536MiB；单组最长105秒，实际约19秒后退出。
- 在触及GPU之前，备份正式service、启动脚本、浏览器wrapper与软件profile设置至手机专用baseline目录，并核SHA；原pstore也核SHA，未清除。所有新增内容位于独立测试目录及新诊断脚本。
- 已安装的Mesa工具带架构后缀，例如 `glxinfo.aarch64-linux-gnu`。本轮未为此安装或升级软件包。

## 逐项结果

| 项目 | 实际证据 | 判定 |
| --- | --- | --- |
| 软件对照 | 相同GLES探针返回llvmpipe，三种shader输出的64像素逐项匹配 | 对照有效 |
| GPU基础绘图 | 显式GBM renderD128，vendor=freedreno、renderer=FD650、GLES3.2；三种shader颜色回读正确 | **通过** |
| GPU实际执行 | 探针自身DRM fdinfo：driver=msm，drm-engine-gpu=56144ns、cycles=32990，非仅枚举设备 | **通过** |
| Xtigervnc DRI3 | 独立`:2`的glxinfo返回direct rendering Yes、Accelerated yes、FD650，GL4.6/GLES3.2 | **通过** |
| GPU画面捕获与RFB传输 | FD650绘制小齿轮，X根窗口截图可见彩色画面；真实Windows客户端经SSH连接私有socket，VncAuth成功并传输171次framebuffer更新 | **通过短测** |
| Firefox软件对照 | 实际诊断为WebRender (Software)，HW_COMPOSITING disabled | 对照有效 |
| Firefox硬件合成 | 仅改layers.acceleration.disabled为false，实际变成WebRender，HW_COMPOSITING/OPENGL_COMPOSITING/WEBRENDER均available | **通过短测** |
| WebGL | 再仅改webgl.disabled为false；特权诊断中的WebGL1/2 renderer均为freedreno -- FD650，网页WebGL1像素为[51,102,153,255]、GL error0 | **通过短测** |
| 视频硬解 | 再仅改硬解pref为true；Firefox实际报告unavailable/VIDEO_DECODING_TEST_FAILED，运行内核未启用QCOM_VENUS | **当前栈不支持** |

基础shader回读分别为 `[255,0,0,255]`、`[0,255,0,255]`、`[51,102,153,255]`，每组64像素均符合预期，不以设备文件存在或版本字符串替代绘图结果。

WebGL网页接口的renderer被隐私机制泛化为 `Generic Renderer`；用与about:support同源的特权Troubleshoot数据确认实际FD650。WebGL2完成了context创建和renderer查询，但本轮独立网页像素回读只执行WebGL1，未把二者写成同样的绘图覆盖。

## Firefox 单变量与判断方法

所有组的 `gfx.webrender.software=false`，不强制绕过blocklist；使用相同本地HTML轻量动画页。完整重启每个独立profile：

| 相邻组 | 唯一变化的pref |
| --- | --- |
| software → compositor | layers.acceleration.disabled：true→false |
| compositor → webgl | webgl.disabled：true→false |
| webgl → video | media.hardware-video-decoding.enabled：false→true |

结果文件中的prefs已机械比对，三组均只有上述单项变化。各组新profile带来冷启动/编译差异，故这不是严格的性能基准。

通过Marionette读取 `Troubleshoot.snapshot().graphics`，同源于about:support，未凭pref要求值推断实际加速。软件组的 `numAcceleratedWindows` 同样为1，**该计数不能单独判GPU成功**。以实际 `windowLayerManagerType`、FD650 renderer和featureLog综合判定。[Mozilla实现](https://raw.githubusercontent.com/mozilla-firefox/firefox/main/toolkit/modules/Troubleshoot.sys.mjs)、[about:support实现](https://raw.githubusercontent.com/mozilla-firefox/firefox/main/toolkit/content/aboutSupport.js)。

所有GPU组的 `WEBRENDER_COMPOSITOR` 原生合成优化仍为blocklisted；它与基础硬件WebRender是不同条目。本轮没有强开该优化、gfx.webrender.all或layers.acceleration.force-enabled。

使用Firefox153实际支持的 `--marionette --remote-allow-system-access`，控制口位于试验进程独立网络命名空间的回环上，只控制新建测试profile，未对外暴露或连接正式浏览器。保留完整graphics JSON，报告不使用虚拟显示中不可靠的PCI vendor/device ID作物理身份依据。

## 视频硬解缺口

运行内核自身的 `/proc/config.gz` 明确为：

```text
# CONFIG_VIDEO_QCOM_VENUS is not set
# CONFIG_VIDEO_QCOM_IRIS is not set
```

v4l2-ctl列出的video0–13均为Qualcomm Camera Subsystem，不是解码器；没有Venus驱动模块/运行节点。用户态也没有libva和libavcodec的可用动态库条目。

硬解开关打开后，Firefox真实报告 `HARDWARE_VIDEO_DECODING=unavailable`，错误为 `FEATURE_FAILURE_VIDEO_DECODING_TEST_FAILED`。codecSupportInfo为H264 NONE、HEVC NONE，VP8/VP9/AV1 SWDEC，未报告任何HWDEC。这是现有栈的诊断结果，不等于SM8250芯片没有解码硬件。

本轮没有播放样片或做真实硬解中断/帧统计，不能声称硬解“跑过后崩溃”或“播放验证通过”。要补齐该功能，需另做Venus内核/设备树/固件及FFmpeg或VA相关用户态路径工作，然后用真实视频、decoder日志和硬件活动验证。Firefox存在“about:support报告支持但Venus未真正工作”的历史案例，不能仅看开关或支持表判通过。[Linux解码器API](https://docs.kernel.org/userspace-api/media/v4l/dev-decoder.html)、[Mozilla Venus案例](https://bugzilla.mozilla.org/show_bug.cgi?id=1852765)。

Adreno图形加速、Venus视频解码、VNC视频编码是三件事。此次GPU绘图通过不表示已经有硬解或H.264远程桌面编码器。

## 负载与观察边界

四组Firefox运行分别约18.799/18.987/19.155/19.913秒，记录CPU时间11.558/11.842/12.314/12.216秒，内存峰值约402/510.1/507.6/508.4MiB。含首次启动和诊断，**不能据此比较真实网页的GPU收益**，也不能与正式桌面当前页面资源直接比较。

取得218条独立采样，USB始终online1、停充owned1、last_error0；电池37.1–37.3°C，所采CPU/GPU传感器最高59.7°C。采样包含各组前后空闲窗口，浏览器没有持续负载120秒；额外齿轮短测的完整瞬态不在上述连续采样覆盖内。

齿轮使用320×240小窗口、有界18秒。虽然设置vblank_mode，虚拟显示没有按预期限制渲染速率，日志出现约3000FPS；**这是API小场景绘制速率，绝不是1080p远程桌面FPS**。RFB记录171次协议更新也不能直接当显示帧数。当前未测真实持续1080p帧率或端到端输入延迟。

全程未再次重启；结束时kernel查询窗口无新日志，原pstore SHA保持。多个条件与上午事故不同，不能据此证明之前一定是GPU、供电或其它原因。

## 证据与收尾

手机目录 `/srv/kebab-gpu-validation-20261105`，电脑目录 `local/gpu-validation-20261105`。四组结果、五份采样、逐单元journal和GPU截图共22个artifact双端SHA256全部匹配；manifest与本地核验回执均保留。

辅助源码：`kebab-egl-render-probe.py`、`kebab-gpu-test-xserver.sh`、`kebab-firefox-gpu-probe.py`、`kebab-gpu-trial-observer.py`，位于scripts/linux。所有测试单位有独立名称和时间/资源上限。第一次Xvnc未配置任何RFB端点而拒绝启动，补为私有Unix socket后成功；证据收集第一次误用Path.chown，在清理前失败，修正为os.chown后完整执行。失败记录未覆盖，未把它们计为GPU故障。

仅停止本轮列出的GPU测试单位，保留其数据；临时客户端/SSH进程只结束本轮创建者。收尾无failed，正式桌面PID43460、DSH236037、酒馆748、NapCat747、三档877与本轮起点保持。正式service、启动脚本、浏览器wrapper及user.js五文件SHA前后完全一致；原pstore SHA仍为81983d81…。

因此当前可作的结论是：**这套栈具备可工作的GPU绘图路径，视频硬解尚缺栈支持。** 正式桌面继续维持原配置；若后续切换GPU，应保留CPU回退，安排会话重启，不直接关闭现有应用，也不把短测升级为长期稳定承诺。
