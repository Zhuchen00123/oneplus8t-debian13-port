# Git 管理

管理仓库保存项目规范、来源锁定、脚本、配置和非敏感证据。第三方仓库独立下载，所有镜像/个人信息忽略。当前无远程仓库；不默认发布或推送。

## 分支与提交

- `main`：已审核的项目状态。
- 主题分支建议 `port/kebab-7.3`、`debian/initramfs`、`backup/device`；一个分支只处理明确目标。
- WSL 中的内核仓库保留纯 `baseline-sm8250-7.2.0` 标签，补丁在 `port/kebab-7.2-debian13` 分支按顺序提交。
- 每阶段记录社区来源 SHA、自己修改、实际验证与未测试事项；升级时保留旧 refs。
- 初始化提交如没有配置身份，使用单次 `Codex <codex@localhost>` 自动化身份，不修改全局 user.name/user.email。后续设置用户自己选择的身份。

```powershell
git status --short --branch
git diff --stat
git switch -c debian/initramfs
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
