import unittest
from unittest.mock import patch

import warmup_models


class WarmupTests(unittest.TestCase):
    def test_all_registry_models_and_core_models_attempted_after_failure(self):
        registry = {"models": {"detection": [{"version": "v6", "model_id": "default"}, {"version": "v6", "model_id": "ft"}],
                               "recognition": [{"version": "v5", "model_id": "custom"}]}}
        calls = []

        def request(path, **kwargs):
            calls.append(path)
            if path == "/api/v1/ocr-models":
                return 200, registry, 1
            if "model_id=ft" in path:
                return 503, {"detail": "weights missing"}, 2
            return 200, {"success": True}, 3

        with patch.object(warmup_models, "load_secrets", return_value={"MODEL_GATEWAY_API_KEY": "secret"}), \
                patch.object(warmup_models, "request", side_effect=request), \
                patch.object(warmup_models, "image_url", return_value="image"), \
                patch.object(warmup_models, "layout_image"), patch.object(warmup_models, "table_image"), \
                patch.object(warmup_models, "text_image"):
            outcomes = warmup_models.warmup()
        self.assertEqual(len(outcomes), 6)
        self.assertEqual(sum(not item["ready"] for item in outcomes), 1)
        self.assertEqual(outcomes[2]["error"], "weights missing")
        self.assertTrue(any("image-verifications" in path for path in calls))
        self.assertTrue(any("table-model-results" in path for path in calls))


if __name__ == "__main__":
    unittest.main()
