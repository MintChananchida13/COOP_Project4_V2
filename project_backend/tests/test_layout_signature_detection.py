import unittest
from unittest.mock import patch
from collections import OrderedDict
import numpy as np
from app.model_runtime import layout_analysis_service as service
from app.processing.layout_signature_service import build_layout_signature, compare_layout_signatures

class LayoutOnlySignatureTests(unittest.TestCase):
    def response(self):
        return {"boxes": [{"label": "image", "coordinate": [5, 5, 35, 70], "score": .9},
                       {"label": "text", "coordinate": [40, 10, 90, 30], "score": .95}]}

    def test_layout_boxes_including_text_used_once_and_cached_without_detection(self):
        image = np.zeros((100,100,3), np.uint8)
        with patch.object(service, "_layout_raw_items_cache", OrderedDict()), patch.object(service, "LAYOUT_RAW_ITEMS_CACHE_SIZE",16), patch.object(service, "_require_runtime"), patch.object(service, "remote_analyze_layout",return_value=self.response()) as remote:
            timing = {}
            template = service.analyze_layout_signature(image, timing)
            query = service.analyze_layout_signature(image.copy())
        remote.assert_called_once_with(image, timing=timing["runtime_client"], layout_only=True)
        self.assertEqual(template["model"], "PP-DocLayoutV3")
        self.assertEqual([r["type"] for r in template["regions"]].count("text"), 1)
        self.assertEqual([r["type"] for r in template["regions"]].count("image"), 1)
        self.assertEqual(compare_layout_signatures(build_layout_signature(template),build_layout_signature(query))["score"],1)
        self.assertEqual(timing["text_detection_item_count"], 0)
        self.assertEqual(template["region_sources"], {"layout": 2, "text_detection": 0})

    def test_old_layout_only_cache_is_not_reused(self):
        image = np.zeros((100,100,3), np.uint8)
        with patch.object(service, "_layout_raw_items_cache", OrderedDict()), patch.object(service, "LAYOUT_RAW_ITEMS_CACHE_SIZE",16), patch.object(service, "_require_runtime"), patch.object(service, "remote_analyze_layout",return_value=self.response()) as remote:
            service._set_cached_layout_raw_items(image, [], service._RAW_LAYOUT_ONLY_CACHE_MODE)
            service._set_cached_layout_raw_items(image, [{"label": "text", "coordinate": [1, 1, 99, 99],
                                                        "source": "text_detection"}], "layout_signature_layout_det_v6")
            result = service.analyze_layout_signature(image)
        remote.assert_called_once()
        self.assertEqual(len(result["regions"]), 2)
        self.assertTrue(all(region["source"] == "layout" for region in result["regions"]))

    def test_invalid_or_old_combined_response_is_an_explicit_error(self):
        invalid = [{"raw": {"layout": [], "text_detection": []}}, {},
                   {"contract_version": "leaf-inference-v1", "kind": "layout", "raw_output": None},
                   {"contract_version": "leaf-inference-v1", "kind": "layout", "raw_output": [None]}]
        for response in invalid:
            with self.subTest(response=response), patch.object(service, "_get_cached_layout_raw_items", return_value=None), \
                 patch.object(service, "_require_runtime"), patch.object(service, "remote_analyze_layout", return_value=response):
                with self.assertRaisesRegex(service.LayoutAnalysisUnavailableError, "raw Layout"):
                    service.analyze_layout_signature(np.zeros((100, 100, 3), np.uint8))

    def test_unrelated_detection_branch_is_not_added_to_layout_signature(self):
        response = self.response()
        response["text_detection"] = {"dt_polys": [[[1, 1], [99, 1], [99, 99], [1, 99]]]}
        with patch.object(service, "_get_cached_layout_raw_items", return_value=None), \
             patch.object(service, "_require_runtime"), patch.object(service, "remote_analyze_layout", return_value=response):
            result = service.analyze_layout_signature(np.zeros((100, 100, 3), np.uint8))
        self.assertEqual(len(result["regions"]), 2)
        self.assertEqual(result["region_sources"]["text_detection"], 0)
