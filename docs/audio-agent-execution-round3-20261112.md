# 音频第三轮：两个喇叭都响了，UCM 打通（2026-11-12）

配套：[第一轮](audio-agent-execution-20261112.md)。仍 r4、未重启、未刷写；收尾无声卡、
`tfa2` 卸载、两功放 UNBOUND、黑名单 2 条、boot_id 不变。**本轮推翻前两轮的一个结论，
并把 UCM 从 partial 推到 pass。**

## 1. 结论更正：左声道是通的

前两轮把左声道判为 fail（−42 dBFS corr 0.0374、−36 dBFS corr −0.119）。本轮同样参数
（channel=left、250 ms、−36 dBFS、2 s 采集）在两次独立 run 得到 **|corr| = 0.9381 / 0.9322**：

| run | 动作 | corr | peak | rms |
|---|---|---|---|---|
| 1340z | probe-left | **0.9381** | −70.31 dBFS | −80.01 |
| 1355z | probe-left-1/2 | **−0.9322** | −69.48 dBFS | −78.51 |
| 1355z | probe-right-1 | **−0.9987** | −53.28 dBFS | −56.89 |

**为什么上一轮判错**：上一轮"左声道"窗口的峰值是 −59.18 / −65.2 dBFS，比本轮实测的真实音调
电平（−69.5 dBFS）**高 10~11 dB**——窗口里除了很弱的音调，还有约 10 dB 的无关能量（流启动
或上一次采集的尾巴），把相关系数压到 0 附近。参数完全相同 ⇒ **测量污染，不是配置差异**。
上一轮的"左声道 fail"作废，以本轮两次独立测量为准。

**左右差 16 dB 不是故障**：同一驱动电平下右 −53.3 / 左 −69.5 dBFS；两个功放寄存器配置只差
slot，增益是同一套硬编码值，16 dB 与"底部主扬声器 + 顶部听筒 + 麦在底部"的几何相符。

## 2. 驱动侧事实

`snd_soc_tfa2` 是本项目自己的驱动（`author: kebab audio port`、`sound/soc/codecs/tfa2.c`、
stage-1 DSP-bypass）。打开 `dynamic_debug` 后读到每个功放的真实配置：

```
tfa2 15-0034: tfa9874 rev 0xc74 ready (channel=0, stage-1 DSP-bypass, NO speaker protection)
tfa2 15-0035: tfa9874 rev 0xc74 ready (channel=1, stage-1 DSP-bypass, NO speaker protection)
tfa2 15-0034: hw_params: 2 ch 16-bit in 16-bit slots @ 48000 Hz -> 32 bclk/frame, FSBCLKS=0, slot 0
tfa2 15-0035: hw_params: 2 ch 16-bit in 16-bit slots @ 48000 Hz -> 32 bclk/frame, FSBCLKS=0, slot 1
```

两个功放**都在同一条 DAI link 上**且都被 `hw_params` 正确配置，只有 slot 不同；DT 里
`speaker-amp@34` = channel 0 / prefix Left、`speaker-amp@35` = channel 1 / prefix Right。
"左声道没挂上"这个方向从一开始就不成立。

## 3. UCM 根因（读 alsa-lib 源码定位）

前几轮卡在 `execute_sequence: cdev is not defined!`。alsa-lib `src/ucm/main.c`（master）里：

- 设备序列中 cset 的 `cdev` **不是从 `PlaybackPCM` 推的**，而是读 **`PlaybackCTL` / `CaptureCTL`**
  两个 Value；两者都缺 → `snd_error("cdev is not defined!")` 并 `-EINVAL`。
- 真实音频服务调用 UCM 时由调用方传入 card handle，所以系统里只写 `PlaybackPCM` 的 Qualcomm
  配置在 `alsaucm` 里跑不动——不是结构写错，是**独立 CLI 缺这层上下文**。

修法：`SectionVerb.Value` 与每个 `SectionDevice.Value` 补 `PlaybackCTL "hw:0"` /
`CaptureCTL "hw:0"`。另一个坑：`alsaucm` **每进程新状态**，单独跑 `_disdev` 是静默空操作，
必须 `-b -` 批处理把 `_verb` + `_enadev` + `_disdev` 放同一会话。

结果（1510z / 1520z）：

| 操作 | rc | 读回 |
|---|---|---|
| `_enadev Speaker` | 0 | `TERT_MI2S_RX Audio Mixer MultiMedia1 = on` |
| `_enadev Speaker` + `_disdev Speaker` | 0 | 同控制 = **off** |
| `_enadev Mic` | 0 | `MultiMedia2 Mixer = on`、`VA_AIF1_CAP DEC0 = on`、`VA DMIC MUX0 = 2` |
| `_disdev Mic` | 0 | 三项回到 off/off/ZERO |

`ucm-pair` 校验 `check_passed: true`，`result.json` 里 **`ucm: pass`**。

**已装进系统树** `/usr/share/alsa/ucm2/O8T/{O8T.conf,HiFi.conf}`（0644，只新增一个目录，
不动现有文件），**不需要** `ALSA_CONFIG_UCM2`：`env -u ALSA_CONFIG_UCM2 alsaucm -c O8T list _verbs`
→ `0: HiFi`。移除方式：`sudo rm -rf /usr/share/alsa/ucm2/O8T`。

## 4. 两个工程坑

1. **部署路径不一致**：`deploy-selftest.sh` 只铺 `/tmp/kebab-audio-selftest`，执行器实际跑
   `/usr/local/lib/kebab-audio/kebab_audio.py`。改了执行器只部署到 `/tmp` → 新代码（`seq`
   批处理）没生效，被误判成"批处理不管用"。现在固定加一步 `install -m 0700`。
2. **预算按实验独立**：4 个 UCM 动作 + `ucm-pair`(2) + `route-off`(1) 超了 `route_variant` 的 6
   上限（`run: budget refused: route_variant: used 6 + 1 > cap 6`）。执行器拒绝得对；做法是
   去掉冗余 `route-off`（守卫停声本来就会静音同样四项）。

## 5. 预算（每轮独立）

| run | 用途 | module_load | route_variant | playback | capture |
|---|---|---|---|---|---|
| 1330z | 读驱动配置 | 1/2 | 1/6 | 250 ms | 2 s |
| 1340z | 左右同电平对比 | 1/2 | 2/6 | 500 ms | 4 s |
| 1355z | 底噪 + 左×2 + 右 | 1/2 | 2/6 | 750 ms | 10 s |
| 1410z–1520z | UCM 排障与验收 | 各 1/2 | 5~6/6 | 0 | 0 |

## 6. 仍然不能声称

- 没有做出「左=顶部听筒、右=底部主扬」的物理对应；单麦无法分离声源，要闭合得靠near-field/双麦。
- 左比右低 16 dB 没有硬件定论：可能是几何，也可能是单元老化，没拆机/近场测量不下结论。
- `unattended_ready` 仍 **fail**：TFA2 DSP-bypass，喇叭没有行程/热保护。
- PipeWire 本机不存在，`pipewire` 仍 not_tested；UCM 只在 ALSA 层面验过。
