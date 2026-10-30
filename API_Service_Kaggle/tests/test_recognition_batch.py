import base64
import unittest
from dataclasses import replace
from unittest.mock import patch

import cv2
import numpy as np
from fastapi.testclient import TestClient

from app import main


def image_url(value):
    ok, encoded = cv2.imencode(".png", np.full((8, 8, 3), value, dtype=np.uint8))
    assert ok
    return "data:image/png;base64," + base64.b64encode(encoded).decode()


class FakeRecognition:
    def __init__(self):
        self.calls = []

    def predict(self, images, batch_size):
        self.calls.append((len(images), batch_size))
        return [{"res": {"rec_text": str(int(image[0, 0, 0])), "rec_score": 0.9}} for image in images]


class RecognitionBatchTests(unittest.TestCase):
    def test_finetuned_batch_keeps_order_and_response_contract(self):
        for version, model_id in (("v5", "default"), ("v5", "thai_ft_v3"), ("v6", "thai_ft_v1")):
            model = FakeRecognition()
            settings = replace(main.settings, api_key="secret", recognition_batch_size=2)
            with patch.object(main, "settings", settings), patch.object(main.manager, "get_ocr", return_value=(model, 0)), patch.object(main.manager, "get", return_value=(model, 0)):
                client = TestClient(main.app)
                response = client.post(f"/api/v1/text-recognition-batches?version={version}&model_id={model_id}",
                                       headers={"Authorization": "Bearer secret"},
                                       json={"images": [image_url(value) for value in (10, 20, 30, 40, 50)]})
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(set(body), {"success", "model", "result", "timing"})
            self.assertEqual([item["res"]["rec_text"] for item in body["result"]["results"]], ["10", "20", "30", "40", "50"])
            self.assertEqual(model.calls, [(2, 2), (2, 2), (1, 1)])

    def test_invalid_result_count_fails_without_silent_fallback(self):
        model = FakeRecognition()
        model.predict = lambda images, batch_size: []
        with self.assertRaisesRegex(RuntimeError, "result count or shape"):
            main.paddle_recognition_batch(model, [np.zeros((8, 8, 3), dtype=np.uint8)])


if __name__ == "__main__":
    unittest.main()
