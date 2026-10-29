import unittest
from app.processing.detection_work_policy import detection_work_scope, include_timing_details
from app.api.detection_response import user_detection_response

class OptInTimingTests(unittest.TestCase):
    def test_user_default_stays_small_and_opt_in_only_adds_calls(self):
        raw = {"matched": False, "debug": {"timing": {"model_call_count": 1,
            "model_calls": [{"call_id": "one", "inference_ms": 4}]}}}
        @detection_work_scope
        def response(user_context=True, timing_details=False):
            return user_detection_response(raw)
        normal = response()
        detailed = response(timing_details=True)
        self.assertNotIn("model_calls", normal["debug"]["timing"])
        self.assertEqual(detailed["debug"]["timing"].pop("model_calls"), raw["debug"]["timing"]["model_calls"])
        self.assertEqual(normal, detailed)
        self.assertFalse(include_timing_details())

    def test_scope_resets_after_failure(self):
        @detection_work_scope
        def failure(timing_details=False):
            self.assertTrue(include_timing_details())
            raise ValueError()
        with self.assertRaises(ValueError):
            failure(timing_details=True)
        self.assertFalse(include_timing_details())
