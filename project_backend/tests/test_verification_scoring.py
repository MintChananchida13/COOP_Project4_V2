import unittest

from app.services import VerificationService


class VerificationScoringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = VerificationService()

    def test_short_substring_does_not_pass_contains_match(self) -> None:
        result = self.service._score_match(
            expected_text="Thai National ID Card",
            actual_text="i",
            match_type="contains",
            ocr_confidence=0.99,
            verification_threshold=0.70,
        )

        self.assertFalse(result["passed"])
        self.assertLess(result["text_similarity_score"], 0.25)
        self.assertEqual(result["field_score"], 0.0)
        self.assertEqual(result["failure_reason"], "low_text_similarity")

    def test_near_full_substring_still_scores_as_partial_ocr_match(self) -> None:
        result = self.service._score_match(
            expected_text="PASSPORT",
            actual_text="assport",
            match_type="contains",
            ocr_confidence=0.69,
            verification_threshold=0.70,
        )

        self.assertTrue(result["passed"])
        self.assertGreaterEqual(result["text_similarity_score"], 0.90)
        self.assertGreaterEqual(result["field_score"], 0.90)
        self.assertEqual(result["text_match_score"], result["field_score"])

    def test_text_score_stays_continuous_when_below_threshold(self) -> None:
        result = self.service._score_match(
            expected_text="PASSPORT",
            actual_text="assport",
            match_type="contains",
            ocr_confidence=0.69,
            verification_threshold=0.95,
        )

        self.assertFalse(result["passed"])
        self.assertGreater(result["field_score"], 0.0)
        self.assertLess(result["field_score"], 1.0)
        self.assertEqual(result["score"], result["field_score"])
        self.assertEqual(result["text_match_score"], result["field_score"])
        self.assertEqual(result["failure_reason"], "below_threshold")

    def test_image_anchor_errors_do_not_drop_other_anchors(self) -> None:
        fields = [
            {
                "id": f"anchor_{index}",
                "template_id": "template_1",
                "page_number": 1,
                "field_name": f"anchor_{index}",
                "display_label": f"Anchor {index}",
                "data_type": "image",
                "use_for_verification": True,
                "required_for_verification": False,
                "verification_weight": 1.0,
                "image_category": "signature",
                "roi_padding": 0,
                "roi": {"page_number": 1, "x_ratio": 0.1, "y_ratio": 0.1, "width_ratio": 0.2, "height_ratio": 0.2},
            }
            for index in range(1, 5)
        ]
        self.service.load_verification_fields = lambda template_id: fields  # type: ignore[method-assign]

        def score_image_anchor(field, image_path):
            if field["id"] in {"anchor_3", "anchor_4"}:
                raise RuntimeError("remote image verifier failed")
            return {
                "score": 1.0,
                "field_score": 1.0,
                "evidence_score": 1.0,
                "passed": True,
                "status": "matched",
                "failure_reason": "passed",
                "verification_threshold": 0.5,
                "margin_threshold": 0.0,
                "reference_crop_preview_data_url": None,
                "current_crop_preview_data_url": None,
                "siglip_similarity_score": 1.0,
                "image_category_score": 1.0,
                "raw_logit": 0.0,
                "raw_pair_score": 0.0,
                "relative_percentage": 100.0,
                "image_category": "signature",
                "image_category_label": "Signature",
                "image_category_prompt": "",
                "predicted_image_category": "signature",
                "predicted_image_category_label": "Signature",
                "predicted_image_category_prompt": "",
                "siglip_target_rank": 1,
                "siglip_score_margin": 1.0,
                "siglip_labels": [],
                "siglip_ui_percentages": [],
            }

        self.service._score_image_anchor = score_image_anchor  # type: ignore[method-assign]

        result = self.service.verify_template("template_1", {1: "missing-but-not-used.png"})

        self.assertEqual(len(result["checked_fields"]), 4)
        self.assertEqual(sum(1 for item in result["checked_fields"] if item["passed"]), 2)
        self.assertEqual(sum(1 for item in result["checked_fields"] if not item["passed"]), 2)
        self.assertTrue(all(item["anchor_type"] == "image" for item in result["checked_fields"]))
        self.assertTrue(
            all(
                item["failure_reason"].startswith("image_verification_error:")
                for item in result["checked_fields"]
                if not item["passed"]
            )
        )


if __name__ == "__main__":
    unittest.main()
