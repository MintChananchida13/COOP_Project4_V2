import unittest
from unittest.mock import patch

import numpy as np

from app.paddle_thai_ocr_adapter import (
    PaddleThaiOcrUnavailableError,
    run_paddle_thai_ocr,
    run_paddle_thai_ocr_batch,
)


class PaddleThaiOcrAdapterRuntimeRoutingTest(unittest.TestCase):
    def test_remote_runtime_does_not_fallback_to_local_single_image(self) -> None:
        image = np.zeros((10, 10, 3), dtype=np.uint8)

        with patch.dict("os.environ", {"TEXT_RECOGNITION_MODEL_URL": "https://model.example"}, clear=False), patch(
            "app.paddle_thai_ocr_adapter.remote_recognize_image",
            return_value={"text": "remote", "confidence": 0.9, "engine": "paddle_thai_ocr"},
        ) as remote, patch("app.paddle_thai_ocr_adapter._load_text_recognizer") as load_local, patch(
            "app.paddle_thai_ocr_adapter.cv2.imwrite"
        ) as make_temp:
            result = run_paddle_thai_ocr(image)

        self.assertEqual(result["text"], "remote")
        remote.assert_called_once()
        load_local.assert_not_called()
        make_temp.assert_not_called()

    def test_remote_thai_text_preserves_utf8_and_records_runtime_model(self) -> None:
        image = np.zeros((10, 10, 3), dtype=np.uint8)

        with patch.dict("os.environ", {"TEXT_RECOGNITION_MODEL_URL": "https://model.example"}, clear=False), patch(
            "app.paddle_thai_ocr_adapter.remote_recognize_image",
            return_value={"rec_text": "ชื่อสินค้า", "rec_score": 0.97, "model": "th_PP-OCRv5_mobile_rec"},
        ):
            result = run_paddle_thai_ocr(image)

        self.assertEqual(result["raw_text"], "ชื่อสินค้า")
        self.assertEqual(result["normalized_text"], "ชื่อสินค้า")
        self.assertEqual(result["text"], "ชื่อสินค้า")
        self.assertEqual(result["model"], "th_PP-OCRv5_mobile_rec")

    def test_remote_runtime_error_raises_without_local_fallback(self) -> None:
        image = np.zeros((10, 10, 3), dtype=np.uint8)

        with patch.dict("os.environ", {"TEXT_RECOGNITION_MODEL_URL": "https://model.example"}, clear=False), patch(
            "app.paddle_thai_ocr_adapter.remote_recognize_image",
            side_effect=RuntimeError("remote boom"),
        ), patch("app.paddle_thai_ocr_adapter._load_text_recognizer") as load_local, patch(
            "app.paddle_thai_ocr_adapter.cv2.imwrite"
        ) as make_temp:
            with self.assertRaisesRegex(PaddleThaiOcrUnavailableError, "remote boom"):
                run_paddle_thai_ocr(image)

        load_local.assert_not_called()
        make_temp.assert_not_called()

    def test_remote_runtime_none_batch_raises_without_local_fallback(self) -> None:
        images = [np.zeros((10, 10, 3), dtype=np.uint8)]

        with patch.dict("os.environ", {"TEXT_RECOGNITION_MODEL_URL": "https://model.example"}, clear=False), patch(
            "app.paddle_thai_ocr_adapter.remote_recognize_images",
            return_value=None,
        ), patch("app.paddle_thai_ocr_adapter._load_text_recognizer") as load_local, patch(
            "app.paddle_thai_ocr_adapter.cv2.imwrite"
        ) as make_temp:
            with self.assertRaisesRegex(PaddleThaiOcrUnavailableError, "Remote OCR runtime returned no result."):
                run_paddle_thai_ocr_batch(images)

        load_local.assert_not_called()
        make_temp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
