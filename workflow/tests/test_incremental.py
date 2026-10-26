"""Offline adversarial tests: no authentication and no network calls."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import safe_incremental as tool
from workflow.config import Scope


def shape(frame=0, ident=10):
    return {"id": ident, "type": "mask", "frame": frame, "label_id": 12,
            "points": [0, 4, 0, 0, 1, 1], "source": "manual", "occluded": False,
            "outside": False, "z_order": 0, "rotation": 0, "group": None,
            "attributes": [], "elements": [], "score": 1.0}


def baseline():
    return {"annotations": {"version": 0, "shapes": [shape()], "tags": [], "tracks": []},
            "frames": [{"width": 4, "height": 3, "name": f"image-{f}"} for f in range(150)],
            "annotations_sha256": "a" * 64, "metadata_sha256": "b" * 64}


def candidate(changes):
    value = {"schema": "cvat_incremental_candidate_v1", "task_id": 101, "job_id": 7,
             "label_id": 12, "baseline_annotations_sha256": "a" * 64,
             "frame_metadata_sha256": "b" * 64, "changes": changes}
    value["review"] = {"status": "approved", "reviewer": "offline-reviewer", "author": "offline-author", "candidate_sha256": tool.candidate_digest(value)}
    return value


class FakeClient:
    def __init__(self, data, *, lost_response=False, mutate_before_third_read=False):
        self.data = copy.deepcopy(data)
        self.calls = []
        self.reads = 0
        self.lost_response = lost_response
        self.mutate = mutate_before_third_read

    def annotations(self):
        self.reads += 1
        if self.mutate and self.reads == 3:
            self.data["shapes"].append(shape(0, 999))
        self.calls.append(("GET", None))
        return copy.deepcopy(self.data)

    def patch(self, action, body):
        self.calls.append(("PATCH", action))
        target = copy.deepcopy(body["shapes"][0])
        if action == "create":
            target["id"] = max([s["id"] for s in self.data["shapes"]] + [999]) + 1
            self.data["shapes"].append(target)
        elif action == "update":
            self.data["shapes"] = [target if s["id"] == target["id"] else s for s in self.data["shapes"]]
        elif action == "delete":
            self.data["shapes"] = [s for s in self.data["shapes"] if s["id"] != target["id"]]
        if self.lost_response:
            raise TimeoutError("simulated committed request with lost response")
        return copy.deepcopy(self.data)


class SafetyTests(unittest.TestCase):
    def setUp(self):
        scope=Scope.from_dict({"base_url":"https://cvat.example.invalid","task_id":101,"job_id":7,"labels":{"pallet":12},"frame_count":150,"width":4,"height":3,"function_id":"sam2","protected_frames":[],"work_dir":"work","images_dir":"frames","frame_pattern":"frame_{frame:06d}.jpg","sequence":{"mode":"explicit","runs":[list(range(150))]}})
        tool.configure(scope,"pallet")

    def test_reviewed_occlusion_is_preserved_and_strictly_typed(self):
        base = baseline()
        change = {"frame": 1, "action": "create", "points": [0, 4, 0, 0, 1, 1], "occluded": True}
        self.assertIs(tool.prepare_change(change, base)["shape"]["occluded"], True)
        base["annotations"]["shapes"][0]["occluded"] = True
        update = {"frame": 0, "action": "update", "shape_id": 10, "points": [0, 4, 0, 0, 1, 1]}
        self.assertIs(tool.prepare_change(update, base)["shape"]["occluded"], True)
        for bad in (1, "true", None):
            with self.subTest(value=bad), self.assertRaises(tool.GuardError):
                tool.prepare_change({**change, "occluded": bad}, base)
        with self.assertRaises(tool.GuardError):
            tool.prepare_change({"frame": 0, "action": "delete", "shape_id": 10, "occluded": False}, base)


    def test_mask_accepts_inclusive_bbox_and_rejects_malformed_data(self):
        self.assertEqual(tool.validate_mask([0., 4., 0., 0., 1., 1.], 4, 3), [0, 4, 0, 0, 1, 1])
        for points in ([0, 3, 0, 0, 1, 1], [0, 4, 0, 0, 4, 1], [0, -4, 0, 0, 1, 1], [4, 0, 0, 0, 1, 1], [False, 4, 0, 0, 1, 1], [0, float("nan"), 0, 0, 1, 1]):
            with self.subTest(points=points), self.assertRaises(tool.GuardError):
                tool.validate_mask(points, 4, 3)

    def test_manual_geometry_update_preserves_id_source_and_all_metadata(self):
        base = baseline()
        original = base["annotations"]["shapes"][0]
        original.update(group=42, occluded=True, z_order=5, score=0.7,
                        attributes=[{"spec_id": 8, "value": "human-reviewed"}])
        points = [0, 1, 1, 1, 1, 1]
        proposal = candidate([{"frame": 0, "action": "update", "shape_id": 10, "points": points}])
        client = FakeClient(base["annotations"])
        with tempfile.TemporaryDirectory() as folder:
            result = tool.apply_candidate(client, proposal, base, Path(folder))
        self.assertEqual(result[0]["status"], "READBACK_VERIFIED")
        self.assertEqual(client.data["shapes"][0], {**original, "points": points})


    def test_scope_and_candidate_binding_rejected(self):
        for field, value in (("task_id", 102), ("job_id", 8), ("label_id", 202), ("baseline_annotations_sha256", "wrong"), ("frame_metadata_sha256", "wrong")):
            proposal = candidate([{"frame": 1, "action": "create", "points": [0, 4, 0, 0, 1, 1]}])
            proposal[field] = value
            with self.subTest(field=field), self.assertRaises(tool.GuardError):
                tool.build_plan(proposal, baseline())

    def test_human_added_or_changed_same_frame_blocks_only_that_frame(self):
        base = baseline()
        proposal = candidate([{"frame": 0, "action": "update", "shape_id": 10, "points": [0, 1, 1, 1, 1, 1]}, {"frame": 1, "action": "create", "points": [0, 4, 0, 0, 1, 1]}])
        for change in ("new", "points", "attributes", "source"):
            current = copy.deepcopy(base["annotations"])
            if change == "new": current["shapes"].append(shape(0, 11))
            elif change == "points": current["shapes"][0]["points"] = [0, 1, 1, 1, 1, 1]
            elif change == "attributes": current["shapes"][0]["attributes"] = [{"spec_id": 1, "value": "new"}]
            else: current["shapes"][0]["source"] = "semi-auto"
            plan = tool.build_plan(proposal, base, current)
            self.assertEqual([p["frame"] for p in plan["eligible"]], [1])
            self.assertEqual([p["frame"] for p in plan["blocked"]], [0])

    def test_unrelated_frame_change_does_not_block_planning(self):
        current = copy.deepcopy(baseline()["annotations"])
        current["shapes"].append(shape(9, 99))
        proposal = candidate([{"frame": 1, "action": "create", "points": [0, 4, 0, 0, 1, 1]}])
        self.assertEqual(len(tool.build_plan(proposal, baseline(), current)["eligible"]), 1)

    def test_duplicate_target_and_wrong_id_rejected(self):
        tests = [
            [{"frame": 0, "action": "create", "points": [0, 4, 0, 0, 1, 1]}],
            [{"frame": 0, "action": "update", "shape_id": 11, "points": [0, 4, 0, 0, 1, 1]}],
            [{"frame": 1, "action": "create", "points": [0, 4, 0, 0, 1, 1]}] * 2,
        ]
        for changes in tests:
            with self.subTest(changes=changes), self.assertRaises(tool.GuardError):
                tool.build_plan(candidate(changes), baseline())

    def test_delete_has_full_geometry_and_preserves_metadata(self):
        item = tool.prepare_change({"frame": 0, "action": "delete", "shape_id": 10}, baseline())
        body = tool.patch_body(item, baseline()["annotations"])
        self.assertEqual(body["shapes"][0], shape())
        self.assertEqual(body["tags"], [])
        self.assertEqual(body["tracks"], [])

    def test_multi_create_with_existing_pallet_and_repeated_create(self):
        changes = [{"frame": 0, "action": "create", "points": [0, 1, 2, 0, 2, 0]},
                   {"frame": 0, "action": "create", "points": [0, 1, 3, 0, 3, 0]}]
        base = baseline()
        client = FakeClient(base["annotations"])
        with tempfile.TemporaryDirectory() as folder:
            results = tool.apply_candidate(client, candidate(changes), base, Path(folder))
        self.assertEqual([x["status"] for x in results], ["READBACK_VERIFIED"] * 2)
        self.assertEqual(len(client.data["shapes"]), 3)
        self.assertEqual(client.data["shapes"][0], shape())

    def test_exact_id_update_and_delete_preserve_other_same_frame_instances(self):
        base = baseline()
        second = shape(0, 11)
        second["points"] = [0, 1, 3, 0, 3, 0]
        base["annotations"]["shapes"].append(second)
        changes = [{"frame": 0, "action": "update", "shape_id": 10, "points": [0, 1, 1, 1, 1, 1]},
                   {"frame": 0, "action": "create", "points": [0, 1, 2, 2, 2, 2]},
                   {"frame": 0, "action": "delete", "shape_id": 11}]
        client = FakeClient(base["annotations"])
        with tempfile.TemporaryDirectory() as folder:
            results = tool.apply_candidate(client, candidate(changes), base, Path(folder))
        self.assertEqual([x["status"] for x in results], ["READBACK_VERIFIED"] * 3)
        self.assertEqual(len(client.data["shapes"]), 2)
        self.assertIn(10, [s["id"] for s in client.data["shapes"]])
        self.assertNotIn(11, [s["id"] for s in client.data["shapes"]])

    def test_exact_pixel_duplicate_even_with_different_padded_bbox(self):
        tight = [0, 4, 0, 0, 1, 1]
        padded = [0, 2, 1, 2, 1, 0, 0, 2, 1]
        self.assertEqual(tool.mask_pixel_digest(tight, 4, 3), tool.mask_pixel_digest(padded, 4, 3))
        with self.assertRaisesRegex(tool.GuardError, "EXACT_PIXEL_DUPLICATE"):
            tool.build_plan(candidate([{"frame": 0, "action": "create", "points": padded}]), baseline())

    def test_repeated_same_shape_update_is_rejected(self):
        change = {"frame": 0, "action": "update", "shape_id": 10, "points": [0, 1, 1, 1, 1, 1]}
        with self.assertRaisesRegex(tool.GuardError, "REPEATED_TARGET_SHAPE_ID"):
            tool.build_plan(candidate([change, change]), baseline())

    def test_external_change_between_same_frame_operations_stops_remaining_frame(self):
        class Changed(FakeClient):
            def annotations(self):
                if self.reads == 4:
                    extra = shape(0, 9999)
                    extra["points"] = [0, 1, 3, 2, 3, 2]
                    self.data["shapes"].append(extra)
                return super().annotations()
        changes = [{"frame": 0, "action": "create", "points": [0, 1, 2, 0, 2, 0]},
                   {"frame": 0, "action": "create", "points": [0, 1, 3, 0, 3, 0]},
                   {"frame": 0, "action": "delete", "shape_id": 10}]
        client = Changed(baseline()["annotations"])
        with tempfile.TemporaryDirectory() as folder:
            results = tool.apply_candidate(client, candidate(changes), baseline(), Path(folder))
        self.assertEqual([x["status"] for x in results], ["READBACK_VERIFIED", "SKIPPED_FRAME_CHANGED", "SKIPPED_FRAME_CHANGED"])
        self.assertEqual(sum(method == "PATCH" for method, _ in client.calls), 1)

    def test_pending_or_stale_review_rejected_before_network(self):
        proposal = candidate([{"frame": 0, "action": "delete", "shape_id": 10}])
        proposal["review"]["status"] = "pending"
        with self.assertRaises(tool.GuardError): tool.ensure_review(proposal)
        proposal["review"]["status"] = "approved"
        proposal["changes"][0]["frame"] = 1
        with self.assertRaises(tool.GuardError): tool.ensure_review(proposal)

    def test_patch_actions_roundtrip_fake_transport_without_put(self):
        for action in ("create", "update", "delete"):
            change = {"frame": 1 if action == "create" else 0, "action": action}
            if action != "create": change["shape_id"] = 10
            if action != "delete": change["points"] = [0, 1, 1, 1, 1, 1]
            client = FakeClient(baseline()["annotations"])
            with tempfile.TemporaryDirectory() as folder:
                results = tool.apply_candidate(client, candidate([change]), baseline(), Path(folder))
                self.assertEqual(results[0]["status"], "READBACK_VERIFIED")
                self.assertTrue((Path(folder) / "annotations_before.json").exists())
            self.assertEqual([call for call in client.calls if call[0] != "GET"], [("PATCH", action)])

    def test_last_moment_human_addition_skips_without_patch(self):
        client = FakeClient(baseline()["annotations"], mutate_before_third_read=True)
        with tempfile.TemporaryDirectory() as folder:
            results = tool.apply_candidate(client, candidate([{"frame": 0, "action": "delete", "shape_id": 10}]), baseline(), Path(folder))
        self.assertEqual(results[0]["status"], "SKIPPED_FRAME_CHANGED")
        self.assertFalse(any(method == "PATCH" for method, _ in client.calls))

    def test_lost_response_never_retries_or_rolls_back(self):
        client = FakeClient(baseline()["annotations"], lost_response=True)
        proposal = candidate([{"frame": 0, "action": "delete", "shape_id": 10}])
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(tool.GuardError, "RECONCILIATION"):
            tool.apply_candidate(client, proposal, baseline(), Path(folder))
        self.assertEqual(sum(method == "PATCH" for method, _ in client.calls), 1)

    def test_readback_detects_unrelated_changes(self):
        before = baseline()["annotations"]
        item = tool.prepare_change({"frame": 0, "action": "delete", "shape_id": 10}, baseline())
        after = {"version": 0, "shapes": [shape(4, 50)], "tags": [], "tracks": []}
        with self.assertRaisesRegex(tool.GuardError, "UNRELATED"):
            tool.verify_after(before, after, item)

    def test_network_whitelist_never_allows_full_put_or_delete(self):
        client = object.__new__(tool.Client)
        client.allow_apply = True
        for method, route in (("PUT", tool.ANNOTATIONS), ("DELETE", tool.ANNOTATIONS), ("PATCH", "/api/jobs/8/annotations?action=update")):
            with self.subTest(method=method), self.assertRaises(tool.GuardError):
                client._request(method, route, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
