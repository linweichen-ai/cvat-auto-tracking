# REPORT-02 凭据验证与卡点

**汇报时间：2026-10-26（周一）**

## ① 本节点结论
三把"钥匙"验了两把半：**CVAT 应用账号已打通，GitHub 仓库已建成**；但两把钥匙各差最后一步——GitHub token 缺代码写权限、服务器 SSH 登录账号不对。**当前唯一卡点是等您补两类凭据**，其余工作（模型规范核对）在并行推进，凭据一到立即继续。

## ② 关键事实
- **CVAT 应用层 ✅**：用 leonlee233 调 API 登录成功，已拿到 token，后续所有自动化验证（读任务、提标注）都能跑通。
- **GitHub ⚠️**：公开仓库 `linweichen-ai/cvat-auto-tracking` 已成功创建（账号下可见、admin 权限正常），但推送代码返回 403、Contents API 写入也 403。判断原因：当前 fine-grained PAT 只授了 Administration 权限（所以能建库），**缺 Contents: Read and write 权限**。
- **服务器 SSH ❌**：用 leonlee233/leonlee233 以及 root、ubuntu 等常见用户名均认证失败——**CVAT 应用账号不等于操作系统登录账号**，需要您提供服务器 OS 层的正确登录凭据。
- **模型调研 🔄**：正在下载 CVAT v2.61 官方源码，核对 serverless function 的接口规范，进行中。

## ③ 问题与风险
- GitHub 403 是权限配置问题，不是仓库或网络问题，调一下 token 设置即可解决，风险低。
- SSH 登不上意味着 Nuclio 安装还没法开始，这是当前进度的硬阻塞；服务器真实账号体系需用户确认，我们这边猜不出来。

## ④ 需要您做什么
1. **调整 GitHub token 权限**：把该 fine-grained PAT 的 **Contents 权限改为 Read and write**（在仓库 `linweichen-ai/cvat-auto-tracking` 的 Settings → Developer settings 里改即可）；或者直接换一个带 `repo` 权限的 classic token。
2. **提供服务器（172.16.88.68）的 OS 登录凭据**：即 SSH 22 端口真正能用的用户名 + 密码（或密钥），不是 CVAT 网页的账号。
3. 其余无需操作。

## 下一步路线
Contents 写权限到位 → 立即首次推送代码；SSH 凭据到位 → 立即上服务器加装 Nuclio、部署 tracker，然后进入真实任务的自动跟踪演示。
