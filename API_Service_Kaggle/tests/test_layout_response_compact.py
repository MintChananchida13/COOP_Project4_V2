import base64
import unittest
from dataclasses import replace
from unittest.mock import patch

import cv2
import numpy as np
from fastapi.testclient import TestClient

from app import main
from app.models import predict


class Result(dict):
    @property
    def json(self):
        raise AssertionError("Must preserve existing result shape")


class FakeModel:
    def __init__(self, result):
        self.result = result

    def predict(self, image):
        return [self.result]


class CompactLayoutTests(unittest.TestCase):
    def test_omit_only_image_before_serialization_and_preserve_original(self):
        image = np.zeros((4, 5, 3), dtype=np.uint8)
        original = Result(input_img=image, boxes=[{"label": "text", "score": 0.8,
                                                   "coordinate": np.array([1, 2, 3, 4]), "order": 7}],
                          input_path=None, page_index=0, custom_metadata={"value": 42})
        result = predict("layout", image, FakeModel(original))[0]
        self.assertNotIn("input_img", result)
        self.assertIs(original["input_img"], image)
        self.assertEqual(result, {"boxes": [{"label": "text", "score": 0.8, "coordinate": [1, 2, 3, 4], "order": 7}],
                                  "input_path": None, "page_index": 0, "custom_metadata": {"value": 42}})
        self.assertIn("input_img", predict("", image, FakeModel(original))[0])

    def test_layout_single_and_combined_keep_boxes_and_detector_result(self):
        image = np.zeros((8, 8, 3), dtype=np.uint8)
        ok, data = cv2.imencode(".png", image)
        self.assertTrue(ok)
        encoded = "data:image/png;base64," + base64.b64encode(data).decode()
        layout = FakeModel(Result(input_img=image, boxes=[{"label": "text", "score": 0.9, "coordinate": [1, 1, 6, 4]}]))
        detector = FakeModel(Result(input_img=image, dt_polys=[[[1, 1], [6, 1], [6, 4], [1, 4]]], dt_scores=[0.9]))
        with patch.object(main, "settings", replace(main.settings, api_key="secret")), \
                patch.object(main.manager, "get", side_effect=lambda kind: (layout if kind == "layout" else detector, 0)):
            client = TestClient(main.app)
            for layout_only in (True, False):
                response = client.post("/api/v1/document-layouts", headers={"Authorization": "Bearer secret"},
                                       json={"image": encoded, "layout_only": layout_only})
                self.assertEqual(response.status_code, 200, response.text)
                raw = response.json()["result"]["raw"]
                self.assertEqual(raw["layout"][0]["boxes"][0]["coordinate"], [1, 1, 6, 4])
                self.assertNotIn("input_img", raw["layout"][0])
                if layout_only:
                    self.assertNotIn("text_detection", raw)
                else:
                    self.assertEqual(raw["text_detection"][0]["dt_scores"], [0.9])
                    self.assertIn("input_img", raw["text_detection"][0])


if __name__ == "__main__":
    unittest.main()
