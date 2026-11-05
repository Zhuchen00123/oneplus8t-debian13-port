# Git 管理

管理仓库保存规范、来源锁定、脚本、配置与脱敏证据。第三方仓库独立下载，镜像/个人信息忽略。现已配置origin指向[GitHub](https://github.com/Zhuchen00123/oneplus8t-debian13-port)，但管理仓库与公开库历史仍不同，不能直接推送本地完整refs。此前“未配置remote”仅为早期状态。

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
