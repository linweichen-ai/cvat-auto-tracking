# DevOps / 仓库运维状态

## 当前状态
- 阶段：**本地仓库骨架已就绪，等待 GitHub PAT 凭据以建库并首次推送**。
- 本地工作目录：`C:\Users\seer\Doubao\chats\2026-10-26\new-chat\cvat-auto-tracking`
- git / gh 已安装（git 2.56.0、gh 2.103.2）。
- 尚未 `git init`、尚未配置 user.name/user.email、尚未配置远程。

## 最近完成
- 2026-10-26：
  - 创建仓库骨架：README.md、.gitignore、LICENSE(MIT)、sync-to-github.ps1。
  - 创建部署脚本框架：`deploy/deploy.ps1`（Windows 侧 SSH/SCP）、`deploy/deploy.sh`（Linux 侧 nuctl）。
  - 确认已有产物保留：`docs/reports/REPORT-01-*.md`、`status/TIMELINE.md`、`status/model-research.md`。
  - 建立 `docs/CHANGELOG.md` 自动追加机制（由 sync 脚本写入）。

## 下一步
1. 接收协调 Agent 发来的 GitHub PAT + 用户名。
2. 配置 `git config --global user.name / user.email`（或仓库级 local 配置）。
3. 用 PAT 通过 `gh auth login --with-token` 写入 Git Credential Manager（不落地到任何文件）。
4. `git init` → 首次提交全部骨架 → `gh repo create cvat-auto-tracking --private --source=. --push`。
5. 验证 `git remote -v` 与 `git ls-remote` 可访问。
6. 之后每次新产物落地，执行 `.\sync-to-github.ps1 -Message "..."` 实时入库。

## 阻塞问题
- **等待 GitHub PAT + 用户名**（协调 Agent 提供）。
- 等待目标服务器（172.16.88.68）SSH 凭据，用于后续 Nuclio 部署（与本仓库任务并行，不阻塞建库）。

## 更新时间
- 2026-10-26（初建）
