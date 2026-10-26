"""Check a fixed report's local links, snapshot counts, image bindings and hashes.

Offline only. Visual review is a separately recorded human/agent action; this
validator does not infer visual correctness from counts or file integrity.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--checkpoint-dir", type=Path, required=True)
    ap.add_argument("--report", type=Path, action="append", default=[])
    args = ap.parse_args()
    dest = args.checkpoint_dir.resolve()
    state = read(dest / "checkpoint.json")
    annotations = read(dest / "saved-annotations.json")
    shapes = annotations["shapes"]
    assert state["last_readback_sha256"] == sha(dest / "saved-annotations.json")
    assert state["saved_masks"] == sum(s["type"] == "mask" for s in shapes)
    assert state["label_counts"] == dict(Counter(str(s["label_id"]) for s in shapes))
    assert state["source_counts"] == dict(Counter(s["source"] for s in shapes))
    assert state["nonempty_frames"] == len({s["frame"] for s in shapes})
    ledger = read(dest / "operation-ledger.json")
    assert state["batches"] == len(ledger)
    assert state["verified_operations"] == sum(r["verified_operations"] for r in ledger)
    assert state["verified_actions"] == dict(sum((Counter(r["actions"]) for r in ledger), Counter()))
    reviewed_sources = {}
    if state["all_classes_completed_frames"]:
        receipt = read(dest / "all-classes-coverage-review.json")
        assert receipt["status"] == "PRACTICAL_SEVEN_CLASS_COVERAGE_REVIEW_COMPLETE"
        assert receipt["snapshot_sha256"] == state["last_readback_sha256"]
        assert receipt["completed_frames"] == list(range(state["frames"]))
        assert receipt["structural_errors"] == 0
        reviewed_sources = {r['frame']: r['source_image_sha256'] for r in receipt['source_images']}
        assert sorted(reviewed_sources) == list(range(state['frames']))
    bindings = read(dest / "image-bindings.json")
    assert len({b["frame"] for b in bindings}) == len(bindings)
    for b in bindings:
        assert sha(dest / b["image"]) == b["image_sha256"]
        expected = [s["id"] for s in shapes if s["frame"] == b["frame"]]
        assert b["display_shape_ids"] == expected and b["saved_shapes"] == len(expected)
        assert b["visual_review"].startswith("PASS"), ("image not visually reviewed", b["image"])
        if reviewed_sources:
            assert b['source_sha256'] == reviewed_sources[b['frame']]
    manifest = read(dest / "asset-manifest.json")
    assert len({r["file"] for r in manifest}) == len(manifest)
    assert {r["file"] for r in manifest} == {p.name for p in dest.iterdir() if p.is_file() and p.name != "asset-manifest.json"}
    for row in manifest:
        assert sha(dest / row["file"]) == row["sha256"]
    links = 0
    for report in args.report:
        body = report.read_text(encoding="utf-8")
        for target in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", body):
            if re.match(r"[a-zA-Z]+://", target) or target.startswith("#"):
                continue
            target = unquote(target.split("#", 1)[0].split("?", 1)[0].strip("<>"))
            assert (report.parent / target).exists(), (report, target)
            links += 1
    sensitive = re.compile(r"[A-Za-z]:[\\/](?:Users|Documents)[\\/]|172\.16\.88\.68|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|Bearer\s+[A-Za-z0-9._-]{20,}")
    scans = [p for p in dest.iterdir() if p.suffix in (".json", ".md")] + args.report
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for key, item in value.items():
                yield str(key)
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)
    for path in scans:
        values = strings(read(path)) if path.suffix == '.json' else [path.read_text(encoding='utf-8')]
        assert not any(sensitive.search(v) for v in values), ("sensitive-pattern match", path.name)
    print(json.dumps({"status": "PASS", "assets": len(manifest), "image_bindings": len(bindings), "local_links": links,
                      "saved_masks": state["saved_masks"], "batches": state["batches"], "sensitive_pattern_matches": 0}))


if __name__ == "__main__":
    main()
