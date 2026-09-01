import os
from uuid import uuid4
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.image_verification_category_service import (
    ImageVerificationCategoryService,
    list_image_verification_categories,
)
from app.services import DecisionService, VerificationService
from app.siglip_image_verification_adapter import (
    SIGLIP_SCORING_VERSION,
    verify_image_category,
    verify_image_category_from_logits,
)


def _category(
    value: str,
    prompt: str,
    enabled: bool = True,
):
    return {
        "value": value,
        "label": value.replace("_", " ").title(),
        "prompt": prompt,
        "match_threshold": 0.60,
        "margin_threshold": 0.10,
        "evidence_temperature": 1.0,
        "enabled": enabled,
    }


class SiglipImageVerificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.categories = [
            _category("qr_code", "QR code"),
            _category("barcode", "barcode"),
            _category("portrait", "portrait photograph"),
        ]

    def test_expected_top1_returns_binary_one(self) -> None:
        result = verify_image_category_from_logits([2.0, 0.0, -1.0], "qr_code", self.categories)

        self.assertTrue(result.passed)
        self.assertEqual(result.status, "matched")
        self.assertEqual(result.failure_reason, "passed")
        self.assertEqual(result.score, 1.0)
        self.assertEqual(result.evidence_score, 1.0)
        self.assertEqual(result.predicted_category, "qr_code")
        self.assertEqual(result.target_rank, 1)
        self.assertEqual(result.scoring_version, SIGLIP_SCORING_VERSION)

    def test_expected_not_top1_returns_binary_zero(self) -> None:
        result = verify_image_category_from_logits([0.2, 2.0, -1.0], "qr_code", self.categories)

        self.assertFalse(result.passed)
        self.assertEqual(result.status, "mismatched")
        self.assertEqual(result.failure_reason, "predicted_category_mismatch")
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.evidence_score, 0.0)
        self.assertEqual(result.predicted_category, "barcode")
        self.assertEqual(result.target_rank, 2)

    def test_low_pair_score_still_passes_when_expected_is_top1(self) -> None:
        result = verify_image_category_from_logits([-10.0, -11.0, -12.0], "qr_code", self.categories)

        self.assertLess(result.raw_pair_score, 0.01)
        self.assertTrue(result.passed)
        self.assertEqual(result.score, 1.0)
        self.assertEqual(result.status, "matched")

    def test_no_other_category_is_displayed_or_used(self) -> None:
        result = verify_image_category_from_logits([2.0, 0.0, -1.0], "qr_code", self.categories)

        self.assertNotIn("other", [item["image_category"] for item in result.labels])
        self.assertNotIn("other", [item["image_category"] for item in result.ui_percentages])

    def test_disabled_not_found_and_no_active_return_zero(self) -> None:
        no_active = verify_image_category("dummy.png", "qr_code", [_category("qr_code", "QR code", enabled=False)])
        disabled_target = verify_image_category(
            "dummy.png",
            "qr_code",
            [_category("qr_code", "QR code", enabled=False), _category("barcode", "barcode")],
        )
        missing_target = verify_image_category("dummy.png", "unknown", self.categories)

        for result in (no_active, disabled_target, missing_target):
            self.assertFalse(result.passed)
            self.assertEqual(result.status, "error")
            self.assertEqual(result.score, 0.0)
            self.assertEqual(result.evidence_score, 0.0)
        self.assertEqual(no_active.failure_reason, "no_active_categories")
        self.assertEqual(disabled_target.failure_reason, "category_disabled")
        self.assertEqual(missing_target.failure_reason, "category_not_found")

    def test_relative_debug_percentages_sum_to_100_without_other(self) -> None:
        result = verify_image_category_from_logits([2.0, 0.0, -1.0], "qr_code", self.categories)

        self.assertAlmostEqual(sum(item["percentage"] for item in result.ui_percentages), 100.0, places=1)
        self.assertNotIn("other", [item["image_category"] for item in result.ui_percentages])

    def test_remote_runtime_logits_flow_matches_backend_scoring_contract(self) -> None:
        local = verify_image_category_from_logits([2.0, 0.0, -1.0], "qr_code", self.categories)
        remote_payload = {"logits": [2.0, 0.0, -1.0], "device": "kaggle-gpu"}

        with patch.dict("os.environ", {"IMAGE_VERIFICATION_MODEL_URL": "https://siglip.example"}, clear=False), patch(
            "app.siglip_image_verification_adapter.remote_verify_image_logits",
            return_value=remote_payload,
        ) as remote_api:
            remote = verify_image_category("dummy.png", "qr_code", self.categories)

        remote_api.assert_called_once()
        self.assertEqual(remote.score, local.score)
        self.assertEqual(remote.evidence_score, local.evidence_score)
        self.assertEqual(remote.status, local.status)
        self.assertEqual(remote.predicted_category, local.predicted_category)
        self.assertEqual(remote.scoring_version, local.scoring_version)
        self.assertEqual(remote.device, "kaggle-gpu")

    def test_verification_service_uses_binary_evidence_score_as_field_score(self) -> None:
        fake_result = SimpleNamespace(
            evidence_score=1.0,
            passed=True,
            status="matched",
            failure_reason="passed",
            verification_threshold=0.60,
            margin_threshold=0.10,
            model_version="mock-siglip",
            scoring_version=SIGLIP_SCORING_VERSION,
            raw_logit=-10.0,
            raw_pair_score=0.0001,
            relative_percentage=51.0,
            image_category="qr_code",
            image_category_label="QR Code",
            prompt="QR code",
            predicted_category="qr_code",
            predicted_label="QR Code",
            predicted_prompt="QR code",
            target_rank=1,
            score_margin=0.2,
            labels=[],
            ui_percentages=[],
            model_name="mock-model",
            device="test",
        )
        service = VerificationService()
        field = {
            "id": "field_1",
            "template_id": "tpl_1",
            "roi": {"x_ratio": 0.1, "y_ratio": 0.1, "width_ratio": 0.2, "height_ratio": 0.2},
            "roi_padding": 0,
            "image_category": "qr_code",
        }

        with patch("app.services._crop_anchor_roi", return_value=Path("crop.png")), patch(
            "app.services._image_path_to_data_url", return_value="data:image/png;base64,"
        ), patch("app.services._active_image_category_payloads", return_value=[self.categories[0]]), patch(
            "app.services._image_category_api",
            return_value={"value": "qr_code", "label": "QR Code", "prompt": "QR code"},
        ), patch("app.services.verify_image_category", return_value=fake_result):
            result = service._score_image_anchor(field, "query.png")

        self.assertEqual(result["field_score"], 1.0)
        self.assertEqual(result["score"], 1.0)
        self.assertEqual(result["image_category_score"], 1.0)
        self.assertEqual(result["evidence_score"], 1.0)

    def test_required_verification_still_gates_final_decision(self) -> None:
        decision = DecisionService().decide_candidate(
            0.90,
            {
                "status": "verified",
                "passed": False,
                "required_passed": False,
                "layout_passed": True,
                "score": 0.95,
                "text_anchor_score": 1.0,
                "image_anchor_score": 0.0,
                "checked_fields": [{"field_name": "anchor_1", "required": True, "passed": False}],
            },
            0.75,
        )

        self.assertFalse(decision["final_passed"])
        self.assertEqual(decision["decision_reason"], "required_verification_failed")


class ImageVerificationCategoryServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        if os.getenv("RUN_POSTGRES_INTEGRATION_TESTS") != "1":
            self.skipTest("PostgreSQL integration tests are disabled.")
        database_url = os.getenv("DATABASE_URL", "")
        if not database_url.startswith(("postgresql://", "postgres://")):
            self.skipTest("DATABASE_URL must point to PostgreSQL.")
        self.unique_suffix = uuid4().hex[:8]

    def tearDown(self) -> None:
        return None

    def test_admin_created_category_is_used_by_next_inference(self) -> None:
        service = ImageVerificationCategoryService()
        created = service.create(
            {
                "value": f"seal_{self.unique_suffix}",
                "label": "Seal",
                "prompt": "official seal",
                "enabled": True,
            }
        )["category"]
        self.assertEqual(created["value"], f"seal_{self.unique_suffix}")

        active_payload = [item.to_api() for item in list_image_verification_categories(enabled_only=True)]
        values = [item["value"] for item in active_payload]
        self.assertIn(f"seal_{self.unique_suffix}", values)

        logits = [0.0 for _ in active_payload]
        logits[values.index(f"seal_{self.unique_suffix}")] = 4.0
        result = verify_image_category_from_logits(logits, f"seal_{self.unique_suffix}", active_payload)

        self.assertTrue(result.passed)
        self.assertEqual(result.score, 1.0)
        self.assertEqual(result.predicted_category, "seal")

    def test_duplicate_value_is_rejected(self) -> None:
        service = ImageVerificationCategoryService()
        payload = {
            "value": f"seal_duplicate_{self.unique_suffix}",
            "label": "Seal",
            "prompt": "official seal",
            "enabled": True,
        }
        service.create(payload)
        with self.assertRaises(Exception):
            service.create(payload)

    def test_disabled_category_is_not_active(self) -> None:
        service = ImageVerificationCategoryService()
        value = f"seal_disabled_{self.unique_suffix}"
        service.create(
            {
                "value": value,
                "label": "Seal",
                "prompt": "official seal",
                "enabled": True,
            }
        )
        updated = service.update(value, {"enabled": False})["category"]
        self.assertFalse(updated["enabled"])

        active_values = {item.value for item in list_image_verification_categories(enabled_only=True)}
        self.assertNotIn(value, active_values)

    def test_other_cannot_be_created_as_a_category(self) -> None:
        with self.assertRaises(Exception):
            ImageVerificationCategoryService().create(
                {
                    "value": "other",
                    "label": "Other",
                    "prompt": "other",
                    "enabled": True,
                }
            )


if __name__ == "__main__":
    unittest.main()
