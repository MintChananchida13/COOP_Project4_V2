import copy
import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api.detection_response import user_detection_response, without_image_payloads


class UserDetectionResponseTests(unittest.TestCase):
    def test_template_endpoint_preserves_admin_and_filters_user(self):
        # Execute the actual route body with a stub storage service, without
        # connecting to production DB/model services during transport tests.
        routes = Path(__file__).resolve().parents[1] / 'app/api/routes.py'
        tree = ast.parse(routes.read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'get_template')
        function.decorator_list = []
        payload = {'id': 'card', 'pages': [{'sample_image_url': 'data:image/png;base64,sample', 'page_number': 1}]}
        class Templates:
            def get_template(self, template_id):
                return payload
        namespace = {'admin_templates': Templates(), 'ok': lambda item: item,
                     'ApiResponse': dict, 'without_image_payloads': without_image_payloads}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(routes), 'exec'), namespace)
        self.assertEqual(namespace['get_template']('card'), payload)
        self.assertEqual(namespace['get_template']('card', 'user'), {'id': 'card', 'pages': [{'page_number': 1}]})
        self.assertIn('sample_image_url', payload['pages'][0])

    def test_nested_verification_previews_are_not_sent(self):
        source = {'best_candidate': {'verification': {'checked_fields': [{
            'current_crop_preview_data_url': 'data:image/png;base64,crop',
            'reference_crop_preview_data_url': '/reference.png',
            'flexible_overlay_preview_data_url': 'overlay',
            'crop_preview_url': '/crop.png', 'passed': True,
        }]}}}
        result = user_detection_response(source)
        self.assertEqual(result['best_candidate']['verification']['checked_fields'], [{'passed': True}])

    def test_template_request_and_ocr_metadata_do_not_echo_images(self):
        for source in [
            {'id': 'template', 'pages': [{'sample_image_url': '/sample.png',
                'normalized_image_url': 'data:image/png;base64,page', 'page_number': 1}],
             'fields': [{'roi': {'x_ratio': 0.1}}]},
            {'id': 'request', 'sample_file_url': 'data:image/png;base64,file',
             'requested_fields': [{'field_name': 'name'}], 'pages': [{'sample_image_url': 'image'}]},
            {'success': True, 'extracted_data': [{'text': 'hello', 'table_rows': [['value']],
                'table_debug': {'current_crop_preview_data_url': 'data:image/png;base64,crop'}}]},
        ]:
            snapshot = copy.deepcopy(source)
            result = without_image_payloads(source)
            self.assertEqual(source, snapshot)
            self.assertNotIn('data:image/', str(result))
            self.assertNotIn('sample_image_url', str(result))
            self.assertNotIn('sample_file_url', result)
            if 'extracted_data' in result:
                self.assertEqual(result['extracted_data'][0]['text'], 'hello')
                self.assertEqual(result['extracted_data'][0]['table_rows'], [['value']])

    def test_original_image_is_not_sent_again(self):
        for same_path in (True, False):
            with self.subTest(same_path=same_path):
                candidate = {'template_id': 'card', 'extraction_image_preview_url': 'same-image',
                             'extraction_image_path': 'original.png' if same_path else 'copy.png'}
                source = {'best_candidate': candidate, 'pages': [{
                    'original_image_path': 'original.png',
                    'original_image_preview_url': 'other-url' if same_path else 'same-image',
                    'candidates': [candidate],
                }]}
                result = user_detection_response(source)
                self.assertEqual(result['pages'][0]['processing_image_source'], 'original')
                self.assertNotIn('processing_image_preview_url', result['pages'][0])
                self.assertNotIn('extraction_image_preview_url', result['best_candidate'])

    def test_filters_nested_diagnostics_preserving_processing_contract(self):
        candidate = {
            'template_id': 'card', 'final_passed': True,
            'extraction_image_preview_url': 'data:extraction',
            'aligned_image_preview_url': 'data:aligned',
            'template_rois': [{'x_ratio': 0.1}],
            'projected_fields': [{'x': 20}],
            'roi_coordinate_space': 'processing',
            'processing_pages': [{'processingImageReference': 'processing_page_1.png'}],
            'decision_path': 'standard',
            'normalized_image_preview_url': 'data:unused',
            'extraction_image_path': 'D:/server/image.png',
            'coordinate_debug': {'large': [1, 2]},
            'alignment': {'aligned_image_preview_url': 'duplicate'},
            'metadata': {'version_name': 'v1', 'layout_signature': [1, 2]},
        }
        source = {
            'best_candidate': candidate, 'candidates': [candidate],
            'pages': [{'candidates': [candidate], 'normalization': {'debug': 'large'}}],
            'debug': {'verification_strategy': 'standard', 'query_page_paths': ['server'],
                      'timing': {'total_runtime_ms': 123, 'breakdown': {'large': []}}},
        }
        snapshot = copy.deepcopy(source)
        result = user_detection_response(source)
        self.assertEqual(source, snapshot)
        self.assertEqual(result['debug'], {'verification_strategy': 'standard', 'timing': {'total_runtime_ms': 123}})
        for actual in [result['best_candidate'], result['candidates'][0], result['pages'][0]['candidates'][0]]:
            for key in ['normalized_image_preview_url', 'extraction_image_path', 'coordinate_debug', 'alignment',
                        'extraction_image_preview_url', 'aligned_image_preview_url']:
                self.assertNotIn(key, actual)
            for key in ['template_rois',
                        'projected_fields', 'roi_coordinate_space', 'processing_pages', 'decision_path']:
                self.assertEqual(actual[key], candidate[key])
            self.assertEqual(actual['metadata'], {'version_name': 'v1'})
        self.assertEqual(result['pages'][0]['processing_image_preview_url'], 'data:extraction')
        self.assertEqual(result['pages'][0]['processing_template_id'], 'card')

    def test_aligned_selection_and_multiple_pages(self):
        source = {'best_candidate': {'template_id': 'chosen'}, 'pages': []}
        for index in range(2):
            source['pages'].append({'page_index': index + 1, 'candidates': [
                {'template_id': 'other', 'extraction_image_preview_url': 'unused'},
                {'template_id': 'chosen', 'selected_processing_source': 'aligned',
                 'extraction_image_preview_url': 'duplicate',
                 'aligned_image_preview_url': f'aligned-{index}'},
            ]})
        result = user_detection_response(source)
        for index, page in enumerate(result['pages']):
            self.assertEqual(page['processing_image_preview_url'], f'aligned-{index}')
            self.assertEqual(page['processing_template_id'], 'chosen')
            self.assertFalse(any('aligned_image_preview_url' in item for item in page['candidates']))


if __name__ == '__main__':
    unittest.main()
