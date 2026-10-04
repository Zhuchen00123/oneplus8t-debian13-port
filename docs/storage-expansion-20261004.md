# 当前容量与Debian扩容方案（2026-10-04）

用户明确授权“允许清空userdata，采用7GiB系统盘＋约224GiB数据盘”。本轮已完成系统文件系统在线增长、userdata格式化为ext4并挂载/srv，正常重启与数据持久性验收通过，没有修改分区表或启动镜像。

## 最新结果

| 挂载点 | 文件系统显示容量 | 已用 | 普通可用 |
| --- | --- | --- | --- |
| / | 6.9GiB（7GiB分区） | 2.9GiB | 约3.9GiB |
| /srv | 约220GiB（224.46GiB分区） | 35MiB | 约220GiB |

显示容量扣除了ext4元数据，不能把224.46GiB分区容量等同于全部可用容量。数据盘保留块比例设为0；格式化采用nodiscard、不延迟初始化inode和journal，不是安全擦除所有原始字节。

适合将MC服务端、世界与下载放在/srv下，由MC会话创建相应目录。MC服务应要求/srv已挂载，例如同时配置 `RequiresMountsFor=/srv` 和 `ConditionPathIsMountPoint=/srv`，避免数据盘未挂载时误把游戏文件写到小系统盘。

## 执行前的实测容量

| 项目 | 当前结果 |
| --- | --- |
| UFS各Linux磁盘合计 | 255,911,264,256字节，约255.91GB / 238.34GiB |
| Debian根文件系统 | 4GiB原始ext4；df显示3.9GiB，已用约2.9GiB，普通可用约971MiB |
| 根文件系统所在super分区 | /dev/sda15，7,516,192,768字节，7GiB |
| userdata分区 | /dev/sda28，241,008,750,592字节，约241.01GB / 224.46GiB |

256GB标称容量使用十进制；Linux显示的GiB采用二进制单位。Debian只使用4GiB文件系统是最初部署镜像的尺寸，不是手机仅有4GiB存储。现有super中还有3GiB未被该文件系统使用。userdata未挂载，blkid/wipefs未识别到文件系统签名；这**不能证明它没有数据**，可能包含Android加密内容。

## 方案一：原地利用完整7GiB系统分区

只把现有/dev/sda15上的ext4从4GiB增长到7GiB。分区起止位置、GPT、boot、根UUID和userdata保持不变。预期普通可用空间增加约3GiB，实际df结果会扣除新增文件系统元数据。

已实读ext4的resize_inode、64bit、extent等特性，手机已有e2fsprogs 1.47.2的resize2fs/e2fsck/e2image。手机自带resize2fs说明支持挂载中的ext4在线增长，且工具本身不修改分区表。未运行resize命令。

执行前须再次核对真实根UUID、分区标签、设备映射、7GiB块设备长度、当前4GiB块计数和存储错误；保存当前私有配置及必要的可恢复备份。增长成功后核对块数、文件读写校验、SSH、健康/限充状态。不在挂载的根文件系统上强制运行修复式fsck。出现文件系统错误时停止，使用已核验的救援环境和备份恢复，不能把简单缩回当作保证恢复。

## 方案二：7GiB系统盘加约224GiB数据盘

将本机已核验的userdata(/dev/sda28)格式化为ext4，按新文件系统UUID挂载到/srv，存放MC世界、服务端、下载和备份。这样系统与大容量数据分开，不需要扩大super、移动中间分区或修改GPT布局；数据盘挂载故障也应保留系统USB/SSH维护能力。

**这会清除原Android userdata内容，需要明确授权。** 既有boot_a/dtbo_a/super持续授权不包含userdata。原厂26个分区备份不包含userdata；目前没有已核验的userdata或其中个人文件的可恢复备份。本机F盘当前约103GB空闲，不足以保存整份241GB原始userdata；如需要保留，应先准备足够大的外部介质或完成实际可恢复的数据备份。

得到明确选择后再准备对应部署脚本。格式化前再次核验设备路径、PARTLABEL、容量、未挂载状态及无进程使用；没有授权不执行mkfs/wipefs写入。挂载配置先备份原fstab、按UUID设置，做实测写入/fsync/直接读取校验；重启验收前确认USB/SSH和限充仍可用。

此方案增加的是Debian可使用的数据容量，根目录/本身仍约7GiB。如果要求/本身变成200多GiB，应另行准备迁移根文件系统和更新启动参数的方案，不能直接把不相邻的super与userdata拼成一个分区。

## 实际执行与验证

- 执行前当前Debian文件及配置通过二进制SSH流备份到主机 `backups/storage-before-expand-20261004-200202/`。归档1,780,637,710字节、33,313个成员，完整解码读取及SHA-256通过；保留fstab、SSH和限充服务。它是运行中文件归档，不是原子块快照，不包括userdata、虚拟挂载、临时目录和日志；原Android userdata没有备份，已按明确授权格式化。
- [扩容工具](../scripts/linux/expand-kebab-storage.py)默认只读预览；实际写入需 `--apply --erase-userdata`。实机预览已核对根UUID、两个标签映射/容量、主备GPT CRC与目标范围、userdata无挂载/块设备占用、/srv为空、无Java负载及限充active。主机/手机脚本哈希一致后才执行；再次运行遇到数据盘已有签名/挂载会拒绝，避免意外二次格式化。
- 在线增长根ext4至1,835,008个4096-byte块（7GiB），原fstab与限充/健康程序和服务文件哈希保持；随后格式化原userdata为kebab-data ext4，未使用强制格式化参数，按UUID挂载到/srv。原fstab另存手机 `/var/lib/kebab-storage-deployment/before-*/fstab`。
- 新fstab经findmnt验证无错误；数据盘使用noatime、nofail和10秒设备等待，维护链路不应因缺盘卡住。保存32MiB随机验证文件并fsync，使用dd的直接I/O读取，SHA-256匹配；主备sda GPT哈希与执行前完全相同。
- 经一次systemd正常重启，boot_id改变，严格USB SSH恢复；/srv自动挂载/dev/sda28，32MiB文件直接读取哈希不变，两个文件系统容量保留、主备GPT未变。A槽successful=true、retry保持5；srv.mount及限充服务active，无failed unit。限充等待3次新采样后恢复停充读回（最终采样另存local）。
- 格式化工具实际提示原厂userdata分区偏离512KiB最小I/O对齐393216字节；它仍对齐4096-byte逻辑/物理块。保留原厂布局，没有据此重分区；当前读写校验通过，性能影响尚未做基准测试，不能把此提示当作介质损坏。

原始执行、完整fstab、新文件系统UUID和验证manifest仅留 `local/storage-expansion-20261004/` 与手机root私有目录，不上传镜像、密钥或个人数据。此授权只覆盖本次指定userdata格式化及既有系统空间增长，不扩展为分区重排、NV写入或任意数据删除。
