import unittest

from app.core.model_runtime_client import _extract_gateway_timing


class ModelRuntimeTimingTests(unittest.TestCase):
    def test_reads_model_timing_from_data_siblings(self):
        timing = _extract_gateway_timing({
            "data": {
                "results": [],
                "text_det_inference_ms": 12.5,
                "text_rec_inference_ms": 34.75,
            }
        })
        self.assertEqual(timing["text_det_inference_ms"], 12.5)
        self.assertEqual(timing["text_rec_inference_ms"], 34.75)

    def test_explicit_timing_wins_and_does_not_double_count(self):
        timing = _extract_gateway_timing({
            "timing": {"text_rec_inference_ms": 20.0},
            "data": {
                "timing": {"text_rec_inference_ms": 30.0},
                "text_rec_inference_ms": 40.0,
                "text_det_inference_ms": 10.0,
            },
        })
        self.assertEqual(timing["text_rec_inference_ms"], 20.0)
        self.assertEqual(timing["text_det_inference_ms"], 10.0)

    def test_missing_model_timing_stays_missing(self):
        self.assertIsNone(_extract_gateway_timing({"data": {"results": []}}))


if __name__ == "__main__":
    unittest.main()
