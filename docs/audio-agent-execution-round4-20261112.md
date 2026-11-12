# 音频第四轮：桌面音频栈（PipeWire）打通（2026-11-12）

配套：[第三轮](audio-agent-execution-round3-20261112.md)。仍 r4、未重启未刷写；收尾无声卡、`tfa2` 卸载、两功放 UNBOUND。
本轮把音频从「ALSA 能出声」推进到「PipeWire 桌面栈能出声，并且一条命令开关」。

## 1. 装了什么

设备原本只有 `alsa-utils`，没有任何声音服务（`xfce4-pulseaudio-plugin` 是空壳）。DNS 仍走不通
（默认路由指向坏的 usb0），用之前为 ModemManager 写的 `fetch-debs-kebab.py`
（`curl --interface wlan0 --resolve` 按 IP 直连）拉了 23 个包：

- `pipewire 1.4.2` + `pipewire-bin` + `pipewire-alsa` + `pipewire-pulse`
- `wireplumber 0.5.8`
- `dbus-user-session`（**没有它，用户会话没有 D-Bus，`pipewire.service` 直接起不来**：
  `Unit dbus.service not found`，`/run/user/1000/bus` 不存在）

现在 `pipewire`/`wireplumber` 在 `/run/user/1000` 下 active，root 也能连（socket 是 `srw-rw-rw-`）。

## 2. 两个根因（都不是「驱动不支持」）

### 2.1 PCM 打不开：DAPM 后端没通电

路由没开的时候 `aplay -D hw:0,0 --dump-hw-params` 直接 `audio open error: Invalid argument`。
用 `ctypes` 逐个试 `SND_PCM_NO_AUTO_RESAMPLE/CHANNELS/FORMAT`、`NONBLOCK`、`SOFTVOL` 全部 `-22`，
**与打开标志无关**。用 UCM 把 speaker 设备使能（`TERT_MI2S_RX Audio Mixer MultiMedia1 = on`）
之后立刻正常：

    HW Params of device hw:0,0:
    ACCESS: MMAP_INTERLEAVED RW_INTERLEAVED
    FORMAT: S16_LE S24_LE   SAMPLE_BITS: [16 32]   RATE: 48000

=> PipeWire 起设备时若路由未通电，看到的是一张「打不开的卡」，于是一个 sink 都建不出来
（早先的 `spa.alsa: 'hw:0,0': playback open failed: Invalid argument` 就是这个）。
**顺序必须是：先 UCM 使能路由，再让 PipeWire 开会话。**

### 2.2 开了也静音：WirePlumber 的 ACP 设备是 8 声道

路由打通、sink 建出来、`pw-play` 返回 0、`/proc/asound/card0/pcm0p/sub0/status` 显示
**RUNNING 持续 2.5 s**，功放侧 `hw_params` 也与 aplay 完全一致
（`2 ch 16-bit in 16-bit slots @ 48000 Hz -> 32 bclk/frame, slot 0/1`）——**但麦克风里什么都没有**。
同窗口 A/B：`aplay` corr **-0.9388**，`pw-play` corr **-0.0601**。

`pw-top` 一句话点破：

    R  63  1024  48000  ...  S24_32 8 48000  alsa_output.platform-sound.pro-output-0
    R  79  4800  48000  ...  S16LE  2 48000   + pw-play

ACP 的 **pro-audio** profile 给这张卡建的是 **8 声道 S24_32** 节点（这张卡只有 2 声道 DAI），
2 声道数据在 8 声道帧里对不上，出来就是静音。

**解法**：绕开 profile，用 `pw-cli create-node` 造一个 2 声道 S16 的 ALSA sink。三个坑：
`object.linger=true` 少了节点会在 pw-cli 退出时被销毁；`api.alsa.path` 的值要带引号，
否则 `hw:0,0` 里的冒号让属性解析器报 `Expected object key`；description 里有空格必须转义。

这一改，同一发探针 corr 从 **0.06 -> 0.6184**，采集里 1 kHz 出现在 **1.00-1.20 s**（-79 dB），
与参考音实际到达时间相符（PipeWire 起节点比 aplay 慢约 1 s，所以参考音加了 600 ms 前导静音）。

## 3. 交付物：一条命令开关

`/usr/local/sbin/kebab-audio-on`（仓库 `scripts/audio/kebab-audio-on.sh`）：

1. `modprobe snd_soc_tfa2`（黑名单只挡 udev，显式加载可用）
2. `alsaucm -c O8T -b -`：`_verb HiFi` + `_enadev Speaker` + `_enadev Mic`，读回 TERT 断言
3. `pw-cli create-node` 那个 2ch/S16LE sink，`wpctl set-default`
4. 打印状态与「无行程保护」警告

`/usr/local/sbin/kebab-audio-off` 逆序撤销并**校验**：

    audio-off: destroyed node 64 (o8t-speaker)
    audio-off: device 72 profile off
    audio-off: route:   Mono: Playback [off]
    audio-off: unbound 15-0034 / 15-0035
    audio-off: snd_soc_tfa2 unloaded
    audio-off: card O8T gone

**三次 on/off 循环**都回到 `--- no soundcards ---` + `tfa2 unloaded`。

## 4. 又踩到的四个坑（都已修）

1. **`alsaucm` 每进程无状态**：`_disdev` 前必须在**同一会话**里 `_enadev`，否则静默空操作
   （`route: [on]` 但其实没关）。与第三轮同一个坑，这次在 off 脚本里复发。
2. **`rmmod` 要先把两个功放 unbind**：只 rmmod 会因 codec 绑定 EBUSY 而失败，
   且脚本若「无条件打印成功」就会留下卡在位的假干净（第一版就是这样）。
3. **部署路径**：脚本必须先 `install -m 0755` 到 `/usr/local/sbin`，否则跑的是旧版。
4. **工具层波动**：本轮 `git_bash` 从工具目录里消失，改用 `pwsh` 调 `ssh`/`scp`；
   `write`/`edit` 也出现过 `ReplaceFileW EIO`，改用 Node `fs` 直接读写绕过。

## 5. 证据与预算

| run | 用途 | module | route | playback | capture |
|---|---|---|---|---|---|
| 1830z | 路由前 PCM 打不开 | 1 | 1 | 0 | 0 |
| 1845z | UCM 使能后 PCM 可开 + sink 出现 | 1 | 1 | 250 ms | 2 s |
| 1900z-2035z | 8 声道排查（含 A/B、pw-top） | 各 1 | 各 1 | 各 250 ms | 各 2 s |
| 2145z | 2ch 显式 sink 出声 | 1 | 1 | 250 ms | 2 s |

本轮播放能量合计约 2.75 s 的 250 ms 短探针（每次单独计费），全部走执行器 probe 动作，
写前有守卫租约、写后有读回。

## 6. 还没闭合的

- **采集侧同样问题**：`pw-record` 走 ACP 也会拿到 8 声道节点，尚未验证/修正；ALSA `arecord -D hw:0,1` 是好的。
- **ACP 的 8 声道是上游行为**，我们只是绕开；值得整理成 issue 反馈。
- **UCM 没被 WirePlumber 用上**：`ucm2/conf.d/sm8250/OnePlus 8T.conf` 已让 `alsaucm -c hw:0` 正确解析到 HiFi，
  但 WirePlumber 0.5.8 的二进制里没有任何 UCM 字符串，它根本不传 `api.alsa.use-ucm`。
  这是「为什么需要手工建 sink」的上游原因。
- **开机不自启**（有意）：黑名单与 server profile 保持不变，音频要显式 `kebab-audio-on`。
- 左右换能器的物理对应仍未定论（单麦无法分离）。

## 7. 采集侧

`pw-record` 走 ACP 会拿到同样的 8 声道节点。采集侧的解法、三个真 bug 与端到端验证
记在[第五轮记录](audio-agent-execution-round5-20261112.md)；同一命令 `kebab-audio-on`
现在同时发布 2ch S16 sink 与 1ch S16 source。
