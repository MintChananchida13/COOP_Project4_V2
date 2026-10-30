import unittest
from unittest.mock import patch

from app.api.schemas import OcrModelPayload
from app.business.services import GlobalSettingsService, DEFAULT_OCR_MODEL_SETTINGS


class OcrModelRegistrationTests(unittest.TestCase):
    def test_create_registers_runtime_model_and_builds_paths(self):
        import copy
        service = GlobalSettingsService()
        settings = copy.deepcopy(DEFAULT_OCR_MODEL_SETTINGS)
        payload = OcrModelPayload(display_name="Fine-tuned", single_api_path="/api/v1/text-recognitions",
                                  batch_api_path="/api/v1/text-recognition-batches", model_name="th_PP-OCRv5_mobile_rec",
                                  model_version="v5", model_dir="/weights/new", runtime_model_id="thai_new")
        with patch.object(service, "get_ocr_model_settings", return_value={"ocr_models": settings}), \
                patch.object(service, "_save_setting") as save, \
                patch("app.core.model_runtime_client.register_ocr_registry_model") as register:
            result = service.upsert_ocr_model("text_recognition", payload)
        self.assertEqual(result["model"]["id"], "thai_new")
        self.assertIn("version=v5&model_id=thai_new", result["model"]["single_api_path"])
        self.assertEqual(result["ocr_models"]["active"]["text_recognition"], "thai_ocr_v5_mobile")
        self.assertEqual(register.call_args.args[1]["model_dir"], "/weights/new")
        save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
