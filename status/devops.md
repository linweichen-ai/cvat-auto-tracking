# DevOps / 仓库运维状态

## 当前状态
- 阶段：**GitHub 远程仓库已创建（public），本地首次提交已就绪，但 push 被 PAT 权限阻塞**。
- 远程仓库：https://github.com/linweichen-ai/cvat-auto-tracking （PUBLIC，default branch main）
- 本地提交：`d7a00d6` "Initial commit: CVAT auto-tracking repo skeleton ..."（15 文件，574 行）
- git / gh 已安装（git 2.56.0、gh 2.103.2）；gh 已登录 linweichen-ai（keyring 存储 token）。
- 本仓库级 credential helper 已强制指向 `gh auth git-credential`（绕过 GCM 旧缓存）。

## 最近完成
- 2026-10-26：
  - 创建仓库骨架：README.md、.gitignore、LICENSE(MIT)、sync-to-github.ps1。
  - 创建部署脚本框架：`deploy/deploy.ps1`（Windows 侧 SSH/SCP）、`deploy/deploy.sh`（Linux 侧 nuctl）。
  - 确认已有产物保留：`docs/reports/REPORT-01-*.md`、`status/TIMELINE.md`、`status/model-research.md`。
  - 建立 `docs/CHANGELOG.md` 自动追加机制（由 sync 脚本写入）。
  - `git init -b main`、配置 local user.name/email、完成首次提交 d7a00d6。
  - `gh repo create linweichen-ai/cvat-auto-tracking --public` 成功，远程仓库已存在。

## 下一步
1. **【阻塞解除后立即执行】** 用户在 GitHub PAT 设置页给该 fine-grained token 补：Repository permissions → **Contents: Read and write**（并确认 Repository access 包含本仓库，或选 All repositories）。
2. 若只是编辑原 token 权限（未重新生成），直接重跑：`git push -u origin main`。
3. 若重新生成了新 token，先 `gh auth login --with-token`（stdin）再 push。
4. 推送成功后验证：`git ls-remote origin`、`gh repo view`。
5. 之后每次新产物落地，执行 `.\sync-to-github.ps1 -Message "..."` 实时入库。

## 阻塞问题
- **PAT 权限不足**：fine-grained token 当前可读仓库、可建仓库，但 **Contents 写权限未授予**，git push 与 Contents API PUT 均返回 403 `Resource not accessible by personal access token`。
  - 已验证：`gh api repos/.../permissions` 显示 owner 视角 push=true，但 token 自身 scope 不含 Contents:write。
  - 已排除：GCM 旧凭据缓存（已 erase 并改用 gh helper）；网络问题（API 读正常）。
- 等待目标服务器（172.16.88.68）SSH 凭据，用于后续 Nuclio 部署（与建库并行，不阻塞）。

## 更新时间
- 2026-10-26（建库完成、push 阻塞待解）

