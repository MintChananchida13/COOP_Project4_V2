import unittest

from app.timing import build_timing


class TimingContractTests(unittest.TestCase):
    def test_layout_component_excludes_nested_detector_but_preserves_operation_total(self):
        components = {"layout": {"inference_ms": 10.0}, "text_detection": {"inference_ms": 20.0}}
        result = build_timing("layout", 1.0, 0.0, 2.0, 35.0, 3.0, 42.0, model_timings=components)
        self.assertEqual(result["layout_runtime_ms"], 10.0)
        self.assertEqual(result["inference_ms"], 35.0)
        self.assertEqual(result["model_timings"], components)

    def test_cached_layout_model_retains_legacy_keys(self):
        result = build_timing("layout", 2.0, 0.0, 3.0, 40.0, 4.0, 52.0)
        self.assertEqual(result["model_load_ms"], 0.0)
        self.assertEqual(result["layout_runtime_ms"], 40.0)
        self.assertEqual(result["inference_including_load_and_adaptation"], 47.0)
        self.assertEqual(result["total_runtime_ms"], 52.0)

    def test_single_and_batch_endpoint_model_keys_remain_available(self):
        for name, key in (
            ("det", "text_det_inference_ms"),
            ("rec", "text_rec_inference_ms"),
            ("table", "table_runtime_ms"),
            ("siglip", "image_siglip_runtime_ms"),
        ):
            with self.subTest(name=name):
                result = build_timing(name, 1.0, 2.0, 3.0, 4.0, 5.0, 16.0)
                self.assertEqual(result[key], 4.0)
                self.assertEqual(result["inference_lock_wait_ms"], 3.0)
                self.assertEqual(result["inference_ms"], 4.0)


if __name__ == "__main__":
    unittest.main()
