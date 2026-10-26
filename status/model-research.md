# CVAT 自动跟踪技术调研 — 状态追踪

## 当前状态
- 阶段：信息收集中（CVAT serverless tracker 官方清单 + function.yaml 规范 + Nuclio 加装方案）
- 项目根：`C:\Users\seer\Doubao\chats\2026-10-26\new-chat\cvat-auto-tracking`
- 目标环境：CVAT 2.61.0，远程内网服务器 172.16.88.68（仅开放 12080/22），未接 Nuclio

## 最近完成
- 2026-10-26：创建项目目录结构（status/ docs/ serverless/tracker/）与本状态文件。

## 下一步
1. 拉取 openvinotoolkit/cvat 仓库 serverless 目录结构与 tracker 类函数清单（2.61 分支）。
2. 核对每个 tracker 的 function.yaml 字段规范（kind: tracker、spec、event triggers）。
3. 调研 SiamMask / TransT / SiamRPN++ / NanoTrack / ViT-Track / MOT(ByteTrack) / SAMURAI 等模型原理与资源需求。
4. 形成 model-selection.md，给出首选 + 备选。
5. 落地 serverless/tracker/<model>/ 的 function.yaml + main.py + wrapper。
6. 写 nuclio-setup.md（GPU / 无 GPU 两条路径）。
7. 写 deploy-runbook.md（从 SSH 到 UI 点 Track）。

## 阻塞问题
- 暂无。注意：内网服务器无法直接访问 Docker Hub / ghcr.io，镜像需提前离线导出/代理。

## 更新时间
- 2026-10-26（初建）
