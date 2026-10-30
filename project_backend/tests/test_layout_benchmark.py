import ast
import asyncio
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from app.processing import search_benchmark


class LayoutBenchmarkTests(unittest.TestCase):
    def test_flexible_layout_trace_records_real_calls_without_changing_regions(self):
        source = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8"))
        function = next(item for item in source.body if isinstance(item, ast.AsyncFunctionDef) and item.name == "analyze_document_layout")
        function.decorator_list = []
        module = ast.Module(body=[function], type_ignores=[])
        analysis = {"image_width": 100, "image_height": 100, "engine": "layout", "model": "layout", "regions": []}

        @search_benchmark.instrument_runtime
        def fake_runtime(*, timing=None):
            if timing is None:
                return
            timing.update(model_call_id="call", model_kind="layout", http_request_ms=10,
                          model_call_start_monotonic=1, model_call_end_monotonic=1.01,
                          gateway_timing={"queue_wait_ms": 2, "inference_ms": 5, "total_runtime_ms": 8})

        def analyze(*args, **kwargs):
            fake_runtime()
            return analysis

        from fastapi import HTTPException
        namespace = {"LayoutAnalysisPayload": object, "Dict": dict, "List": list, "Any": object,
                     "HTTPException": HTTPException, "LayoutAnalysisUnavailableError": RuntimeError,
                     "_ensure_processing_available_for_context": Mock(), "decode_base64_image": lambda _: (None, object()),
                     "analyze_layout": analyze, "_dedupe_overlapping_auto_roi_regions": lambda regions: regions, "time": time}
        exec(compile(module, "main.py", "exec"), namespace)
        payload = Mock(context="flexible", images=[Mock(page_index=0, image="image")], benchmark_trace_id="a" * 32)
        result = asyncio.run(namespace["analyze_document_layout"](payload))
        self.assertEqual(result["benchmark_timing"]["trace_id"], "a" * 32)
        self.assertEqual(result["benchmark_timing"]["layout_inference_ms"], 5)
        self.assertEqual(result["pages"][0]["regions"], [])
        payload.benchmark_trace_id = None
        self.assertNotIn("benchmark_timing", asyncio.run(namespace["analyze_document_layout"](payload)))


if __name__ == "__main__":
    unittest.main()
