# Changelog

本文件记录每次同步到 GitHub 的提交内容与时间。由 `sync-to-github.ps1` 自动追加。

## 2026-10-26 12:06:51 +08:00

- commit message: `Integrate userscript/extension overlay bridge, TransT tracker, deploy runbooks, e2e tests, results backups (2026-10-26 12:06:51 +08:00)`
- commit sha: `08e53f8`
- changed files (35 files, +35836 / -24):

```
[mod]    status/TIMELINE.md
[mod]    status/devops.md
[mod]    status/model-research.md
[new]    docs/deploy-runbook.md
[new]    docs/model-selection.md
[new]    docs/nuclio-setup.md
[new]    docs/overlay-usage.md
[new]    docs/reports/REPORT-02-凭据验证与卡点.md
[new]    docs/reports/REPORT-03-面板开发完成与E2E验证.md
[new]    extension/ (manifest.json, background.js, content.js, panel-core.js, bridge-extension.js, README.md, icons/)
[new]    results/e2e-test-log.md
[new]    results/job419_annotations_backup_20261026_115920.json
[new]    results/job419_annotations_backup_20261026_120051.json
[new]    results/job419_annotations_backup_20261026_120218.json
[new]    results/panel-render.png
[new]    serverless/tracker/transt/ (function.yaml, function-gpu.yaml, main.py, model_handler.py, README.md)
[new]    status/integration.md
[new]    tests/e2e_test.py
[new]    tools/build.ps1
[new]    userscript/ (cvat-auto-annotation.user.js, bridge-userscript.js, userscript-header.txt)
```

## 2026-10-26 12:07:00 +08:00 (remote, by GitHub web/API)

- `4644688` probe (write-permission probe file `.perm-probe`)
- `255def1` chore: remove write-permission probe
- 备注：这两个提交由 PAT 权限验证过程产生，探针文件已删除。
## 2026-10-26 12:23:08 +08:00

- commit message: `真实任务演示：job423 首帧7种子→自动传播59个shape，含演示报告与截图 (2026-10-26 12:23:08 +08:00)`
- changed files:

```
[new]    status/integration.md
[new]    results/demo/
[new]    tests/demo_propagate.py
[new]    tests/render_demo.py
```

