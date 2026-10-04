# Git 管理

管理仓库保存规范、来源锁定、脚本、配置与脱敏证据。第三方仓库独立下载，镜像/个人信息忽略。本机管理仓库未配置remote；公开脱敏仓库另在[GitHub](https://github.com/Zhuchen00123/oneplus8t-debian13-port)，不能把两者当同一历史或直接推送本地完整refs。

## 当前文档与公开导出

2026-10-04已有发布核验记录：公开库上传的是独立审核文件集，不含镜像、固件或私有历史。导出/脱敏脚本和核验材料在local/github-export-20261004/；它们是本机私有流程材料。后续发布先重新核验文件允许清单、隐私、链接依赖与实际Git blob，不能将新文档指向未导出的私有产物。

本轮完善项目文档并生成FigJam画板；没有同步GitHub。当前gh只读查询返回401，无法用本机登录复核远端最新状态，不据此宣称本轮修改已发布。此情况不影响本地文档、图表和阶段快照完成。

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
