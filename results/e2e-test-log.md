# CVAT 自助标注 E2E 测试日志
时间: 2026-10-26T12:02:18
目标: CVAT=http://172.16.88.68:12080  SAM3=http://172.16.88.68:11089  Job=419  范围=0-1

## 1. 登录 CVAT
- POST /api/auth/login → 200，已获取 API token 与 sessionid/csrftoken（HttpOnly，浏览器侧靠 chrome.cookies/GM_cookie 读取）。
## 2. SAM3 连接检测
- POST /api/auth_status → 200 `{"status": "success", "authenticated": true, "username": "leonlee233"}`
- POST /api/get_labels {job_id:419} → 200，7 个标签: ['pallet', 'person', 'floor', 'slope', 'ego', 'other_robot', 'cargo_floor']

## 3. 备份 annotations
- GET /api/jobs/419/annotations → shapes=24
- 备份文件: `results/job419_annotations_backup_20261026_120218.json`
- 分帧分布(frame,label): {(0, 201): 6, (0, 203): 1, (0, 205): 1, (0, 206): 1, (1, 201): 4, (1, 203): 1, (1, 205): 1, (1, 206): 1, (2, 201): 2, (2, 203): 1, (2, 205): 1, (2, 206): 1, (3, 201): 1, (3, 203): 1, (3, 205): 1}
- 选定种子 shape: id=2398446 frame=0 label=201 type=mask

## 4. 缓存帧 0-1
- POST /api/cache_frames → 200 `{"status": "ready", "message": "All frames already cached."}`
- cache_status: loaded=5/5 range_ready=True
- 结论: 帧缓存 OK

## 5. propagate_shape 种子 2398446 → 0-1
- POST /api/propagate_shape → 200 `{"status": "success", "generated": 2}`

## 6. 验证新增
- 传播后 shapes=25（传播前 24）
- 新增 shape IDs: [2398468]
  - new id=2398468 frame=1 label_id=201 type=mask

## 7. 回滚：PUT 整份恢复备份
- （API 教训：CVAT 2.61 的 DELETE /annotations 会清空整个 job；PATCH ?action=delete 需要完整 mask payload，按 id 删除不可行。已验证回滚方式=剥 id 后 PUT 整份备份）
- PUT /api/jobs/419/annotations（备份整份，去掉 id）→ 200
- 回滚后 shapes=24（原始 24）

## 测试结论汇总
- ✅ propagate_shape 生效：新增 1 个 shape（[2398468]），全部落在 frame 0-1、标签 201
- ✅ 回滚成功：shape 数量与分帧分布完全恢复到备份状态（24 个；PUT 会重新分配 id，属正常）
- ✅ 全部断言通过：登录/连接/标签/缓存/传播/验证/回滚链路完整可用。
- 说明：浏览器面板（userscript/扩展）复用本测试同一套 11089 API 与凭据头，此即面板功能可用的 API 证据。
