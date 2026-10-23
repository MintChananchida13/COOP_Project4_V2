import unittest
from unittest.mock import patch

from app.core.model_runtime_client import (
    _extract_gateway_timing,
    remote_recognize_image,
    remote_recognize_images,
    remote_recognize_table_raw,
    remote_verify_image_logits,
    remote_verify_image_logits_batch,
)


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

    def test_single_and_batch_calls_forward_timing_collectors(self):
        timing = {}
        with patch("app.core.model_runtime_client.is_runtime_configured", return_value=True), \
                patch("app.core.model_runtime_client._image_to_data_url", return_value="image"), \
                patch("app.core.model_runtime_client._path_to_data_url", return_value="image"), \
                patch("app.core.model_runtime_client._post_predict", return_value={}) as predict:
            remote_recognize_image(None, timing=timing)
            self.assertIs(predict.call_args.kwargs["timing"], timing)
            remote_recognize_images([None], timing=timing)
            self.assertIs(predict.call_args.kwargs["timing"], timing)
            remote_recognize_table_raw(None, timing=timing)
            self.assertIs(predict.call_args.kwargs["timing"], timing)
            remote_verify_image_logits("image.png", "type", timing=timing)
            self.assertIs(predict.call_args.kwargs["timing"], timing)
            remote_verify_image_logits_batch(["image.png"], timing=timing)
            self.assertIs(predict.call_args.kwargs["timing"], timing)


if __name__ == "__main__":
    unittest.main()
