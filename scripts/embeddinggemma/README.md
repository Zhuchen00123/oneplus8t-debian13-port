# EmbeddingGemma 2 ONNX Q8 有界试跑

本目录只提供离线、合成文本与公开图像的小样例验收，不安装常驻嵌入服务，不修改酒馆提供商或旧向量库。来源和文件哈希见项目 `sources.lock.json` 与 `config/embeddinggemma-q8-*-manifest.json`。

手机独立环境：`/srv/embeddinggemma-q8-trial-20261007`。目录中的 `runtime` 为 Node 20.20.2 的独立副本，NPM 依赖固定在本目录的 package-lock。模型全部放在 `models/`；图片放在 `assets/`。模型推理关闭远程下载，音频编码器不加载。

后续重新准备时，仅在新目录内运行 `npm ci --ignore-scripts --omit=dev`。安装后核验本机 Linux ARM64 ONNX 原生库和 sharp 可加载；不要在酒馆目录安装依赖。

## 推荐复验命令

先用 `ssh kebab` 登录手机，确认当前没有局部烫手、`/srv` 已挂载、既有服务正常，再运行下面一个命令。两种模式顺序运行，不并发；示例以普通大核 CPU4、单线程、每请求间隔5秒降低短测负载。

```bash
sudo systemd-run --unit=kebab-embeddinggemma-q8-retest \
  --collect --wait --pipe --service-type=exec \
  --property=User=debian \
  --property=WorkingDirectory=/srv/embeddinggemma-q8-trial-20261007 \
  --property=RuntimeMaxSec=90 --property=MemoryMax=3G \
  --property=Nice=10 --property=CPUAffinity=4 \
  --setenv=EG_PROFILE=short --setenv=EG_PAUSE_MS=5000 \
  --setenv=EG_VISION_TOKENS=70 \
  /srv/embeddinggemma-q8-trial-20261007/runtime/bin/node \
  /srv/embeddinggemma-q8-trial-20261007/benchmark.mjs text
```

将末尾 `text` 改为 `vision` 可复验图片。新测试覆盖 `results/text.json` 或 `results/vision.json`，需要留历史时先在本试验目录备份这两个报告。

脚本会检查768维输出、有限值、单位范数、中文文本检索，以及视觉模式下不同图片的向量和中英文跨模态检索。每次采样和结束都会写入报告，未出现 `success: true` 不能视作全部验收通过。模型最后调用 dispose；达到电池41°C或有效热区65°C的自定短测停止阈值时终止，不修改系统热策略。这些阈值和传感器采样不是表面温度或长期安全认证。

延迟统计不包含主动等待间隔；需分清预处理、推理、模型加载和总请求时间。峰值RSS与systemd cgroup内存的共享页计量不同，不应混为同一指标。样例仅为功能冒烟，不能据此宣称中文检索质量或多模态基准成绩已复现。

## 停止与保留

运行时可用 `sudo systemctl stop kebab-embeddinggemma-q8-retest` 结束该轮。测试单元为临时单元，没有enabled、自启、监听端口或公网入口。试验目录保留供复验，不删除既有模型、聊天或备份。
