import unittest
from unittest.mock import patch

from app.detection_service import _lightweight_candidate_from_result


class DetectionIncludeDraftCandidateTest(unittest.TestCase):
    def test_included_draft_candidate_is_kept_for_ranking_when_layout_is_below_threshold(self) -> None:
        result = {
            "vector_id": "layout_draft_template_1",
            "score": 0.31,
            "layout_score": 0.31,
            "metadata": {
                "template_id": "draft_template",
                "template_name": "Draft Template",
                "template_status": "draft",
                "page_count": 1,
                "field_count": 0,
                "final_confidence_threshold": 0.75,
                "layout_weight": 0.4,
                "text_anchor_weight": 0.3,
                "image_anchor_weight": 0.3,
            },
        }

        with patch("app.detection_service._fetch_template", return_value={"id": "draft_template", "name": "Draft Template", "status": "draft"}):
            candidate = _lightweight_candidate_from_result(result, include_template_id="draft_template")

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["template_id"], "draft_template")
        self.assertEqual(candidate["template_status"], "draft")
        self.assertFalse(candidate["final_passed"])

    def test_unincluded_draft_candidate_is_still_hidden_from_normal_detection(self) -> None:
        result = {
            "vector_id": "layout_draft_template_1",
            "score": 0.31,
            "metadata": {"template_id": "draft_template", "template_status": "draft"},
        }

        with patch("app.detection_service._fetch_template", return_value={"id": "draft_template", "name": "Draft Template", "status": "draft"}):
            candidate = _lightweight_candidate_from_result(result)

        self.assertIsNone(candidate)


if __name__ == "__main__":
    unittest.main()
