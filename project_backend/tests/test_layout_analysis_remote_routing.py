import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from app.layout_analysis_service import LayoutAnalysisUnavailableError, analyze_layout, detect_text_boxes
from app.model_runtime_client import ModelRuntimeUnavailableError


class LayoutAnalysisRemoteRoutingTest(unittest.TestCase):
    def test_analyze_layout_uses_remote_without_local_text_detection(self) -> None:
        image = np.zeros((20, 20, 3), dtype=np.uint8)
        remote_payload = {
            "engine": "remote",
            "model": "runtime",
            "image_width": 20,
            "image_height": 20,
            "regions": [],
        }

        with patch.dict("os.environ", {"LAYOUT_MODEL_URL": "https://layout.example", "TEXT_DETECTION_MODEL_URL": "https://text.example"}, clear=False), patch(
            "app.layout_analysis_service.remote_analyze_layout",
            return_value={"regions": []},
        ) as remote, patch("app.layout_analysis_service._load_text_detector") as load_text, patch(
            "app.layout_analysis_service._run_text_detection"
        ) as run_text, patch("app.layout_analysis_service.remote_detect_text_boxes", return_value={"regions": []}):
            result = analyze_layout(image)

        self.assertEqual(result["regions"], [])
        remote.assert_called_once()
        load_text.assert_not_called()
        run_text.assert_not_called()

    def test_detect_text_boxes_uses_remote_without_local_text_detection(self) -> None:
        image = np.zeros((20, 20, 3), dtype=np.uint8)
        remote_payload = {
            "engine": "remote",
            "model": "runtime-text-det",
            "image_width": 20,
            "image_height": 20,
            "regions": [],
        }

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
            image_path = temp_file.name
        try:
            cv2.imwrite(image_path, image)
            with patch.dict("os.environ", {"TEXT_DETECTION_MODEL_URL": "https://text.example"}, clear=False), patch(
                "app.layout_analysis_service.remote_detect_text_boxes",
                return_value=remote_payload,
            ) as remote, patch("app.layout_analysis_service._load_text_detector") as load_text, patch(
                "app.layout_analysis_service._run_text_detection"
            ) as run_text:
                result = detect_text_boxes(image_path)
        finally:
            Path(image_path).unlink(missing_ok=True)

        self.assertEqual(result["regions"], remote_payload["regions"])
        remote.assert_called_once_with(image_path)
        load_text.assert_not_called()
        run_text.assert_not_called()

    def test_remote_text_detection_error_is_raised_without_local_fallback(self) -> None:
        image = np.zeros((20, 20, 3), dtype=np.uint8)

        with patch.dict("os.environ", {"LAYOUT_MODEL_URL": "https://layout.example", "TEXT_DETECTION_MODEL_URL": "https://text.example"}, clear=False), patch(
            "app.layout_analysis_service.remote_analyze_layout",
            side_effect=ModelRuntimeUnavailableError("remote text detection failed"),
        ), patch("app.layout_analysis_service._load_text_detector") as load_text, patch(
            "app.layout_analysis_service._run_text_detection"
        ) as run_text:
            with self.assertRaisesRegex(LayoutAnalysisUnavailableError, "remote text detection failed"):
                analyze_layout(image)

        load_text.assert_not_called()
        run_text.assert_not_called()

    def test_remote_text_detection_none_is_clear_error(self) -> None:
        image = np.zeros((20, 20, 3), dtype=np.uint8)

        with patch.dict("os.environ", {"LAYOUT_MODEL_URL": "https://layout.example", "TEXT_DETECTION_MODEL_URL": "https://text.example"}, clear=False), patch(
            "app.layout_analysis_service.remote_analyze_layout",
            return_value=None,
        ), patch("app.layout_analysis_service._load_text_detector") as load_text:
            with patch("app.layout_analysis_service.remote_detect_text_boxes", return_value={"items": []}):
                with self.assertRaisesRegex(LayoutAnalysisUnavailableError, "Layout runtime returned an invalid response."):
                    analyze_layout(image)

        load_text.assert_not_called()

    def _analyze_from_remote_raw(self, image: np.ndarray, raw_items: list[dict], expand: bool = True) -> dict:
        with patch.dict(
            "os.environ",
            {"LAYOUT_MODEL_URL": "https://layout.example", "TEXT_DETECTION_MODEL_URL": "https://text.example"},
            clear=False,
        ), patch("app.layout_analysis_service.remote_analyze_layout", return_value={"items": raw_items}), patch(
            "app.layout_analysis_service.remote_detect_text_boxes",
            return_value={"items": []},
        ):
            return analyze_layout(image, expand_text_rois=expand, auto_roi_mode="text_line")

    def _detect_from_remote_raw(self, image_path: str, raw_items: list[dict]) -> dict:
        with patch.dict("os.environ", {"TEXT_DETECTION_MODEL_URL": "https://text.example"}, clear=False), patch(
            "app.layout_analysis_service.remote_detect_text_boxes",
            return_value={"items": raw_items},
        ):
            return detect_text_boxes(image_path)

    def test_auto_roi_expands_table_bbox_slightly_to_keep_edges(self) -> None:
        image = np.zeros((100, 200, 3), dtype=np.uint8)
        raw_items = [
            {"bbox": [20, 10, 120, 60], "label": "table", "score": 0.95},
            {"bbox": [150, 20, 170, 30], "label": "text", "score": 0.9},
        ]

        result = self._analyze_from_remote_raw(image, raw_items)

        table_region = next(region for region in result["regions"] if region["type"] == "table")
        text_region = next(region for region in result["regions"] if region["type"] == "text")

        self.assertEqual(table_region["roi"]["x_ratio"], 18 / 200)
        self.assertEqual(table_region["roi"]["y_ratio"], 8 / 100)
        self.assertEqual(table_region["roi"]["width_ratio"], 104 / 200)
        self.assertEqual(table_region["roi"]["height_ratio"], 54 / 100)
        self.assertTrue(table_region["roi_expansion"]["enabled"])
        self.assertEqual(table_region["roi_expansion"]["reason"], "table_edge_guard_padding")
        self.assertEqual(table_region["roi_expansion"]["padding"]["left"], 2)
        self.assertLess(text_region["roi"]["x_ratio"], 150 / 200)

    def test_auto_roi_skips_image_region_when_text_is_inside(self) -> None:
        image = np.zeros((100, 200, 3), dtype=np.uint8)
        raw_items = [
            {"bbox": [20, 10, 120, 80], "label": "image", "score": 0.95},
            {"bbox": [40, 30, 70, 45], "label": "text", "score": 0.9},
        ]

        result = self._analyze_from_remote_raw(image, raw_items)

        self.assertEqual([region["type"] for region in result["regions"]], ["text"])

    def test_auto_roi_keeps_image_region_when_no_text_is_inside(self) -> None:
        image = np.zeros((100, 200, 3), dtype=np.uint8)
        raw_items = [
            {"bbox": [20, 10, 120, 80], "label": "image", "score": 0.95},
            {"bbox": [150, 30, 180, 45], "label": "text", "score": 0.9},
        ]

        result = self._analyze_from_remote_raw(image, raw_items)

        self.assertEqual([region["type"] for region in result["regions"]], ["image", "text"])

    def test_auto_roi_drops_nested_text_region_inside_larger_text_region(self) -> None:
        image = np.zeros((160, 240, 3), dtype=np.uint8)
        raw_items = [
            {"bbox": [20, 20, 200, 70], "label": "text", "score": 0.95},
            {"bbox": [50, 32, 90, 48], "label": "text", "score": 0.9},
            {"bbox": [20, 90, 200, 130], "label": "text", "score": 0.92},
        ]

        result = self._analyze_from_remote_raw(image, raw_items)

        self.assertEqual(len(result["regions"]), 2)
        self.assertTrue(all(region["type"] == "text" for region in result["regions"]))

    def test_auto_roi_drops_tiny_text_fragment_like_diacritic(self) -> None:
        image = np.zeros((160, 240, 3), dtype=np.uint8)
        raw_items = [
            {"bbox": [20, 20, 200, 42], "label": "text", "score": 0.95},
            {"bbox": [20, 55, 200, 77], "label": "text", "score": 0.94},
            {"bbox": [86, 50, 92, 56], "label": "text", "score": 0.91},
        ]

        result = self._analyze_from_remote_raw(image, raw_items)

        self.assertEqual(len(result["regions"]), 2)
        heights = [region["roi"]["height_ratio"] for region in result["regions"]]
        self.assertTrue(all(height > 0.08 for height in heights))


if __name__ == "__main__":
    unittest.main()
