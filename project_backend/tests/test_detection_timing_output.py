import ast
import __future__
from pathlib import Path
from types import SimpleNamespace
import unittest


class DetectionTimingOutputTests(unittest.TestCase):
    def collect(self, timing, pages):
        source = Path(__file__).parents[1] / "app/processing/detection_service.py"
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        names = {"_detection_timing_debug", "_ms", "_runtime_ms", "_debug_timing_value"}
        functions = ast.Module(body=[item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name in names], type_ignores=[])
        namespace = {"time": SimpleNamespace(perf_counter=lambda: 20.0),
                     "_benchmark_timing_debug": lambda *args, **kwargs: {},
                     "_template_matching_timing_ms_list": lambda value: value,
                     "_template_matching_timing_ms": lambda value: value}
        exec(compile(functions, str(source), "exec", flags=__future__.annotations.compiler_flag), namespace)
        return namespace["_detection_timing_debug"](timing, pages, 19.0)

    def test_standard_models_propagate_to_benchmark_and_deduplicate(self):
        calls = [{"model_call_id": kind, "model_kind": kind,
                  "model_call_start_monotonic": 19 + index / 10,
                  "model_call_end_monotonic": 19.05 + index / 10,
                  "http_request_ms": 50, "gateway_timing": {"inference_ms": index, "model_load_ms": 0}}
                 for index, kind in enumerate(("layout", "text_detection", "text_recognition", "table", "image_verification"))]
        pages = [{"candidates": [{"timing": {"model_calls": calls}}]}]
        result = self.collect({"model_calls": calls}, pages)
        self.assertEqual(result["model_call_count"], 5)
        self.assertEqual(result["model_wait_union_ms"], 250)
        self.assertEqual(result["backend_runtime_ms"], 750)
        self.assertEqual(result["layout_runtime_ms"], 0)
        self.assertEqual(result["table_runtime_ms"], 3)
        self.assertEqual(result["image_siglip_runtime_ms"], 4)
        for key in ("model_calls", "table_runtime_ms", "image_siglip_runtime_ms", "timing_missing_reasons", "model_wait_union_ms"):
            self.assertEqual(result[key], result["benchmark"][key])

    def test_text_only_does_not_fabricate_unused_model_values(self):
        result = self.collect({"text_only_model_calls": [{
            "model_call_id": "rec", "model_kind": "text_recognition", "http_request_ms": 100,
            "model_call_start_monotonic": 19.1, "model_call_end_monotonic": 19.2,
            "gateway_timing": {"inference_ms": 20},
        }]}, [])
        self.assertEqual(result["text_recognition_runtime_ms"], 20)
        for key in ("layout_runtime_ms", "image_siglip_runtime_ms", "table_runtime_ms", "frontend_render_ms"):
            self.assertIsNone(result[key])
            self.assertTrue(result["timing_missing_reasons"][key])


if __name__ == "__main__":
    unittest.main()
