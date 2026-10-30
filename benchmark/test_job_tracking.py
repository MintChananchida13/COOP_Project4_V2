import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import Mock, patch

import user_flow


class JobTrackingTests(unittest.TestCase):
    def test_layout_calls_from_result_preparation_are_merged_without_duplicates(self):
        snapshot = self.completed()
        snapshot["tables"][0].extend([
            {"metric": "layout_inference_ms", "valueMs": 12},
            {"metric": "layout_model_calls", "value": [{"call_id": "layout-flex", "kind": "layout", "inference_ms": 12},
                                                        {"call_id": "layout-flex", "kind": "layout", "inference_ms": 12}]},
        ])
        row, _ = self.run_snapshots([snapshot])
        self.assertEqual(row["layout_inference_ms"], 12)
        self.assertEqual([call["call_id"] for call in row["_model_calls"]].count("layout-flex"), 1)

    def run_snapshots(self, snapshots, timeout=1000):
        page = Mock()
        state = {"index": 0}

        def evaluate(script, token=None):
            if token:
                state["token"] = token
            if script == user_flow.RUN_SNAPSHOT:
                snapshot = snapshots[min(state["index"], len(snapshots) - 1)]
                state["index"] += 1
                if snapshot is None:
                    return None
                snapshot = dict(snapshot)
                if isinstance(snapshot.get("job"), dict):
                    snapshot["job"] = {**snapshot["job"], "benchmark_timing": {"trace_id": state["token"]}}
                snapshot["tables"] = [list(table) for table in snapshot.get("tables", [])]
                if snapshot["tables"] and not any(item.get("metric") == "benchmark_run_token" for table in snapshot["tables"] for item in table):
                    snapshot["tables"][0].append({"metric": "benchmark_run_token", "value": state["token"]})
                return {"token": state["token"], "pending": 0, "posts": 1, "errors": [], **snapshot}

        page.evaluate.side_effect = evaluate
        clock = iter(range(10000))
        with patch.object(user_flow.time, "monotonic", side_effect=lambda: next(clock) / 100):
            row = user_flow.run_case(page, Path("a.pdf"), "standard", timeout)
        page.on.assert_not_called()
        return row, page

    def completed(self, matched=True):
        return {"job_id": "current-job", "job": {"status": "completed", "result": {
            "matched": matched, "query_id": "query-current", "debug": {"verification_strategy": "standard",
            "timing": {"backend_runtime_ms": 23, "model_call_count": 1, "model_intervals_overlap": False,
                       "model_wait_union_ms": 10, "model_wall_clock_ms": 10,
                       "image_siglip_runtime_ms": 5, "image_siglip_wait_ms": 10,
                       "model_calls": [{"call_id": "call-current", "kind": "image_verification", "inference_ms": 5}]}}}},
            "tables": [[{"metric": "end_to_end_ms", "valueMs": 120},
                        {"metric": "frontend_visibility_status", "value": "READY"},
                        {"metric": "frontend_timing_kind", "value": "paint-ready estimate; not actual paint"},
                        {"metric": "frontend_render_ms", "valueMs": 35},
                        {"metric": "frontend_result_visible_ms", "valueMs": 75},
                        {"metric": "frontend_preview_ready_ms", "valueMs": 60},
                        {"metric": "frontend_commit_end_to_end_ms", "valueMs": 85},
                        {"stage": "uploadJobMs", "elapsedMs": 4}, {"stage": "jobPollingMs", "elapsedMs": 100},
                        {"stage": "detectionJobMs", "elapsedMs": 110}]]}

    def test_completed_no_match_is_pass_with_all_timing_and_ids(self):
        row, _ = self.run_snapshots([self.completed(False)])
        self.assertEqual(row["status"], "PASS")
        self.assertIs(row["matched"], False)
        self.assertEqual(row["job_id"], "current-job")
        self.assertEqual(row["query_id"], "query-current")
        for key, value in (("end_to_end_ms", 120), ("backend_runtime_ms", 23), ("image_siglip_runtime_ms", 5),
                           ("image_siglip_wait_ms", 10), ("model_call_count", 1), ("uploadJobMs", 4),
                           ("jobPollingMs", 100), ("detectionJobMs", 110), ("model_wait_union_ms", 10)):
            self.assertEqual(row[key], value, key)

    def test_waits_for_body_drain_and_frontend_completion(self):
        finished = self.completed()
        row, page = self.run_snapshots([{**finished, "pending": 1}, {**finished, "tables": []}, finished])
        self.assertEqual(row["status"], "PASS")
        self.assertEqual(page.wait_for_timeout.call_count, 2)
        self.assertTrue(row["_ready_for_next"])

    def test_prior_capture_error_does_not_override_valid_terminal_poll(self):
        row, _ = self.run_snapshots([{**self.completed(), "errors": ["body evicted"]}])
        self.assertEqual(row["status"], "PASS")

    def test_unreadable_body_is_capture_error_not_detection_failure(self):
        row, _ = self.run_snapshots([{"errors": ["Response body navigated away / evicted"]}])
        self.assertEqual(row["status"], "FAIL")
        self.assertEqual(row["error_type"], "RESPONSE_CAPTURE_ERROR")
        self.assertFalse(row["_ready_for_next"])

    def test_navigation_losing_capture_is_not_pass(self):
        row, _ = self.run_snapshots([None])
        self.assertEqual(row["error_type"], "RESPONSE_CAPTURE_ERROR")

    def test_terminal_failure_is_detection_failed(self):
        row, _ = self.run_snapshots([{**self.completed(), "job": {"status": "failed", "error": "backend failed"}}])
        self.assertEqual(row["error_type"], "DETECTION_FAILED")
        self.assertTrue(row["_ready_for_next"])

    def test_processing_timeout_stops_navigation(self):
        row, _ = self.run_snapshots([{"job_id": "current-job", "job": {"status": "processing"}}], timeout=20)
        self.assertEqual(row["error_type"], "POLLING_TIMEOUT")
        self.assertFalse(row["_ready_for_next"])

    def test_completed_with_pending_body_timeout_is_benchmark_error(self):
        row, _ = self.run_snapshots([{**self.completed(), "pending": 1}], timeout=20)
        self.assertEqual(row["error_type"], "BENCHMARK_ERROR")
        self.assertEqual(row["matched"], True)
        self.assertFalse(row["_ready_for_next"])

    def test_visibility_errors_are_not_detection_failures(self):
        for status in ("PREVIEW_ERROR", "VISIBILITY_TIMEOUT", "VISIBILITY_ERROR"):
            state = self.completed()
            state["tables"][0] = [item for item in state["tables"][0] if item.get("metric") not in ("end_to_end_ms", "frontend_visibility_status")]
            state["tables"][0].extend([{"metric": "frontend_visibility_status", "value": status},
                                       {"metric": "frontend_visibility_reason", "value": "preview did not load"},
                                       {"metric": "end_to_end_ms", "valueMs": None, "reason": "preview did not load"}])
            row, _ = self.run_snapshots([state])
            self.assertEqual(row["status"], "FAIL")
            self.assertEqual(row["error_type"], status)
            self.assertEqual(row["job_status"], "completed")
            self.assertTrue(row["matched"])
            self.assertIsNone(row["end_to_end_ms"])
            self.assertEqual(row["timing_missing_reasons"]["end_to_end_ms"], "preview did not load")

    def test_visibility_from_another_benchmark_token_is_rejected(self):
        state = self.completed()
        state["tables"][0].append({"metric": "benchmark_run_token", "value": "previous-run"})
        row, _ = self.run_snapshots([state])
        self.assertEqual(row["error_type"], "RESPONSE_CAPTURE_ERROR")
        self.assertFalse(row["_ready_for_next"])

    def test_legacy_commit_marker_without_readiness_cannot_pass(self):
        state = self.completed()
        state["tables"][0] = [item for item in state["tables"][0] if item.get("metric") != "frontend_visibility_status"]
        row, _ = self.run_snapshots([state], timeout=20)
        self.assertEqual(row["error_type"], "VISIBILITY_TIMEOUT")
        self.assertFalse(row["_ready_for_next"])

    def test_ambiguous_post_submissions_are_not_pass(self):
        row, _ = self.run_snapshots([{**self.completed(), "posts": 2}])
        self.assertEqual(row["error_type"], "RESPONSE_CAPTURE_ERROR")
        self.assertFalse(row["_ready_for_next"])

    def test_capture_result_keeps_excel_job_and_timing_columns_aligned(self):
        from openpyxl import Workbook, load_workbook
        import io

        row, _ = self.run_snapshots([self.completed(False)])
        row.update(run_number=2, runs_per_file=2)
        output = io.BytesIO()
        original_save = Workbook.save
        with patch.object(Path, "mkdir"), patch.object(Workbook, "save", autospec=True,
                side_effect=lambda workbook, path: original_save(workbook, output)):
            user_flow.save_excel(Path("test.xlsx"), [row])
        output.seek(0)
        workbook = load_workbook(output)
        try:
            summary = list(workbook["User Flow Timing"].values)
            mapped = dict(zip(summary[0], summary[1]))
            self.assertEqual(mapped["status"], "PASS")
            self.assertEqual(mapped["job_id"], "current-job")
            self.assertEqual(mapped["query_id"], "query-current")
            self.assertIs(mapped["matched"], False)
            self.assertEqual(mapped["end_to_end_ms"], 120)
            self.assertEqual(mapped["frontend_render_ms"], 35)
            self.assertEqual(mapped["frontend_result_visible_ms"], 75)
            self.assertEqual(mapped["frontend_preview_ready_ms"], 60)
            self.assertEqual(mapped["frontend_commit_end_to_end_ms"], 85)
            self.assertEqual(mapped["frontend_visibility_status"], "READY")
            self.assertIn("not actual paint", mapped["frontend_timing_kind"])
            self.assertIsInstance(mapped["timing_missing_reasons"], str)
            calls = list(workbook["Model Calls"].values)
            self.assertEqual(len(calls), 2)
            call = dict(zip(calls[0], calls[1]))
            self.assertEqual(call["call_id"], "call-current")
            self.assertEqual(call["inference_ms"], 5)
            self.assertEqual(call["run_number"], 2)
            self.assertEqual(call["runs_per_file"], 2)
        finally:
            workbook.close()

    def test_save_precedes_reset_and_next_run(self):
        events = []
        arguments = Mock(repeats=2, timeout_seconds=1, output=Path("test.xlsx"))
        arguments.output_failed = False
        with patch.object(user_flow, "run_case", side_effect=lambda *args: events.append("run") or {"status": "PASS", "_ready_for_next": True}), \
                patch.object(user_flow, "save_results", side_effect=lambda *args: events.append("saved") or True), \
                patch.object(user_flow, "reset_document", side_effect=lambda *args: events.append("reset")), patch("builtins.print"):
            self.assertTrue(user_flow.run_cases(Mock(), arguments, [(Path("a.pdf"), "standard")], [], {"closed": False}))
        self.assertEqual(events, ["run", "saved", "reset", "run", "saved", "reset"])

    def test_incomplete_capture_saves_row_but_never_resets_or_uploads_next(self):
        with patch.object(user_flow, "run_case", return_value={"status": "FAIL", "_ready_for_next": False}) as run, \
                patch.object(user_flow, "save_results", return_value=True) as save, \
                patch.object(user_flow, "reset_document") as reset, patch("builtins.print"):
            self.assertFalse(user_flow.run_cases(Mock(), Mock(repeats=2, timeout_seconds=1), [(Path("a.pdf"), None)], [], {"closed": False}))
        run.assert_called_once()
        save.assert_called_once()
        reset.assert_not_called()

    @unittest.skipUnless(shutil.which("node"), "Node is needed for isolated fetch-capture tests")
    def test_capture_javascript_without_browser(self):
        script = Path(__file__).with_name("test_job_capture.cjs").read_text(encoding="utf-8")
        result = subprocess.run([shutil.which("node"), "-e", script], input=json.dumps(user_flow.CAPTURE_FETCH),
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
