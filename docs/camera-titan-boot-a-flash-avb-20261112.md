# boot_a 刷写事故复盘：ABL「系统文件被损坏」的两种成因与安全刷写规则

> 事故：2026-11-11 22:28（CST）刷 `boot_a` 后设备无法启动，ABL 报「系统文件已损坏」。
> 22:30 用 known-good 回滚，22:31 恢复 ssh，全程无数据损失，boot_b/dtbo/super/misc 未触碰。
> 结论：**本次失败的直接成因尚未唯一确定**；本文给出已证的证据、两个候选机制、可判别实验，
> 以及无论哪个机制成立都必须遵守的安全刷写规则（已写成检查表）。

---

## 0. 先说结论（三层，按确定性排序）

| 层级 | 内容 | 确定性 |
| --- | --- | --- |
| 已证事实 | 本项目历史上**每一次能启动的部署，都只写镜像本身（45.6–47.9 MB），分区后 52.8 MB 从不触碰** | 高（代码+实测双证） |
| 已证事实 | 本次我**补零写到满分区 96 MiB**，把这段抹成 0 | 高 |
| 候选机制 A | 尾部含分区引导链所需的 AVB 结构（内嵌 vbmeta@60,076,032 + footer@100,663,232），抹掉即断链 | 中（结构存在已证，ABL 是否强依赖未证） |
| 候选机制 B | ABL 对「内容/启动完成度」的判定（同款提示在 2026-10-04 出现过一次，那次镜像同尺寸、dd 写法、尾部未动，**原因至今未查明**） | 中（有同症状历史先例） |
| 建议 | **不补零、只写镜像字节**；先把 A/B 判别实验做了再谈持久化 | — |

**唯一能一锤定音的实验**：把**同一份未补零的 `boot-g4.img`（47,869,952 B）**刷进去看能否启动。
- 能启动 → 机制 A 成立（补零是元凶），安全刷写规则确认为「只写镜像」。
- 仍失败 → 机制 B 成立（与尾部无关），需要另找 ABL 的判定条件；此时说明「历史能启动」另有原因。

---

## 1. 事故时间线

| 时间 | 事件 |
| --- | --- |
| 22:22 | `boot-g4.img`（47,869,952 B）用 `fastboot boot` 启动成功，节点出现、无 oops |
| 22:27:46 | 组装 `boot-g4-flash.img`：`cp` 后 `truncate -s 100663296` 补零 |
| 22:27:59 | `fastboot flash boot_a` OKAY（98304 KB，整分区） |
| 22:28:01 | `fastboot reboot` |
| 22:28–22:30 | 无法启动，屏幕「系统文件已损坏」；用户手动按键进入 fastboot |
| 22:30:43 | 回滚：整块写回 known-good（96 MiB 快照） |
| 22:31:56 | ssh 恢复（90 秒），kernel `7.2.0-kebab-debian13-r4-venus+` |
| 22:32 | 回读 boot_a 分区 SHA = `4838d489…`，与基线完全一致；尾部 `77c30aa9…` 亦一致 |

---

## 2. 为什么 `fastboot boot` 通过、刷进去却不启动

| 路径 | bootloader 行为 |
| --- | --- |
| `fastboot boot`（T1） | 镜像收到内存直接启动，**不走分区的 AVB / 槽位判定** |
| 从分区启动（T2 之后） | 走 ABL 的引导链校验与槽位状态判定 |

所以 **T1 成功只证明「这套 kernel+dtb 能启动」，不证明「这个分区内容能被 ABL 接受」**。
任务书 §4 把「先 `fastboot boot` 同一镜像」当作 T2 的充分前置条件——这条不成立，
必须再加「分区尾部不变」与「槽位状态健康」两条。

---

## 3. 证据

### 3.1 尾部是继承的，历史部署从不写它

| 对象 | 大小 | `[47869952:]` 的 SHA-256 |
| --- | --- | --- |
| r3 备份（`/srv/kebab-r4-deploy-20261105/backup/boot_a.img`） | 100,663,296 | `77c30aa9df80e295e9758c632d8fd1778fde2909b9799503b372e9614f6db98b` |
| r4 现状（known-good `4838d489…`） | 100,663,296 | `77c30aa9df80e295e9758c632d8fd1778fde2909b9799503b372e9614f6db98b` |

跨 r3→r4 逐字节相同 ⇒ 部署从不写这段。且 r4 的 known-good 前 47,869,952 B 恰好等于构建产物 `8d409dfd…`。

### 3.2 既有部署代码刻意只写前缀并校验尾部

`scripts/linux/deploy-venus-kernel.py`：

```python
BOOT_BYTES = 100663296
'boot_before_prefix_sha256': digest(boot, bundle_info['image'].stat().st_size),
'boot_before_tail_sha256': digest_range(boot, bundle_info['image'].stat().st_size,
                                        BOOT_BYTES - bundle_info['image'].stat().st_size),
...
prefix = digest(plan['boot'], plan['image'].stat().st_size)
tail = digest_range(plan['boot'], plan['image'].stat().st_size,
                    BOOT_BYTES - plan['image'].stat().st_size)
if prefix != BOOT_IMAGE_SHA256 or tail != plan['boot_before_tail_sha256']:
    raise RuntimeError('boot_a post-write prefix/tail verification failed')
```

设备侧 `/srv/kebab-r4-deploy-20261105/boot-guards.py`：

```python
def replace_boot(boot, image, rollback, previous_sha, input_sha):
    run('dd', 'if=' + str(image), 'of=' + str(boot), 'bs=4M', 'conv=fsync', 'status=none')
    if digest(boot, image.stat().st_size) != input_sha:
        raise RuntimeError('Candidate readback mismatch')
```

`dd` 没有 count、没有补零：写多少字节就多少字节；写完**专门校验 tail 未变**。

### 3.3 分区里的 AVB 结构全在尾部区间

| 结构 | 偏移 | 说明 |
| --- | --- | --- |
| 镜像本体结束 | 47,869,952 | 之后全是继承数据 |
| 内嵌 vbmeta `AVB0` | 60,076,032 | avbtool 1.1.0 签名；属性 `com.android.build.boot.fingerprint = qti/kona/kona:12/UKQ1.230924.001/…:user/release-keys` ⇒ 原厂镜像的 vbmeta |
| 页尾 `AVBf` | 100,663,232 | 分区最后 64 B |

`vbmeta_a` 分区（只读取证，65,536 B）：`AVB0` v1.0、algorithm 2（SHA256_RSA2048）、
**flags = 0（校验开启）**、4 个 **CHAIN** 描述符 + 若干 property 描述符。

### 3.4 槽位元数据（GPT，不是 misc）

`kebab-boot-slot-state` 读 `/dev/sde` GPT（Qualcomm BootLib：active=bit50、priority=bits48:49、
unbootable=bit55）。事故后：

```json
"boot_a": {"active": true, "tries": 6, "successful": false, "unbootable": false}
"boot_b": {"active": false, "tries": 6, "successful": true, "unbootable": true}
```

`tries` 是重试余量，耗尽才会被标 unbootable；`successful=false` 是历史遗留（Debian 侧没有
Android 的 boot_success 上报，项目用 `kebab-mark-boot-success` 手动置位）。

### 3.5 历史同症状先例（关键反例）

`docs/touch-protocol-r2-20261004.md` §11：同一台机器、同样提示
「系统文件被损坏，无法开机 / The current image(boot/recovery) have been destroyed」，
当时镜像是 **45,617,152 B、与可用镜像同尺寸、用同样 dd 写法、尾部未动**，
换了内核内容后就起不来，**原因未查明**，最后靠 fastboot 刷回 `ccacb17a…` 恢复。

⇒ 这个提示**不止一种成因**。不能把本次失败简单归因于补零。

---

## 4. 两个候选机制

**A. 尾部 AVB 结构被抹（本次独有差异）**
补零把内嵌 vbmeta 与 footer 覆盖成 0 ⇒ `vbmeta_a` 的 CHAIN 指向的目标不存在 ⇒ ABL 判损坏。
支持：结构确实全在尾部；历史上没人写过尾部。
反证：解锁状态不校验内容哈希（否则改前缀也起不来），所以「必须存在」这一点未被直接证实。

**B. ABL 对内容/启动完成度的判定（与尾部无关）**
ABL 可能在多次启动失败、或 boot_success 未置位且重试耗尽后显示同一提示。
支持：2026-10-04 那次尾部完好也出现同提示；本机 `successful=false` 长期存在；
cmdline 里有 `phx_rus_conf.kernel_time=240` 这类启动时限参数。
反证：本次只失败了一次就出现提示，tries 还有 6 次余量。

---

## 5. 判别实验（唯一能定性的动作）

```bash
# 用未补零的原图，47,869,952 B
ls -l boot-g4.img                      # 必须是 47869952
sha256sum boot-g4.img                  # 8b469f18…
fastboot flash boot_a boot-g4.img      # 不要 truncate、不要 dd conv=sync
fastboot reboot
```

- 起来 → **A 成立**，把「只写镜像字节」定为铁律，P3 可以直接继续。
- 起不来 → **B 成立**（或另有第三种原因），需要继续查 ABL 判定；此时立刻用 known-good 整块回滚。

回滚命令（已实测 90 秒恢复）：

```bash
fastboot flash boot_a known-good/boot_a-ed3ae39d.img
fastboot reboot
```

---

## 6. 强制检查表（以后每次写 boot_a 之前逐条打勾）

1. [ ] 镜像是 mkbootimg 原始输出，**不要 truncate、不要补零、不要 dd conv=sync**。
2. [ ] 刷前记录 `tail_sha = sha256(分区[47869952:])`，本机基线 `77c30aa9…`。
3. [ ] 刷后回读：前缀 SHA == 镜像 SHA；尾部 SHA == 刷前 tail_sha。
4. [ ] `fastboot boot` 只当「内核可启动」证据，**不当作「ABL 会接受」证据**。
5. [ ] 恢复件在手：完整 96 MiB known-good（`4838d489…`）；**整分区快照可以整块写回**，
       与新镜像「只写前缀」的规则不同，别混。
6. [ ] 先看 `kebab-boot-slot-state`：`unbootable=false`、`tries` 有余量。
7. [ ] 人能在现场按「音量上+音量下+电源」进 fastboot（本次就是靠这个救回来的）。

---

## 7. 恢复过程（本次实际执行）

```bash
fastboot devices
fastboot flash boot_a known-good/boot_a-ed3ae39d.img   # 96 MiB 整块覆盖
fastboot reboot
# 90 秒后 ssh 回来；回读分区 SHA = 4838d489…，尾部 = 77c30aa9…，与基线一致
```

---

## 8. 未解问题（诚实标注）

1. 页尾 `AVBf` 按教科书 AVB 1.0 footer 解码不出来（`original_image_size` 读出 0，
   `vbmeta_offset` 异常），而 `AVB0` 实际在 60,076,032。可能是厂商变体或不同工具写入。
2. 解锁状态下 ABL 到底校验什么，没有厂商源码可证；目前只有行为证据。
3. 2026-10-04 的同症状事故至今未查明，与本次是否同因未知。
4. `boot_a successful=false` 的历史由来与是否复位（会写 GPT），待用户决定，本轮未动。

---

## 9. 教训

1. **「先 boot 一下」不等于「可以刷」**：RAM 启动绕过分区校验与槽位判定。
2. **分区 ≠ 镜像**：96 MiB 分区里的 47.9 MiB 镜像之外还有继承数据，别当填充物。
3. **历史脚本里的「不严谨」往往是有意的**：`dd` 不带 count 是刻意保留尾部，改流程前先读代码。
4. **同一个错误提示可能有多种成因**：这次差点把「补零」当成唯一根因写死，
   是 2026-10-04 的旧记录提醒了还存在第二种机制。
5. **恢复件按「整分区快照」准备**，回滚永远整块写回，不依赖对内部结构的理解。
6. 4 分钟恢复的前提是：刷之前就把 known-good 的路径和 SHA 写进了 STATE.md。
