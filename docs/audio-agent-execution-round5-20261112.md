# 音频第五轮：采集侧接进桌面栈，双向端到端验通（2026-11-12）

配套：[第三轮](audio-agent-execution-round3-20261112.md)（左声道与 UCM）、
[第四轮](audio-agent-execution-round4-20261112.md)（桌面栈与播放）。
仍 r4、未重启未刷写；收尾无声卡、`tfa2` 卸载、两功放 UNBOUND。

## 1. 这轮补的是什么

第四轮只让**播放**走了 PipeWire。采集侧走 ACP 同样会拿到 8 声道 `S24_32` 节点
（本卡只有 1 声道 DMIC 通路），所以 `kebab-audio-on` 现在**同时**发布一个 source：

    pw-cli create-node adapter '{ factory.name=api.alsa.pcm.source node.name=o8t-speaker-mic \
      node.description="OnePlus 8T Mic" api.alsa.path="hw:0,1" \
      audio.channels=1 audio.format=S16LE audio.rate=48000 \
      media.class=Audio/Source object.linger=true }'

`kebab-audio-off` 改成按 `o8t-` 前缀匹配，sink 与 source 一起销毁。

## 2. 端到端验证（run 20261112t2245z）

同一个窗口里 `pw-play --target o8t-speaker` 播放与 `pw-record --target o8t-mic` 录音同时跑，
两路都在 PipeWire 图里（`pw-top` 显示 `o8t-speaker` 与 `o8t-speaker-mic` 两个节点）。
采集逐 50 ms 窗口：

| t(s) | rms_dB | 1k_amp | 1k_dB |
|---|---|---|---|
| 0.80 | −83.9 | 0.0 | −118.0 |
| 0.85 | −84.2 | 0.0 | −139.3 |
| **0.90** | **−79.6** | **1.6** | **−86.2** |
| **0.95** | **−77.1** | **2.9** | **−81.2** |
| **1.00** | **−77.0** | **2.9** | **−81.1** |
| **1.05** | **−77.3** | **2.8** | **−81.4** |
| **1.10** | **−77.9** | **2.5** | **−82.3** |
| 1.15 | −83.4 | 0.1 | −108.3 |

底噪约 −84 dB，音调段 −77 dB、1 kHz 频点 −81 dB ⇒ **PipeWire 播放 → DAI → 功放 → 喇叭 →
空气 → DMIC → PipeWire 采集**全链路通。

## 3. 判据提醒：这条路径上不要看 `corr`

这一枪执行器给出的 `corr` 只有 **0.0267**，**但声音是有的**。原因是 PipeWire 建节点比 `aplay`
慢约 1 s，音调落在采集的 0.90–1.10 s，而执行器的相关搜索按参考音起点对齐（参考音里
`lead_ms=600` 的前导静音已被裁掉），对不上就退化成噪声。

- PipeWire 路径的判据：`analyze-capture.py` 的逐 50 ms 窗口表（上表）。
- ALSA 直连路径的 `corr` 仍然可用：同窗口 A/B 实测 `aplay` **−0.9388**、`pw-play`（8 声道节点）−0.0601。

后续若要统一判据，应把执行器的相关搜索改成全范围 + 报告最佳 lag/峰值，而不是按起点固定对齐。

## 4. 这轮修的三个真 bug

1. **sink 创建竞态**：`modprobe` 之后立刻 `pw-cli create-node`，约一半概率节点不出现在图里，
   而 `pw-cli` 的报错被 `2>&1 >/dev/null` 吞掉了，表现为「audio-on 说没有 sink」。
   现在 `make_node()`：先查 `pw-cli ls Node` 是否已有该名字 → 没有则创建 → **轮询确认节点真的存在**
   （最多 5 次、间隔 0.4 s）→ 失败时打印 `pw-cli` 的原话；UCM 路由之后再 `sleep 0.3` 让卡落地。
2. **`kebab-audio-off` 漏删节点**：原来只精确匹配 `node.name = "o8t-speaker"`，后加的
   `o8t-speaker-mic` 不在名单里。改按 `o8t-` 前缀匹配。
3. **执行器缺 `recorder`**：probe 动作原本只能指定自己的播放器（`player`），录音固定是
   `arecord -D <capture_pcm>`。现在与 `player` 对称地支持 `recorder` / `recorder_env`，
   但仍然受 `capture_seconds` 预算约束——到点 `terminate`（实测 2 s 预算得到 1.88 s 文件），
   录音（哪怕是 `pw-record` 这种不会自己退出的客户端）不能被放开来跑。

## 5. 现在能说什么

- 扬声器**能出声**、麦克风**能收音**，两条路径（裸 ALSA 与 PipeWire 桌面栈）都有声学证据。
- 一条命令开、一条命令关；关完断言 `--- no soundcards ---` + `tfa2 unloaded` + 两功放 UNBOUND。
- 多次 on/off 循环都回到干净状态（本轮又验了两轮）。

## 6. 仍然不能说的

- **`unattended_ready` 保持 fail**：功放跑 stage-1 DSP-bypass，没有行程/热保护。
  本轮验的是「短探针能出声」，不是「可以无人值守长时间大音量播放」。
- **开机不自启**（有意）：黑名单与 server profile 不变，要用时显式 `kebab-audio-on`。
- **左右换能器的物理对应仍未定论**（单麦无法分离），需要人耳或第二支麦克风。
- ACP 的 8 声道是上游行为，我们只是绕开；WirePlumber 0.5.8 二进制里没有任何 UCM 字符串，
  它不传 `api.alsa.use-ucm`——这两条都值得整理成上游 issue。
- 3.5 mm 耳机口、蓝牙、USB 音频本轮与之前都没验过。
