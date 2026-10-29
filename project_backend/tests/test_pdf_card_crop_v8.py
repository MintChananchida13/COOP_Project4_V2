import ast
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import cv2
import numpy as np
from app.processing import pdf_card_crop as production
from app.processing.image_normalization import ImageNormalizationService

class PdfCardCropV8Tests(unittest.TestCase):
    def image(self, color=30):
        image = np.full((1000, 800, 3), 255, np.uint8)
        cv2.rectangle(image, (160, 340), (640, 640), (color,)*3, 3)
        return image

    def test_detector_matches_reference_on_card_faint_blank_and_grid(self):
        path = Path(__file__).resolve().parents[2] / "test_pdf_crop_v8_multipage.py"
        if not path.exists():
            self.skipTest("Standalone v8 reference script is not present")
        spec = importlib.util.spec_from_file_location("reference_v8", path)
        reference = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reference)
        grid = np.full((1000, 800, 3), 255, np.uint8)
        for y in range(100, 901, 160):
            cv2.line(grid, (80, y), (720, y), (0,)*3, 3)
        for x in (80, 400, 720):
            cv2.line(grid, (x, 100), (x, 900), (0,)*3, 3)
        for image in (self.image(), self.image(235), np.full_like(grid, 255), grid):
            actual = production.detect(image, production.DEFAULT_CONFIG)
            expected = reference.detect(image, production.DEFAULT_CONFIG)
            self.assertEqual(actual[:2], expected[:2])
            self.assertEqual(production.ruled_form_evidence(image, production.DEFAULT_CONFIG),
                             reference.ruled_form_evidence(image, production.DEFAULT_CONFIG))

    def test_multipage_skips_reading_and_all_detection(self):
        with patch("cv2.imread") as read, patch.object(production, "detect") as detect:
            result = ImageNormalizationService().detect_pdf_subdocument_boundary("page.png", total_pages=2)
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "multipage_pdf")
        read.assert_not_called()
        detect.assert_not_called()

    def test_ruled_page_cannot_fall_back_to_old_crop(self):
        with patch("cv2.imread", return_value=self.image()), patch.object(production, "ruled_form_evidence", return_value={"reject": True}), patch.object(production, "detect") as detect:
            result = ImageNormalizationService().detect_pdf_subdocument_boundary("form.png")
        self.assertEqual(result["reason"], "ruled_form_page")
        detect.assert_not_called()

    def test_output_box_uses_xyxy_contract_and_crop_content(self):
        image = self.image()
        selected, _, _ = production.detect(image, production.DEFAULT_CONFIG)
        self.assertIsNotNone(selected)
        with patch("cv2.imread", return_value=image), patch("cv2.imwrite", return_value=True) as write:
            result = ImageNormalizationService().detect_pdf_subdocument_boundary("card.png", "out.png")
        x, y, w, h = selected["bbox"]
        self.assertEqual(result["crop_box"], [x, y, x+w, y+h])
        self.assertTrue(result["passed"])
        self.assertGreater(write.call_args.args[1].size, 0)

    def test_detection_service_propagates_page_count_to_post_match(self):
        path = Path(__file__).resolve().parents[1] / "app/processing/detection_service.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == "detect_pdf_subdocument_boundary"]
        self.assertTrue(any(any(kw.arg == "total_pages" for kw in call.keywords) for call in calls))
