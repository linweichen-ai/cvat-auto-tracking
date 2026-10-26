# CVAT 自助标注面板 —— 浏览器扩展（Manifest V3）

在 CVAT 标注作业页（`http://172.16.88.68:12080/tasks/<id>/jobs/<id>`）注入一个悬浮面板，
对接 SAM3 跟踪后端（`http://172.16.88.68:11089`），实现「标前几帧 → 一键范围跟踪 → 跳帧检查」。

## 文件说明

| 文件 | 作用 |
|---|---|
| `manifest.json` | MV3 清单 |
| `background.js` | service worker：用 `chrome.cookies` 读取 HttpOnly 的 `sessionid` |
| `content.js` | 注入面板（构建产物 = `bridge-extension.js` + `panel-core.js`） |
| `panel-core.js` | 面板 UI/逻辑源码（改这里后跑 `tools/build.ps1` 重新生成 `content.js`） |
| `icons/` | 占位图标 |

> 凭据安全：扩展**不保存任何密码**。`sessionid`/`csrftoken` 只在你已登录 CVAT 的浏览器里通过
> `chrome.cookies` API 临时读取，作为请求头转发给 SAM3 后端。

## 加载方法（Edge / Chrome）

1. 打开 `edge://extensions`（或 `chrome://extensions`）。
2. 右上角打开「开发者模式 / Developer mode」。
3. 点「加载解压的扩展 / Load unpacked」，选择本目录（`extension/`）。
4. 先在浏览器里登录 CVAT（`http://172.16.88.68:12080`），然后打开任意标注作业页，
   页面右上角应出现深色面板「SAM3 自助标注」。

## 改完代码如何生效

`panel-core.js` 是源码；`content.js` 是构建产物。修改 `panel-core.js` 后，
在项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File tools\build.ps1
```

然后到扩展页面点该扩展的「刷新/重新加载」按钮。

## 与 userscript 的关系

功能与 `userscript/cvat-auto-annotation.user.js` 完全一致；区别仅在凭据读取方式：
扩展用 `chrome.cookies`（无需任何授权弹窗），userscript 用 Tampermonkey 的 `GM_cookie`。
