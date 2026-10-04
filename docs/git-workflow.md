# Git 管理

管理仓库保存规范、来源锁定、脚本、配置与脱敏证据。第三方仓库独立下载，镜像/个人信息忽略。本机管理仓库未配置remote；公开脱敏仓库另在[GitHub](https://github.com/Zhuchen00123/oneplus8t-debian13-port)，不能把两者当同一历史或直接推送本地完整refs。

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
