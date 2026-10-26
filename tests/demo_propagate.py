# -*- coding: utf-8 -*-
"""
真实任务演示：Task3 (task 430 / job 423, 150 帧)
流程：备份 annotations → cache_frames 0-30 → 逐种子 propagate_shape(0→30) → 统计分帧。
结果写入 results/demo/。标注保留（不回滚），回滚方法见报告。
用法: python tests/demo_propagate.py [end_frame]   默认 end_frame=30
"""
import json, os, sys, time, urllib.request, http.cookiejar, urllib.error
from collections import Counter
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "results", "demo")
os.makedirs(DEMO, exist_ok=True)

CVAT = "http://172.16.88.68:12080"
SAM3 = "http://172.16.88.68:11089"
JOB = 423
START = 0
END = int(sys.argv[1]) if len(sys.argv) > 1 else 30

cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def call(url, body=None, method="POST", token=None, timeout=900, retries=6):
    last = None
    for i in range(retries):
        try:
            rq = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method)
            rq.add_header("Content-Type", "application/json")
            if token: rq.add_header("Authorization", "Token " + token)
            with op.open(rq, timeout=timeout) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            last = e
            body_txt = ''
            try: body_txt = e.read().decode()[:200]
            except Exception: pass
            if e.code in (400, 409, 500, 502, 503) and i < retries - 1:
                log(f"  retry {i+1} after HTTP {e.code}: {body_txt}")
                time.sleep(5 * (i + 1))
                continue
            log(f"  GIVE UP HTTP {e.code}: {body_txt}")
            raise
    raise last

t0 = time.time()
def log(msg):
    print(f"[{time.time()-t0:7.1f}s] {msg}", flush=True)

# 1. 登录
_, login = call(CVAT + "/api/auth/login", {"username": "leonlee233", "password": "leonlee233"})
TOKEN = json.loads(login)["key"]
cookies = {c.name: c.value for c in cj}
H = {"X-CVAT-URL": CVAT, "X-CVAT-Session-ID": cookies["sessionid"],
     "X-CVAT-CSRF-Token": cookies.get("csrftoken", "")}
log("登录完成")
time.sleep(3)

def get_annotations():
    # 必须显式 GET（call 默认 POST 会路由到 annotations 导入接口）
    st, body = call(f"{CVAT}/api/jobs/{JOB}/annotations", token=TOKEN, method="GET", timeout=120)
    return json.loads(body)

def _sam(endpoint, payload, timeout=900):
    last = None
    for i in range(4):
        try:
            rq = urllib.request.Request(SAM3 + endpoint, data=json.dumps(payload).encode(), method="POST")
            rq.add_header("Content-Type", "application/json")
            for k, v in H.items(): rq.add_header(k, v)
            with op.open(rq, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (400, 409, 500, 502, 503) and i < 3:
                time.sleep(3 * (i + 1)); continue
            raise
    raise last

# 2. 备份
ann0 = get_annotations()
bk = os.path.join(DEMO, "job423-backup.json")
with open(bk, "w", encoding="utf-8") as f:
    json.dump(ann0, f, ensure_ascii=False)
seeds = [s for s in ann0["shapes"] if s["frame"] == START]
log(f"备份完成 → results/demo/job423-backup.json；frame{START} 种子 {len(seeds)} 个: " +
    ", ".join(f"id{s['id']}/L{s['label_id']}" for s in seeds))

# 3. 缓存
log(f"请求缓存 {START}-{END} ...")
r = _sam("/api/cache_frames", {"job_id": JOB, "start_frame": START, "end_frame": END}, 600)
log(f"cache_frames → {r}")
cached = False
for i in range(120):
    r = _sam("/api/cache_status", {"job_id": JOB, "start_frame": START, "end_frame": END}, 30)
    log(f"  cache_status: loaded={r.get('loaded')}/{r.get('total')} ready={r.get('range_ready')}")
    if r.get("range_ready"): cached = True; break
    time.sleep(3)
assert cached, "缓存超时"

# 4. 逐种子传播
per_seed = []
for s in seeds:
    sid, lid = s["id"], s["label_id"]
    log(f"传播种子 id={sid} label={lid} → frame {START}-{END} ...")
    try:
        r = _sam("/api/propagate_shape", {"job_id": JOB, "start_frame": START, "end_frame": END,
                                          "label_id": lid, "shape_id": sid}, 1800)
        log(f"  → {r}")
        per_seed.append({"shape_id": sid, "label_id": lid, "ok": True, "resp": r})
    except urllib.error.HTTPError as e:
        err = e.read().decode()[:300]
        log(f"  → HTTP {e.code} ERROR: {err}")
        per_seed.append({"shape_id": sid, "label_id": lid, "ok": False, "error": f"HTTP {e.code}: {err}"})
    except Exception as e:
        log(f"  → ERROR: {e}")
        per_seed.append({"shape_id": sid, "label_id": lid, "ok": False, "error": str(e)})

# 5. 重新拉 annotations 统计
time.sleep(3)
ann1 = get_annotations()
cnt = Counter(s["frame"] for s in ann1["shapes"] if START <= s["frame"] <= END)
table = {f: cnt.get(f, 0) for f in range(START, END + 1)}
log("分帧 shape 统计 (frame: count):")
for f, c in table.items():
    log(f"  frame {f:3d}: {c}")

summary = {
    "job": JOB, "range": [START, END], "seeds": per_seed,
    "before_total_shapes": len(ann0["shapes"]),
    "after_total_shapes": len(ann1["shapes"]),
    "per_frame_counts": table,
    "elapsed_sec": round(time.time() - t0, 1),
    "finished_at": datetime.now().isoformat(timespec="seconds"),
}
with open(os.path.join(DEMO, "propagate-summary.json"), "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=1)
log(f"完成。耗时 {summary['elapsed_sec']}s，before={len(ann0['shapes'])} after={len(ann1['shapes'])}")
