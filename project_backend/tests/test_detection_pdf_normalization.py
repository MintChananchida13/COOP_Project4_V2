import unittest
from pathlib import Path
from unittest.mock import patch

from app.detection_service import _normalize_query_pages


class DetectionPdfNormalizationTest(unittest.TestCase):
    def test_pdf_rendered_pages_skip_image_normalization(self) -> None:
        page_paths = [Path("page_1.png"), Path("page_2.png"), Path("page_3.png")]

        with patch("app.detection_service.normalization_service.normalize_document") as normalize_document:
            pages = _normalize_query_pages("query_pdf", page_paths, skip_normalization=True)

        normalize_document.assert_not_called()
        self.assertEqual([page["page_index"] for page in pages], [1, 2, 3])
        self.assertEqual([page["normalized_path"] for page in pages], ["page_1.png", "page_2.png", "page_3.png"])
        self.assertTrue(all(page["normalization"]["normalization_status"] == "skipped" for page in pages))


if __name__ == "__main__":
    unittest.main()
