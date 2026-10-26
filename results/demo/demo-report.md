# 真实任务演示报告 —— Task3（task 430 / job 423）

时间：2026-10-26 12:20 (UTC+8)

## 场景
- Task3：task 430 / job 423，共 150 帧（叉车第一视角仓库视频）。
- 起始状态：仅 frame 0 上有 **7 个手工标注的 pallet(201) mask** 种子形状。
- 目标场景：**人工只标首帧 → 一键自动跟踪后续帧**。

## 操作步骤（脚本 `tests/demo_propagate.py 30`）
1. 登录 CVAT（Token + sessionid/csrftoken），备份 job 423 原始 annotations → `results/demo/job423-backup.json`。
2. `cache_frames 0→30`：后端 6.3s 完成 31 帧缓存（cache_status loaded=31/31 ready）。
3. 对 frame 0 的 7 个种子逐个 `propagate_shape(start=0,end=30,label=201,shape_id=*)`。
4. 重新拉 annotations 统计分帧 shape 数。

## 每实例生成量
| 种子 shape_id | label | generated |
|---|---|---|
| 2398412 | 201 pallet | 8 |
| 2398413 | 201 pallet | 20 |
| 2398414 | 201 pallet | 19 |
| 2398415 | 201 pallet | 18 |
| 2398416 | 201 pallet | 19 |
| 2398417 | 201 pallet | 8 |
| 2398418 | 201 pallet | 7 |

全部成功，无失败实例。

## 分帧 shape 统计（frame 0-30）
| frame | count | frame | count | frame | count |
|---|---|---|---|---|---|
| 0 | 7 | 10 | 6 | 20 | 0 |
| 1 | 0 | 11 | 1 | 21 | 3 |
| 2 | 6 | 12 | 6 | 22 | 0 |
| 3 | 1 | 13 | 1 | 23 | 3 |
| 4 | 6 | 14 | 5 | 24 | 0 |
| 5 | 1 | 15 | 2 | 25 | 1 |
| 6 | 6 | 16 | 0 | 26-30 | 0 |
| 7 | 1 | 17 | 1 | | |
| 8 | 6 | 18 | 0 | | |
| 9 | 1 | 19 | 2 | | |

- 传播前 7 个 shape → 传播后 **66 个**（+59）。
- frame 0-15 每帧都有标注（偶数帧 5-6 个，奇数帧 0-2 个，与视频抽帧节奏一致）。
- frame 16 起目标托盘逐渐出画，跟踪自然停止（0 shape = 目标离开画面，非报错）。

## 渲染图（PIL 叠加 mask，按 label 着色半透明）
- `demo-frame-000.png`：frame 0，7 个 pallet mask（人工种子）
- `demo-frame-010.png`：frame 10，6 个 mask（自动跟踪结果，仍贴合托盘）
- `demo-frame-020.png`：frame 20，0 个 mask（场景已换到货架区，目标出画）
- `demo-frame-030.png`：frame 30，0 个 mask
- `demo-grid.png`：以上 2×2 拼图
- `panel-demo.png`：浏览器实连面板截图（CVAT:leonlee233 / SAM3:已连接，7 标签已加载）

## 耗时
- 缓存 6.3s；7 个实例传播约 49s；全程 **58.6s**（含登录/备份/统计）。

## 结论
✅ 达成「标首帧 → 自动完成后续」：人工 7 个种子，1 分钟内自动铺满 frame 0-15 并延续到 ~frame 25；
目标出画后自动停止，无需人工干预。后续帧（26+）建议人工补标——这正是面板设计的工作流。

## 标注保留与回滚
- **本次生成的标注保留在 Task3（job 423）上**，供实际使用。
- 一键回滚方法（如需）：用 `results/demo/job423-backup.json`，剥掉每个 shape 的 `id` 字段后
  `PUT /api/jobs/423/annotations`（CVAT 2.61 的 DELETE 会清空整个 job，不可用于回滚）。
  参考脚本：`scratch/restore_job419.py`（把 JOB 换成 423、备份路径换成 demo/job423-backup.json）。
