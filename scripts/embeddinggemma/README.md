# ONNX Q8 离线有界测试

需要Linux ARM64、Node20.20.2兼容运行时及systemd。脚本是功能和性能小样例，不是HTTP嵌入服务。不要在现有应用目录安装依赖。

1. 在/srv新建独立试验目录，将本目录的package.json、package-lock.json、benchmark.mjs复制进去。
2. 在该目录运行`npm ci --ignore-scripts --omit=dev`。核对Linux ARM64 ONNX原生库和sharp实际可加载。
3. 从[模型文件清单](../../config/embeddinggemma-q8-model-manifest.json)给出的固定Hugging Face提交下载列出的文件到models/，保留onnx/子目录；逐项核验大小与SHA-256。
4. 从[图片清单](../../config/embeddinggemma-q8-sample-manifest.json)的固定URL下载cats.jpg与corgi.jpg到assets/，核验SHA-256。这些文件只留本地。
5. 顺序运行文本和视觉短测，不并发。脚本关闭远程模型下载，文本模式禁用图像和音频编码器，视觉模式禁用音频。

例如测试目录为/srv/embeddinggemma-q8-trial，使用普通大核CPU4单线程和每请求5秒间隔。先检查设备状态与管理链路，再运行；用户及Node路径按实际安装替换：

```bash
sudo systemd-run --unit=embeddinggemma-q8-trial \
  --collect --wait --pipe --service-type=exec \
  --property=User=debian \
  --property=WorkingDirectory=/srv/embeddinggemma-q8-trial \
  --property=RuntimeMaxSec=90 --property=MemoryMax=3G \
  --property=Nice=10 --property=CPUAffinity=4 \
  --setenv=EG_PROFILE=short --setenv=EG_PAUSE_MS=5000 \
  --setenv=EG_VISION_TOKENS=70 \
  /usr/bin/node /srv/embeddinggemma-q8-trial/benchmark.mjs text
```

将末尾text改为vision可复验图像。停止使用`sudo systemctl stop embeddinggemma-q8-trial`。单元为临时单元，没有开机自启。

报告在试验目录results/text.json或results/vision.json，每次采样保存检查点；必须出现success:true才是整轮通过。验证768维、有限值、单位范数、中文文本检索、不同图像向量以及中英文跨模态检索。每次重测会覆盖报告，需保留历史时先备份。

电池41°C或有效热区65°C为本脚本自定停止条件，不能作为硬件安全标准或表面温度证明；本次不改系统热策略。达到时间/内存限额或主动停止时，报告可能不完整。延迟排除主动等待，RSS与cgroup内存应分别报告。

实测摘要见[试跑报告](../../docs/embeddinggemma-q8.md)。源码最终释放模型；请勿由几条成功样例推断完整基准成绩或长期服务就绪。
