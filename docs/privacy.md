# 脱敏与上传范围

本仓库按明确文件白名单从设备管理项目导出，未直接推送设备管理仓库及其旧 Git 历史。原始交接和阶段取证文档没有直接复制，公开说明经过重写。

## 包含

构建/格式校验脚本、合成回归测试、Debian 通用配置、本机驱动源码补丁、固定的公开上游 URL/commit/补丁 hash，以及脱敏后的结构、思路与验证限制。USB 192.168.8.0/24 是配方的通用私有网络设计，不是个人公网地址。

## 排除

所有 Image/boot/rootfs/DTB/DTBO/模块二进制、固件、OEM/NV/校准、分区镜像、可用系统归档、原始设备日志、原始交接、私有 /etc overlay、SSH 密钥与 authorized_keys、实际硬件序列号、IMEI/ICCID、蓝牙硬件地址、machine-id、根 UUID、用户路径、照片及备份。

实际设备配置 config/device-kb2000.json 不发布。boot 模板删除原厂输入 hash 和运行/授权记录，stock_boot_sha256 须在本地校准后填写。通用 boot header/offset 属技术参数，保留用于配方审查，不能替代另一台设备调查。

测试中的 0123456789abcdef0123456789abcdef 等身份值是显式的合成 fixture。源码中的 PRIVATE KEY 字符串用于格式检查，不包含任何密钥材料。补丁 hash 用于校验公开的源码差异，不是固件或设备身份。

## 发布前检查

检查文件白名单、扩展名、UTF-8 文本、大小、token/密钥格式、个人路径和设备身份字段；检查 JSON、Python/Bash/PowerShell 语法与适用主机测试；核对提交树和新仓库历史。输出与验证日志保持本地。

.gitignore 是辅助措施；发布白名单及提交内容复核是实际边界。不要通过 git add -f 上传忽略文件，不要把个人 fork 的运行产物放到 releases、附件或 LFS。
