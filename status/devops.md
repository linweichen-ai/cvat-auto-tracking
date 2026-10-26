# DevOps / 仓库运维状态

## 当前状态
- 阶段：**仓库已打通，main 分支与远程同步，实时推送机制就绪**。
- 远程仓库：https://github.com/linweichen-ai/cvat-auto-tracking （PUBLIC，default branch `main`）
- 本地与远程 HEAD：`08e53f8`（已 `git ls-remote` 校验一致）
- 认证方式：classic PAT 通过环境变量 `GH_TOKEN` 注入 gh CLI 与 git credential helper（token 不落盘、不入仓库）。
  - 注：gh keyring 中仍保留旧 fine-grained token，但 push 实际走 `GH_TOKEN` 环境变量。
- 本仓库 credential helper：`!gh auth git-credential`（已绕过 GCM 旧缓存）。
- `core.quotepath=false` 已配置，中文文件名正常显示。

## 最近完成
- 2026-10-26：
  - 创建仓库骨架（README/.gitignore/LICENSE/sync-to-github.ps1/deploy 脚本/状态文件）。
  - `gh repo create linweichen-ai/cvat-auto-tracking --public` 建库。
  - 首次提交（rebase 后 `b2105dc`）：15 文件。
  - 第二次提交 `08e53f8`：35 文件，+35836 行，包含：
    - userscript/ + extension/（CVAT 页面注入与悬浮面板）
    - serverless/tracker/transt/（TransT tracker 函数骨架）
    - docs/ 新增 deploy-runbook / model-selection / nuclio-setup / overlay-usage / REPORT-02 / REPORT-03
    - tests/e2e_test.py、tools/build.ps1
    - results/ 下 e2e 日志、job419 标注备份、panel-render.png
  - 清理远程探针文件 `.perm-probe`（API DELETE，提交 `255def1`）。
  - 修复 sync-to-github.ps1：porcelain 解析偏移、CHANGELOG 表头重复日期、中文路径转义。

## 下一步
1. 每次新产物落地后执行：`.\sync-to-github.ps1 -Message "说明"`。
   - 脚本自动：追加 docs/CHANGELOG.md → git add -A → 带时间戳 commit → push。
   - 注意：PowerShell 会话内需先 `$env:GH_TOKEN = "<token>"`（或改用 `gh auth login` 持久化）。
2. 待服务器 SSH 凭据到位后：按 docs/deploy-runbook.md 在 172.16.88.68 上装 Nuclio 并部署 TransT tracker。
3. 在 CVAT UI 上跑通"首帧标注 → 自动跟踪"端到端演示。

## 阻塞问题
- 无。
- （历史）旧 fine-grained PAT 缺 Contents:write，已由用户换发 classic token 解决。

## 更新时间
- 2026-10-26（仓库打通，实时推送就绪）
