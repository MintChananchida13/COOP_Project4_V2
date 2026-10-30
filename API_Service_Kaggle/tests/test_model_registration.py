import unittest
from dataclasses import replace
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import main


class ModelRegistrationTests(unittest.TestCase):
    def test_registration_forwards_metadata_and_rejects_invalid_paths(self):
        payload = {"kind": "rec", "version": "v5", "model_id": "new_thai", "model_name": "th_PP-OCRv5_mobile_rec",
                   "display_name": "Thai model", "model_dir": "/workspace/API_service/weights/new/infer"}
        with patch.object(main, "settings", replace(main.settings, api_key="secret")), \
                patch.object(main, "register_ocr_model") as register:
            client = TestClient(main.app)
            headers = {"Authorization": "Bearer secret"}
            response = client.post("/api/v1/ocr-models", json=payload, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(register.call_args.args[1]["requires_local_weights"])
            self.assertEqual(register.call_args.args[1]["model_id"], "new_thai")
            bad = client.post("/api/v1/ocr-models", json={**payload, "model_dir": "/weights/../other"}, headers=headers)
            self.assertEqual(bad.status_code, 422)
            self.assertEqual(register.call_count, 1)
            self.assertEqual(client.post("/api/v1/ocr-models", json=payload).status_code, 401)

    def test_duplicate_id_reports_conflict(self):
        payload = {"kind": "det", "version": "v6", "model_id": "exists", "model_name": "PP-OCRv6_medium_det",
                   "display_name": "Existing", "model_dir": "/weights/infer"}
        with patch.object(main, "settings", replace(main.settings, api_key="secret")), \
                patch.object(main, "register_ocr_model", side_effect=ValueError("Model ID already exists")):
            result = TestClient(main.app).post("/api/v1/ocr-models", json=payload, headers={"Authorization": "Bearer secret"})
        self.assertEqual(result.status_code, 409)


if __name__ == "__main__":
    unittest.main()
