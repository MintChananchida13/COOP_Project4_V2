import copy
import unittest
from unittest.mock import patch

import numpy as np

from app.model_runtime import layout_analysis_service

from app.processing.layout_signature_service import (
    LABELS, build_layout_signature, compare_layout_signatures,
    signature_from_json, signature_to_json,
)


def signature(count, fingerprint=None):
    regions = []
    for label in LABELS:
        for index in range(count):
            regions.append({"type": label, "roi": {
                "x_ratio": 0.05 + (index % 10) * 0.08,
                "y_ratio": 0.05 + (index // 10) * 0.06,
                "width_ratio": 0.04, "height_ratio": 0.03,
            }})
    return build_layout_signature({"image_width": 800, "image_height": 1200,
                                   "image_fingerprint": fingerprint, "regions": regions})


class LayoutSignatureIdentityTests(unittest.TestCase):
    def test_pixel_fingerprint_survives_cache_without_extra_model_calls(self):
        image = np.zeros((8, 12, 3), dtype=np.uint8)
        with patch.object(layout_analysis_service, "_layout_raw_items_cache", layout_analysis_service.OrderedDict()), patch.object(layout_analysis_service, "LAYOUT_RAW_ITEMS_CACHE_SIZE", 16), patch.object(layout_analysis_service, "_require_runtime"), patch.object(layout_analysis_service, "remote_analyze_layout", return_value={"result": {"raw": {"layout": []}}}) as remote:
            first = layout_analysis_service.analyze_layout_signature(image)
            cached = layout_analysis_service.analyze_layout_signature(image.copy())
            self.assertEqual(first["image_fingerprint"], cached["image_fingerprint"])
            remote.assert_called_once()
            image[0, 0] = 255
            changed = layout_analysis_service.analyze_layout_signature(image)
            self.assertNotEqual(first["image_fingerprint"], changed["image_fingerprint"])
            self.assertEqual(remote.call_count, 2)

    def test_same_signature_scores_one_above_twenty_regions_per_label(self):
        for count in (1, 20, 21, 100):
            with self.subTest(regions_per_label=count):
                reference = signature(count)
                result = compare_layout_signatures(copy.deepcopy(reference), reference)
                self.assertEqual(result["score"], 1.0)
                self.assertEqual(result["spatial_score"], 1.0)

    def test_json_roundtrip_keeps_identity(self):
        reference = signature(50)
        restored = signature_from_json(signature_to_json(reference))
        self.assertEqual(compare_layout_signatures(restored, reference)["score"], 1.0)

    def test_full_region_count_is_still_penalized(self):
        result = compare_layout_signatures(signature(40), signature(80), disable_prefilter=True)
        self.assertEqual(result["label_count_score"], 0.5)
        self.assertLess(result["score"], 1.0)

    def test_regions_after_twenty_affect_spatial_score(self):
        reference = signature(40)
        changed = copy.deepcopy(reference)
        for label in LABELS:
            regions = [region for region in changed["regions"] if region["label"] == label]
            for region in regions[20:]:
                region["bbox"] = {"x_ratio": 0.95, "y_ratio": 0.95, "width_ratio": 0.001, "height_ratio": 0.001}
        result = compare_layout_signatures(changed, reference)
        self.assertLess(result["spatial_score"], 1.0)
        self.assertLess(result["score"], 1.0)

    def test_pixel_identity_is_diagnostic_only(self):
        reference = signature(25, "same-pixels")
        same = compare_layout_signatures(copy.deepcopy(reference), reference)
        self.assertTrue(same["normalization_debug"]["source_pixels_identical"])
        different = {**reference, "image_fingerprint": "different-pixels"}
        result = compare_layout_signatures(different, reference)
        self.assertFalse(result["normalization_debug"]["source_pixels_identical"])
        self.assertEqual(result["score"], same["score"])
        missing = compare_layout_signatures(signature(25), reference)
        self.assertIsNone(missing["normalization_debug"]["source_pixels_identical"])


if __name__ == "__main__":
    unittest.main()
