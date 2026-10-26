"""Configuration-scoped multi-instance mask proposals; offline by default, never PUT.

Network writes exist only behind the explicit apply flag and approved digest.
CVAT does not provide a verified atomic frame-CAS contract here: a concurrent
editor between the final GET and PATCH remains a race, detected when possible
by the post-write read. Use one remote writer; this client never retries writes.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import http.cookiejar
import os
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

# A process is bound to exactly one explicit scope and one label per plan.
# Use separate processes for independent jobs; configuration has no secret fields.
SCOPE = None
BASE = TASK = JOB = LABEL = FRAME_COUNT = ROOT = ANNOTATIONS = None
LABEL_NAME = None


def configure(scope, label_name):
    global SCOPE, BASE, TASK, JOB, LABEL, FRAME_COUNT, ROOT, ANNOTATIONS, LABEL_NAME
    if label_name not in scope.labels:
        raise ValueError("LABEL_NOT_IN_CONFIGURED_MAP")
    SCOPE = scope
    BASE, TASK, JOB = scope.base_url, scope.task_id, scope.job_id
    LABEL, LABEL_NAME = scope.labels[label_name], label_name
    FRAME_COUNT, ROOT = scope.frame_count, scope.work_dir
    ANNOTATIONS = f"/api/jobs/{JOB}/annotations"


WRITABLE = ("type", "frame", "label_id", "group", "source", "score", "occluded", "outside", "z_order", "rotation", "points", "attributes", "elements")


class GuardError(Exception):
    pass


def require(condition, code):
    if not condition:
        raise GuardError(code)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save_json(path, value):
    with Path(path).open("xb") as output:
        output.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"))


def timestamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def validate_annotations(annotations):
    require(isinstance(annotations, dict), "ANNOTATIONS_OBJECT_REQUIRED")
    require(isinstance(annotations.get("shapes"), list), "SHAPES_REQUIRED")
    require(not annotations.get("tracks"), "TRACKS_UNSUPPORTED_FOR_THIS_MASK_WORKFLOW")
    ids = []
    for shape in annotations["shapes"]:
        require(type(shape.get("id")) is int and shape["id"] > 0, "SAVED_SHAPE_ID_REQUIRED")
        require(type(shape.get("frame")) is int and 0 <= shape["frame"] < FRAME_COUNT, "INVALID_SAVED_FRAME")
        ids.append(shape["id"])
    require(len(ids) == len(set(ids)), "DUPLICATE_SAVED_SHAPE_ID")


def frame_hash(annotations, frame):
    validate_annotations(annotations)
    # Hash every saved field, including IDs, geometry, attributes and source.
    shapes = sorted((s for s in annotations["shapes"] if s["frame"] == frame), key=lambda s: s["id"])
    tags = sorted((t for t in annotations.get("tags", []) if t.get("frame") == frame), key=lambda t: canonical(t))
    return sha(canonical({"shapes": shapes, "tags": tags}))


def validate_mask(points, width, height):
    require(isinstance(points, list) and len(points) >= 6, "MASK_POINTS_REQUIRED")
    require(all(type(x) in (int, float) and math.isfinite(x) and int(x) == x for x in points), "MASK_INTEGER_VALUES_REQUIRED")
    values = list(map(int, points))
    runs, (x1, y1, x2, y2) = values[:-4], values[-4:]
    require(0 <= x1 <= x2 < width and 0 <= y1 <= y2 < height, "MASK_BBOX_OUT_OF_BOUNDS")
    require(all(run >= 0 for run in runs), "MASK_NEGATIVE_RUN")
    require(sum(runs) == (x2 - x1 + 1) * (y2 - y1 + 1), "MASK_RLE_AREA_MISMATCH")
    require(sum(runs[1::2]) > 0, "EMPTY_TARGET_MASK")
    return values


def load_baseline(directory):
    directory = Path(directory).resolve()
    manifest = read_json(directory / "manifest.json")
    require(manifest.get("base_url") == BASE, "BASELINE_ORIGIN_MISMATCH")
    require(manifest.get("task_id") == TASK and manifest.get("job_id") == JOB, "BASELINE_SCOPE_MISMATCH")
    require(manifest.get("annotation_canonical_identical") is True, "BASELINE_NOT_STABLE")
    for filename in ("annotations_after.json", "job_data_meta.json", "job.json", "task.json", "labels_page_1.json"):
        require(sha((directory / filename).read_bytes()) == manifest["files"][filename]["sha256"], "BASELINE_FILE_HASH_MISMATCH")
    job, task = read_json(directory / "job.json"), read_json(directory / "task.json")
    require(job.get("id") == JOB and job.get("task_id") == TASK and task.get("id") == TASK, "BASELINE_IDENTITY_MISMATCH")
    require(job.get("start_frame") == 0 and job.get("stop_frame") == FRAME_COUNT - 1, "JOB_FRAME_CONTRACT_MISMATCH")
    meta = read_json(directory / "job_data_meta.json")
    require(meta.get("start_frame") == 0 and meta.get("stop_frame") == FRAME_COUNT - 1 and meta.get("frame_filter", "") == "", "FRAME_MAPPING_NOT_VALIDATED")
    require(len(meta.get("frames", [])) == FRAME_COUNT and not meta.get("deleted_frames"), "FRAME_POPULATION_MISMATCH")
    labels = read_json(directory / "labels_page_1.json").get("results", [])
    require(any(label.get("id") == LABEL and label.get("name") == LABEL_NAME for label in labels), "CONFIGURED_LABEL_MISMATCH")
    require(all(f.get("width") == SCOPE.width and f.get("height") == SCOPE.height for f in meta["frames"]), "CONFIGURED_IMAGE_DIMENSIONS_MISMATCH")
    annotations = read_json(directory / "annotations_after.json")
    validate_annotations(annotations)
    return {
        "annotations": annotations, "frames": meta["frames"],
        "annotations_sha256": sha((directory / "annotations_after.json").read_bytes()),
        "metadata_sha256": sha((directory / "job_data_meta.json").read_bytes()),
        "directory": str(directory),
    }


def candidate_digest(candidate):
    return sha(canonical({key: value for key, value in candidate.items() if key != "review"}))


def prepare_change(change, baseline):
    require(isinstance(change, dict), "CHANGE_OBJECT_REQUIRED")
    require(not (set(change) - {"frame", "action", "shape_id", "points", "occluded"}), "UNKNOWN_CHANGE_FIELD")
    frame, action = change.get("frame"), change.get("action")
    require(type(frame) is int and 0 <= frame < FRAME_COUNT, "INVALID_CANDIDATE_FRAME")
    require(frame not in SCOPE.protected_frames, "HUMAN_PROTECTED_FRAME")
    require(action in ("create", "update", "delete"), "PATCH_ACTION_REQUIRED")
    if action == "create":
        require("shape_id" not in change, "CREATE_ID_FORBIDDEN")
        shape = {"type": "mask", "frame": frame, "label_id": LABEL, "group": 0, "source": "auto", "occluded": False, "outside": False, "z_order": 0, "rotation": 0.0, "attributes": []}
    else:
        require(type(change.get("shape_id")) is int, "TARGET_SHAPE_ID_MISMATCH")
        old = [s for s in baseline["annotations"]["shapes"] if s["id"] == change["shape_id"]]
        require(len(old) == 1 and old[0].get("type") == "mask", "EXACT_EXISTING_MASK_REQUIRED")
        require(old[0]["frame"] == frame and old[0]["label_id"] == LABEL, "TARGET_SHAPE_SCOPE_MISMATCH")
        shape = {key: copy.deepcopy(old[0][key]) for key in WRITABLE if key in old[0]}
        shape["id"] = old[0]["id"]
    size = baseline["frames"][frame]
    if action == "delete":
        require("points" not in change, "DELETE_MUST_USE_COMPLETE_BASELINE_SHAPE")
        require("occluded" not in change, "DELETE_OCCLUDED_FIELD_FORBIDDEN")
        validate_mask(shape["points"], size["width"], size["height"])
    else:
        shape["points"] = validate_mask(change.get("points"), size["width"], size["height"])
        # New instances start as auto above. Local geometry corrections retain
        # the original source and metadata, including manual annotation origin.
        require(not shape.get("outside", False), "OUTSIDE_TARGET_UNSUPPORTED")
        if "occluded" in change:
            require(type(change["occluded"]) is bool, "OCCLUDED_MUST_BE_BOOLEAN")
            shape["occluded"] = change["occluded"]
    return {"frame": frame, "action": action, "shape": shape, "expected_frame_sha256": frame_hash(baseline["annotations"], frame)}


def mask_pixel_digest(points, width, height):
    """Exact pixels, independent of bbox padding or equivalent zero-length RLE."""
    values = validate_mask(points, width, height)
    x1, y1, x2, y2 = values[-4:]
    box_width = x2 - x1 + 1
    pixels = bytearray(width * height)
    position = 0
    for index, run in enumerate(values[:-4]):
        end = position + run
        if index % 2:
            while position < end:
                row, column = divmod(position, box_width)
                length = min(end - position, box_width - column)
                start = (y1 + row) * width + x1 + column
                pixels[start:start + length] = b"\x01" * length
                position += length
        position = end
    return sha(pixels)


def preflight_instances(prepared, baseline):
    """Simulate ordered changes; reject repeated IDs and exact-pixel copies."""
    simulated = copy.deepcopy(baseline["annotations"]["shapes"])
    seen = set()
    for index, item in enumerate(prepared):
        target, frame, action = item["shape"], item["frame"], item["action"]
        target_id = target.get("id")
        if target_id is not None:
            require(target_id not in seen, "REPEATED_TARGET_SHAPE_ID")
            seen.add(target_id)
            simulated = [s for s in simulated if s["id"] != target_id]
        if action == "delete":
            continue
        size = baseline["frames"][frame]
        target_digest = mask_pixel_digest(target["points"], size["width"], size["height"])
        for other in simulated:
            if other["frame"] == frame and other["label_id"] == LABEL and other["type"] == "mask":
                require(mask_pixel_digest(other["points"], size["width"], size["height"]) != target_digest,
                        "EXACT_PIXEL_DUPLICATE_MASK")
        replacement = copy.deepcopy(target)
        replacement.setdefault("id", -(index + 1))
        simulated.append(replacement)


def build_plan(candidate, baseline, current=None):
    require(candidate.get("schema") == "cvat_incremental_candidate_v1", "CANDIDATE_SCHEMA_MISMATCH")
    require(candidate.get("task_id") == TASK and candidate.get("job_id") == JOB and candidate.get("label_id") == LABEL, "CANDIDATE_SCOPE_MISMATCH")
    require(candidate.get("baseline_annotations_sha256") == baseline["annotations_sha256"], "CANDIDATE_BASELINE_MISMATCH")
    require(candidate.get("frame_metadata_sha256") == baseline["metadata_sha256"], "CANDIDATE_METADATA_MISMATCH")
    require(isinstance(candidate.get("changes"), list) and candidate["changes"], "NONEMPTY_CHANGES_REQUIRED")
    prepared = [prepare_change(change, baseline) for change in candidate["changes"]]
    preflight_instances(prepared, baseline)
    eligible, blocked = [], []
    for item in prepared:
        if current is not None and frame_hash(current, item["frame"]) != item["expected_frame_sha256"]:
            blocked.append({"frame": item["frame"], "reason": "FRAME_CHANGED_OR_HUMAN_ADDED_SHAPE"})
        else:
            eligible.append(item)
    return {"candidate_sha256": candidate_digest(candidate), "live_comparison": current is not None, "eligible": eligible, "blocked": blocked}


def ensure_review(candidate):
    review = candidate.get("review", {})
    require(review.get("status") == "approved" and bool(review.get("reviewer")), "APPROVED_REVIEW_REQUIRED")
    require(bool(review.get("author")) and review["author"] != review["reviewer"], "DISTINCT_AUTHOR_AND_REVIEWER_REQUIRED")
    require(review.get("candidate_sha256") == candidate_digest(candidate), "REVIEW_DIGEST_MISMATCH")


def patch_body(item, current):
    return {"version": current.get("version", 0), "tags": [], "shapes": [item["shape"]], "tracks": []}


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise GuardError("REDIRECT_REJECTED")


class Client:
    """Exact-route transport; token/password stay only in process memory.

    An interactor deployment must be independently verified to return a mask
    without writing annotations. Async lambda requests are never allowed.
    """
    def __init__(self, *, allow_apply=False, allow_inference=False):
        require(SCOPE is not None, "EXPLICIT_CONFIG_REQUIRED")
        require(not (allow_apply and allow_inference), "INFERENCE_AND_WRITE_SESSIONS_MUST_BE_SEPARATE")
        self.allow_apply, self.allow_inference = allow_apply, allow_inference
        self.bound_scope = SCOPE
        self.opener = request.build_opener(NoRedirect(), request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.key = os.environ.get("CVAT_TOKEN")
        if not self.key:
            username, password = os.environ.get("CVAT_USERNAME"), os.environ.get("CVAT_PASSWORD")
            require(bool(username) and bool(password), "SET_CVAT_TOKEN_OR_USERNAME_AND_PASSWORD")
            body = canonical({"username": username, "password": password})
            req = request.Request(BASE + "/api/auth/login", data=body, method="POST", headers={"Content-Type":"application/json", "Accept":"application/vnd.cvat+json"})
            del username, password
            with self.opener.open(req, timeout=20) as response:
                login = json.load(response)
            self.key = login.get("key")
            del login, body, req
        require(isinstance(self.key,str) and bool(self.key), "AUTH_KEY_MISSING")

    def annotations(self):
        return self._request("GET", ANNOTATIONS)

    def frame(self, frame):
        SCOPE.frame(frame)
        return self._request("GET", f"/api/jobs/{JOB}/data?type=frame&number={frame}&quality=original", raw=True)

    def metadata(self):
        job = self._request("GET", f"/api/jobs/{JOB}")
        task = self._request("GET", f"/api/tasks/{TASK}")
        meta = self._request("GET", f"/api/jobs/{JOB}/data/meta")
        labels = self._request("GET", f"/api/labels?task_id={TASK}&page_size=10000")
        require(job.get("id") == JOB and job.get("task_id") == TASK and task.get("id") == TASK, "REMOTE_TASK_JOB_IDENTITY_MISMATCH")
        # This edition supports image jobs spanning zero-based logical frames.
        # Fail closed for nonzero job starts or filtered/deleted frame mappings.
        require(job.get("start_frame") == 0 and job.get("stop_frame") == FRAME_COUNT - 1, "JOB_FRAME_CONTRACT_MISMATCH")
        require(meta.get("start_frame") == 0 and meta.get("stop_frame") == FRAME_COUNT - 1 and not meta.get("frame_filter") and not meta.get("deleted_frames"), "FRAME_MAPPING_NOT_VALIDATED")
        require(len(meta.get("frames",[])) == FRAME_COUNT and all(f.get("width") == SCOPE.width and f.get("height") == SCOPE.height for f in meta["frames"]), "FRAME_DIMENSIONS_OR_COUNT_MISMATCH")
        require(any(x.get("id") == LABEL and x.get("name") == LABEL_NAME for x in labels.get("results",[])), "REMOTE_LABEL_SCOPE_MISMATCH")
        return {"job.json":job,"task.json":task,"job_data_meta.json":meta,"labels_page_1.json":labels}

    def infer(self, prompt):
        from .inference import validate_prompts
        validate_prompts([prompt])
        require(self.allow_inference, "INFERENCE_NOT_ENABLED")
        body = {"task":TASK,"job":JOB,"frame":prompt["frame"],"pos_points":prompt["pos_points"],"neg_points":prompt["neg_points"]}
        return body, self._request("POST", "/api/lambda/functions/" + SCOPE.function_id, body)

    def check_interactor(self):
        require(self.allow_inference, "INFERENCE_NOT_ENABLED")
        models = self._request("GET", "/api/lambda/functions")
        if isinstance(models,dict):
            models = models.get("results",[])
        require(isinstance(models,list), "MODEL_LIST_CONTRACT_MISMATCH")
        matches = [m for m in models if m.get("id") == SCOPE.function_id]
        require(len(matches) == 1 and matches[0].get("kind") == "interactor", "CONFIGURED_FUNCTION_IS_NOT_AN_INTERACTOR")

    def patch(self, action, body):
        require(self.allow_apply and action in ("create","update","delete"), "WRITE_NOT_ENABLED")
        require(body.get("tags") == [] and body.get("tracks") == [] and len(body.get("shapes",[])) == 1, "SINGLE_SHAPE_PATCH_REQUIRED")
        shape = body["shapes"][0]
        require(shape.get("type") == "mask" and shape.get("label_id") == LABEL and type(shape.get("frame")) is int and 0 <= shape["frame"] < FRAME_COUNT, "PATCH_SHAPE_SCOPE_MISMATCH")
        require(shape["frame"] not in SCOPE.protected_frames, "HUMAN_PROTECTED_FRAME")
        validate_mask(shape.get("points"), SCOPE.width, SCOPE.height)
        require((action == "create" and "id" not in shape) or (action != "create" and type(shape.get("id")) is int and shape["id"] > 0), "PATCH_ID_CONTRACT_MISMATCH")
        return self._request("PATCH", ANNOTATIONS + "?action=" + action, body)

    def _request(self, method, path, body=None, *, raw=False):
        reads = {ANNOTATIONS,f"/api/jobs/{JOB}",f"/api/tasks/{TASK}",f"/api/jobs/{JOB}/data/meta",f"/api/labels?task_id={TASK}&page_size=10000","/api/lambda/functions"}
        allowed = method == "GET" and path in reads
        allowed |= method == "GET" and path in {f"/api/jobs/{JOB}/data?type=frame&number={frame}&quality=original" for frame in range(FRAME_COUNT)}
        allowed |= getattr(self,"allow_apply",False) and method == "PATCH" and path in [ANNOTATIONS + "?action=" + action for action in ("create","update","delete")]
        allowed |= getattr(self,"allow_inference",False) and method == "POST" and path == "/api/lambda/functions/" + SCOPE.function_id
        require(allowed, "NETWORK_OPERATION_NOT_WHITELISTED")
        require(getattr(self,"bound_scope",None) is SCOPE, "CLIENT_SCOPE_CHANGED_AFTER_AUTHENTICATION")
        req = request.Request(BASE + path, data=canonical(body) if body is not None else None, method=method, headers={"Authorization":"Token " + self.key,"Content-Type":"application/json","Accept":"application/vnd.cvat+json"})
        with self.opener.open(req, timeout=60) as response:
            return response.read() if raw else json.load(response)


def full_snapshot(client, output):
    before = client.annotations()
    validate_annotations(before)
    files = client.metadata()
    after = client.annotations()
    validate_annotations(after)
    files.update({"annotations_before.json":before,"annotations_after.json":after})
    for name, value in files.items():
        save_json(output / name, value)
    manifest = {"base_url":BASE,"task_id":TASK,"job_id":JOB,"annotation_canonical_identical":canonical(before) == canonical(after),"files":{name:{"sha256":sha((output/name).read_bytes())} for name in files}}
    save_json(output / "manifest.json", manifest)
    require(manifest["annotation_canonical_identical"], "REMOTE_CHANGED_DURING_SNAPSHOT")
    return manifest

def fresh_snapshot(client, output):
    first = client.annotations()
    validate_annotations(first)
    save_json(output / "annotations_before.json", first)
    second = client.annotations()
    validate_annotations(second)
    save_json(output / "annotations_after.json", second)
    stable = canonical(first) == canonical(second)
    save_json(output / "snapshot.json", {"task_id": TASK, "job_id": JOB, "stable": stable, "before_sha256": sha(canonical(first)), "after_sha256": sha(canonical(second)), "frame_sha256": {str(f): frame_hash(second, f) for f in range(FRAME_COUNT)}})
    require(stable, "REMOTE_CHANGED_DURING_SNAPSHOT")
    return second


def verify_after(before, after, item):
    validate_annotations(after)
    touched_id = item["shape"].get("id")
    old_other = sorted([s for s in before["shapes"] if s["id"] != touched_id], key=lambda s: s["id"])
    old_ids = {s["id"] for s in before["shapes"]}
    if item["action"] == "create":
        additions = [s for s in after["shapes"] if s["id"] not in old_ids]
        require(len(additions) == 1, "CREATE_RESULT_AMBIGUOUS")
        touched_id = additions[0]["id"]
    after_other = sorted([s for s in after["shapes"] if s["id"] != touched_id], key=lambda s: s["id"])
    require(canonical(old_other) == canonical(after_other), "UNRELATED_SHAPE_CHANGED_AFTER_PATCH")
    require(canonical(before.get("tags", [])) == canonical(after.get("tags", [])), "TAGS_CHANGED_AFTER_PATCH")
    if item["action"] == "delete":
        require(all(s["id"] != touched_id for s in after["shapes"]), "DELETE_NOT_CONFIRMED")
    else:
        targets = [s for s in after["shapes"] if s["id"] == touched_id]
        require(len(targets) == 1 and targets[0]["frame"] == item["frame"] and targets[0]["label_id"] == LABEL, "EXACT_TARGET_RESULT_REQUIRED")
        actual = targets[0]
        for key, expected in item["shape"].items():
            require(actual.get(key) == expected, "PATCH_RESULT_FIELD_MISMATCH:" + key)


def apply_candidate(client, candidate, baseline, output):
    ensure_review(candidate)
    initial = fresh_snapshot(client, output)
    plan = build_plan(candidate, baseline, initial)
    save_json(output / "plan.json", plan)
    results = list(plan["blocked"])
    expected = {item["frame"]: item["expected_frame_sha256"] for item in plan["eligible"]}
    blocked_frames = set()
    for index, item in enumerate(plan["eligible"]):
        frame = item["frame"]
        if frame in blocked_frames:
            results.append({"frame": frame, "action": item["action"], "shape_id": item["shape"].get("id"), "status": "SKIPPED_FRAME_CHANGED"})
            continue
        latest = client.annotations()
        if frame_hash(latest, frame) != expected[frame]:
            blocked_frames.add(frame)
            results.append({"frame": frame, "action": item["action"], "shape_id": item["shape"].get("id"), "status": "SKIPPED_FRAME_CHANGED"})
            continue
        step = output / f"step_{index:03d}_frame_{item['frame']:03d}"
        step.mkdir()
        save_json(step / "before.json", latest)
        body = patch_body(item, latest)
        save_json(step / "intent.json", {"action": item["action"], "body": body, "frame_sha256": expected[frame], "candidate_sha256": plan["candidate_sha256"]})
        # One request only. Any error may have happened after commit; stop and
        # leave the intent/backup for reconciliation, never auto-retry or undo.
        try:
            response = client.patch(item["action"], body)
            save_json(step / "response.json", response)
            after = client.annotations()
            save_json(step / "after.json", after)
            verify_after(latest, after, item)
        except Exception:
            save_json(step / "outcome.json", {"status": "STOPPED_OUTCOME_REQUIRES_READONLY_RECONCILIATION", "no_retry": True})
            raise GuardError("PATCH_OUTCOME_REQUIRES_READONLY_RECONCILIATION") from None
        # Only our own fully verified mutation may advance this frame's guard.
        expected[frame] = frame_hash(after, frame)
        results.append({"frame": frame, "action": item["action"], "shape_id": item["shape"].get("id"), "status": "READBACK_VERIFIED"})
    save_json(output / "results.json", results)
    return results

