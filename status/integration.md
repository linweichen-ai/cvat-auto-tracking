# 集成状态 —— CVAT 自助标注面板

## 当前状态
面板已开发完成并通过 API 级 E2E 验证；浏览器内渲染截图待人工/自动化补拍。

## 最近完成
- `userscript/cvat-auto-annotation.user.js`（Tampermonkey 单文件，~36KB）已生成。
- `extension/` MV3 扩展（manifest + background.js + content.js + 占位图标 + README）已生成。
- 面板功能：job/frame 自动识别、cookie 复用凭据（chrome.cookies / GM_cookie）、
  标签/实例下拉、缓存帧进度条、逐实例范围跟踪（核心）、传播下一帧、删除/改标签（二次确认）、
  点/框/文本提示分割、撤销、可拖动可折叠。
- E2E（`tests/e2e_test.py`）全绿：登录→auth_status→7 标签→备份 24 shapes→缓存→
  propagate_shape(种子 2398446→frame1)→新增 1 shape(id 2398468, frame1, label201)→
  PUT 整份回滚→恢复 24 shapes 且分帧分布一致。日志见 `results/e2e-test-log.md`，
  备份见 `results/job419_annotations_backup_*.json`。

## 下一步
- [x] 浏览器自动化已验证面板在 job 419 页面正常渲染（截图 `results/panel-render.png`：
  自动识别 Job 419 / 帧 0，全部按钮就位；注入环境读不到 HttpOnly sessionid 故连接显红，
  真实扩展由 background 的 chrome.cookies 提供）。
- [ ] 二期：画布上直接点击/拖拽取点取框（当前降级为坐标输入）。
- [ ] 二期：跟踪完成后不整页刷新，改为触发 CVAT 内部重载 annotations。

## 阻塞问题 / 重要发现
- **sessionid 是 HttpOnly**：document.cookie 读不到。扩展用 `chrome.cookies`，
  userscript 用 `GM_cookie`；都失败时面板提供手动粘贴入口。
- **CVAT 2.61 annotations REST 陷阱（实测）**：
  - `DELETE /api/jobs/{id}/annotations` **不接受 shape_id，会清空整个 job**。
  - `PATCH ?action=delete` 按 id 删除需要完整 mask payload，不可行。
  - 已验证回滚方式：剥掉 shape `id` 后整份 `PUT /api/jobs/{id}/annotations`（CREATE 语义）。
  - E2E 期间曾误删 job 419 全部标注，已用备份 PUT 恢复并核对一致（24 shapes，分帧分布吻合）。
- get_shapes 的 label_id 必填（None → 422）。

## 更新时间
2026-10-26 12:05 (UTC+8)
