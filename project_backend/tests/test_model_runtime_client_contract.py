import json
import unittest
from unittest.mock import patch

import numpy as np

from app.model_runtime_client import (
    ModelRuntimeKind,
    _post_predict,
    remote_recognize_image,
)
from app.table_recognition_v2_adapter import recognize_table_v2


class _FakeHttpResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class ModelRuntimeClientContractTest(unittest.TestCase):
    def test_predict_uses_standard_result_wrapper(self) -> None:
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _FakeHttpResponse(
                {
                    "success": True,
                    "model": "th_PP-OCRv5_mobile_rec",
                    "result": {"rec_text": "hello", "rec_score": 0.98},
                }
            )

        image = np.zeros((8, 16, 3), dtype=np.uint8)
        with patch.dict("os.environ", {"TEXT_RECOGNITION_MODEL_URL": "https://ocr.example"}, clear=False), patch(
            "urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            result = remote_recognize_image(image)

        self.assertEqual(captured["url"], "https://ocr.example/predict")
        self.assertIn("image", captured["body"])
        self.assertTrue(captured["body"]["image"].startswith("data:image/png;base64,"))
        self.assertEqual(result, {"rec_text": "hello", "rec_score": 0.98, "model": "th_PP-OCRv5_mobile_rec"})

    def test_predict_preserves_utf8_thai_text_and_runtime_model_metadata(self) -> None:
        class Utf8HttpResponse(_FakeHttpResponse):
            def read(self) -> bytes:
                return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")

        with patch.dict("os.environ", {"TEXT_RECOGNITION_MODEL_URL": "https://ocr.example"}, clear=False), patch(
            "urllib.request.urlopen",
            return_value=Utf8HttpResponse(
                {
                    "success": True,
                    "model": "th_PP-OCRv5_mobile_rec",
                    "result": {"rec_text": "ชื่อสินค้า", "rec_score": 0.98},
                }
            ),
        ):
            result = _post_predict(ModelRuntimeKind.TEXT_RECOGNITION, {"image": "data:image/png;base64,AA=="})

        self.assertEqual(result["rec_text"], "ชื่อสินค้า")
        self.assertEqual(result["model"], "th_PP-OCRv5_mobile_rec")

    def test_legacy_data_wrapper_is_supported_temporarily(self) -> None:
        with patch.dict("os.environ", {"LAYOUT_MODEL_URL": "https://layout.example"}, clear=False), patch(
            "urllib.request.urlopen",
            return_value=_FakeHttpResponse({"success": True, "model": "legacy", "data": {"boxes": []}}),
        ):
            result = _post_predict(ModelRuntimeKind.LAYOUT, {"image": "data:image/png;base64,AA=="})

        self.assertEqual(result, {"boxes": []})

    def test_table_raw_http_response_is_processed_by_backend_table_logic(self) -> None:
        payload = {
            "success": True,
            "model": "SLANeXt_wired",
            "result": {
                "raw_output": [
                    {
                        "html": "<table><tr><td>A</td><td>B</td></tr><tr><td>1</td><td>2</td></tr></table>",
                        "structure_model": "SLANeXt_wired",
                        "score": 0.96,
                    }
                ]
            },
        }
        image = np.zeros((80, 160, 3), dtype=np.uint8)

        with patch.dict("os.environ", {"TABLE_MODEL_URL": "https://table.example"}, clear=False), patch(
            "urllib.request.urlopen",
            return_value=_FakeHttpResponse(payload),
        ), patch("app.table_recognition_v2_adapter._load_table_model") as load_local:
            result = recognize_table_v2(image)

        load_local.assert_not_called()
        self.assertEqual(result["table_rows"], [["A", "B"], ["1", "2"]])
        self.assertEqual(result["table_selected_method"], "slanext")
        self.assertIn("quality", result["table_debug"])

    def test_table_runtime_result_list_matches_local_model_predict_shape(self) -> None:
        payload = {
            "success": True,
            "model": "SLANeXt_wired",
            "result": [
                {
                    "html": "<table><tr><td>A</td><td>B</td></tr><tr><td>1</td><td>2</td></tr></table>",
                    "structure_model": "SLANeXt_wired",
                    "score": 0.96,
                }
            ],
        }
        image = np.zeros((80, 160, 3), dtype=np.uint8)

        with patch.dict("os.environ", {"TABLE_MODEL_URL": "https://table.example"}, clear=False), patch(
            "urllib.request.urlopen",
            return_value=_FakeHttpResponse(payload),
        ), patch("app.table_recognition_v2_adapter._load_table_model") as load_local:
            result = recognize_table_v2(image)

        load_local.assert_not_called()
        self.assertEqual(result["table_rows"], [["A", "B"], ["1", "2"]])
        self.assertEqual(result["table_debug"]["source_structure_model"], "SLANeXt_wired")

    def test_table_remote_structure_uses_backend_ocr_core_for_cell_text(self) -> None:
        payload = {
            "success": True,
            "model": "SLANeXt_wired",
            "result": {
                "raw_output": [
                    {
                        "table_structured": {
                            "rows": [["Name", "Amount"]],
                            "cells": [
                                {"row": 0, "col": 0, "text": "Name", "bbox": {"x": 0, "y": 0, "width": 80, "height": 24}},
                                {"row": 0, "col": 1, "text": "Amount", "bbox": {"x": 80, "y": 0, "width": 80, "height": 24}},
                            ],
                            "headerRowCount": 1,
                        },
                        "structure_model": "SLANeXt_wired",
                        "score": 0.96,
                    }
                ]
            },
        }
        image = np.zeros((40, 180, 3), dtype=np.uint8)

        with patch.dict("os.environ", {"TABLE_MODEL_URL": "https://table.example"}, clear=False), patch(
            "urllib.request.urlopen",
            return_value=_FakeHttpResponse(payload),
        ), patch(
            "app.table_recognition_v2_adapter._recognize_text_crops_with_core",
            return_value=(
                [
                    {"text": "ชื่อสินค้า", "confidence": 0.94},
                    {"text": "จำนวน", "confidence": 0.93},
                ],
                {"ocr_core": "recognize_text_roi", "crop_count": 2},
            ),
        ) as ocr_core:
            result = recognize_table_v2(image)

        ocr_core.assert_called()
        self.assertEqual(result["table_rows"], [["ชื่อสินค้า", "จำนวน"]])
        self.assertEqual(result["table_structured"]["cells"][0]["assignmentSource"], "backend_ocr_core")
        self.assertEqual(
            result["table_debug"]["backend_ocr_core"]["mapping_method"],
            "slanext_cell_bbox_to_backend_ocr_core",
        )


if __name__ == "__main__":
    unittest.main()

