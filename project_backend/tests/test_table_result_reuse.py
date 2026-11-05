import copy
import unittest
from unittest.mock import patch

import numpy as np

from app.model_runtime import table_recognition_v2_adapter as table
from tests import test_table_postprocessing as table_fixtures


class TableResultReuseTests(unittest.TestCase):
    def test_success_and_fallback_normalize_rows_and_scan_once(self):
        fixtures = [table_fixtures.TablePostprocessingTests().output(),
                    {'html': '<table><tr><td>ข้อความ a 9</td><td>123</td></tr></table>'},
                    {'rows': [['  A\n B ', '123'], ['ข้อความ a', '']]}, {}]
        for output in fixtures:
            with self.subTest(output=output):
                with patch.object(table, '_collect_dicts', wraps=table._collect_dicts) as scan, \
                     patch.object(table, '_normalize_table_rows', wraps=table._normalize_table_rows) as normalize:
                    table._reconstruct_with_test_structure_rules(output, np.zeros((80, 200, 3)), 0)
                self.assertEqual(scan.call_count, 1)
                self.assertEqual(normalize.call_count, 1)

    def test_entire_response_matches_original_repeated_normalization(self):
        fixtures = [table_fixtures.TablePostprocessingTests().output(),
                    {'html': '<table><tr><td> Ａ  ข้อความ a 9 </td><td>123 | 456</td></tr></table>'},
                    {'rows': [['Ａ\n B', 'ข้อความ a 9'], ['123 | 456', ''], []]}, {}]
        structured = table._structured_from_cells
        markdown = table._markdown_table
        for output in fixtures:
            original = copy.deepcopy(output)
            image = np.zeros((80, 200, 3))
            with self.subTest(output=output), patch.object(table.time, 'perf_counter', return_value=100):
                # Reproduce the previous extra normalization in the real formatting functions.
                with patch.object(table, '_structured_from_cells', side_effect=lambda rows, cells, **kw: structured(rows, cells)), \
                     patch.object(table, '_markdown_table', side_effect=lambda rows, **kw: markdown(rows)):
                    before = table._reconstruct_with_test_structure_rules(output, image, 100)
                after = table._reconstruct_with_test_structure_rules(output, image, 100)
            self.assertEqual(after, before)
            self.assertEqual(output, original)
            rows = after['table_structured']['rows']
            if rows:
                self.assertIsNot(rows, after['table_rows'])

    def test_deduplication_uses_identity_and_preserves_first_order(self):
        first = {'cell_box_list': []}
        equal_but_distinct = copy.deepcopy(first)
        output = {'table_res_list': [first, equal_but_distinct, first]}
        candidates = table._table_result_candidates(output)
        self.assertEqual([id(item) for item in candidates], [id(first), id(equal_but_distinct)])
        self.assertEqual([id(item) for item in table._table_result_candidates(output)],
                         [id(first), id(equal_but_distinct)])  # no cross-request cache

    def test_failed_candidate_is_not_reconstructed_twice(self):
        output = table_fixtures.TablePostprocessingTests().output()
        with patch.object(table, 'reconstruct_table_from_ocr', side_effect=ValueError('same failure')) as reconstruct:
            result = table._reconstruct_with_test_structure_rules(output, np.zeros((80, 200, 3)), 0)
        reconstruct.assert_called_once()
        self.assertEqual(result['table_selected_method'], 'remote_default_parser')
        self.assertIn('same failure', result['table_debug']['embedded_rules_fallback_reason'])

    def test_empty_supplied_inventory_does_not_trigger_rescan(self):
        with patch.object(table, '_collect_dicts', side_effect=AssertionError('rescan')):
            self.assertEqual(table._extract_table_html({}, []), '')
            self.assertEqual(table._extract_table_rows({}, '', []), [])


if __name__ == '__main__':
    unittest.main()
