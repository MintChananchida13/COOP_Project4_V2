import unittest

from app.processing.model_timing import backend_runtime_from_union, layout_runtime_from_breakdown, summarize_model_calls


def call(call_id, kind, start, end, wait=100.0, load=0.0):
    return {
        "model_call_id": call_id,
        "model_kind": kind,
        "model_call_start_monotonic": start,
        "model_call_end_monotonic": end,
        "http_request_ms": wait,
        "gateway_timing": {
            "model_load_ms": load,
            "queue_wait_ms": 0.0,
            "inference_ms": 10.0,
            "total_runtime_ms": 20.0,
        },
    }


class ModelTimingSummaryTests(unittest.TestCase):
    def test_siglip_single_and_batch_inferences_deduplicate_by_call_id(self):
        single = call("single", "image_verification", 1.0, 1.1)
        batch = call("batch", "image_verification", 1.2, 1.3)
        result = summarize_model_calls([single, {"nested": single}, batch])
        self.assertEqual(result["inferences"]["image_verification"], 20.0)

    def test_siglip_inference_requires_all_call_measurements(self):
        complete = call("complete", "image_verification", 1.0, 1.1)
        incomplete = call("missing", "image_verification", 1.2, 1.3)
        del incomplete["gateway_timing"]["inference_ms"]
        self.assertIsNone(summarize_model_calls([complete, incomplete])["inferences"]["image_verification"])
        self.assertNotIn("image_verification", summarize_model_calls([])["inferences"])

    def test_sequential_single_and_batch_calls(self):
        result = summarize_model_calls([
            call("single", "layout", 1.0, 1.1),
            call("batch", "text_detection", 1.2, 1.4, 200.0),
        ])
        self.assertEqual(result["model_wait_union_ms"], 300.0)
        self.assertEqual(result["model_wall_clock_ms"], 400.0)
        self.assertFalse(result["model_intervals_overlap"])
        self.assertEqual(result["waits"], {"layout": 100.0, "text_detection": 200.0})

    def test_overlapping_calls_use_union_not_sum(self):
        result = summarize_model_calls([
            call("det", "text_detection", 1.0, 1.3, 300.0),
            call("rec", "text_recognition", 1.2, 1.4, 200.0),
        ])
        self.assertEqual(result["model_wait_union_ms"], 400.0)
        self.assertTrue(result["model_intervals_overlap"])

    def test_duplicate_record_uses_call_id(self):
        original = call("same", "table", 1.0, 1.1)
        result = summarize_model_calls([original, {"wrapper": original}])
        self.assertEqual(result["model_call_count"], 1)
        self.assertEqual(result["waits"]["table"], 100.0)
        self.assertEqual(result["model_calls"][0]["model_load_ms"], 0.0)

    def test_missing_interval_never_uses_wait_sum_as_union(self):
        incomplete = call("rec", "text_recognition", 1.2, 1.3)
        del incomplete["model_call_end_monotonic"]
        result = summarize_model_calls([call("layout", "layout", 1.0, 1.1), incomplete])
        self.assertIsNone(result["model_wait_union_ms"])
        self.assertIsNone(result["model_intervals_overlap"])
        self.assertEqual(result["model_timing_missing_reason"], "incomplete_model_call_intervals")

    def test_no_calls_is_unknown_not_zero(self):
        result = summarize_model_calls({})
        self.assertIsNone(result["model_wait_union_ms"])
        self.assertEqual(result["model_call_count"], 0)

    def test_layout_runtime_comes_from_nested_breakdown(self):
        self.assertEqual(layout_runtime_from_breakdown([
            {"analyze_layout_signature": {"layout_runtime_ms": 12.5}},
            {"analyze_layout_signature": {"layout_runtime_ms": 13.25}},
            {"analyze_layout_signature": {"raw_cache_hit": True}},
        ]), 25.75)
        self.assertIsNone(layout_runtime_from_breakdown([]))

    def test_backend_residual_requires_complete_sequential_intervals(self):
        self.assertEqual(backend_runtime_from_union(500.0, 300.0, False), 200.0)
        self.assertEqual(backend_runtime_from_union(500.0, 300.0, True), 200.0)
        self.assertIsNone(backend_runtime_from_union(500.0, None, None))
        self.assertIsNone(backend_runtime_from_union(500.0, 600.0, False))

    def test_normalized_export_can_be_summarized_without_duplicate_intervals(self):
        original = call("same", "image_verification", 1.0, 1.1)
        first = summarize_model_calls(original)
        result = summarize_model_calls([original, first["model_calls"]])
        self.assertEqual(result["model_call_count"], 1)
        self.assertEqual(result["model_wait_union_ms"], 100.0)
        self.assertEqual(result["inferences"]["image_verification"], 10.0)

    def test_complementary_duplicate_records_merge(self):
        original = call("same", "table", 1.0, 1.1)
        partial = dict(original, gateway_timing={})
        result = summarize_model_calls([partial, original])
        self.assertEqual(result["inferences"]["table"], 10.0)

    def test_incomplete_wait_and_nonfinite_intervals_are_unknown(self):
        incomplete = call("missing", "table", 1.2, float("inf"), wait=None)
        result = summarize_model_calls([call("first", "table", 1.0, 1.1), incomplete])
        self.assertIsNone(result["waits"]["table"])
        self.assertIsNone(result["model_wait_union_ms"])

    def test_conflicting_duplicate_is_not_silently_accepted(self):
        result = summarize_model_calls([call("same", "layout", 1.0, 1.1), call("same", "layout", 1.0, 1.2)])
        self.assertIsNone(result["model_wait_union_ms"])
        self.assertIsNone(result["inferences"]["layout"])

    def test_layout_components_share_one_http_interval(self):
        original = call("layout-det", "layout", 1.0, 1.1)
        original["gateway_timing"]["model_timings"] = {
            "layout": {"inference_ms": 3.0}, "text_detection": {"inference_ms": 7.0},
        }
        result = summarize_model_calls(original)
        self.assertEqual(result["inferences"], {"layout": 3.0, "text_detection": 7.0})
        self.assertEqual(result["model_wait_union_ms"], 100.0)
        self.assertEqual(result["model_call_count"], 1)
        self.assertIsNone(result["waits"]["text_detection"])


if __name__ == "__main__":
    unittest.main()
