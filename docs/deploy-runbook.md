# 部署 Runbook：从 SSH 到 CVAT 里点 Track

> 目标服务器：`172.16.88.68`，SSH 端口 22，CVAT Web 端口 12080。
> 已存在 CVAT 2.61.0 docker-compose 部署，未接 Nuclio。
> 假设：服务器上有 `docker` / `docker compose` 命令，compose 项目目录在 `/opt/cvat`（按实际替换）。

---

## Step 1. SSH 登录并确认现状

```bash
ssh user@172.16.88.68

# 确认 CVAT 容器在跑
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}' | grep -i cvat

# 找到 compose 项目目录（含 docker-compose.yml）
cd /opt/cvat
ls docker-compose.yml

# 确认 docker 网络名（后续 nuctl 要用）
docker network ls | grep cvat
# 期望看到类似： cvat_cvat   bridge
```

记录两个事实：
- compose 目录路径（下文记作 `$CVAT_DIR=/opt/cvat`）
- docker 网络名（下文默认 `cvat_cvat`，如不同需全文替换）

---

## Step 2. 放置 Nuclio overlay 文件

在 `$CVAT_DIR` 下新建 `docker-compose.serverless.override.yml`（内容见 `nuclio-setup.md` §1.1，与官方 v2.61 overlay 对齐）。

```bash
cd $CVAT_DIR
# 用 vi/nano 把 docker-compose.serverless.override.yml 写好
```

## Step 3. 带 overlay 重启 CVAT

```bash
cd $CVAT_DIR
docker compose -f docker-compose.yml -f docker-compose.serverless.override.yml up -d

# 等 30 秒，确认 nuclio 起来了
sleep 30
docker ps --filter name=nuclio
curl -s http://127.0.0.1:8070/api/functions
# 期望：返回 {} （空函数列表）或 JSON 对象，不是 connection refused
```

> 这一步不会删除已有任务/数据（数据卷不动）。

---

## Step 4. 安装 nuctl 1.15.9

```bash
# 若服务器能上外网：
wget https://github.com/nuclio/nuclio/releases/download/1.15.9/nuctl-1.15.9-linux-amd64 -O /usr/local/bin/nuctl
chmod +x /usr/local/bin/nuctl

# 若服务器不能上外网：在外网机器下载后
#   scp nuctl-1.15.9-linux-amd64 user@172.16.88.68:/usr/local/bin/nuctl
#   ssh 上去 chmod +x /usr/local/bin/nuctl

nuctl --version
```

## Step 5. （离线 only）导入预构建镜像

如果按 `nuclio-setup.md` §4 方式 B 在外网机器构建好了镜像包：

```bash
gunzip -c ~/base-images.tar.gz | docker load
gunzip -c ~/transt-cpu.tar.gz  | docker load
docker images | grep -i transt   # 确认 cvat.pth.dschoerk.transt:latest 存在
```

## Step 6. 创建 nuclio 项目并部署 TransT

把本仓库 `serverless/tracker/transt/` 整个目录传到服务器：

```bash
# 在本机（Windows PowerShell）：
# scp -r serverless/tracker/transt user@172.16.88.68:~/transt
```

然后服务器上：

```bash
cd ~/transt
nuctl create project cvat --platform local

# CPU 版
nuctl deploy --project-name cvat --path . \
  --file function.yaml --platform local \
  --env CVAT_FUNCTIONS_REDIS_HOST=cvat_redis_ondisk \
  --env CVAT_FUNCTIONS_REDIS_PORT=6666 \
  --platform-config '{"attributes": {"network": "cvat_cvat"}}'
```

观察输出，等待构建完成（离线场景用预构建镜像时会很快）。

验证：
```bash
nuctl get function --platform local
# NAME                 PROJECT  STATE  ...
# pth-dschoerk-transt  cvat     ready  ...

curl -s http://127.0.0.1:8070/api/functions | python3 -m json.tool
# 应看到一条 name=pth-dschoerk-transt 的记录
```

> 若 STATE 不是 ready：`docker logs nuclio` 看 dashboard 日志；`nuctl get function pth-dschoerk-transt -o yaml --platform local` 看函数容器状态；`docker ps -a | grep transt` 看函数容器是否崩溃。

---

## Step 7. 在 CVAT Web 里验证模型已注册

1. 浏览器打开 `http://172.16.88.68:12080`，登录管理员账号。
2. 左侧导航 → **Models**（或“模型”）页面。
3. 应能看到 **TransT** 一条，类型 tracker。

> 若 Models 页面没有：刷新页面；确认 cvat_server 环境变量有 `CVAT_SERVERLESS=1`（`docker exec cvat_server env | grep CVAT_SERVERLESS`）；确认 `curl http://nuclio:8070/api/functions` 在 cvat_server 容器里能返回数据（`docker exec cvat_server curl -s http://nuclio:8070/api/functions`）。

---

## Step 8. 在视频任务里使用 Track（UI 操作路径）

前置：任务里已经上传仓库叉车视频，并在第一帧用 Rectangle 工具画好要跟踪的目标（叉车/托盘/货箱）。

1. 打开任务 → 点击 **Tasks → 选中任务 → Open**（标注模式）。
2. 右侧边栏切换到 **AI Tools**（魔法棒图标）。
3. 找到 **Tracker** 子标签：
   - **Label**：选目标标签（如 `forklift`）。
   - **Tracker** 下拉：选 **TransT**。
   - 点击 **Track** 按钮开启跟踪开关。
4. 在第一帧画好 bbox（或选中已有 bbox）。
5. 按 **F**（或顶部 Next 按钮）跳到下一帧——TransT 自动把框延续到新帧；继续按 F，逐帧自动跟踪。
6. 跟丢时：在当帧手动修正 bbox，再按 F 继续（修正后的框会作为新模板重新初始化）。

官方文档参考：`https://docs.cvat.ai/docs/manual/advanced/ai-tools/`（“Go to the Tracker tab, select the label, and click Tracking... All annotated objects will be automatically tracked when you move to the next frame.”）。

> 注意区分：
> - **Track mode（插值）**：左侧工具栏的 “Track” 按钮画出来的框会在帧之间线性插值，那是纯几何插值，不调模型。
> - **AI Tracker（TransT）**：上面 §8 的 AI Tools → Tracker 路径，才是真正调 nuclio 模型。两者可以配合：先用 TransT 跟，再让 CVAT 在缺帧处插值。

---

## Step 9. 日常运维速查

```bash
# 看函数列表
nuctl get function --platform local

# 看某函数日志
nuctl get function pth-dschoerk-transt --platform local

# 删除函数（重新部署前）
nuctl delete function pth-dschoerk-transt --platform local

# 重启 nuclio dashboard
docker restart nuclio

# 看 CVAT 后端调用函数时报的错
docker logs cvat_server --tail 100 | grep -i -E 'lambda|nuclio|transt'
docker logs cvat_worker_annotation --tail 100 | grep -i -E 'lambda|nuclio|transt'
```

---

## 常见故障

| 现象 | 排查 |
|---|---|
| Models 页面看不到 TransT | `docker exec cvat_server curl -s http://nuclio:8070/api/functions` 是否返回函数；`nuctl get function --platform local` 是否 ready；CVAT 项目名是否 `cvat`。 |
| 点 Track 报错 / 一直转圈 | `docker logs cvat_worker_annotation`；函数容器能否访问 `cvat_redis_ondisk:6666`（部署时 network 是否写成 `cvat_cvat`）；函数容器是否真的在 cvat_cvat 网络上（`docker network inspect cvat_cvat`）。 |
| 函数启动 OOM | 服务器内存不足；CPU 版 TransT 常驻约 1–2GB，确认 `docker stats`。 |
| 跟几帧就跟丢 | 正常现象（单目标无在线更新）；在跟丢帧手动修正后继续；目标出画后重新框选。 |
