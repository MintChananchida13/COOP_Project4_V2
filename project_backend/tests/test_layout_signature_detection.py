import unittest
from unittest.mock import patch
from collections import OrderedDict
import numpy as np
from app.model_runtime import layout_analysis_service as service
from app.processing.layout_signature_service import build_layout_signature, compare_layout_signatures

class CombinedSignatureTests(unittest.TestCase):
    def response(self):
        return {"result": {"raw": {
            "layout": [{"boxes": [{"label": "image", "coordinate": [5, 5, 35, 70], "score": .9}]}],
            "text_detection": [{"dt_polys": [[[40,10],[90,10],[90,20],[40,20]],
                                               [[40,30],[90,30],[90,40],[40,40]]], "dt_scores": [.95,.96]}]
        }}}

    def test_combined_boxes_used_once_and_cached_without_second_inference(self):
        image = np.zeros((100,100,3), np.uint8)
        with patch.object(service, "_layout_raw_items_cache", OrderedDict()), patch.object(service, "LAYOUT_RAW_ITEMS_CACHE_SIZE",16), patch.object(service, "_require_runtime"), patch.object(service, "remote_analyze_layout",return_value=self.response()) as remote:
            timing = {}
            template = service.analyze_layout_signature(image, timing)
            query = service.analyze_layout_signature(image.copy())
        remote.assert_called_once_with(image, timing=timing["runtime_client"], layout_only=False)
        self.assertEqual(template["model"], "PP-DocLayoutV3+PP-OCRv6_medium_det")
        self.assertEqual([r["type"] for r in template["regions"]].count("text"), 2)
        self.assertEqual([r["type"] for r in template["regions"]].count("image"), 1)
        self.assertEqual(compare_layout_signatures(build_layout_signature(template),build_layout_signature(query))["score"],1)
        self.assertEqual(timing["text_detection_item_count"],2)

    def test_old_layout_only_cache_is_not_reused(self):
        image = np.zeros((100,100,3), np.uint8)
        with patch.object(service, "_layout_raw_items_cache", OrderedDict()), patch.object(service, "LAYOUT_RAW_ITEMS_CACHE_SIZE",16), patch.object(service, "_require_runtime"), patch.object(service, "remote_analyze_layout",return_value=self.response()) as remote:
            service._set_cached_layout_raw_items(image, [], service._RAW_LAYOUT_ONLY_CACHE_MODE)
            result = service.analyze_layout_signature(image)
        remote.assert_called_once()
        self.assertEqual(len(result["regions"]),3)

    def test_missing_detection_branch_is_explicit_error_not_silent_layout_only(self):
        with patch.object(service,"_get_cached_layout_raw_items",return_value=None), patch.object(service,"_require_runtime"), patch.object(service,"remote_analyze_layout",return_value={"result":{"raw":{"layout":[]}}}):
            with self.assertRaisesRegex(service.LayoutAnalysisUnavailableError,"both Layout and TextDetection"):
                service.analyze_layout_signature(np.zeros((100,100,3),np.uint8))
