import copy
import time
import unittest
from unittest.mock import patch

import numpy as np

from app.model_runtime import table_recognition_v2_adapter as table
from app.processing import extraction_timing


class TablePostprocessingTests(unittest.TestCase):
    def output(self):
        boxes = [[0, 0, 100, 40], [100, 0, 200, 40], [0, 40, 100, 80], [100, 40, 200, 80]]
        return {'table_res_list': [{'cell_box_list': boxes,
            'table_ocr_pred': {'rec_boxes': [[4, 4, 90, 30], [104, 4, 190, 30],
                [4, 44, 90, 70], [104, 44, 190, 70]],
                'rec_texts': ['Name', 'Value', 'Example', '123'], 'rec_scores': [.9] * 4}}],
                'input_img': [[[0, 0, 0], [255, 255, 255]]]}

    def test_dictionary_traversal_preserves_order_and_objects(self):
        child = {'value': 2}
        root = {'pixels': [[0, 1, 2], [3, 4, 5]], 'table': [{'nested': child}], 'none': None}
        self.assertEqual(table._collect_dicts(root), [root, root['table'][0], child])
        self.assertIs(table._collect_dicts(root)[-1], child)

    def test_candidates_can_reuse_scan_without_traversing_again(self):
        output = self.output()
        collected = table._collect_dicts(output)
        expected = table._table_result_candidates(output)
        with patch.object(table, '_collect_dicts', side_effect=AssertionError('duplicate scan')):
            self.assertEqual(table._table_result_candidates(output, collected), expected)

    def test_reconstruction_scans_once_and_preserves_table_structure(self):
        output = self.output()
        original = copy.deepcopy(output)
        image = np.zeros((80, 200, 3), dtype=np.uint8)
        # Count only top-level scans (the walker recursively calls itself).
        scan = table._collect_dicts
        top_calls = []
        def counted(value):
            if value is output:
                top_calls.append(value)
            return scan(value)
        with patch.object(table, '_collect_dicts', side_effect=counted):
            result = table._reconstruct_with_test_structure_rules(output, image, time.perf_counter())
        self.assertEqual(len(top_calls), 1)
        self.assertEqual(result['table_rows'], [['Name', 'Value'], ['Example', '123']])
        self.assertEqual(result['table_structured']['rows'], result['table_rows'])
        self.assertEqual(result['table_selected_method'], 'embedded_table_rules')
        self.assertNotIn('raw_results', result)
        self.assertEqual(output, original)
        for value in result['table_debug']['post_processing_timing'].values():
            self.assertGreaterEqual(value, 0)

    def test_remote_html_fallback_still_works(self):
        output = {'html': '<table><tr><td>Example</td><td>123</td></tr></table>'}
        result = table._reconstruct_with_test_structure_rules(output, np.zeros((2, 2, 3), dtype=np.uint8), time.perf_counter())
        self.assertEqual(result['table_rows'], [['Example', '123']])
        self.assertEqual(result['table_selected_method'], 'remote_default_parser')

    def test_table_phases_accumulate_in_extraction_report(self):
        @extraction_timing.measure_processing
        def process():
            for _ in range(2):
                extraction_timing.call('table_roi', lambda: {
                    'table_debug': {'post_processing_timing': {'structure_rules_ms': 3, 'remote_call_ms': 7}}})
            return {}
        stage = process()['extraction_timing']['stages']['table_roi']
        self.assertEqual(stage['count'], 2)
        self.assertEqual(stage['structure_rules_ms'], 6)
        self.assertEqual(stage['remote_call_ms'], 14)


if __name__ == '__main__':
    unittest.main()
