# 相机 fastboot 循环：agent 执行说明

- 状态：前摄 --no-reboot 已验证；PON 进入 fastboot 已验证；--boot-image 已用 dtb_offset=0x03000000 的镜像跑通（fastboot boot、抓帧、回 baseline）。
- 日期：2026-11-10。
- 关联：[r4-camera 任务](r4-camera-task-20261110.md)、[启动记账修复](boot-success-repair-20261004.md)。

## 1. 文件

- 设备侧测试：[camera-capture-test.py](../scripts/linux/camera-capture-test.py)
- 主机侧循环：[fastboot-camera-loop.py](../scripts/linux/fastboot-camera-loop.py)
- 槽位只读检查：[boot-slot-state.py](../scripts/linux/boot-slot-state.py)
- 成功位置位：[mark-kebab-boot-success.py](../scripts/linux/mark-kebab-boot-success.py)
- PON 模块：[ponboot.c](../scripts/linux/ponboot/ponboot.c)
- 重启 helper：[kebab-reboot-fastboot.sh](../scripts/linux/kebab-reboot-fastboot.sh)

## 2. 当前已验证

在现役 r4 上执行：

~~~
py scripts/linux/fastboot-camera-loop.py --no-reboot --camera front
~~~

结果：ok=true，boot_id 前后一致，前摄 1 帧 2,628,608 B（stride 2416），解码 PNG；帧偏暗，链路正常。运行记录里包含 slot 状态。

boot 模式也已实测（2026-11-10 23:24 / 23:27）：`boot-r4-dtb48.img`（SHA c50ccdeb...）经 PON 进 fastboot，`fastboot boot` 成功，抓帧后正常回 baseline；`returned_to_baseline=true`。原 r4 镜像 dtb 地址 0x1f00000 与 32 MB ramdisk 重叠，`fastboot boot` 会报 Load Error；必须用 0x03000000。

PON 入口已实测：模块把 PON spare 写成 0x2 后，ABL 进入 fastboot，product=kona、current-slot=a；fastboot reboot 后正常回 Debian。helper 已安装：

~~~
/usr/local/sbin/kebab-reboot-fastboot
/usr/local/lib/kebab-reboot/ponboot.ko
~~~

当前槽位（只读）：

- boot_a：active、successful=1、tries=6、unbootable=0
- boot_b：inactive、successful=1、tries=6、unbootable=1

## 3. Agent 现在只做这一段

1. 运行 --no-reboot --camera front。
2. 读取 local/camera-runs/<run_id>/run.json，检查 ok、boot_id 前后、slot.boot_a。
3. 报告 run_id、slot 状态、raw_bytes/stride/mean/min/max/result。
4. 失败附 error 和远端 stderr；不要自行改槽位或刷写。

## 4. boot 模式（已验证，候选镜像仍需按布局打包）

~~~
py scripts/linux/fastboot-camera-loop.py --boot-image <本机 boot.img> --entry pon --camera front
~~~

- --entry pon 是默认入口；helper 先检查槽位健康，再写 PON magic 并重启。
- 主机脚本 preflight 调用 /usr/local/sbin/kebab-boot-slot-state；如果 boot_a 不是 active/successful、tries<3 或 unbootable，直接拒绝自动重启。
- 主机脚本会解析 boot.img header：如果 ramdisk_addr + ramdisk_size > dtb_addr，直接拒绝，并提示重建 dtb_offset。
- 打包必须显式用 --dtb_offset 0x03000000；ramdisk 结束 0x2eea827，0x1f00000 会被覆盖。
- --boot-image 必须是正确的 boot.img，不能是 100 MB 的 boot 分区镜像。
- 若 fastboot boot 失败：脚本会 fastboot reboot 回 r4，记录错误，不写槽位、不自动 flash。
- 回到 baseline 的判据是 boot_id 改变、kernel 仍是 baseline release，而不是 boot_id 与启动前相等；boot_id 每次重启都会变。

## 5. 启动记账事故（2026-11-10）

“系统文件被损坏，无法开机”不是文件系统损坏，而是 ABL 的 A 槽记账耗尽：successful=no、retry=0、unbootable=yes。

原因：r4 槽没有在每次启动后置成功位；ABL 每次启动递减 retry，约 7 次后归零。连续测试 PON 重启把它触发出来。

恢复过程：

1. 手动进 fastboot。
2. fastboot flash boot_a 写回已验证的完整分区镜像 local/kexec-test/boot_a-r4.img（100,663,296 B，SHA 4838d489...）。
3. fastboot reboot；r4 正常启动。
4. 用 kebab-mark-boot-success 置 boot_a 成功位；主备 GPT 写后读回通过。
5. 当前 boot_a successful=1、tries=6，正常重启不再递减。

硬规则：

- boot_a 的 successful=0 或 tries<3 时，禁止任何自动重启/PON 循环。
- 每次 fastboot flash boot 后，首次成功启动必须置成功位。
- 不 fsck、不 wipe、不重建分区表；先读 GPT 槽位，再决定恢复。
- 恢复优先用完整分区镜像 flash；不要把 100 MB 分区镜像当 boot.img 去 fastboot boot。
- 连续两次启动失败/无 SSH 就熔断，保留 fastboot 入口。

## 6. 禁止

- 不运行 fastboot flash/erase/format/-w/set_active 作为自动流程的一部分。
- 不写 boot/GPT/super/vbmeta/misc，除非是明确批准的恢复步骤。
- 不在槽位不健康时跑 PON 入口。
- 不把 partial（画面暗/遮挡）当链路失败，也不把 pass 当画质验收。
- 不在服务器维护窗口之外跑 boot 模式；候选启动期间 DSH/酒馆/三档会下线。

## 7. 输出与停点

- 每次运行一个目录：local/camera-runs/<run_id>/，含 run.json、pre.json、front.json、front-preview.png、front-frame.raw。
- no-reboot 验收：ok=true 且 boot_id 不变。
- boot 验收：ok=true 且 returned_to_baseline=true；否则保留现场并停止。
- 任一 SSH/SCP/fastboot 失败，停止并报告，不无限重试。

## 8. 成功位修复命令（明确批准后）

~~~
sudo /usr/local/sbin/kebab-mark-boot-success \
  --expected-boot-sha256 4838d489afb25402c64c439b35d58701ec4dacecb879825ce3187790b7cc2ac5 \
  --expected-boot-bytes 100663296 \
  --expected-rootfs-uuid bc52fbbe-777b-47c8-84fc-ba56db7a8813 \
  --apply
~~~

默认不带 --apply 是只读预览。
