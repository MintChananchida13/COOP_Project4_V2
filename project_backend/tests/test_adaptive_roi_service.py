import unittest

from app.adaptive_roi_service import AdaptiveRoiService


class AdaptiveRoiServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = AdaptiveRoiService()

    def test_groups_words_on_same_line(self) -> None:
        words = [
            {"text": "John", "confidence": 0.9, "bbox": {"x_ratio": 0.10, "y_ratio": 0.10, "width_ratio": 0.08, "height_ratio": 0.03}},
            {"text": "Doe", "confidence": 0.88, "bbox": {"x_ratio": 0.20, "y_ratio": 0.105, "width_ratio": 0.07, "height_ratio": 0.03}},
            {"text": "Address", "confidence": 0.92, "bbox": {"x_ratio": 0.10, "y_ratio": 0.20, "width_ratio": 0.12, "height_ratio": 0.03}},
        ]

        groups = self.service.group_word_boxes(words)

        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["word_count"], 2)
        self.assertEqual(groups[0]["text"], "John Doe")
        self.assertEqual(groups[1]["word_count"], 1)

    def test_refine_field_returns_adaptive_roi_for_valid_words(self) -> None:
        projected_roi = {"page_number": 1, "x_ratio": 0.10, "y_ratio": 0.10, "width_ratio": 0.30, "height_ratio": 0.08}
        words = [
            {"text": "John", "confidence": 0.9, "bbox": {"x_ratio": 0.12, "y_ratio": 0.12, "width_ratio": 0.08, "height_ratio": 0.03}},
            {"text": "Doe", "confidence": 0.88, "bbox": {"x_ratio": 0.21, "y_ratio": 0.122, "width_ratio": 0.07, "height_ratio": 0.03}},
        ]

        result = self.service.refine_field(projected_roi, words)

        self.assertEqual(result["status"], "refined")
        self.assertGreater(result["adaptive_confidence"], 0.0)
        self.assertEqual(result["word_count"], 2)
        self.assertTrue(result["validation_result"]["passed"])

    def test_refine_field_tries_multiple_word_groups_before_fallback(self) -> None:
        projected_roi = {"page_number": 1, "x_ratio": 0.10, "y_ratio": 0.10, "width_ratio": 0.18, "height_ratio": 0.06}
        words = [
            {"text": "Wrong", "confidence": 0.95, "bbox": {"x_ratio": 0.05, "y_ratio": 0.15, "width_ratio": 0.40, "height_ratio": 0.025}},
            {"text": "John", "confidence": 0.86, "bbox": {"x_ratio": 0.12, "y_ratio": 0.115, "width_ratio": 0.07, "height_ratio": 0.025}},
            {"text": "Doe", "confidence": 0.84, "bbox": {"x_ratio": 0.20, "y_ratio": 0.116, "width_ratio": 0.06, "height_ratio": 0.025}},
        ]

        result = self.service.refine_field(projected_roi, words)

        self.assertEqual(result["status"], "refined")
        self.assertEqual(result["word_count"], 2)
        self.assertEqual(result["adaptive_roi"]["x_ratio"], 0.12)
        self.assertGreaterEqual(len(result["ranked_word_groups"]), 2)

    def test_bbox_to_ratio_clips_right_and_bottom_edges(self) -> None:
        ratio = self.service.bbox_to_ratio({"x": 95, "y": 90, "width": 20, "height": 30}, 100, 100)

        self.assertEqual(ratio["x_ratio"], 0.95)
        self.assertEqual(ratio["y_ratio"], 0.9)
        self.assertEqual(ratio["width_ratio"], 0.05)
        self.assertEqual(ratio["height_ratio"], 0.1)

    def test_refine_field_fallback_when_no_words_in_search_region(self) -> None:
        projected_roi = {"page_number": 1, "x_ratio": 0.10, "y_ratio": 0.10, "width_ratio": 0.20, "height_ratio": 0.06}
        words = [
            {"text": "Far", "confidence": 0.9, "bbox": {"x_ratio": 0.80, "y_ratio": 0.80, "width_ratio": 0.06, "height_ratio": 0.03}},
        ]

        result = self.service.refine_field(projected_roi, words)

        self.assertEqual(result["status"], "fallback")
        self.assertEqual(result["fallback_reason"], "no_word_boxes_in_search_region")
        self.assertEqual(result["adaptive_roi"], projected_roi)

    def test_refine_field_prefers_box_with_better_position_size_and_overlap(self) -> None:
        projected_roi = {"page_number": 1, "x_ratio": 0.30, "y_ratio": 0.30, "width_ratio": 0.20, "height_ratio": 0.06}
        words = [
            {"text": "", "confidence": 0.95, "bbox": {"x_ratio": 0.11, "y_ratio": 0.30, "width_ratio": 0.04, "height_ratio": 0.03}},
            {"text": "", "confidence": 0.70, "bbox": {"x_ratio": 0.31, "y_ratio": 0.31, "width_ratio": 0.18, "height_ratio": 0.045}},
        ]

        result = self.service.refine_field(projected_roi, words)

        self.assertEqual(result["status"], "refined")
        self.assertAlmostEqual(result["adaptive_roi"]["x_ratio"], 0.31)
        validation = result["validation_result"]
        self.assertGreater(validation["position_similarity"], 0.8)
        self.assertGreater(validation["size_similarity"], 0.7)
        self.assertGreater(validation["overlap_score"], 0.7)


if __name__ == "__main__":
    unittest.main()
