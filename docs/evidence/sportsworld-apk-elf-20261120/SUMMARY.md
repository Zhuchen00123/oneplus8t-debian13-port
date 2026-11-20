# 运动世界 7.4.01（com.zjwh.android_wh_physicalfitness）APK 静态解析摘要

只读分析：仅 seek 读取 ZIP 中央目录、local file header 与 ELF 结构体；未解压、未修改 APK、未联网。
生成时间 2026-11-20 17:40:37。机器可读结果见同目录 apk-elf.json。

## 0. 校验

| 项 | 值 |
| --- | --- |
| APK | F:\codexprojects\oneplus8t\运动世界_7.4.01.apk |
| 大小 | 331,188,204 B |
| SHA-256 实测 | 4049a546ee3560589a4fd3f38af561f8f28123bb8915600bec51730efa47f938 |
| SHA-256 期望 | 4049a546ee3560589a4fd3f38af561f8f28123bb8915600bec51730efa47f938 |
| 结论 | **一致** |

## 1. 结论速览

1. **真 TLS 库 = libssl.so**（配套 libcrypto.so），三套 ABI 各一份；每份导出 **229 个 SSL\* 符号**，10 个关键 API 全部为已定义导出，且都能反推出 APK 内绝对偏移。
2. **libnesec.so**：arm64-v8a 为 **AArch64**，build-id 42848dfedb0d7015563ae1339e4c84bdc8d6b823；armeabi-v7a 与 armeabi 为 **同一份 ARM32 文件**（build-id 41b39b249a42159458109bfe3a4b2e1cdea135f3，字节相同）。
   同名的 libnesec-x86.so 在 arm64-v8a 目录里其实是 **x86-64**（build-id 04aca0a775d0bdd3819def4f709fcbaa30bad998），在 v7a/armeabi 目录里是 **i386**（build-id 28870b29c70078fb26ade9bf6155094e7a4c2566）。
3. **真实架构计数**：AArch64 = 53，ARM32 = 65，x86-64 = 1，i386 = 4，其它 = 0。
4. **全部 123 个 .so 都是 method=0（stored）**，与 extractNativeLibs=false 吻合：运行期直接从 APK 内 mmap，本文的 zip_data_offset / apk_offset 就是映射里的真实文件偏移。
5. 没有任何 .so 显式导入 SSL_\* 符号（见第 6 节），说明 TLS 由 Java/JNI 侧驱动或经 dlsym 动态取用，而不是被某个 native 库在链接期直接调用。

## 2. ZIP 层

| 项 | 值 |
| --- | --- |
| ZIP 形态 | zip32 |
| EOCD 绝对偏移 | 331188182 |
| 中央目录 offset / size | 330801152 / 387030 |
| 中央目录条目总数 | 5969 |
| 其中 .so 条目 | 123 |
| 压缩方式 | 全部 method=0（stored），compressed == uncompressed |
| .so 解压后总量 | 205,344,256 B |

data_offset = local_header_offset + 30 + name_len + extra_len，其中 name_len / extra_len 取自 **local file header**，不是中央目录里的同名长度（两者可能不同）。全部 123 个 data_offset 处都能读到 ELF 魔数 7F 45 4C 46。

## 3. ABI 目录 vs 真实架构

| 目录 | 条目数 | aarch64 | arm32 | x86_64 | i386 | other |
| --- | --- | --- | --- | --- | --- | --- |
| lib/arm64-v8a | 54 | 53 | 0 | 1 | 0 | 0 |
| lib/armeabi-v7a | 49 | 0 | 47 | 0 | 2 | 0 |
| lib/armeabi | 20 | 0 | 18 | 0 | 2 | 0 |

目录名与架构不一致的条目：

| 条目 | 所在目录 | 真实架构 | e_machine |
| --- | --- | --- | --- |
| lib/armeabi-v7a/libxmediaplayer_x.so | lib/armeabi-v7a | **Intel 80386** | 0x3 |
| lib/armeabi/libxmediaplayer_x.so | lib/armeabi | **Intel 80386** | 0x3 |
| lib/armeabi/libnesec-x86.so | lib/armeabi | **Intel 80386** | 0x3 |
| lib/armeabi-v7a/libnesec-x86.so | lib/armeabi-v7a | **Intel 80386** | 0x3 |
| lib/arm64-v8a/libnesec-x86.so | lib/arm64-v8a | **x86-64** | 0x3e |

## 4. 重点库（名字含 ssl/crypto/boring/nesec/flutter/cronet/mbedtls/conscrypt/wolfssl/openssl/protect/sec/jiagu/nqshield/yidun）

| 条目 | 架构 | build-id | .dynsym | SSL 导出 | soname |
| --- | --- | --- | --- | --- | --- |
| lib/arm64-v8a/libcrypto.so | AArch64 | 22e36fa0f72e26fe... | 3326 | 38 | libcrypto.so |
| lib/arm64-v8a/libflutter.so | AArch64 | 87c29aaa78edb752... | 517 | 0 | libflutter.so |
| lib/arm64-v8a/libmsaoaidsec.so | AArch64 | - | null | 0 | - |
| lib/arm64-v8a/libnetseckit-4.2.4.so | AArch64 | 9952898f5b8f19f9... | 361 | 0 | libnetsecsdk.so |
| lib/arm64-v8a/libsecsdk_tianmu.so | AArch64 | dfd0b81e672e7140... | 60 | 0 | libsecsdk.so |
| lib/arm64-v8a/libssl.so | AArch64 | c4daca73993bc223... | 752 | 229 | libssl.so |
| lib/arm64-v8a/libxmopendatacrypto.so | AArch64 | 73d6e046da6efdea... | 33 | 0 | libxmopendatacrypto.so |
| lib/arm64-v8a/libzxprotect.so | AArch64 | 00b9de6e4d158e3f... | 427 | 0 | libzxprotect.so |
| lib/armeabi-v7a/libcrypto.so | ARM | 0e3c944b2837ae62... | 3327 | 38 | libcrypto.so |
| lib/armeabi-v7a/libflutter.so | ARM | b6f8bc5528eef579... | 535 | 0 | libflutter.so |
| lib/armeabi-v7a/libmsaoaidsec.so | ARM | - | null | 0 | - |
| lib/armeabi-v7a/libnetseckit-4.2.4.so | ARM | 027046f748876017... | 398 | 0 | libnetsecsdk.so |
| lib/armeabi-v7a/libsecsdk_tianmu.so | ARM | 38aa35857f595b44... | 81 | 0 | libsecsdk.so |
| lib/armeabi-v7a/libssl.so | ARM | 400ed69d7c35ca7f... | 753 | 229 | libssl.so |
| lib/armeabi-v7a/libxmopendatacrypto.so | ARM | - | 116 | 0 | libxmopendatacrypto.so |
| lib/armeabi-v7a/libzxprotect.so | ARM | 85f343003e8d8805... | 427 | 0 | libzxprotect.so |
| lib/armeabi/libcrypto.so | ARM | 7037451db39cf52d... | 3423 | 38 | libcrypto.so |
| lib/armeabi/libnetseckit-4.2.4.so | ARM | 7c236effc4c67106... | 458 | 0 | libnetsecsdk.so |
| lib/armeabi/libssl.so | ARM | c7d1dbd521ec81aa... | 804 | 229 | libssl.so |
| lib/armeabi/libxmopendatacrypto.so | ARM | - | 116 | 0 | libxmopendatacrypto.so |
| lib/armeabi/libzxprotect.so | ARM | 85f343003e8d8805... | 427 | 0 | libzxprotect.so |
| lib/armeabi/libnesec.so | ARM | 41b39b249a421594... | 2990 | 0 | libsecexe.so |
| lib/armeabi-v7a/libnesec.so | ARM | 41b39b249a421594... | 2990 | 0 | libsecexe.so |
| lib/arm64-v8a/libnesec.so | AArch64 | 42848dfedb0d7015... | 2983 | 0 | libsecexe.so |
| lib/armeabi/libnesec-x86.so | Intel 80386 | 28870b29c70078fb... | 2987 | 0 | libsecexe.so |
| lib/armeabi-v7a/libnesec-x86.so | Intel 80386 | 28870b29c70078fb... | 2987 | 0 | libsecexe.so |
| lib/arm64-v8a/libnesec-x86.so | x86-64 | 04aca0a775d0bdd3... | 2986 | 0 | libsecexe.so |

## 5. 真 TLS 库：libssl.so 关键符号与 APK 内绝对偏移

### lib/arm64-v8a/libssl.so（AArch64，build-id c4daca73993bc2238ec35febc1ac8849fe4b1309，zip_data_offset 98336768）

| 符号 | st_value | st_size | ELF 文件偏移 | APK 绝对偏移 |
| --- | --- | --- | --- | --- |
| SSL_get_fd | 0x2ae04 | 4 | 175620 | 98512388 |
| SSL_write | 0x2b530 | 128 | 177456 | 98514224 |
| SSL_free | 0x2dd2c | 460 | 187692 | 98524460 |
| SSL_set_fd | 0x2ae74 | 132 | 175732 | 98512500 |
| SSL_read | 0x2b46c | 104 | 177260 | 98514028 |
| SSL_new | 0x2da94 | 664 | 187028 | 98523796 |
| SSL_accept | 0x2e230 | 84 | 188976 | 98525744 |
| SSL_CTX_new | 0x2c044 | 964 | 180292 | 98517060 |
| SSL_connect | 0x2e2cc | 84 | 189132 | 98525900 |
| SSL_do_handshake | 0x2d848 | 152 | 186440 | 98523208 |

### lib/armeabi-v7a/libssl.so（ARM，build-id 400ed69d7c35ca7fba376634f43fa3b3b22c6460，zip_data_offset 182288384）

| 符号 | st_value | st_size | ELF 文件偏移 | APK 绝对偏移 |
| --- | --- | --- | --- | --- |
| SSL_accept | 0x1d0ad | 24 | 118957 | 182407341 |
| SSL_connect | 0x1d0f3 | 24 | 119027 | 182407411 |
| SSL_read | 0x1b379 | 68 | 111481 | 182399865 |
| SSL_write | 0x1b3fd | 84 | 111613 | 182399997 |
| SSL_get_fd | 0x1afc9 | 4 | 110537 | 182398921 |
| SSL_set_fd | 0x1b00d | 72 | 110605 | 182398989 |
| SSL_CTX_new | 0x1ba3d | 740 | 113213 | 182401597 |
| SSL_do_handshake | 0x1c9e1 | 96 | 117217 | 182405601 |
| SSL_new | 0x1cb35 | 532 | 117557 | 182405941 |
| SSL_free | 0x1cd49 | 344 | 118089 | 182406473 |

### lib/armeabi/libssl.so（ARM，build-id c7d1dbd521ec81aae480adf49c7b61d9d51a706e，zip_data_offset 192512000）

| 符号 | st_value | st_size | ELF 文件偏移 | APK 绝对偏移 |
| --- | --- | --- | --- | --- |
| SSL_accept | 0x1e39f | 24 | 123807 | 192635807 |
| SSL_connect | 0x1e3e3 | 24 | 123875 | 192635875 |
| SSL_read | 0x1c53d | 64 | 116029 | 192628029 |
| SSL_write | 0x1c5bd | 84 | 116157 | 192628157 |
| SSL_get_fd | 0x1c13d | 8 | 115005 | 192627005 |
| SSL_set_fd | 0x1c185 | 72 | 115077 | 192627077 |
| SSL_CTX_new | 0x1cc35 | 748 | 117813 | 192629813 |
| SSL_do_handshake | 0x1dc81 | 96 | 121985 | 192633985 |
| SSL_new | 0x1dde5 | 548 | 122341 | 192634341 |
| SSL_free | 0x1e009 | 368 | 122889 | 192634889 |

其余 SSL\* 符号（每份最多取前 200 个）见 apk-elf.json 的 elf[].symbols。

## 6. 谁碰 TLS

**链接期导入 SSL_\* 的库**：

无。123 个 .so 的 .dynsym 里没有任何未定义的 SSL_\* 符号 —— TLS 不是 native 库链接期直接调用的。

**二进制里直接出现 libssl 相关字面量的库**（dlsym / dlopen / 字符串线索）：

| 条目 | 架构 | 命中 |
| --- | --- | --- |
| lib/arm64-v8a/libcrypto.so | AArch64 | libcrypto.so |
| lib/arm64-v8a/libijkffmpeg.so | AArch64 | SSL_CTX_new, SSL_write |
| lib/arm64-v8a/librxffmpeg-core.so | AArch64 | SSL_CTX_new, SSL_write |
| lib/arm64-v8a/libssl.so | AArch64 | libssl.so, libcrypto.so, SSL_CTX_new, SSL_write |
| lib/arm64-v8a/libswsport.so | AArch64 | libssl.so, libcrypto.so |
| lib/armeabi-v7a/libcrypto.so | ARM | libcrypto.so |
| lib/armeabi-v7a/libijkffmpeg.so | ARM | SSL_CTX_new, SSL_write |
| lib/armeabi-v7a/librxffmpeg-core.so | ARM | SSL_CTX_new, SSL_write |
| lib/armeabi-v7a/libssl.so | ARM | libssl.so, libcrypto.so, SSL_CTX_new, SSL_write |
| lib/armeabi-v7a/libswsport.so | ARM | libssl.so, libcrypto.so |
| lib/armeabi/libcrypto.so | ARM | libcrypto.so |
| lib/armeabi/libssl.so | ARM | libssl.so, libcrypto.so, SSL_CTX_new, SSL_write |
| lib/armeabi/libswsport.so | ARM | libssl.so, libcrypto.so |

**DT_NEEDED 里直接依赖 libssl.so / libcrypto.so 的库**（链接期消费者）：

| 条目 | 架构 | DT_NEEDED 中的 ssl/crypto |
| --- | --- | --- |
| lib/arm64-v8a/libssl.so | AArch64 | libcrypto.so |
| lib/arm64-v8a/libswsport.so | AArch64 | libcrypto.so, libssl.so |
| lib/armeabi-v7a/libssl.so | ARM | libcrypto.so |
| lib/armeabi-v7a/libswsport.so | ARM | libcrypto.so, libssl.so |
| lib/armeabi/libssl.so | ARM | libcrypto.so |
| lib/armeabi/libswsport.so | ARM | libcrypto.so, libssl.so |

结论：除 libssl.so 自身依赖 libcrypto.so 外，**唯一**消费者是应用自带的 libswsport.so（三套 ABI 都有）。libswsport.so 从 libcrypto 导入的是 EVP_\* 一族（AES-CBC 等），没有导入任何 SSL_\* 会话函数，而它的二进制里存在 dlopen / dlsym 字面量 —— 真正的 TLS 会话要么由 dlsym 动态取符号，要么由 Java 层完成，native 侧只承担加解密原语。

**构建指纹（字符串扫描，重点库）**：

| 条目 | 命中 |
| --- | --- |
| lib/arm64-v8a/libflutter.so | clang version |
| lib/arm64-v8a/libxmopendatacrypto.so | clang version |
| lib/arm64-v8a/libzxprotect.so | clang version |
| lib/armeabi-v7a/libflutter.so | clang version |
| lib/armeabi-v7a/libzxprotect.so | clang version |
| lib/armeabi/libzxprotect.so | clang version |

注意：这些 libssl.so 里没有 OpenSSL 版本横幅（无 "OpenSSL 1.1.1" 之类字符串），但符号集合（SSL_state、SSLv2_method、SSL_CTX_use_RSAPrivateKey_ASN1、SSL_get_peer_finished）是 OpenSSL 1.1.x 系列 API 的典型形态。

## 7. 全部 .so 清单（条目名 + 真实 e_machine）

| # | 条目 | 架构 | e_machine | class | e_type | build-id | APK data_offset |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | lib/arm64-v8a/libAMapSDK_NAVI_v10_0_700.so | AArch64 | 0xb7 | 64 | ET_DYN | 6ea92bf6597ca39a... | 16384 |
| 2 | lib/arm64-v8a/libBugly.so | AArch64 | 0xb7 | 64 | ET_DYN | 2abb6ba8e67d928d... | 39059456 |
| 3 | lib/arm64-v8a/libBugly_Native.so | AArch64 | 0xb7 | 64 | ET_DYN | b9c637d9e30c4ad0... | 39272448 |
| 4 | lib/arm64-v8a/libPglbizssdk_ml.so | AArch64 | 0xb7 | 64 | ET_DYN | 179802b6b113d5de... | 39485440 |
| 5 | lib/arm64-v8a/libane.so | AArch64 | 0xb7 | 64 | ET_DYN | 0c139b263dffa269... | 40648704 |
| 6 | lib/arm64-v8a/libapp.so | AArch64 | 0xb7 | 64 | ET_DYN | 369f5a6f07223de3... | 40779776 |
| 7 | lib/arm64-v8a/libavmdl_lite.so | AArch64 | 0xb7 | 64 | ET_DYN | 4b40b51b14d1a7af... | 50446336 |
| 8 | lib/arm64-v8a/libcrashsdk.so | AArch64 | 0xb7 | 64 | ET_DYN | af353b0fdc0e5055... | 51216384 |
| 9 | lib/arm64-v8a/libcrypto.so | AArch64 | 0xb7 | 64 | ET_DYN | 22e36fa0f72e26fe... | 51888128 |
| 10 | lib/arm64-v8a/libdatastore_shared_counter.so | AArch64 | 0xb7 | 64 | ET_DYN | 17db37bd6770ac00... | 53444608 |
| 11 | lib/arm64-v8a/libdeepknow-lib.so | AArch64 | 0xb7 | 64 | ET_DYN | 75e4778cbb7a501f... | 53477376 |
| 12 | lib/arm64-v8a/libflutter.so | AArch64 | 0xb7 | 64 | ET_DYN | 87c29aaa78edb752... | 53886976 |
| 13 | lib/arm64-v8a/libfntvcrash.so | AArch64 | 0xb7 | 64 | ET_DYN | c01e954a7204b53b... | 64733184 |
| 14 | lib/arm64-v8a/libgtc4core.so | AArch64 | 0xb7 | 64 | ET_DYN | e10a9072b6050e9a... | 64864256 |
| 15 | lib/arm64-v8a/libgtcore.so | AArch64 | 0xb7 | 64 | ET_DYN | 3e2230fc53e49055... | 66043904 |
| 16 | lib/arm64-v8a/libijiami_1011.so | AArch64 | 0xb7 | 64 | ET_DYN | a14e5a143ac19517... | 67239936 |
| 17 | lib/arm64-v8a/libijkffmpeg.so | AArch64 | 0xb7 | 64 | ET_DYN | - | 67420160 |
| 18 | lib/arm64-v8a/libijkplayer.so | AArch64 | 0xb7 | 64 | ET_DYN | 1a1c63656e725697... | 72974336 |
| 19 | lib/arm64-v8a/libijksdl.so | AArch64 | 0xb7 | 64 | ET_DYN | 6b8aa1eda89f5e5b... | 73416704 |
| 20 | lib/arm64-v8a/libimage_processing_util_jni.so | AArch64 | 0xb7 | 64 | ET_DYN | e667d7f16c83b70a... | 73924608 |
| 21 | lib/arm64-v8a/libmediadatacryto.so | AArch64 | 0xb7 | 64 | ET_DYN | 4b0ec521a5a6ac58... | 73973760 |
| 22 | lib/arm64-v8a/libmmkv.so | AArch64 | 0xb7 | 64 | ET_DYN | ab7c5caf201f0d75... | 74006528 |
| 23 | lib/arm64-v8a/libmsaoaidauth.so | AArch64 | 0xb7 | 64 | ET_DYN | - | 74629120 |
| 24 | lib/arm64-v8a/libmsaoaidsec.so | AArch64 | 0xb7 | 64 | ET_DYN | - | 75071488 |
| 25 | lib/arm64-v8a/libnative-tianmu-lib.so | AArch64 | 0xb7 | 64 | ET_DYN | 88997ca691fecf4b... | 75792384 |
| 26 | lib/arm64-v8a/libneonui_shared.so | AArch64 | 0xb7 | 64 | ET_DYN | - | 76103680 |
| 27 | lib/arm64-v8a/libneonuijni_public.so | AArch64 | 0xb7 | 64 | ET_DYN | - | 80707584 |
| 28 | lib/arm64-v8a/libnesec-x86.so | x86-64 | 0x3e | 64 | ET_DYN | 04aca0a775d0bdd3... | 297943040 |
| 29 | lib/arm64-v8a/libnesec.so | AArch64 | 0xb7 | 64 | ET_DYN | 42848dfedb0d7015... | 291766272 |
| 30 | lib/arm64-v8a/libnetseckit-4.2.4.so | AArch64 | 0xb7 | 64 | ET_DYN | 9952898f5b8f19f9... | 80789504 |
| 31 | lib/arm64-v8a/libpanglearmor.so | AArch64 | 0xb7 | 64 | ET_DYN | 047673e60abf290e... | 82329600 |
| 32 | lib/arm64-v8a/libpangleflipped.so | AArch64 | 0xb7 | 64 | ET_DYN | 1a42e30ae0f330e0... | 82640896 |
| 33 | lib/arm64-v8a/libplt-base.so | AArch64 | 0xb7 | 64 | ET_DYN | f989a7f297740bb7... | 82673664 |
| 34 | lib/arm64-v8a/librtmp-jni.so | AArch64 | 0xb7 | 64 | ET_DYN | 8870e6537cdae7ca... | 83066880 |
| 35 | lib/arm64-v8a/librxffmpeg-core.so | AArch64 | 0xb7 | 64 | ET_DYN | - | 83181568 |
| 36 | lib/arm64-v8a/librxffmpeg-invoke.so | AArch64 | 0xb7 | 64 | ET_DYN | 4c1adfb868c49a98... | 93487104 |
| 37 | lib/arm64-v8a/librxffmpeg-player.so | AArch64 | 0xb7 | 64 | ET_DYN | 84e09e757ca36601... | 93929472 |
| 38 | lib/arm64-v8a/libsecsdk_tianmu.so | AArch64 | 0xb7 | 64 | ET_DYN | dfd0b81e672e7140... | 94158848 |
| 39 | lib/arm64-v8a/libsgcore.so | AArch64 | 0xb7 | 64 | ET_DYN | d720dbfd171876cb... | 94568448 |
| 40 | lib/arm64-v8a/libsqlcipher.so | AArch64 | 0xb7 | 64 | ET_DYN | deda9f425883f98d... | 94683136 |
| 41 | lib/arm64-v8a/libssl.so | AArch64 | 0xb7 | 64 | ET_DYN | c4daca73993bc223... | 98336768 |
| 42 | lib/arm64-v8a/libswsport.so | AArch64 | 0xb7 | 64 | ET_DYN | bd9bcad715f7cbf5... | 98680832 |
| 43 | lib/arm64-v8a/libtensorflowlite_gpu_jni.so | AArch64 | 0xb7 | 64 | ET_DYN | 85ee85b3b999e40f... | 99336192 |
| 44 | lib/arm64-v8a/libtensorflowlite_jni.so | AArch64 | 0xb7 | 64 | ET_DYN | 850a925e891f910b... | 101384192 |
| 45 | lib/arm64-v8a/libti-monitor.so | AArch64 | 0xb7 | 64 | ET_DYN | d2d5aabaa5aac5d0... | 104955904 |
| 46 | lib/arm64-v8a/libtobEmbedEncrypt.so | AArch64 | 0xb7 | 64 | ET_DYN | f1f3c31bd820fc1c... | 105103360 |
| 47 | lib/arm64-v8a/libtt_ugen_layout.so | AArch64 | 0xb7 | 64 | ET_DYN | b0b66914de732536... | 105185280 |
| 48 | lib/arm64-v8a/libttmplayer_lite.so | AArch64 | 0xb7 | 64 | ET_DYN | f4a13be00c5be24a... | 105644032 |
| 49 | lib/arm64-v8a/libumeng-spy.so | AArch64 | 0xb7 | 64 | ET_DYN | 208f7ec58fa1ba72... | 107167744 |
| 50 | lib/arm64-v8a/libweibosdkcore.so | AArch64 | 0xb7 | 64 | ET_DYN | - | 107724800 |
| 51 | lib/arm64-v8a/libxmediaplayerv8.so | AArch64 | 0xb7 | 64 | ET_DYN | 153917195a97212a... | 107773952 |
| 52 | lib/arm64-v8a/libxmopendatacrypto.so | AArch64 | 0xb7 | 64 | ET_DYN | 73d6e046da6efdea... | 108756992 |
| 53 | lib/arm64-v8a/libyf_ads.so | AArch64 | 0xb7 | 64 | ET_DYN | 16d09ddfa901c975... | 108822528 |
| 54 | lib/arm64-v8a/libzxprotect.so | AArch64 | 0xb7 | 64 | ET_DYN | 00b9de6e4d158e3f... | 109117440 |
| 55 | lib/armeabi-v7a/libAMapSDK_NAVI_v10_0_700.so | ARM | 0x28 | 32 | ET_DYN | 95fa784d57aa4867... | 109674496 |
| 56 | lib/armeabi-v7a/libBugly.so | ARM | 0x28 | 32 | ET_DYN | 300e8325e67d6fc6... | 135757824 |
| 57 | lib/armeabi-v7a/libBugly_Native.so | ARM | 0x28 | 32 | ET_DYN | 6d6208d619e458a4... | 135954432 |
| 58 | lib/armeabi-v7a/libane.so | ARM | 0x28 | 32 | ET_DYN | cda039f13ec5e27a... | 136167424 |
| 59 | lib/armeabi-v7a/libapp.so | ARM | 0x28 | 32 | ET_DYN | 3f8618a0eddb3b43... | 136249344 |
| 60 | lib/armeabi-v7a/libcocklogic-1.1.3.so | ARM | 0x28 | 32 | ET_DYN | - | 147013632 |
| 61 | lib/armeabi-v7a/libcrashsdk.so | ARM | 0x28 | 32 | ET_DYN | a309bc3a1c2ad010... | 147062784 |
| 62 | lib/armeabi-v7a/libcrypto.so | ARM | 0x28 | 32 | ET_DYN | 0e3c944b2837ae62... | 147587072 |
| 63 | lib/armeabi-v7a/libdatastore_shared_counter.so | ARM | 0x28 | 32 | ET_DYN | c2c541cc4337a0fc... | 148439040 |
| 64 | lib/armeabi-v7a/libdeepknow-lib.so | ARM | 0x28 | 32 | ET_DYN | 6ae541fc06c91f49... | 148471808 |
| 65 | lib/armeabi-v7a/libflutter.so | ARM | 0x28 | 32 | ET_DYN | b6f8bc5528eef579... | 148766720 |
| 66 | lib/armeabi-v7a/libgtc4core.so | ARM | 0x28 | 32 | ET_DYN | c33aa360e6ce490a... | 156401664 |
| 67 | lib/armeabi-v7a/libgtcore.so | ARM | 0x28 | 32 | ET_DYN | 9dfa770ccc37f89d... | 156958720 |
| 68 | lib/armeabi-v7a/libijiami_1011.so | ARM | 0x28 | 32 | ET_DYN | ac1af285d0a3ebb1... | 157515776 |
| 69 | lib/armeabi-v7a/libijkffmpeg.so | ARM | 0x28 | 32 | ET_DYN | - | 157696000 |
| 70 | lib/armeabi-v7a/libijkplayer.so | ARM | 0x28 | 32 | ET_DYN | ebfaa1fb89083a11... | 162332672 |
| 71 | lib/armeabi-v7a/libijksdl.so | ARM | 0x28 | 32 | ET_DYN | 750bd624d1e5cfff... | 162660352 |
| 72 | lib/armeabi-v7a/libimage_processing_util_jni.so | ARM | 0x28 | 32 | ET_DYN | 12e91d3874791a1d... | 162906112 |
| 73 | lib/armeabi-v7a/libmediadatacryto.so | ARM | 0x28 | 32 | ET_DYN | 913e1c64947efade... | 162955264 |
| 74 | lib/armeabi-v7a/libmmkv.so | ARM | 0x28 | 32 | ET_DYN | 30431471c8d0ac3d... | 163004416 |
| 75 | lib/armeabi-v7a/libmsaoaidauth.so | ARM | 0x28 | 32 | ET_DYN | - | 163397632 |
| 76 | lib/armeabi-v7a/libmsaoaidsec.so | ARM | 0x28 | 32 | ET_DYN | - | 163594240 |
| 77 | lib/armeabi-v7a/libnative-tianmu-lib.so | ARM | 0x28 | 32 | ET_DYN | 272a753e02958fad... | 164102144 |
| 78 | lib/armeabi-v7a/libneonui_shared.so | ARM | 0x28 | 32 | ET_DYN | - | 164298752 |
| 79 | lib/armeabi-v7a/libneonuijni_public.so | ARM | 0x28 | 32 | ET_DYN | - | 168214528 |
| 80 | lib/armeabi-v7a/libnesec-x86.so | Intel 80386 | 0x3 | 32 | ET_DYN | 28870b29c70078fb... | 295796736 |
| 81 | lib/armeabi-v7a/libnesec.so | ARM | 0x28 | 32 | ET_DYN | 41b39b249a421594... | 290127872 |
| 82 | lib/armeabi-v7a/libnetseckit-4.2.4.so | ARM | 0x28 | 32 | ET_DYN | 027046f748876017... | 168280064 |
| 83 | lib/armeabi-v7a/libpangleflipped.so | ARM | 0x28 | 32 | ET_DYN | ef660c82f735f130... | 169017344 |
| 84 | lib/armeabi-v7a/libplt-base.so | ARM | 0x28 | 32 | ET_DYN | 703589545264193c... | 169050112 |
| 85 | lib/armeabi-v7a/librtmp-jni.so | ARM | 0x28 | 32 | ET_DYN | 798e1ecd5fc03798... | 169328640 |
| 86 | lib/armeabi-v7a/librxffmpeg-core.so | ARM | 0x28 | 32 | ET_DYN | - | 169426944 |
| 87 | lib/armeabi-v7a/librxffmpeg-invoke.so | ARM | 0x28 | 32 | ET_DYN | ad60f3fe41b30bc1... | 179126272 |
| 88 | lib/armeabi-v7a/librxffmpeg-player.so | ARM | 0x28 | 32 | ET_DYN | 94107d3c70dbd9f5... | 179437568 |
| 89 | lib/armeabi-v7a/libsecsdk_tianmu.so | ARM | 0x28 | 32 | ET_DYN | 38aa35857f595b44... | 179585024 |
| 90 | lib/armeabi-v7a/libsgcore.so | ARM | 0x28 | 32 | ET_DYN | 5f62a44805debe29... | 179879936 |
| 91 | lib/armeabi-v7a/libsqlcipher.so | ARM | 0x28 | 32 | ET_DYN | 56fb6995735f2634... | 180027392 |
| 92 | lib/armeabi-v7a/libssl.so | ARM | 0x28 | 32 | ET_DYN | 400ed69d7c35ca7f... | 182288384 |
| 93 | lib/armeabi-v7a/libswsport.so | ARM | 0x28 | 32 | ET_DYN | 6e03f6458320f7c1... | 182501376 |
| 94 | lib/armeabi-v7a/libtensorflowlite_gpu_jni.so | ARM | 0x28 | 32 | ET_DYN | a1148dfd010e1265... | 182878208 |
| 95 | lib/armeabi-v7a/libtensorflowlite_jni.so | ARM | 0x28 | 32 | ET_DYN | cfd9b3fc0e1bfcce... | 184221696 |
| 96 | lib/armeabi-v7a/libti-monitor.so | ARM | 0x28 | 32 | ET_DYN | 4ae8d2bdfd414228... | 186613760 |
| 97 | lib/armeabi-v7a/libumeng-spy.so | ARM | 0x28 | 32 | ET_DYN | 397f82fcacea972a... | 186712064 |
| 98 | lib/armeabi-v7a/libweibosdkcore.so | ARM | 0x28 | 32 | ET_DYN | - | 187088896 |
| 99 | lib/armeabi-v7a/libxmediaplayer_x.so | Intel 80386 | 0x3 | 32 | ET_DYN | 1f67642ed4f97de5... | 187138048 |
| 100 | lib/armeabi-v7a/libxmediaplayerv7.so | ARM | 0x28 | 32 | ET_DYN | 10a89fb6818c888a... | 188088320 |
| 101 | lib/armeabi-v7a/libxmopendatacrypto.so | ARM | 0x28 | 32 | ET_DYN | - | 189022208 |
| 102 | lib/armeabi-v7a/libyf_ads.so | ARM | 0x28 | 32 | ET_DYN | 5644f45f3af8426d... | 189087744 |
| 103 | lib/armeabi-v7a/libzxprotect.so | ARM | 0x28 | 32 | ET_DYN | 85f343003e8d8805... | 189235200 |
| 104 | lib/armeabi/libBugly.so | ARM | 0x28 | 32 | ET_DYN | 0a25aede6168df6f... | 189628416 |
| 105 | lib/armeabi/libBugly_Native.so | ARM | 0x28 | 32 | ET_DYN | 6d6208d619e458a4... | 189825024 |
| 106 | lib/armeabi/libane.so | ARM | 0x28 | 32 | ET_DYN | b48eb620b9b0febc... | 190038016 |
| 107 | lib/armeabi/libcocklogic-1.1.3.so | ARM | 0x28 | 32 | ET_DYN | - | 190119936 |
| 108 | lib/armeabi/libcrashsdk.so | ARM | 0x28 | 32 | ET_DYN | 5c832cf030a9da1e... | 190169088 |
| 109 | lib/armeabi/libcrypto.so | ARM | 0x28 | 32 | ET_DYN | 7037451db39cf52d... | 190693376 |
| 110 | lib/armeabi/libmediadatacryto.so | ARM | 0x28 | 32 | ET_DYN | 913e1c64947efade... | 191578112 |
| 111 | lib/armeabi/libnesec-x86.so | Intel 80386 | 0x3 | 32 | ET_DYN | 28870b29c70078fb... | 293650432 |
| 112 | lib/armeabi/libnesec.so | ARM | 0x28 | 32 | ET_DYN | 41b39b249a421594... | 288489472 |
| 113 | lib/armeabi/libnetseckit-4.2.4.so | ARM | 0x28 | 32 | ET_DYN | 7c236effc4c67106... | 191627264 |
| 114 | lib/armeabi/libsgcore.so | ARM | 0x28 | 32 | ET_DYN | 8df18e4229439dd7... | 192364544 |
| 115 | lib/armeabi/libssl.so | ARM | 0x28 | 32 | ET_DYN | c7d1dbd521ec81aa... | 192512000 |
| 116 | lib/armeabi/libswsport.so | ARM | 0x28 | 32 | ET_DYN | fbdbe4e8c3425d51... | 192741376 |
| 117 | lib/armeabi/libumeng-spy.so | ARM | 0x28 | 32 | ET_DYN | c6f739dc4c859b8a... | 193134592 |
| 118 | lib/armeabi/libweibosdkcore.so | ARM | 0x28 | 32 | ET_DYN | - | 193560576 |
| 119 | lib/armeabi/libxmediaplayer.so | ARM | 0x28 | 32 | ET_DYN | 834da9f59703fe5b... | 193609728 |
| 120 | lib/armeabi/libxmediaplayer_x.so | Intel 80386 | 0x3 | 32 | ET_DYN | 1f67642ed4f97de5... | 194543616 |
| 121 | lib/armeabi/libxmediaplayerv7.so | ARM | 0x28 | 32 | ET_DYN | 10a89fb6818c888a... | 195493888 |
| 122 | lib/armeabi/libxmopendatacrypto.so | ARM | 0x28 | 32 | ET_DYN | - | 196427776 |
| 123 | lib/armeabi/libzxprotect.so | ARM | 0x28 | 32 | ET_DYN | 85f343003e8d8805... | 196493312 |

## 8. 限制与注意事项

- **libmsaoaidsec.so**（arm64-v8a 与 armeabi-v7a）的 section header 被裁剪到只剩 4 个（NULL + 2 个 STRTAB + DYNAMIC），该库的 .dynsym 不可用，按约定符号返回 null；它的 .dynamic 段仍在，需要时可改用 DT_SYMTAB/DT_GNU_HASH 路径解析。
- 版本节点：本包 libssl.so **没有 OPENSSL_\* 版本定义**。arm64 无 .gnu.version_d；32 位只有 base 版本节点（名字等于 SONAME libssl.so，带 VER_FLG_BASE）。所以 JSON 中 version 为 null 或 libssl.so（后者 version_is_base=true），不代表解析失败。
- 无 .note.gnu.build-id 的 .so 共 19 个：lib/arm64-v8a/libijkffmpeg.so、lib/arm64-v8a/libmsaoaidauth.so、lib/arm64-v8a/libmsaoaidsec.so、lib/arm64-v8a/libneonui_shared.so、lib/arm64-v8a/libneonuijni_public.so、lib/arm64-v8a/librxffmpeg-core.so、lib/arm64-v8a/libweibosdkcore.so、lib/armeabi-v7a/libcocklogic-1.1.3.so、lib/armeabi-v7a/libijkffmpeg.so、lib/armeabi-v7a/libmsaoaidauth.so、lib/armeabi-v7a/libmsaoaidsec.so、lib/armeabi-v7a/libneonui_shared.so、lib/armeabi-v7a/libneonuijni_public.so、lib/armeabi-v7a/librxffmpeg-core.so、lib/armeabi-v7a/libweibosdkcore.so、lib/armeabi-v7a/libxmopendatacrypto.so、lib/armeabi/libcocklogic-1.1.3.so、lib/armeabi/libweibosdkcore.so、lib/armeabi/libxmopendatacrypto.so
- 符号偏移用 PT_LOAD 反推，只覆盖落在 p_filesz 内的 st_value；落在 .bss 等无文件内容区域的符号会置 null 并写原因。
- 未反汇编、未解密任何加固段；对节头被破坏的库只给结构层结论。
- abi_counts 里的 x86 键表示 32 位 i386；other 为空。

## 9. 复现

~~~text
py -3 -X utf8 analyze.py     # 仅标准库；流式 seek 读取，内存占用与 APK 大小无关
~~~
