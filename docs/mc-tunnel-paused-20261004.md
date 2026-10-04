# MC/穿透暂停与分工（2026-10-04）

用户明确暂停当前会话的MC与穿透，先完成服务器基础配置、电池与充电策略；随后准备另开会话继续MC，并要求提供提示词。

网络已完成：gdut手动认证、外部HTTP/HTTPS、DNS、NTP及IPv4 TCP443/TLS通过。IPv6校外入站未通过，临时探针关闭、超时失败状态已清理。MC、隧道客户端及新CNB环境均未部署或创建。

指定参考 `F:\codexprojects\mcfuwuqi\cnb-tunnel` 的README已读：自写Rust mcbridge，CNB提供WSS/443入口、旧DSW主动反隧道、玩家需要本地forward；拟将旧DSW落点迁到手机ARM64。源码协议及环境状态未审核。参考仓库已有README修改，未由本会话改动。此前“不试免费中继”的选择不应被误读为拒绝用户自己的CNB参考方案；未经选择不能安装playit等服务。

新会话使用[提示词](mc-cnb-resume-prompt-20261004.md)，独立工作树并只负责MC/隧道。当前会话负责电池/温度、电源、日志和基础监控，不继续运行CNB参考脚本或修改旧DSW，不并行碰游戏负载。公共README/AGENTS/设备状态文件由主会话整合。

私有网络证据仍在 `local/wifi-gdut-20261004/`，公开详细记录在[GDUT接入记录](gdut-wifi-20261004.md)。不复制账号、地址或默认令牌到公开导出。
