# TransT Tracker（CVAT v2.61 官方 serverless tracker）

本目录文件**原样拷贝自 cvat-ai/cvat v2.61.0**，未做任何改动：

| 文件 | 上游 URL（v2.61.0 tag） |
|---|---|
| `function.yaml`（CPU） | https://github.com/cvat-ai/cvat/blob/v2.61.0/serverless/pytorch/dschoerk/transt/nuclio/function.yaml |
| `function-gpu.yaml`（GPU） | https://github.com/cvat-ai/cvat/blob/v2.61.0/serverless/pytorch/dschoerk/transt/nuclio/function-gpu.yaml |
| `main.py` | https://github.com/cvat-ai/cvat/blob/v2.61.0/serverless/pytorch/dschoerk/transt/nuclio/main.py |
| `model_handler.py` | https://github.com/cvat-ai/cvat/blob/v2.61.0/serverless/pytorch/dschoerk/transt/nuclio/model_handler.py |

上游模型仓库（构建时 nuclio 自动 `git clone --depth 1 --branch v1.0 https://github.com/dschoerk/TransT`）：
https://github.com/dschoerk/TransT/tree/v1.0

## 字段速览（与 CVAT 2.61 规范一致）

- `metadata.name: pth-dschoerk-transt`，`metadata.namespace: cvat`
- `metadata.annotations.type: tracker` ← CVAT 据此把它列在 Model 页面的 Trackers
- `spec.runtime: python:3.10`，`spec.handler: main:handler`
- CPU 版：`baseImage: ubuntu:22.04`，`torch==1.13.1+cpu torchvision==0.14.1+cpu numpy==1.26.4`
- GPU 版：`torch==1.13.1+cu117 torchvision==0.14.1+cu117`，并声明 `resources.limits.nvidia.com/gpu: 1`
- 权重：构建时从 Google Drive `https://drive.google.com/uc?id=1Pq0sK-9jmbLAVtgB9-dPDc2pipCxYdM5` 下载到 `/transt.pth`
- `pysot_toolkit` 由 dschoerk/TransT 仓库自带，`PYTHONPATH=/opt/nuclio/trans-t` 自动可导入

## 部署命令（在已装好 Nuclio 的服务器上执行，详见 ../../docs/deploy-runbook.md）

```bash
# CPU
nuctl deploy --project-name cvat --path . \
  --file function.yaml --platform local \
  --env CVAT_FUNCTIONS_REDIS_HOST=cvat_redis_ondisk \
  --env CVAT_FUNCTIONS_REDIS_PORT=6666 \
  --platform-config '{"attributes": {"network": "cvat_cvat"}}'

# GPU（有 NVIDIA 时）
nuctl deploy --project-name cvat --path . \
  --file function-gpu.yaml --platform local \
  --env CVAT_FUNCTIONS_REDIS_HOST=cvat_redis_ondisk \
  --env CVAT_FUNCTIONS_REDIS_PORT=6666 \
  --platform-config '{"attributes": {"network": "cvat_cvat"}}'
```

## 内网构建注意（重要）

`nuctl deploy` 构建镜像时需要在**构建机**上访问：
1. Docker Hub：`ubuntu:22.04`、nuclio builder 镜像
2. PyPI 与 `https://download.pytorch.org/whl/cpu`（或 cu117）
3. GitHub：`git clone https://github.com/dschoerk/TransT`
4. Google Drive：下载 transt.pth 权重

目标服务器 172.16.88.68 内网不通上述地址。两种落地方式见 `../../docs/nuclio-setup.md` §离线构建：
- 方式 A：在一台能上外网、且能通过 `DOCKER_HOST=ssh://user@172.16.88.68` 连到服务器 docker 的机器上执行 nuctl（构建发生在服务器 docker，外网请求从这台跳板机走代理）；
- 方式 B：在外网机器 `nuctl deploy` 出镜像后 `docker save`，scp 到服务器 `docker load`，再在服务器上用已有镜像部署（修改 function.yaml 的 build 指令为直接引用镜像）。
