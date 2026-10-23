import unittest

from test_all_models import (
    TestFailure,
    batch_results,
    envelope,
    validate_det,
    validate_layout,
    validate_logits,
    validate_rec,
    validate_table,
)


class ResponseValidationTests(unittest.TestCase):
    def test_envelope_requires_model_and_timing(self):
        response = {"success": True, "model": "th_PP-OCRv5_mobile_rec", "result": {"res": {"rec_text": "HELLO", "rec_score": 0.9}}, "timing": {"model_load_ms": 10, "inference_ms": 20, "total_runtime_ms": 35}}
        self.assertEqual(validate_rec(envelope(200, response, "rec"))["text"], "HELLO")
        with self.assertRaises(TestFailure):
            envelope(503, {"detail": "failed"}, "rec")
        with self.assertRaises(TestFailure):
            envelope(200, {**response, "model": "wrong"}, "rec")

    def test_layout_requires_real_box_fields(self):
        result = {"raw": {"layout": [{"res": {"boxes": [{"label": "table", "score": 0.9, "coordinate": [0, 0, 100, 100]}]}}]}}
        self.assertEqual(validate_layout(result)["box_count"], 1)
        with self.assertRaises(TestFailure):
            validate_layout({"raw": {"layout": [{"res": {"boxes": []}}]}})

    def test_detection_requires_polygons_and_scores(self):
        result = {"res": {"dt_polys": [[[0, 0], [5, 0], [5, 5], [0, 5]]], "dt_scores": [0.8]}}
        self.assertEqual(validate_det(result)["polygon_count"], 1)
        with self.assertRaises(TestFailure):
            validate_det({"res": {"dt_polys": result["res"]["dt_polys"], "dt_scores": []}})

    def test_table_requires_cells_and_ocr(self):
        result = {"raw_output": {"res": {"table_res_list": [{"cell_box_list": [[0, 0, 10, 10]], "table_ocr_pred": {"rec_texts": ["A"]}}]}}}
        self.assertEqual(validate_table(result)["cell_count"], 1)
        with self.assertRaises(TestFailure):
            validate_table({"raw_output": {"res": {"table_res_list": []}}})

    def test_batch_count_and_siglip_logits(self):
        self.assertEqual(len(batch_results({"results": [1, 2]}, 2)), 2)
        with self.assertRaises(TestFailure):
            batch_results({"results": [1]}, 2)
        self.assertEqual(validate_logits({"logits": [1.2, -0.3], "device": "cpu"}), [1.2, -0.3])
        with self.assertRaises(TestFailure):
            validate_logits({"logits": [1.2], "device": "cpu"})


if __name__ == "__main__":
    unittest.main()
