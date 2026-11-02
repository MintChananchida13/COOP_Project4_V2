import base64
import unittest
from dataclasses import replace
from unittest.mock import patch

import cv2
import numpy as np
from fastapi.testclient import TestClient
from app import main


def image_url(value):
    ok, encoded = cv2.imencode('.png', np.full((8, 8, 3), value, dtype=np.uint8))
    assert ok
    return 'data:image/png;base64,' + base64.b64encode(encoded).decode()


class FakeDetection:
    def __init__(self):
        self.calls = []

    def predict(self, images, batch_size=None):
        single = isinstance(images, np.ndarray)
        items = [images] if single else images
        if not single:
            np.stack(items)  # Mirror PaddleX rejecting differing image shapes.
        self.calls.append(('single' if single else 'batch', len(items), batch_size))
        return [{'res': {'dt_polys': [[[int(image[0, 0, 0]), 1]]], 'dt_scores': [0.9]}} for image in items]


class DetectionBatchTests(unittest.TestCase):
    def test_true_batch_preserves_order_and_api_for_default_and_finetune(self):
        for model_id in ('default', 'thai_ft_v2'):
            model = FakeDetection()
            settings = replace(main.settings, api_key='secret', detection_batch_size=2)
            with patch.object(main, 'settings', settings), patch.object(main.manager, 'get_ocr', return_value=(model, 0)), \
                 patch.object(main.manager, 'get', return_value=(model, 0)):
                response = TestClient(main.app).post(
                    f'/api/v1/text-detection-batches?version=v6&model_id={model_id}',
                    headers={'Authorization': 'Bearer secret'},
                    json={'images': [image_url(value) for value in (10, 20, 30, 40, 50)]})
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(set(body), {'success', 'model', 'result', 'timing'})
            self.assertEqual([x['res']['dt_polys'][0][0][0] for x in body['result']['results']], [10, 20, 30, 40, 50])
            self.assertEqual(model.calls, [('batch', 2, 2), ('batch', 2, 2), ('single', 1, None)])

    def test_mixed_roi_shapes_are_grouped_and_returned_in_original_order(self):
        model = FakeDetection()
        originals = [np.full((height, width, 3), value, dtype=np.uint8) for height, width, value in
                     [(8, 12, 10), (5, 7, 20), (8, 12, 30), (6, 9, 40), (5, 7, 50)]]
        copies = [image.copy() for image in originals]
        with patch.object(main, 'settings', replace(main.settings, detection_batch_size=8)):
            result = main.paddle_detection_batch(model, originals)
        self.assertEqual([x['res']['dt_polys'][0][0][0] for x in result['results']], [10, 20, 30, 40, 50])
        self.assertEqual(model.calls, [('batch', 2, 2), ('batch', 2, 2), ('single', 1, None)])
        self.assertTrue(all(np.array_equal(a, b) for a, b in zip(originals, copies)))

    def test_default_path_still_calls_each_image_separately(self):
        model = FakeDetection()
        with patch.object(main, 'settings', replace(main.settings, detection_batch_size=1)):
            result = main.paddle_detection_batch(model, [np.full((4, 4, 3), x, dtype=np.uint8) for x in (2, 5)])
        self.assertEqual(len(result['results']), 2)
        self.assertEqual(model.calls, [('single', 1, None), ('single', 1, None)])

    def test_batch_size_eight_preserves_order_and_reports_timing(self):
        model = FakeDetection()
        settings = replace(main.settings, api_key='secret', detection_batch_size=8)
        with patch.object(main, 'settings', settings), patch.object(main.manager, 'get', return_value=(model, 0)):
            response = TestClient(main.app).post(
                '/api/v1/text-detection-batches?version=v6',
                headers={'Authorization': 'Bearer secret'},
                json={'images': [image_url(value) for value in range(9)]})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIsInstance(body['timing']['inference_ms'], (int, float))
        self.assertEqual([x['res']['dt_polys'][0][0][0] for x in body['result']['results']], list(range(9)))
        self.assertEqual(model.calls, [('batch', 8, 8), ('single', 1, None)])

    def test_single_image_path_reports_inference_timing_for_batch_endpoint(self):
        model = FakeDetection()
        settings = replace(main.settings, api_key='secret', detection_batch_size=1)
        with patch.object(main, 'settings', settings), patch.object(main.manager, 'get', return_value=(model, 0)):
            response = TestClient(main.app).post(
                '/api/v1/text-detection-batches?version=v6',
                headers={'Authorization': 'Bearer secret'},
                json={'images': [image_url(10), image_url(20)]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsInstance(response.json()['timing']['inference_ms'], (int, float))
        self.assertEqual(model.calls, [('single', 1, None), ('single', 1, None)])

    def test_bad_count_or_batch_failure_is_not_silently_retried(self):
        model = FakeDetection()
        model.predict = lambda images, batch_size: []
        with patch.object(main, 'settings', replace(main.settings, detection_batch_size=2)):
            with self.assertRaisesRegex(RuntimeError, 'result count or shape'):
                main.paddle_detection_batch(model, [np.zeros((4, 4, 3), dtype=np.uint8)] * 2)


if __name__ == '__main__':
    unittest.main()
