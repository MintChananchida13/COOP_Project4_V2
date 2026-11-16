import unittest
from app.model_runtime.inference_adapters import (
    adapt_layout, adapt_text_detection, adapt_text_recognition,
    adapt_siglip, adapt_table, InferenceAdapterError, TextResponseContractError,
)


class InferenceAdapterTests(unittest.TestCase):
    def test_layout_preserves_fields_and_empty_boxes(self):
        raw = {"boxes": [], "page_index": 2}
        self.assertIs(adapt_layout([{"res": raw}], expected_count=1)[0], raw)
        for invalid in ([], [raw, raw], [None], [{}]):
            with self.subTest(invalid=invalid), self.assertRaises(InferenceAdapterError):
                adapt_layout(invalid, expected_count=1)

    def test_detection_order_scores_and_count_validation(self):
        raw = [{"dt_polys": [[[0, 0], [2, 0], [2, 2]]], "dt_scores": [.9]},
               {"dt_polys": [], "dt_scores": []}]
        result = adapt_text_detection({"predictions": raw, "model": "det"}, expected_count=2)
        self.assertEqual(result[0]["dt_scores"], [.9])
        self.assertEqual(result[1]["dt_polys"], [])
        self.assertEqual(result[0]["model"], "det")
        with self.assertRaises(TextResponseContractError):
            adapt_text_detection(raw, expected_count=1)
        with self.assertRaises(TextResponseContractError):
            adapt_text_detection({"dt_polys": [], "dt_scores": [.9]})

    def test_recognition_aliases_and_compatibility_import(self):
        from app.model_runtime.text_response_contract import normalize_single
        raw = {"text": "hello", "confidence": .8}
        self.assertEqual(adapt_text_recognition(raw), {"rec_text": "hello", "rec_score": .8})
        self.assertEqual(normalize_single(raw, "rec"), adapt_text_recognition(raw))

    def test_siglip_preserves_prompt_order_and_rejects_nonfinite_logits(self):
        raw = {"contract_version": "leaf-inference-v1", "kind": "siglip",
               "categories": ["first", "second"], "device": "cpu",
               "raw_output": [{"logits_per_image": [1., -2.]}]}
        categories = [{"prompt": "first"}, {"prompt": "second"}]
        self.assertEqual(adapt_siglip(raw, categories), [1., -2.])
        with self.assertRaises(ValueError):
            adapt_siglip(raw, categories[::-1])
        raw["raw_output"][0]["logits_per_image"][0] = float("nan")
        with self.assertRaises(ValueError):
            adapt_siglip(raw, categories)

    def test_table_unwrap_preserves_contents(self):
        raw = [{"table_res_list": [{"pred_html": "<table/>"}]}]
        self.assertIs(adapt_table({"data": {"raw_output": raw}}), raw)
        with self.assertRaises(InferenceAdapterError):
            adapt_table({"error": "failed"})
