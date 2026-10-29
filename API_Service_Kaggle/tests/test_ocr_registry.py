import base64
import unittest
import sys
import types
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from fastapi.testclient import TestClient

from app import main
from app.models import ModelManager, local_weights_path, resolve_ocr


class RegistryTests(unittest.TestCase):
    def test_versions_and_unknown_models(self):
        self.assertEqual(resolve_ocr("det", "v6", "thai_ft_v2")["model_name"], "PP-OCRv6_medium_det")
        self.assertEqual(resolve_ocr("rec", "v5", "thai_ft_v3")["model_name"], "th_PP-OCRv5_mobile_rec")
        self.assertEqual(resolve_ocr("rec", "v6", "thai_ft_v1")["model_name"], "PP-OCRv6_medium_rec")
        with self.assertRaises(ValueError):
            resolve_ocr("rec", "v5", "thai_ft_v1")

    def test_missing_and_incomplete_weights(self):
        entry = {"model_dir": "/weights/test"}
        with patch.object(Path, "is_dir", return_value=False):
            with self.assertRaises(FileNotFoundError):
                local_weights_path(entry)
        with patch.object(Path, "is_dir", return_value=True), patch.object(Path, "is_file", return_value=False):
            with self.assertRaisesRegex(FileNotFoundError, "inference.yml"):
                local_weights_path(entry)
        with patch.object(Path, "is_dir", return_value=True), patch.object(Path, "is_file", return_value=True):
            self.assertEqual(local_weights_path(entry), Path("/weights/test"))

    def test_single_and_batch_select_correct_entry_without_changing_default(self):
        ok, image = cv2.imencode(".png", np.zeros((8, 8, 3), dtype=np.uint8))
        self.assertTrue(ok)
        data = "data:image/png;base64," + base64.b64encode(image).decode()
        observed = []
        def fake_run(kind, operation, **kwargs):
            observed.append((kind, kwargs.get("ocr_entry")))
            return {"success": True, "model": kind, "result": {}, "timing": {}}
        with patch.object(main, "settings", replace(main.settings, api_key="secret")), patch.object(main, "run", fake_run):
            client = TestClient(main.app)
            headers = {"Authorization": "Bearer secret"}
            for route, version, model_id in (("text-detections", "v6", "thai_ft_v2"), ("text-recognitions", "v5", "thai_ft_v3"), ("text-recognitions", "v6", "thai_ft_v1")):
                for batch in (False, True):
                    path = route if not batch else route.replace("detections", "detection-batches").replace("recognitions", "recognition-batches")
                    response = client.post(f"/api/v1/{path}?version={version}&model_id={model_id}", headers=headers, json={"images": [data]} if batch else {"image": data})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(observed[-1][1]["model_id"], model_id)
            self.assertEqual(client.post("/api/v1/text-recognitions", headers=headers, json={"image": data}).status_code, 200)
            self.assertIsNone(observed[-1][1])
            self.assertEqual(client.post("/api/v1/text-recognitions?version=v5&model_id=thai_ft_v1", headers=headers, json={"image": data}).status_code, 422)

    def test_cache_is_per_model_and_restart_starts_empty(self):
        manager = ModelManager()
        key = ("rec", "v5", "thai_ft_v3")
        marker = object()
        manager.ocr_models[key] = marker
        self.assertIs(manager.get_ocr("rec", resolve_ocr("rec", "v5", "thai_ft_v3"))[0], marker)
        self.assertNotIn(key, ModelManager().ocr_models)

    def test_model_load_cache_and_failure_isolation(self):
        calls = []
        def construct(**kwargs):
            calls.append(kwargs)
            return object()
        paddle = types.SimpleNamespace(set_device=lambda device: None)
        paddleocr = types.SimpleNamespace(TextDetection=construct, TextRecognition=construct)
        manager = ModelManager()
        first = resolve_ocr("rec", "v5", "thai_ft_v3")
        second = resolve_ocr("rec", "v6", "thai_ft_v1")
        with patch.dict(sys.modules, {"paddle": paddle, "paddleocr": paddleocr}), patch("app.models.local_weights_path", return_value=Path("/weights/infer")):
            model, _ = manager.get_ocr("rec", first)
            self.assertIs(manager.get_ocr("rec", first)[0], model)
            self.assertIsNot(manager.get_ocr("rec", second)[0], model)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0]["model_dir"], str(Path("/weights/infer")))
        with patch("app.models.local_weights_path", side_effect=FileNotFoundError("missing")):
            with self.assertRaises(FileNotFoundError):
                manager.get_ocr("det", resolve_ocr("det", "v6", "thai_ft_v2"))
        self.assertIs(manager.ocr_models[("rec", "v5", "thai_ft_v3")], model)


if __name__ == "__main__":
    unittest.main()
