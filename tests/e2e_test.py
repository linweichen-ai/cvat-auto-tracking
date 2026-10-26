# -*- coding: utf-8 -*-
"""
E2E 端到端验证（不依赖浏览器）：
  登录 CVAT → auth_status → get_labels → 备份 job419 annotations →
  cache_frames(0-1) → propagate_shape(frame0 某实例 → frame1) →
  拉 annotations 验证新增 shape → DELETE 回滚 → 验证恢复原状。
所有请求/响应/结论写入 results/e2e-test-log.md。
写操作只作用于 DemoTask job 419，且全程备份可回滚。
"""
import json
import os
import sys
import time
import urllib.request
import urllib.parse
import http.cookiejar
from collections import Counter
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "results")
os.makedirs(RESULTS, exist_ok=True)

CVAT = "http://172.16.88.68:12080"
SAM3 = "http://172.16.88.68:11089"
JOB_ID = 419
START_FRAME, END_FRAME = 0, 1
LABEL_ID = 201  # pallet

log_lines = []
def log(text=""):
    log_lines.append(text)
    print(text)

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def http_call(url, body=None, headers=None, method="POST", timeout=900):
    rq = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method)
    rq.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        rq.add_header(k, v)
    with opener.open(rq, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode())

def sam3(endpoint, payload, timeout=900):
    return http_call(SAM3 + endpoint, payload, headers=H, timeout=timeout)

def ann_get():
    rq = urllib.request.Request(f"{CVAT}/api/jobs/{JOB_ID}/annotations")
    rq.add_header("Authorization", "Token " + TOKEN)
    with urllib.request.urlopen(rq, timeout=60) as r:
        return json.loads(r.read().decode())

def ann_restore(backup_ann):
    # CVAT 2.61 实测回滚方式：DELETE /annotations 会清空整个 job（不接受 shape_id）；
    # PATCH ?action=delete 需要完整 mask payload，按 id 删除不可行。
    # 已验证可行：剥掉 id 后整份 PUT 恢复（CREATE 语义，服务端重新分配 id）。
    data = json.loads(json.dumps(backup_ann))  # 深拷贝
    for s in data.get("shapes", []): s.pop("id", None)
    for t in data.get("tracks", []): t.pop("id", None)
    for t in data.get("tags", []): t.pop("id", None)
    rq = urllib.request.Request(f"{CVAT}/api/jobs/{JOB_ID}/annotations",
                                data=json.dumps(data).encode(), method="PUT")
    rq.add_header("Content-Type", "application/json")
    rq.add_header("Authorization", "Token " + TOKEN)
    with urllib.request.urlopen(rq, timeout=300) as r:
        return r.status, r.read().decode()[:300]

def per_frame(ann):
    return dict(sorted(Counter((s["frame"], s["label_id"]) for s in ann.get("shapes", [])).items()))

conclusions = []

# ---------------------------------------------------------------- 1. 登录
log("# CVAT 自助标注 E2E 测试日志")
log(f"时间: {datetime.now().isoformat(timespec='seconds')}")
log(f"目标: CVAT={CVAT}  SAM3={SAM3}  Job={JOB_ID}  范围={START_FRAME}-{END_FRAME}\n")

st, login_body = http_call(CVAT + "/api/auth/login", {"username": "leonlee233", "password": "leonlee233"})
TOKEN = login_body["key"]
cookies = {c.name: c.value for c in cj}
H = {"X-CVAT-URL": CVAT,
     "X-CVAT-Session-ID": cookies["sessionid"],
     "X-CVAT-CSRF-Token": cookies.get("csrftoken", "")}
log(f"## 1. 登录 CVAT\n- POST /api/auth/login → {st}，已获取 API token 与 sessionid/csrftoken（HttpOnly，浏览器侧靠 chrome.cookies/GM_cookie 读取）。")

# ---------------------------------------------------------------- 2. auth_status / get_labels
st, r = sam3("/api/auth_status", {}, 15)
log(f"## 2. SAM3 连接检测\n- POST /api/auth_status → {st} `{json.dumps(r, ensure_ascii=False)}`")
assert r.get("authenticated") is True, "auth_status 未认证！"

st, r = sam3("/api/get_labels", {"job_id": JOB_ID}, 15)
labels = r.get("labels", [])
log(f"- POST /api/get_labels {{job_id:{JOB_ID}}} → {st}，{len(labels)} 个标签: {[l['name'] for l in labels]}")
assert len(labels) == 7, f"期望 7 个标签，实际 {len(labels)}"

# ---------------------------------------------------------------- 3. 备份 annotations
ann0 = ann_get()
backup_path = os.path.join(RESULTS, f"job{JOB_ID}_annotations_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
with open(backup_path, "w", encoding="utf-8") as f:
    json.dump(ann0, f, ensure_ascii=False, indent=1)
ids0 = {s["id"] for s in ann0["shapes"]}
log(f"\n## 3. 备份 annotations\n- GET /api/jobs/{JOB_ID}/annotations → shapes={len(ann0['shapes'])}")
log(f"- 备份文件: `results/{os.path.basename(backup_path)}`")
log(f"- 分帧分布(frame,label): {per_frame(ann0)}")

# 选种子：START_FRAME 上 LABEL_ID 的第一个 shape
seed = [s for s in ann0["shapes"] if s["frame"] == START_FRAME and s["label_id"] == LABEL_ID][0]
SEED_ID = seed["id"]
log(f"- 选定种子 shape: id={SEED_ID} frame={START_FRAME} label={LABEL_ID} type={seed['type']}")

# ---------------------------------------------------------------- 4. cache
log(f"\n## 4. 缓存帧 {START_FRAME}-{END_FRAME}")
st, r = sam3("/api/cache_frames", {"job_id": JOB_ID, "start_frame": START_FRAME, "end_frame": END_FRAME}, 300)
log(f"- POST /api/cache_frames → {st} `{json.dumps(r, ensure_ascii=False)[:200]}`")
ready = False
for i in range(60):
    st, r = sam3("/api/cache_status", {"job_id": JOB_ID, "start_frame": START_FRAME, "end_frame": END_FRAME}, 15)
    log(f"- cache_status: loaded={r.get('loaded')}/{r.get('total')} range_ready={r.get('range_ready')}")
    if r.get("range_ready"):
        ready = True
        break
    time.sleep(2)
assert ready, "缓存未就绪"
log("- 结论: 帧缓存 OK")

# ---------------------------------------------------------------- 5. propagate_shape
log(f"\n## 5. propagate_shape 种子 {SEED_ID} → {START_FRAME}-{END_FRAME}")
st, r = sam3("/api/propagate_shape", {
    "job_id": JOB_ID, "start_frame": START_FRAME, "end_frame": END_FRAME,
    "label_id": LABEL_ID, "shape_id": SEED_ID})
log(f"- POST /api/propagate_shape → {st} `{json.dumps(r, ensure_ascii=False)[:300]}`")
time.sleep(2)  # 等后端落库

# ---------------------------------------------------------------- 6. 验证新增
ann1 = ann_get()
ids1 = {s["id"] for s in ann1["shapes"]}
new_ids = sorted(ids1 - ids0)
log(f"\n## 6. 验证新增\n- 传播后 shapes={len(ann1['shapes'])}（传播前 {len(ann0['shapes'])}）")
log(f"- 新增 shape IDs: {new_ids}")
new_shapes = [s for s in ann1["shapes"] if s["id"] in set(new_ids)]
for s in new_shapes:
    log(f"  - new id={s['id']} frame={s['frame']} label_id={s['label_id']} type={s['type']}")
assert new_ids, "传播后没有新增任何 shape！"
assert all(s["frame"] >= START_FRAME and s["frame"] <= END_FRAME for s in new_shapes), "新增 shape 超出帧范围！"
assert all(s["label_id"] == LABEL_ID for s in new_shapes), "新增 shape 标签不匹配！"
conclusions.append(f"✅ propagate_shape 生效：新增 {len(new_ids)} 个 shape（{new_ids}），全部落在 frame {START_FRAME}-{END_FRAME}、标签 {LABEL_ID}")

# ---------------------------------------------------------------- 7. 回滚
log(f"\n## 7. 回滚：PUT 整份恢复备份")
log("- （API 教训：CVAT 2.61 的 DELETE /annotations 会清空整个 job；PATCH ?action=delete 需要完整 mask payload，按 id 删除不可行。已验证回滚方式=剥 id 后 PUT 整份备份）")
st, body = ann_restore(ann0)
log(f"- PUT /api/jobs/{JOB_ID}/annotations（备份整份，去掉 id）→ {st}")
time.sleep(2)

ann2 = ann_get()
ids2 = {s["id"] for s in ann2["shapes"]}
log(f"- 回滚后 shapes={len(ann2['shapes'])}（原始 {len(ann0['shapes'])}）")
assert len(ids2) == len(ids0), f"shape 总数 {len(ids2)} ≠ 备份 {len(ids0)}！"
assert per_frame(ann2) == per_frame(ann0), f"分帧分布与备份不一致！\n回滚:{per_frame(ann2)}\n备份:{per_frame(ann0)}"
conclusions.append(f"✅ 回滚成功：shape 数量与分帧分布完全恢复到备份状态（{len(ids2)} 个；PUT 会重新分配 id，属正常）")

# ---------------------------------------------------------------- 8. 汇总
log("\n## 测试结论汇总")
for c in conclusions:
    log("- " + c)
log("- ✅ 全部断言通过：登录/连接/标签/缓存/传播/验证/回滚链路完整可用。")
log("- 说明：浏览器面板（userscript/扩展）复用本测试同一套 11089 API 与凭据头，此即面板功能可用的 API 证据。")

log_path = os.path.join(RESULTS, "e2e-test-log.md")
with open(log_path, "w", encoding="utf-8") as f:
    f.write("\n".join(log_lines) + "\n")
print(f"\n日志已写入: {log_path}")
