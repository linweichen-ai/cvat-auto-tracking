"""Build a fixed, local, publication-staged checkpoint from readback evidence.

No network or annotation writes. Use an explicit last run so later work cannot
silently change the meaning of an existing report. Credentials are never read.
"""
import argparse
import collections
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from cvat_sdk.masks import decode_mask


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--evidence-root", type=Path, required=True)
    ap.add_argument("--through-run", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--frames", nargs="+", type=int, required=True)
    args = ap.parse_args()
    assert all(ch.isalnum() or ch in "-_" for ch in args.tag)
    root = args.evidence_root.resolve()
    final_run = root / "incremental_runs" / args.through_run
    assert final_run.is_dir()
    final_path = sorted(final_run.glob("step_*/after.json"))[-1]
    final = read(final_path)
    dest = Path(__file__).resolve().parents[3] / "results/reviewed-workflow" / args.tag
    dest.mkdir(parents=True, exist_ok=True)
    ledger = []
    for run in sorted((root / "incremental_runs").glob("plan_*")):
        if run.name > args.through_run or not (run / "results.json").exists():
            continue
        results = read(run / "results.json")
        verified = [r for r in results if r.get("status") == "READBACK_VERIFIED"]
        if not verified:
            continue
        ledger.append({"run": run.name, "verified_operations": len(verified),
                       "actions": dict(collections.Counter(r["action"] for r in verified)),
                       "frames": sorted({r["frame"] for r in verified}),
                       "results_sha256": sha(run / "results.json"),
                       "last_readback_sha256": sha(sorted(run.glob("step_*/after.json"))[-1]),
                       "candidate_sha256": read(run / "plan.json")["candidate_sha256"]})
    summary = {"task_id": 430, "job_id": 423, "frames": 150,
               "last_run": args.through_run, "last_readback_sha256": sha(final_path),
               "saved_masks": sum(s["type"] == "mask" for s in final["shapes"]),
               "nonempty_frames": len({s["frame"] for s in final["shapes"]}),
               "source_counts": dict(collections.Counter(s["source"] for s in final["shapes"])),
               "label_counts": dict(collections.Counter(str(s["label_id"]) for s in final["shapes"])),
               "verified_operations": sum(r["verified_operations"] for r in ledger),
               "verified_actions": dict(sum((collections.Counter(r["actions"]) for r in ledger), collections.Counter())),
               "batches": len(ledger), "all_classes_completed_frames": 0,
               "pallet_full_image_complete": False,
               "classification": "Independent identifiable pallets only; integral wooden shipping-crate bases excluded. Separate wooden pallets below plastic crates remain included.",
               "material_holds": [{"frame": f, "instance": "rack_upper_bottom_blue_clipped", "material": "white corner strip retained pending evidence"} for f in [133, 146]],
               "caution": "Counts are not accuracy, recall or completion. This is a fixed intermediate checkpoint."}
    save(dest / "checkpoint.json", summary)
    save(dest / "operation-ledger.json", ledger)
    save(dest / "saved-annotations.json", final)
    bindings = []
    colors = [(235, 35, 160), (30, 220, 190), (170, 70, 245), (240, 130, 45), (240, 220, 20)]
    for frame in args.frames:
        src = root / f"baseline/live_20261026T070739278666Z/frame_api_{frame:03d}.jpg"
        original = Image.open(src).convert("RGB")
        overlay = np.asarray(original).astype(float)
        shapes = [s for s in final["shapes"] if s["frame"] == frame]
        masks = []
        for i, shape in enumerate(shapes):
            mask = decode_mask(shape["points"], image_width=640, image_height=480).astype(bool)
            overlay[mask] = overlay[mask] * .6 + np.array(colors[i % len(colors)]) * .4
            masks.append(mask)
        canvas = Image.new("RGB", (1280, 520), (20, 24, 30))
        canvas.paste(original, (0, 40))
        canvas.paste(Image.fromarray(overlay.astype("uint8")), (640, 40))
        draw = ImageDraw.Draw(canvas)
        draw.text((8, 6), f"Frame {frame} | ORIGINAL / SAVED MASKS {len(shapes)} | CHECKPOINT {summary['saved_masks']} MASKS", fill="white")
        draw.text((8, 23), "Intermediate evidence: full-image coverage and material review still in progress.", fill="white")
        for i, mask in enumerate(masks):
            yy, xx = np.where(mask)
            # Place numbers on an actual mask pixel, never over the excluded cargo.
            nearest = np.argmin((xx - xx.mean()) ** 2 + (yy - yy.mean()) ** 2)
            draw.text((640 + int(xx[nearest]), 40 + int(yy[nearest])), str(i + 1), fill="white", stroke_width=1, stroke_fill="black")
        target = dest / f"frame-{frame:03d}-saved.jpg"
        canvas.save(target, quality=94)
        bindings.append({"frame": frame, "source_sha256": sha(src), "image": target.name,
                         "image_sha256": sha(target), "saved_shapes": len(shapes),
                         "display_shape_ids": [s["id"] for s in shapes], "visual_review": "PENDING"})
    save(dest / "image-bindings.json", bindings)
    save(dest / "asset-manifest.json", [{"file": p.name, "sha256": sha(p)} for p in sorted(dest.iterdir()) if p.is_file() and p.name != "asset-manifest.json"])
    print(json.dumps({"output": str(dest), **summary}))


if __name__ == "__main__":
    main()
