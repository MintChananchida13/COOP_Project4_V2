import unittest

from app.layout_signature_service import build_layout_signature, compare_layout_signatures
from app.services import DecisionService


def _layout(regions, width=1000, height=500):
    return {
        "engine": "paddleocr",
        "model": "PP-DocLayoutV3",
        "image_width": width,
        "image_height": height,
        "regions": regions,
    }


def _region(label, x, y, width, height, confidence=0.9):
    return {
        "type": label,
        "confidence": confidence,
        "roi": {
            "x_ratio": x,
            "y_ratio": y,
            "width_ratio": width,
            "height_ratio": height,
        },
    }


class LayoutSignatureServiceTest(unittest.TestCase):
    def test_build_signature_contains_counts_area_and_grid_features(self) -> None:
        signature = build_layout_signature(
            _layout(
                [
                    _region("text", 0.10, 0.10, 0.30, 0.08),
                    _region("table", 0.10, 0.35, 0.70, 0.30),
                    _region("image", 0.72, 0.08, 0.18, 0.20),
                ],
                width=1200,
                height=800,
            )
        )

        self.assertEqual(signature["version"], "layout-signature-v1")
        self.assertEqual(signature["page_aspect_ratio"], 1.5)
        self.assertEqual(signature["label_counts"]["text"], 1)
        self.assertEqual(signature["label_counts"]["table"], 1)
        self.assertEqual(signature["label_counts"]["image"], 1)
        self.assertGreater(signature["area_by_label"]["table"], signature["area_by_label"]["text"])
        self.assertEqual(len(signature["grid_area"]["text"]), 16)

    def test_same_layout_ranks_above_different_layout(self) -> None:
        query = build_layout_signature(
            _layout(
                [
                    _region("text", 0.10, 0.10, 0.28, 0.08),
                    _region("table", 0.12, 0.40, 0.70, 0.28),
                ]
            )
        )
        similar = build_layout_signature(
            _layout(
                [
                    _region("text", 0.11, 0.11, 0.27, 0.08),
                    _region("table", 0.12, 0.39, 0.69, 0.29),
                ]
            )
        )
        different = build_layout_signature(
            _layout(
                [
                    _region("image", 0.70, 0.70, 0.20, 0.20),
                    _region("image", 0.10, 0.72, 0.20, 0.18),
                ]
            )
        )

        similar_score = compare_layout_signatures(query, similar)["score"]
        different_score = compare_layout_signatures(query, different)["score"]

        self.assertGreater(similar_score, 0.9)
        self.assertLess(different_score, 0.75)
        self.assertGreater(similar_score, different_score)

    def test_reject_threshold_for_dissimilar_layout(self) -> None:
        query = build_layout_signature(_layout([_region("text", 0.10, 0.10, 0.30, 0.08)]))
        candidate = build_layout_signature(
            _layout(
                [
                    _region("image", 0.60, 0.60, 0.30, 0.25),
                    _region("image", 0.15, 0.65, 0.20, 0.20),
                ]
            )
        )

        score = compare_layout_signatures(query, candidate)["score"]

        self.assertLess(score, 0.75)

    def test_image_anchor_score_changes_decision_for_similar_layout(self) -> None:
        service = DecisionService()
        high_image_anchor = service.decide_candidate(
            0.86,
            {
                "status": "verified",
                "passed": True,
                "required_passed": True,
                "score": 0.9,
                "text_anchor_score": 0.9,
                "image_anchor_score": 0.95,
                "checked_fields": [{"anchor_type": "image", "score": 0.95, "passed": True}],
            },
            0.8,
        )
        low_image_anchor = service.decide_candidate(
            0.86,
            {
                "status": "verified",
                "passed": True,
                "required_passed": True,
                "score": 0.9,
                "text_anchor_score": 0.9,
                "image_anchor_score": 0.1,
                "checked_fields": [{"anchor_type": "image", "score": 0.1, "passed": True}],
            },
            0.8,
        )

        self.assertTrue(high_image_anchor["final_passed"])
        self.assertFalse(low_image_anchor["final_passed"])
        self.assertGreater(high_image_anchor["final_score"], low_image_anchor["final_score"])


if __name__ == "__main__":
    unittest.main()
