# -*- coding: utf-8 -*-
"""渲染 demo：下载 frame 0/10/20/30 原图，叠加该帧 mask（按 label 着色半透明），
输出 results/demo/demo-frame-XXX.png 与 demo-grid.png。"""
import io, json, os, urllib.request, http.cookiejar
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "results", "demo")
CVAT = "http://172.16.88.68:12080"
JOB = 423
FRAMES = [0, 10, 20, 30]

COLORS = {
    201: (255, 165, 0),    # pallet 橙
    202: (80, 130, 255),   # person 蓝
    203: (80, 220, 120),   # floor 绿
    204: (240, 220, 80),   # slope 黄
    205: (190, 120, 255),  # ego 紫
    206: (80, 220, 230),   # other_robot 青
    207: (255, 130, 180),  # cargo_floor 粉
}
NAMES = {201: "pallet", 202: "person", 203: "floor", 204: "slope",
         205: "ego", 206: "other_robot", 207: "cargo_floor"}

cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
r = op.open(urllib.request.Request(CVAT + "/api/auth/login",
    data=json.dumps({"username": "leonlee233", "password": "leonlee233"}).encode(),
    headers={"Content-Type": "application/json"}))
TOKEN = json.loads(r.read())["key"]

def get(url, binary=False):
    rq = urllib.request.Request(url)
    rq.add_header("Authorization", "Token " + TOKEN)
    with op.open(rq, timeout=120) as resp:
        return resp.read()

ann = json.loads(get(f"{CVAT}/api/jobs/{JOB}/annotations"))
print("total shapes:", len(ann["shapes"]))

def load_font(size):
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\arial.ttf"):
        try: return ImageFont.truetype(p, size)
        except Exception: pass
    return ImageFont.load_default()

tiles = []
for f in FRAMES:
    raw = get(f"{CVAT}/api/jobs/{JOB}/data?type=frame&number={f}&quality=original")
    img = Image.open(io.BytesIO(raw)).convert("RGBA")
    # 缩放到宽 960
    w, h = img.size
    scale = 960 / w
    img = img.resize((960, int(h * scale)), Image.LANCZOS)
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    dr = ImageDraw.Draw(overlay)
    shapes_f = [s for s in ann["shapes"] if s["frame"] == f]
    for s in shapes_f:
        pts = s["points"]
        if len(pts) < 6: continue
        poly = [(pts[i] * scale, pts[i + 1] * scale) for i in range(0, len(pts) - 1, 2)]
        c = COLORS.get(s["label_id"], (255, 255, 255))
        dr.polygon(poly, fill=c + (110,), outline=c + (255,))
    out = Image.alpha_composite(img, overlay).convert("RGB")
    d = ImageDraw.Draw(out)
    d.rectangle([0, 0, 300, 44], fill=(0, 0, 0))
    d.text((10, 8), f"frame {f}  ({len(shapes_f)} shapes)", fill=(255, 255, 0), font=load_font(24))
    path = os.path.join(DEMO, f"demo-frame-{f:03d}.png")
    out.save(path)
    tiles.append(out)
    print(f"frame {f}: {len(shapes_f)} shapes → {path}")

# 2x2 拼图
tw, th = tiles[0].size
grid = Image.new("RGB", (tw * 2 + 20, th * 2 + 20), (30, 30, 30))
for i, t in enumerate(tiles):
    grid.paste(t, ((i % 2) * (tw + 20), (i // 2) * (th + 20)))
grid_path = os.path.join(DEMO, "demo-grid.png")
grid.save(grid_path)
print("grid →", grid_path)
