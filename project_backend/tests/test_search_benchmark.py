import unittest
from unittest.mock import patch

from app.processing import search_benchmark
from app.api import routes


class SearchBenchmarkTests(unittest.TestCase):
    def test_template_search_uses_matching_in_all_modes_and_preserves_zero(self):
        for timing, expected in (({"template_matching_ms": 12}, 12),
                                 ({"anchor_strategy_matching_ms": 23}, 23),
                                 ({"text_only_matching_ms": 34}, 34),
                                 ({"anchor_strategy_matching_ms": 0}, 0), ({}, None)):
            state, token = search_benchmark.begin("1" * 32)
            try:
                state["timing"] = timing
                self.assertEqual(search_benchmark.metrics(state, 50)["template_search_ms"], expected)
            finally:
                search_benchmark.end(token)

    def test_backend_union_avoids_double_counting_and_rejects_missing_spans(self):
        trace = "e" * 32
        search_benchmark.record_span(trace, "search", 1, 3)
        search_benchmark.record_span(trace, "post", 2, 4)
        search_benchmark.record_span(trace, "post", 3.5, 5)
        measured = search_benchmark.span_metrics(trace, 2)
        self.assertEqual(measured["backend_search_ms"], 2000)
        self.assertEqual(measured["backend_post_search_ms"], 3000)
        self.assertEqual(measured["backend_total_active_ms"], 4000)
        incomplete = search_benchmark.span_metrics(trace, 3)
        self.assertEqual(incomplete["backend_search_ms"], 2000)
        self.assertIsNone(incomplete["backend_post_search_ms"])
        self.assertIsNone(incomplete["backend_total_active_ms"])

    def test_post_search_http_span_is_recorded_only_for_traced_requests(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.processing.search_benchmark_middleware import SearchBenchmarkMiddleware
        app = FastAPI()
        app.add_middleware(SearchBenchmarkMiddleware)
        @app.get("/admin/templates/{template_id}")
        def template(template_id: str):
            return {"id": template_id}
        trace = "f" * 32
        search_benchmark.record_span(trace, "search", 1, 2)
        with TestClient(app) as client:
            self.assertEqual(client.get(f"/admin/templates/tpl?context=user&benchmark_trace_id={trace}").status_code, 200)
            self.assertEqual(client.get("/admin/templates/tpl").status_code, 200)
        result = search_benchmark.span_metrics(trace, 1)
        self.assertEqual(result["post_requests_observed"], 1)
        self.assertIsNotNone(result["backend_post_search_ms"])

    def test_model_calls_report_queue_separately_and_not_run_as_null(self):
        state, token = search_benchmark.begin("a" * 32)
        try:
            @search_benchmark.instrument_runtime
            def fake(*, timing):
                timing.update(model_call_id="call", model_kind="text_recognition", http_request_ms=20,
                              model_call_start_monotonic=1, model_call_end_monotonic=1.02,
                              gateway_timing={"queue_wait_ms": 3, "inference_ms": 9, "total_runtime_ms": 15})
            fake()
            search_benchmark.record_result_processing(2)
            result = search_benchmark.metrics(state, 50)
        finally:
            search_benchmark.end(token)
        self.assertEqual(result["backend_runtime_ms"], 50)
        self.assertEqual(result["text_recognition_queue_wait_ms"], 3)
        self.assertEqual(result["text_recognition_inference_ms"], 9)
        self.assertEqual(result["text_recognition_total_ms"], 15)
        self.assertIsNone(result["siglip_inference_ms"])
        self.assertEqual(result["result_processing_ms"], 2)

    def test_failed_job_retains_trace_and_does_not_change_normal_results(self):
        job_id = "test_benchmark_failure"
        routes.detect_dev_jobs[job_id] = {"status": "processing"}
        try:
            with patch.object(routes, "detect_template_dev", side_effect=RuntimeError("failed")), patch.object(routes, "clear_layout_analysis_cache"):
                routes._run_detect_dev_job(job_id, b"image", benchmark_trace_id="b" * 32)
            job = routes.detect_dev_jobs[job_id]
            self.assertEqual(job["status"], "failed")
            self.assertEqual(job["benchmark_timing"]["trace_id"], "b" * 32)
            self.assertIsNone(job["benchmark_timing"]["text_recognition_inference_ms"])
        finally:
            routes.detect_dev_jobs.pop(job_id, None)

    def test_shared_model_queue_and_overlapping_queue_are_not_double_counted(self):
        state, token = search_benchmark.begin("c" * 32)
        try:
            state["model_calls"] = [
                {"model_call_id": "one", "model_kind": "layout", "http_request_ms": 10,
                 "model_call_start_monotonic": 1, "model_call_end_monotonic": 1.01,
                 "gateway_timing": {"queue_wait_ms": 2, "inference_ms": 6, "total_runtime_ms": 9,
                                    "model_timings": {"layout": {"inference_ms": 3}, "text_detection": {"inference_ms": 3}}}},
                {"model_call_id": "two", "model_kind": "text_recognition", "http_request_ms": 10,
                 "model_call_start_monotonic": 1.005, "model_call_end_monotonic": 1.015,
                 "gateway_timing": {"queue_wait_ms": 1, "inference_ms": 4, "total_runtime_ms": 8}},
            ]
            result = search_benchmark.metrics(state, 30)
        finally:
            search_benchmark.end(token)
        self.assertIsNone(result["model_queue_wait_ms"])
        self.assertIsNone(result["text_detection_queue_wait_ms"])
        self.assertEqual(result["text_detection_inference_ms"], 3)

    def test_traced_http_job_keeps_user_result_and_exposes_timing(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(routes.router)
        def detect(*args, **kwargs):
            search_benchmark.capture_result({"debug": {"verification_strategy": "text_only", "timing": {}}})
            return {"matched": False, "query_id": "query"}
        trace = "d" * 32
        with patch.object(routes, "_ensure_processing_available_for_request"), \
                patch.object(routes, "detect_template_dev", side_effect=detect), \
                patch.object(routes, "clear_layout_analysis_cache"):
            with TestClient(app) as client:
                response = client.post(f"/api/templates/detect-dev?confirmedImages=1&benchmark_trace_id={trace}",
                                       files={"file": ("page.png", b"sample", "image/png")})
                self.assertEqual(response.status_code, 202)
                job_id = response.json()["data"]["job_id"]
                job = client.get(f"/api/templates/detect-dev/jobs/{job_id}").json()["data"]
        try:
            self.assertEqual(job["result"], {"matched": False, "query_id": "query"})
            self.assertEqual(job["benchmark_timing"]["trace_id"], trace)
            self.assertEqual(job["benchmark_timing"]["processing_mode"], "text_only")
            self.assertIsNone(job["benchmark_timing"]["siglip_inference_ms"])
        finally:
            routes.detect_dev_jobs.pop(job_id, None)


if __name__ == "__main__":
    unittest.main()
