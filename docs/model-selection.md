# CVAT 2.61 Serverless Tracker 模型选型报告

> 调研对象：openvinotoolkit / cvat-ai/cvat **v2.61.0** 官方 `serverless/` 目录。
> 调研方式：拉取 v2.61.0 源码包（`https://codeload.github.com/cvat-ai/cvat/tar.gz/refs/tags/v2.61.0`）逐文件核对 function.yaml 的 `annotations.type`。
> 业务场景：仓库/叉车视频，目标为叉车、托盘、货物等大目标，运动较规律；首帧（或前几帧）人工框选后自动跨帧跟踪 bbox，必要时可分割。

---

## 0. 核心结论（先看这里）

**CVAT v2.61.0 官方 serverless 中，`annotations.type: tracker` 的函数只有一个：TransT（`pth-dschoerk-transt`）。**

证据：对 v2.61.0 源码包 `serverless/**/function*.yaml` 全部 14 个函数逐一解析 `annotations.type`，结果如下：

| 函数名 | type | 框架 |
|---|---|---|
| onnx-wongkinyiu-yolov7 | detector | ONNX |
| openvino-omz-intel-face-detection-0205 | detector | OpenVINO |
| openvino-omz-public-mask_rcnn_inception_resnet_v2_atrous_coco | detector | OpenVINO |
| **pth-dschoerk-transt** | **tracker** | PyTorch |
| pth-facebookresearch-detectron2-retinanet_r101 | detector | PyTorch |
| pth-facebookresearch-sam-vit-h | interactor | PyTorch |
| pth-mmpose-hrnet32 | detector | PyTorch |
| pth-shiyinzhang-iog | interactor | PyTorch |
| tf-faster-rcnn-inception_v2_coco | detector | TF |

来源：
- 源码树：`https://github.com/cvat-ai/cvat/tree/v2.61.0/serverless`
- TransT function.yaml：`https://github.com/cvat-ai/cvat/blob/v2.61.0/serverless/pytorch/dschoerk/transt/nuclio/function.yaml`
- CVAT 官方文档“Creating Custom Serverless Functions → Creating a Tracker Function”：`https://docs.cvat.ai/docs/for-developers/serverless/`（镜像 `https://mintlify.wiki/cvat-ai/cvat/self-hosted/serverless/custom-models`）

**推荐：**

- **首选（唯一官方 tracker）：TransT（CPU 版 `function.yaml`）** —— 开箱即用、bbox 输出、无需 GPU、与 CVAT 2.61 完全对齐。
- **备选（同模型 GPU 版）：TransT-GPU（`function-gpu.yaml`）** —— 若服务器有 NVIDIA GPU 且视频帧率/分辨率高，换 GPU 镜像即可，部署路径完全一致。
- **分割备选：SAM（interactor）+ IOG** —— 需要 mask 时按帧交互分割，不是跨帧 tracker，需人工逐帧辅助；或后续升级到 CVAT 新的 SAM2 agent（见 §5）。

---

## 1. CVAT tracker 类函数的协议（v2.61 实证）

tracker 与 detector/interactor 的本质区别：**它是有状态的**。CVAT 前端每发一帧，都把上一帧模型返回的 opaque `state` 原样带回；模型据此跨帧维持同一个目标的身份。

请求体（main.py 实测）：
```json
{ "image": "<base64 jpg/png>", "shapes": [[x1,y1,x2,y2], ...], "states": [<上一帧state>, ...] }
```
响应体：
```json
{ "shapes": [[x1,y1,x2,y2], ...], "states": [<新state>, ...] }
```
- 某 shape 的 `state` 为 `null`/缺失 ⇒ 首帧初始化（用该框在当前帧建模板）。
- 否则用带回的 state 在当前帧续跟，输出新 bbox + 新 state。
- 一次请求可带多个 shape（多目标同时跟踪），function.yaml 注释按“4K 图上同时跟 100 个目标”把 `maxRequestBodySize` 设到 256MB。

metadata 规范（官方文档 + v2.61 真实 yaml 双重核对）：
```yaml
metadata:
  name: pth-dschoerk-transt
  namespace: cvat
  annotations:
    name: TransT
    type: tracker        # ← 决定 CVAT 把它列为 Trackers
    spec:                # tracker 可写 supported_shape_types: rectangle,polygon（TransT 留空=默认 rectangle）
```

---

## 2. 候选模型逐一对比

### 2.1 TransT（✅ v2.61 官方内置 tracker）

| 项 | 内容 |
|---|---|
| 原理一句话 | 孪生特征提取 backbone（ResNet 22/101）+ Transformer 自/交叉注意力融合模板帧与搜索帧特征 + 分类/回归头，无在线更新模块，一次框选后逐帧回归 bbox。 |
| 初始化 | **首帧单框**初始化（CVAT 里在第一帧画框 → Track）。 |
| 输出 | **bbox（rectangle）**。CVAT 版不输出 mask。 |
| 速度 | 论文：GPU 上约 **50–54 FPS**（TransT），TransT-M 约 42 FPS。 |
| 精度 | LaSOT / TrackingNet / GOT-10k 上 2021 年 SOTA；VOT2021 实时赛道冠军（TransT-M）。 |
| CPU/GPU | 官方同时提供 CPU 镜像（`torch==1.13.1+cpu`）与 GPU 镜像（`torch==1.13.1+cu117`，声明 `nvidia.com/gpu: 1`）。 |
| 镜像大小 | baseImage `ubuntu:22.04` + PyTorch CPU wheel，经验值 **~2–3 GB**；GPU 版因 cu117 约 **4–6 GB**。构建时现场拉取，非预构建镜像。 |
| 许可证 | 上游 TransT 代码 **GPL-3.0**；CVAT wrapper（main.py/model_handler.py）为 MIT。 |
| 上游 | 论文 arXiv:2103.15436（CVPR 2021）：`https://arxiv.org/abs/2103.15436`；CVAT 用的封装 `dschoerk/TransT` tag v1.0：`https://github.com/dschoerk/TransT/tree/v1.0`；权重 Google Drive file id `1Pq0sK-9jmbLAVtgB9-dPDc2pipCxYdM5`（构建时 `ADD` 进镜像）。 |

来源：
- `https://github.com/cvat-ai/cvat/blob/v2.61.0/serverless/pytorch/dschoerk/transt/nuclio/function.yaml`
- `https://github.com/cvat-ai/cvat/blob/v2.61.0/serverless/pytorch/dschoerk/transt/nuclio/model_handler.py`
- `https://www.computer.org/csdl/proceedings-article/cvpr/2021/450900i122/1yeLxFwneEM`（“runs at approximatively 50 fps on GPU”）

### 2.2 SiamMask（⚠️ 历史版本曾内置，2.61 已移除）

| 项 | 内容 |
|---|---|
| 原理一句话 | SiamRPN 基础上增加 mask 分支，单目标跟踪同时输出 bbox + 二值 mask。 |
| 初始化 | 首帧框选。 |
| 输出 | bbox **+ mask**（这是它相对 TransT 的唯一优势）。 |
| 速度/精度 | GPU 实时（VOT2019 级别），精度弱于 TransT。 |
| CVAT 现状 | **v2.61 的 serverless 目录里已不存在**（历史路径 `serverless/pytorch/foolwood/siammask` 在旧版 cvat 中存在，见 `https://science.china-vo.org/gitlab/external/cvat/-/commits/.../serverless/pytorch/foolwood/siammask/nuclio/model_handler.py`）。 |
| 许可证 | MIT-licensed wrapper，上游 SiamMask 为 MIT。 |

结论：**不推荐为 2.61 重新引入**。若必须要“跟踪 + mask”，优先评估 §2.6 SAM/IOG 或 §2.7 SAM2 agent，而不是把已下线的老函数搬回来。

### 2.3 SiamRPN++ / NanoTrack / ViT-Track（❌ 从未作为 CVAT 官方 serverless 函数发布）

| 模型 | 说明 |
|---|---|
| SiamRPN++ | 经典孪生 anchor-free 跟踪器（CVPR 2019）。社区汇总仓库 `HonglinChu/SiamTrackers`（`https://github.com/HonglinChu/SiamTrackers`）。**CVAT 官方 serverless 从未发布该函数**。 |
| NanoTrack | 轻量孪生跟踪（2022）。同样只在社区仓库出现，**非 CVAT 官方函数**。 |
| ViT-Track | 纯 ViT 端到端跟踪（ECCV 2022）。**非 CVAT 官方函数**。 |

结论：三者都需要自行按 CVAT tracker 协议（§1）包一层 nuclio function，工作量与风险都高于直接用官方 TransT。**不建议在本项目首期引入。**

### 2.4 ByteTrack / MOT 类（❌ 与“首帧框选跟踪”不是一类问题）

| 项 | 内容 |
|---|---|
| 原理 | 先检测器（YOLO/ByteTrack 关联）逐帧检测，再做跨帧数据关联，输出多目标轨迹。 |
| 与本场景差距 | 需要**目标类别固定且检测器能每帧检出**叉车/托盘；它不是“用户框一个跟一个”的单目标 tracker，而是全自动检测+关联。CVAT 官方文档 academy 页提到 ByteTrack 用于自动标注视频（`https://www.cvat.ai/academy/auto-annotation-tools`），但 **v2.61 serverless 目录中没有 ByteTrack tracker 函数**。 |
| 适用性 | 若未来需要“全程自动标所有叉车”，可部署 YOLO detector + 在 CVAT 外用 ByteTrack 跑轨迹再导回；不属于本期“首帧框选自动跟踪”的范畴。 |

### 2.5 SiamMask/TransT 之外的 mask 跟踪（SAMURAI、Segment-Anything-Track）

| 模型 | 说明 |
|---|---|
| SAMURAI | 基于 SAM2 的视频对象分割跟踪（arXiv:2411.31732），首帧框选/点选后跨帧输出 **mask**。学术项目，**未打包进 CVAT serverless**。 |
| Segment-Anything-Track / Cutie 等 | 同类 VOS 研究项目，均非 CVAT 官方函数。 |

结论：CVAT 2.61 已自带 SAM interactor（逐帧点选/框选出 mask）。若真要“首帧框 → 跨帧 mask”，最省事的官方路径是升级使用 CVAT 新的 **SAM2 agent**（见 §5），而不是自包 SAMURAI。

### 2.6 SAM / IOG（interactor，补充项）

- `pth-facebookresearch-sam-vit-h`：interactor，框/点引导出 mask，**逐帧**调用，不跨帧维持状态。
- `pth-shiyinzhang-iog`：interactor，`startswith_box: true`（先给包围框再补正负点得 mask），同样逐帧。
- 用途：当 TransT 跟丢或需要精细分割时，人工补帧。

---

## 3. 针对仓库/叉车场景的推荐

场景特征：固定机位（仓库 CCTV）、目标大（叉车/托盘/货箱）、运动方向规律、遮挡中等、对跨帧身份一致性要求高。

| 维度 | TransT CPU | TransT GPU |
|---|---|---|
| 与 CVAT 2.61 集成 | ✅ 官方现成 | ✅ 官方现成 |
| 首帧框选即用 | ✅ | ✅ |
| CPU 上 1080p 实时性 | 约 5–15 FPS（ResNet22，单目标），标注场景“逐帧点 Track 前进”够用 | — |
| GPU 上 | — | ~50 FPS，多目标同时跟也不卡 |
| bbox 稳定性 | 对大目标、平滑运动鲁棒；无在线更新，目标大幅形变/出画后会跟丢，需人工纠正 | 同左 |
| 内网落地难度 | 中（构建需联网拉 pip/github/权重，见 nuclio-setup.md 离线方案） | 中 + 需 NVIDIA 驱动/nvidia-container-runtime |

**最终建议：**

1. **首选：部署 TransT CPU 版**（`serverless/tracker/transt/function.yaml`）。理由：
   - 它是 v2.61 官方维护、字段规范已核对无误的唯一 tracker；
   - 仓库视频通常 1080p/低帧率，CPU 版足够支撑“人工首帧框选 → 连续点 Track 跟进”的标注流；
   - 不依赖 GPU，内网服务器部署链路最短。
2. **若服务器有 NVIDIA GPU：改用 TransT GPU 版**（同目录 `function-gpu.yaml`），镜像 tag `cvat.pth.dschoerk.transt:latest-gpu`，声明 `nvidia.com/gpu: 1`。
3. **mask 需求**：不引入 SiamMask/SAMURAI。先用 TransT 出 bbox；需要精细分割的少量帧，用官方 SAM interactor 逐帧补。
4. **后续升级方向**：若视频量大、需要“全程自动检测+跟踪所有叉车”，再评估 YOLO detector + ByteTrack 后处理，或 CVAT 的 SAM2 agent（见 §5）。

---

## 4. 官方上游链接汇总

- CVAT v2.61 serverless 目录：`https://github.com/cvat-ai/cvat/tree/v2.61.0/serverless`
- TransT 函数目录：`https://github.com/cvat-ai/cvat/tree/v2.61.0/serverless/pytorch/dschoerk/transt/nuclio`
- TransT 论文：`https://arxiv.org/abs/2103.15436`
- TransT 封装仓库（CVAT 构建时 clone）：`https://github.com/dschoerk/TransT/tree/v1.0`
- CVAT tracker 自定义文档：`https://docs.cvat.ai/docs/for-developers/serverless/`
- Nuclio 本地平台文档：`https://docs.nuclio.io/en/latest/setup/docker/getting-started-docker.html`

## 5. 备注：CVAT 2.61 的新 AI agents（非本期采用）

v2.61 源码中另有 `ai-models/agents_deployment/`（SAM2、YOLO 的 agent 化部署模板），走的是“CVAT 后端直接注册远程模型服务”的新路线，不经过 Nuclio 端口 8070；但它需要能访问 HuggingFace 拉 SAM2 权重，且与现有 docker-compose 加装方式不同。本期坚持走成熟、可离线的 Nuclio + TransT 路线，SAM2 agent 作为后续升级选项记录。
