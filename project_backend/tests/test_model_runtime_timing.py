import unittest
from unittest.mock import patch

from app.core.model_runtime_client import (
    _extract_gateway_timing,
    _post_predict, ModelRuntimeKind, ModelRuntimeUnavailableError,
    remote_recognize_image,
    remote_recognize_images,
    remote_recognize_table_raw,
    remote_verify_image_logits,
    remote_verify_image_logits_batch,
)


class ModelRuntimeTimingTests(unittest.TestCase):
    def test_failed_http_call_keeps_monotonic_interval(self):
        timing = {}
        with patch("app.core.model_runtime_client.runtime_url", return_value="http://localhost/model"), \
                patch("app.core.model_runtime_client.urllib.request.urlopen", side_effect=OSError("offline")):
            with self.assertRaises(ModelRuntimeUnavailableError):
                _post_predict(ModelRuntimeKind.LAYOUT, {}, timing=timing)
        self.assertGreaterEqual(timing["http_request_ms"], 0)
        self.assertGreaterEqual(timing["model_call_end_monotonic"], timing["model_call_start_monotonic"])
        self.assertEqual(timing["model_call_interval_ms"], timing["http_request_ms"])

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
                patch("app.core.model_runtime_client._active_ocr_path", return_value="/api/v1/test"), \
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

    def test_missing_active_model_or_path_does_not_fall_back(self):
        from app.core.model_runtime_client import _active_ocr_model, _active_ocr_path
        from unittest.mock import MagicMock

        connection = MagicMock()
        connection.__enter__.return_value.execute.return_value.fetchone.return_value = {
            "value": '{"active":{"text_recognition":"missing"},"models":{"text_recognition":[{"id":"other"}]}}'
        }
        with patch("app.core.model_runtime_client.connect_db", return_value=connection):
            with self.assertRaisesRegex(ModelRuntimeUnavailableError, "missing"):
                _active_ocr_model("text_recognition")
        with patch("app.core.model_runtime_client._active_ocr_model", return_value={"id": "chosen"}):
            with self.assertRaisesRegex(ModelRuntimeUnavailableError, "chosen"):
                _active_ocr_path("text_recognition", "single")

    def test_unavailable_settings_do_not_fall_back(self):
        from app.core.model_runtime_client import _active_ocr_model
        with patch("app.core.model_runtime_client.connect_db", side_effect=RuntimeError("offline")):
            with self.assertRaisesRegex(ModelRuntimeUnavailableError, "Unable to load"):
                _active_ocr_model("text_recognition")

    def test_selected_model_must_be_confirmed_by_runtime(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"success":true,"model":"th_PP-OCRv5_mobile_rec","result":{}}'
        with patch("app.core.model_runtime_client.GATEWAY_URL", "http://localhost"), \
                patch("app.core.model_runtime_client.urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(ModelRuntimeUnavailableError, "did not confirm"):
                _post_predict(ModelRuntimeKind.TEXT_RECOGNITION, {}, path_override="/api/v1/text-recognitions?version=v5&model_id=thai_ft_v3")
            response.__enter__.return_value.read.return_value = b'{"success":true,"model":"th_PP-OCRv5_mobile_rec:thai_ft_v3","result":{}}'
            self.assertEqual(_post_predict(ModelRuntimeKind.TEXT_RECOGNITION, {}, path_override="/api/v1/text-recognitions?version=v5&model_id=thai_ft_v3")["model"], "th_PP-OCRv5_mobile_rec:thai_ft_v3")


if __name__ == "__main__":
    unittest.main()
