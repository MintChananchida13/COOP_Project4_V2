import json
import unittest
from collections import OrderedDict
from unittest.mock import MagicMock, patch

import numpy as np

from app.core import model_runtime_client as client
from app.model_runtime import layout_analysis_service as service


LAYOUT = {"boxes": [{"label": "image", "coordinate": [5, 5, 35, 70], "score": .9}]}


class LayoutBatchContractTests(unittest.TestCase):
    def test_http_endpoint_images_payload_and_single_result_unwrap(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {"data": [LAYOUT], "meta": {}}).encode()
        image = np.zeros((100, 100, 3), np.uint8)
        with patch.object(client, "GATEWAY_URL", "https://gateway.invalid"), \
             patch.object(client.urllib.request, "urlopen", return_value=response) as http:
            result = client.remote_analyze_layout(image, layout_only=True)
        self.assertEqual(result, LAYOUT)
        http.assert_called_once()
        request = http.call_args.args[0]
        self.assertEqual(request.full_url, "https://gateway.invalid/paddle/api/v1/layout-prediction-batches")
        self.assertEqual(request.method, "POST")
        payload = json.loads(request.data)
        self.assertEqual(set(payload), {"images"})
        self.assertEqual(len(payload["images"]), 1)
        self.assertTrue(payload["images"][0].startswith("data:image/png;base64,"))

    def test_invalid_batch_count_or_leaf_fails_without_detection(self):
        invalid = [None, {}, [], [LAYOUT, LAYOUT], [None], [{}],
                   [{"boxes": None}], [{"boxes": [], "error": "failed"}],
                   {"count": 1, "results": [LAYOUT]}]
        for value in invalid:
            with self.subTest(value=value), patch.object(client, "is_runtime_configured", return_value=True), \
                 patch.object(client, "_post_predict", return_value=value) as post:
                with self.assertRaises(client.ModelRuntimeUnavailableError):
                    client.remote_analyze_layout(np.zeros((10, 10, 3), np.uint8))
                post.assert_called_once()

    def test_signature_uses_one_layout_request_without_detection_and_caches(self):
        image = np.zeros((100, 100, 3), np.uint8)
        with patch.object(service, "_layout_raw_items_cache", OrderedDict()), \
             patch.object(service, "LAYOUT_RAW_ITEMS_CACHE_SIZE", 16), \
             patch.object(service, "_require_runtime"), \
             patch.object(client, "is_runtime_configured", return_value=True), \
             patch.object(client, "_post_predict", return_value=[LAYOUT]) as post:
            timing = {}
            result = service.analyze_layout_signature(image, timing)
            cached = service.analyze_layout_signature(image.copy())
        self.assertEqual(result["region_sources"], {"layout": 1, "text_detection": 0})
        self.assertEqual(result["regions"], cached["regions"])
        self.assertEqual(post.call_count, 1)
        self.assertEqual([call.args[0] for call in post.call_args_list],
                         [client.ModelRuntimeKind.LAYOUT])
        self.assertNotIn("text_detection_runtime_client", timing["runtime_client"])

    def test_empty_layout_is_valid_and_transport_never_calls_detection(self):
        empty = [{"boxes": []}]
        with patch.object(client, "is_runtime_configured", return_value=True), \
             patch.object(client, "_post_predict", return_value=empty) as post:
            result = client.remote_analyze_layout(np.zeros((10, 10, 3), np.uint8), layout_only=True)
        self.assertEqual(result["boxes"], [])
        post.assert_called_once()

    def test_layout_gateway_timeout_propagates_without_retry(self):
        with patch.object(client, "is_runtime_configured", return_value=True), \
             patch.object(client, "_post_predict", side_effect=client.ModelRuntimeUnavailableError("timeout")) as post:
            with self.assertRaisesRegex(client.ModelRuntimeUnavailableError, "timeout"):
                client.remote_analyze_layout(np.zeros((10, 10, 3), np.uint8))
        post.assert_called_once()
