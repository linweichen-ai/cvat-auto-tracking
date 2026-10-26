# 为已有的 CVAT 2.61 docker-compose 加装 Nuclio

> 适用对象：已经用官方 `docker-compose.yml` 在 172.16.88.68 上跑起 CVAT 2.61.0（当前 `/api/functions` 返回 404、8070 未开放）的环境。
> 所有配置均从 v2.61.0 源码核对：
> - overlay：`https://github.com/cvat-ai/cvat/blob/v2.61.0/components/serverless/docker-compose.serverless.yml`
> - 后端设置：`cvat/settings/base.py`、`cvat/settings/production.py`、`cvat/apps/lambda_manager/views.py`

## 0. 加装原理（30 秒理解）

CVAT 自身不跑模型。它通过：
1. **Nuclio Dashboard 容器**（端口 8070）发现函数列表；
2. 函数容器被 nuctl 部署到 docker 里、加入 `cvat_cvat` 网络；
3. CVAT 后端（cvat_server / cvat_worker_annotation）用 **direct 方式**经 `http://host.docker.internal:<函数端口>` 直接调用函数。

所以要做的事就是：起一个 nuclio dashboard 容器、给 cvat_server 加 `CVAT_SERVERLESS=1`、给两个后端容器加 `host.docker.internal:host-gateway`。

---

## 1. 需要新增/修改的东西

### 1.1 新增一个 compose overlay 文件

在服务器上 CVAT 的 docker-compose 目录（即有 `docker-compose.yml` 的那层）新建 `docker-compose.serverless.override.yml`，内容与官方 overlay 一致：

```yaml
services:
  nuclio:
    container_name: nuclio
    image: quay.io/nuclio/dashboard:1.15.9-amd64
    restart: always
    networks:
      - cvat
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      NUCLIO_CHECK_FUNCTION_CONTAINERS_HEALTHINESS: 'true'
      NUCLIO_DASHBOARD_DEFAULT_FUNCTION_MOUNT_MODE: 'volume'
    # 不要把 8070 暴露到 0.0.0.0！CVAT 容器内网访问即可。
    # 如需临时调试，可只绑回环：
    ports:
      - '127.0.0.1:8070:8070'
    logging:
      driver: "json-file"
      options:
        max-size: 100m
        max-file: "3"

  cvat_server:
    environment:
      CVAT_SERVERLESS: 1
    extra_hosts:
      - "host.docker.internal:host-gateway"

  cvat_worker_annotation:
    extra_hosts:
      - "host.docker.internal:host-gateway"

networks:
  cvat:
    external: true
    name: cvat_cvat
```

> 说明：
> - 官方 overlay 里 `ports: ['8070:8070']` 是绑 0.0.0.0。本场景服务器只对外开 12080/22，建议绑 `127.0.0.1:8070`，更安全；CVAT 容器之间走 docker 网络访问 `nuclio:8070`，不依赖宿主机端口映射。
> - `networks.cvat` 的实际名字要用 `docker network ls | grep cvat` 确认。官方默认项目名是 `cvat`，网络名即 `cvat_cvat`；若你的 compose 项目名不同，把上面 `name:` 改成实际值，且后续 `nuctl deploy` 的 `--platform-config` 里网络名也要对应改。

### 1.2 CVAT 侧环境变量（无需额外设置，默认值即可）

v2.61 源码实测：
- `CVAT_SERVERLESS=1`：开启 serverless 功能（overlay 已加在 cvat_server）。
- `CVAT_NUCLIO_HOST`：production 默认 `nuclio`（即上面 dashboard 的容器名），**不用设**。
- `CVAT_NUCLIO_PORT`：默认 `8070`，不用设。
- `CVAT_NUCLIO_SCHEME`：默认 `http`，不用设。
- `CVAT_NUCLIO_INVOKE_METHOD`：非 K8s 环境默认 `direct`（经 host.docker.internal 直连函数端口），不用设。
- 列函数时 CVAT 自动带 header `x-nuclio-project-name: cvat`，所以 `nuctl` 部署必须用 `--project-name cvat`。

来源：`cvat/settings/base.py`（`NUCLIO` 字典）、`cvat/apps/lambda_manager/views.py`（`LambdaGateway._http`）。

---

## 2. 无 GPU 路径（推荐先走这条）

### 2.1 在服务器上启动 overlay

```bash
cd /opt/cvat   # 你的 docker-compose.yml 所在目录
# 先确认网络名
docker network ls | grep cvat
# 带着 overlay 重新 up（不会动已有数据卷）
docker compose -f docker-compose.yml -f docker-compose.serverless.override.yml up -d
# 验证
docker ps --filter name=nuclio
curl -s http://127.0.0.1:8070/api/functions   # 此时返回空 {} 即正常
```

### 2.2 安装 nuctl（在服务器上）

nuctl 是 nuclio 的 CLI，版本要与 dashboard `1.15.9` 对齐：

```bash
# Linux x86_64
wget https://github.com/nuclio/nuclio/releases/download/1.15.9/nuctl-1.15.9-linux-amd64 -O /usr/local/bin/nuctl
chmod +x /usr/local/bin/nuctl
nuctl --version
```

> 内网无法直接 wget：在外网机器下载后 scp 到服务器即可。
> release 页：`https://github.com/nuclio/nuclio/releases/tag/1.15.9`

### 2.3 创建项目

```bash
nuctl create project cvat --platform local
```

### 2.4 部署 TransT（CPU）

把本仓库 `serverless/tracker/transt/` 目录 scp 到服务器，然后：

```bash
cd transt
nuctl deploy --project-name cvat --path . \
  --file function.yaml --platform local \
  --env CVAT_FUNCTIONS_REDIS_HOST=cvat_redis_ondisk \
  --env CVAT_FUNCTIONS_REDIS_PORT=6666 \
  --platform-config '{"attributes": {"network": "cvat_cvat"}}'
```

> 这一步会现场 `docker build`：拉 ubuntu:22.04、apt、pip、git clone TransT、下 Google Drive 权重。**服务器内网不通这些地址**时必须按 §4 离线方案处理。

验证：
```bash
nuctl get function --platform local
curl -s http://127.0.0.1:8070/api/functions | jq .
```
然后浏览器开 `http://172.16.88.68:12080` → 左侧菜单 Models，应能看到 TransT。

---

## 3. 有 GPU 路径

前置：服务器已装 NVIDIA 驱动 + `nvidia-container-toolkit`，且 `docker run --rm --gpus all nvidia/cuda:11.7.0-base-ubuntu22.04 nvidia-smi` 能跑通。

### 3.1 给 docker 装 nvidia runtime（如未装）

```bash
# Ubuntu/Debian 示例
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### 3.2 部署 GPU 版函数

```bash
cd transt
nuctl deploy --project-name cvat --path . \
  --file function-gpu.yaml --platform local \
  --env CVAT_FUNCTIONS_REDIS_HOST=cvat_redis_ondisk \
  --env CVAT_FUNCTIONS_REDIS_PORT=6666 \
  --platform-config '{"attributes": {"network": "cvat_cvat"}}'
```

`function-gpu.yaml` 已声明 `resources.limits.nvidia.com/gpu: 1`，并设了 `NVIDIA_VISIBLE_DEVICES=all`。

---

## 4. 内网离线构建方案（必须面对）

服务器 172.16.88.68 只通 12080/22，构建 TransT 镜像需要访问的东西：

| 资源 | 用途 | 拉取地址 |
|---|---|---|
| `quay.io/nuclio/dashboard:1.15.9-amd64` | nuclio dashboard 本体 | quay.io |
| `ubuntu:22.04` | 函数 baseImage | docker.io |
| nuclio builder / python:3.10 runtime 镜像 | nuctl 构建时自动拉 | quay.io |
| PyPI 包 | jsonpickle/opencv/numpy 等 | pypi.org |
| torch 1.13.1+cpu / +cu117 | 模型推理 | download.pytorch.org |
| dschoerk/TransT v1.0 源码 | git clone | github.com |
| transt.pth 权重 | Google Drive | drive.google.com |

### 方式 A（推荐）：跳板机 + DOCKER_HOST 远程构建

1. 找一台**能上外网**且能 `ssh user@172.16.88.68` 的 Linux 跳板机。
2. 在跳板机上装 nuctl 1.15.9。
3. 让跳板机的 docker CLI 直连服务器 dockerd：
   ```bash
   # 方式1：SSH 隧道（推荐）
   ssh -N -L 2375:/var/run/docker.sock user@172.16.88.68 &
   export DOCKER_HOST=tcp://127.0.0.1:2375
   # 方式2：直接 docker context create
   docker context create remote --docker host=ssh://user@172.16.88.68
   docker context use remote
   ```
4. 在跳板机上执行 `nuctl deploy --platform local ...`。nuclio 会通过 SSH 隧道里的 docker API 在**服务器上**建容器，但所有外网拉取（pip/github/google drive）由跳板机网络完成——前提是 dockerd 构建容器时的网络走的是服务器的网卡，而不是跳板机。⚠️ 这一点在 Linux 上不成立：docker build 发生在服务器 dockerd 内，build 容器的网络出口是服务器网卡，服务器上不了外网就会失败。
   → 因此方式 A 实际只适合“服务器能走 HTTP 代理出网”的情况：在 overlay 里给 nuclio 容器和 docker daemon 配 `HTTP_PROXY`/`HTTPS_PROXY` 指向跳板机的代理端口。

### 方式 B（最稳）：外网机器构建 → save/load 搬运

1. 在**一台完全能上外网的 Linux** 机器上（Docker 已装），装 nuctl 1.15.9：
   ```bash
   nuctl create project cvat --platform local
   cd transt
   nuctl deploy --project-name cvat --path . --file function.yaml --platform local
   ```
   等待镜像构建完成（`nuctl get function` 状态 ready）。
2. 导出镜像：
   ```bash
   docker images | grep -i transt          # 记下镜像名 tag，官方默认 cvat.pth.dschoerk.transt:latest
   docker save cvat.pth.dschoerk.transt:latest | gzip > transt-cpu.tar.gz
   # 顺带导出 nuclio dashboard 与 base 镜像
   docker save quay.io/nuclio/dashboard:1.15.9-amd64 ubuntu:22.04 | gzip > base-images.tar.gz
   ```
3. scp 到服务器：
   ```bash
   scp transt-cpu.tar.gz base-images.tar.gz user@172.16.88.68:~/
   ```
4. 服务器上 load：
   ```bash
   ssh user@172.16.88.68
   gunzip -c transt-cpu.tar.gz | docker load
   gunzip -c base-images.tar.gz | docker load
   ```
5. 在服务器上**不再重新 build**，而是让 nuclio 直接用已有镜像。做法：把 `function.yaml` 里 `build:` 段整段删掉/注释，仅保留 metadata/spec/triggers，并保证 `build.image` 与 load 进来的镜像名 tag 完全一致，再 `nuctl deploy ... --platform local`。

> nuclio 用已有镜像部署时 `build.image` 字段保留即可，不要配 `build.directives`。

### 方式 C：给服务器配 HTTP 代理

若内网有现成出网代理（如 `http://proxy.corp:8080`），在 overlay 的 nuclio 容器环境加：
```yaml
environment:
  HTTP_PROXY: http://proxy.corp:8080
  HTTPS_PROXY: http://proxy.corp:8080
  NO_PROXY: cvat,cvat_cvat,127.0.0.1,localhost,cvat_redis_ondisk
```
并给服务器 dockerd 本身配 `/etc/systemd/system/docker.service.d/http-proxy.conf`（docker build 容器继承 daemon 代理）。然后按 §2 正常 deploy。

---

## 5. 验证清单

- [ ] `docker ps` 看到 `nuclio` 容器 Up。
- [ ] `curl http://127.0.0.1:8070/api/functions` 返回 JSON。
- [ ] `nuctl get function --platform local` 看到 `pth-dschoerk-transt`，状态 ready。
- [ ] CVAT Web → Models 页面出现 TransT。
- [ ] 打开一个视频任务 → 标注框右键菜单有 “Track”，或 AI Tools 面板里能选 TransT。

## 6. 参考

- 官方 overlay：`https://github.com/cvat-ai/cvat/blob/v2.61.0/components/serverless/docker-compose.serverless.yml`
- 官方 deploy 脚本：`https://github.com/cvat-ai/cvat/blob/v2.61.0/serverless/deploy_cpu.sh`
- Nuclio docker 平台文档：`https://docs.nuclio.io/en/latest/setup/docker/getting-started-docker.html`
- nuctl release：`https://github.com/nuclio/nuclio/releases/tag/1.15.9`
