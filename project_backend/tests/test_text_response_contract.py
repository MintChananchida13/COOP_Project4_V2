import copy
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
from app.core import model_runtime_client as client
from app.model_runtime import layout_analysis_service as det
from app.model_runtime import paddle_thai_ocr_adapter as rec
from app.model_runtime.text_response_contract import (
    normalize_batch, normalize_single, TextResponseContractError,
)


POLYGON = [[0, 1], [135, 1], [135, 23], [0, 23]]
DETECTION = {'dt_polys': [POLYGON], 'dt_scores': [.8652826579402793]}
RECOGNITION = {'rec_text': 'Example text', 'rec_score': .92}


class TextResponseContractTests(unittest.TestCase):
    def test_exact_gateway_det_example_has_one_polygon_and_ignores_router_metadata(self):
        value = json.loads((Path(__file__).parent / 'fixtures/det_leaf_inference_v1.json').read_text())
        image = np.zeros((80, 200, 3), dtype=np.uint8)
        result = det._text_detection_result_from_remote(value, image)
        self.assertEqual(result['model'], 'PP-OCRv6_medium_det')
        self.assertEqual(len(result['regions']), 1)
        self.assertEqual(result['regions'][0]['confidence'], DETECTION['dt_scores'][0])
        self.assertEqual(result['regions'][0]['bbox'], {'x': 0., 'y': 1., 'width': 135., 'height': 22.})

    def test_payload_containers_are_model_and_version_independent(self):
        # These are structural compatibility fixtures, not live results from these models.
        for kind, leaf in [('det', DETECTION), ('rec', RECOGNITION)]:
            for version in ('v3', 'v4', 'v5', 'v6', 'custom-version'):
                for wrapper in ('data', 'result', 'results', 'raw_output', 'predictions', 'res'):
                    with self.subTest(kind=kind, version=version, wrapper=wrapper):
                        model = f'configured-{kind}-{version}'
                        nested = [leaf] if wrapper in {'results', 'raw_output', 'predictions'} else leaf
                        value = {'data': {wrapper: nested,
                            'model_selection': {'version': version, 'model_name': model}},
                            'meta': {'model': 'router', 'label': 'not OCR', 'score': .01}}
                        parsed = normalize_single(value, kind)
                        self.assertEqual(parsed, {**leaf, 'model': model})

    def test_confirmed_det_batch_container_prefers_raw_predictions_over_duplicate_regions(self):
        value = {'data': {'count': 1, 'results': [
            {'regions': [{'text': 'not an OCR result', 'confidence': .01}],
             'raw_predictions': [{'res': DETECTION}],
             'model_selection': {'version': 'v5', 'model_name': 'PP-OCRv5_server_det'}}]}}
        with patch.object(det, '_require_runtime'), patch.object(det, '_remote_detect_text_boxes_batch', return_value=value) as remote:
            result = det.detect_text_boxes_batch([np.zeros((80, 200, 3), dtype=np.uint8)])
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]['regions']), 1)
        self.assertEqual(result[0]['regions'][0]['confidence'], DETECTION['dt_scores'][0])
        self.assertEqual(result[0]['model'], 'PP-OCRv5_server_det')
        self.assertEqual(remote.call_count, 1)

    def test_confirmed_rec_single_and_one_image_raw_output_batch(self):
        value = {'data': {'contract_version': 'leaf-inference-v1', 'count': 1,
            'raw_output': [RECOGNITION],
            'model_selection': {'version': 'v5', 'model_name': 'th_PP-OCRv5_mobile_rec'}}}
        self.assertEqual(rec._extract_text_confidence(value), ('Example text', .92))
        with patch.object(rec, '_require_text_recognition_runtime'), patch.object(rec, 'remote_recognize_images', return_value=value) as remote:
            result = rec.run_paddle_thai_ocr(np.zeros((20, 80, 3), dtype=np.uint8))
        self.assertEqual(result['text'], 'Example text')
        self.assertEqual(result['confidence'], .92)
        self.assertEqual(result['model'], 'th_PP-OCRv5_mobile_rec')
        self.assertEqual(remote.call_count, 1)

    def test_existing_ordered_batch_contract_preserves_empty_middle_image(self):
        # Exercises the existing internal results[] positional contract; not a
        # claim about an unobserved multi-image Gateway response.
        det_results = [DETECTION, {'dt_polys': [], 'dt_scores': []},
                       {'dt_polys': [[[2, 3], [20, 3], [20, 12], [2, 12]]], 'dt_scores': [.7]}]
        rec_results = [RECOGNITION, {'rec_text': '', 'rec_score': 0.}, {'rec_text': 'Last', 'rec_score': .7}]
        for kind, items in [('det', det_results), ('rec', rec_results)]:
            value = {'result': {'results': [{'result': item} for item in items]}}
            self.assertEqual(normalize_batch(value, kind, 3), items)
        images = [np.zeros((h, 200, 3), dtype=np.uint8) for h in (80, 90, 100)]
        with patch.object(det, '_require_runtime'), patch.object(det, '_remote_detect_text_boxes_batch', return_value={'results': det_results}):
            results = det.detect_text_boxes_batch(images)
        self.assertEqual([item['image_height'] for item in results], [80, 90, 100])
        self.assertEqual([len(item['regions']) for item in results], [1, 0, 1])
        with patch.object(rec, '_require_text_recognition_runtime'), patch.object(rec, 'remote_recognize_images', return_value={'results': rec_results}):
            results = rec.run_paddle_thai_ocr_batch(images)
        self.assertEqual([item['text'] for item in results], ['Example text', '', 'Last'])
        self.assertEqual([item['confidence'] for item in results], [.92, 0., .7])

    def test_valid_empty_fields_and_empty_zero_input_batch(self):
        self.assertEqual(normalize_single({'res': {'dt_polys': [], 'dt_scores': []}}, 'det'),
                         {'dt_polys': [], 'dt_scores': []})
        self.assertEqual(normalize_single({'raw_output': [{'rec_text': '', 'rec_score': 0}]}, 'rec'),
                         {'rec_text': '', 'rec_score': 0.})
        self.assertEqual(normalize_batch({'results': []}, 'rec', 0), [])

    def test_rec_metadata_is_never_concatenated_or_averaged_into_ocr(self):
        value = {'data': {'raw_output': [{'res': {**RECOGNITION, 'text': 'duplicate summary', 'score': .01}}],
                          'model_selection': {'label': 'metadata', 'score': .01}},
                 'meta': {'text': 'gateway', 'confidence': .01}}
        self.assertEqual(rec._extract_text_confidence(value), ('Example text', .92))
        self.assertEqual(rec._extract_text_confidence({'result': {'text': 'Legacy', 'confidence': .8}}), ('Legacy', .8))

    def test_existing_rec_alias_pairs_remain_supported_without_score_averaging(self):
        for text_key in ('rec_text', 'text', 'label'):
            for score_key in ('rec_score', 'confidence', 'score', 'prob'):
                with self.subTest(text=text_key, score=score_key):
                    self.assertEqual(rec._extract_text_confidence({'res': {text_key: 'Legacy', score_key: .8}}), ('Legacy', .8))

    def test_missing_extra_null_or_ambiguous_results_are_errors_not_padding(self):
        for kind, leaf in [('det', DETECTION), ('rec', RECOGNITION)]:
            for value, count in [({'results': []}, 1), ({'results': [leaf]}, 2),
                                 ({'results': [leaf, leaf]}, 1), ({'results': [None]}, 1),
                                 ({'data': {}, 'result': leaf}, 1),
                                 ({'results': [{'raw_output': [leaf, leaf]}, {'raw_output': []}]}, 2)]:
                with self.subTest(kind=kind, value=value), self.assertRaises(TextResponseContractError):
                    normalize_batch(value, kind, count)
        for kind in ('det', 'rec'):
            with self.assertRaises(TextResponseContractError):
                normalize_single({'raw_output': []}, kind)

    def test_malformed_fields_fail_without_synthesizing_scores_or_text(self):
        cases = [('det', {'dt_polys': [POLYGON]}), ('det', {'dt_polys': [], 'dt_scores': [.9]}),
                 ('det', {'dt_polys': [[[0, 1]]], 'dt_scores': [.9]}),
                 ('det', {'dt_polys': [POLYGON], 'dt_scores': [float('nan')]}),
                 ('rec', {'rec_text': 'missing score'}), ('rec', {'rec_text': None, 'rec_score': .9}),
                 ('rec', {'rec_text': 'bad score', 'rec_score': True}),
                 ('rec', {'rec_text': 'bad score', 'rec_score': float('inf')}),
                 ('rec', {'raw_output': [RECOGNITION], 'success': False}),
                 ('rec', {'debug': RECOGNITION})]
        for kind, value in cases:
            with self.subTest(kind=kind, value=value), self.assertRaises(TextResponseContractError):
                normalize_single(value, kind)

    def test_adapter_contract_errors_use_existing_error_types_and_no_retry(self):
        image = np.zeros((20, 40, 3), dtype=np.uint8)
        with patch.object(det, '_require_runtime'), patch.object(det, '_remote_detect_text_boxes_batch', return_value={'results': []}) as remote:
            with self.assertRaises(det.LayoutAnalysisUnavailableError):
                det.detect_text_boxes_batch([image])
            self.assertEqual(remote.call_count, 1)
        with patch.object(rec, '_require_text_recognition_runtime'), patch.object(rec, 'remote_recognize_images', return_value={'results': []}) as remote:
            with self.assertRaises(rec.PaddleThaiOcrUnavailableError):
                rec.run_paddle_thai_ocr_batch([image])
            self.assertEqual(remote.call_count, 1)

    def test_rec_chunking_and_order_are_unchanged(self):
        image = np.zeros((10, 20, 3), dtype=np.uint8)
        def response(images, timing=None):
            start = 0 if len(images) == 60 else 60
            return {'results': [{'rec_text': f'item{i}', 'rec_score': .9} for i in range(start, start + len(images))]}
        with patch.object(rec, '_require_text_recognition_runtime'), patch.object(rec, 'remote_recognize_images', side_effect=response) as remote:
            result = rec.run_paddle_thai_ocr_batch([image] * 61)
        self.assertEqual([len(call.args[0]) for call in remote.call_args_list], [60, 1])
        self.assertEqual([item['text'] for item in result], [f'item{i}' for i in range(61)])

    def test_http_layer_preserves_real_model_identity_for_det_and_rec(self):
        value = json.loads((Path(__file__).parent / 'fixtures/det_leaf_inference_v1.json').read_text())
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(value).encode()
        with patch.object(client, 'GATEWAY_URL', 'https://gateway.example'), patch.object(client.urllib.request, 'urlopen', return_value=response):
            output = client._post_predict(client.ModelRuntimeKind.TEXT_DETECTION, {'image': 'fixture'}, path_override='/paddle/api/v1/text-detections')
        self.assertEqual(output['model'], 'PP-OCRv6_medium_det')
        self.assertEqual(normalize_single(output, 'det')['dt_scores'], DETECTION['dt_scores'])

    def test_normalization_does_not_mutate_the_gateway_response(self):
        value = {'data': {'raw_output': [DETECTION]}}
        before = copy.deepcopy(value)
        normalize_single(value, 'det')
        normalize_batch(value, 'det', 1)
        self.assertEqual(value, before)

    def test_remote_single_and_batch_clients_keep_paths_requests_and_internal_results(self):
        image = np.zeros((80, 200, 3), dtype=np.uint8)
        cases = [
            ('det', 'single', {'raw_output': [DETECTION]}),
            ('det', 'batch', {'results': [{'regions': [], 'raw_predictions': [{'res': DETECTION}]}]}),
            ('rec', 'single', {'raw_output': [RECOGNITION]}),
            ('rec', 'batch', {'raw_output': [RECOGNITION]}),
        ]
        for kind, mode, data in cases:
            with self.subTest(kind=kind, mode=mode):
                data = {**data, 'model_selection': {'model_name': f'configured-{kind}', 'version': 'custom'}}
                response = MagicMock()
                response.__enter__.return_value.read.return_value = json.dumps({'data': data, 'meta': {'model': 'router'}}).encode()
                with patch.object(client, 'GATEWAY_URL', 'https://gateway.example'), \
                     patch.object(client, '_active_ocr_path', return_value='/paddle/api/v1/configured?version=custom'), \
                     patch.object(client, '_path_to_data_url', return_value='data:image/png;base64,fixture'), \
                     patch.object(client.urllib.request, 'urlopen', return_value=response) as remote, \
                     patch.object(det, '_require_runtime'), patch.object(rec, '_require_text_recognition_runtime'), \
                     patch.object(det.cv2, 'imread', return_value=image):
                    if kind == 'det':
                        result = det.detect_text_boxes('fixture.png') if mode == 'single' else det.detect_text_boxes_batch([image])[0]
                        self.assertEqual(len(result['regions']), 1)
                        self.assertEqual(result['regions'][0]['confidence'], DETECTION['dt_scores'][0])
                    elif mode == 'single':
                        result = rec._result_from_output(client.remote_recognize_image(image))
                    else:
                        result = rec.run_paddle_thai_ocr_batch([image])[0]
                    if kind == 'rec':
                        self.assertEqual((result['text'], result['confidence']), ('Example text', .92))
                self.assertEqual(result['model'], f'configured-{kind}')
                self.assertEqual(remote.call_count, 1)
                request = remote.call_args.args[0]
                self.assertEqual(request.full_url, 'https://gateway.example/paddle/api/v1/configured?version=custom')
                self.assertEqual(set(json.loads(request.data)), {'image' if mode == 'single' else 'images'})

    def test_layout_table_and_siglip_are_not_subject_to_text_contract_normalization(self):
        data = {'raw_output': [{'arbitrary': 'preserved'}], 'predictions': [], 'regions': []}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({'data': data, 'meta': {}}).encode()
        for kind in (client.ModelRuntimeKind.LAYOUT, client.ModelRuntimeKind.TABLE, client.ModelRuntimeKind.IMAGE_VERIFICATION):
            with self.subTest(kind=kind), patch.object(client, 'GATEWAY_URL', 'https://gateway.example'), \
                 patch.object(client.urllib.request, 'urlopen', return_value=response):
                self.assertEqual(client._post_predict(kind, {}, path_override='/unchanged'), data)

    def test_optional_raw_rec_debug_output_still_works(self):
        with patch.dict('os.environ', {'BACKEND_OCR_INCLUDE_RAW_OUTPUT': 'true'}):
            result = rec._result_from_output({'res': RECOGNITION})
        self.assertEqual(result['raw_output'], [{'res': RECOGNITION}])


if __name__ == '__main__':
    unittest.main()
