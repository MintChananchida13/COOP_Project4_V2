import unittest
from user_flow import collect_model_calls, timing_values, PREPARATION_METRICS

class DetailedTimingTests(unittest.TestCase):
    def test_detection_and_layout_calls_keep_separate_phases(self):
        calls = collect_model_calls({"model_calls": [{"call_id": "det", "inference_ms": 12}]},
            {"layoutRequests": [{"index": 2, "browser_request_ms": 90, "pages": [
                {"cache_hit": False, "timing": {"model_calls": [{"call_id": "layout", "inference_ms": 30}]}},
                {"cache_hit": True, "timing": {"model_calls": []}}]}]})
        self.assertEqual([c["call_id"] for c in calls], ["det", "layout"])
        self.assertEqual(calls[1]["phase"], "roi_preparation_layout")
        self.assertEqual(calls[1]["browser_request_ms"], 90)
        self.assertNotIn("browser_request_ms", calls[0])

    def test_missing_details_are_compatible_and_no_cache_calls_are_fabricated(self):
        self.assertEqual(collect_model_calls({}, {}), [])

    def test_preparation_stages_are_exportable(self):
        values = timing_values([[{"stage": key, "elapsedMs": 12} for key in PREPARATION_METRICS]], {})
        for key in PREPARATION_METRICS:
            self.assertEqual(values[key], 12)
