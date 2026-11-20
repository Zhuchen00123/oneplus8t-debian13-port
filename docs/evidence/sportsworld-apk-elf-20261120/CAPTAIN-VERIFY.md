# 队长独立复核：APK 内 ZIP + ELF 偏移（2026-11-20）

对象：`运动世界_7.4.01.apk`（331,188,204 B，SHA-256 `4049a546…f938`，与预期一致）。
复核人：队长（独立于产出该 JSON 的离线子代理）。方法：Node 直接随机读 APK，重建 ZIP 中央目录与 local header，再解析 ELF 程序头。

## 1. ZIP 层（自己算的，与子代理一致）

| 项 | 值 |
|---|---|
| EOCD 绝对偏移 | 331,188,182 |
| 中央目录 offset / size | 330,801,152 / 387,030 |
| 条目总数 / .so 数 | 5,969 / 123 |
| .so 压缩方式 | **全部 method=0（stored）**，compressed==uncompressed |
| .so 总量 | 205,344,256 B |

## 2. lib/arm64-v8a/libssl.so（真 TLS 库候选）

- local header 偏移 98,308,528，name_len=23，**extra_len=28,187** → data_offset = **98,336,768**（注意：extra 字段很大，不能用“30+名字长度”简化计算）。
- data_offset 处 ELF magic `7f 45 4c 46`，class=2，machine=0x00B7（AArch64）。
- PT_LOAD(off=0, vaddr=0, filesz=280,824) 覆盖 `st_value=0x2b530` → elf_off=177,456 → **APK 内绝对偏移 98,514,224**。
- 该偏移处前 12 字节：`fd 7b bf a9 e3 03 00 aa fd 03 00 91` = `stp x29,x30,[sp,#-16]!` 等标准函数序言 ⇒ 偏移指向真实函数入口，不是数据。

| 符号 | st_value | ELF 偏移 | APK 内绝对偏移 |
|---|---|---|---|
| SSL_write | 0x2b530 | 177,456 | **98,514,224** |
| SSL_read | 0x2b46c | 177,260 | 98,514,028 |
| SSL_CTX_new | 0x2c044 | 180,292 | 98,517,060 |
| SSL_do_handshake | 0x2d848 | 186,440 | 98,523,208 |
| SSL_new | 0x2da94 | 187,028 | 98,523,796 |
| SSL_free | 0x2dd2c | 187,692 | 98,524,460 |

build-id（arm64）：`c4daca73993bc2238ec35febc1ac8849fe4b1309`。

## 3. ARM32 两份（v7a / armeabi）——有 Thumb 位陷阱

| 条目 | data_offset | SSL_write st_value | 实际代码偏移 | APK 内绝对偏移 |
|---|---|---|---|---|
| lib/armeabi-v7a/libssl.so | 182,288,384 | 111,613（奇数=Thumb） | 111,612 | 182,399,997（偶数 182,399,996） |
| lib/armeabi/libssl.so | 192,512,000 | 116,157（奇数=Thumb） | 116,156 | 192,628,157（偶数 192,628,156） |

⇒ **ARM32 的 st_value 带 Thumb bit0，挂 uprobe / 算偏移必须先 `& ~1`**。这条既是工具要求，也是 r5/compat 清单的一条。

## 4. 关键结论（决定第三步怎么挂）

1. 自带 `libssl.so` 是 OpenSSL 1.1.x 形态（有 `SSLv2_method`、`SSL_CTX_use_RSAPrivateKey_ASN1` 等 1.1 符号），三套 ABI 各一份，且**全部 stored 可原地 mmap**。
2. 但 **123 个 .so 的 .dynsym 里没有任何一个导入 SSL_***；只有 `libswsport.so`（三 ABI）DT_NEEDED → libcrypto，导入的是 `EVP_*`，且二进制里有 `dlopen`/`dlsym` 字面量。
3. 所以真正的 TLS 会话要么是运行时 `dlopen(libssl.so)+dlsym`，要么在 Java 层（Conscrypt）。**必须由第一步真机 `/proc/<pid>/maps` 回答**：
   - 若 maps 里出现 base.apk 的 98,336,768 偏移段 ⇒ 用上面的 APK 内偏移挂 SSL_write/SSL_read；
   - 若没有、而出现 /system/lib64/libssl.so ⇒ 走系统 BoringSSL 路径（另需符号核对，不能套用这份偏移）。

## 5. 复核结论

子代理给出的 ZIP/EOCD/中央目录/extra_len/PT_LOAD 反推与本次独立计算**逐项一致**；符号偏移处的机器码形态符合函数序言。该 JSON 可以作为第三步的输入，但**运行时是否真的映射这份 libssl 仍未验证**——留给真机窗口。
