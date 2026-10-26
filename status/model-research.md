# CVAT 自动跟踪技术调研 — 状态追踪

## 当前状态
- 阶段：**调研与产物交付完成**。等待在 172.16.88.68 上按 deploy-runbook.md 实际部署验证。
- 项目根：`C:\Users\seer\Doubao\chats\2026-10-26\new-chat\cvat-auto-tracking`
- 目标环境：CVAT 2.61.0，远程内网服务器 172.16.88.68（仅开放 12080/22），未接 Nuclio

## 最近完成
- 2026-10-26：拉取 cvat-ai/cvat v2.61.0 源码包逐文件核对 serverless 函数清单。
- 2026-10-26：**关键结论：v2.61 官方 serverless 中 type=tracker 的函数只有 TransT**（pth-dschoerk-transt）；其余为 detector/interactor。SiamMask 等已在旧版后移除。
- 2026-10-26：完成 docs/model-selection.md（首选 TransT CPU，GPU 备选）。
- 2026-10-26：原样拷贝上游 function.yaml / function-gpu.yaml / main.py / model_handler.py 到 serverless/tracker/transt/。
- 2026-10-26：完成 docs/nuclio-setup.md（无 GPU / 有 GPU / 内网离线构建 三种路径）。
- 2026-10-26：完成 docs/deploy-runbook.md（SSH → UI Track 全流程）。

## 下一步
1. 在一台外网 Linux 机器上按 nuclio-setup.md §4 方式 B 预构建 TransT 镜像 + nuclio dashboard 镜像。
2. scp 到 172.16.88.68，docker load。
3. 按 deploy-runbook.md Step 2–6 起 overlay、装 nuctl、部署函数。
4. UI 验证：Models 页面出现 TransT；视频任务 AI Tools → Tracker 选 TransT → 按 F 自动跟帧。
5. 用一段真实仓库叉车视频实测跟得稳不稳，必要时再评估 SAM2 agent。

## 阻塞问题
- 服务器内网无法访问 Docker Hub / PyPI / GitHub / Google Drive，构建期必须走离线镜像搬运（已在 nuclio-setup.md §4 给出 A/B/C 三方案）。
- 服务器是否有 NVIDIA GPU 尚未确认；无 GPU 走 CPU 版即可。

## 更新时间
- 2026-10-26（全部产物完成）
