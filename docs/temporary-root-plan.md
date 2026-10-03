# KB2000 临时 root 与屏幕取证

日期：2026-10-03。用户在已解锁并完成普通 ADB 检查后要求“继续啊，怎么提供权限”。本阶段准备匹配原厂 boot 的 Magisk 临时启动，权限用于面板读取和本机原厂分区备份；永久 root、刷 recovery、刷其他分区、切槽及重新锁定不在范围内。

## 已完成准备

- ColorOS `KB2000_14.0.0.602(CN01)`，系统及 bootimage 指纹均为 `OnePlus/OnePlus8T_CH/OnePlus8T:14/UKQ1.230924.001/R.1983865_1_2:user/release-keys`，安全补丁 2024-08-05；ADB UID 2000，无 su。
- 原厂 fastbootd 的 `is-userspace=yes`、slot `a` 已读取；`fetch boot_a` 返回不支持 fetch，没有产生分区备份，随后已重启回 ColorOS。
- 从厂商 HTTPS CDN 按范围读取 OTA，metadata 的型号、版本、指纹、增量号和安全补丁与实机一致。完整 OTA 为 6,500,912,607 字节，慢速全量下载主动停止，保留 726,089,728 字节 partial；按需读取约 53.94 MB 即取得所选分区的数据。
- 所选操作数据 SHA-256 已验证；固定上游 `payload-dumper-go 2.1.0` 验证输出后，主机再次核对 4 个镜像的字节数及 payload manifest SHA-256。辅助构造的 selected-payload.bin 只包含选定数据，不能作为完整 OTA 安装输入。
- 官方原厂包镜像与手机原始分区备份分别记载。未验证完整 OTA 签名，未取得本机 EFS/NV/persist 等镜像；这些资料不能声称是完整救砖包或全盘备份。
- 官方 Magisk v30.7 APK 与 GitHub 发布资产 SHA-256 一致。修补组件来自该 APK，在这台手机的 `/data/local/tmp/` 独立目录以普通 shell 权限运行官方 boot_patch.sh，未执行 flash_image、分区刷写或直接安装。

| 来源镜像 | 字节数 | SHA-256 |
|---|---:|---|
| boot | 100663296 | `cb4e546fe00141cb315cdaf22980bcba6a96e82d6365350311697f9579816991` |
| recovery | 104857600 | `6286ad199599cf15fc4a9634d213432985167ed05dd2cf59e2c57e8a20f59072` |
| dtbo | 25165824 | `3e4a4aadeed79d5293bb2bfa655fafb7d40f7cb5ee0b1fd67bf20e1c02792706` |
| vbmeta | 8192 | `38e53e24e22d2f8f9e7532a2db6c91877f473cfbb1830a52281b3c4d4c4c5a25` |

候选 `magisk-30.7-temporary-boot.img` 为 100663296 字节，SHA-256 `545c0a40d4b3141a425528061937e1c029395c51891125343d83e9a122c3cb97`，手机端和电脑端一致。实测 Android boot header v2、page size 4096。修补前后内核、DTB、命令行、地址、OS 编码字段均相同，ramdisk 已改变；原厂头内 OS 字段编码 12，保留该厂商原值，实际版本匹配依据为 OTA metadata 与实机指纹。

修补参数：KEEPVERITY=true、KEEPFORCEENCRYPT=true、PATCHVBMETAFLAG=false、RECOVERYMODE=false、LEGACYSAR=false。原厂与候选镜像、来源清单、修补日志、APK 和本地主机辅助脚本已保存到 `backups/temporary-root-preflight-20261003-145345/`，13 个文件；原厂及候选镜像副本 SHA-256 通过。

## 执行与返回系统

1. 安装官方 Magisk 管理应用，用于随后由手机用户给 Shell 授权。应用安装本身不等于已 root；若手机要求安装确认或身份验证，由用户完成。
2. 重新确认型号、ColorOS 构建、槽位、unlocked，以及候选镜像 hash，然后重启 bootloader。
3. 对指定序列号执行 `fastboot boot` 加载候选镜像到内存，不执行 flash、Direct Install、刷 vbmeta 或 disable-verification。
4. 系统启动后通过 `su -c id` 触发 Magisk 的 Shell 授权提示；由用户在手机授予诊断所需权限。确认 UID 0 后才读取面板节点、备份原厂分区，校验每份分区备份的字节数和两端 hash。
5. 获取临时 root 可能创建 Magisk 的应用数据、日志及运行状态文件，不表示手机全程零写入。磁盘上的启动分区保持原厂镜像；正常重启可回到原厂 boot，管理应用和相关数据可能保留。
6. 若临时镜像不能启动，先返回 bootloader 使用指定序列号的 `fastboot reboot` 或强制重启回原厂系统。没有分区刷写时不自动执行恢复刷写；若仍异常，另行调查并提出具体恢复方案。

管理应用安装已返回 Success，包 `com.topjohnwu.magisk` 已核验。再次检查匹配构建、指纹、槽位和候选 hash 后，执行指定序列号的 `fastboot boot`，发送与 Booting 均返回 OKAY。系统 `sys.boot_completed=1`，ADB 重新连接，slot `_a`、unlocked 保持不变，SELinux Enforcing。

Magisk 的 `/debug_ramdisk/magisk` 和 su 链接已出现，但 ColorOS 的默认 PATH 不包含该位置。第一次 `/debug_ramdisk/su -c id` 返回退出码 13 / Permission denied，尚不能标记诊断 shell 已取得 root。打开管理应用并读取界面后发现手机停留在锁屏；已请求用户解锁、保持亮屏并处理 Shell 授权，不点击 Direct Install 或额外设置重启。

分区备份工具增加 `-SuExecutable /debug_ramdisk/su` 支持，保留原有默认 `su`。PowerShell 语法检查通过；包含分号的路径在 ADB 调用前被参数校验拒绝。原始分区备份与 root 权限仍待用户授权后验证。

用户打开管理器并报告额外设置提示后，观察到系统再次启动、原先 su 消失；临时环境已退出，未将此归因于未经核实的具体点击。重新核验并再次 `fastboot boot` 同一候选后，系统正常启动，`/debug_ramdisk/su` 再次出现且 PATH 可找到 `/product/bin/su`。Shell 的 id 请求仍立即返回退出码 13 / Permission denied；此时界面仍是锁屏，已要求用户解锁并检查超级用户页的 Shell 授权。26 个备份分区的 by-name 计划核对通过，无缺失路径，尚无分区镜像。

为避免授权时自动熄屏，尝试保存并临时修改 USB 保持亮屏设置；ColorOS 拒绝 WRITE_SECURE_SETTINGS，修改未生效，原设置未改变。未关闭权限监控、SELinux 或绕过锁屏。原设置值只存本地日志。

## 一手来源

- [Magisk 官方安装说明](https://topjohnwu.github.io/Magisk/install.html)：使用匹配原厂镜像并在本机修补；不使用别人提供的 patched 镜像。
- [Magisk v30.7 官方发布](https://github.com/topjohnwu/Magisk/releases/tag/v30.7)，APK SHA-256 `e0d32d2123532860f97123d927b1bb86c4e08e6fd8a48bfc6b5bee0afae9ebd5`。
- [官方 boot_patch.sh 固定版本](https://github.com/topjohnwu/Magisk/blob/v30.7/scripts/boot_patch.sh)：本轮仅调用文件修补流程，不调用直接安装流程。
- [payload-dumper-go 2.1.0](https://github.com/ssut/payload-dumper-go/releases/tag/2.1.0)，固定源码提交 `c3a50ba8d784764c06143c1f82971cd81a77f9d6`；Linux amd64 发布包 SHA-256 `bbbb53a71955c69272afdef7bc7e83a1bb1770f453d0c21dcaf61a3ba0463c11`。
- [TWRP 8T 官方方法](https://twrp.me/oneplus/oneplus8t.html) 支持 fastboot 临时启动，但其 [发布目录](https://dl.twrp.me/kebab/) 的当前镜像为 Android 11 分支，没有作为本机 ColorOS 14 的已验证兼容恢复环境使用。
- OTA 来源：`https://componentota-manual-cn.allawnfs.com/component-ota/24/08/19/442d4002d2cd40d0809bb2746bc161d6.zip`，亦可从厂商 `component-ota-afs.coloros.com` 的同路径访问。保存 metadata 与来源参数，未安装 OTA 或升级系统。
