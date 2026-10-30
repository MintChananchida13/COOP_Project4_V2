import unittest
from collections import OrderedDict
from unittest.mock import MagicMock, patch

import numpy as np

from app.model_runtime import layout_analysis_service as service


class AutoRoiLayoutOnlyTests(unittest.TestCase):
    def test_layout_boxes_unchanged_when_input_image_is_omitted(self):
        item = {"input_img": [[[255, 255, 255]]],
                "boxes": [{"label": "text", "coordinate": [10, 20, 80, 40], "score": 0.9}]}
        before = service._layout_detection_items_from_response({"raw": {"layout": [item]}})
        after = service._layout_detection_items_from_response({"raw": {"layout": [{"boxes": item["boxes"]}]}})
        self.assertEqual(before, after)

    def test_auto_roi_requests_layout_only_and_preserves_regions(self):
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        layout = [{"boxes": [{"label": "image", "coordinate": [5, 5, 35, 70], "score": .9}]}]
        combined = {"raw": {"layout": layout, "text_detection": [{"dt_polys": [[[40, 10], [90, 10], [90, 20], [40, 20]]], "dt_scores": [.95]}]}}
        layout_only = {"raw": {"layout": layout}}
        temp = MagicMock()
        temp.__enter__.return_value.name = "unused.png"
        with patch.object(service, "_layout_raw_items_cache", OrderedDict()), \
                patch.object(service, "_require_runtime"), \
                patch.object(service.tempfile, "NamedTemporaryFile", return_value=temp), \
                patch.object(service.cv2, "imwrite"), \
                patch.object(service.Path, "unlink"), \
                patch.object(service, "remote_analyze_layout", side_effect=[combined, layout_only]) as remote:
            before = service.analyze_layout(image, expand_text_rois=True, use_text_detection=False)
            service.clear_layout_analysis_cache()
            after = service.analyze_layout(image, expand_text_rois=True, use_text_detection=False)
        self.assertEqual(before["regions"], after["regions"])
        self.assertEqual(remote.call_count, 2)
        self.assertTrue(all(call.kwargs == {"layout_only": True} for call in remote.call_args_list))

    def test_layout_plus_detection_still_requests_detection(self):
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        temp = MagicMock()
        temp.__enter__.return_value.name = "unused.png"
        with patch.object(service, "_layout_raw_items_cache", OrderedDict()), \
                patch.object(service, "_require_runtime"), \
                patch.object(service.tempfile, "NamedTemporaryFile", return_value=temp), \
                patch.object(service.cv2, "imwrite"), \
                patch.object(service.Path, "unlink"), \
                patch.object(service, "remote_analyze_layout", return_value={"raw": {"layout": []}}) as remote, \
                patch.object(service, "remote_detect_text_boxes", return_value={"raw": []}) as detector:
            service.analyze_layout(image, use_text_detection=True)
        self.assertEqual(remote.call_args.kwargs, {"layout_only": False})
        detector.assert_called_once()


if __name__ == "__main__":
    unittest.main()
