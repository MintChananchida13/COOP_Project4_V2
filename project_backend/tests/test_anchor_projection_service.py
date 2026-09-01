import unittest

try:
    from app.anchor_projection_service import AnchorProjectionService
except ModuleNotFoundError as error:
    if error.name == "cv2":
        AnchorProjectionService = None
    else:
        raise


@unittest.skipIf(AnchorProjectionService is None, "OpenCV is not installed in this Python environment")
class AnchorProjectionServiceTest(unittest.TestCase):
    def test_project_field_clips_slightly_outside_roi_without_rejecting(self) -> None:
        service = AnchorProjectionService()
        field = {
            "id": "field_1",
            "field_name": "name",
            "display_label": "Name",
            "page_number": 1,
            "roi": {
                "page_number": 1,
                "x_ratio": 0.92,
                "y_ratio": 0.20,
                "width_ratio": 0.12,
                "height_ratio": 0.08,
            },
        }

        projected = service._project_field(field, "ratio_fallback", None)

        self.assertTrue(projected["projection_valid"])
        self.assertTrue(projected["projection_validation_result"]["passed"])
        self.assertTrue(projected["projection_validation_result"]["outside_image"])
        self.assertEqual(projected["projected_roi"]["x_ratio"], 0.92)
        self.assertAlmostEqual(projected["projected_roi"]["width_ratio"], 0.08)
        self.assertIn("outside_image_clipped", projected["projection_validation_result"]["warnings"])

    def test_clustered_anchor_points_fallback_to_translation_without_collapsing_roi(self) -> None:
        service = AnchorProjectionService()
        pairs = [
            {"expected_point": (0.2, 0.2), "actual_point": (0.5, 0.5)},
            {"expected_point": (0.4, 0.2), "actual_point": (0.5005, 0.5005)},
        ]
        method, matrix, debug = service._estimate_transform(pairs)
        field = {
            "id": "field_2",
            "field_name": "name",
            "display_label": "Name",
            "page_number": 1,
            "roi": {
                "page_number": 1,
                "x_ratio": 0.20,
                "y_ratio": 0.30,
                "width_ratio": 0.20,
                "height_ratio": 0.08,
            },
        }

        projected = service._project_field(field, method, matrix)

        self.assertEqual(method, "translation")
        self.assertEqual(debug["fallback_reason"], "anchor_points_too_clustered_for_partial_affine")
        self.assertTrue(projected["projection_valid"])
        self.assertGreater(projected["projected_roi"]["width_ratio"], 0.0)
        self.assertGreater(projected["projected_roi"]["height_ratio"], 0.0)

    def test_degenerate_affine_is_rejected(self) -> None:
        service = AnchorProjectionService()
        degenerate = __import__("numpy").array([[0.0, 0.0, 0.5], [0.0, 0.0, 0.5]], dtype="float32")

        self.assertFalse(service._is_valid_affine(degenerate))


if __name__ == "__main__":
    unittest.main()
