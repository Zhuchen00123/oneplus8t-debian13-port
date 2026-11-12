# SDX55 支撑单元（设备原样副本）

这三个 unit 是 SDX55 能跑起来的**前提**，由 `kebab-sdx55-modem.service` 的
`bringup.sh` 按顺序拉起（先 RFS/PM，再 MHI，再 EFS）。此前它们只存在于设备
`/etc/systemd/system/`，仓库里没有副本，换机/重装时无法复原——这里补上。

副本是 `scp` 直取，未改动一个字节，SHA-256 与设备逐字节一致（前 16 位）：

| 文件 | 设备路径 | SHA-256（前 16） | 大小 |
| --- | --- | --- | --- |
| `mhi-efs-sync.service` | `/etc/systemd/system/mhi-efs-sync.service` | `5f722499f3e99f30` | 969 B |
| `tqftpserv-sdx55.service` | `/etc/systemd/system/tqftpserv-sdx55.service` | `5d554b9523270a87` | 1154 B |
| `pm-service-native.service` | `/etc/systemd/system/pm-service-native.service` | `4ccbc928cb8d44a2` | 471 B |

它们调用的二进制（不属于本仓库，来自社区 postmarketOS 包，装好后 SHA 如下）：

| 二进制 | SHA-256（前 16） | 作用 |
| --- | --- | --- |
| `/usr/bin/mhi_efs_sync` | `9c0ade17dc18b7f1` | 把 modem 的 EFS 挂到 AP（`-o` 目录模式，**不写分区**） |
| `/usr/bin/tqftpserv-sdx55` | `c95293767979024b` | QRTR **instance 3** 的 TFTP 文件服务（上游只发 instance 1，PCIe 版 SDX55 找 3） |
| `/usr/bin/pm_service_native` | `65a24a3802c49af8` | 原生 Peripheral Manager QMI shim（modem 起来前的 RFS/PM 通道） |

## 安装

```sh
sudo install -m 644 config/sdx55-support/*.service /etc/systemd/system/
sudo systemctl daemon-reload
# 不必须 enable：kebab-sdx55-modem.service 会显式 start 它们
```

前置目录（单元自己会建一部分）：

```sh
sudo mkdir -p /var/lib/mhi-efs /var/lib/tqftpserv /mnt/modemfw
```

`tqftpserv-sdx55.service` 会把 `/dev/disk/by-partlabel/modem_a` 只读挂到
`/mnt/modemfw` 并建 `/lib/firmware/postmarketos/modem_pr` 符号链接；kebab 是
A/B 机器，分区名是 **modem_a**（不是 `modem`）。

## 为什么 `mhi-efs-sync` 必须是 `-o` 目录模式

单元注释里写了：SDX55 固件是 RMTEFS build，指望 AP 托管它的文件系统（Android
用 `/vendor/bin/ks` 走 MHI channel 10）。不服务时 modem 的 EFS 只读、挂在静态
Sahara `efs{1,2,3}.bin` 上，**首次 EFS-Sync 后约 73 秒 ERRFATAL**、NV 永不落盘。

Android 的 `-w /dev/block/... -g mdm1` 形式会把导出区域直接写回 `mdm1m9kefsN`
分区；我们用 `-o /var/lib/mhi-efs` 先落普通目录，**不动本机的真实 IMEI/RF 校准
分区**。实测目录里出现的 `m9kefs1/2` 各 2 MiB，含 `IMGEFS1F` 头。

## 排错

- `/dev/wwan0efs0` 180 秒内不出现 → `mhi-efs-sync` 会一直重启；先看
  `systemctl status kebab-sdx55-modem`，通常是 MHI 栈没起来。
- `tqftpserv-sdx55` 报 instance 冲突 → 机器上还装着上游 `tqftpserv.service`，
  本单元声明了 `Conflicts=`，先 `systemctl disable --now tqftpserv.service`。
- `pm-service-native` 是 `DefaultDependencies=no` + `Before=basic.target`，
  它会抢在 sysinit 阶段起，日志在 `journalctl -u pm-service-native` 最前面。
