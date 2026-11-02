import base64
import unittest
from dataclasses import replace
from unittest.mock import patch

import cv2
import numpy as np
from fastapi.testclient import TestClient
from app import main


class ImageReturningModel:
    def __init__(self, kind):
        self.kind = kind
        self.outputs = []

    def predict(self, images, batch_size=None):
        images = [images] if isinstance(images, np.ndarray) else images
        self.outputs = []
        for image in images:
            value = int(image[0, 0, 0])
            result = {'input_img': image, 'input_path': None, 'page_index': 0,
                      'res': {'input_img': image, 'custom_metadata': {'id': value}}}
            if self.kind == 'det':
                result['res'].update(dt_polys=[[[value, 1], [value + 1, 1], [value + 1, 2], [value, 2]]], dt_scores=[0.9])
            else:
                result['res'].update(rec_text=str(value), rec_score=0.9)
            self.outputs.append(result)
        return self.outputs


def image_url(value):
    ok, image = cv2.imencode('.png', np.full((8, 8, 3), value, dtype=np.uint8))
    assert ok
    return 'data:image/png;base64,' + base64.b64encode(image).decode()


class OcrResponseCompactTests(unittest.TestCase):
    def test_detection_and_recognition_single_and_batch_strip_only_input_image(self):
        for kind, path, version in [('det', 'text-detection', 'v6'), ('rec', 'text-recognition', 'v5')]:
            for batch in (False, True):
                with self.subTest(kind=kind, batch=batch):
                    model = ImageReturningModel(kind)
                    settings = replace(main.settings, api_key='secret', detection_batch_size=8, recognition_batch_size=8)
                    suffix = '-batches' if batch else 's'
                    payload = {'images': [image_url(2), image_url(5)]} if batch else {'image': image_url(2)}
                    with patch.object(main, 'settings', settings), patch.object(main.manager, 'get', return_value=(model, 0)):
                        response = TestClient(main.app).post(f'/api/v1/{path}{suffix}?version={version}',
                            headers={'Authorization': 'Bearer secret'}, json=payload)
                    self.assertEqual(response.status_code, 200, response.text)
                    data = response.json()
                    self.assertIsInstance(data['timing']['inference_ms'], (int, float))
                    results = data['result']['results'] if batch else [data['result']]
                    self.assertEqual(len(results), 2 if batch else 1)
                    self.assertEqual([r['res']['custom_metadata']['id'] for r in results], [2, 5] if batch else [2])
                    for result in results:
                        self.assertNotIn('input_img', result)
                        self.assertNotIn('input_img', result['res'])
                        self.assertIsNone(result['input_path'])
                        if kind == 'det':
                            self.assertEqual(result['res']['dt_scores'], [0.9])
                            self.assertEqual(len(result['res']['dt_polys']), 1)
                        else:
                            self.assertEqual(result['res']['rec_score'], 0.9)
                            self.assertTrue(result['res']['rec_text'])
                    self.assertTrue(all('input_img' in original for original in model.outputs))


if __name__ == '__main__':
    unittest.main()
