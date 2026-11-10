# Git 管理

管理仓库保存规范、来源锁定、脚本、配置与脱敏证据。第三方仓库独立下载，镜像/个人信息忽略。现已配置origin指向[GitHub](https://github.com/Zhuchen00123/oneplus8t-debian13-port)，但管理仓库与公开库历史仍不同，不能直接推送本地完整refs。此前“未配置remote”仅为早期状态。

## 2026-11-10 SDX55 GNSS 打通发布

用户要求更新 git。本地管理分支 `codex/sensors-20261107` 提交 `894613e`（`AGENTS.md`、`docs/sdx55-gnss-feasibility-20261110.md`、`docs/android-lab-p0-plan-20261110.md`；3 个文件、750 insertions，未并入工作区其它既有改动）。

公开导出从已审核的 `codex/sensors-20261110` 末端 `0f917230b7b021524922065edaf94380c5cab9c7` 独立历史新建分支，只新增公开版文档 `docs/sdx55-gnss-20261110.md`，提交为 `dfab053ec7c8d77b6569a4ad4913d9689409030a`，推送至 [codex/gps-20261110](https://github.com/Zhuchen00123/oneplus8t-debian13-port/tree/codex/gps-20261110)。远端 SHA 回读一致；`main` 仍为 `99c24fe…`，未合并或创建 PR。

| 公开文件 | blob SHA-1 | SHA-256 |
| --- | --- | --- |
| `docs/sdx55-gnss-20261110.md` | `54b8b2745e41be02274c21cc70a9455139d383f8` | `d9dc46c1cef497d736cf944a239b25655a94facaa21846f9c35b4839dce0ca40` |

公开文档为独立重写版：不含精确坐标、`/srv/`、家目录、SSH 配置、本机 cell 位置或私有部署路径；只写硬件/协议、最小 QMI 序列、实测指标、GeoClue 接入与限制。守护进程、systemd 单元、agent 与私有证据未导出。隐私扫描（内网 IP、`/srv/`、`C:\`、`local/`、`gho_`、`sk-`、精确经纬度、密钥头）无命中；文件 UTF-8 无 BOM、LF。本次 `git push` 走 Windows git + `socks5h://127.0.0.1:12080` 直接成功，未使用 GitHub API 回退。

## 2026-11-10 磁力计与 SSC 读数器发布

用户要求更新 git。本地管理仓库提交 `7a2b79f`（磁力计打通、SSC 读数器、只读检查工具与文档；仓库里既有的 36 个暂存文件保持暂存、未并入本次提交）。

公开导出沿用独立历史：基于 r4 Venus 公开分支末端 `26f33fadc1f642f1b0f9c0c51e8d9f065c4453db`，只新增 5 个白名单文本，提交为 `6852d7c89a06505e59014aa207331a9cbf5fd66d`，推送至 [codex/sensors-20261110](https://github.com/Zhuchen00123/oneplus8t-debian13-port/tree/codex/sensors-20261110)。远端回读一致；`main` 仍为 `99c24fe…`、`codex/r4-venus-20261105` 仍为 `26f33fa…`，未合并或创建 PR。

| 公开文件 | blob SHA-256 |
| --- | --- |
| `docs/sensor-magnetometer-20261110.md` | `93af31c7815a86ddc23cd93f0df75964742387fbd599e9a5d747f756eb3ea887` |
| `scripts/linux/kebab-mag-read.py` | `6f1c3f3d5bd8b07e5836bb150f9f1c74980f9f0b27f66a831a85ac7583fdb12e` |
| `scripts/linux/kebab-ssc-inspect.py` | `749ee61761a03dd669e89813c34d7635c596065f2c038745dde04a6860ce0c0f` |
| `scripts/linux/test-kebab-mag-read.py` | `9c385ecaa74cffcab76c08aa438ffbab214b76c01f1bac339882a22af7017e88` |
| `scripts/linux/test-kebab-ssc-inspect.py` | `89d57dcfdc555d256054ecbd90e91699b86bfbb9253f151c25e19513ac94b15e` |

公开树在无设备环境下自测通过（读数器 34 项、检查工具 9 项）；5 个文件归一化为 UTF-8 无 BOM、LF；私有信息扫描（内网 IP、`/srv/`、家目录、`gho_`/`sk-` 令牌、密钥头）无命中。**把传感器申报进 SMEM130 的看护模块、安装脚本与回退脚本属于本机私有部署，没有导出**，公开文档头部已注明这一点，并说明公开部分只覆盖协议流程、参数推导与上层读数器。

导出目录在忽略的 `local/git-publish-20261110/`。网络方面：本机可用代理是 Windows 侧 SOCKS5 `127.0.0.1:12080`（hysteria），WSL 里配置的 `127.0.0.1:60243` 已不可用；因此本次 `clone`/`push` 用 Windows git 加 `-c http.proxy=socks5h://127.0.0.1:12080` 完成，提交在 WSL 侧执行。后续同一台机器上发布仍按此法，不要直接把管理分支推成公开同名分支。

**同日第二次发布（E35，前摄光感）**：本地管理仓库提交 `061561f`（AGENTS.md、新文档、读数器与前摄光感支持、测试；96 行）。公开导出在同一分支上新增 4 个白名单文件，提交为 `33304b0c5d2db14d2d0777ac8dee15919e02d92b`，推送 `6852d7c..33304b0`，远端回读一致（`main` 仍 `99c24fe…`、`codex/r4-venus-20261105` 仍 `26f33fa…`）。公开树在无设备环境自测 44 项通过（读数器 35、检查工具 9）。

| 公开文件 | blob SHA-256 |
| --- | --- |
| `docs/sensor-devinfo-declarations-20261110.md` | `d3b19440b542ed21c1a15f4ad5330ed30124af7ee379c1409cb1f62dc9c4d832` |
| `docs/status.md` | `6ecd97a8a06ca5df99c7cabafa7cd42a28fd602b7b6bfa5de995c392ecb969b6` |
| `scripts/linux/kebab-mag-read.py` | `93a4c7abfd3221c8ca2b876385c58d35312bb7aca10350605e1f83dc95ea324a` |
| `scripts/linux/test-kebab-mag-read.py` | `fa8d73c343f7f21d3f24cf1f6574b3f4dca43ea5b34fa4aeea4fa8041e6c040c` |

新文档同样只写协议流程、结构偏移、参数推导与读数器用法；申报用的看护模块、安装与回退脚本仍属本机私有部署，未导出。私有信息扫描无命中，文件为 UTF-8 无 BOM、LF。
## 2026-11-05 r4 Venus与限充发布

用户明确授权正式启用r4并更新Git/推送。手机boot_a已更新并正常启动，Venus自动绑定和720p90帧逐帧对照通过；实际部署与回退记录见[本地完整报告](venus-video-decode-20261105.md)。本地管理提交仅纳入本次r4/Venus、限充修复、相关测试与状态记录，其他原有暂存工作保持。

公开导出继续使用独立历史，基于此前已审核的三段键公开提交 `d6fac93b2f3648dcc90ebb8e6af82832a928acbb`，没有推管理仓库的旧refs。14个白名单文本（通用0008/0009补丁、解码探针、限充策略、启动模板和测试、独立说明）已提交为 `26f33fadc1f642f1b0f9c0c51e8d9f065c4453db`，推送至 [codex/r4-venus-20261105](https://github.com/Zhuchen00123/oneplus8t-debian13-port/tree/codex/r4-venus-20261105)。远端SHA回读一致，main仍为99c24fe…，未合并或创建PR。

公开启动模板将本机manifest路径/摘要改为通用本地配置入口；0009仅重写说明头以移除私有证据路径，diff hunks保持，公开patch SHA另记入公开sources.lock。原厂固件、镜像、分区备份、原始日志、完整私有文档和管理历史未导出。公开目录在忽略的 `local/r4-git-publish-20261105/`，审核白名单/实际blob SHA及回执在 `local/venus-enable-20261105/public-*`。发布前后14个实际Git blob均匹配审核SHA；解码9、Linux init9、限充11测试通过，导出C测试使用最终真实r4头经ASan/UBSan及12万随机PASS。

## 2026-10-07 EmbeddingGemma 2 ONNX Q8发布

用户明确要求提交并推送。本地试跑脚本、固定模型/依赖、清单和完整脱敏记录提交为`8b2fd1c2fd3e999fbeae7b17c0ca7b5bb3989e5c`，分支`codex/embeddinggemma-q8-trial-20261007`；按项目既有规则使用单次`Codex <codex@localhost>`身份，没有修改Git身份配置。用户原有未跟踪的电源路径文档保留，不纳入此次提交。

公开导出从`origin/main`的`99c24fe9516cc81be2c907eb6e862ac983a26227`独立历史开始，提交为`d8ae2f5f335eabc0c58632deb20f40cb15963d51`，已推送至[ONNX Q8试跑分支](https://github.com/Zhuchen00123/oneplus8t-debian13-port/tree/codex/embeddinggemma-q8-trial-20261007)。10个白名单文本增量，公开说明独立重写；模型权重、图片、私有原始报告和本地旧管理历史未导出。全公开树65个文本文件的敏感模式、JSON/Python和相对链接检查无发现，65个实际提交blob逐项匹配审核SHA-256；远端分支SHA回读一致，main回读仍为上述提交。未合并或创建PR。

独立发布目录为忽略的`local/embeddinggemma-git-publish-20261007/`；白名单、审核清单、隐私检查在`local/embeddinggemma-q8-20261007/`。后续不能直接从本地管理分支推送同名公开分支，两者历史不同。试跑状态见[本地完整记录](embeddinggemma-q8-trial-20261007.md)，公开内容见[GitHub实测摘要](https://github.com/Zhuchen00123/oneplus8t-debian13-port/blob/codex/embeddinggemma-q8-trial-20261007/docs/embeddinggemma-q8.md)。

## 2026-10-06 面板与三段键发布

用户明确要求更新Git并推送。本地既有面板暂存区单独提交为`a9205e2`（codex/panel-network-20261006），三段键源码、服务、测试及实施记录提交为`c52b659`（codex/alert-slider-20261006）。保留已有改动，未修改全局Git身份。

公开发布从已审核的`18694a2`独立历史导出，不从管理仓库推送：

| 分支 | 公开提交 | 内容 |
| --- | --- | --- |
| [codex/panel-network-20261006](https://github.com/Zhuchen00123/oneplus8t-debian13-port/tree/codex/panel-network-20261006) | `e322ee466e17f9c1ae47c627c09ecc3fdcfdb8b5` | 面板第三页、只读网络快照及独立公开说明 |
| [codex/alert-slider-20261006](https://github.com/Zhuchen00123/oneplus8t-debian13-port/tree/codex/alert-slider-20261006) | `d6fac93b2f3648dcc90ebb8e6af82832a928acbb` | 在面板分支上增加IST8801驱动、合成测试、本地校准输入模板及公开说明 |

公开三段键包装脚本读取本地校准JSON，测试改用合成值；不上传本机校准、模块二进制、原始采样、私有交接或本地旧历史。实际公开树83个文本blob的敏感模式、扩展名与相对链接检查通过，提交后逐个blob与审核SHA-256清单比对。面板11项回归、三段键262,996个分类点及去抖、公开包装脚本语法检查通过。

两个分支原子推送，远端SHA回读一致；main仍为`99c24fe9516cc81be2c907eb6e862ac983a26227`，没有合并或创建PR。独立发布目录在忽略的`local/git-publish-20261006/`，白名单与审核清单在`local/alert-slider-20261006/`。后续仍沿用独立脱敏导出流程，不能因origin存在就推送管理分支。

## 当前文档与公开导出

2026-10-04已有发布核验记录：公开库上传的是独立审核文件集，不含镜像、固件或私有历史。导出/脱敏脚本和核验材料在local/github-export-20261004/；它们是本机私有流程材料。后续发布先重新核验文件允许清单、隐私、链接依赖与实际Git blob，不能将新文档指向未导出的私有产物。

此前文档/FigJam整理未同步GitHub，当时gh只读查询返回401。随后用户明确授权发布状态面板：通过GitHub连接器复核权限，Git在沙箱外完成公开库读取和分支推送。从公开main的独立脱敏历史导出12个文本增量到[codex/status-panel-20261004](https://github.com/Zhuchen00123/oneplus8t-debian13-port/tree/codex/status-panel-20261004)，提交`18694a21863d155817e539ac3d59c8406280d0e1`；main仍为`99c24fe9516cc81be2c907eb6e862ac983a26227`。没有发布本地完整refs、原始截图/记录、镜像或备份，也没有合并或创建PR。

面板导出入口在local/status-panel-github-20261004/：明确12文件允许清单、完整69文件隐私/链接检查无发现、实际提交69个blob全部匹配SHA-256清单，远端SHA与GitHub提交回读通过。公开与私有管理历史仍分别维护，公开提交与本地源提交`16ab07f`的代码哈希一致；本地发布记录提交不需要上传公开库。

## 分支与提交

- `main`：已审核的项目状态。
- 管理仓库主题分支使用 `codex/` 前缀，例如 `codex/kebab-7.3`、`codex/debian13-boot-prep`；一个分支只处理明确目标。内核独立仓库已有 `port/` 分支按原记录保留。
- WSL 中的内核仓库保留纯 `baseline-sm8250-7.2.0` 标签，补丁在 `port/kebab-7.2-debian13` 分支按顺序提交。
- 每阶段记录社区来源 SHA、自己修改、实际验证与未测试事项；升级时保留旧 refs。
- 初始化提交如没有配置身份，使用单次 `Codex <codex@localhost>` 自动化身份，不修改全局 user.name/user.email。后续设置用户自己选择的身份。

```powershell
git status --short --branch
git diff --stat
git switch -c codex/debian13-boot-prep
# 明确列出要提交的文件；检查 git diff --cached 后再提交。
git add config/debian13.config docs/porting-plan.md
git diff --cached
git commit -m "debian: prepare initramfs integration"
```

内核补丁在 Linux 中导出：

```sh
git -C ~/oneplus8t-build/linux log --oneline baseline-sm8250-7.2.0..HEAD
git -C ~/oneplus8t-build/linux format-patch \
  -o /mnt/f/codexprojects/oneplus8t/patches/kernel \
  baseline-sm8250-7.2.0..HEAD
```

没有新本机修改时，不必把社区的 21 份补丁复制进管理仓库；`sources.lock.json`、来源仓库和其许可证保留了出处。

## 快照与恢复

```powershell
pwsh -NoProfile -File scripts/Backup-Project.ps1
git bundle verify backups/project-时间戳/project.bundle
```

恢复到全新目录时先验证哈希和 bundle，再运行：

```powershell
git clone 路径/project.bundle 新目录
```

`working-tree.zip` 是备份时的文件内容，包括跟踪文件修改和未忽略的新增文件；`status.txt` 记录暂存区/工作区状态。先对照差异，再将 ZIP 解压到新的恢复目录，避免覆盖正在使用的项目。ZIP 不精确复刻 Git 暂存区，bundle 才是提交历史。

## 提交前

核对 `.gitignore` 与 `git ls-files`，确保 local/firmware/backups/artifacts/sources 和分区镜像、EFS/NV、密钥未跟踪。禁止 `git add -f` 导入设备私密资料。不执行 `reset --hard`、`clean -fd` 或删除用户分支来处理问题。

## 11/10 第三次公开导出（codex/sensors-20261110 → 0f917230）

内容：全量扫描结论、前摄光感字段更正、接近光解码、铁磁干扰实测、a_vector 算法申报；
文件：`docs/sensor-devinfo-declarations-20261110.md`、`scripts/linux/kebab-mag-read.py`、
`scripts/linux/test-kebab-mag-read.py`（41 项测试通过，隐私关键词扫描干净）。

**这次是走 GitHub API 建的提交，不是 `git push`**：

- 现象：`git push`（HTTPS）无论走直连还是走 socks 代理都在上传阶段卡死，`ls-remote` 这类小请求时通时不通；
- 根因之一已排除：WSL `~/.bashrc` 里 export 了 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY=http://127.0.0.1:60243`，
  该端口无监听，git/curl 全被这条死代理挡住（已移除，备份 `~/.bashrc.bak-20261110-proxy`）；
- 直连恢复后 `ls-remote` 可通（三次里两次 1.2 秒），但 `push` 的 POST 上传仍被中断，故改用 API：
  `gh api --method POST .../git/blobs` ×3 → `.../git/trees`（base_tree=33304b0 的 tree）→
  `.../git/commits` → `PATCH .../git/refs/heads/codex/sensors-20261110`（force=true 覆盖第一次乱码提交）。

结果：远端 `0f917230b7b021524922065edaf94380c5cab9c7`，其 tree `9b3a2081…` 与本地 `69ebda2` 的 tree 完全一致，
提交信息经 UTF-8 回读校验正常。此前第一次生成的提交 `112479b` 因 PowerShell 按本地代码页读 UTF-8 文件而乱码，已被覆盖。

**注意**：本地发布克隆仍停在 `69ebda2`（与远端同树、不同提交对象，因提交对象由 API 生成）。
网络恢复后先 `git -C local/r4-git-publish-20261105 fetch origin codex/sensors-20261110 && git reset --hard FETCH_HEAD`
再继续工作，否则下次 push 会因非快进而被拒。
